# SOFP Tracer

A small internal web tool for Orenda Welfare Trust. Upload the financial
statements, the trial balance and the general ledger, and it breaks every
**Statement of Financial Position** head down into the trial balance accounts
behind it, and each of those accounts into the individual ledger entries behind
it — with the differences at each step called out.

```
SOFP head              ->  trial balance accounts       ->  general ledger entries
Property and equipment ->  12000 Accum. Depreciation…    ->  MISC/2025/06/0003  170,847
                           (+ the cost accounts)             Initial balance    435,281
```

## Running it

```bash
pip install -r requirements.txt
python -m app.main
```

Then open <http://localhost:8000>. Set the `PORT` environment variable to use a
different port.

## What it does

**1. Reads three workbooks (`.xlsx` only)**

| Input | What is taken from it |
| --- | --- |
| Financial statements | The `SOFP` sheet: each balance sheet head, its note reference, its section, and every comparative period column |
| Trial balance | Every account with its opening, period movement and closing debit/credit |
| General ledger | Every posting: date, month, reference, partner, description, debit, credit |

On first start the app picks up any workbooks already shared with the project
(it looks in `../uploads`, `./uploads` and `./sample_data`, or `$SOFP_SEED_DIR`)
and works out which is which from the sheet names. Anything you upload through
the **Data files** tab replaces them. Uploads are stored under `data/uploads/`.

**2. Maps trial balance accounts to SOFP heads**

Accounts are matched to heads by keyword — "accumulated depreciation" to
*Property and equipment*, "deferred grant" to *Restricted grants*, and so on.
Every mapping shows how it was made and how confident it is:

* **Auto · high / medium / low** — matched by rule. Low-confidence matches are
  listed for confirmation.
* **Set by you** — your own assignment. These are saved to `data/mapping.json`
  and always beat the automatic rule, including after a re-upload.
* **Unmapped** — no rule matched. Listed on the *Review & mapping* tab, where
  you assign it to a head (or to the income & expenditure bucket if it does not
  belong on the balance sheet).

The keyword rules are a starting point, not a chart of accounts. **Review the
mapping before relying on the figures** — that is what the Review tab is for.

**3. Ties everything out**

At head level: financial statement figure vs. sum of mapped trial balance
accounts vs. sum per the general ledger. At account level: trial balance closing
vs. GL opening + debits − credits. Anything outside the tolerance (default
PKR 1, adjustable in the header) is flagged red.

The *Review & mapping* tab collects everything a reviewer needs: unmapped
accounts, low-confidence mappings, accounts where the TB and GL disagree,
accounts with no ledger detail, and ledger accounts missing from the TB.

**4. Exports the whole trace**

"Export workbook" produces an `.xlsx` with five sheets: *Tie-out summary*,
*SOFP heads*, *Head breakdown* (head → account), *GL entries* (every posting
behind every SOFP head) and *Review list*.

## Signs

Balances are held debit-positive (debit − credit) throughout, which is how the
trial balance and ledger express themselves. Heads are then displayed on their
natural side, so asset and liability heads both read positive and compare
directly with the printed statement. This is also why *Property and equipment*
works out correctly: the cost accounts are debits, accumulated depreciation is a
credit, and the sum is the net book value.

## Known limits

* Only `.xlsx` is read. Re-save `.xls` or `.csv` files first.
* The financials workbook is read for the SOFP heads and their period columns
  only. It is not recalculated, and cells that Excel saved as errors (`#REF!`)
  are treated as "no figure" rather than being guessed at.
* In the 2025 draft, the 2025 and 2024 columns are cleanly aligned, but the
  older comparative columns have review notes typed in between the figures. The
  period dropdown marks any column that is only partly readable, and heads with
  no readable figure in the chosen column show a blank rather than a made-up
  number.
* A trial balance account can belong to one head only.
* The tool reads what it is given and reports differences. It does not post
  adjustments or write to any of the source workbooks.

## Layout

```
app/
  main.py          FastAPI app, upload handling, JSON API
  parsers.py       workbook readers (SOFP, trial balance, general ledger)
  tracing.py       account -> head mapping and the tie-out calculations
  export.py        the Excel export
  static/          the single-page UI (plain HTML/CSS/JS, no build step)
data/              uploaded workbooks and saved mappings (created at runtime)
```

### API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | liveness |
| `GET /api/state` | which workbooks are loaded, parsing notes |
| `POST /api/upload` | upload `financials`, `trial_balance`, `general_ledger` |
| `GET /api/trace` | all SOFP heads with the tie-out |
| `GET /api/head?head_id=…` | trial balance accounts behind one head |
| `GET /api/account?key=…` | one account and its ledger entries |
| `GET /api/review` | everything needing review |
| `POST /api/mapping` | save mapping overrides (`{"assignments": {...}}`) |
| `GET /api/export.xlsx` | the export workbook |

All accept `year=` and `tolerance=` query parameters.
