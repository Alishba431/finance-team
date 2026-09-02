"""
Optional data source: pull SOFP/TB/GL data directly from Odoo instead of an
Excel upload.

Connects read-only, via Odoo's standard XML-RPC external API (stdlib
`xmlrpc.client`, no extra dependency), to the staging instance already
configured for this tool through environment variables:

    ODOO_URL          e.g. https://staging.example.odoo.com
    ODOO_DB           database name
    ODOO_USERNAME     login used to authenticate
    ODOO_API_KEY      Odoo API key (used as the XML-RPC password)
    ODOO_COMPANY_ID   optional; restricts the pull to one company id if the
                       instance has more than one

No credentials are ever accepted from the browser or stored anywhere other
than these environment variables - the web UI only ever asks for a period.

Only posted (`parent_state = 'posted'`) `account.move.line` records are
read from `account.move.line` / `account.account`. Nothing is written back
to Odoo. The result is reshaped into the same row/column structures
`parser.py` produces from an Excel workbook, so `matching.py` and the rest
of the app run identically regardless of source.
"""
import os
import xmlrpc.client
from datetime import datetime


class OdooError(Exception):
    """Base class for problems talking to Odoo."""


class OdooConfigError(OdooError):
    """The server this tool runs on isn't configured to reach Odoo."""


class OdooConnectionError(OdooError):
    """Odoo was reachable/configured but the request failed."""


class OdooEmptyPeriodError(OdooError):
    """Odoo has no posted entries for the requested period."""


# Odoo's own account_type values (Odoo 17/18) that belong on a Statement of
# Financial Position, mapped to a human-readable line label. Income/expense
# and off-balance accounts are left out of the SOFP grouping (they still
# appear as ordinary TB/GL accounts, and are flagged "unmapped" by the
# existing matching logic exactly as an Excel TB with extra accounts would
# be) because a balance sheet does not report them.
BALANCE_SHEET_LABELS = {
    "asset_cash": "Cash and Cash Equivalents",
    "asset_current": "Other Current Assets",
    "asset_non_current": "Other Non-Current Assets",
    "asset_prepayments": "Prepayments and Advances",
    "asset_fixed": "Property, Plant and Equipment",
    "asset_receivable": "Trade and Other Receivables",
    "liability_current": "Other Current Liabilities",
    "liability_non_current": "Non-Current Liabilities",
    "liability_payable": "Trade and Other Payables",
    "liability_credit_card": "Credit Card Liabilities",
    "equity": "Equity",
    "equity_unaffected": "Retained Earnings / Accumulated Surplus",
}


def _get_config():
    url = os.environ.get("ODOO_URL", "").strip().rstrip("/")
    db = os.environ.get("ODOO_DB", "").strip()
    username = os.environ.get("ODOO_USERNAME", "").strip()
    api_key = os.environ.get("ODOO_API_KEY", "").strip()
    missing = [n for n, v in (
        ("ODOO_URL", url), ("ODOO_DB", db),
        ("ODOO_USERNAME", username), ("ODOO_API_KEY", api_key),
    ) if not v]
    if missing:
        raise OdooConfigError(
            "Odoo is not configured on this server (missing environment "
            f"variable{'s' if len(missing) > 1 else ''}: {', '.join(missing)}). "
            "Ask whoever runs this tool to set the Odoo connection details, "
            "or use the Excel upload option instead."
        )
    company_id = os.environ.get("ODOO_COMPANY_ID", "").strip()
    return url, db, username, api_key, (int(company_id) if company_id else None)


def _connect():
    url, db, username, api_key, company_id = _get_config()
    try:
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, username, api_key, {})
    except Exception as e:
        raise OdooConnectionError(f"Could not reach the Odoo instance at '{url}': {e}")
    if not uid:
        raise OdooConnectionError(
            "Odoo rejected the configured username/API key for this database. "
            "Check the ODOO_DB, ODOO_USERNAME and ODOO_API_KEY settings on the server."
        )
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models, company_id


def _execute(models, db, uid, api_key, model, method, *args, **kwargs):
    try:
        return models.execute_kw(db, uid, api_key, model, method, list(args), kwargs)
    except OdooError:
        raise
    except Exception as e:
        raise OdooConnectionError(f"Odoo request failed ({model}.{method}): {e}")


def _bucket_from_type_name(type_name):
    t = (type_name or "").lower()
    if "receivable" in t:
        return "asset_receivable"
    if "bank" in t or "cash" in t:
        return "asset_cash"
    if "fixed asset" in t:
        return "asset_fixed"
    if "prepay" in t:
        return "asset_prepayments"
    if "non-current asset" in t or "non current asset" in t:
        return "asset_non_current"
    if "current asset" in t:
        return "asset_current"
    if "payable" in t:
        return "liability_payable"
    if "non-current liabilit" in t or "non current liabilit" in t:
        return "liability_non_current"
    if "current liabilit" in t:
        return "liability_current"
    if "equity" in t:
        return "equity"
    return None


def _fetch_accounts(models, db, uid, api_key, account_ids):
    if not account_ids:
        return {}
    try:
        rows = _execute(
            models, db, uid, api_key, "account.account", "search_read",
            [("id", "in", account_ids)], fields=["code", "name", "account_type"],
        )
        for r in rows:
            r["_bucket_type"] = r.get("account_type")
    except OdooConnectionError:
        # Older Odoo versions (pre account_type field) expose the
        # classification via the user_type_id relation instead.
        rows = _execute(
            models, db, uid, api_key, "account.account", "search_read",
            [("id", "in", account_ids)], fields=["code", "name", "user_type_id"],
        )
        for r in rows:
            type_name = (r.get("user_type_id") or [None, ""])[1]
            r["_bucket_type"] = _bucket_from_type_name(type_name)
    return {r["id"]: r for r in rows}


def _account_label(code, name):
    return f"{code} {name}".strip() if code else name


def pull_period(start_date, end_date):
    """Pulls posted journal entries for [start_date, end_date] (inclusive,
    "YYYY-MM-DD" strings) and reshapes them into sofp_items/tb_accounts/
    gl_accounts matching parser.py's output shapes.

    Raises OdooConfigError / OdooConnectionError / OdooEmptyPeriodError /
    OdooError (bad input) on failure.
    """
    if not start_date or not end_date:
        raise OdooError("Both a start date and an end date are required.")
    try:
        d_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        d_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        raise OdooError("Dates must be in YYYY-MM-DD format.")
    if d_start > d_end:
        raise OdooError("The start date must be on or before the end date.")

    db, uid, api_key, models, company_id = _connect()

    base_domain = [("parent_state", "=", "posted")]
    if company_id:
        base_domain.append(("company_id", "=", company_id))

    period_domain = base_domain + [("date", ">=", start_date), ("date", "<=", end_date)]
    opening_domain = base_domain + [("date", "<", start_date)]

    period_lines = _execute(
        models, db, uid, api_key, "account.move.line", "search_read",
        period_domain,
        fields=["date", "name", "ref", "debit", "credit", "account_id", "move_id"],
        order="account_id, date, id",
    )
    if not period_lines:
        raise OdooEmptyPeriodError(
            f"No posted journal entries were found in Odoo between {start_date} and "
            f"{end_date}. Check the period, or confirm the entries for it have been "
            "posted (not left as drafts) in Odoo, and try again."
        )

    opening_groups = _execute(
        models, db, uid, api_key, "account.move.line", "read_group",
        opening_domain, fields=["debit", "credit"], groupby=["account_id"],
    )
    opening_by_account = {}
    for g in opening_groups:
        if not g.get("account_id"):
            continue
        opening_by_account[g["account_id"][0]] = (g.get("debit") or 0.0) - (g.get("credit") or 0.0)

    lines_by_account = {}
    for l in period_lines:
        if not l.get("account_id"):
            continue
        lines_by_account.setdefault(l["account_id"][0], []).append(l)

    account_ids = sorted(set(opening_by_account) | set(lines_by_account))
    accounts = _fetch_accounts(models, db, uid, api_key, account_ids)

    tb_accounts = []
    gl_accounts = {}
    sofp_totals = {}
    row_counter = 1

    for acc_id in account_ids:
        info = accounts.get(acc_id, {})
        code = info.get("code") or None
        name = info.get("name") or f"Account {acc_id}"
        label = _account_label(code, name)

        opening = opening_by_account.get(acc_id, 0.0)
        acc_lines = lines_by_account.get(acc_id, [])
        period_debit = sum(l.get("debit") or 0.0 for l in acc_lines)
        period_credit = sum(l.get("credit") or 0.0 for l in acc_lines)
        closing = opening + period_debit - period_credit

        tb_accounts.append({
            "row": row_counter, "code": code, "name": label, "raw_name": label,
            "opening": round(opening, 2),
            "debit_end": round(period_debit, 2), "credit_end": round(period_credit, 2),
            "closing": round(closing, 2),
        })
        row_counter += 1

        transactions = []
        if abs(opening) > 1e-9:
            transactions.append({
                "row": 0, "date": start_date,
                "description": "Opening balance (posted entries before this period)",
                "debit": round(opening, 2) if opening > 0 else 0.0,
                "credit": round(-opening, 2) if opening < 0 else 0.0,
                "stated_balance": None,
            })
        for l in sorted(acc_lines, key=lambda x: (x.get("date") or "", x.get("id") or 0)):
            move = l.get("move_id")
            desc = l.get("name") or l.get("ref") or (move[1] if move else "") or ""
            transactions.append({
                "row": l.get("id"), "date": l.get("date"), "description": desc,
                "debit": round(l.get("debit") or 0.0, 2),
                "credit": round(l.get("credit") or 0.0, 2),
                "stated_balance": None,
            })

        computed_closing = sum(t["debit"] - t["credit"] for t in transactions)
        gl_accounts[label] = {
            "name": label, "code": code, "raw_name": label,
            "transactions": transactions,
            "computed_closing": round(computed_closing, 2),
            "transaction_count": len(transactions),
            "stated_closing": None,
        }

        bucket_label = BALANCE_SHEET_LABELS.get(info.get("_bucket_type"))
        if bucket_label:
            sofp_totals[bucket_label] = sofp_totals.get(bucket_label, 0.0) + closing

    sofp_items = [
        {"row": i + 1, "label": label, "value": round(total, 2), "error": None}
        for i, (label, total) in enumerate(sofp_totals.items())
    ]

    sofp_meta = {
        "year_header_row": None, "current_year": d_end.year, "current_year_col": None,
        "note_col": None, "years_seen": sorted({d_start.year, d_end.year}, reverse=True),
    }
    tb_meta = {"header_row": "odoo-pull", "closing_pair_cols": None, "single_closing_col": None}
    gl_meta = {"header_row": "odoo-pull", "account_order": list(gl_accounts.keys())}

    period_label = f"{d_start.strftime('%d %b %Y')} – {d_end.strftime('%d %b %Y')}"

    return {
        "sofp_items": sofp_items, "sofp_meta": sofp_meta,
        "tb_accounts": tb_accounts, "tb_meta": tb_meta,
        "gl_accounts": gl_accounts, "gl_meta": gl_meta,
        "period_label": period_label,
    }
