"""Google OAuth (web flow) for read-only access to one admin Gmail inbox.

Requires two environment variables, set up once by whoever administers the
Google Cloud project the finance team uses:

  GMAIL_CLIENT_ID       - OAuth 2.0 client id ("Web application" type)
  GMAIL_CLIENT_SECRET   - OAuth 2.0 client secret

The redirect URI is computed at request time from the URL the app is
actually being served at (http(s)://host:port/auth/gmail/callback) so it
works whatever port the tool is started on. That exact URL must be added to
the OAuth client's "Authorized redirect URIs" list in Google Cloud Console
before the admin signs in.

No secrets are hardcoded here; if the env vars are absent the app still
starts, it just reports Gmail as "not configured" and refuses to start the
OAuth flow until they're set.
"""
import json
import os
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleAuthRequest

from .paths import TOKEN_PATH

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def oauth_configured() -> bool:
    return bool(os.environ.get("GMAIL_CLIENT_ID") and os.environ.get("GMAIL_CLIENT_SECRET"))


def _client_config(redirect_uri: str) -> dict:
    client_id = os.environ["GMAIL_CLIENT_ID"]
    client_secret = os.environ["GMAIL_CLIENT_SECRET"]
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def build_flow(redirect_uri: str) -> Flow:
    return Flow.from_client_config(_client_config(redirect_uri), scopes=SCOPES, redirect_uri=redirect_uri)


def save_credentials(creds: Credentials) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())


def load_credentials() -> Optional[Credentials]:
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text())
        creds = Credentials.from_authorized_user_info(data, SCOPES)
    except Exception:
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            save_credentials(creds)
        except Exception:
            return None
    return creds


def is_connected() -> bool:
    creds = load_credentials()
    return bool(creds and creds.valid)


def disconnect() -> None:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
