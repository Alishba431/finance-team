"""IMAP sign-in for the admin Gmail inbox using an email address + a Gmail
App Password (Google Account -> Security -> 2-Step Verification -> App
Passwords). Replaces the previous Google OAuth / Cloud Console flow.

The app password is only ever held in memory for the duration of a single
run's login. It is never written to disk, never saved in any settings file,
and never included in any log line or error message.
"""
import imaplib

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


class GmailSignInError(Exception):
    """Raised when sign-in cannot proceed. Never include the password here."""


def connect(email_address: str, app_password: str) -> imaplib.IMAP4_SSL:
    """Logs into the admin Gmail inbox over IMAP and returns the connection.

    Raises GmailSignInError with a clear, password-free reason on any
    failure (blank fields, wrong credentials, 2-Step Verification not on).
    """
    email_address = (email_address or "").strip()
    # Google displays App Passwords with spaces every 4 characters for
    # readability (e.g. "abcd efgh ijkl mnop"), but the real password has no
    # spaces. Strip ALL whitespace, not just leading/trailing, so a
    # copy-pasted password with its original spacing still works.
    app_password = "".join((app_password or "").split())
    if not email_address or not app_password:
        raise GmailSignInError("Enter both the admin Gmail address and the app password.")

    try:
        imap_conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    except Exception as exc:
        raise GmailSignInError(f"Could not reach Gmail's IMAP server: {exc}")

    try:
        imap_conn.login(email_address, app_password)
    except imaplib.IMAP4.error:
        try:
            imap_conn.logout()
        except Exception:
            pass
        raise GmailSignInError(
            "Google rejected these credentials. Double-check the admin email address and "
            "the app password, and make sure 2-Step Verification is turned on for that account."
        )
    except Exception as exc:
        try:
            imap_conn.logout()
        except Exception:
            pass
        raise GmailSignInError(f"Could not sign in to Gmail: {exc}")

    return imap_conn


def disconnect(imap_conn) -> None:
    if not imap_conn:
        return
    try:
        imap_conn.close()
    except Exception:
        pass
    try:
        imap_conn.logout()
    except Exception:
        pass
