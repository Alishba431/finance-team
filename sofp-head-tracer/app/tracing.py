"""Mapping and tie-out logic: SOFP head -> trial balance accounts -> GL entries.

Sign convention used throughout
-------------------------------
Every balance is held *debit-positive* (debit - credit), which is how the trial
balance and the ledger express themselves. A SOFP head is then presented on its
natural side:

* asset heads              -> presented amount =  debit-positive balance
* fund and liability heads -> presented amount = -debit-positive balance

so both compare directly with the positive figures printed in the statement.

Nothing here invents a chart of accounts. The auto-mapping is a keyword
suggestion layer only: every account carries the rule that classified it and its
confidence, and any assignment can be overridden by the user (overrides are
saved and always win).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .parsers import nkey

# Accounts that belong to the statement of income and expenditure rather than
# the SOFP. Kept as an explicit bucket so they are visibly excluded rather than
# silently dropped.
PL_BUCKET = "__income_and_expenditure__"
PL_LABEL = "Income & expenditure accounts (not a SOFP head)"
UNMAPPED = "__unmapped__"

# Ordered rules. First match wins.
#   account: regex tested against "<code> <name>" lower-cased
#   head:    regex tested against the SOFP head label lower-cased
#            (or PL_BUCKET for income/expenditure accounts)
AUTO_RULES: List[Dict[str, Any]] = [
    # --- unambiguous balance-sheet accounts -----------------------------
    {"account": r"payable to orenda|orenda.*\bpayable", "head": r"payable to orenda", "confidence": "high"},
    {"account": r"receivable from orenda|orenda.*receivable", "head": r"receivable from orenda", "confidence": "high"},
    {"account": r"deferred capital grant", "head": r"deferred capital grant", "confidence": "high"},
    {"account": r"deferred grant|restricted grant|grant deferred", "head": r"restricted grant", "confidence": "high"},
    {"account": r"\bprepaid\b|prepayment", "head": r"prepaid", "confidence": "high"},
    {"account": r"advance tax|tax deduction|withholding tax receivable|wht on|income tax refundable", "head": r"advance tax", "confidence": "high"},
    {"account": r"accu\w*\s*amorti[sz]ation|intangible|learning application|\bsoftware asset\b", "head": r"intangible", "confidence": "high"},
    {"account": r"accu\w*\s*depreciation", "head": r"property and equipment|property, plant", "confidence": "high"},
    {"account": r"long term investment|investment in|\binvestments?\b", "head": r"investment", "confidence": "high"},
    {"account": r"\bcash\b|petty cash|\bbank\b(?!\s*charge)|current account|saving account|deposit account", "head": r"cash and bank", "confidence": "high"},
    {"account": r"\bstock\b|\bstores?\b|inventor", "head": r"store|inventor", "confidence": "medium"},

    # --- income & expenditure -------------------------------------------
    {"account": r"\bexpenses?\b|\bsalar(y|ies)\b|\bincome\b|revenue|\bcharges\b|subscription|donation|profit on|\brent\b|utilit|\bfee\b|\bfees\b|marketing|repair|maintain|maintenance|printing|stationary|stationery|water|internet|mobile|workshop|medical|travel|lunch|entertainment|office suppl|boarding|lodging|fuel|training|bonus|allowance|advertis|audit fee|amorti[sz]ation expense|depreciation expense|\bevent\b|top-up|development fund", "head": PL_BUCKET, "confidence": "medium"},

    # --- broader balance-sheet catch-alls (run after P&L) ----------------
    {"account": r"furniture|fixture|computer equipment|office equipment|electrical|vehicle|motor|leasehold|building|\bland\b|plant|machinery|capital work|\bequipments?\b|\bit assets?\b", "head": r"property and equipment|property, plant", "confidence": "low"},
    {"account": r"security deposit|\bdeposits?\b|advance to|advances|receivable|prepayments|other receivable", "head": r"advances|receivable", "confidence": "low"},
    {"account": r"accrued|\bpayable\b|provision for tax|provision", "head": r"accrued and other payable|accrued", "confidence": "low"},
    {"account": r"\bfund\b|accumulated surplus|general reserve|reserve", "head": r"^fund|accumulated fund", "confidence": "low"},
]


def _match_head(head_pattern: str, heads: List[Dict[str, Any]]) -> Optional[str]:
    """Find the SOFP head whose label matches a rule's head pattern."""
    for head in heads:
        if re.search(head_pattern, nkey(head["label"])):
            return head["id"]
    return None


def suggest_mapping(account_label: str, heads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Suggest a SOFP head for a trial balance account. Never guesses silently."""
    text = nkey(account_label)
    for rule in AUTO_RULES:
        if not re.search(rule["account"], text):
            continue
        if rule["head"] == PL_BUCKET:
            return {
                "head_id": PL_BUCKET,
                "source": "auto",
                "confidence": rule["confidence"],
                "rule": rule["account"],
            }
        head_id = _match_head(rule["head"], heads)
        if head_id:
            return {
                "head_id": head_id,
                "source": "auto",
                "confidence": rule["confidence"],
                "rule": rule["account"],
            }
    return {"head_id": UNMAPPED, "source": "none", "confidence": "none", "rule": None}


def build_assignments(
    tb_accounts: List[Dict[str, Any]],
    heads: List[Dict[str, Any]],
    overrides: Dict[str, str],
) -> Dict[str, Dict[str, Any]]:
    """Resolve every trial balance account to a head: user override, else rule."""
    valid_head_ids = {head["id"] for head in heads} | {PL_BUCKET, UNMAPPED}
    assignments: Dict[str, Dict[str, Any]] = {}

    for account in tb_accounts:
        key = account["key"]
        override = overrides.get(key)
        if override and override in valid_head_ids:
            assignments[key] = {
                "head_id": override,
                "source": "user",
                "confidence": "user",
                "rule": None,
            }
        elif override:
            # The saved override points at a head that no longer exists in the
            # uploaded statement (e.g. the SOFP was re-issued with new wording).
            assignments[key] = {
                "head_id": UNMAPPED,
                "source": "stale-override",
                "confidence": "none",
                "rule": None,
                "stale_head_id": override,
            }
        else:
            assignments[key] = suggest_mapping(account["label"], heads)

    return assignments


def _fs_value(head: Dict[str, Any], year_label: Optional[str]) -> Optional[float]:
    values = head.get("values") or {}
    if year_label and year_label in values:
        return values[year_label]
    return None


def build_trace(
    sofp: Optional[Dict[str, Any]],
    tb: Optional[Dict[str, Any]],
    gl: Optional[Dict[str, Any]],
    overrides: Dict[str, str],
    tolerance: float = 1.0,
    year_label: Optional[str] = None,
    prior_year_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the full SOFP -> TB -> GL trace."""
    heads = list(sofp["items"]) if sofp else []
    tb_accounts = list(tb["accounts"]) if tb else []
    gl_accounts = dict(gl["accounts"]) if gl else {}

    years = sofp["years"] if sofp else []
    if year_label is None and years:
        year_label = years[0]["label"]
    if prior_year_label is None and len(years) > 1:
        prior_year_label = years[1]["label"]

    assignments = build_assignments(tb_accounts, heads, overrides)

    # ---- account level: trial balance vs general ledger -------------------
    account_rows: Dict[str, Dict[str, Any]] = {}
    for account in tb_accounts:
        key = account["key"]
        assignment = assignments[key]
        ledger = gl_accounts.get(key)

        row: Dict[str, Any] = {
            "key": key,
            "code": account.get("code"),
            "name": account.get("name"),
            "label": account["label"],
            "tb_row": account["row"],
            "tb_opening": account["opening"],
            "tb_movement": account["movement"],
            "tb_movement_debit": account["movement_debit"],
            "tb_movement_credit": account["movement_credit"],
            "tb_closing": account["closing"],
            "tb_closing_debit": account["closing_debit"],
            "tb_closing_credit": account["closing_credit"],
            "head_id": assignment["head_id"],
            "map_source": assignment["source"],
            "map_confidence": assignment["confidence"],
            "in_gl": ledger is not None,
        }

        if ledger:
            row.update(
                {
                    "gl_opening": ledger["opening"],
                    "gl_debit": ledger["debit"],
                    "gl_credit": ledger["credit"],
                    "gl_movement": ledger["debit"] - ledger["credit"],
                    "gl_closing": ledger["closing"],
                    "gl_transaction_count": ledger["transaction_count"],
                    "opening_difference": round(account["opening"] - ledger["opening"], 2),
                    "movement_difference": round(
                        account["movement"] - (ledger["debit"] - ledger["credit"]), 2
                    ),
                    "closing_difference": round(account["closing"] - ledger["closing"], 2),
                }
            )
            row["ties_to_gl"] = abs(row["closing_difference"]) <= tolerance
        else:
            row.update(
                {
                    "gl_opening": None,
                    "gl_debit": None,
                    "gl_credit": None,
                    "gl_movement": None,
                    "gl_closing": None,
                    "gl_transaction_count": 0,
                    "opening_difference": None,
                    "movement_difference": None,
                    "closing_difference": None,
                    "ties_to_gl": None,
                }
            )
        account_rows[key] = row

    # ---- head level -------------------------------------------------------
    by_head: Dict[str, List[Dict[str, Any]]] = {}
    for row in account_rows.values():
        by_head.setdefault(row["head_id"], []).append(row)

    head_rows: List[Dict[str, Any]] = []
    for head in heads:
        rows = sorted(
            by_head.get(head["id"], []), key=lambda r: (r["code"] or "", r["label"])
        )
        sign = 1.0 if head["side"] == "asset" else -1.0

        tb_amount = round(sign * sum(r["tb_closing"] for r in rows), 2) if rows else None
        tb_opening = round(sign * sum(r["tb_opening"] for r in rows), 2) if rows else None
        gl_rows = [r for r in rows if r["in_gl"]]
        gl_amount = (
            round(sign * sum(r["gl_closing"] for r in gl_rows), 2) if gl_rows else None
        )

        fs_amount = _fs_value(head, year_label)
        fs_prior = _fs_value(head, prior_year_label)

        fs_vs_tb = (
            round((fs_amount or 0.0) - tb_amount, 2)
            if fs_amount is not None and tb_amount is not None
            else None
        )
        tb_vs_gl = (
            round(tb_amount - gl_amount, 2)
            if tb_amount is not None and gl_amount is not None
            else None
        )

        if not rows:
            status = "no-accounts"
        elif fs_amount is None:
            status = "no-fs-figure"
        elif fs_vs_tb is not None and abs(fs_vs_tb) <= tolerance:
            status = "tie"
        else:
            status = "difference"

        head_rows.append(
            {
                "id": head["id"],
                "label": head["label"],
                "section": head["section"],
                "side": head["side"],
                "note": head["note"],
                "sofp_row": head["row"],
                "fs_amount": fs_amount,
                "fs_prior_amount": fs_prior,
                "fs_movement": (
                    round(fs_amount - fs_prior, 2)
                    if fs_amount is not None and fs_prior is not None
                    else None
                ),
                "tb_amount": tb_amount,
                "tb_opening": tb_opening,
                "tb_movement": (
                    round(tb_amount - tb_opening, 2)
                    if tb_amount is not None and tb_opening is not None
                    else None
                ),
                "gl_amount": gl_amount,
                "fs_vs_tb": fs_vs_tb,
                "tb_vs_gl": tb_vs_gl,
                "account_count": len(rows),
                "accounts_without_gl": sum(1 for r in rows if not r["in_gl"]),
                "status": status,
                "values": head["values"],
            }
        )

    # ---- buckets ----------------------------------------------------------
    def bucket(head_id: str) -> List[Dict[str, Any]]:
        return sorted(
            by_head.get(head_id, []), key=lambda r: (r["code"] or "", r["label"])
        )

    unmapped = bucket(UNMAPPED)
    pl_accounts = bucket(PL_BUCKET)

    # GL accounts that never appear in the trial balance.
    tb_keys = {a["key"] for a in tb_accounts}
    gl_only = [
        {
            "key": key,
            "label": account["label"],
            "gl_closing": account["closing"],
            "transaction_count": account["transaction_count"],
        }
        for key, account in gl_accounts.items()
        if key not in tb_keys
    ]
    gl_only.sort(key=lambda a: a["label"])

    # ---- summary ----------------------------------------------------------
    mapped_rows = [r for r in account_rows.values() if r["head_id"] not in (UNMAPPED, PL_BUCKET)]
    fs_total_assets = sum(
        h["fs_amount"] or 0.0 for h in head_rows if h["side"] == "asset" and h["fs_amount"] is not None
    )
    fs_total_liabilities = sum(
        h["fs_amount"] or 0.0
        for h in head_rows
        if h["side"] == "liability" and h["fs_amount"] is not None
    )
    tb_total_assets = sum(
        h["tb_amount"] or 0.0 for h in head_rows if h["side"] == "asset" and h["tb_amount"] is not None
    )
    tb_total_liabilities = sum(
        h["tb_amount"] or 0.0
        for h in head_rows
        if h["side"] == "liability" and h["tb_amount"] is not None
    )

    summary = {
        "year_label": year_label,
        "prior_year_label": prior_year_label,
        "tolerance": tolerance,
        "head_count": len(head_rows),
        "heads_tied": sum(1 for h in head_rows if h["status"] == "tie"),
        "heads_with_difference": sum(1 for h in head_rows if h["status"] == "difference"),
        "heads_without_accounts": sum(1 for h in head_rows if h["status"] == "no-accounts"),
        "tb_account_count": len(tb_accounts),
        "mapped_account_count": len(mapped_rows),
        "pl_account_count": len(pl_accounts),
        "unmapped_account_count": len(unmapped),
        "accounts_needing_review": sum(
            1
            for r in mapped_rows
            if r["map_source"] == "auto" and r["map_confidence"] == "low"
        ),
        "accounts_not_in_gl": sum(1 for r in mapped_rows if not r["in_gl"]),
        "accounts_gl_out_of_balance": sum(
            1 for r in mapped_rows if r["ties_to_gl"] is False
        ),
        "gl_only_account_count": len(gl_only),
        "fs_total_assets": round(fs_total_assets, 2),
        "fs_total_funds_and_liabilities": round(fs_total_liabilities, 2),
        "tb_total_assets": round(tb_total_assets, 2),
        "tb_total_funds_and_liabilities": round(tb_total_liabilities, 2),
        "tb_control": tb["control"] if tb else None,
        "gl_transaction_count": gl["transaction_count"] if gl else 0,
    }

    return {
        "summary": summary,
        "years": years,
        "heads": head_rows,
        "accounts": account_rows,
        "unmapped": unmapped,
        "pl_accounts": pl_accounts,
        "gl_only": gl_only,
        "head_options": [
            {"id": h["id"], "label": h["label"], "section": h["section"]} for h in heads
        ]
        + [
            {"id": PL_BUCKET, "label": PL_LABEL, "section": "EXCLUDED"},
            {"id": UNMAPPED, "label": "Unmapped - needs review", "section": "EXCLUDED"},
        ],
    }
