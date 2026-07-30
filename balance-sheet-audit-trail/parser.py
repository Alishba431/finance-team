"""
Workbook parsing for the Balance Sheet Audit Support tool.

All functions here treat the uploaded workbook purely as tabular data:
cell values are read as numbers/text/dates, never interpreted as instructions.

Three shapes are parsed, generically (no filename, sheet name or account
name from any sample is hardcoded):

  * SOFP  - a statement with a year header row and a label column. The
            column belonging to the most recent year is treated as the
            reported closing balance for that line.
  * TB    - a trial balance with either a single "Closing Balance" column
            or repeating Debit/Credit column pairs (Initial / Period / End).
            The "End"/"Closing" pair (or the rightmost pair, if none is
            labelled) is treated as the closing balance.
  * GL    - a general ledger laid out as one block per account: a row
            naming the account, optionally an "Initial Balance" row, then
            transaction rows, until the next account block.

If a sheet does not match these shapes closely enough to find a header
row, the parser returns an explicit error message instead of guessing.
"""
import re
from difflib import SequenceMatcher

YEAR_RE = re.compile(r"(19|20)\d{2}")


def _cell_text(v):
    if v is None:
        return ""
    return str(v).strip()


def _to_number(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    if s.startswith("#"):
        return None
    s2 = s.replace(",", "")
    try:
        return float(s2)
    except ValueError:
        return None


def list_sheets(wb):
    return wb.sheetnames


def _normalize_sheet_name(name):
    return re.sub(r"[\s\-_]+", "", name).lower()


def guess_sheet(sheet_names, target, keyword_sets):
    """Best-effort guess of which sheet corresponds to a role (sofp/tb/gl).

    Tries an exact normalized match against `target` first, then falls
    back to scoring sheet names against a list of keyword lists (any of
    which, if all its keywords are present, counts as a hit).
    """
    norm_target = _normalize_sheet_name(target)
    for s in sheet_names:
        if _normalize_sheet_name(s) == norm_target:
            return s, 1.0

    best, best_score = None, 0.0
    for s in sheet_names:
        low = s.lower()
        score = 0.0
        for kws in keyword_sets:
            if all(kw in low for kw in kws):
                score = max(score, 1.0 - 0.05 * len(kws))
        # partial credit for sharing text with target
        if score == 0.0:
            ratio = SequenceMatcher(None, norm_target, _normalize_sheet_name(s)).ratio()
            score = ratio * 0.6
        if score > best_score:
            best_score, best = score, s
    return best, best_score


def parse_sofp(ws, max_scan_rows=25):
    rows = list(ws.iter_rows(values_only=True))
    header_row_idx = None
    year_cols = {}
    best_count = 0
    scan_limit = min(max_scan_rows, len(rows))
    for i in range(scan_limit):
        row = rows[i]
        cols = {}
        for j, v in enumerate(row):
            if v is None:
                continue
            m = YEAR_RE.search(str(v))
            if m:
                y = int(m.group(0))
                if 1990 <= y <= 2100:
                    cols[j] = y
        if len(cols) > best_count:
            best_count, header_row_idx, year_cols = len(cols), i, cols

    if header_row_idx is None or not year_cols:
        return [], {
            "error": "Could not find a row with year labels (e.g. 2024, 2025) in "
                     "the first rows of the SOFP sheet, so the current-year column "
                     "could not be identified."
        }

    current_col = max(year_cols.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    current_year = year_cols[current_col]

    note_col = None
    for j, v in enumerate(rows[header_row_idx]):
        if v and "note" in str(v).lower():
            note_col = j
            break

    label_scan_end = note_col if note_col is not None else current_col
    label_scan_end = max(label_scan_end, 1)

    items = []
    for i in range(header_row_idx + 1, len(rows)):
        row = rows[i]
        if not row:
            continue
        label = None
        for j in range(0, min(label_scan_end, len(row))):
            v = row[j]
            if v is None:
                continue
            s = str(v).strip()
            if not s or YEAR_RE.fullmatch(s):
                continue
            label = s
            break
        raw_val = row[current_col] if current_col < len(row) else None
        value, error = None, None
        if raw_val is not None:
            if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
                value = float(raw_val)
            else:
                s = str(raw_val).strip()
                if s.startswith("#"):
                    error = s
                elif s:
                    value = _to_number(s)
        if label is None and value is None and error is None:
            continue
        items.append({
            "row": i + 1,
            "label": label or f"(unlabeled row {i + 1})",
            "value": value,
            "error": error,
        })

    meta = {
        "year_header_row": header_row_idx + 1,
        "current_year": current_year,
        "current_year_col": current_col + 1,
        "note_col": (note_col + 1) if note_col is not None else None,
        "years_seen": sorted(set(year_cols.values()), reverse=True),
    }
    return items, meta


def parse_tb(ws, max_scan_rows=25):
    rows = list(ws.iter_rows(values_only=True))
    scan_limit = min(max_scan_rows, len(rows))
    header_row_idx = None

    for i in range(scan_limit):
        low = [_cell_text(v).lower() for v in rows[i]]
        if "debit" in low and "credit" in low:
            header_row_idx = i
            break

    single_closing_col = None
    if header_row_idx is None:
        for i in range(scan_limit):
            low = [_cell_text(v).lower() for v in rows[i]]
            for j, v in enumerate(low):
                if "closing balance" in v:
                    header_row_idx = i
                    single_closing_col = j
                    break
            if header_row_idx is not None:
                break

    if header_row_idx is None:
        return [], {
            "error": "Could not find a Debit/Credit or Closing Balance header row "
                     "in the TB sheet."
        }

    header_row = rows[header_row_idx]
    low = [_cell_text(v).lower() for v in header_row]

    pairs = []
    j = 0
    while j < len(low):
        if low[j] == "debit" and j + 1 < len(low) and low[j + 1] == "credit":
            pairs.append((j, j + 1))
            j += 2
            continue
        j += 1

    if single_closing_col is None:
        for j, v in enumerate(low):
            if "closing balance" in v:
                single_closing_col = j
                break

    opening_col = None
    for j, v in enumerate(low):
        if "opening balance" in v:
            opening_col = j
            break

    def group_label(col):
        for back in range(1, 4):
            ri = header_row_idx - back
            if ri < 0:
                break
            for cc in range(max(0, col - 1), col + 2):
                r = rows[ri]
                if cc < len(r) and r[cc] and str(r[cc]).strip():
                    return str(r[cc]).strip()
        return ""

    closing_pair = None
    if pairs:
        labeled = [(p, group_label(p[0])) for p in pairs]
        end_candidates = [p for p, l in labeled if re.search(r"end|clos", l, re.I)]
        closing_pair = end_candidates[-1] if end_candidates else pairs[-1]

    accounts = []
    for i in range(header_row_idx + 1, len(rows)):
        row = rows[i]
        if not row:
            continue
        name_raw = row[0] if len(row) else None
        name = _cell_text(name_raw)
        if not name:
            continue
        if name.lower().startswith("total"):
            continue

        m = re.match(r"^(\d{3,8})\s+(.*)$", name)
        code, clean_name = (m.group(1), m.group(2).strip()) if m else (None, name)

        def num(col):
            if col is None or col >= len(row):
                return None
            return _to_number(row[col])

        closing, debit_end, credit_end = None, None, None
        if closing_pair:
            debit_end = num(closing_pair[0]) or 0.0
            credit_end = num(closing_pair[1]) or 0.0
            closing = debit_end - credit_end
        elif single_closing_col is not None:
            closing = num(single_closing_col)

        opening = num(opening_col) if opening_col is not None else None

        if closing is None and opening is None and debit_end is None and credit_end is None:
            continue

        accounts.append({
            "row": i + 1,
            "code": code,
            "name": clean_name,
            "raw_name": name,
            "opening": opening,
            "debit_end": debit_end,
            "credit_end": credit_end,
            "closing": closing if closing is not None else 0.0,
        })

    return accounts, {
        "header_row": header_row_idx + 1,
        "closing_pair_cols": [c + 1 for c in closing_pair] if closing_pair else None,
        "single_closing_col": (single_closing_col + 1) if single_closing_col is not None else None,
    }


def parse_gl(ws, max_scan_rows=30):
    rows = list(ws.iter_rows(values_only=True))
    scan_limit = min(max_scan_rows, len(rows))
    header_row_idx = None
    date_col = desc_col = debit_col = credit_col = balance_col = None

    for i in range(scan_limit):
        low = [_cell_text(v).lower() for v in rows[i]]
        if "date" in low and ("debit" in low or "credit" in low or "balance" in low):
            header_row_idx = i
            for j, v in enumerate(low):
                if v == "date" and date_col is None:
                    date_col = j
                elif v == "debit" and debit_col is None:
                    debit_col = j
                elif v == "credit" and credit_col is None:
                    credit_col = j
                elif v == "balance" and balance_col is None:
                    balance_col = j
                elif v in ("communication", "memo", "memo/description", "description",
                           "transaction type", "particulars") and desc_col is None:
                    desc_col = j
            break

    if header_row_idx is None:
        return {}, {
            "error": "Could not find a Date/Debit/Credit/Balance header row in the GL sheet."
        }

    accounts = {}
    order = []
    current = None

    for i in range(header_row_idx + 1, len(rows)):
        row = rows[i]
        if not row:
            continue
        col0s = _cell_text(row[0] if len(row) else None)
        date_val = row[date_col] if date_col is not None and date_col < len(row) else None
        has_date = date_val is not None and str(date_val).strip() != ""

        is_account_header = (
            col0s
            and not has_date
            and not col0s.lower().startswith("total for")
            and col0s.lower() not in ("initial balance", "beginning balance")
        )
        if is_account_header:
            m = re.match(r"^(\d{3,8})\s+(.*)$", col0s)
            code, clean = (m.group(1), m.group(2).strip()) if m else (None, col0s)
            current = clean
            if current not in accounts:
                accounts[current] = {
                    "name": current, "code": code, "raw_name": col0s, "transactions": []
                }
                order.append(current)
            continue

        if col0s.lower().startswith("total for"):
            continue

        if current is None:
            continue

        if col0s.lower() in ("initial balance", "beginning balance"):
            desc = col0s
        else:
            desc = _cell_text(row[desc_col]) if desc_col is not None and desc_col < len(row) else ""

        debit = _to_number(row[debit_col]) if debit_col is not None and debit_col < len(row) else None
        credit = _to_number(row[credit_col]) if credit_col is not None and credit_col < len(row) else None
        bal_raw = row[balance_col] if balance_col is not None and balance_col < len(row) else None
        stated_balance = _to_number(bal_raw)

        if debit is None and credit is None and stated_balance is None and not has_date and not desc:
            continue

        accounts[current]["transactions"].append({
            "row": i + 1,
            "date": str(date_val) if date_val is not None else None,
            "description": desc,
            "debit": debit or 0.0,
            "credit": credit or 0.0,
            "stated_balance": stated_balance,
        })

    for acc in accounts.values():
        computed = 0.0
        for t in acc["transactions"]:
            computed += t["debit"] - t["credit"]
        acc["computed_closing"] = computed
        acc["transaction_count"] = len(acc["transactions"])
        last_stated = None
        for t in reversed(acc["transactions"]):
            if t["stated_balance"] is not None:
                last_stated = t["stated_balance"]
                break
        acc["stated_closing"] = last_stated

    return accounts, {"header_row": header_row_idx + 1, "account_order": order}
