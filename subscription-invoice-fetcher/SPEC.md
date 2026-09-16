# Subscription Invoice Fetcher (v4)

## What it does
This tool reads your subscription/invoice tracking sheet, searches your Gmail inbox for a matching invoice email for each row, and produces a report showing which invoices were found and which are still missing. It now correctly searches Gmail using the actual vendor name (e.g. Microsoft, Zoom) instead of your internal department name, so real invoices actually get matched.

## What you give it
- **The sheet (Excel file)** — one row per subscription/expense. It needs, somewhere in its header row (which may not be row 1 — the tool scans the first 10 rows to find the real header):
  - An **Enterprise** column (your internal department/cost-center, e.g. Engineering, Admin) — used for display only.
  - A **Partner (Vendor)** column (the company that actually sent the invoice, e.g. Microsoft, Obsidian.md, Zoom) — used to search Gmail. If your sheet has no separate vendor column, the tool will search using the Enterprise column instead, same as before.
  - A breakdown/description column, an invoice date column, and an amount column.
  - If any of these required columns can't be found at all, that row (or the whole sheet, if the header itself can't be located) is marked as **Missing Data** rather than guessed at.
- **Gmail access** — the inbox the tool searches for invoice emails.

## What it gives you back
A report (on screen, and/or downloadable) with one row per line item from your sheet, showing:
- The department (Enterprise), the vendor name, the description, date, and amount, as they appear on your sheet.
- Whether a matching invoice email was found in Gmail, and if so, a link/reference to it.
- Rows marked **Missing Data** for anything the tool couldn't read from your sheet.
While it runs, you'll see a live progress line (e.g. "Processing row 3 of 24...") instead of a plain "Loading" message.

## Rules it follows
1. The tool looks for a header row within the first 10 rows of the sheet; the row with the most recognizable column-name keywords (enterprise/client/company/vendor, breakdown/description, date, amount) is treated as the header, and anything above it (titles, section labels) is ignored.
2. To find the invoice-matching value for a row, the tool first looks for a distinct vendor/partner column (keywords like "vendor", "partner", "supplier"). If found, that column's value is used to search Gmail sender, subject, and body.
3. If no such vendor/partner column exists on the sheet, the tool falls back to using the Enterprise/department column for the Gmail search, as it did before this fix.
4. The Enterprise/department column is always shown in the output report as its own field, regardless of which column was used for matching.
5. A row is marked **Missing Data** if the required columns (vendor-or-enterprise, breakdown, date, amount) can't be identified for it.
6. An invoice is considered "matched" for a row when a Gmail message is found whose sender, subject, or body contains the vendor value (or, in fallback sheets, the enterprise value) used for that row.

## What I am assuming
- "Vendor/partner column" is any column whose header contains "vendor", "partner", or "supplier" — this is separate from the enterprise/client/company group and is not treated as the same thing.
- Matching is a plain text search for the vendor value against sender, subject, and body — not fuzzy matching (so "Microsoft" won't match "MSFT" unless the email literally contains "Microsoft").
- The progress indicator is a simple text line updated per row; it doesn't need a graphical progress bar.
- Everything else about how the tool finds emails, dates, and amounts, and how it formats the report, stays exactly as it works today — this change only affects which column is used as the search key, and adds the progress line.

## Not included
- No change to how emails are matched once the correct name is used (no smarter fuzzy/alias matching, e.g. recognizing "Obsidian.md" and "Obsidian Publishing" as the same vendor).
- No handling for sheets where the vendor name in the Partner (Vendor) column doesn't match the wording used in the actual invoice email (e.g. sheet says "AWS" but email is from "Amazon Web Services") — these will still show as not found.
- No editing of your sheet — the Enterprise/Vendor distinction is only used internally by the tool, nothing is written back.

## How you will know it works
1. On a sheet with both an Enterprise and a Partner (Vendor) column, rows for vendors like Microsoft or Zoom that have a real invoice email in the inbox now come back matched, instead of Missing Data.
2. The report still shows the correct department name (Enterprise) next to each row, even though matching used the vendor name.
3. On an older sheet that only has an Enterprise column (no separate vendor column), matching still works exactly as it did before this change.
4. While processing a large sheet, you see the row-by-row progress message update instead of a static "Loading" screen.