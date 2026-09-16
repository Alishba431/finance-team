"""Search the admin Gmail inbox (over IMAP) for an invoice matching one
breakdown row, and fetch any PDF/image attachment found on the match.

Read-only: only IMAP SELECT (readonly) / SEARCH / FETCH are used. Nothing is
ever sent, modified, or deleted in the mailbox.
"""
import email
import re
import html as html_lib
from datetime import timedelta
from email.header import decode_header

MAX_CANDIDATES = 150  # cap per-row IMAP reads so one huge mailbox can't hang a run
ATTACHMENT_MIME_PREFIXES = ("application/pdf", "image/")

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


def _walk_message(msg, out_text, out_attachments):
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        content_disposition = str(part.get("Content-Disposition") or "")
        content_type = part.get_content_type()
        filename = part.get_filename()
        if filename and "attachment" in content_disposition.lower():
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
        _walk_message(msg, text_parts, attachments)
        candidates.append({
            "uid": uid.decode() if isinstance(uid, bytes) else uid,
            "sender": _decode_header_value(msg.get("From")),
            "subject": _decode_header_value(msg.get("Subject")),
            "body_text": "\n".join(text_parts)[:20000],
            "snippet": "",
            "attachments": [a for a in attachments if a["mimeType"].startswith(ATTACHMENT_MIME_PREFIXES)],
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


def amount_matches(expected_amount: float, candidate: dict, tolerance: float = 1.0) -> bool:
    haystack = " ".join([candidate.get("subject", ""), candidate.get("body_text", ""),
                          candidate.get("snippet", "")])
    for raw in _AMOUNT_RE.findall(haystack):
        cleaned = raw.replace(",", "").replace(" ", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if abs(value - expected_amount) <= tolerance:
            return True
    return False


def find_best_match(candidates, enterprise_name, expected_amount):
    """Returns (status, candidate_or_none) per the spec's rules.

    status is one of: 'found', 'review', 'not_found'
    """
    matches = [c for c in candidates
               if name_matches(enterprise_name, c) and amount_matches(expected_amount, c)]
    if not matches:
        return "not_found", None
    with_attachment = [c for c in matches if c["attachments"]]
    if with_attachment:
        return "found", with_attachment[0]
    return "review", matches[0]


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
