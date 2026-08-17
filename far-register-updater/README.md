# Fixed Asset Register Updater

Updates your existing Fixed Asset Register (FAR) from this period's Fixed
Asset GL additions and any invoices you have for them: new assets are added
without duplicates, and depreciation / accumulated depreciation / NBV are
rolled forward for every asset - old and new - for the period.

The full approved specification is in `SPEC.md` - read that first if you want
the exact rules this tool follows. This README is about running it.

## Running it

```
pip install -r requirements.txt
python main.py
```

The app starts a small web server (default port 8000, or the `PORT`
environment variable if set) and serves an upload form at `/`. There is
nothing to configure and no database - every run is stateless; nothing you
upload is stored on disk.

Open the page, upload your files, fill in the period details, and click
"Update FAR". The updated workbook downloads straight to your browser as
`FAR_Updated_<period end date>.xlsx`. Your original FAR file on disk is
never touched.

## What to have ready

1. **Your existing FAR workbook (.xlsx)** - the exact file you already
   maintain, with the "FAR Schedule" summary sheet and the category ledger
   sheets (Land and buildings, Furniture and fixtures, Office equipment,
   Electrical Equipment, Mobile Phones, Computer Equipment, Vehicles, or
   however yours are named/organised). Each category sheet needs
   Description, Date of Purchase, Cost, Depr. Rate, Opening, Dep for the
   year, Acc Dep and WDV columns present, exactly as they are today - the
   tool finds these by column header text, not by position, so small
   differences between sheets are fine.

2. **This period's Fixed Asset GL entries (.xlsx or .csv)** - one row per
   addition, with at minimum: a date, a unique voucher/transaction
   reference, the GL account (used to route the row to the right category
   sheet), and the amount. A narration/description column is used as the
   fallback asset description when no invoice matches.

3. **Invoices for the new additions** (optional but recommended) - either:
   - an **invoice register** spreadsheet (.xlsx/.csv) with columns for
     invoice number, invoice date, vendor, description, amount, and
     optionally category/reference - this is the reliable path, or
   - **raw invoice files** (.pdf or .txt) uploaded directly - the tool pulls
     whatever text is embedded in the file and looks for an invoice number,
     date, vendor line and description using pattern matching. This is
     best-effort only: there is no OCR or handwriting recognition, so a
     scanned image with no extractable text is treated as "no invoice" for
     that addition (per the approved spec).

4. **Period end date** and **period length in months** for this run (e.g.
   monthly = 1, quarterly = 3), and optionally a **fiscal year-end override**
   (MM-DD) if the tool cannot detect it automatically from the FAR
   Schedule sheet's title (e.g. "AS ON JUNE 30, 2025").

## What you get back

An Excel workbook containing:
- Every category sheet from your FAR, with new addition rows appended (each
  tagged with its GL reference in a new "GL Reference" tracking column) and
  every existing row's depreciation, accumulated depreciation and NBV rolled
  forward for the period.
- An appended "UPDATED FAR SUMMARY" block on the FAR Schedule sheet, with
  opening cost, additions, closing cost, depreciation, accumulated
  depreciation and NBV recalculated per category from the updated sheets.
- A **Needs Review** sheet: additions with no matching invoice, GL rows that
  couldn't be added automatically (missing reference/date/amount, or an
  unmapped GL account), and any category where the FAR's addition total for
  the period doesn't tie to the GL's addition total.
- An **Additions Log (This Run)** sheet: a full audit trail of every new row
  added this run (GL reference, GL date/amount, invoice number/vendor/date
  if matched, description used, rate and first-year depreciation applied) -
  a convenience beyond the minimum spec, for traceability.

## Key assumptions this tool makes (see SPEC.md for the full list)

- A GL row is only added once its reference, date, amount and GL
  account/category all parse successfully and the account maps to an
  existing category sheet; otherwise it goes to Needs Review instead of
  being silently added or guessed.
- The depreciation rate applied to a new addition is the rate on the most
  recently purchased existing asset in that category sheet (rule 5 in
  SPEC.md) - if a category sheet has no existing rows with a usable rate,
  the addition is still added (rate 0) and flagged for you to set manually.
  It never invents a rate that isn't already present in your FAR.
- A new addition's first depreciation charge is prorated from its purchase
  date to the FAR's fiscal year-end, the same convention already used in
  your sheets (e.g. a purchase 3 months before year-end depreciates for
  3/12 of the annual rate in its first period).
  Existing assets instead get this run's period fraction (period length /
  12) of their annual depreciation added on top of what's already recorded.
- If this run's period crosses into a new fiscal year (detected from the
  FAR Schedule sheet's "AS ON ..." title versus the period end date you
  give it), each existing asset's "Dep for prior year" is folded in and
  "Dep for the year" restarts for the new year; otherwise it keeps
  accumulating within the same year. If the prior period-end date can't be
  detected, this fold-over is skipped and flagged as an assumption on the
  Needs Review sheet - please check this manually the first time you run
  the tool, or whenever your workbook's title cell is missing/unreadable.
  If your workbook was produced without ever being opened/recalculated in
  Excel (so formula cells have no cached values), those cells cannot be
  read as numbers and the affected rows will be skipped - a working file
  that has been opened and saved in Excel at least once avoids this.
- Cells you did not ask this tool to change are left exactly as they were,
  formulas included (so the pre-existing `#REF!` errors mentioned in your
  workbook are preserved, not fixed, per SPEC.md).
- New rows are inserted directly below the last recognised asset row on
  each sheet (reusing blank rows first, then inserting new ones); any
  totals/notes rows further down are pushed down accordingly rather than
  recalculated, since those aren't part of the accumulated-depreciation
  columns this tool owns.

## Not included (see SPEC.md)

Disposals, write-offs, transfers, asset revaluations, fixing pre-existing
broken formulas, correcting historic depreciation-rate inconsistencies,
posting entries back into an accounting system, and multi-currency / tax-
basis depreciation are all out of scope for this tool.
