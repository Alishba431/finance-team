"""
Fixed Asset Register Updater - FastAPI app.

Serves a small upload form (no build step) and a /process endpoint that
runs the FAR update and streams back an updated workbook. Nothing is
persisted server-side; every upload lives only in memory for the duration
of one request.
"""
import io
import os
import re
import traceback
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from processing import process_far

app = FastAPI(title="Fixed Asset Register Updater")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok"}


def _parse_fye_override(value):
    if not value:
        return None
    value = value.strip()
    m = re.match(r"^(\d{1,2})-(\d{1,2})$", value)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    if 1 <= month <= 12 and 1 <= day <= 31:
        return (month, day)
    return None


@app.post("/process")
async def process(
    far_file: UploadFile = File(...),
    gl_file: UploadFile = File(...),
    invoice_register: Optional[UploadFile] = File(None),
    invoice_files: Optional[List[UploadFile]] = File(None),
    period_end_date: str = Form(...),
    period_months: int = Form(1),
    fiscal_year_end: str = Form(""),
):
    try:
        if not far_file.filename.lower().endswith(".xlsx"):
            return JSONResponse(
                {"error": "The existing FAR must be an .xlsx workbook - the exact file you already maintain."},
                status_code=400,
            )
        far_bytes = await far_file.read()

        gl_filename = gl_file.filename or "gl.xlsx"
        if not (gl_filename.lower().endswith(".xlsx") or gl_filename.lower().endswith(".csv")):
            return JSONResponse(
                {"error": "The GL export must be an .xlsx or .csv file."}, status_code=400
            )
        gl_bytes = await gl_file.read()

        invoice_register_bytes = None
        invoice_register_filename = None
        if invoice_register is not None and invoice_register.filename:
            invoice_register_filename = invoice_register.filename
            invoice_register_bytes = await invoice_register.read()

        invoice_pairs = []
        for f in invoice_files or []:
            if f is None or not f.filename:
                continue
            data = await f.read()
            invoice_pairs.append((f.filename, data))

        try:
            period_end = datetime.strptime(period_end_date, "%Y-%m-%d").date()
        except ValueError:
            return JSONResponse(
                {"error": "Period end date must be a valid date (YYYY-MM-DD)."}, status_code=400
            )

        if period_months < 1 or period_months > 12:
            return JSONResponse(
                {"error": "Period length must be between 1 and 12 months."}, status_code=400
            )

        fye_override = _parse_fye_override(fiscal_year_end)

        output_bytes, summary = process_far(
            far_bytes=far_bytes,
            gl_bytes=gl_bytes,
            gl_filename=gl_filename,
            invoice_files=invoice_pairs,
            invoice_register_bytes=invoice_register_bytes,
            invoice_register_filename=invoice_register_filename,
            period_end_date=period_end,
            period_months=period_months,
            fye_override=fye_override,
        )

        out_name = f"FAR_Updated_{period_end.isoformat()}.xlsx"
        headers = {
            "Content-Disposition": f'attachment; filename="{out_name}"',
            "X-FAR-Summary-Additions": str(summary["additions_count"]),
            "X-FAR-Summary-NeedsReview": str(summary["needs_review_count"]),
        }
        return StreamingResponse(
            io.BytesIO(output_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )
    except Exception as exc:  # noqa: BLE001 - surface a readable error to the finance user
        traceback.print_exc()
        return JSONResponse(
            {"error": f"Something went wrong while processing these files: {exc}"},
            status_code=400,
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
