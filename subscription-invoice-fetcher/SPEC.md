# Subscription Invoice Fetcher

## What it does
This tool reads your subscription invoice spreadsheet, finds the real column headers even when the sheet has a title or section label sitting above them (e.g. "20001 Software Subscriptions - Engineering" in row 1), and checks every invoice row underneath for the enterprise name, invoice date, description, and amount it needs. Rows that are genuinely missing one of those are flagged as Missing Data — rows aren't flagged just because the tool was looking at the wrong row for headers.

## What you give it
- **The invoice spreadsheet.** Somewhere in its first 10 rows there must be one row of column headers containing recognizable names for: Enterprise (or Client/Company/Vendor), a description/breakdown column (e.g. Communication / Breakdown), an invoice date, and an amount. Above that header row, the sheet can have a title or section-label row — the tool now skips past it instead of treating it as the header.
- If no row in the first 10 looks like a header at all, the tool skips that sheet and reports it as unreadable rather than marking every row Missing Data.

## What it gives you back
The same results report you already get: one row per invoice, showing the enterprise, invoice date, description, and amount the tool read out, with a Missing Data flag on any row that's actually missing one of those fields — plus, now, a separate "sheet unreadable" note if no header row could be found at all.

## Rules it follows
1. The tool looks at the first 10 rows of the sheet and picks the one row that best matches expected column names (enterprise/client/company/vendor, breakdown/description/communication, date, amount) as the real header row.
2. Any row above that header row — titles, section labels — is treated as decoration and never read as invoice data.
3. Once the header row is found, a row is marked Missing Data only if it is actually missing the enterprise, invoice date, description/breakdown, or amount for that row.
4. If no row in the first 10 looks enough like a header, the whole sheet is reported as unreadable, instead of every row in it being marked Missing Data.

## What I am assuming
- The real header row is always within the first 10 rows of the sheet — never further down.
- When a column is literally named "Enterprise," that is the enterprise/client name column.
- Each sheet has one header row, not headers split across two rows.
- A sheet that doesn't have any row resembling a header (wrong keywords, non-standard names) is better reported to you as "unreadable" than guessed at.
- Everything else about the report — its layout and how you receive it — stays exactly as it is today; only how the header row is found is changing.

## Not included
- No way to manually tell the tool which row is the header if its automatic guess is wrong on a particular sheet — you'd need to flag that sheet to us.
- Sheets whose column names don't contain any of the recognized keywords (enterprise/client/company/vendor, breakdown/description/communication, date, amount) will still be reported as unreadable, not processed by guesswork.
- Headers split across two separate rows are not supported.

## How you will know it works
- Upload the real sheet with the "20001 Software Subscriptions..." title row in row 1 — the invoice rows should now come back with real data instead of every row saying Missing Data.
- A row that's genuinely missing, say, an invoice date should still show Missing Data for that row.
- A normal sheet with no title row above the headers should still work exactly as it did before this fix.
- A sheet with no recognizable header row anywhere in the first 10 rows should come back as "unreadable," not as a wall of Missing Data rows.