"""Excel export of the SOFP -> trial balance -> general ledger trace."""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .tracing import PL_BUCKET, PL_LABEL, UNMAPPED

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(color="FFFFFF", bold=True)
MONEY = "#,##0.00;[Red](#,##0.00)"


def _write_sheet(
    workbook: Workbook,
    title: str,
    columns: List[str],
    rows: List[List[Any]],
    money_from: int = 0,
) -> None:
    sheet = workbook.create_sheet(title[:31])
    sheet.append(columns)
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in rows:
        sheet.append(row)

    for index, name in enumerate(columns, start=1):
        letter = get_column_letter(index)
        longest = max([len(str(name))] + [len(str(r[index - 1])) for r in rows[:400]] or [10])
        sheet.column_dimensions[letter].width = min(max(12, longest + 2), 60)
        if money_from and index >= money_from:
            for cell in sheet[letter][1:]:
                cell.number_format = MONEY
    sheet.freeze_panes = "A2"


def build_workbook(
    trace: Dict[str, Any],
    gl: Optional[Dict[str, Any]],
    sources: Dict[str, Any],
) -> bytes:
    """Render the whole trace to an .xlsx file and return its bytes."""
    workbook = Workbook()
    workbook.remove(workbook.active)

    summary = trace["summary"]
    head_label = {head["id"]: head["label"] for head in trace["heads"]}
    head_label[PL_BUCKET] = PL_LABEL
    head_label[UNMAPPED] = "Unmapped - needs review"

    # ---- 1. tie-out summary ------------------------------------------------
    info_rows = [
        ["Reporting period column used", summary["year_label"] or "-"],
        ["Comparative column", summary["prior_year_label"] or "-"],
        ["Rounding tolerance (PKR)", summary["tolerance"]],
        ["", ""],
        ["Financial statements file", sources.get("financials") or "not loaded"],
        ["Trial balance file", sources.get("trial_balance") or "not loaded"],
        ["General ledger file", sources.get("general_ledger") or "not loaded"],
        ["", ""],
        ["SOFP heads", summary["head_count"]],
        ["Heads agreeing to trial balance", summary["heads_tied"]],
        ["Heads with a difference", summary["heads_with_difference"]],
        ["Heads with no accounts mapped", summary["heads_without_accounts"]],
        ["", ""],
        ["Trial balance accounts", summary["tb_account_count"]],
        ["Mapped to a SOFP head", summary["mapped_account_count"]],
        ["Income & expenditure (excluded)", summary["pl_account_count"]],
        ["Unmapped - needs review", summary["unmapped_account_count"]],
        ["Low-confidence auto mappings", summary["accounts_needing_review"]],
        ["", ""],
        ["Mapped accounts with no GL detail", summary["accounts_not_in_gl"]],
        ["Mapped accounts where GL <> TB", summary["accounts_gl_out_of_balance"]],
        ["GL accounts absent from the TB", summary["gl_only_account_count"]],
        ["GL transactions read", summary["gl_transaction_count"]],
        ["", ""],
        ["FS total assets", summary["fs_total_assets"]],
        ["TB total assets (mapped)", summary["tb_total_assets"]],
        ["FS total funds & liabilities", summary["fs_total_funds_and_liabilities"]],
        ["TB total funds & liabilities (mapped)", summary["tb_total_funds_and_liabilities"]],
    ]
    control = summary.get("tb_control") or {}
    if control:
        info_rows += [
            ["", ""],
            ["TB closing debits", control.get("closing_debit")],
            ["TB closing credits", control.get("closing_credit")],
            ["TB debits less credits", control.get("closing_difference")],
        ]
    _write_sheet(workbook, "Tie-out summary", ["Measure", "Value"], info_rows)

    # ---- 2. SOFP heads -----------------------------------------------------
    head_rows = [
        [
            head["section"],
            head["label"],
            head["note"],
            head["side"],
            head["fs_amount"],
            head["tb_amount"],
            head["fs_vs_tb"],
            head["gl_amount"],
            head["tb_vs_gl"],
            head["account_count"],
            head["status"],
        ]
        for head in trace["heads"]
    ]
    _write_sheet(
        workbook,
        "SOFP heads",
        [
            "Section",
            "SOFP head",
            "Note",
            "Side",
            f"Per FS ({summary['year_label'] or '-'})",
            "Per trial balance",
            "FS less TB",
            "Per general ledger",
            "TB less GL",
            "Accounts",
            "Status",
        ],
        head_rows,
        money_from=5,
    )

    # ---- 3. head -> account breakdown -------------------------------------
    breakdown: List[List[Any]] = []
    for head in trace["heads"]:
        for account in trace["accounts"].values():
            if account["head_id"] != head["id"]:
                continue
            breakdown.append(
                [
                    head["section"],
                    head["label"],
                    account["code"] or "",
                    account["name"],
                    account["map_source"],
                    account["map_confidence"],
                    account["tb_opening"],
                    account["tb_movement_debit"],
                    account["tb_movement_credit"],
                    account["tb_closing"],
                    account["gl_opening"],
                    account["gl_debit"],
                    account["gl_credit"],
                    account["gl_closing"],
                    account["closing_difference"],
                    account["gl_transaction_count"],
                ]
            )
    _write_sheet(
        workbook,
        "Head breakdown",
        [
            "Section",
            "SOFP head",
            "GL code",
            "Account",
            "Mapped by",
            "Confidence",
            "TB opening",
            "TB period debit",
            "TB period credit",
            "TB closing",
            "GL opening",
            "GL debit",
            "GL credit",
            "GL closing",
            "TB less GL",
            "GL entries",
        ],
        breakdown,
        money_from=7,
    )

    # ---- 4. GL transactions behind the SOFP heads --------------------------
    transaction_rows: List[List[Any]] = []
    if gl:
        for head in trace["heads"]:
            for account in trace["accounts"].values():
                if account["head_id"] != head["id"]:
                    continue
                ledger = gl["accounts"].get(account["key"])
                if not ledger:
                    continue
                for entry in ledger["transactions"]:
                    transaction_rows.append(
                        [
                            head["label"],
                            account["code"] or "",
                            account["name"],
                            entry["date"] or "",
                            entry["month"] or "",
                            entry["reference"] or "",
                            entry["partner"] or "",
                            entry["description"] or "",
                            entry["debit"],
                            entry["credit"],
                            entry["amount"],
                            entry["row"],
                        ]
                    )
    _write_sheet(
        workbook,
        "GL entries",
        [
            "SOFP head",
            "GL code",
            "Account",
            "Date",
            "Month",
            "Reference",
            "Partner",
            "Description",
            "Debit",
            "Credit",
            "Net (Dr-Cr)",
            "GL row",
        ],
        transaction_rows,
        money_from=9,
    )

    # ---- 5. review list ----------------------------------------------------
    review: List[List[Any]] = []
    for account in trace["unmapped"]:
        review.append(
            [
                "Unmapped account",
                account["code"] or "",
                account["label"],
                account["tb_closing"],
                "Assign this account to a SOFP head (or mark it income & expenditure).",
            ]
        )
    for account in trace["accounts"].values():
        if account["head_id"] in (UNMAPPED, PL_BUCKET):
            continue
        if account["map_source"] == "auto" and account["map_confidence"] == "low":
            review.append(
                [
                    "Low-confidence auto mapping",
                    account["code"] or "",
                    account["label"],
                    account["tb_closing"],
                    f"Auto-mapped to '{head_label.get(account['head_id'], '?')}' - confirm.",
                ]
            )
        if account["ties_to_gl"] is False:
            review.append(
                [
                    "TB does not agree to GL",
                    account["code"] or "",
                    account["label"],
                    account["closing_difference"],
                    f"TB closing {account['tb_closing']:,.2f} vs GL closing {account['gl_closing']:,.2f}.",
                ]
            )
        if not account["in_gl"]:
            review.append(
                [
                    "No GL detail",
                    account["code"] or "",
                    account["label"],
                    account["tb_closing"],
                    "Account is in the trial balance but not in the uploaded general ledger.",
                ]
            )
    for account in trace["gl_only"]:
        review.append(
            [
                "In GL but not in TB",
                "",
                account["label"],
                account["gl_closing"],
                f"{account['transaction_count']} ledger entries with no trial balance line.",
            ]
        )
    for head in trace["heads"]:
        if head["status"] == "difference":
            review.append(
                [
                    "Head does not agree",
                    "",
                    head["label"],
                    head["fs_vs_tb"],
                    "Financial statement figure differs from the mapped trial balance accounts.",
                ]
            )
        elif head["status"] == "no-accounts":
            review.append(
                [
                    "Head has no accounts",
                    "",
                    head["label"],
                    head["fs_amount"],
                    "No trial balance account is mapped to this SOFP head.",
                ]
            )
    _write_sheet(
        workbook,
        "Review list",
        ["Issue", "GL code", "Item", "Amount", "What to check"],
        review,
        money_from=4,
    )

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()
