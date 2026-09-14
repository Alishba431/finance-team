"""Search the authorized admin Gmail inbox for an invoice matching one
breakdown row, and save any PDF/image attachment found on the match.

Read-only: only users.messages().list / get / attachments().get are called.
Nothing is ever sent, modified, or deleted in the mailbox.
"""
import base64
import re
import html as html_lib
from datetime import timedelta

from googleapiclient.discovery import build

MAX_CANDIDATES = 150  # cap per-row Gmail reads so one huge mailbox can't hang a run
ATTACHMENT_MIME_PREFIXES = ("application/pdf", "image/")

_TAG_RE = re.compile(r"<[^>]+>")
_AMOUNT_RE = re.compile(r"(?<![\d.])\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?(?![\d])")
_SUFFIX_WORDS = {
    "enterprise", "enterprises", "pvt", "ltd", "llc", "inc", "co", "company",
    "corp", "corporation", "the", "and", "&",
}


def build_service(creds):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def normalize_name(name: str) -> set:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return {w for w in words if w not in _SUFFIX_WORDS}


def _strip_html(text: str) -> str:
    return html_lib.unescape(_TAG_RE.sub(" ", text or ""))


def _header(headers, name):
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _walk_parts(payload, out_text, out_attachments):
    if not payload:
        return
    mime_type = payload.get("mimeType", "")
    filename = payload.get("filename") or ""
    body = payload.get("body", {}) or {}
    if filename and body.get("attachmentId"):
        out_attachments.append({
            "filename": filename,
            "mimeType": mime_type,
            "attachmentId": body["attachmentId"],
        })
    elif mime_type in ("text/plain", "text/html") and body.get("data"):
        try:
            decoded = base64.urlsafe_b64decode(body["data"] + "==").decode("utf-8", errors="ignore")
        except Exception:
            decoded = ""
        out_text.append(_strip_html(decoded) if mime_type == "text/html" else decoded)
    for part in payload.get("parts", []) or []:
        _walk_parts(part, out_text, out_attachments)


def fetch_candidates(service, start_date, end_date):
    """List + fetch full messages dated within [start_date, end_date] (inclusive).

    Gmail's `before:` is exclusive, so we push it a day past end_date.
    Returns a list of dicts: sender, subject, body_text, attachments, msg_id.
    """
    query = "after:{} before:{}".format(
        start_date.strftime("%Y/%m/%d"),
        (end_date + timedelta(days=1)).strftime("%Y/%m/%d"),
    )
    message_ids = []
    page_token = None
    while len(message_ids) < MAX_CANDIDATES:
        resp = service.users().messages().list(
            userId="me", q=query, pageToken=page_token,
            maxResults=min(50, MAX_CANDIDATES - len(message_ids)),
        ).execute()
        message_ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    candidates = []
    for mid in message_ids:
        try:
            msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception:
            continue
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        text_parts, attachments = [], []
        _walk_parts(payload, text_parts, attachments)
        candidates.append({
            "msg_id": mid,
            "sender": _header(headers, "From"),
            "subject": _header(headers, "Subject"),
            "body_text": "\n".join(text_parts)[:20000],
            "snippet": msg.get("snippet", ""),
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


def download_attachment(service, msg_id, attachment):
    data = service.users().messages().attachments().get(
        userId="me", messageId=msg_id, id=attachment["attachmentId"]
    ).execute()
    raw = base64.urlsafe_b64decode(data["data"] + "==")
    return raw
