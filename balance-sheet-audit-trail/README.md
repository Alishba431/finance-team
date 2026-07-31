# Balance Sheet Audit Trail

A small internal tool for Finance/Audit that takes an uploaded Excel workbook
(SOFP + Trial Balance + General Ledger sheets) and, for every line on the
Statement of Financial Position, builds an audit trail down to the
supporting Trial Balance accounts and their General Ledger transactions —
so no one has to scroll through the whole GL by hand.

## What it does

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

## Running it

```
pip install -r requirements.txt
python main.py
```

The app serves on `http://localhost:8000` (or on the port given in the
`PORT` environment variable). Open it in a browser, upload a workbook, and
follow the on-screen steps. With no file uploaded yet, the home page shows
the upload form and nothing else — there is no sample or seed data bundled
with the tool.

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
