# Subscription Invoice Fetcher

Matches each row of a subscription/invoice breakdown sheet to the matching
invoice email in one admin Gmail inbox, saves the PDF/image attachment, and
returns your sheet with a Status column and a link to the saved file next to
each row.

See `SPEC.md` for the full approved specification (what it does, the exact
matching rules, and what's explicitly out of scope). This README only covers
running it.

## What you need before running it

1. **A Gmail App Password for the admin inbox.** Generate it yourself, once
   per machine you sign in from, at Google Account → Security → 2-Step
   Verification → App Passwords (2-Step Verification must already be turned
   on for that account). It's a 16-character code — no Cloud Console project,
   OAuth client, or developer setup required. You paste this straight into
   the tool's page each time you run it; nothing is saved between runs.

2. **The invoice breakdown sheet**, as a real `.xlsx` file, with columns
   (headers can vary in wording) for: enterprise/client/department name, a
   breakdown/description, an invoice date, and an amount. If the sheet also
   has a separate vendor/partner column (e.g. "Partner (Vendor)"), the tool
   searches Gmail using that vendor name instead of the enterprise/department
   name, and still shows the enterprise/department name in the report. Sheets
   without a separate vendor column match on the enterprise column, as
   before.

## Install

```
pip install -r requirements.txt
```

## Optional: save found invoices to Google Drive instead of this app

By default, found attachments are saved locally and linked from
`data/attachments/<run>/`. To have them uploaded to a Google Drive folder
instead (so the sheet's "Invoice File" link is a Drive link), set up a
service account once:

1. In Google Cloud Console, create (or reuse) a project, enable the
   **Google Drive API**, then create a **Service Account** and download its
   JSON key file.
2. In Google Drive, create (or pick) a folder for invoices and **share it**
   with the service account's email address (found in the JSON key, field
   `client_email`) as an **Editor**.
3. Before running the app, set:
   ```
   export GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service-account-key.json
   export GOOGLE_DRIVE_FOLDER_ID=<the folder's id from its Drive URL>
   ```
   Optionally also set `GOOGLE_DRIVE_MAKE_PUBLIC=true` if you want each
   uploaded file to be viewable by anyone with the link, rather than only
   people the folder is already shared with (off by default, since invoices
   can be sensitive).

If these aren't set, the tool works exactly as before — no Drive project or
setup is required.

## Run

```
python -m app.main
```

The app binds to the `PORT` environment variable if set, otherwise 8000.
Open the printed URL in a browser:

1. Enter the admin Gmail address and the App Password you generated, and
   choose the breakdown sheet, then click "Sign in and run". The tool signs
   in to the inbox over IMAP for this run only — the app password is held in
   memory for the duration of the run and is never written to disk, saved in
   any settings, or logged. You re-enter it every run.
2. If the email or app password is wrong (or 2-Step Verification isn't on
   for that account), the tool reports "Google rejected these credentials"
   and stops before touching the sheet.
3. Download the result sheet — each row has a Status and, where found, a
   link to the saved invoice attachment (also saved under
   `data/attachments/<run>/`).

No data ships with this tool and none is required for it to start — the
sign-in fields and upload form are simply empty until you fill them in.

## Notes

- Only the one admin Gmail inbox you sign in to is ever read, over IMAP.
  Nothing is sent, and nothing is written back to that inbox.
- Matching is text + amount based, per SPEC.md's rules — it does not read
  the contents of the PDF itself. This is unchanged from before this
  sign-in update.
- Re-run per breakdown cycle with a fresh copy of the sheet; each run gets
  its own output file and attachment folder.
