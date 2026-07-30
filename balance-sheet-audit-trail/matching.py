"""
Fuzzy matching between SOFP lines, TB accounts and GL accounts, plus the
reconciliation/audit-trail computation.

Matching is heuristic (name similarity + generic accounting-terminology
synonyms). It is a *starting suggestion* the auto mapping produces so the
audit trail can be generated without manual review of the whole GL first;
the web UI lets the Finance/Audit user move accounts between lines, and
every line that could not be auto-mapped is explicitly flagged rather than
silently left out or guessed at.
"""
import re
from difflib import SequenceMatcher

STOPWORDS = {"and", "the", "of", "a", "an", "for", "to", "in", "on", "at", "-"}

# Generic accounting-domain keyword groups used only to nudge similarity
# scoring between a SOFP line label and a TB/GL account name. These are
# standard, generic financial-statement terms - not data taken from any
# uploaded workbook - and are meant to help with common line-item wording
# that plain text similarity would miss (e.g. "Property and equipment" vs
# "Computer Equipment" / "Accumulated Depreciation").
CONCEPT_KEYWORDS = {
    "cash_bank": ["cash", "bank", "savings", "current account", "petty cash"],
    "receivables": ["receivable", "debtor", "trade debt", "due from"],
    "advances_prepayments": ["advance", "prepaid", "prepayment", "deposit", "security deposit"],
    "fixed_assets": ["equipment", "furniture", "fixture", "vehicle", "building", "land",
                     "computer", "office equipment", "electrical", "depreciation",
                     "property", "plant", "machinery", "asset"],
    "intangibles": ["intangible", "amortization", "amortisation", "software", "application", "license", "licence"],
    "investments": ["investment", "security", "term deposit", "shares"],
    "inventory": ["stock", "inventory"],
    "payables": ["payable", "accrued", "accrual", "creditor", "due to"],
    "provisions": ["provision"],
    "deferred_grants_income": ["deferred grant", "grant", "subsidy", "deferred income"],
    "tax": ["tax", "wht", "withholding", "duty"],
    "payroll": ["salary", "salaries", "payroll", "eobi", "gratuity", "provident", "employee", "wages"],
    "equity": ["reserve", "surplus", "fund balance", "capital", "retained earning", "equity"],
    "borrowings": ["loan", "borrowing", "lease liability", "finance lease"],
}


def _normalize(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in STOPWORDS]
    return " ".join(tokens)


def _concepts(text):
    t = (text or "").lower()
    hits = set()
    for concept, kws in CONCEPT_KEYWORDS.items():
        for kw in kws:
            if kw in t:
                hits.add(concept)
                break
    return hits


def similarity(label_a, label_b):
    a, b = _normalize(label_a), _normalize(label_b)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    containment = 0.0
    if ta and tb:
        containment = len(ta & tb) / max(1, min(len(ta), len(tb)))
    concept_bonus = 0.3 if (_concepts(label_a) & _concepts(label_b)) else 0.0
    return max(ratio, containment) * 0.7 + concept_bonus


def build_auto_owner_map(sofp_leaf_labels, tb_accounts, threshold=0.32):
    """Returns tb_name -> best-matching sofp label (or None if below threshold)."""
    owner = {}
    for tb in tb_accounts:
        best_label, best_score = None, 0.0
        for label in sofp_leaf_labels:
            s = similarity(label, tb["name"])
            if s > best_score:
                best_score, best_label = s, label
        owner[tb["name"]] = best_label if best_score >= threshold else None
    return owner


def match_tb_to_gl(tb_accounts, gl_accounts, threshold=0.5):
    """Returns tb_name -> gl_account_key (or None)."""
    gl_keys = list(gl_accounts.keys())
    gl_norm = {k: _normalize(k) for k in gl_keys}
    result = {}
    for tb in tb_accounts:
        target_norm = _normalize(tb["name"])
        # exact normalized match first
        exact = [k for k in gl_keys if gl_norm[k] == target_norm]
        if exact:
            result[tb["name"]] = exact[0]
            continue
        # code match
        if tb.get("code"):
            code_match = [k for k, acc in gl_accounts.items() if acc.get("code") == tb["code"]]
            if code_match:
                result[tb["name"]] = code_match[0]
                continue
        best_key, best_score = None, 0.0
        for k in gl_keys:
            s = SequenceMatcher(None, target_norm, gl_norm[k]).ratio()
            if s > best_score:
                best_score, best_key = s, k
        result[tb["name"]] = best_key if best_score >= threshold else None
    return result


def compute_report(sofp_items, tb_accounts, gl_accounts, tolerance, manual_overrides):
    """Builds the full audit / reconciliation report.

    manual_overrides: dict of tb_account_name -> target_label ("" / None means
    force-unmapped). Overrides win over the automatic mapping.
    """
    leaf_items = [it for it in sofp_items if it.get("value") is not None or it.get("error")]
    numeric_items = [it for it in leaf_items if it.get("value") is not None]
    numeric_labels = [it["label"] for it in numeric_items]

    auto_owner = build_auto_owner_map(numeric_labels, tb_accounts)
    gl_owner = match_tb_to_gl(tb_accounts, gl_accounts)

    def effective_owner(tb_name):
        if tb_name in manual_overrides:
            ov = manual_overrides[tb_name]
            return ov if ov else None
        return auto_owner.get(tb_name)

    lines = []
    used_tb = set()

    for it in leaf_items:
        label = it["label"]
        owned_tb = [t for t in tb_accounts if effective_owner(t["name"]) == label]
        tb_detail = []
        tb_total = 0.0
        gl_total = 0.0
        gl_total_valid = True

        for t in owned_tb:
            used_tb.add(t["name"])
            gl_key = gl_owner.get(t["name"])
            gl_acc = gl_accounts.get(gl_key) if gl_key else None
            gl_closing = gl_acc["computed_closing"] if gl_acc else None
            if gl_closing is None:
                gl_total_valid = False
            else:
                gl_total += gl_closing
            tb_total += t["closing"]
            tb_detail.append({
                "tb_name": t["name"],
                "tb_code": t["code"],
                "tb_closing": round(t["closing"], 2),
                "gl_account": gl_key,
                "gl_closing": round(gl_closing, 2) if gl_closing is not None else None,
                "gl_transaction_count": gl_acc["transaction_count"] if gl_acc else 0,
                "gl_stated_closing": round(gl_acc["stated_closing"], 2) if gl_acc and gl_acc.get("stated_closing") is not None else None,
                "flag": None if gl_key else "NO_GL_MATCH",
            })

        flags = []
        if it.get("error"):
            flags.append(
                f"SOFP_SOURCE_ERROR: the reported value is '{it['error']}' in the "
                "workbook (a broken formula reference) and cannot be reconciled."
            )
        if not owned_tb:
            flags.append("NO_TB_MAPPING: no Trial Balance account is mapped to this line yet.")

        diff_sofp_tb = None
        if it.get("value") is not None and owned_tb:
            diff_sofp_tb = round(tb_total - it["value"], 2)
            if abs(diff_sofp_tb) > tolerance:
                if abs(tb_total + it["value"]) <= tolerance:
                    flags.append(
                        f"SIGN_MISMATCH: the TB-derived total ({tb_total:,.2f}) is the "
                        f"mirror image of the reported SOFP value ({it['value']:,.2f}); "
                        "check the sign convention for this account type."
                    )
                else:
                    flags.append(
                        f"DIFF_SOFP_VS_TB: TB-derived total ({tb_total:,.2f}) differs from "
                        f"the reported SOFP value ({it['value']:,.2f}) by {diff_sofp_tb:,.2f}."
                    )

        diff_tb_gl = None
        if owned_tb:
            if gl_total_valid:
                diff_tb_gl = round(tb_total - gl_total, 2)
                if abs(diff_tb_gl) > tolerance:
                    flags.append(
                        f"DIFF_TB_VS_GL: GL-derived total ({gl_total:,.2f}) differs from the "
                        f"TB closing balance ({tb_total:,.2f}) by {diff_tb_gl:,.2f}."
                    )
            else:
                flags.append(
                    "INCOMPLETE_GL_MATCH: one or more mapped TB accounts could not be "
                    "located in the GL sheet, so the GL-derived total is incomplete."
                )

        lines.append({
            "label": label,
            "sofp_value": it.get("value"),
            "sofp_error": it.get("error"),
            "tb_accounts": tb_detail,
            "tb_total": round(tb_total, 2) if owned_tb else None,
            "gl_total": round(gl_total, 2) if owned_tb and gl_total_valid else None,
            "diff_sofp_tb": diff_sofp_tb,
            "diff_tb_gl": diff_tb_gl,
            "flags": flags,
            "status": "OK" if (owned_tb and not flags) else ("UNMAPPED" if not owned_tb else "REVIEW"),
        })

    unmapped_tb = [
        {"name": t["name"], "code": t["code"], "closing": round(t["closing"], 2)}
        for t in tb_accounts if t["name"] not in used_tb
    ]

    covered_labels = {l["label"] for l in lines}
    section_rows = [
        {"label": it["label"], "row": it["row"]}
        for it in sofp_items
        if it.get("value") is None and not it.get("error") and it["label"] not in covered_labels
    ]

    summary = {
        "total_sofp_lines": len(lines),
        "lines_mapped": sum(1 for l in lines if l["tb_accounts"]),
        "lines_unmapped": sum(1 for l in lines if not l["tb_accounts"]),
        "lines_flagged": sum(1 for l in lines if l["flags"]),
        "unmapped_tb_accounts": len(unmapped_tb),
        "total_tb_accounts": len(tb_accounts),
        "total_gl_accounts": len(gl_accounts),
    }

    return {
        "summary": summary,
        "lines": lines,
        "unmapped_tb_accounts": unmapped_tb,
        "section_rows": section_rows,
        "all_sofp_labels": numeric_labels,
    }
