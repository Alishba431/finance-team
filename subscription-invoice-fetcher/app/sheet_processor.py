"""Reads the uploaded breakdown workbook, looks up each row against Gmail,
and writes an output workbook with Status + Invoice File columns added.
"""
import re
from datetime import timedelta
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font

from . import gdrive, gmail_search

REQUIRED_FIELDS = ["date", "amount"]

COLUMN_KEYWORDS = {
    "enterprise": ["enterprise", "client", "company", "customer", "organisation", "organization"],
    "vendor": ["vendor", "partner", "supplier"],
    "breakdown": ["breakdown", "description", "communication", "line item", "item", "particular"],
    "date": ["date"],
    "amount": ["amount", "total", "value"],
}

STATUS_FOUND = "Found (Attachment)"
STATUS_REVIEW = "Found — No Attachment (Review)"
STATUS_NOT_FOUND = "Not Found"
STATUS_MISSING = "Missing Data"

HEADER_SCAN_ROWS = 10

_AMOUNT_CLEAN_RE = re.compile(r"[^\d.\-]")


def _cell_has_text(value):
    return value is not None and not (isinstance(value, float) and pd.isna(value)) and str(value).strip() != ""


def _score_header_row(cells):
    """Counts how many distinct expected fields (enterprise/breakdown/date/amount)
    have a matching keyword somewhere in this row's cells."""
    field_hits = set()
    for cell in cells:
        if not _cell_has_text(cell):
            continue
        text = str(cell).strip().lower()
        for field, keywords in COLUMN_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                field_hits.add(field)
    return len(field_hits)


def find_header_row(input_path, max_scan_rows=HEADER_SCAN_ROWS):
    """Scans the first `max_scan_rows` rows of the sheet for the row that best
    matches expected column-name keywords, skipping any title/label rows above
    it. Returns the 0-based row index to use as the header, or None if no row
    in that range looks enough like a header at all."""
    raw = pd.read_excel(input_path, header=None, nrows=max_scan_rows)
    best_idx, best_score = None, 0
    for i in range(len(raw)):
        score = _score_header_row(raw.iloc[i].tolist())
        if score > best_score:
            best_score, best_idx = score, i
    return best_idx


def _populated_count(df, column, field):
    series = df[column]
    if field == "amount":
        return series.apply(_parse_amount).notna().sum()
    if field == "date":
        return series.apply(_parse_date).notna().sum()
    return series.apply(_cell_has_text).sum()


def _find_column(df, keywords, field):
    lower_cols = {str(c).strip().lower(): c for c in df.columns}
    matches = []
    for lower, original in lower_cols.items():
        if any(kw in lower for kw in keywords):
            matches.append(original)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    # More than one column name matches (e.g. "Breakup Amounts" and "Amount in
    # dollars") - prefer whichever one actually has real values in most rows
    # rather than just taking the first name match.
    return max(matches, key=lambda col: _populated_count(df, col, field))


def detect_columns(df):
    detected = {}
    for field, keywords in COLUMN_KEYWORDS.items():
        detected[field] = _find_column(df, keywords, field)
    return detected


def _parse_amount(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = _AMOUNT_CLEAN_RE.sub("", str(value))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce")
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.date()


def _safe_filename(text):
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("_")
    return text or "row"


def process_workbook(input_path: Path, output_path: Path, attachments_dir: Path,
                      base_url: str, run_id: str, imap_conn, progress_cb=None):
    """Runs the full match pipeline. `imap_conn` is a logged-in IMAP4_SSL
    connection to the admin Gmail inbox (may be None to test column
    detection without touching Gmail; not used in normal operation).

    `progress_cb`, if given, is called as progress_cb(current_row, total_rows)
    before each row is processed, so callers can surface a live "Processing
    row N of M..." indicator instead of a static loading message."""
    header_row = find_header_row(input_path)
    if header_row is None:
        return {
            "counts": {STATUS_FOUND: 0, STATUS_REVIEW: 0, STATUS_NOT_FOUND: 0, STATUS_MISSING: 0},
            "missing_columns": REQUIRED_FIELDS,
            "total_rows": 0,
            "unreadable": True,
        }

    df = pd.read_excel(input_path, header=header_row)
    columns = detect_columns(df)

    missing_required = [f for f in REQUIRED_FIELDS if columns.get(f) is None]
    # Enterprise is display-only; some identifier (vendor OR enterprise) is
    # still needed to search Gmail, but it doesn't have to be "enterprise"
    # specifically - a sheet with only a Partner/Vendor column is fine.
    if columns.get("vendor") is None and columns.get("enterprise") is None:
        missing_required.append("vendor or enterprise")

    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice Matches"

    header = list(df.columns) + ["Status", "Invoice File"]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    attachments_dir.mkdir(parents=True, exist_ok=True)

    counts = {STATUS_FOUND: 0, STATUS_REVIEW: 0, STATUS_NOT_FOUND: 0, STATUS_MISSING: 0}

    # A separate vendor/partner column - the company that actually sent the
    # invoice - is what should be searched for in Gmail. The enterprise
    # column (internal department/cost-center) stays in the output for
    # display only. Sheets with no separate vendor column fall back to
    # matching on the enterprise column, same as before this change.
    vendor_col = columns.get("vendor")
    enterprise_col = columns.get("enterprise")
    match_col = vendor_col if vendor_col is not None else enterprise_col

    total_rows = len(df)
    for idx, row in df.iterrows():
        if progress_cb:
            progress_cb(idx + 1, total_rows)
        row_values = list(row)
        status = None
        link_url = None

        if missing_required:
            status = STATUS_MISSING
        else:
            match_name = row.get(match_col)
            amount = _parse_amount(row.get(columns["amount"]))
            invoice_date = _parse_date(row.get(columns["date"]))

            def _blank(value):
                return not value or (isinstance(value, float) and pd.isna(value))

            if _blank(match_name) or amount is None or invoice_date is None:
                status = STATUS_MISSING
            else:
                start = invoice_date - timedelta(days=7)
                end = invoice_date + timedelta(days=7)
                candidates = gmail_search.fetch_candidates(imap_conn, start, end)
                match_status, candidate = gmail_search.find_best_match(candidates, str(match_name), amount)

                if match_status == "found":
                    attachment = candidate["attachments"][0]
                    raw = gmail_search.download_attachment(imap_conn, candidate["uid"], attachment)
                    ext = Path(attachment["filename"]).suffix or (
                        ".pdf" if "pdf" in attachment["mimeType"] else ".bin")
                    fname = f"{_safe_filename(match_name)}_{invoice_date.isoformat()}_{idx}{ext}"
                    status = STATUS_FOUND
                    # Prefer a Google Drive link when Drive upload is
                    # configured (GOOGLE_SERVICE_ACCOUNT_JSON +
                    # GOOGLE_DRIVE_FOLDER_ID); otherwise fall back to
                    # serving the saved copy from this app.
                    link_url = gdrive.upload_pdf(fname, raw, attachment["mimeType"])
                    if not link_url:
                        (attachments_dir / fname).write_bytes(raw)
                        link_url = f"{base_url.rstrip('/')}/attachments/{run_id}/{fname}"
                elif match_status == "review":
                    status = STATUS_REVIEW
                else:
                    status = STATUS_NOT_FOUND

        counts[status] += 1
        out_row = row_values + [status, link_url or ""]
        ws.append(out_row)
        if link_url:
            cell = ws.cell(row=ws.max_row, column=len(header))
            cell.hyperlink = link_url
            cell.value = "Open invoice"
            cell.font = Font(color="0563C1", underline="single")

    wb.save(output_path)

    return {
        "counts": counts,
        "missing_columns": missing_required,
        "total_rows": len(df),
        "unreadable": False,
    }
