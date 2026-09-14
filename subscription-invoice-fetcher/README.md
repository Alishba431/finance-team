# Subscription Invoice Fetcher

Matches each row of a subscription/invoice breakdown sheet to the matching
invoice email in one admin Gmail inbox, saves the PDF/image attachment, and
returns your sheet with a Status column and a link to the saved file next to
each row.

See `SPEC.md` for the full approved specification (what it does, the exact
matching rules, and what's explicitly out of scope). This README only covers
running it.

## What you need before running it

1. **A Google Cloud OAuth 2.0 client** (type "Web application") for the admin
   Gmail account, so the tool can ask for read-only Gmail access:
   - Create a project in Google Cloud Console, enable the **Gmail API**.
   - Create an OAuth client (Web application type).
   - Set these two environment variables before starting the app:
     - `GMAIL_CLIENT_ID`
     - `GMAIL_CLIENT_SECRET`
   - The app will tell you, on its home page, the exact redirect URL to add
     to that OAuth client's "Authorized redirect URIs" (it depends on the
     host/port you run the app on, e.g. `http://localhost:8000/auth/gmail/callback`).
   - This is a one-time setup step per Google Cloud project; the finance team
     then does a one-time sign-in in the browser to grant access.

2. **The invoice breakdown sheet**, as a real `.xlsx` file, with columns
   (headers can vary in wording) for: enterprise/client name, a
   breakdown/description, an invoice date, and an amount.

## Install

```
pip install -r requirements.txt
```

## Run

```
python -m app.main
```

The app binds to the `PORT` environment variable if set, otherwise 8000.
Open the printed URL in a browser:

1. Click "Connect Gmail" and sign in as the admin account (one-time; the
   token is cached locally under `data/gmail_token.json` and reused on
   later runs — reconnect only if it's revoked or expires).
2. Upload the breakdown sheet and click "Run".
3. Download the result sheet — each row has a Status and, where found, a
   link to the saved invoice attachment (also saved under
   `data/attachments/<run>/`).

No data ships with this tool and none is required for it to start — the
Gmail connection card and upload form are simply empty/disconnected until
you provide them.

## Notes

- Only the one authorized Gmail inbox is ever read. Nothing is sent, and
  nothing is written back to that inbox.
- Matching is text + amount based, per SPEC.md's rules — it does not read
  the contents of the PDF itself.
- Re-run per breakdown cycle with a fresh copy of the sheet; each run gets
  its own output file and attachment folder.
