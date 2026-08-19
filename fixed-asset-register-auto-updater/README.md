# Fixed Asset Register (FAR) Auto-Updater

A small local web tool that keeps a Fixed Asset Register up to date, period
after period, from the invoices and GL you give it - without ever rebuilding
or manually re-typing the register. See `SPEC.md` for the full approved
specification this tool implements; this README covers install/run and the
practical details of how it works.

## What it does

1. **Upload your existing FAR once**, as the baseline. The tool inspects the
   workbook and learns its tabs (one per asset class), columns, and the
   depreciation rate already in use in each tab. It never invents a tab,
   column, or rate.
2. **Whenever you have new purchases**, upload the invoices (PDF/image) and the
   GL covering those transactions (.xlsx, .xlsm, .xls or .csv) - there is no
   period/date range to fill in. The GL can be a clean extract, or simply the
   same FAR/GL workbook you already maintain if it keeps its GL as a sheet
   inside that workbook (the tool finds the GL sheet on its own and ignores
   everything else in that upload; the FAR tabs it actually updates are
   always read from the register it is already tracking, not from this
   file). The tool:
   - Reads description/date/vendor/reference/amount off each invoice.
   - Reads date/amount/account-classification off the GL.
   - Matches an invoice to a GL line by amount + date (and vendor/reference
     where available), uses the GL's classification to pick the existing FAR
     tab, and uses the invoice's details to fill in the new row.
   - Works out the period to depreciate for itself: it picks up from the
     register's own last recorded closing position and runs through to the
     latest transaction date found in this batch - so you're never asked for
     a period start/end.
   - Calculates that new asset's depreciation for the period using the same
     rate already used in that tab, charging a full month in the month of
     purchase.
   - Looks for disposal transactions in the GL (by keyword) and updates the
     matching existing asset row instead of adding a new one, stopping
     depreciation the month before disposal.
   - Skips anything already in the FAR (no duplicates on re-run).
   - Anything it can't confidently resolve is written to an
     **"Exceptions – Review Required"** tab in the workbook, with a reason.
3. **Download the updated FAR.** It becomes the new baseline automatically -
   next time you only need that batch's invoices and GL; the register
   remembers where it left off.

## Install & run

```
pip install -r requirements.txt
python app.py
```

The app serves on `http://localhost:8000` (or the port in the `PORT`
environment variable, if set). Open it in a browser.

On first start, with no FAR uploaded yet, the app runs fine and simply shows
an empty state asking you to upload your baseline FAR.

### OCR for image invoices

Reading **image** invoices (PNG/JPG/TIFF/BMP - including photos and scans)
uses a local, free OCR engine (`rapidocr-onnxruntime`) that installs entirely
via `pip install -r requirements.txt`, with its recognition models bundled
inside the Python package itself. There is **no separate system install**
(no `tesseract`, no `apt`/`brew` step) and no image is ever sent anywhere -
recognition runs fully on this machine. **PDF invoices with a normal text
layer work the same way they always have, with no OCR needed.** If an image
is too blurry/unclear for the OCR engine to extract usable text, that invoice
is simply flagged in Exceptions for manual review rather than guessed at, and
the rest of the batch keeps processing.

## How the tool decides things (so you don't have to)

- **Tabs/columns/rates**: read from the baseline FAR by matching header text
  (e.g. "Date of Purchase", "Cost", "Depr. Rate", "WDV") - not asked of you.
- **Which tab a purchase belongs to**: decided by matching the GL's
  account/classification text against the tab names (and, if needed, the
  values already in that tab's "Category" column) in the baseline FAR.
- **Depreciation rate for a new asset**: the rate already used by the other
  rows in that same tab (most common rate found).
- **The period covered by a run**: from the day after the register's last
  recorded closing position (tracked automatically after each run, seeded
  from the latest purchase date already in the baseline on first upload)
  through to the latest transaction date actually found in this run's own
  GL/invoices. Never asked of you.
- **Depreciation for the purchase period**: `Cost × Rate × (full months held
  this period ÷ 12)`, with the purchase month always counted as a full
  month.
- **Disposals**: recognized from GL narration/classification text containing
  words like "disposal", "written off", "scrap", "sold". The matching
  existing row is found by description/reference similarity, its
  depreciation for the period is calculated up to (and including) the month
  before disposal, and the row is annotated `[DISPOSED <date>]` in its
  Description cell (no new column is created, since the baseline FAR may not
  have one).
- **New rows are appended after all existing content in each tab** (never
  inserted in the middle), so no existing row, formula, or total is ever
  shifted or altered. Only additions and identified disposals are computed
  each period - all other existing rows are left exactly as they were,
  matching the rule that nothing before the FAR's current closing position
  is ever recalculated or rewritten.

## Validation / exceptions

Each run reports (and writes to the "Exceptions – Review Required" tab):
- Invoices that couldn't be read reliably (blurry/missing amount or date).
- Invoices with no matching GL entry, and GL entries with no matching invoice.
- Matched pairs whose amounts disagree.
- Invoices/GL matches that are ambiguous (more than one plausible match).
- GL transactions that couldn't be classified into any existing FAR tab.
- Disposals identified in the GL that couldn't be matched to an existing
  asset row.

Duplicates (a transaction already reflected in the FAR, by amount + date) are
silently skipped rather than added again or flagged as an exception.

## Assumptions

- The baseline FAR's tab/column layout is exactly what future updates should
  match; the tool never adds a new tab, column, or asset class.
- "Matching" means amount and date reasonably close (a configurable
  tolerance window), boosted by vendor/reference where present; more than
  one plausible match is treated as an exception, not guessed.
- Each update run covers one batch; you are responsible for uploading an
  invoice batch and a GL that belong together and haven't been processed
  before. No period/date range is asked - the tool derives it from the
  register's own closing position and the dates already present in your
  files, per above.
- If a tab lacks dedicated Vendor/Invoice Number columns, that detail from
  the invoice is folded into the Description cell text rather than a new
  column being created.
- If a transaction's date falls on or before the register's last recorded
  closing position, it is flagged as an exception ("already recorded?")
  rather than silently added, since it may be a re-supplied prior-period
  item; genuinely new but backdated transactions can still be reviewed and
  resolved from that exception.

## Known limitations (gaps)

- **Scanned PDFs without a text layer are not OCR'd** - only text-based PDFs
  and plain image files (PNG/JPG/TIFF/BMP, via the built-in local OCR engine)
  are read. A scanned PDF will be listed as an exception rather than guessed
  at; save it as an image instead if you need it read automatically.
- **The GL must be a flat table with a header row** containing recognizable
  Date/Amount/Account-classification columns. A "sectioned ledger" export
  (account name as a row heading, followed by unlabeled transaction rows,
  with no repeated header) - like some accounting-system ledger exports - is
  not parsed; re-export the GL as a table with column headers first.
- Automated text-extraction from invoices is best-effort; a misread field
  causes that invoice to be skipped to Exceptions rather than being corrected
  automatically - there is no manual-correction step inside the tool itself.
- The tool does not reconcile the FAR against your trial balance or audited
  financial statements, and does not handle revaluations, impairments, or
  transfers between asset classes - only additions and identifiable
  disposals, per the approved specification.

## Files

- `app.py` - FastAPI web app and HTTP routes.
- `far_engine.py` - all the FAR/invoice/GL processing logic.
- `static/` - the single-page UI (plain HTML/CSS/JS, no build step).
- `data/` - created at runtime; holds the current FAR workbook and small run-
  history metadata. Empty until you upload a baseline.
- `SPEC.md` - the approved specification this tool implements.
