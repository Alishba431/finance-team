"""
Fixed Asset Register (FAR) Auto-Updater - FastAPI app.

Upload your existing FAR once as the baseline, then upload each period's
invoices + GL to have new asset additions (and identifiable disposals)
added into the correct existing tab, with depreciation calculated the same
way the baseline FAR already calculates it.

Run with:  python app.py
Binds to the PORT environment variable if set, else 8000.
"""
import os
import pathlib
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import far_engine as fe

BASE_DIR = pathlib.Path(__file__).parent

app = FastAPI(title="Fixed Asset Register Auto-Updater")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/")
def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/status")
def status():
    return fe.get_status()


@app.post("/api/baseline")
async def upload_baseline(far_file: UploadFile = File(...)):
    data = await far_file.read()
    if not data:
        raise HTTPException(400, "The uploaded file was empty.")
    try:
        result = fe.set_baseline(far_file.filename, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not process baseline FAR: {e}")
    return result


@app.post("/api/run_update")
async def run_update(
    gl_file: Optional[UploadFile] = File(default=None),
    invoices: Optional[List[UploadFile]] = File(default=None),
):
    if not fe.has_baseline():
        raise HTTPException(400, "No baseline FAR uploaded yet. Please upload the baseline FAR first.")

    if gl_file is None or not invoices:
        return JSONResponse({
            "status": "nothing_to_update",
            "message": ("Both invoices and a GL are required to process an update. "
                         "Nothing was updated."),
        })

    gl_bytes = await gl_file.read()
    if not gl_bytes:
        return JSONResponse({"status": "nothing_to_update", "message": "The GL file was empty."})

    invoice_payload = []
    for f in invoices:
        content = await f.read()
        if content:
            invoice_payload.append((f.filename, content))

    if not invoice_payload:
        return JSONResponse({"status": "nothing_to_update", "message": "No usable invoice files were received."})

    try:
        summary = fe.run_update(invoice_payload, (gl_file.filename, gl_bytes))
    except fe.NoBaselineError:
        raise HTTPException(400, "No baseline FAR uploaded yet.")
    except Exception as e:
        raise HTTPException(500, f"Update failed: {e}")
    return summary


@app.get("/api/download")
def download():
    if not fe.has_baseline():
        raise HTTPException(404, "No FAR available yet. Upload a baseline FAR first.")
    return FileResponse(
        str(fe.CURRENT_FAR_PATH),
        filename="Fixed_Asset_Register_Updated.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
