# Fixed Asset Register (FAR) Auto-Updater

## What it does
This tool takes your existing Fixed Asset Register, uploaded once, and updates it every period by reading new purchase invoices and the matching GL entries — adding new assets to the correct existing tab, continuing depreciation exactly the way your FAR already calculates it, and leaving all historical data untouched. Each run produces the same FAR file, just carried forward, plus a list of anything it couldn't confidently process.

## What you give it
- **The baseline FAR (uploaded once)** — your existing FAR workbook, in the same tab/column layout as what you currently maintain (e.g. separate tabs per asset class such as Computer Equipment, Furniture & Fixtures, Mobile Phones, Vehicles, etc., each with columns like Description, Date of Purchase, Cost, Depreciation Rate, Accumulated Depreciation, WDV). The tool reads this once to learn your structure, rates, and formulas. If a tab or column it needs to update is missing or laid out in a way it can't recognize, that tab is left untouched and reported as an exception rather than guessed at.
- **Invoices, each period** — PDF or image invoices for new asset purchases (one file per invoice or a batch of files). The tool reads description, date, vendor, invoice/reference number, and amount off each one using document scanning. An invoice it cannot read clearly (blurry, missing amount, missing date) is skipped and flagged, not guessed.
- **GL, each period** — the general ledger extract covering the same transactions, showing at minimum the transaction date, amount, and the account/classification (e.g. "Computer Equipment") it was posted to. This is what tells the tool which existing FAR tab a purchase belongs in. A GL file without a recognizable classification column is rejected for that run and flagged.

If either invoices or GL are missing for a period, the tool does not process that period — it reports "nothing to update" rather than adding assets from only one source.

## What it gives you back
A single updated FAR workbook (same file, carried forward) with:
- All existing tabs, historical assets, and prior-period figures exactly as they were.
- New assets added as new rows in the correct existing tab, with the same columns already used (description, purchase date, cost, rate, accumulated depreciation, WDV, etc.).
- Depreciation and carrying-amount figures recalculated for the period using the same method and rates already present in that tab.
- A new tab, **"Exceptions – Review Required,"** listing every item the tool could not process, with the reason (e.g. "invoice unreadable," "no matching GL entry," "amount mismatch," "could not classify").

The tool also keeps this updated FAR internally, so next period you only need to supply that period's invoices and GL — you don't need to re-upload the FAR unless you want to start over from a fresh baseline.

## Rules it follows
1. The most recently uploaded or produced FAR is always the starting point; nothing before its closing balances is ever recalculated or rewritten.
2. A new asset is only added when an invoice and a GL entry can be matched to each other — matching is done by amount and date being reasonably close, and, where available, vendor name or reference number.
3. The GL entry's account/classification decides which existing FAR tab the asset goes into (e.g. a GL line under "Computer Equipment" → the existing Computer Equipment tab). The invoice supplies the descriptive detail (description, vendor, invoice number, exact date, amount) for that row.
4. A new asset is depreciated using the same rate, useful-life logic, and calculation formulas already present in the tab it's added to — these are read from the existing FAR and never asked of you or re-entered.
5. The month an asset is purchased is charged a full month of depreciation, regardless of the day of purchase within that month.
6. When a disposal can be identified from the GL/supporting information, the existing asset row is updated (not treated as a new addition); depreciation stops after the month before the disposal month, and no depreciation is charged in the disposal month itself, following the disposal treatment already used in that FAR tab.
7. A transaction already recorded in the FAR (matched by amount, date, vendor, and/or reference number) is not added again, even if it reappears in a later period's invoices or GL.
8. An invoice with no matching GL entry, a GL entry with no matching invoice, a matched pair whose amounts disagree, or a transaction that cannot be assigned to any existing tab is not added to the FAR — it is listed on the Exceptions tab with the reason instead.

## What I am assuming
- The baseline FAR's tab layout (one tab per asset class) and column layout, as shown in your current file, is what every future update must match — the tool will not introduce new tabs, columns, or asset classes on its own.
- Invoices are supplied as PDFs/images and read via automated document scanning; if a scan misreads a value, that invoice goes to Exceptions rather than being silently entered wrong — there's no manual correction step in the tool itself before that.
- "Matching" an invoice to a GL line means reasonably close amount and date (and vendor/reference where present) — an exact, single unambiguous match is required; anything with more than one plausible match, or no match, is an exception.
- You will review and manually resolve items on the Exceptions tab yourself (e.g. by re-supplying a clearer invoice next period) — the tool does not re-attempt them automatically.
- Each update run covers one period's invoices and GL together; you are responsible for making sure the invoice batch and GL extract you upload together belong to the same period.
- The tool updates only the FAR itself — it does not touch or reconcile against your general ledger, trial balance, or financial statements beyond using the GL as a classification/validation input.

## Not included
- The tool does not generate journal entries or post anything back to your accounting system — it only produces the updated FAR file.
- It does not reconcile FAR totals against your audited financial statements or trial balance; any pre-existing mismatches in the baseline FAR (e.g. rate differences noted in your current file) are carried forward as-is, not corrected.
- It does not handle asset revaluations, impairments, or transfers between asset classes — only additions and (where identifiable) disposals.
- It does not manually correct misread invoice data on your behalf — misreads are flagged as exceptions for you to resolve.
- It cannot create a new asset-class tab or column that doesn't already exist in your baseline FAR — a purchase that doesn't fit any existing tab is flagged as an exception, not force-fitted or auto-created.

## How you will know it works
- Upload your current FAR as baseline, then run one period with a small batch of invoices and GL you already know the answer for — check that the new rows appear in the correct tabs with the correct amounts and dates, and that prior-year figures haven't changed.
- Deliberately include one invoice with no matching GL line and one GL line with no matching invoice — confirm both land on the Exceptions tab with a sensible reason instead of appearing in the FAR.
- Re-run the same period's invoices and GL a second time — confirm no duplicate rows are added.
- Check a newly added asset's depreciation for its purchase month — confirm it shows a full month's charge regardless of the exact purchase date.
- If you include a disposal in the GL, confirm the existing asset row is updated (not duplicated) and depreciation stops the month before disposal, with nothing charged in the disposal month itself.