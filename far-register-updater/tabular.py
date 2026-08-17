"""
Generic helpers for reading tabular input (GL exports, invoice registers)
from either .xlsx or .csv, plus flexible header/date/amount parsing.

Nothing here is specific to any one sample file - it works off column
*names*, matched by flexible alias lists, never off fixed positions or
hardcoded sample values.
"""
import csv
import io
import re
from datetime import date, datetime

import openpyxl
from dateutil import parser as dateparser


def normalize_header(value):
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = s.replace(".", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def read_raw_table(data: bytes, filename: str):
    """Return (header_row: list[str raw], rows: list[list]) from the first
    sheet of an xlsx file or from a csv file. Values are returned as-is
    (openpyxl native types for xlsx, strings for csv)."""
    lower = (filename or "").lower()
    if lower.endswith(".csv"):
        text = None
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = data.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        all_rows = [r for r in reader]
        if not all_rows:
            return [], []
        return all_rows[0], all_rows[1:]
    else:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        ws = wb[wb.sheetnames[0]]
        all_rows = list(ws.iter_rows(values_only=True))
        if not all_rows:
            return [], []
        header = list(all_rows[0])
        body = [list(r) for r in all_rows[1:]]
        return header, body


def find_column(norm_headers, aliases):
    alias_set = {normalize_header(a) for a in aliases}
    for i, h in enumerate(norm_headers):
        if h in alias_set:
            return i
    return None


def parse_date(value):
    """Best-effort date parsing. Returns a datetime.date or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return dateparser.parse(s, dayfirst=False, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def parse_amount(value):
    """Best-effort numeric parsing. Returns a float or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    if s in ("", "-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def rows_to_dicts(header_raw, body_rows):
    """Zip a header row onto each body row, keyed by normalized header text."""
    norm_headers = [normalize_header(h) for h in header_raw]
    out = []
    for row in body_rows:
        if row is None:
            continue
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        rec = {}
        for i, nh in enumerate(norm_headers):
            rec[nh] = row[i] if i < len(row) else None
        out.append(rec)
    return out, norm_headers
