"""Workbook parsers for the SOFP tracer.

Everything here is deliberately *structure detecting* rather than hardcoded to
fixed row/column positions, because accounting exports move their headings
around between periods and systems.

Three parsers:

* ``parse_sofp``           - the Statement of Financial Position from the
                             financial-statements workbook (the SOFP heads).
* ``parse_trial_balance``  - the trial balance (opening / movement / closing
                             debit-credit column pairs).
* ``parse_general_ledger`` - the GL, transaction by transaction, rolled up per
                             account.

No figures are ever invented: a cell that cannot be read as a number becomes
``None`` and is reported as a warning rather than being guessed at.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import load_workbook


class ParseError(Exception):
    """Raised when a workbook does not look like the statement we expected."""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
# "12000 Accumulated Depreciation-Furniture and Fixtures" -> ("12000", "Accum...")
_CODE_RE = re.compile(r"^\s*(\d{3,8}[A-Za-z0-9\-]*)\s*[\-–:.]?\s+(.*\S)\s*$")

_ERROR_CELLS = {"#ref!", "#value!", "#div/0!", "#n/a", "#name?", "#null!", "#num!"}


def norm(value: Any) -> str:
    """Collapse a cell to a single-line, single-spaced string."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    text = str(value).replace("\xa0", " ").replace("\n", " ").replace("\r", " ")
    return _WS_RE.sub(" ", text).strip()


def nkey(value: Any) -> str:
    """Normalised lower-case key for comparing headings."""
    return norm(value).lower()


def to_number(value: Any) -> Optional[float]:
    """Read a cell as a number, or return None.

    Handles real numbers, "1,234.50", "(1,234)" negatives, blank/dash cells and
    Excel error values such as ``#REF!`` (which do appear in the source
    workbook). Never guesses: anything unreadable comes back as None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
            return None
        return number
    if isinstance(value, (datetime, date)):
        return None

    text = norm(value)
    if not text or text.lower() in _ERROR_CELLS or text.startswith("#"):
        return None

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    for token in (",", "PKR", "Rs.", "Rs"):
        text = text.replace(token, "")
    text = text.replace("−", "-").strip()
    if text in {"", "-", "–", "—", "."}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def iso_date(value: Any) -> Optional[str]:
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    text = norm(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def split_code(label: str) -> Tuple[Optional[str], str]:
    """Split "12000 Accumulated Depreciation" into ("12000", "Accumulated ...")."""
    match = _CODE_RE.match(norm(label))
    if match:
        return match.group(1), match.group(2)
    return None, norm(label)


def account_key(label: str) -> str:
    """Stable identity for an account: its code when it has one, else its name."""
    code, name = split_code(label)
    return code if code else nkey(name)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", nkey(text)).strip("-")
    return slug or "item"


def _sheet_rows(worksheet, max_rows: int = 250_000) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for index, row in enumerate(worksheet.iter_rows(values_only=True)):
        rows.append(list(row))
        if index >= max_rows:
            break
    return rows


def _pick_sheet(workbook, hints: Sequence[str], probe: Sequence[str] = ()):
    """Choose a worksheet by name hint, then by probing cell contents."""
    titles = {nkey(name): name for name in workbook.sheetnames}

    for hint in hints:  # exact title match first
        if hint in titles:
            return workbook[titles[hint]]
    for hint in hints:  # then substring match
        for key, original in titles.items():
            if hint in key:
                return workbook[original]

    for phrase in probe:  # then look inside the sheets for a distinctive phrase
        for name in workbook.sheetnames:
            worksheet = workbook[name]
            blob = " ".join(
                nkey(cell)
                for row in worksheet.iter_rows(min_row=1, max_row=12, values_only=True)
                for cell in row
                if cell is not None
            )
            if phrase in blob:
                return worksheet

    return workbook[workbook.sheetnames[0]]


def _load(path: str, hints: Sequence[str], probe: Sequence[str] = ()):
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        worksheet = _pick_sheet(workbook, hints, probe)
        return worksheet.title, _sheet_rows(worksheet), list(workbook.sheetnames)
    finally:
        workbook.close()


def _header_positions(row: Sequence[Any]) -> Dict[str, List[int]]:
    positions: Dict[str, List[int]] = {}
    for index, cell in enumerate(row):
        key = nkey(cell)
        if key:
            positions.setdefault(key, []).append(index)
    return positions


def _debit_credit_pairs(row: Sequence[Any]) -> List[Tuple[int, int]]:
    """Find (debit_col, credit_col) pairs in a candidate heading row."""
    debits = [i for i, cell in enumerate(row) if nkey(cell) in {"debit", "dr", "debits"}]
    credits = [i for i, cell in enumerate(row) if nkey(cell) in {"credit", "cr", "credits"}]
    pairs: List[Tuple[int, int]] = []
    used: set = set()
    for debit_col in debits:
        candidates = [c for c in credits if c > debit_col and c not in used]
        if candidates:
            credit_col = min(candidates)
            used.add(credit_col)
            pairs.append((debit_col, credit_col))
    return pairs


# --------------------------------------------------------------------------
# Statement of Financial Position
# --------------------------------------------------------------------------

_SECTION_WORDS = ("asset", "liabilit", "fund", "equity", "contingenc", "capital")
_LIABILITY_TRIGGERS = ("fund", "liabilit", "equity", "reserve")
_SKIP_LABELS = {
    "note",
    "notes",
    "rupees",
    "(pak rupees)",
    "chairman",
    "trustee",
    "director",
    "chief executive",
}


def _looks_like_section(label: str) -> bool:
    stripped = label.replace(" ", "")
    if not stripped or not stripped.isupper():
        return False
    lowered = label.lower()
    return any(word in lowered for word in _SECTION_WORDS)


def parse_sofp(path: str) -> Dict[str, Any]:
    """Parse the Statement of Financial Position into its heads and year columns."""
    sheet, rows, sheetnames = _load(
        path,
        hints=["sofp", "statement of financial position", "balance sheet", "bs"],
        probe=["statement of financial position", "balance sheet"],
    )
    warnings: List[str] = []

    # 1. Locate the heading row carrying the comparative year columns.
    year_row_index: Optional[int] = None
    found_years: List[Dict[str, Any]] = []
    for index, row in enumerate(rows[:40]):
        candidates: List[Dict[str, Any]] = []
        for col, cell in enumerate(row):
            text = norm(cell)
            if not text:
                continue
            match = _YEAR_RE.search(text)
            if match and len(re.sub(r"[^0-9]", "", text)) <= 8:
                candidates.append({"col": col, "year": int(match.group(0)), "label": text})
        if len(candidates) >= 2 and len({c["year"] for c in candidates}) >= 2:
            year_row_index, found_years = index, candidates
            break

    if year_row_index is None:
        raise ParseError(
            f"Could not find comparative year columns on sheet '{sheet}'. Expected a row "
            "containing at least two year headings (for example 2025 and 2024)."
        )

    years: List[Dict[str, Any]] = []
    seen_cols: set = set()
    used_labels: set = set()
    for candidate in found_years:
        if candidate["col"] in seen_cols:
            continue
        seen_cols.add(candidate["col"])
        label = candidate["label"]
        if label in used_labels:  # keep every period column addressable
            label = f"{label} (col {candidate['col'] + 1})"
        used_labels.add(label)
        years.append({"label": label, "year": candidate["year"], "col": candidate["col"]})

    first_year_col = min(item["col"] for item in years)
    label_limit = max(first_year_col, 1)

    items: List[Dict[str, Any]] = []
    section = ""
    side = "asset"
    used_ids: set = set()

    for index in range(year_row_index + 1, len(rows)):
        row = rows[index]
        if not any(cell is not None and norm(cell) for cell in row):
            continue

        # Label = first non-numeric text cell left of the year columns.
        # A bare number in that zone is the note reference.
        label = ""
        note = ""
        for col in range(0, min(label_limit, len(row))):
            text = norm(row[col])
            if not text:
                continue
            if to_number(row[col]) is not None:
                if not note:
                    note = text
                continue
            if not label:
                label = text

        values: Dict[str, Optional[float]] = {}
        for year in years:
            cell = row[year["col"]] if year["col"] < len(row) else None
            values[year["label"]] = to_number(cell)

        if label and _looks_like_section(label):
            section = label
            if any(trigger in label.lower() for trigger in _LIABILITY_TRIGGERS):
                side = "liability"
            continue

        if not label or nkey(label) in _SKIP_LABELS:
            continue  # subtotal band / spacer / signature row: no head name
        if nkey(label).startswith("the annexed notes"):
            continue
        if all(value is None for value in values.values()):
            continue  # narrative row with no figures is not a SOFP head

        base_id = slugify(label)
        item_id, suffix = base_id, 2
        while item_id in used_ids:
            item_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(item_id)

        items.append(
            {
                "id": item_id,
                "label": label,
                "note": note,
                "section": section or "UNCLASSIFIED",
                "side": side,
                "row": index + 1,
                "values": values,
            }
        )

    if not items:
        raise ParseError(
            f"Found the year columns on sheet '{sheet}' but no line items beneath them."
        )

    # How much of each period column actually holds figures? Working drafts often
    # interleave review notes with the older comparatives, which leaves those
    # columns only partly readable. Report that instead of hiding it.
    for year in years:
        year["coverage"] = sum(
            1 for item in items if item["values"].get(year["label"]) is not None
        )
        year["line_count"] = len(items)

    empty = [year for year in years if year["coverage"] == 0]
    for year in empty:
        warnings.append(
            f"Period column '{year['label']}' holds no readable figures on sheet "
            f"'{sheet}' and has been ignored."
        )
        for item in items:
            item["values"].pop(year["label"], None)
    years = [year for year in years if year["coverage"] > 0]

    best_coverage = max((year["coverage"] for year in years), default=0)
    for year in years:
        year["partial"] = year["coverage"] < best_coverage
        if year["partial"]:
            warnings.append(
                f"Period column '{year['label']}' has figures for only "
                f"{year['coverage']} of {len(items)} line items; the remaining cells "
                "are blank or hold text, so those heads show no comparative."
            )

    return {
        "sheet": sheet,
        "sheetnames": sheetnames,
        "years": years,
        "items": items,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# Trial balance
# --------------------------------------------------------------------------


def parse_trial_balance(path: str) -> Dict[str, Any]:
    """Parse a trial balance with opening / movement / closing debit-credit pairs."""
    sheet, rows, sheetnames = _load(
        path,
        hints=["trial balance", "trial-balance", "trial", "tb"],
        probe=["trial balance"],
    )
    warnings: List[str] = []

    header_index: Optional[int] = None
    pairs: List[Tuple[int, int]] = []
    for index, row in enumerate(rows[:40]):
        candidate = _debit_credit_pairs(row)
        if len(candidate) > len(pairs):
            header_index, pairs = index, candidate

    if header_index is None or not pairs:
        raise ParseError(
            f"No Debit/Credit column headings found on sheet '{sheet}' - this does not "
            "look like a trial balance export."
        )

    numeric_cols = {col for pair in pairs for col in pair}
    first_numeric = min(numeric_cols)
    label_col = 0
    if label_col in numeric_cols:
        label_col = next(
            (c for c in range(first_numeric) if c not in numeric_cols), first_numeric
        )

    # Column pairs, left to right, are opening / movement / closing.
    if len(pairs) >= 3:
        opening_pair, movement_pair, closing_pair = pairs[0], pairs[1], pairs[-1]
    elif len(pairs) == 2:
        opening_pair, movement_pair, closing_pair = pairs[0], None, pairs[1]
        warnings.append(
            "Trial balance has two Debit/Credit pairs; read as opening and closing "
            "(period movement derived as closing less opening)."
        )
    else:
        opening_pair, movement_pair, closing_pair = None, None, pairs[0]
        warnings.append(
            "Trial balance has a single Debit/Credit pair; read as closing balances only "
            "(no opening balance or period movement available)."
        )

    def pair_values(row: Sequence[Any], pair) -> Tuple[float, float]:
        if pair is None:
            return 0.0, 0.0
        debit = to_number(row[pair[0]]) if pair[0] < len(row) else None
        credit = to_number(row[pair[1]]) if pair[1] < len(row) else None
        return (debit or 0.0), (credit or 0.0)

    accounts: List[Dict[str, Any]] = []
    reported_totals: List[Dict[str, Any]] = []
    seen_keys: Dict[str, int] = {}

    for index in range(header_index + 1, len(rows)):
        row = rows[index]
        label = norm(row[label_col]) if label_col < len(row) else ""
        if not label:
            for col in range(0, first_numeric):
                if col < len(row) and norm(row[col]) and to_number(row[col]) is None:
                    label = norm(row[col])
                    break

        has_number = any(
            to_number(row[col]) is not None for col in numeric_cols if col < len(row)
        )
        if not label or not has_number:
            continue

        opening_debit, opening_credit = pair_values(row, opening_pair)
        movement_debit, movement_credit = pair_values(row, movement_pair)
        closing_debit, closing_credit = pair_values(row, closing_pair)

        opening = opening_debit - opening_credit
        closing = closing_debit - closing_credit
        movement = (closing - opening) if movement_pair is None else (movement_debit - movement_credit)

        record: Dict[str, Any] = {
            "label": label,
            "row": index + 1,
            "opening_debit": opening_debit,
            "opening_credit": opening_credit,
            "opening": opening,
            "movement_debit": movement_debit,
            "movement_credit": movement_credit,
            "movement": movement,
            "movement_derived": movement_pair is None,
            "closing_debit": closing_debit,
            "closing_credit": closing_credit,
            "closing": closing,
        }

        if nkey(label).startswith(("total", "grand total", "sum ")):
            reported_totals.append(record)
            continue

        code, name = split_code(label)
        key = code if code else nkey(name)
        if key in seen_keys:
            warnings.append(
                f"Duplicate trial balance account '{label}' (rows {seen_keys[key]} and "
                f"{index + 1}); both rows are kept separately."
            )
            key = f"{key}#{index + 1}"
        else:
            seen_keys[key] = index + 1

        record.update({"key": key, "code": code, "name": name})
        accounts.append(record)

    if not accounts:
        raise ParseError(f"No account rows found beneath the headings on sheet '{sheet}'.")

    control = {
        "opening_debit": round(sum(a["opening_debit"] for a in accounts), 2),
        "opening_credit": round(sum(a["opening_credit"] for a in accounts), 2),
        "movement_debit": round(sum(a["movement_debit"] for a in accounts), 2),
        "movement_credit": round(sum(a["movement_credit"] for a in accounts), 2),
        "closing_debit": round(sum(a["closing_debit"] for a in accounts), 2),
        "closing_credit": round(sum(a["closing_credit"] for a in accounts), 2),
    }
    control["closing_difference"] = round(
        control["closing_debit"] - control["closing_credit"], 2
    )
    control["account_count"] = len(accounts)

    return {
        "sheet": sheet,
        "sheetnames": sheetnames,
        "accounts": accounts,
        "control": control,
        "reported_totals": reported_totals,
        "column_layout": {
            "label_col": label_col,
            "opening": opening_pair,
            "movement": movement_pair,
            "closing": closing_pair,
        },
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# General ledger
# --------------------------------------------------------------------------

_ACCOUNT_HEADERS = ("gl name", "gl account", "account name", "account", "gl")
_DESC_HEADERS = (
    "communication",
    "memo/description",
    "description",
    "memo",
    "narration",
    "particulars",
    "label",
)
_PARTNER_HEADERS = ("partner", "vendor", "customer", "supplier", "counterparty")
_OPENING_MARKERS = {
    "initial balance",
    "opening balance",
    "balance b/f",
    "balance brought forward",
    "b/f",
}


def parse_general_ledger(path: str) -> Dict[str, Any]:
    """Parse a GL export into per-account transaction listings."""
    sheet, rows, sheetnames = _load(
        path, hints=["general ledger", "ledger", "gl"], probe=["general ledger"]
    )
    warnings: List[str] = []

    header_index: Optional[int] = None
    for index, row in enumerate(rows[:40]):
        if _debit_credit_pairs(row):
            header_index = index
            break
    if header_index is None:
        raise ParseError(
            f"No Debit/Credit column headings found on sheet '{sheet}' - this does not "
            "look like a general ledger export."
        )

    header = rows[header_index]
    positions = _header_positions(header)
    debit_col, credit_col = _debit_credit_pairs(header)[0]

    def first_col(names: Sequence[str]) -> Optional[int]:
        for name in names:  # exact heading match wins
            if name in positions:
                return positions[name][0]
        for name in names:  # then a substring match
            for key, cols in positions.items():
                if name in key:
                    return cols[0]
        return None

    account_col = first_col(_ACCOUNT_HEADERS)
    if account_col is None:
        account_col = 0
        warnings.append(
            "No 'GL Name' heading found; assumed the first column holds the account name."
        )

    # Odoo-style exports repeat the account heading for the entry reference column.
    reference_col: Optional[int] = None
    account_header_key = nkey(header[account_col]) if account_col < len(header) else ""
    if account_header_key and len(positions.get(account_header_key, [])) > 1:
        reference_col = positions[account_header_key][1]
    if reference_col is None:
        reference_col = first_col(
            ("reference", "ref", "entry", "move", "journal", "num", "number", "document")
        )

    date_col = first_col(("date", "posting date", "entry date"))
    month_col = first_col(("month", "period"))
    description_col = first_col(_DESC_HEADERS)
    partner_col = first_col(_PARTNER_HEADERS)
    balance_col = first_col(("balance", "running balance", "cumulative balance"))

    def cell(row: Sequence[Any], col: Optional[int]) -> Any:
        if col is None or col >= len(row):
            return None
        return row[col]

    accounts: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    current_key: Optional[str] = None
    unassigned = 0

    for index in range(header_index + 1, len(rows)):
        row = rows[index]
        if not any(c is not None and norm(c) for c in row):
            continue

        label = norm(cell(row, account_col))
        reference = norm(cell(row, reference_col))
        debit = to_number(cell(row, debit_col)) or 0.0
        credit = to_number(cell(row, credit_col)) or 0.0
        when = iso_date(cell(row, date_col))

        if label:
            key = account_key(label)
            if key != current_key:
                current_key = key
                if key not in accounts:
                    code, name = split_code(label)
                    accounts[key] = {
                        "key": key,
                        "code": code,
                        "name": name,
                        "label": label,
                        "opening": 0.0,
                        "opening_rows": 0,
                        "debit": 0.0,
                        "credit": 0.0,
                        "transactions": [],
                        "reported_debit": None,
                        "reported_credit": None,
                        "reported_balance": None,
                        "first_row": index + 1,
                    }
                    order.append(key)

        if current_key is None:
            unassigned += 1
            continue

        account = accounts[current_key]

        # An account summary row repeats the account name in the reference column.
        if label and reference and nkey(reference) == nkey(label):
            account["reported_debit"] = to_number(cell(row, debit_col))
            account["reported_credit"] = to_number(cell(row, credit_col))
            account["reported_balance"] = to_number(cell(row, balance_col))
            continue

        marker = nkey(reference) or nkey(cell(row, description_col))
        if marker in _OPENING_MARKERS:
            account["opening"] += debit - credit
            account["opening_rows"] += 1
            continue

        if not when and not debit and not credit:
            continue  # spacer row

        account["debit"] += debit
        account["credit"] += credit
        account["transactions"].append(
            {
                "row": index + 1,
                "reference": reference or None,
                "date": when,
                "month": norm(cell(row, month_col)) or None,
                "description": norm(cell(row, description_col)) or None,
                "partner": norm(cell(row, partner_col)) or None,
                "debit": debit,
                "credit": credit,
                "amount": debit - credit,
                "balance": to_number(cell(row, balance_col)),
            }
        )

    if unassigned:
        warnings.append(
            f"{unassigned} ledger row(s) appeared before any account name and were skipped."
        )
    if not accounts:
        raise ParseError(f"No ledger accounts found beneath the headings on sheet '{sheet}'.")

    for account in accounts.values():
        account["closing"] = account["opening"] + account["debit"] - account["credit"]
        account["transaction_count"] = len(account["transactions"])
        reported = account["reported_balance"]
        account["summary_difference"] = (
            round(account["closing"] - reported, 2) if reported is not None else None
        )

    return {
        "sheet": sheet,
        "sheetnames": sheetnames,
        "accounts": accounts,
        "order": order,
        "transaction_count": sum(a["transaction_count"] for a in accounts.values()),
        "column_layout": {
            "account": account_col,
            "reference": reference_col,
            "date": date_col,
            "description": description_col,
            "partner": partner_col,
            "debit": debit_col,
            "credit": credit_col,
            "balance": balance_col,
        },
        "warnings": warnings,
    }
