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
