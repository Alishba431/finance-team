"""
Best-effort extraction of invoice number / date / vendor / description from
supplied invoice files.

Two invoice input paths are supported:
1. An "invoice register" spreadsheet (.xlsx/.csv) the finance team fills in
   themselves - this is the reliable path (see tabular.py for parsing).
2. Raw invoice files (.pdf or .txt) - text is pulled straight out of the
   file (no OCR, no image recognition) and a handful of regular expressions
   look for an invoice number, a date, a vendor line and a description.
   If a PDF has no extractable text (e.g. it is a scanned image), it is
   reported as unreadable and the caller treats the addition as having
   "no invoice", exactly as SPEC.md requires.

This is inherently heuristic. It is not a substitute for the invoice
register spreadsheet, and the README says so.
"""
import io
import re

from tabular import parse_amount, parse_date

try:
    import pdfplumber
except ImportError:  # pragma: no cover - guarded so the app still boots
    pdfplumber = None

INV_NO_RE = re.compile(
    r"(?:invoice|inv|bill)\s*(?:no\.?|number|#)?\s*[:\-]?\s*([A-Za-z0-9\-\/]{3,25})",
    re.I,
)
DATE_LABEL_RE = re.compile(
    r"(?:invoice\s*date|date)\s*[:\-]?\s*([0-9A-Za-z,\-\/\. ]{6,22})", re.I
)
AMOUNT_RE = re.compile(
    r"(?:grand\s*total|total\s*amount|amount\s*due|net\s*amount|total)\s*"
    r"[:\-]?\s*(?:rs\.?|pkr|\$)?\s*([\d,]+\.?\d*)",
    re.I,
)


def _extract_text(data: bytes, filename: str):
    lower = (filename or "").lower()
    if lower.endswith(".txt"):
        for enc in ("utf-8", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")
    if lower.endswith(".pdf"):
        if pdfplumber is None:
            return ""
        text_parts = []
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    text_parts.append(t)
        except Exception:
            return ""
        return "\n".join(text_parts)
    return ""


def extract_invoice(data: bytes, filename: str):
    """Returns a dict describing what could be read from one invoice file.

    Keys: filename, extracted (bool), invoice_number, invoice_date,
    vendor, description, amount (amount is used only for matching, never
    to set the asset's recorded cost - see SPEC.md rule 3).
    """
    text = _extract_text(data, filename)
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", filename or "invoice")
    stem = re.sub(r"[_\-]+", " ", stem).strip()

    result = {
        "filename": filename,
        "extracted": bool(text and text.strip()),
        "invoice_number": None,
        "invoice_date": None,
        "vendor": None,
        "description": None,
        "amount": None,
    }
    if not result["extracted"]:
        return result

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    m = INV_NO_RE.search(text)
    if m:
        result["invoice_number"] = m.group(1).strip()

    m = DATE_LABEL_RE.search(text)
    if m:
        result["invoice_date"] = parse_date(m.group(1))
    if result["invoice_date"] is None:
        # Fall back to scanning the first handful of lines for anything
        # date-shaped.
        for l in lines[:15]:
            d = parse_date(l)
            if d:
                result["invoice_date"] = d
                break

    m = AMOUNT_RE.search(text)
    if m:
        result["amount"] = parse_amount(m.group(1))

    if lines:
        # First non-empty line is usually the vendor / letterhead name.
        vendor_line = lines[0]
        if INV_NO_RE.match(vendor_line) or len(vendor_line) > 60:
            vendor_line = lines[1] if len(lines) > 1 else vendor_line
        result["vendor"] = vendor_line[:120]

    desc_idx = None
    for i, l in enumerate(lines):
        if re.match(r"^description\b", l, re.I):
            desc_idx = i
            break
    if desc_idx is not None and desc_idx + 1 < len(lines):
        result["description"] = lines[desc_idx + 1][:200]
    else:
        result["description"] = f"Per invoice: {stem}"[:200]

    return result
