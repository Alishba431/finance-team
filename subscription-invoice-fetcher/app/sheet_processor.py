"""Reads the uploaded breakdown workbook, looks up each row against Gmail,
and writes an output workbook with Status + Invoice File columns added.
"""
import re
from datetime import timedelta
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font

from . import gmail_search

REQUIRED_FIELDS = ["enterprise", "date", "amount"]

COLUMN_KEYWORDS = {
    "enterprise": ["enterprise", "client", "company", "customer", "vendor", "organisation", "organization"],
    "breakdown": ["breakdown", "description", "line item", "item", "particular"],
    "date": ["date"],
    "amount": ["amount", "total", "value"],
}

STATUS_FOUND = "Found (Attachment)"
STATUS_REVIEW = "Found — No Attachment (Review)"
STATUS_NOT_FOUND = "Not Found"
STATUS_MISSING = "Missing Data"

_AMOUNT_CLEAN_RE = re.compile(r"[^\d.\-]")


def _find_column(columns, keywords):
    lower_cols = {str(c).strip().lower(): c for c in columns}
    for kw in keywords:
        for lower, original in lower_cols.items():
            if kw in lower:
                return original
    return None


def detect_columns(df):
    detected = {}
    for field, keywords in COLUMN_KEYWORDS.items():
        detected[field] = _find_column(df.columns, keywords)
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
                      base_url: str, run_id: str, service):
    """Runs the full match pipeline. `service` may be None to test column
    detection without touching Gmail (not used in normal operation)."""
    df = pd.read_excel(input_path)
    columns = detect_columns(df)

    missing_required = [f for f in REQUIRED_FIELDS if columns.get(f) is None]

    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice Matches"

    header = list(df.columns) + ["Status", "Invoice File"]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    attachments_dir.mkdir(parents=True, exist_ok=True)

    counts = {STATUS_FOUND: 0, STATUS_REVIEW: 0, STATUS_NOT_FOUND: 0, STATUS_MISSING: 0}

    for idx, row in df.iterrows():
        row_values = list(row)
        status = None
        link_url = None

        if missing_required:
            status = STATUS_MISSING
        else:
            enterprise_name = row.get(columns["enterprise"])
            amount = _parse_amount(row.get(columns["amount"]))
            invoice_date = _parse_date(row.get(columns["date"]))

            if (not enterprise_name or (isinstance(enterprise_name, float) and pd.isna(enterprise_name))
                    or amount is None or invoice_date is None):
                status = STATUS_MISSING
            else:
                start = invoice_date - timedelta(days=7)
                end = invoice_date + timedelta(days=7)
                candidates = gmail_search.fetch_candidates(service, start, end)
                match_status, candidate = gmail_search.find_best_match(candidates, str(enterprise_name), amount)

                if match_status == "found":
                    attachment = candidate["attachments"][0]
                    raw = gmail_search.download_attachment(service, candidate["msg_id"], attachment)
                    ext = Path(attachment["filename"]).suffix or (
                        ".pdf" if "pdf" in attachment["mimeType"] else ".bin")
                    fname = f"{_safe_filename(enterprise_name)}_{invoice_date.isoformat()}_{idx}{ext}"
                    (attachments_dir / fname).write_bytes(raw)
                    status = STATUS_FOUND
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
    }
