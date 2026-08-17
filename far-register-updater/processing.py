"""
Core Fixed Asset Register update logic.

Everything here operates on whatever workbook/GL/invoices are handed to it
at run time - nothing about any sample file is baked in. Column layouts are
discovered by header text, not by position, so the tool tolerates the small
header differences seen between category sheets (e.g. some sheets have a
"First Year dep months" column, some don't).
"""
import io
import re
from datetime import date, datetime

import openpyxl
from openpyxl.styles import Font

from tabular import (
    find_column,
    normalize_header,
    parse_amount,
    parse_date,
    read_raw_table,
    rows_to_dicts,
)
from invoice_extract import extract_invoice

# ---------------------------------------------------------------------------
# Column aliases for the category ledger sheets (Office equipment, Vehicles,
# Furniture and fixtures, ...). Matched by EXACT normalized header text so
# "Cost" never gets confused with "Cost for the prior year ended".
# ---------------------------------------------------------------------------
CATEGORY_FIELD_ALIASES = {
    "sr_no": ["sr no", "s no", "sno"],
    "item_code": ["item code"],
    "category": ["category"],
    "description": ["description"],
    "date_of_purchase": ["date of purchase"],
    "year_of_purchase": ["year of purchase"],
    "cost": ["cost"],
    "cost_prior_year": ["cost for the prior year ended", "cost for the prior year"],
    "cost_year_ended": ["cost for the year ended"],
    "depr_rate": ["depr rate", "depreciation rate", "dep rate"],
    "opening": ["opening for the year ended", "opening"],
    "dep_prior_year": ["dep for prior year"],
    "dep_for_year": ["dep for the year"],
    "acc_dep": ["acc dep", "accumulated depreciation"],
    "wdv": ["wdv for the year ended", "wdv", "nbv"],
    "first_year_months": ["first year dep months"],
    "first_year_dep": ["first year dep"],
    "gl_reference": ["gl reference", "gl ref"],
}
REQUIRED_HEADER_FIELDS = ["description", "date_of_purchase", "cost", "depr_rate"]

GL_FIELD_ALIASES = {
    "reference": [
        "reference", "reference no", "voucher", "voucher no", "vch no",
        "transaction id", "txn id", "doc no", "document no", "gl reference",
    ],
    "date": ["date", "posting date", "txn date", "voucher date", "transaction date"],
    "account": [
        "account", "gl account", "account name", "category", "account code",
        "head of account", "gl head", "particulars head",
    ],
    "amount": ["amount", "debit", "value", "cost", "amount (pkr)", "addition amount"],
    "narration": ["narration", "description", "particulars", "details", "memo", "remarks"],
}

INVOICE_REGISTER_ALIASES = {
    "invoice_number": ["invoice number", "invoice no", "inv no", "bill no"],
    "invoice_date": ["invoice date", "date"],
    "vendor": ["vendor", "supplier", "vendor name"],
    "description": ["description", "particulars", "item description"],
    "amount": ["amount", "invoice amount", "value"],
    "category": ["category", "gl account"],
    "reference": ["reference", "gl reference", "voucher"],
}

CATEGORY_NAME_HINTS = {
    "land and buildings": ["land", "building"],
    "furniture and fixtures": ["furniture", "fixture"],
    "office equipment": ["office equipment"],
    "electrical equipment": ["electrical"],
    "mobile phones": ["mobile", "phone"],
    "computer equipment": ["computer"],
    "vehicles": ["vehicle"],
}


def _norm_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class NeedsReviewLog:
    def __init__(self):
        self.rows = []

    def add(self, kind, category, gl_reference, gl_date, amount, description, reason):
        self.rows.append(
            {
                "type": kind,
                "category": category or "",
                "gl_reference": gl_reference or "",
                "date": gl_date,
                "amount": amount,
                "description": description or "",
                "reason": reason,
            }
        )


class AdditionsLog:
    def __init__(self):
        self.rows = []

    def add(self, **kwargs):
        self.rows.append(kwargs)


# ---------------------------------------------------------------------------
# Category sheet discovery
# ---------------------------------------------------------------------------
def find_header_row(ws, max_scan_rows=15):
    for r in range(1, min(max_scan_rows, ws.max_row) + 1):
        norm_cells = [normalize_header(ws.cell(r, c).value) for c in range(1, ws.max_column + 1)]
        col_index = {}
        for field, aliases in CATEGORY_FIELD_ALIASES.items():
            idx = find_column(norm_cells, aliases)
            if idx is not None and field not in col_index:
                col_index[field] = idx + 1  # 1-based column number
        if all(f in col_index for f in REQUIRED_HEADER_FIELDS):
            return r, col_index
    return None, None


def discover_category_sheets(wb):
    """Return {sheet_name: {"header_row": int, "col_index": dict}} for every
    sheet that looks like a category ledger. Sheets that don't match this
    shape (e.g. the 'FAR Schedule' summary, or ad-hoc working sheets) are
    left completely untouched."""
    sheets = {}
    for name in wb.sheetnames:
        if normalize_header(name) == "far schedule":
            continue
        ws = wb[name]
        header_row, col_index = find_header_row(ws)
        if header_row is not None:
            sheets[name] = {"header_row": header_row, "col_index": col_index}
    return sheets


def category_hint_key(sheet_name):
    nk = _norm_key(sheet_name)
    for canon, hints in CATEGORY_NAME_HINTS.items():
        for h in hints:
            if _norm_key(h) in nk:
                return canon
    return _norm_key(sheet_name)


# ---------------------------------------------------------------------------
# Existing-row scanning
# ---------------------------------------------------------------------------
def scan_existing_rows(ws, header_row, col_index):
    """Walk the used range below the header row and return the rows that
    look like real asset lines (non-blank description + numeric cost).
    Everything else (blank separators, note rows, stray totals) is left
    exactly where it is."""
    rows = []
    last_data_row = header_row
    max_r = max(ws.max_row, header_row)
    for r in range(header_row + 1, max_r + 1):
        desc = ws.cell(r, col_index["description"]).value
        cost = ws.cell(r, col_index["cost"]).value
        desc_ok = isinstance(desc, str) and desc.strip() != ""
        cost_ok = isinstance(cost, (int, float)) and not isinstance(cost, bool)
        if desc_ok and cost_ok:
            rec = {"row": r}
            for field, col in col_index.items():
                rec[field] = ws.cell(r, col).value
            rows.append(rec)
            last_data_row = r
    return rows, last_data_row


def ensure_gl_reference_column(ws, header_row, col_index):
    if "gl_reference" in col_index:
        return col_index
    new_col = ws.max_column + 1
    ws.cell(header_row, new_col).value = "GL Reference"
    ws.cell(header_row, new_col).font = Font(bold=True)
    col_index = dict(col_index)
    col_index["gl_reference"] = new_col
    return col_index


# ---------------------------------------------------------------------------
# Fiscal year helpers
# ---------------------------------------------------------------------------
def detect_fiscal_year_end(wb):
    """Look for an 'AS ON <Month> <day>, <year>' style title on the FAR
    Schedule sheet and return (month, day). Returns None if not found."""
    far_sheet = None
    for name in wb.sheetnames:
        if normalize_header(name) == "far schedule":
            far_sheet = wb[name]
            break
    if far_sheet is None:
        return None, None, None
    pattern = re.compile(
        r"AS\s+ON\s+([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I
    )
    for row in far_sheet.iter_rows(min_row=1, max_row=10):
        for cell in row:
            if isinstance(cell.value, str):
                m = pattern.search(cell.value)
                if m:
                    try:
                        dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
                        return dt.month, dt.day, dt.date()
                    except ValueError:
                        continue
    return None, None, None


def fiscal_year_end_for(d: date, fye_month: int, fye_day: int) -> date:
    try:
        candidate = date(d.year, fye_month, fye_day)
    except ValueError:
        candidate = date(d.year, fye_month, 28)
    if d <= candidate:
        return candidate
    try:
        return date(d.year + 1, fye_month, fye_day)
    except ValueError:
        return date(d.year + 1, fye_month, 28)


def months_to_fye(purchase_date: date, fye: date) -> int:
    months = (fye.year - purchase_date.year) * 12 + (fye.month - purchase_date.month)
    if purchase_date.day <= fye.day:
        months += 1
    return max(1, min(12, months))


# ---------------------------------------------------------------------------
# GL parsing
# ---------------------------------------------------------------------------
def parse_gl_rows(gl_bytes, gl_filename):
    header_raw, body = read_raw_table(gl_bytes, gl_filename)
    recs, norm_headers = rows_to_dicts(header_raw, body)
    col = {}
    for field, aliases in GL_FIELD_ALIASES.items():
        idx = find_column(norm_headers, aliases)
        if idx is not None:
            col[field] = norm_headers[idx]

    out = []
    for i, rec in enumerate(recs):
        reference = rec.get(col.get("reference", "")) if "reference" in col else None
        gl_date_raw = rec.get(col.get("date", "")) if "date" in col else None
        account = rec.get(col.get("account", "")) if "account" in col else None
        amount_raw = rec.get(col.get("amount", "")) if "amount" in col else None
        narration = rec.get(col.get("narration", "")) if "narration" in col else None

        reference_str = str(reference).strip() if reference is not None else ""
        out.append(
            {
                "row_num": i + 2,  # +1 header, +1 to make it 1-based
                "reference": reference_str,
                "date": parse_date(gl_date_raw),
                "account": str(account).strip() if account is not None else "",
                "amount": parse_amount(amount_raw),
                "narration": str(narration).strip() if narration is not None else "",
                "missing_columns": [f for f in ("reference", "date", "account", "amount") if f not in col],
            }
        )
    return out, list(col.keys())


def parse_invoice_register(data, filename):
    header_raw, body = read_raw_table(data, filename)
    recs, norm_headers = rows_to_dicts(header_raw, body)
    col = {}
    for field, aliases in INVOICE_REGISTER_ALIASES.items():
        idx = find_column(norm_headers, aliases)
        if idx is not None:
            col[field] = norm_headers[idx]

    out = []
    for rec in recs:
        out.append(
            {
                "invoice_number": str(rec.get(col.get("invoice_number", ""), "")).strip() or None,
                "invoice_date": parse_date(rec.get(col.get("invoice_date", ""))),
                "vendor": str(rec.get(col.get("vendor", ""), "")).strip() or None,
                "description": str(rec.get(col.get("description", ""), "")).strip() or None,
                "amount": parse_amount(rec.get(col.get("amount", ""))),
                "category": str(rec.get(col.get("category", ""), "")).strip() or None,
                "reference": str(rec.get(col.get("reference", ""), "")).strip() or None,
                "source": filename,
            }
        )
    return out


def match_category_sheet(gl_account, sheet_names):
    key = _norm_key(gl_account)
    best = None
    for name in sheet_names:
        nk = _norm_key(name)
        if nk and (nk in key or key in nk):
            return name
    # try hinted canonical matching (e.g. "computer equipments" GL account
    # vs "Computer Equipment" sheet name)
    for name in sheet_names:
        canon_sheet = category_hint_key(name)
        for canon, hints in CATEGORY_NAME_HINTS.items():
            if canon == canon_sheet:
                for h in hints:
                    if _norm_key(h) in key:
                        return name
    return best


def match_invoice(gl_row, category_name, invoice_pool, used_flags):
    # 1) invoice number appears in the GL narration
    for i, inv in enumerate(invoice_pool):
        if used_flags[i]:
            continue
        if inv.get("invoice_number") and gl_row["narration"]:
            if inv["invoice_number"].lower() in gl_row["narration"].lower():
                return i
    # 2) amount + date proximity within same category
    best_i, best_diff = None, None
    for i, inv in enumerate(invoice_pool):
        if used_flags[i]:
            continue
        if inv.get("amount") is None or gl_row["amount"] is None:
            continue
        if abs(inv["amount"] - gl_row["amount"]) > 0.01:
            continue
        if inv.get("category") and _norm_key(inv["category"]) not in _norm_key(category_name) \
                and _norm_key(category_name) not in _norm_key(inv["category"]):
            continue
        if inv.get("invoice_date") and gl_row["date"]:
            diff = abs((inv["invoice_date"] - gl_row["date"]).days)
            if diff > 45:
                continue
            if best_diff is None or diff < best_diff:
                best_i, best_diff = i, diff
        elif inv.get("invoice_date") is None:
            if best_i is None:
                best_i = i
    return best_i


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def process_far(
    far_bytes,
    gl_bytes,
    gl_filename,
    invoice_files,           # list of (filename, bytes)
    invoice_register_bytes,  # bytes or None
    invoice_register_filename,
    period_end_date: date,
    period_months: int,
    fye_override,            # (month, day) or None
):
    # Two workbooks are loaded from the same bytes: `wb` is what gets edited
    # and saved, with data_only=False so that any formulas in cells we never
    # touch are preserved exactly as they are (including the pre-existing
    # #REF! errors this tool deliberately does not fix). `wb_values` is a
    # read-only helper loaded with data_only=True so that when a cell we
    # need to READ (cost, rate, opening, etc.) holds a formula rather than a
    # literal number, we get its last-calculated value instead of the
    # formula text - otherwise every formula-driven cell would look
    # non-numeric and its row would be silently skipped.
    wb = openpyxl.load_workbook(io.BytesIO(far_bytes), data_only=False)
    wb_values = openpyxl.load_workbook(io.BytesIO(far_bytes), data_only=True)

    # Drop any previous run's generated sheets so re-running on our own
    # output doesn't pile up duplicates of these.
    for gen_name in ("Needs Review", "Additions Log (This Run)"):
        if gen_name in wb.sheetnames:
            del wb[gen_name]

    detected_month, detected_day, detected_prior_period_end = detect_fiscal_year_end(wb_values)
    assumptions = []
    if fye_override:
        fye_month, fye_day = fye_override
    elif detected_month:
        fye_month, fye_day = detected_month, detected_day
    else:
        fye_month, fye_day = period_end_date.month, period_end_date.day
        assumptions.append(
            "Could not detect a fiscal year-end date from the FAR Schedule sheet title, "
            "so the period end date's month/day was used as the fiscal year-end. Please verify."
        )

    crossed_new_fy = False
    if detected_prior_period_end:
        prior_fye = fiscal_year_end_for(detected_prior_period_end, fye_month, fye_day)
        this_fye = fiscal_year_end_for(period_end_date, fye_month, fye_day)
        crossed_new_fy = this_fye != prior_fye
    else:
        assumptions.append(
            "Could not detect the FAR's prior period-end date, so existing assets' "
            "depreciation was simply accumulated for this period rather than rolled "
            "into a new fiscal year. Please verify if this run crosses a year-end."
        )

    category_sheets = discover_category_sheets(wb)
    sheet_names = list(category_sheets.keys())

    needs_review = NeedsReviewLog()
    additions_log = AdditionsLog()

    # ---- Build the invoice pool -------------------------------------------------
    invoice_pool = []
    if invoice_register_bytes:
        invoice_pool.extend(parse_invoice_register(invoice_register_bytes, invoice_register_filename))
    unreadable_invoices = []
    for fname, fbytes in invoice_files or []:
        inv = extract_invoice(fbytes, fname)
        if not inv["extracted"]:
            unreadable_invoices.append(fname)
            continue
        invoice_pool.append(
            {
                "invoice_number": inv["invoice_number"],
                "invoice_date": inv["invoice_date"],
                "vendor": inv["vendor"],
                "description": inv["description"],
                "amount": inv["amount"],
                "category": None,
                "reference": None,
                "source": fname,
            }
        )
    used_flags = [False] * len(invoice_pool)

    # ---- Parse GL ------------------------------------------------------------
    gl_rows, gl_columns_found = parse_gl_rows(gl_bytes, gl_filename)

    # Collect existing GL references already recorded on every category sheet,
    # for global duplicate detection.
    existing_refs = set()
    sheet_state = {}
    for name, meta in category_sheets.items():
        ws = wb[name]
        ws_vals = wb_values[name]
        col_index = ensure_gl_reference_column(ws, meta["header_row"], meta["col_index"])
        existing_rows, last_data_row = scan_existing_rows(ws_vals, meta["header_row"], col_index)
        for r in existing_rows:
            ref = r.get("gl_reference")
            if ref not in (None, ""):
                existing_refs.add(str(ref).strip().lower())
        sheet_state[name] = {
            "ws": ws,
            "ws_vals": ws_vals,
            "header_row": meta["header_row"],
            "col_index": col_index,
            "existing_rows": existing_rows,
            "last_data_row": last_data_row,
            "opening_cost": sum(r["cost"] for r in existing_rows if isinstance(r["cost"], (int, float))),
            "new_rows_cost_total": 0.0,
            "period_dep_total": 0.0,
            "new_rows_to_write": [],
        }

    gl_addition_totals_by_category = {}
    duplicate_totals_by_category = {}

    for gl_row in gl_rows:
        target_sheet = match_category_sheet(gl_row["account"], sheet_names) if gl_row["account"] else None
        cat_key = target_sheet or gl_row["account"] or "(unmapped)"
        gl_addition_totals_by_category[cat_key] = gl_addition_totals_by_category.get(cat_key, 0.0) + (gl_row["amount"] or 0.0)

        if gl_row["missing_columns"]:
            needs_review.add(
                "Blocked - Missing GL Column(s)", cat_key, gl_row["reference"], gl_row["date"],
                gl_row["amount"], gl_row["narration"],
                f"GL file is missing column(s): {', '.join(gl_row['missing_columns'])}",
            )
            continue
        if not gl_row["reference"]:
            needs_review.add(
                "Blocked - No Reference", cat_key, gl_row["reference"], gl_row["date"],
                gl_row["amount"], gl_row["narration"],
                "No voucher/transaction reference - cannot be checked for duplicates, so not added automatically.",
            )
            continue
        if gl_row["date"] is None:
            needs_review.add(
                "Blocked - Invalid Date", cat_key, gl_row["reference"], gl_row["date"],
                gl_row["amount"], gl_row["narration"], "GL row has a missing or unreadable date.",
            )
            continue
        if gl_row["amount"] is None:
            needs_review.add(
                "Blocked - Invalid Amount", cat_key, gl_row["reference"], gl_row["date"],
                gl_row["amount"], gl_row["narration"], "GL row has a missing or unreadable amount.",
            )
            continue
        if target_sheet is None:
            needs_review.add(
                "Blocked - Unmapped Category", cat_key, gl_row["reference"], gl_row["date"],
                gl_row["amount"], gl_row["narration"],
                f"GL account/category '{gl_row['account']}' did not match any existing FAR category sheet.",
            )
            continue

        ref_norm = gl_row["reference"].strip().lower()
        if ref_norm in existing_refs:
            duplicate_totals_by_category[target_sheet] = duplicate_totals_by_category.get(target_sheet, 0.0) + gl_row["amount"]
            continue  # rule 1 - already captured, do not add again

        # New addition - try to pair an invoice (rule 4)
        inv_idx = match_invoice(gl_row, target_sheet, invoice_pool, used_flags)
        matched_invoice = None
        if inv_idx is not None:
            matched_invoice = invoice_pool[inv_idx]
            used_flags[inv_idx] = True

        if matched_invoice:
            description = matched_invoice.get("description") or gl_row["narration"] or "(no description)"
            purchase_date = matched_invoice.get("invoice_date") or gl_row["date"]
        else:
            description = gl_row["narration"] or "(no description - no GL narration provided)"
            purchase_date = gl_row["date"]
            needs_review.add(
                "Added - No Invoice Match", target_sheet, gl_row["reference"], gl_row["date"],
                gl_row["amount"], description,
                "No invoice matched this addition; added using the GL narration as the description.",
            )

        state = sheet_state[target_sheet]
        # Rule 5 - depreciation rate currently used for the category: the
        # rate on the most recently purchased existing row, else the last
        # data row with a numeric rate.
        rate = None
        rows_with_rate = [r for r in state["existing_rows"] if isinstance(r.get("depr_rate"), (int, float))]
        if rows_with_rate:
            dated = [r for r in rows_with_rate if isinstance(r.get("date_of_purchase"), (date, datetime))]
            if dated:
                rate = max(dated, key=lambda r: r["date_of_purchase"])["depr_rate"]
            else:
                rate = rows_with_rate[-1]["depr_rate"]
        if rate is None:
            rate = 0.0
            needs_review.add(
                "Review - No Rate Found", target_sheet, gl_row["reference"], gl_row["date"],
                gl_row["amount"], description,
                "No existing depreciation rate found on this category sheet; rate set to 0 - please set manually.",
            )

        fye = fiscal_year_end_for(purchase_date, fye_month, fye_day)
        months = months_to_fye(purchase_date, fye)
        cost = gl_row["amount"]
        first_year_dep = round(cost * rate * (months / 12.0), 2)

        new_row = {
            "sr_no": None,
            "item_code": None,
            "category": target_sheet,
            "description": description,
            "date_of_purchase": purchase_date,
            "year_of_purchase": fye.year,
            "cost": cost,
            "cost_prior_year": 0,
            "cost_year_ended": cost,
            "depr_rate": rate,
            "opening": 0,
            "dep_prior_year": 0,
            "dep_for_year": first_year_dep,
            "acc_dep": first_year_dep,
            "wdv": round(cost - first_year_dep, 2),
            "first_year_months": months,
            "first_year_dep": first_year_dep,
            "gl_reference": gl_row["reference"],
        }
        state["new_rows_to_write"].append(new_row)
        state["new_rows_cost_total"] += cost
        state["period_dep_total"] += first_year_dep

        additions_log.add(
            category=target_sheet,
            gl_reference=gl_row["reference"],
            gl_date=gl_row["date"],
            gl_amount=cost,
            invoice_number=(matched_invoice or {}).get("invoice_number"),
            invoice_vendor=(matched_invoice or {}).get("vendor"),
            invoice_date=(matched_invoice or {}).get("invoice_date"),
            invoice_matched="Yes" if matched_invoice else "No",
            description_used=description,
            depreciation_rate=rate,
            first_year_dep_months=months,
            first_year_depreciation=first_year_dep,
        )

    for fname in unreadable_invoices:
        needs_review.add(
            "Review - Invoice Unreadable", "", "", None, None, fname,
            "No extractable text found in this invoice (possibly a scanned image); treated as no invoice for its addition.",
        )

    # ---- Roll forward existing rows & write new rows into each sheet --------
    category_summary = {}
    for name, state in sheet_state.items():
        ws = state["ws"]
        ws_vals = state["ws_vals"]
        col_index = state["col_index"]

        existing_period_dep_total = 0.0
        acc_dep_closing_total = 0.0
        for r in state["existing_rows"]:
            cost = r.get("cost") if isinstance(r.get("cost"), (int, float)) else 0.0
            rate = r.get("depr_rate") if isinstance(r.get("depr_rate"), (int, float)) else 0.0
            acc_dep_old = r.get("acc_dep") if isinstance(r.get("acc_dep"), (int, float)) else 0.0
            dep_for_year_old = r.get("dep_for_year") if isinstance(r.get("dep_for_year"), (int, float)) else 0.0
            dep_prior_year_old = r.get("dep_prior_year") if isinstance(r.get("dep_prior_year"), (int, float)) else 0.0
            opening_old = r.get("opening") if isinstance(r.get("opening"), (int, float)) else 0.0

            remaining = max(cost - acc_dep_old, 0.0)
            period_dep = min(cost * rate * (period_months / 12.0), remaining)
            period_dep = round(period_dep, 2)

            if crossed_new_fy:
                new_opening = acc_dep_old
                new_dep_prior_year = round(dep_prior_year_old + dep_for_year_old, 2)
                new_dep_for_year = period_dep
            else:
                new_opening = opening_old
                new_dep_prior_year = dep_prior_year_old
                new_dep_for_year = round(dep_for_year_old + period_dep, 2)

            new_acc_dep = round(acc_dep_old + period_dep, 2)
            new_wdv = round(cost - new_acc_dep, 2)

            row_num = r["row"]
            if "opening" in col_index:
                ws.cell(row_num, col_index["opening"]).value = new_opening
            if "dep_prior_year" in col_index:
                ws.cell(row_num, col_index["dep_prior_year"]).value = new_dep_prior_year
            if "dep_for_year" in col_index:
                ws.cell(row_num, col_index["dep_for_year"]).value = new_dep_for_year
            if "acc_dep" in col_index:
                ws.cell(row_num, col_index["acc_dep"]).value = new_acc_dep
            if "wdv" in col_index:
                ws.cell(row_num, col_index["wdv"]).value = new_wdv

            existing_period_dep_total += period_dep
            acc_dep_closing_total += new_acc_dep

        # Write new addition rows - reuse trailing blank rows if available,
        # otherwise insert fresh rows right after the last real data row so
        # nothing above is disturbed.
        new_rows = state["new_rows_to_write"]
        last_row = state["last_data_row"]
        if new_rows:
            blank_rows = []
            scan_r = last_row + 1
            while len(blank_rows) < len(new_rows) and scan_r <= ws_vals.max_row:
                if all(
                    ws_vals.cell(scan_r, c).value in (None, "")
                    for c in range(1, ws_vals.max_column + 1)
                ):
                    blank_rows.append(scan_r)
                    scan_r += 1
                else:
                    break
            remaining_needed = len(new_rows) - len(blank_rows)
            insert_at = last_row + 1 + len(blank_rows)
            if remaining_needed > 0:
                ws.insert_rows(insert_at, amount=remaining_needed)
                blank_rows.extend(range(insert_at, insert_at + remaining_needed))

            for target_row, new_row in zip(blank_rows, new_rows):
                for field, col in col_index.items():
                    ws.cell(target_row, col).value = new_row.get(field)

        acc_dep_closing_total += sum(nr["acc_dep"] for nr in new_rows)
        closing_cost = state["opening_cost"] + state["new_rows_cost_total"]

        category_summary[name] = {
            "opening_cost": state["opening_cost"],
            "additions_cost": state["new_rows_cost_total"],
            "closing_cost": closing_cost,
            "depreciation_for_period": round(existing_period_dep_total + state["period_dep_total"], 2),
            "accumulated_depreciation": round(acc_dep_closing_total, 2),
            "nbv": round(closing_cost - acc_dep_closing_total, 2),
        }

    # ---- Reconciliation (rule 9) ---------------------------------------------
    variances = []
    for cat_key, gl_total in gl_addition_totals_by_category.items():
        if cat_key not in category_summary:
            # Not a real FAR category sheet - already flagged per-row above
            # as "Blocked - Unmapped Category"; skip the redundant variance.
            continue
        far_added = category_summary.get(cat_key, {}).get("additions_cost", 0.0)
        dup_total = duplicate_totals_by_category.get(cat_key, 0.0)
        expected_new_total = gl_total - dup_total
        variance = round(far_added - expected_new_total, 2)
        if abs(variance) > 0.01:
            variances.append(
                {
                    "category": cat_key,
                    "gl_total_period": round(gl_total, 2),
                    "already_in_far_duplicates": round(dup_total, 2),
                    "expected_new_additions": round(expected_new_total, 2),
                    "far_added_this_run": round(far_added, 2),
                    "variance": variance,
                }
            )
            needs_review.add(
                "Category Variance", cat_key, "", period_end_date, variance,
                "",
                f"FAR additions this run ({round(far_added,2)}) do not tie to expected GL "
                f"additions ({round(expected_new_total,2)}) for this category - review required.",
            )

    # ---- Write FAR Schedule summary block ------------------------------------
    write_far_schedule_summary(wb, category_summary, sheet_names, period_end_date)

    # ---- Write Needs Review sheet ---------------------------------------------
    write_needs_review_sheet(wb, needs_review, assumptions)

    # ---- Write Additions Log sheet ---------------------------------------------
    write_additions_log_sheet(wb, additions_log)

    out_stream = io.BytesIO()
    wb.save(out_stream)
    out_stream.seek(0)

    summary = {
        "categories": category_summary,
        "variances": variances,
        "needs_review_count": len(needs_review.rows),
        "additions_count": len(additions_log.rows),
        "assumptions": assumptions,
        "unmatched_gl_columns_found": gl_columns_found,
        "crossed_new_fiscal_year": crossed_new_fy,
        "fiscal_year_end": f"{fye_month:02d}-{fye_day:02d}",
    }
    return out_stream.getvalue(), summary


def write_far_schedule_summary(wb, category_summary, sheet_names, period_end_date):
    far_name = None
    for name in wb.sheetnames:
        if normalize_header(name) == "far schedule":
            far_name = name
            break
    if far_name is None:
        return
    ws = wb[far_name]
    start_row = ws.max_row + 3

    ws.cell(start_row, 1).value = (
        f"UPDATED FAR SUMMARY — Period Ended {period_end_date.isoformat()} "
        f"(auto-generated by FAR Register Updater)"
    )
    ws.cell(start_row, 1).font = Font(bold=True)

    header_row = start_row + 2
    ws.cell(header_row, 1).value = "Description"
    for i, name in enumerate(sheet_names):
        ws.cell(header_row, 2 + i).value = name
    total_col = 2 + len(sheet_names)
    ws.cell(header_row, total_col).value = "Total"
    for c in range(1, total_col + 1):
        ws.cell(header_row, c).font = Font(bold=True)

    metrics = [
        ("Opening Cost", "opening_cost"),
        ("Additions (this period)", "additions_cost"),
        ("Closing Cost", "closing_cost"),
        ("Depreciation for the Period", "depreciation_for_period"),
        ("Accumulated Depreciation (closing)", "accumulated_depreciation"),
        ("Net Book Value (closing)", "nbv"),
    ]
    for r_off, (label, key) in enumerate(metrics):
        r = header_row + 1 + r_off
        ws.cell(r, 1).value = label
        total = 0.0
        for i, name in enumerate(sheet_names):
            val = category_summary.get(name, {}).get(key, 0.0)
            ws.cell(r, 2 + i).value = val
            total += val
        ws.cell(r, total_col).value = round(total, 2)


def write_needs_review_sheet(wb, needs_review, assumptions):
    ws = wb.create_sheet("Needs Review")
    headers = ["Type", "Category", "GL Reference", "Date", "Amount", "Description / Narration", "Reason"]
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(1, c)
        cell.value = h
        cell.font = Font(bold=True)
    r = 2
    for row in needs_review.rows:
        ws.cell(r, 1).value = row["type"]
        ws.cell(r, 2).value = row["category"]
        ws.cell(r, 3).value = row["gl_reference"]
        ws.cell(r, 4).value = row["date"].isoformat() if isinstance(row["date"], date) else row["date"]
        ws.cell(r, 5).value = row["amount"]
        ws.cell(r, 6).value = row["description"]
        ws.cell(r, 7).value = row["reason"]
        r += 1
    if not needs_review.rows:
        ws.cell(2, 1).value = "Nothing needs review for this run."
    if assumptions:
        r += 2
        ws.cell(r, 1).value = "Assumptions made this run:"
        ws.cell(r, 1).font = Font(bold=True)
        for a in assumptions:
            r += 1
            ws.cell(r, 1).value = a


def write_additions_log_sheet(wb, additions_log):
    ws = wb.create_sheet("Additions Log (This Run)")
    headers = [
        "Category", "GL Reference", "GL Date", "GL Amount", "Invoice Number",
        "Invoice Vendor", "Invoice Date", "Invoice Matched", "Description Used",
        "Depreciation Rate Applied", "First Year Dep Months", "First Year Depreciation",
    ]
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(1, c)
        cell.value = h
        cell.font = Font(bold=True)
    r = 2
    for row in additions_log.rows:
        ws.cell(r, 1).value = row["category"]
        ws.cell(r, 2).value = row["gl_reference"]
        gl_date = row["gl_date"]
        ws.cell(r, 3).value = gl_date.isoformat() if isinstance(gl_date, date) else gl_date
        ws.cell(r, 4).value = row["gl_amount"]
        ws.cell(r, 5).value = row["invoice_number"]
        ws.cell(r, 6).value = row["invoice_vendor"]
        inv_date = row["invoice_date"]
        ws.cell(r, 7).value = inv_date.isoformat() if isinstance(inv_date, date) else inv_date
        ws.cell(r, 8).value = row["invoice_matched"]
        ws.cell(r, 9).value = row["description_used"]
        ws.cell(r, 10).value = row["depreciation_rate"]
        ws.cell(r, 11).value = row["first_year_dep_months"]
        ws.cell(r, 12).value = row["first_year_depreciation"]
        r += 1
    if not additions_log.rows:
        ws.cell(2, 1).value = "No new additions were added this run."
