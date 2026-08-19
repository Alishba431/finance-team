"""
Core engine for the Fixed Asset Register (FAR) Auto-Updater.

Everything here is pure, local processing: no network calls, no secrets.
The uploaded baseline FAR is treated as the single source of truth for
structure, tabs, columns, and depreciation rates - this module never invents
a tab, column, rate, or figure that isn't derivable from the baseline FAR,
the invoices, or the GL.
"""
import io
import re
import csv
import json
import pathlib
import difflib
import datetime
from collections import Counter

import openpyxl
import numpy as np
from PIL import Image
from dateutil import parser as dateutil_parser

try:
    # Pure-Python/ONNX local OCR engine - no system binary (e.g. tesseract)
    # needs to be separately installed on the machine; the model files ship
    # inside the pip package itself, and nothing is sent anywhere over the
    # network at run time.
    from rapidocr_onnxruntime import RapidOCR
except Exception:
    RapidOCR = None

_OCR_ENGINE = None


def get_ocr_engine():
    """Lazily construct the OCR engine on first use (keeps app startup fast
    and lets the rest of the app run fine even if this optional dependency
    is somehow missing - image invoices just become exceptions instead)."""
    global _OCR_ENGINE
    if RapidOCR is None:
        raise RuntimeError(
            "The local OCR engine (rapidocr-onnxruntime) is not installed. "
            "Run: pip install -r requirements.txt"
        )
    if _OCR_ENGINE is None:
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


BASE_DIR = pathlib.Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CURRENT_FAR_PATH = DATA_DIR / "current_far.xlsx"
STATE_PATH = DATA_DIR / "state.json"

EXCEPTIONS_SHEET_NAME = "Exceptions – Review Required"


class NoBaselineError(Exception):
    pass


# ---------------------------------------------------------------------------
# generic state persistence (just a small JSON file - no database needed)
# ---------------------------------------------------------------------------

def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def has_baseline():
    return CURRENT_FAR_PATH.exists()


# ---------------------------------------------------------------------------
# text / value normalisation helpers
# ---------------------------------------------------------------------------

def normalize(s):
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def score_text(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def parse_any_date(value):
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    s = str(value).strip()
    if not s or s.lower() in ("none", "nan"):
        return None
    try:
        return dateutil_parser.parse(s, fuzzy=True, dayfirst=True).date()
    except Exception:
        return None


def parse_any_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            if value != value:  # NaN
                return None
        except Exception:
            pass
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    s = s.replace(",", "")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s in ("-", "."):
        return None
    try:
        return float(s)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# header / column detection (shared by FAR tabs and GL files)
# ---------------------------------------------------------------------------

def match_header_row(matrix, aliases_dict, mandatory_keys, max_scan_rows=25):
    """Scan the first rows of a matrix (list of rows of raw cell values) for
    a header row whose cells match the given alias dictionary. Returns
    (row_index, col_map) using 0-based indices, or (None, None)."""
    limit = min(max_scan_rows, len(matrix))
    for r_idx in range(limit):
        row = matrix[r_idx]
        norm_cells = [normalize(c) for c in row]
        col_map = {}
        # exact-match pass
        for c_idx, cell in enumerate(norm_cells):
            if not cell:
                continue
            for key, aliases in aliases_dict.items():
                if key in col_map:
                    continue
                if cell in aliases:
                    col_map[key] = c_idx
                    break
        # substring fallback pass, mandatory keys only
        for key in mandatory_keys:
            if key in col_map:
                continue
            best = None
            for c_idx, cell in enumerate(norm_cells):
                if not cell:
                    continue
                for alias in aliases_dict[key]:
                    if alias in cell:
                        if best is None or len(alias) > best[1]:
                            best = (c_idx, len(alias))
            if best:
                col_map[key] = best[0]
        if all(k in col_map for k in mandatory_keys):
            return r_idx, col_map
    return None, None


FAR_ALIASES = {
    "sr_no": ["sr. no.", "sr no", "sr. no", "s.no", "s. no.", "sr#"],
    "item_code": ["item code"],
    "category": ["category"],
    "description": ["description", "particulars", "asset description"],
    "date_of_purchase": ["date of purchase", "purchase date"],
    "year_of_purchase": ["year of purchase"],
    "cost": ["cost"],
    "cost_prior_year": ["cost for the prior year ended", "cost for prior year ended"],
    "cost_current_year": ["cost for the year ended", "cost for current year ended"],
    "depr_rate": ["depr. rate", "depreciation rate", "dep. rate", "dep rate", "rate"],
    "opening": ["opening for the year ended", "opening"],
    "dep_prior_year": ["dep for prior year", "depreciation for prior year"],
    "dep_current_year": ["dep for the year", "depreciation for the year", "dep for the year ended"],
    "acc_dep": ["acc dep", "accumulated depreciation", "accumulated dep"],
    "wdv": ["wdv for the year ended", "wdv", "net book value", "carrying amount"],
    "first_year_dep_months": ["first year dep months"],
    "first_year_dep": ["first year dep"],
    "vendor": ["vendor", "supplier", "party"],
    "invoice_no": ["invoice no", "invoice number", "invoice/reference", "reference no", "ref no"],
}
FAR_MANDATORY = ["description", "date_of_purchase", "cost", "depr_rate"]

GL_ALIASES = {
    "date": ["date", "transaction date", "posting date", "gl date", "value date"],
    "amount": ["amount", "debit", "value", "transaction amount", "debit amount", "amount (pkr)"],
    "classification": ["account", "classification", "category", "gl account", "head",
                        "account head", "account title", "account name"],
    "vendor": ["vendor", "payee", "party", "supplier"],
    "reference": ["reference", "ref", "ref no", "voucher", "voucher no", "invoice no"],
    "description": ["description", "narration", "details"],
}
GL_MANDATORY = ["date", "amount", "classification"]

DISPOSAL_KEYWORDS = [
    "disposal", "disposed", "dispose", "write off", "written off", "written-off",
    "scrap", "scrapped", "sale of asset", "sold",
]


# ---------------------------------------------------------------------------
# baseline FAR structure detection
# ---------------------------------------------------------------------------

def find_true_last_row(ws, fallback):
    max_r = ws.max_row or fallback
    max_c = ws.max_column or 1
    for r in range(max_r, fallback - 1, -1):
        for c in range(1, max_c + 1):
            v = ws.cell(row=r, column=c).value
            if v not in (None, ""):
                return r
    return fallback


def compute_mode_rate(ws_vals, col_map, header_row, last_row):
    idx = col_map.get("depr_rate")
    if idx is None:
        return None
    vals = []
    for r in range(header_row + 1, last_row + 1):
        v = parse_any_number(ws_vals.cell(row=r, column=idx + 1).value)
        if v is not None and 0 < v < 1.5:
            vals.append(round(v, 6))
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


def collect_category_terms(ws_vals, col_map, header_row, last_row, sheet_name):
    terms = {normalize(sheet_name)}
    idx = col_map.get("category")
    if idx is not None:
        for r in range(header_row + 1, last_row + 1):
            v = ws_vals.cell(row=r, column=idx + 1).value
            n = normalize(v)
            if n and len(n.split()) <= 4:
                terms.add(n)
    return terms


def mode_text(ws_vals, col_map, header_row, last_row, key):
    idx = col_map.get(key)
    if idx is None:
        return None
    vals = []
    for r in range(header_row + 1, last_row + 1):
        v = ws_vals.cell(row=r, column=idx + 1).value
        n = normalize(v)
        if n:
            vals.append(v)
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


def detect_asset_tabs(wb, wb_vals):
    tabs = {}
    for ws in wb.worksheets:
        name = ws.title
        if name == EXCEPTIONS_SHEET_NAME:
            continue
        scan_limit = min(20, ws.max_row or 1)
        matrix = [[c.value for c in row] for row in ws.iter_rows(min_row=1, max_row=scan_limit)]
        r_idx, col_map = match_header_row(matrix, FAR_ALIASES, FAR_MANDATORY)
        if col_map is None:
            continue
        header_row = r_idx + 1
        ws_vals = wb_vals[name]
        last_row = find_true_last_row(ws, header_row)
        rate = compute_mode_rate(ws_vals, col_map, header_row, last_row)
        category_terms = collect_category_terms(ws_vals, col_map, header_row, last_row, name)
        common_category = mode_text(ws_vals, col_map, header_row, last_row, "category") or name
        tabs[name] = {
            "header_row": header_row,
            "col_map": col_map,
            "rate": rate,
            "category_terms": category_terms,
            "common_category": common_category,
            "ws": ws,
            "ws_vals": ws_vals,
            "last_row": last_row,
        }
    return tabs


# ---------------------------------------------------------------------------
# GL parsing
# ---------------------------------------------------------------------------

def parse_gl_bytes(filename, data):
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    matrices = []
    if ext in ("xlsx", "xlsm", "xls"):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        except Exception as e:
            return None, f"Could not read the GL workbook: {e}"
        for ws in wb.worksheets:
            matrix = [list(row) for row in ws.iter_rows(values_only=True)]
            matrices.append((ws.title, matrix))
    elif ext == "csv":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        reader = csv.reader(io.StringIO(text))
        matrices.append((filename, [row for row in reader]))
    else:
        return None, f"Unsupported GL file type: .{ext or '?'}"

    for sheetname, matrix in matrices:
        if not matrix:
            continue
        r_idx, col_map = match_header_row(matrix, GL_ALIASES, GL_MANDATORY,
                                           max_scan_rows=min(30, len(matrix)))
        if not col_map:
            continue
        rows = []
        for row in matrix[r_idx + 1:]:
            if row is None:
                continue

            def get(key):
                idx = col_map.get(key)
                if idx is None or idx >= len(row):
                    return None
                return row[idx]

            date_val = parse_any_date(get("date"))
            amount_val = parse_any_number(get("amount"))
            classification_raw = get("classification")
            if date_val is None and amount_val is None and not normalize(classification_raw):
                continue
            rows.append({
                "date": date_val,
                "amount": amount_val,
                "classification_raw": classification_raw,
                "vendor": get("vendor"),
                "reference": get("reference"),
                "description": get("description"),
                "sheet": sheetname,
            })
        return rows, None
    return None, ("Could not find a recognizable header row with Date, Amount and an "
                  "Account/Classification column in this GL file. If your GL export is a "
                  "sectioned ledger report rather than a flat table, please re-export it as "
                  "a table with column headers.")


def is_disposal_row(gl_row):
    text = normalize(gl_row.get("classification_raw")) + " " + normalize(gl_row.get("description"))
    return any(k in text for k in DISPOSAL_KEYWORDS)


def best_match_by_terms(text, name_to_terms):
    scored = []
    for name, terms in name_to_terms.items():
        s = max((score_text(text, t) for t in terms), default=0.0)
        scored.append((name, s))
    if not scored:
        return None, []
    scored.sort(key=lambda x: -x[1])
    top_score = scored[0][1]
    if top_score < 0.6:
        return None, []
    winners = [n for n, s in scored if s >= top_score - 0.05]
    if len(winners) == 1:
        return winners[0], scored
    return "ambiguous", winners


def classify_gl_row(gl_row, tabs):
    text = normalize(gl_row.get("classification_raw"))
    if not text:
        return None, "GL row has no classification/account text"
    sheet_terms = {name: {normalize(name)} for name in tabs}
    match, winners = best_match_by_terms(text, sheet_terms)
    if match and match != "ambiguous":
        return match, None
    if match == "ambiguous":
        return None, f"ambiguous match among tabs: {winners}"
    cat_terms = {name: t["category_terms"] for name, t in tabs.items()}
    match2, winners2 = best_match_by_terms(text, cat_terms)
    if match2 and match2 != "ambiguous":
        return match2, None
    if match2 == "ambiguous":
        return None, f"ambiguous match among tabs: {winners2}"
    return None, f"no existing FAR tab matches classification '{gl_row.get('classification_raw')}'"


# ---------------------------------------------------------------------------
# invoice reading (PDF text extraction, optional OCR for images)
# ---------------------------------------------------------------------------

TOTAL_KEYWORDS = ["grand total", "total amount", "amount due", "net amount",
                   "invoice total", "total payable", "balance due", "total"]
DATE_KEYWORDS = ["invoice date", "date", "dated", "bill date", "date of purchase"]
VENDOR_KEYWORDS = ["vendor:", "bill from:", "from:", "supplier:", "sold by:"]
EXCLUDE_DESC_KEYWORDS = ["invoice", "total", "date", "vendor", "bill", "tax", "ntn", "gst",
                          "signature", "page"]
DATE_LOOKING = re.compile(
    r"\d{1,4}[-/ ]\w{2,9}[-/ ]\d{2,4}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
    r"\w{3,9}\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}"
)


def _nums_in_line(line):
    vals = []
    for n in re.findall(r"\d[\d,]*\.?\d*", line):
        v = parse_any_number(n)
        if v is not None:
            vals.append(v)
    return vals


def extract_amount(text):
    lines = text.splitlines()
    tagged = []
    for line in lines:
        low = line.lower()
        if any(k in low for k in TOTAL_KEYWORDS):
            vals = _nums_in_line(line)
            if vals:
                tagged.append(max(vals))
    if tagged:
        return max(tagged)
    allvals = [v for line in lines for v in _nums_in_line(line) if v >= 10]
    return max(allvals) if allvals else None


def _try_parse_line_date(line):
    m = DATE_LOOKING.search(line)
    if not m:
        return None
    try:
        return dateutil_parser.parse(m.group(0), fuzzy=True, dayfirst=True).date()
    except Exception:
        return None


def extract_date(text):
    lines = text.splitlines()
    for line in lines:
        low = line.lower()
        if any(k in low for k in DATE_KEYWORDS):
            d = _try_parse_line_date(line)
            if d:
                return d
    for line in lines:
        d = _try_parse_line_date(line)
        if d:
            return d
    return None


def extract_vendor(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        low = line.lower()
        for k in VENDOR_KEYWORDS:
            if k in low:
                val = line.split(":", 1)[-1].strip()
                if val:
                    return val[:120]
    if lines:
        return lines[0][:120]
    return None


def extract_invoice_no(text):
    m = re.search(
        r"(?:invoice\s*(?:no\.?|number|#)?|inv\.?\s*no\.?|ref(?:erence)?\s*(?:no\.?)?)"
        r"\s*[:#-]?\s*([A-Za-z0-9\-\/]{2,30})",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return None


def extract_description(text, filename):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    candidates = [l for l in lines if len(l.split()) >= 3
                  and not any(k in l.lower() for k in EXCLUDE_DESC_KEYWORDS)]
    if candidates:
        return max(candidates, key=len)[:200]
    return pathlib.Path(filename).stem


def extract_invoice(filename, data):
    result = {"filename": filename, "description": None, "date": None, "amount": None,
              "vendor": None, "reference": None, "error": None}
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    text = None
    try:
        if ext == "pdf":
            import pdfplumber
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                parts = [(page.extract_text() or "") for page in pdf.pages]
                text = "\n".join(parts)
            if not text or len(text.strip()) < 15:
                result["error"] = ("This PDF has no extractable text (it is likely a scanned "
                                    "image). OCR of scanned PDFs is not supported - please "
                                    "provide a text-based PDF or a plain image file instead.")
                return result
        elif ext in ("png", "jpg", "jpeg", "tif", "tiff", "bmp"):
            engine = get_ocr_engine()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            ocr_lines, _ = engine(np.array(img))
            text = "\n".join(line[1] for line in (ocr_lines or []) if len(line) > 1 and line[1])
            if not text or len(text.strip()) < 5:
                result["error"] = "Could not extract any readable text from this image."
                return result
        else:
            result["error"] = f"Unsupported invoice file type: .{ext or '?'}"
            return result
    except Exception as e:
        result["error"] = f"Failed to read this file: {e}"
        return result

    result["amount"] = extract_amount(text)
    result["date"] = extract_date(text)
    result["vendor"] = extract_vendor(text)
    result["reference"] = extract_invoice_no(text)
    result["description"] = extract_description(text, filename)
    if result["amount"] is None or result["date"] is None:
        missing = []
        if result["amount"] is None:
            missing.append("amount")
        if result["date"] is None:
            missing.append("date")
        result["error"] = f"Could not reliably read {', '.join(missing)} from this invoice."
    return result


# ---------------------------------------------------------------------------
# matching invoices <-> GL rows
# ---------------------------------------------------------------------------

def amounts_close(a, b, rel_tol=0.005, abs_tol=5):
    if a is None or b is None:
        return False
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))


def fuzzy_close(a, b, threshold=0.6):
    an, bn = normalize(a), normalize(b)
    if not an or not bn:
        return False
    return score_text(an, bn) >= threshold


def ref_equal(a, b):
    an, bn = normalize(a), normalize(b)
    return bool(an) and bool(bn) and an == bn


def match_invoices_to_gl(invoices, gl_addition_rows, date_window_days=20):
    matched_pairs = []
    unmatched_invoices = []
    amount_mismatches = []
    ambiguous = []
    consumed_gl = set()

    for inv in invoices:
        candidates_exact = []
        candidates_other = []
        for gi, gl in enumerate(gl_addition_rows):
            if gi in consumed_gl:
                continue
            if gl["date"] is None or inv["date"] is None:
                continue
            if abs((gl["date"] - inv["date"]).days) > date_window_days:
                continue
            if amounts_close(inv["amount"], gl["amount"]):
                candidates_exact.append(gi)
            elif fuzzy_close(inv.get("vendor"), gl.get("vendor")) or ref_equal(inv.get("reference"), gl.get("reference")):
                candidates_other.append(gi)

        if len(candidates_exact) == 1:
            gi = candidates_exact[0]
            matched_pairs.append((inv, gl_addition_rows[gi]))
            consumed_gl.add(gi)
        elif len(candidates_exact) > 1:
            ambiguous.append((inv, [gl_addition_rows[g] for g in candidates_exact]))
        elif len(candidates_other) == 1:
            gi = candidates_other[0]
            amount_mismatches.append((inv, gl_addition_rows[gi]))
            consumed_gl.add(gi)
        elif len(candidates_other) > 1:
            ambiguous.append((inv, [gl_addition_rows[g] for g in candidates_other]))
        else:
            unmatched_invoices.append(inv)

    remaining_gl = [gl_addition_rows[gi] for gi in range(len(gl_addition_rows)) if gi not in consumed_gl]
    return matched_pairs, unmatched_invoices, remaining_gl, amount_mismatches, ambiguous


# ---------------------------------------------------------------------------
# depreciation figures - full-month-of-purchase / stop-before-disposal-month
# ---------------------------------------------------------------------------

def _month_index(d):
    return d.year * 12 + d.month


def compute_addition_figures(cost, rate, purchase_date, period_end):
    months = _month_index(period_end) - _month_index(purchase_date) + 1
    months = max(0, months)
    dep = round(cost * rate * (months / 12.0), 2)
    dep = min(dep, cost)
    wdv = round(cost - dep, 2)
    return {
        "opening": 0, "dep_prior_year": 0, "dep_current_year": dep, "acc_dep": dep,
        "wdv": wdv, "first_year_dep_months": months, "first_year_dep": dep,
    }


def compute_disposal_figures(existing_wdv, existing_acc_dep, rate, disposal_date, period_start):
    months = _month_index(disposal_date) - _month_index(period_start)
    months = max(0, months)
    dep = round(existing_wdv * rate * (months / 12.0), 2)
    dep = min(dep, existing_wdv)
    new_acc_dep = round(existing_acc_dep + dep, 2)
    new_wdv = round(max(0.0, existing_wdv - dep), 2)
    return {
        "dep_current_year": dep, "acc_dep": new_acc_dep, "wdv": new_wdv,
        "first_year_dep_months": months,
    }


# ---------------------------------------------------------------------------
# automatic period tracking - the user is never asked for a period/date range.
# The register's own "closing position" (tracked internally) is the lower
# bound, and the latest transaction date found in this run's own GL/invoice
# data is the upper bound (the new closing position going forward).
# ---------------------------------------------------------------------------

def estimate_closing_date(tabs):
    """Best-effort: the latest purchase date already recorded anywhere in the
    baseline FAR, used only to seed the running closing position the first
    time a baseline is uploaded. Never shown to, or asked of, the user."""
    latest = None
    for tab in tabs.values():
        ws_vals = tab["ws_vals"]
        date_col = tab["col_map"].get("date_of_purchase")
        if date_col is None:
            continue
        for r in range(tab["header_row"] + 1, tab["last_row"] + 1):
            d = parse_any_date(ws_vals.cell(row=r, column=date_col + 1).value)
            if d and (latest is None or d > latest):
                latest = d
    return latest


def determine_period_bounds(state, gl_rows, invoices):
    """Derive the period this run covers with no date input from the user:
    period_start is the day after the register's last known closing
    position, and period_end is the latest transaction date actually found
    in this run's GL/invoice data (falling back to today if none)."""
    all_dates = [r["date"] for r in gl_rows if r.get("date")] + \
                [inv["date"] for inv in invoices if inv.get("date")]
    closing_date = None
    cd_raw = state.get("far_closing_date")
    if cd_raw:
        try:
            closing_date = datetime.date.fromisoformat(cd_raw)
        except ValueError:
            closing_date = None

    if all_dates:
        period_end = max(all_dates)
    else:
        period_end = datetime.date.today()

    if closing_date:
        period_start = closing_date + datetime.timedelta(days=1)
    elif all_dates:
        period_start = min(all_dates)
    else:
        period_start = period_end

    if period_end < period_start:
        period_end = period_start
    return period_start, period_end, closing_date


# ---------------------------------------------------------------------------
# dedup / row lookups against the FAR already on disk
# ---------------------------------------------------------------------------

def dedup_check(tab, cost, purchase_date):
    ws_vals = tab["ws_vals"]
    col_map = tab["col_map"]
    cost_col = col_map.get("cost")
    date_col = col_map.get("date_of_purchase")
    if cost_col is None or date_col is None:
        return False
    for r in range(tab["header_row"] + 1, tab["last_row"] + 1):
        v_cost = parse_any_number(ws_vals.cell(row=r, column=cost_col + 1).value)
        v_date = parse_any_date(ws_vals.cell(row=r, column=date_col + 1).value)
        if v_cost is None or v_date is None:
            continue
        if abs(v_cost - cost) <= max(1.0, 0.005 * cost) and abs((v_date - purchase_date).days) <= 5:
            return True
    return False


def find_existing_row_for_disposal(tab, gl_row):
    desc_target = normalize(gl_row.get("description"))
    ref_target = normalize(gl_row.get("reference"))
    ws_vals = tab["ws_vals"]
    col_map = tab["col_map"]
    desc_col = col_map.get("description")
    code_col = col_map.get("item_code")
    best = None
    for r in range(tab["header_row"] + 1, tab["last_row"] + 1):
        row_desc = normalize(ws_vals.cell(row=r, column=desc_col + 1).value) if desc_col is not None else ""
        if not row_desc:
            continue
        score = score_text(desc_target, row_desc) if desc_target else 0.0
        if code_col is not None and ref_target:
            row_code = normalize(ws_vals.cell(row=r, column=code_col + 1).value)
            if row_code and row_code == ref_target:
                score = max(score, 0.95)
        if best is None or score > best[1]:
            best = (r, score)
    if best and best[1] >= 0.55:
        return best[0]
    return None


# ---------------------------------------------------------------------------
# writing rows back into the workbook
# ---------------------------------------------------------------------------

def append_addition(tab, values, description_text):
    ws = tab["ws"]
    col_map = tab["col_map"]
    new_row = tab["last_row"] + 1

    def setcol(key, val):
        idx = col_map.get(key)
        if idx is not None and val is not None:
            ws.cell(row=new_row, column=idx + 1, value=val)

    setcol("description", description_text)
    setcol("category", tab.get("common_category"))
    setcol("date_of_purchase", values["purchase_date"])
    setcol("year_of_purchase", values["purchase_date"].year)
    setcol("cost", values["cost"])
    setcol("cost_current_year", values["cost"])
    setcol("cost_prior_year", 0)
    setcol("depr_rate", tab["rate"])
    setcol("opening", 0)
    setcol("dep_prior_year", 0)
    setcol("dep_current_year", values["dep_current_year"])
    setcol("acc_dep", values["acc_dep"])
    setcol("wdv", values["wdv"])
    setcol("first_year_dep_months", values["first_year_dep_months"])
    setcol("first_year_dep", values["first_year_dep"])
    setcol("vendor", values.get("vendor"))
    setcol("invoice_no", values.get("invoice_no"))
    tab["last_row"] = new_row
    return new_row


def update_disposal(tab, row, values):
    ws = tab["ws"]
    col_map = tab["col_map"]

    def setcol(key, val):
        idx = col_map.get(key)
        if idx is not None and val is not None:
            ws.cell(row=row, column=idx + 1, value=val)

    setcol("dep_current_year", values["dep_current_year"])
    setcol("acc_dep", values["acc_dep"])
    setcol("wdv", values["wdv"])
    setcol("first_year_dep_months", values["first_year_dep_months"])
    desc_idx = col_map.get("description")
    if desc_idx is not None:
        cur = ws.cell(row=row, column=desc_idx + 1).value or ""
        if "[DISPOSED" not in str(cur):
            ws.cell(row=row, column=desc_idx + 1,
                     value=f"{cur} [DISPOSED {values['disposal_date'].isoformat()}]")


def ensure_exceptions_sheet(wb):
    if EXCEPTIONS_SHEET_NAME in wb.sheetnames:
        return wb[EXCEPTIONS_SHEET_NAME]
    ws = wb.create_sheet(EXCEPTIONS_SHEET_NAME)
    ws.append(["Run Date", "Period", "Source", "Item", "Reason", "Details"])
    return ws


def append_exceptions(ws, run_date, period_label, exceptions_list):
    for exc in exceptions_list:
        ws.append([run_date, period_label, exc.get("source", ""), exc.get("item", ""),
                   exc.get("reason", ""), exc.get("details", "")])


# ---------------------------------------------------------------------------
# baseline upload / status
# ---------------------------------------------------------------------------

def set_baseline(filename, data):
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        raise ValueError("Baseline FAR must be an Excel workbook (.xlsx)")
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=False)
    wb_vals = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    tabs = detect_asset_tabs(wb, wb_vals)
    CURRENT_FAR_PATH.write_bytes(data)
    st = load_state()
    st["baseline_uploaded_at"] = datetime.datetime.now().isoformat()
    st["run_history"] = []
    st["last_run"] = None
    seeded_closing = estimate_closing_date(tabs)
    st["far_closing_date"] = seeded_closing.isoformat() if seeded_closing else None
    save_state(st)
    return {
        "status": "ok",
        "tabs_detected": [{"name": n, "rate": t["rate"]} for n, t in tabs.items()],
        "message": f"Baseline FAR saved. Recognized {len(tabs)} asset-class tab(s).",
    }


def get_status():
    st = load_state()
    result = {
        "has_baseline": has_baseline(),
        "last_run": st.get("last_run"),
        "run_history": st.get("run_history", [])[-20:],
        "far_closing_date": st.get("far_closing_date"),
        "tabs": [],
    }
    if result["has_baseline"]:
        try:
            wb = openpyxl.load_workbook(CURRENT_FAR_PATH, data_only=False)
            wb_vals = openpyxl.load_workbook(CURRENT_FAR_PATH, data_only=True)
            tabs = detect_asset_tabs(wb, wb_vals)
            result["tabs"] = [
                {"name": n, "rate": t["rate"], "rows": t["last_row"] - t["header_row"]}
                for n, t in tabs.items()
            ]
        except Exception as e:
            result["status_error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# the main per-period update
# ---------------------------------------------------------------------------

def run_update(invoice_payload, gl_payload):
    if not CURRENT_FAR_PATH.exists():
        raise NoBaselineError()

    wb = openpyxl.load_workbook(CURRENT_FAR_PATH, data_only=False)
    wb_vals = openpyxl.load_workbook(CURRENT_FAR_PATH, data_only=True)
    tabs = detect_asset_tabs(wb, wb_vals)

    state = load_state()
    run_date = datetime.date.today().isoformat()
    exceptions = []
    additions = []
    disposals = []
    skipped_duplicates = []

    if not tabs:
        exceptions.append({
            "source": "FAR", "item": "(workbook)",
            "reason": "No recognizable asset-class tabs found in the baseline FAR",
            "details": ("Expected columns such as Description, Date of Purchase, Cost and "
                         "Depreciation Rate were not found in any sheet."),
        })

    gl_filename, gl_bytes = gl_payload
    gl_rows, gl_error = parse_gl_bytes(gl_filename, gl_bytes)
    if gl_error:
        exceptions.append({"source": "GL", "item": gl_filename, "reason": "GL file rejected",
                            "details": gl_error})
        gl_rows = []

    invoices = [extract_invoice(fn, data) for fn, data in invoice_payload]
    readable_invoices = []
    for inv in invoices:
        if inv["error"]:
            exceptions.append({"source": "Invoice", "item": inv["filename"],
                                "reason": "Invoice could not be read", "details": inv["error"]})
        else:
            readable_invoices.append(inv)

    transactional = [r for r in gl_rows if r["date"] is not None and r["amount"] not in (None, 0)]
    for r in transactional:
        tabname, reason = classify_gl_row(r, tabs) if tabs else (None, "no asset tabs available")
        r["matched_tab"] = tabname
        r["classify_reason"] = reason

    disposal_rows = [r for r in transactional if is_disposal_row(r)]
    addition_rows = [r for r in transactional if not is_disposal_row(r)]

    # No period is ever asked of the user - it is derived from the register's
    # own last recorded closing position plus whatever dates actually show up
    # in this run's GL/invoices.
    period_start, period_end, prior_closing_date = determine_period_bounds(
        state, transactional, readable_invoices)
    period_label = f"{period_start.isoformat()} to {period_end.isoformat()}"

    addition_rows_classified = []
    for r in addition_rows:
        if r["matched_tab"] is None:
            exceptions.append({
                "source": "GL",
                "item": r.get("description") or r.get("reference") or "(unlabeled transaction)",
                "reason": "Could not classify GL transaction to any existing FAR tab",
                "details": f"Classification text: '{r.get('classification_raw')}'. {r.get('classify_reason') or ''}",
            })
        else:
            addition_rows_classified.append(r)

    matched_pairs, unmatched_invoices, remaining_gl, amount_mismatches, ambiguous = \
        match_invoices_to_gl(readable_invoices, addition_rows_classified)

    for inv in unmatched_invoices:
        exceptions.append({
            "source": "Invoice", "item": inv["filename"],
            "reason": "No matching GL entry found for this invoice",
            "details": f"Invoice amount {inv['amount']}, date {inv['date']}",
        })

    for gl in remaining_gl:
        exceptions.append({
            "source": "GL", "item": gl.get("description") or gl.get("reference") or "(unlabeled transaction)",
            "reason": "GL transaction has no matching invoice",
            "details": f"GL amount {gl['amount']}, date {gl['date']}, classified as {gl['matched_tab']}",
        })

    for inv, gl in amount_mismatches:
        exceptions.append({
            "source": "Invoice/GL", "item": inv["filename"],
            "reason": "Amount mismatch between invoice and GL entry",
            "details": (f"Invoice amount {inv['amount']} on {inv['date']} vs GL amount "
                         f"{gl['amount']} on {gl['date']} (tab: {gl['matched_tab']})"),
        })

    for inv, gl_list in ambiguous:
        exceptions.append({
            "source": "Invoice", "item": inv["filename"],
            "reason": "Multiple plausible GL matches found; cannot determine a single match",
            "details": f"{len(gl_list)} candidate GL transactions within the date/amount window",
        })

    for inv, gl in matched_pairs:
        tab = tabs[gl["matched_tab"]]
        purchase_date = inv["date"]
        cost = inv["amount"]
        if prior_closing_date is not None and purchase_date <= prior_closing_date:
            exceptions.append({
                "source": "Invoice/GL", "item": inv["filename"],
                "reason": "Transaction date is on or before the register's last recorded closing position",
                "details": (f"Date {purchase_date} is not after the last closing position "
                             f"({prior_closing_date}); please verify this was not already "
                             "recorded in a prior update before re-supplying it."),
            })
            continue
        if tab["rate"] is None:
            exceptions.append({
                "source": "FAR", "item": gl["matched_tab"],
                "reason": "Could not determine the depreciation rate used in this tab; asset not added",
                "details": inv["filename"],
            })
            continue
        if dedup_check(tab, cost, purchase_date):
            skipped_duplicates.append({"tab": gl["matched_tab"], "item": inv["filename"],
                                        "cost": cost, "date": purchase_date.isoformat()})
            continue
        figures = compute_addition_figures(cost, tab["rate"], purchase_date, period_end)
        desc_text = inv.get("description") or pathlib.Path(inv["filename"]).stem
        extra = []
        if inv.get("vendor") and "vendor" not in tab["col_map"]:
            extra.append(f"Vendor: {inv['vendor']}")
        if inv.get("reference") and "invoice_no" not in tab["col_map"]:
            extra.append(f"Inv#: {inv['reference']}")
        if extra:
            desc_text = f"{desc_text} ({', '.join(extra)})"
        row = append_addition(tab, {**figures, "purchase_date": purchase_date, "cost": cost,
                                     "vendor": inv.get("vendor"), "invoice_no": inv.get("reference")},
                               desc_text)
        additions.append({
            "tab": gl["matched_tab"], "row": row, "description": desc_text, "cost": cost,
            "date": purchase_date.isoformat(), "dep_this_period": figures["dep_current_year"],
            "wdv": figures["wdv"],
        })

    for gl in disposal_rows:
        if gl["matched_tab"] is None:
            exceptions.append({
                "source": "GL", "item": gl.get("description") or "(disposal)",
                "reason": "Could not classify disposal transaction to any existing FAR tab",
                "details": f"Classification text: '{gl.get('classification_raw')}'",
            })
            continue
        tab = tabs[gl["matched_tab"]]
        row = find_existing_row_for_disposal(tab, gl)
        if row is None:
            exceptions.append({
                "source": "GL", "item": gl.get("description") or "(disposal)",
                "reason": "Disposal identified in GL but no matching existing asset row was found",
                "details": f"tab: {gl['matched_tab']}, date {gl['date']}, amount {gl['amount']}",
            })
            continue
        desc_idx = tab["col_map"].get("description")
        current_desc = str(tab["ws"].cell(row=row, column=desc_idx + 1).value or "") if desc_idx is not None else ""
        if "[DISPOSED" in current_desc:
            skipped_duplicates.append({"tab": gl["matched_tab"], "item": f"row {row} (already marked disposed)"})
            continue
        wdv_idx = tab["col_map"].get("wdv")
        acc_idx = tab["col_map"].get("acc_dep")
        wdv_val = parse_any_number(tab["ws_vals"].cell(row=row, column=wdv_idx + 1).value) if wdv_idx is not None else None
        acc_val = parse_any_number(tab["ws_vals"].cell(row=row, column=acc_idx + 1).value) if acc_idx is not None else 0
        if wdv_val is None or tab["rate"] is None:
            exceptions.append({
                "source": "GL", "item": gl.get("description") or "(disposal)",
                "reason": "Could not determine existing carrying amount or rate for this asset; disposal not processed",
                "details": f"tab: {gl['matched_tab']}, row {row}",
            })
            continue
        figures = compute_disposal_figures(wdv_val, acc_val or 0, tab["rate"], gl["date"], period_start)
        figures["disposal_date"] = gl["date"]
        update_disposal(tab, row, figures)
        disposals.append({
            "tab": gl["matched_tab"], "row": row, "disposal_date": gl["date"].isoformat(),
            "dep_this_period": figures["dep_current_year"], "wdv": figures["wdv"],
        })

    exc_ws = ensure_exceptions_sheet(wb)
    append_exceptions(exc_ws, run_date, period_label, exceptions)
    wb.save(CURRENT_FAR_PATH)

    st = load_state()
    st.setdefault("run_history", [])
    entry = {
        "run_date": run_date, "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(), "additions": len(additions),
        "disposals": len(disposals), "exceptions": len(exceptions),
        "skipped_duplicates": len(skipped_duplicates),
    }
    st["run_history"].append(entry)
    st["last_run"] = entry
    # Advance the register's own closing position - this is what makes the
    # next update automatic, with no period ever asked of the user again.
    if additions or disposals:
        new_closing = period_end
        if prior_closing_date and prior_closing_date > new_closing:
            new_closing = prior_closing_date
        st["far_closing_date"] = new_closing.isoformat()
    save_state(st)

    return {
        "status": "ok", "period": period_label, "additions": additions, "disposals": disposals,
        "exceptions": exceptions, "skipped_duplicates": skipped_duplicates,
    }
