import csv
import io
import os
import uuid

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import openpyxl

import parser as wbparser
import matching

app = FastAPI(title="Balance Sheet Audit Support")

# In-memory session store. Nothing uploaded is written to disk; a session
# only lives in this process's memory and is lost on restart.
SESSIONS = {}

SOFP_KEYWORDS = [["sofp"], ["statement", "financial", "position"], ["balance", "sheet"]]
TB_KEYWORDS = [["tb", "new"], ["trial", "balance"], ["tb"]]
GL_KEYWORDS = [["gl", "new"], ["general", "ledger"], ["gl"]]


def _err(msg, status=400):
    return JSONResponse({"error": msg}, status_code=status)


@app.get("/")
def index():
    return FileResponse(os.path.join("static", "index.html"))


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    try:
        data = await file.read()
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=False)
    except Exception as e:
        return _err(f"Could not open this file as an Excel workbook: {e}")

    sheet_names = wbparser.list_sheets(wb)
    if not sheet_names:
        return _err("The workbook has no sheets.")

    sofp_guess, _ = wbparser.guess_sheet(sheet_names, "SOFP", SOFP_KEYWORDS)
    tb_guess, _ = wbparser.guess_sheet(sheet_names, "TB New 2025", TB_KEYWORDS)
    gl_guess, _ = wbparser.guess_sheet(sheet_names, "GL New 2025", GL_KEYWORDS)

    session_id = uuid.uuid4().hex
    SESSIONS[session_id] = {
        "filename": file.filename,
        "workbook_bytes": data,
        "manual_overrides": {},
        "tolerance": 1.0,
    }

    return {
        "session_id": session_id,
        "filename": file.filename,
        "sheet_names": sheet_names,
        "guessed": {"sofp": sofp_guess, "tb": tb_guess, "gl": gl_guess},
    }


def _get_session(session_id):
    return SESSIONS.get(session_id)


@app.post("/api/analyze")
async def analyze(
    session_id: str = Form(...),
    sofp_sheet: str = Form(...),
    tb_sheet: str = Form(...),
    gl_sheet: str = Form(...),
    tolerance: float = Form(1.0),
):
    session = _get_session(session_id)
    if not session:
        return _err("Unknown or expired session. Please re-upload the workbook.", 404)

    try:
        wb = openpyxl.load_workbook(io.BytesIO(session["workbook_bytes"]), data_only=True)
    except Exception as e:
        return _err(f"Could not re-read the uploaded workbook: {e}")

    for role, name in (("SOFP", sofp_sheet), ("TB", tb_sheet), ("GL", gl_sheet)):
        if name not in wb.sheetnames:
            return _err(f"Sheet '{name}' selected for {role} was not found in the workbook.")

    sofp_items, sofp_meta = wbparser.parse_sofp(wb[sofp_sheet])
    if sofp_meta.get("error"):
        return _err(f"SOFP sheet ('{sofp_sheet}'): {sofp_meta['error']}")

    tb_accounts, tb_meta = wbparser.parse_tb(wb[tb_sheet])
    if tb_meta.get("error"):
        return _err(f"TB sheet ('{tb_sheet}'): {tb_meta['error']}")

    gl_accounts, gl_meta = wbparser.parse_gl(wb[gl_sheet])
    if gl_meta.get("error"):
        return _err(f"GL sheet ('{gl_sheet}'): {gl_meta['error']}")

    session.update({
        "sofp_sheet": sofp_sheet, "tb_sheet": tb_sheet, "gl_sheet": gl_sheet,
        "sofp_items": sofp_items, "sofp_meta": sofp_meta,
        "tb_accounts": tb_accounts, "tb_meta": tb_meta,
        "gl_accounts": gl_accounts, "gl_meta": gl_meta,
        "tolerance": tolerance,
        "manual_overrides": {},
    })

    report = matching.compute_report(sofp_items, tb_accounts, gl_accounts, tolerance, {})
    return {
        "sofp_meta": sofp_meta, "tb_meta": tb_meta, "gl_meta": gl_meta,
        "report": report,
    }


@app.post("/api/remap")
async def remap(session_id: str = Form(...), tb_name: str = Form(...), target_label: str = Form("")):
    session = _get_session(session_id)
    if not session or "sofp_items" not in session:
        return _err("Unknown session, or the workbook has not been analyzed yet.", 404)

    valid_labels = {it["label"] for it in session["sofp_items"] if it.get("value") is not None}
    if target_label and target_label not in valid_labels:
        return _err(f"'{target_label}' is not a recognized SOFP line for this workbook.")

    session["manual_overrides"][tb_name] = target_label or None

    report = matching.compute_report(
        session["sofp_items"], session["tb_accounts"], session["gl_accounts"],
        session["tolerance"], session["manual_overrides"],
    )
    return {"report": report}


@app.post("/api/tolerance")
async def set_tolerance(session_id: str = Form(...), tolerance: float = Form(1.0)):
    session = _get_session(session_id)
    if not session or "sofp_items" not in session:
        return _err("Unknown session, or the workbook has not been analyzed yet.", 404)
    session["tolerance"] = tolerance
    report = matching.compute_report(
        session["sofp_items"], session["tb_accounts"], session["gl_accounts"],
        tolerance, session["manual_overrides"],
    )
    return {"report": report}


@app.get("/api/gl-transactions/{session_id}")
async def gl_transactions(session_id: str, account: str):
    session = _get_session(session_id)
    if not session or "gl_accounts" not in session:
        return _err("Unknown session, or the workbook has not been analyzed yet.", 404)
    acc = session["gl_accounts"].get(account)
    if not acc:
        return _err(f"GL account '{account}' not found.", 404)
    return {
        "account": account,
        "code": acc.get("code"),
        "computed_closing": round(acc["computed_closing"], 2),
        "stated_closing": round(acc["stated_closing"], 2) if acc.get("stated_closing") is not None else None,
        "transactions": acc["transactions"],
    }


def _current_report(session):
    return matching.compute_report(
        session["sofp_items"], session["tb_accounts"], session["gl_accounts"],
        session["tolerance"], session["manual_overrides"],
    )


@app.get("/api/export/{session_id}/summary.csv")
async def export_summary(session_id: str):
    session = _get_session(session_id)
    if not session or "sofp_items" not in session:
        return _err("Unknown session, or the workbook has not been analyzed yet.", 404)
    report = _current_report(session)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "SOFP Line", "SOFP Reported Value", "TB Account", "TB Code", "TB Closing Balance",
        "GL Account", "GL Closing Balance", "GL Transaction Count",
        "Line TB Total", "Line GL Total", "Diff (TB - SOFP)", "Diff (TB - GL)", "Flags",
    ])
    for line in report["lines"]:
        flags_txt = " | ".join(line["flags"])
        if not line["tb_accounts"]:
            w.writerow([
                line["label"], line["sofp_value"] if line["sofp_value"] is not None else line["sofp_error"],
                "", "", "", "", "", "", "", "", "", "", flags_txt,
            ])
            continue
        for tb in line["tb_accounts"]:
            w.writerow([
                line["label"], line["sofp_value"],
                tb["tb_name"], tb["tb_code"] or "", tb["tb_closing"],
                tb["gl_account"] or "(no GL match)", tb["gl_closing"] if tb["gl_closing"] is not None else "",
                tb["gl_transaction_count"],
                line["tb_total"], line["gl_total"] if line["gl_total"] is not None else "",
                line["diff_sofp_tb"] if line["diff_sofp_tb"] is not None else "",
                line["diff_tb_gl"] if line["diff_tb_gl"] is not None else "",
                flags_txt,
            ])
    w.writerow([])
    w.writerow(["Unmapped TB Accounts (no SOFP line assigned)"])
    w.writerow(["TB Account", "TB Code", "TB Closing Balance"])
    for tb in report["unmapped_tb_accounts"]:
        w.writerow([tb["name"], tb["code"] or "", tb["closing"]])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_summary.csv"},
    )


@app.get("/api/export/{session_id}/gl-detail.csv")
async def export_gl_detail(session_id: str):
    session = _get_session(session_id)
    if not session or "sofp_items" not in session:
        return _err("Unknown session, or the workbook has not been analyzed yet.", 404)
    report = _current_report(session)
    gl_accounts = session["gl_accounts"]

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "SOFP Line", "TB Account", "GL Account", "Transaction Date", "Description",
        "Debit", "Credit", "Running Balance (as stated in GL)",
    ])
    for line in report["lines"]:
        for tb in line["tb_accounts"]:
            gl_key = tb["gl_account"]
            if not gl_key:
                continue
            for t in gl_accounts[gl_key]["transactions"]:
                w.writerow([
                    line["label"], tb["tb_name"], gl_key, t["date"] or "", t["description"],
                    t["debit"], t["credit"], t["stated_balance"] if t["stated_balance"] is not None else "",
                ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_gl_detail.csv"},
    )


app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
