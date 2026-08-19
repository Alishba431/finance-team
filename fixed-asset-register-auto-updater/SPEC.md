# Fixed Asset Register Auto-Updater

## What it does
This tool takes your existing Fixed Asset Register (FAR) and updates it for a new period using that period's invoices and General Ledger (GL). It reads the invoices — including scanned or photographed images — to pull out asset details, uses the GL to classify and validate each transaction, and adds the new assets into the correct existing tab of your FAR with depreciation continued in the same way it's always been calculated.

## What you give it
- **Baseline FAR** (your existing Register, Excel file) — uploaded once. This is read exactly as-is: its tabs, asset classes, columns, depreciation rates/method, formulas and layout are the reference for everything the tool does afterward. You are not asked to redefine any of this.
- **Invoices** for the period — PDF or image files (including photos/scans). The tool reads asset description, date, vendor, invoice/reference number, and amount from each one. If an invoice image is too unclear to read automatically, it is set aside as an exception for you to review rather than guessed at or dropped — the rest of the batch keeps processing normally.
- **GL** for the same period (spreadsheet) — used to confirm which asset-class tab a transaction belongs to and to check the accounting entry. It needs, at minimum, a date, an amount, a description/reference, and the account or classification the transaction was posted to. If a GL row is missing the classification or doesn't match any invoice, it is flagged rather than guessed.

## What it gives you back
An updated version of the same FAR file — same workbook, same tabs, same formulas and formatting — with the new period's assets added into their correct existing sections and depreciation carried forward. Alongside it, a short exceptions list (on screen and/or as a sheet in the workbook) showing anything that needed your attention: unreadable invoices, unmatched invoices/GL lines, amount mismatches, or transactions that couldn't be classified.

## Rules it follows
1. The uploaded baseline FAR is never rebuilt or restructured — the tool only adds to it from its current closing position onward.
2. Every invoice is matched to a GL transaction using date, amount and reference; the GL entry's account determines which existing FAR tab/asset class the new asset is added to.
3. Asset details (description, date, vendor, invoice number, amount) come from the invoice; classification and validation come from the GL — neither source is used alone.
4. Depreciation for a newly added asset starts the same month it is purchased, regardless of the day of the month.
5. If a disposal is identified from the GL, depreciation stops the month before the disposal month, and the tool updates the existing asset record rather than adding a new one.
6. Depreciation rate, useful life, method and presentation for each asset class are taken from what's already in the uploaded FAR — never asked for separately.
7. A transaction already present in the FAR (matched by invoice/reference number) is not added again.
8. If an invoice image cannot be read automatically, it is flagged as an exception for manual review and the rest of the batch continues processing.
9. Anything that cannot be matched, classified, or reconciled (duplicate transaction, invoice with no GL match, GL line with no invoice match, amount mismatch, unclear classification) is listed as an exception, not guessed into the FAR.

## What I am assuming
- Invoice reading (including images) runs using a free, locally installed reader (Tesseract) rather than a paid cloud OCR service — per your instruction.
- The GL file has at least a date, amount, description/reference, and account/classification column; if a GL extract lacks the classification column, its rows will show up as exceptions rather than being auto-classified by guesswork.
- Matching invoices to GL lines relies on date, amount and reference number/vendor being reasonably consistent between the two; if your GL uses a very different reference scheme than the invoices, more items may land in the exceptions list than expected.
- The updated FAR file becomes the new baseline for the next period automatically — you don't re-upload the baseline each time.
- "Reasonable validation" means the checks listed in Rule 9 above; it does not include a full audit-style review of every accounting entry.

## Not included
- No cloud-based OCR service — only local reading of invoice text/images.
- No manual restructuring of the FAR, and no new asset classes, tabs, or depreciation policies created by the tool — it only follows what's already in your uploaded FAR.
- No automatic resolution of exceptions — flagged items always need your review and manual entry.
- No handling of GL files that don't include a classification/account column — those rows will always be flagged, not guessed.

## How you will know it works
- Upload your current FAR as baseline, then a period's invoices (including at least one photographed/scanned invoice) and GL — the image invoice should be read without the "Tesseract not installed" error.
- Check that new assets appear in the correct existing tab, with the same columns and formulas as the rest of that tab.
- Check that a known disposal in your test GL stops depreciation the month before disposal, and a known new purchase starts depreciation in its purchase month.
- Re-run the same invoices/GL a second time and confirm nothing is added twice.
- Include one deliberately blurry or unreadable invoice image and confirm it shows up in the exceptions list instead of blocking the rest of the upload.