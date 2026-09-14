# Subscription Invoice Fetcher

## What it does
Takes your subscription invoice breakdown sheet and the admin Gmail inbox, and for each breakdown row automatically finds the matching invoice email sent around that invoice's date. It saves the invoice attachment and marks each row as found, found-but-needs-review, or not found — so nobody has to search Gmail by hand for each line.

## What you give it
- **The invoice breakdown sheet** — one row per enterprise/breakdown line, with at minimum: enterprise name, breakdown description, invoice date, and amount. If a row is missing any of these, it is skipped and marked "Missing Data" — it will not be guessed at.
  - Note: the file you attached (`subcription_invoice.tsv`) came through in a format the tool couldn't read. Please provide the real sheet as an Excel file or Google Sheet with those columns before this can be built or tested.
- **Admin Gmail access** — a one-time sign-in/authorization for the finance team to grant the tool read access to the admin mailbox. The tool only ever reads this one inbox; it never sends mail from it.

## What it gives you back
Your same breakdown sheet, returned with two things added next to each row:
1. A **Status** column: `Found (Attachment)`, `Found — No Attachment (Review)`, `Not Found`, or `Missing Data`.
2. A **link to the saved invoice file** for any row where one was found (the attachment itself is saved to a folder, one file per row, named by enterprise and date — spreadsheet cells can't hold an actual PDF, so the sheet links to it instead).

## Rules it follows
1. For each breakdown row, the tool searches the admin inbox for emails dated from 7 days before to 7 days after that row's invoice date.
2. An email counts as a **match** only if its sender, subject, or body reasonably corresponds to the row's enterprise name, and its stated amount matches the row's amount (allowing for rounding differences of a few cents/rupees).
3. A match only counts as **Found** if the matching email has a PDF or image attachment. That attachment is saved and linked in the sheet.
4. If a matching email is found but has no attachment, the row is marked **Found — No Attachment (Review)** for someone to check manually — it is not treated as complete.
5. If no matching email is found in the search window, the row is marked **Not Found**. No email, reminder, or escalation is sent automatically — it's left for manual follow-up.
6. If a row is missing the enterprise name, date, or amount needed to search, it's marked **Missing Data** and skipped rather than guessed at.

## What I am assuming
- The breakdown sheet will be provided as a proper Excel/Google Sheet (not the unreadable format submitted), with an enterprise name, a breakdown/description, an invoice date, and an amount column.
- "Admin email" means one specific Gmail inbox, and finance will authorize the tool to read it once.
- Matching is by enterprise name text plus amount — not by any other reference number, since none was described as being on the sheet.
- Each row represents one billing period for one enterprise; you'll re-run the tool each month/cycle with a fresh copy of the sheet.
- Since a spreadsheet cell can't literally contain a PDF, "invoice in the sheet" means a link to the saved attachment file, not the file embedded in the cell.

## Not included
- No verification that the PDF's actual contents (amount, vendor, etc.) match the row — only the email's sender/subject/body and stated amount are checked.
- No automatic emailing, reminders, or escalation for "Not Found" rows.
- No connection to Odoo or any accounting system — this tool only works off the sheet and Gmail.
- No searching of multiple mailboxes — only the one admin inbox that's authorized.

## How you will know it works
- Pick a handful of rows you already know the invoice for; confirm the tool found the right attachment and linked it correctly.
- Pick a row you know has no invoice email; confirm it's marked "Not Found," not silently skipped.
- Check a row where the email exists but you know it has no attachment; confirm it's flagged "Found — No Attachment (Review)," not marked complete.
- Delete a required value (e.g. amount) from one row; confirm it's marked "Missing Data" rather than matched incorrectly.