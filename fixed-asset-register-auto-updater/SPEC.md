# Fixed Asset Register Auto-Updater

## What it does
Keeps a Fixed Asset Register (FAR) up to date, period after period, from the
invoices and GL you give it — without ever rebuilding the register or
manually re-typing rows into it.

## What you give it
- **Baseline FAR** (Excel workbook, .xlsx/.xlsm) — your existing register,
  uploaded once. The tool learns its tabs (one per asset class), columns, and
  the depreciation rate already in use in each tab, by matching header text
  it already contains. It never invents a tab, column, or rate.
- **Each period's invoices** (PDF or image — including scanned/photographed
  images, read with a local, free OCR engine that needs no separate system
  install) and the **GL covering those same transactions** (.xlsx, .xlsm,
  .xls or .csv, as a flat table with a recognizable header row containing
  Date, Amount and an Account/Classification column). The GL can be its own
  file, or the same FAR/GL workbook you already maintain if it keeps its GL
  as a sheet inside it — the tool finds the GL sheet on its own and only
  updates the FAR tabs it is already tracking.

## What it gives you back
- **The updated FAR workbook** (download), with new purchase rows added to
  the correct existing tab and depreciated the same way the baseline FAR
  already calculates it, and disposal transactions applied to the matching
  existing asset row instead of added as a new one.
- **An "Exceptions – Review Required" tab**, plus the same list on screen,
  listing anything the tool could not confidently resolve rather than guess
  at.

## Rules it follows
1. Tabs, columns, and depreciation rates are learned from the baseline FAR by
   matching header text (e.g. "Date of Purchase", "Cost", "Depr. Rate",
   "WDV") — never invented.
2. Which existing tab a new purchase belongs to is decided by matching the
   GL's account/classification text against the tab names, and the values
   already used in that tab's own "Category" column.
3. An invoice is matched to a GL line by amount + date (boosted by
   vendor/reference where available); no match, or more than one plausible
   match, is written to the review list rather than guessed.
4. The period a run covers is worked out automatically — from the day after
   the register's own last recorded closing position through to the latest
   transaction date actually found in this run's GL/invoices. You are never
   asked for a period or date range.
5. Depreciation for a new addition is `Cost × Rate × (full months held this
   period ÷ 12)`, using the rate already in use in that tab, with the
   purchase month always counted as a full month.
6. Disposals are recognized from GL narration/classification text containing
   words like "disposal", "written off", "scrap", "sold"; the matching
   existing row is found by description/reference similarity, its
   depreciation is calculated up to the month before disposal, and it is
   annotated in place — never deleted, moved, or duplicated as a new row.
7. A transaction already reflected in the FAR (same amount + date) is
   silently skipped on re-run, not re-added or flagged as an exception.
8. New rows are appended after all existing content in each tab; nothing
   already in the FAR before the run's period is ever recalculated,
   reordered, or rewritten.

## What I am assuming
- The baseline FAR's tab/column layout is exactly what future updates should
  match; the tool never adds a new tab, column, or asset class.
- "Matching" means amount and date reasonably close (a configurable
  tolerance window), boosted by vendor/reference where present; more than one
  plausible match is treated as an exception, not guessed.
- Each update run covers one batch of invoices + GL that belong together and
  haven't been processed before; no period/date range is asked, since the
  tool derives it from the register's own closing position and the dates
  already present in your files.
- If a tab lacks dedicated Vendor/Invoice Number columns, that detail from
  the invoice is folded into the Description cell text instead of a new
  column being created.
- A transaction dated on or before the register's last recorded closing
  position is flagged as an exception ("already recorded?") rather than
  silently added, since it may be a re-supplied prior-period item.

## Not included
- Does not invent a tab, column, or asset class that isn't already in the
  baseline FAR.
- Does not OCR scanned PDFs without a text layer — only text-based PDFs and
  plain image files (PNG/JPG/TIFF/BMP, via the built-in local OCR engine) are
  read automatically; a scanned PDF is listed as an exception instead.
- Does not parse a "sectioned ledger" GL export (account name as a row
  heading, followed by unlabeled transaction rows, with no repeated header
  row) — the GL must be a flat table with a recognizable header row.
- Does not reconcile the FAR against your trial balance or audited financial
  statements, and does not handle revaluations, impairments, or transfers
  between asset classes — only additions and identifiable disposals.

## How you will know it works
1. Upload a baseline FAR and confirm the tool reports the tabs and
   depreciation rates it recognized, without you having to state them.
2. Upload a batch of invoices with a matching GL and confirm each new
   purchase lands in the correct existing tab with depreciation calculated
   using that tab's own rate.
3. Re-upload the same invoices/GL batch a second time and confirm nothing is
   added twice.
4. Include a disposal transaction in the GL and confirm the matching existing
   asset row is updated in place rather than a new row being added.
5. Include an invoice that's blurry, the wrong document, or has no matching
   GL entry, and confirm it shows up in "Exceptions – Review Required"
   instead of being added with a guessed figure.
