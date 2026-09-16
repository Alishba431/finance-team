import os
import traceback
import uuid

import re

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import gmail_auth, sheet_processor
from .paths import BASE_DIR, UPLOADS_DIR, OUTPUTS_DIR, ATTACHMENTS_DIR, ensure_dirs

ensure_dirs()

app = FastAPI(title="Subscription Invoice Fetcher")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# In-memory: per-run result summaries. A single-admin-inbox tool doesn't need
# persistent multi-user session state, and no sign-in credential is ever
# kept here - the admin email/app password only live inside the /process
# request handler for the duration of that one run.
RESULTS = {}

# In-memory row-by-row progress per run, polled by the browser while
# /process is still working through the sheet so the page can show
# "Processing row N of M..." instead of a static loading message.
PROGRESS = {}
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, error: str = None):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "result": None,
        "error": error,
    })


@app.post("/process", response_class=HTMLResponse)
async def process(
    request: Request,
    file: UploadFile = File(...),
    admin_email: str = Form(...),
    app_password: str = Form(...),
    progress_id: str = Form(None),
):
    error = None
    result = None
    imap_conn = None

    try:
        imap_conn = gmail_auth.connect(admin_email, app_password)
    except gmail_auth.GmailSignInError as exc:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "result": None,
            "error": str(exc),
        })
    finally:
        # The app password is never needed again after this login attempt;
        # drop our only reference to it immediately.
        app_password = None

    run_id = uuid.uuid4().hex[:12]
    # The browser generates its own id before submitting so it can start
    # polling /progress/{progress_key} immediately, without waiting for this
    # (possibly slow) request to finish.
    progress_key = progress_id if progress_id and _RUN_ID_RE.match(progress_id) else run_id
    input_path = UPLOADS_DIR / f"{run_id}_{file.filename}"
    input_path.write_bytes(await file.read())
    output_path = OUTPUTS_DIR / f"{run_id}.xlsx"
    run_attachments_dir = ATTACHMENTS_DIR / run_id

    def report_progress(current, total):
        PROGRESS[progress_key] = {"current": current, "total": total, "done": False}

    try:
        base_url = str(request.base_url)
        summary = sheet_processor.process_workbook(
            input_path, output_path, run_attachments_dir, base_url, run_id, imap_conn,
            progress_cb=report_progress,
        )
        summary["run_id"] = run_id
        if not summary.get("unreadable"):
            RESULTS[run_id] = {"output_path": str(output_path)}
        result = summary
    except Exception as exc:
        traceback.print_exc()
        error = f"Could not process the sheet: {exc}"
    finally:
        gmail_auth.disconnect(imap_conn)
        PROGRESS[progress_key] = {
            **PROGRESS.get(progress_key, {"current": 0, "total": 0}),
            "done": True,
        }

    return templates.TemplateResponse("index.html", {
        "request": request,
        "result": result,
        "error": error,
    })


@app.get("/progress/{progress_key}")
def progress(progress_key: str):
    return JSONResponse(PROGRESS.get(progress_key, {"current": 0, "total": 0, "done": False}))


@app.get("/download/{run_id}")
def download(run_id: str):
    entry = RESULTS.get(run_id)
    if not entry:
        return PlainTextResponse("Unknown run id.", status_code=404)
    return FileResponse(
        entry["output_path"],
        filename="invoice_breakdown_result.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/attachments/{run_id}/{filename}")
def get_attachment(run_id: str, filename: str):
    path = ATTACHMENTS_DIR / run_id / filename
    if not path.exists():
        return PlainTextResponse("Attachment not found.", status_code=404)
    return FileResponse(path, filename=filename)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
