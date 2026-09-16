# Gmail Invoice Fetcher — Sign-in Update

## What it does
This tool reads your subscription invoice sheet, then logs into the admin Gmail inbox and searches for the invoice email matching each enterprise/breakdown row around its billing date. This update only changes *how you sign in* — instead of a Google "Connect" button that needed developer setup in advance, you type the admin email address and a Gmail App Password directly into the tool's page each time you run it.

## What you give it
- **The invoice sheet** (same one as before, uploaded through the tool's page) — no changes to what it contains or requires.
- **Admin Gmail address** — a plain text field.
- **Gmail App Password** — a 16-character code you generate yourself from your Google Account under Security → 2-Step Verification → App Passwords. This field is masked (shows dots, not the characters) as you type it.

If either the email or the app password is left blank, or Google rejects them, the tool stops before touching the sheet and tells you sign-in failed — it will not attempt to guess or reuse a previous password.

## What it gives you back
No change from the current tool: the same on-screen results and/or spreadsheet output listing, for each enterprise/breakdown row, whether a matching invoice email was found and what was done with it. This update does not add, remove, or reformat any output field.

## Rules it follows
1. You must enter both the admin email address and the app password before the tool will start; it does not remember them from a previous run.
2. The app password is used only to connect to the inbox for the current run — it is held in memory while the tool works and is discarded the moment the run ends or the page is closed.
3. The app password is never written to a file, never saved in any settings, and never printed to any log or error message — if sign-in fails, the failure message will name the reason (e.g. "Google rejected these credentials") but never echo back the password itself.
4. Once signed in, the tool searches the inbox for each row the same way it already does today — no change to which emails count as a match or how the search date-window is chosen.
5. If sign-in fails (wrong email, wrong or expired app password, or 2-Step Verification not turned on for that account), the tool reports this clearly and does not process any rows.

## What I am assuming
- The email address and app password are for the same admin Gmail inbox the tool already searches today — this update doesn't change which inbox is used, only how the tool connects to it.
- You (or whoever runs the tool) will generate the app password from the admin Google account beforehand; the tool does not walk you through Google's app-password screen.
- The 2-Step Verification requirement for app passwords is something you'll enable on the admin account once, outside this tool, if it isn't already on.
- Nothing about which invoices count as a match, how dates are compared, or what the output looks like is changing — only the sign-in step.
- The tool runs in an environment you trust (your own machine or a shared internal server) — the "held in memory only" protection covers the app password from being saved to disk or logs, not from someone with access to that machine's memory while it's running.

## Not included
- This update does not change or improve the invoice-matching logic itself — any issues with which emails get picked as matches are unchanged from today's behavior.
- It does not store or remember the app password for next time — you'll re-enter it every run, same as re-uploading the sheet.
- It does not walk you through creating the app password in your Google Account — that's a one-time step you do yourself on Google's site.
- It does not support inboxes or accounts that require the old Cloud Console / OAuth setup — going forward, only the email + app password sign-in works.

## How you will know it works
- You can enter the admin email and a valid app password and the tool signs in and runs, without needing anything set up in a Cloud Console beforehand.
- Entering a wrong app password gives a clear "sign-in failed" message and stops the run — it doesn't silently continue or crash.
- After a run finishes (success or failure), you can check that the app password field is empty again if you reopen or refresh the page.
- The invoice results you get back look the same as they did with the old Google sign-in — same rows matched, same output layout.