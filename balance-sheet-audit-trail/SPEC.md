# Balance Sheet Audit Trail — Odoo Source Option

## What it does
Before running the audit, you now choose where the SOFP, Trial Balance, and General Ledger data comes from: upload the same three Excel files as before, or have the tool pull that data directly from Odoo for a period you specify. Whichever source you pick, the audit trail is built and shown exactly as it is today — this change only affects how the data gets in, not how it's checked.

## What you give it
1. **Source choice** — "Upload Excel files" or "Pull from Odoo," picked fresh each time you run the tool.
2. **If Excel:** the same three files as the current version — SOFP, TB, and GL — in the same layout the tool already expects. Nothing about these files changes.
3. **If Odoo:** just a reporting period — a start date and end date (e.g. one calendar month). No files, no login, no API key entry — the tool connects to the staging Odoo instance using the key already stored in Orion.
4. If Odoo returns no posted entries for the period you gave it, the tool stops and tells you the period was empty rather than producing a report.

## What it gives you back
The same audit trail output the tool already produces — no change to its layout or content. The only addition: a line at the top of the output stating which source was used for that run (e.g. "Source: Odoo, 1–31 Aug 2026" or "Source: Excel upload, files uploaded 2 Sep 2026") so anyone reviewing it later knows where the numbers came from.

## Rules it follows
1. Odoo pull and Excel upload are alternatives for the same run — you pick one, not both, each time you run the audit.
2. When pulling from Odoo, only **posted** (validated) journal entries are included; draft or unposted entries are left out, the same way a finalized Excel export would be.
3. The Odoo pull covers a single company — the one on the staging Odoo instance — and a single period per run, matching how the Excel version works today.
4. Data pulled from Odoo is converted into the same row-and-column format as the Excel files, so every existing audit rule and check runs identically regardless of source.
5. Every run pulls fresh data at the moment you run it — nothing from Odoo is cached or reused between runs.
6. The audit comparison logic itself — what counts as an exception, how figures are matched across SOFP/TB/GL — is unchanged by this update.

## What I am assuming
- The Excel upload path, its file format, and the audit logic all stay exactly as they are in the current version — this change only adds a second way to get data in.
- "Pull from Odoo" means the standard Accounting module records (`account.move`, `account.move.line`, `account.account`) on the staging instance already connected to Orion — not a custom module.
- You'll enter a start and end date for the Odoo pull rather than picking from a list of Odoo's own accounting periods.
- Only posted entries count; anything still in draft in Odoo is treated as not yet part of the books.
- One company, one period per run — no multi-company consolidation or multi-period pulls in a single run.
- If the Odoo connection fails (instance unreachable, key rejected, etc.), the tool reports the failure plainly and does not fall back to asking for Excel files mid-run.

## Not included
- Writing back to Odoo, or correcting/adjusting Odoo records from within this tool — the pull is read-only.
- Pulling from more than one company or consolidating multiple companies in one run.
- Automatic period-over-period comparison across multiple Odoo pulls in a single run.
- Live/continuous sync with Odoo — each run is a one-time pull at the moment you run it, not a standing connection.
- Any change to the audit trail's comparison rules themselves — this update only changes how the source data arrives.

## How you will know it works
- Run the tool, choose "Pull from Odoo," give it a period, and check the pulled SOFP/TB/GL totals against what Odoo's own reports show for that same period.
- Run the same period once via Excel upload and once via Odoo pull and confirm the audit trail output is the same either way.
- Pick a period with no posted entries in Odoo and confirm the tool tells you clearly, instead of producing an empty or misleading report.
- Confirm the Excel upload option still runs exactly as it did before this change, with no new prompts or steps.