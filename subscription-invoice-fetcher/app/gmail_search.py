"""Search the admin Gmail inbox (over IMAP) for an invoice matching one
breakdown row, and fetch any PDF/image attachment found on the match.

Read-only: only IMAP SELECT (readonly) / SEARCH / FETCH are used. Nothing is
ever sent, modified, or deleted in the mailbox.
"""
import email
import io
import re
import html as html_lib
from datetime import timedelta, datetime, timezone
from email.header import decode_header
from pathlib import Path

from .paths import DATA_DIR

_DEBUG_LOG_PATH = DATA_DIR / "gmail_debug.log"


def _debug(line: str) -> None:
    try:
        with open(_DEBUG_LOG_PATH, "a") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {line}\n")
    except Exception:
        pass

MAX_CANDIDATES = 150  # cap per-row IMAP reads so one huge mailbox can't hang a run
ATTACHMENT_MIME_PREFIXES = ("application/pdf", "image/")
# Some senders (and some mail clients that relay/forward a message) label a
# real PDF/image attachment with a generic binary mime type instead of its
# real one. When that happens the filename's extension is the only reliable
# signal left, so it's checked in addition to ATTACHMENT_MIME_PREFIXES below.
GENERIC_BINARY_MIME_TYPES = {"application/octet-stream", "application/x-download", "binary/octet-stream"}
ATTACHMENT_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".heic", ".tif", ".tiff", ".bmp", ".webp")

_TAG_RE = re.compile(r"<[^>]+>")
_AMOUNT_RE = re.compile(r"(?<![\d.])\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?(?![\d])")
_SUFFIX_WORDS = {
    "enterprise", "enterprises", "pvt", "ltd", "llc", "inc", "co", "company",
    "corp", "corporation", "the", "and", "&",
}


def normalize_name(name: str) -> set:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return {w for w in words if w not in _SUFFIX_WORDS}


def _strip_html(text: str) -> str:
    return html_lib.unescape(_TAG_RE.sub(" ", text or ""))


def _decode_header_value(value) -> str:
    if not value:
        return ""
    decoded = ""
    for text, enc in decode_header(str(value)):
        if isinstance(text, bytes):
            try:
                decoded += text.decode(enc or "utf-8", errors="ignore")
            except (LookupError, ValueError):
                decoded += text.decode("utf-8", errors="ignore")
        else:
            decoded += text
    return decoded


def _is_attachment_part(content_type: str, filename: str) -> bool:
    if not filename:
        return False
    if content_type.startswith(ATTACHMENT_MIME_PREFIXES):
        return True
    # A generic/binary mime type is still worth treating as an attachment if
    # the filename itself has a recognizable extension - some senders (and
    # some relaying mail clients) mislabel real PDFs/images this way.
    if content_type in GENERIC_BINARY_MIME_TYPES:
        return Path(filename).suffix.lower() in ATTACHMENT_EXTENSIONS
    return False


def _walk_message(msg, out_text, out_attachments, debug_label=""):
    for part in msg.walk():
        maintype = part.get_content_maintype()
        if maintype == "multipart":
            continue
        content_type = part.get_content_type()
        filename = part.get_filename()
        if debug_label:
            _debug(
                f"{debug_label} part maintype={maintype!r} content_type={content_type!r} "
                f"filename={filename!r} content_disposition={part.get('Content-Disposition')!r}"
            )
        # A part counts as an attachment whenever it carries a filename and a
        # mime type we care about, regardless of Content-Disposition: many
        # billing systems send PDFs with no Content-Disposition header at
        # all, or with "inline" instead of "attachment", and the file is
        # just as real and downloadable either way.
        if _is_attachment_part(content_type, filename):
            out_attachments.append({
                "filename": _decode_header_value(filename),
                "mimeType": content_type,
            })
        elif content_type in ("text/plain", "text/html"):
            try:
                payload = part.get_payload(decode=True)
                decoded = payload.decode(part.get_content_charset() or "utf-8", errors="ignore") if payload else ""
            except Exception:
                decoded = ""
            out_text.append(_strip_html(decoded) if content_type == "text/html" else decoded)


def fetch_candidates(imap_conn, start_date, end_date):
    """SEARCH + FETCH messages dated within [start_date, end_date] (inclusive).

    IMAP's BEFORE is exclusive, so we push it a day past end_date.
    Returns a list of dicts: uid, sender, subject, body_text, attachments.
    """
    imap_conn.select("INBOX", readonly=True)
    since = start_date.strftime("%d-%b-%Y")
    before = (end_date + timedelta(days=1)).strftime("%d-%b-%Y")
    typ, data = imap_conn.uid("search", None, f"(SINCE {since} BEFORE {before})")
    uids = data[0].split() if typ == "OK" and data and data[0] else []
    uids = uids[:MAX_CANDIDATES]
    _debug(f"SEARCH (SINCE {since} BEFORE {before}) typ={typ!r} -> {len(uids)} candidate uid(s)")

    candidates = []
    for uid in uids:
        typ, msg_data = imap_conn.uid("fetch", uid, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        try:
            msg = email.message_from_bytes(raw)
        except Exception:
            continue
        text_parts, attachments = [], []
        uid_str = uid.decode() if isinstance(uid, bytes) else uid
        subject = _decode_header_value(msg.get("Subject"))
        _walk_message(msg, text_parts, attachments, debug_label=f"uid={uid_str} subject={subject!r}")
        _debug(f"uid={uid_str} subject={subject!r} -> attachments={attachments}")
        candidates.append({
            "uid": uid.decode() if isinstance(uid, bytes) else uid,
            "sender": _decode_header_value(msg.get("From")),
            "subject": _decode_header_value(msg.get("Subject")),
            "body_text": "\n".join(text_parts)[:20000],
            "snippet": "",
            "attachments": attachments,
        })
    return candidates


def name_matches(enterprise_name: str, candidate: dict) -> bool:
    target_words = normalize_name(enterprise_name)
    if not target_words:
        return False
    haystack = " ".join([candidate.get("sender", ""), candidate.get("subject", ""),
                          candidate.get("body_text", ""), candidate.get("snippet", "")])
    haystack_words = normalize_name(haystack)
    overlap = target_words & haystack_words
    # Require most of the enterprise-name's significant words to appear.
    return len(overlap) >= max(1, round(len(target_words) * 0.6))


def _amount_in_text(text: str, expected_amount: float, tolerance: float = 1.0) -> bool:
    for raw in _AMOUNT_RE.findall(text or ""):
        cleaned = raw.replace(",", "").replace(" ", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if abs(value - expected_amount) <= tolerance:
            return True
    return False


def amount_matches(expected_amount: float, candidate: dict, tolerance: float = 1.0) -> bool:
    haystack = " ".join([candidate.get("subject", ""), candidate.get("body_text", ""),
                          candidate.get("snippet", "")])
    return _amount_in_text(haystack, expected_amount, tolerance)


def _is_pdf_attachment(attachment: dict) -> bool:
    mime = (attachment.get("mimeType") or "").lower()
    if mime.startswith("application/pdf"):
        return True
    return Path(attachment.get("filename") or "").suffix.lower() == ".pdf"


def _extract_pdf_text(data: bytes) -> str:
    """Best-effort text extraction from a PDF attachment's raw bytes.

    Some invoices only state their amount inside the attached PDF, never in
    the email's own subject/body - amount_matches() alone can never find
    those. This is the fallback: only invoked when a candidate already
    passed name_matches() but failed amount_matches() on the email text
    itself, so it stays rare (most invoices DO put the amount in the body).
    A malformed/encrypted/scanned-with-no-text-layer PDF just yields no
    text, which is treated as "no amount found here" rather than an error -
    this is a best-effort fallback, not a hard requirement.
    """
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return ""


def find_best_match(imap_conn, candidates, enterprise_name, expected_amount):
    """Returns (status, candidate_or_none) per the spec's rules.

    status is one of: 'found', 'review', 'not_found'

    Amount matching is checked first against the email's own subject/body
    text (cheap, no extra IMAP round-trip). Only for a candidate that
    already matches by name but not by that text is a PDF attachment (if
    any) downloaded and its extracted text checked for the amount too -
    this keeps the common case (amount already in the email body) exactly
    as fast as before.
    """
    name_matched = [c for c in candidates if name_matches(enterprise_name, c)]
    if not name_matched:
        return "not_found", None

    body_matches = [c for c in name_matched if amount_matches(expected_amount, c)]
    if body_matches:
        with_attachment = [c for c in body_matches if c["attachments"]]
        if with_attachment:
            return "found", with_attachment[0]
        return "review", body_matches[0]

    for candidate in name_matched:
        for attachment in candidate["attachments"]:
            if not _is_pdf_attachment(attachment):
                continue
            raw = download_attachment(imap_conn, candidate["uid"], attachment)
            if not raw:
                continue
            if _amount_in_text(_extract_pdf_text(raw), expected_amount):
                return "found", candidate

    return "not_found", None


def download_attachment(imap_conn, uid, attachment):
    typ, msg_data = imap_conn.uid("fetch", uid, "(RFC822)")
    if typ != "OK" or not msg_data or not msg_data[0]:
        return b""
    msg = email.message_from_bytes(msg_data[0][1])
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if filename and _decode_header_value(filename) == attachment["filename"]:
            payload = part.get_payload(decode=True)
            return payload or b""
    return b""
