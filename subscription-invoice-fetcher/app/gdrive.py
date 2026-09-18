"""Optional Google Drive upload for found invoice attachments.

Configured via two environment variables:
  GOOGLE_SERVICE_ACCOUNT_JSON - path to a Google service-account key file
  GOOGLE_DRIVE_FOLDER_ID      - id of a Drive folder shared with that
                                 service account (Editor access), where
                                 attachments get uploaded

If either is missing, or the upload fails for any reason, Drive upload is
silently skipped and the caller falls back to serving the attachment from
this app's own /attachments route instead. No Drive setup is required to
run the tool without this feature.

Uploaded files are private to the service account (and anyone the folder
is already shared with) unless GOOGLE_DRIVE_MAKE_PUBLIC=true, in which case
each uploaded file is also made viewable by anyone with the link.
"""
import io
import os

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_service = None
_service_tried = False


def _build_service():
    global _service, _service_tried
    if _service_tried:
        return _service
    _service_tried = True
    key_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not key_path or not os.path.exists(key_path):
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)
        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception:
        _service = None
    return _service


def is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_DRIVE_FOLDER_ID")) and _build_service() is not None


def upload_pdf(filename: str, content: bytes, mime_type: str = "application/pdf"):
    """Uploads `content` to the configured Drive folder.

    Returns a shareable webViewLink on success, or None if Drive isn't
    configured, there's nothing to upload, or the upload fails.
    """
    if not content:
        return None
    service = _build_service()
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if not service or not folder_id:
        return None
    from googleapiclient.http import MediaIoBaseUpload
    try:
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
        file = service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True,
        ).execute()
        file_id = file.get("id")
        if file_id and os.environ.get("GOOGLE_DRIVE_MAKE_PUBLIC", "").lower() in ("1", "true", "yes"):
            try:
                service.permissions().create(
                    fileId=file_id,
                    body={"type": "anyone", "role": "reader"},
                    supportsAllDrives=True,
                ).execute()
            except Exception:
                pass
        return file.get("webViewLink")
    except Exception:
        return None
