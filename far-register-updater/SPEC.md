# Fixed Asset Register Updater

## What it does
This tool takes your existing Fixed Asset Register (FAR), the current period's Fixed Asset GL entries, and any invoices you have for new purchases, and produces an updated FAR: new assets added without duplicates, and depreciation/accumulated depreciation/NBV rolled forward for every asset — old and new — for the period.

## What you give it
- **The existing FAR workbook** (the exact file you attached) — the "FAR Schedule" summary sheet plus the category sheets (Land and buildings, Furniture and fixtures, Office equipment, Electrical Equipment, Mobile Phones, Computer Equipment, Vehicles). Each category sheet needs Description, Date of Purchase, Cost, Depr. Rate, Opening, Dep for the year, Acc Dep, and WDV/NBV present, as they are today.
- **Fixed Asset GL entries for the period** — one row per addition, with: a unique voucher/transaction reference, the posting date, the GL account (so it can be mapped to the right category sheet), and the amount. If a row has no reference, it cannot be checked for duplicates on future runs and will be flagged for review instead of silently added.
- **Invoices for the new additions** — used only to get the asset description and the date; not used for amounts. If a GL addition has no invoice, the tool still adds it (using the GL's own narration as the description) but flags it for your review.

## What it gives you back
A new Excel workbook (a dated copy — your original FAR file is never changed), containing:
- Every category sheet from your FAR, with new addition rows appended and every existing row's depreciation, accumulated depreciation and NBV rolled forward for the period.
- An updated "FAR Schedule" summary with cost, additions, depreciation, accumulated depreciation and NBV totals per category.
- A "Needs Review" list: additions with no matching invoice, and any category where the FAR's addition total doesn't tie to the GL's addition total for the period.

## Rules it follows
1. A GL addition row is matched to the FAR by its voucher/transaction reference. If that reference is already recorded in the FAR, the row is treated as already captured and is not added again.
2. A GL addition row whose reference is not yet in the FAR is added as a new row on the matching category sheet.
3. For a new addition, the description and date come from its invoice, when one is provided; the amount and category always come from the GL, never from the invoice.
4. An invoice is paired to its GL addition by matching amount and date within the same category (or by invoice number if it appears in the GL narration). If a GL addition has no invoice that matches, it is added using the GL narration as its description and flagged on the "Needs Review" list.
5. A new addition's depreciation rate is the rate currently used for that asset category in the existing FAR.
6. A new addition's depreciation start date is its invoice date (or GL date, if no invoice); the first period's depreciation is prorated by the number of months from that date to the FAR's year-end, the same way the current FAR does it.
7. Every asset already in the FAR — old and new — has this period's depreciation added, accumulated depreciation increased, and NBV recalculated as cost minus accumulated depreciation.
8. Category and grand totals on the FAR Schedule summary are recalculated from the updated category sheets.
9. After updating, the tool compares each category's total additions to the GL's total additions for that category for the period. If they don't match exactly, the variance is listed for your review — the tool does not force them to tie.

## What I am assuming
- The GL export has, at minimum: date, a unique voucher/transaction reference, the GL account/category, and the amount — if a column is missing or a reference is blank, that row goes to "Needs Review" instead of being added automatically.
- Invoices are supplied as files (or a list) from which a date and description can be read directly — no handwriting or image recognition is performed. If invoices are scans without extractable text, treat them as "no invoice" for that addition.
- To detect duplicates across future runs, the tool will store the GL reference number against each row it adds — this means a column is added to your category sheets that isn't there today, purely for tracking; nothing else in the layout changes.
- "Depreciation start date" means date of purchase (from the invoice, or GL date if no invoice), consistent with how "Date of Purchase" already drives first-year depreciation in your existing sheets.
- Disposals, transfers, and impairments are out of scope for this tool — it only processes additions and routine period depreciation, since no disposal data was described.
- Each run covers one period (e.g., one month or quarter) of GL additions at a time, matching however often you plan to run this.

## Not included
- Fixing pre-existing broken formulas in your workbook (e.g., the #REF! errors currently in the Land and buildings totals) — these will be flagged, not repaired.
- Handling disposals, write-offs, transfers, or asset revaluations.
- Reconciling or correcting historic depreciation-rate inconsistencies noted in your workbook's comments (e.g., FAR rate vs. audited-financials rate) — the tool applies whatever rate is currently set for each category as-is.
- Posting any entries back into your accounting system — output is an Excel file only.
- Multi-currency or tax-basis (vs. accounting-basis) depreciation.

## How you will know it works
1. Take one GL addition that has a matching invoice — confirm it appears once, in the right category sheet, with the invoice's description and date and the GL's amount.
2. Re-run the tool on the same GL file — confirm that addition does not get added a second time.
3. Take one GL addition with no invoice — confirm it's added using the GL narration and appears on the "Needs Review" list.
4. Pick an asset that was already in the FAR before this run — confirm its depreciation, accumulated depreciation, and NBV have moved forward correctly for the new period.
5. Compare the new file's category addition totals against your GL's addition totals for the same period — confirm any mismatch shows up on the review list, and confirm your original FAR file on disk is untouched.