# Balance Sheet Audit Trail

A small internal tool for Finance/Audit that takes an uploaded Excel workbook
(SOFP + Trial Balance + General Ledger sheets) and, for every line on the
Statement of Financial Position, builds an audit trail down to the
supporting Trial Balance accounts and their General Ledger transactions —
so no one has to scroll through the whole GL by hand.

## What it does

0. **Choose a data source**, fresh each time you run the tool: upload the
   same three Excel files as always, or pull the same data straight from
   Odoo for a period you specify. Whichever you pick, the audit trail is
   built and shown exactly the same way — the report always opens with a
   "Source: ..." line so anyone reviewing it later knows where the numbers
   came from (e.g. "Source: Odoo, 01 Aug 2026 – 31 Aug 2026" or "Source:
   Excel upload, files uploaded 02 Sep 2026").
1. **Upload** an `.xlsx`/`.xlsm` workbook. Nothing is written to disk — the
   file is held in server memory only for as long as the process runs / the
   session is used.
2. The tool detects (or lets you correct) which sheet is the SOFP, which is
   the Trial Balance, and which is the General Ledger.
3. It parses:
   - **SOFP** — finds the row with year headers (e.g. 2024, 2025, ...) and
     uses the most recent year's column as the reported closing balance for
     each line.
   - **TB** — finds the Debit/Credit (or single "Closing Balance") columns
     and reads each account's closing balance. If there are several
     Debit/Credit pairs (e.g. Initial / Period / End), the one labelled
     "End"/"Closing" is used (falling back to the rightmost pair).
   - **GL** — expects one block per account (an account-name row, an
     optional "Initial Balance" row, then transaction rows with
     Date/Debit/Credit/Balance) and computes each account's closing balance
     from its own transactions (opening + debits − credits), not just by
     trusting a stated total.
4. It then **automatically suggests** which TB accounts belong to each SOFP
   line (by name similarity plus generic accounting-terminology hints —
   e.g. "depreciation", "payable", "receivable") and which GL account
   supports each TB account.
5. For every SOFP line it shows: the TB-derived total, the GL-derived
   total, the difference against the reported SOFP figure, and the
   difference between TB and GL — with explicit flags for missing
   mappings, sign mismatches, or amounts that don't reconcile within your
   chosen materiality tolerance.
6. Because automatic name-matching can't be perfect for every chart of
   accounts, the report also lists every **unmapped TB account** and lets
   you assign it to the correct SOFP line (or move/unmap one) directly in
   the browser — the report recalculates immediately.
7. Drill down into any TB/GL account to see its individual transactions,
   or export a CSV summary (per SOFP line → per TB/GL account) and a full
   GL transaction-detail CSV for the audit file.
8. A **dark mode** toggle in the header switches the whole app to a dark
   theme (it remembers your choice for next time via the browser's local
   storage, and defaults to your OS preference on first visit).

## Pulling from Odoo instead of uploading

Choosing "Pull from Odoo" and giving a start/end date pulls posted
(validated) journal entries directly from `account.move.line` /
`account.account` for that period via Odoo's standard read-only XML-RPC
API (`odoo_source.py` — no extra Python package needed, `xmlrpc.client` is
part of the standard library), reshapes them into the same row/column
structures the Excel parser produces, and runs the identical reconciliation.
No file, login or API key is entered in the browser for this path.

This requires the server the tool runs on to have the Odoo connection
configured via environment variables (these are never accepted from the
browser and are not settable from the UI):

| Variable          | Required | Meaning                                             |
|-------------------|----------|------------------------------------------------------|
| `ODOO_URL`        | yes      | Base URL of the staging Odoo instance                |
| `ODOO_DB`         | yes      | Odoo database name                                   |
| `ODOO_USERNAME`   | yes      | Login used to authenticate                           |
| `ODOO_API_KEY`    | yes      | Odoo API key (used as the XML-RPC password)          |
| `ODOO_COMPANY_ID` | no       | Restrict the pull to one company id, if the instance has more than one |

If these aren't set, the "Pull from Odoo" option returns a clear error and
tells you to use the Excel upload option instead; it never falls back to
asking for files mid-run. If Odoo has no posted entries for the period you
gave it, the tool stops and tells you the period was empty instead of
producing a report. Only posted entries are ever included — drafts are
left out, the same way a finalized Excel export would be — and each run
pulls fresh data at that moment; nothing from Odoo is cached or reused
between runs.

## Running it

```
pip install -r requirements.txt
python main.py
```

The app serves on `http://localhost:8000` (or on the port given in the
`PORT` environment variable). Open it in a browser, choose a data source
(Excel upload or Odoo pull), and follow the on-screen steps. With no data
provided yet, the home page shows the source choice and upload form and
nothing else — there is no sample or seed data bundled with the tool.

## Notes, assumptions and known gaps

- **Sheet shapes assumed.** The parser is generic (no filenames, sheet
  names or account names are hardcoded) but it does expect the same
  general layout as the sample workbook: a year-header row on the SOFP
  sheet, Debit/Credit columns on the TB sheet, and one block per account
  with Date/Debit/Credit/Balance columns on the GL sheet. If your workbook
  is laid out very differently, the relevant step will show a clear error
  instead of guessing at numbers.
- **Automatic mapping is a starting point, not a final answer.** Line
  items like "Property and equipment" often group several differently
  named TB/GL accounts (e.g. per-asset-class costs and their accumulated
  depreciation). Name-similarity matching, even with generic accounting
  synonyms built in, will not always get every account right on the first
  pass — that's why unmapped accounts are always listed explicitly and can
  be reassigned in the UI rather than being silently included or excluded.
- **Sign conventions** differ between systems (e.g. a credit-normal
  contra-asset account may be stored as a negative number in the GL but
  presented as a positive deduction in the SOFP). The tool flags a likely
  sign-convention mismatch when the TB-derived total is the mirror image
  of the reported figure, rather than silently correcting the sign.
- **Note-linked disclosures** (separate note sheets referenced by a "Note"
  column on the SOFP, e.g. fixed-asset or grant schedules) are not
  cross-referenced automatically — only the SOFP/TB/GL reconciliation
  described above is performed.
- **No persistence.** Uploaded workbooks and reconciliation state live only
  in server memory for the running process; restarting the app clears all
  sessions.
- Formula errors in the source workbook (e.g. `#REF!`) are surfaced as an
  explicit flag on the affected SOFP line rather than treated as zero or
  skipped.
- **Odoo SOFP line items are generic groupings, not a formatted statement.**
  Odoo doesn't export a laid-out Statement of Financial Position by default,
  so the Odoo pull builds SOFP "lines" by summing account closing balances
  grouped by Odoo's own `account_type` field (e.g. "Cash and Cash
  Equivalents", "Trade and Other Payables"). These labels may not match your
  organization's actual SOFP captions word-for-word; the existing
  name/concept-similarity matching still applies on top of them.
- Only **posted** journal entries are ever pulled from Odoo; income/expense
  and off-balance accounts are pulled into the Trial Balance/General Ledger
  like any other account but are not summed into an SOFP line (a balance
  sheet doesn't report them) — they will show up as "unmapped TB accounts",
  same as an Excel TB with extra accounts would.
- The Odoo pull is single-company, single-period, read-only, and one-shot —
  no consolidation, no period-over-period comparison, and nothing is written
  back to Odoo.
