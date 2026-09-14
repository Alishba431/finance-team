import os
import traceback
import uuid

# Local/loopback OAuth callbacks are typically http, not https; relax the
# oauthlib scheme check accordingly. This has no effect on the OAuth
# client secret handling itself - it only permits the callback URL scheme.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from . import gmail_auth, gmail_search, sheet_processor
from .paths import BASE_DIR, UPLOADS_DIR, OUTPUTS_DIR, ATTACHMENTS_DIR, ensure_dirs

ensure_dirs()

app = FastAPI(title="Subscription Invoice Fetcher")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# In-memory: per-run result summaries and the pending OAuth state token.
# A single-admin-inbox tool doesn't need persistent multi-user session state.
RESULTS = {}
_PENDING_OAUTH_STATE = {}


def _redirect_uri(request: Request) -> str:
    return str(request.url_for("gmail_callback"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request, error: str = None):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "oauth_configured": gmail_auth.oauth_configured(),
        "connected": gmail_auth.is_connected(),
        "redirect_uri": _redirect_uri(request) if gmail_auth.oauth_configured() else "",
        "result": None,
        "error": error,
    })


@app.get("/auth/gmail/start")
def gmail_start(request: Request):
    if not gmail_auth.oauth_configured():
        return RedirectResponse("/?error=" + "Gmail OAuth is not configured (missing GMAIL_CLIENT_ID/SECRET).", status_code=303)
    redirect_uri = _redirect_uri(request)
    flow = gmail_auth.build_flow(redirect_uri)
    auth_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    _PENDING_OAUTH_STATE["state"] = state
    return RedirectResponse(auth_url, status_code=303)


@app.get("/auth/gmail/callback", name="gmail_callback")
def gmail_callback(request: Request):
    expected_state = _PENDING_OAUTH_STATE.get("state")
    if not expected_state or request.query_params.get("state") != expected_state:
        return RedirectResponse("/?error=" + "Gmail sign-in failed: state mismatch, please try connecting again.", status_code=303)
    redirect_uri = _redirect_uri(request)
    flow = gmail_auth.build_flow(redirect_uri)
    try:
        flow.fetch_token(authorization_response=str(request.url))
    except Exception as exc:
        return RedirectResponse("/?error=" + f"Gmail sign-in failed: {exc}", status_code=303)
    gmail_auth.save_credentials(flow.credentials)
    _PENDING_OAUTH_STATE.pop("state", None)
    return RedirectResponse("/", status_code=303)


@app.post("/auth/gmail/disconnect")
def gmail_disconnect():
    gmail_auth.disconnect()
    return RedirectResponse("/", status_code=303)


@app.post("/process", response_class=HTMLResponse)
async def process(request: Request, file: UploadFile = File(...)):
    creds = gmail_auth.load_credentials()
    if not creds or not creds.valid:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "oauth_configured": gmail_auth.oauth_configured(),
            "connected": False,
            "redirect_uri": _redirect_uri(request) if gmail_auth.oauth_configured() else "",
            "result": None,
            "error": "Connect the admin Gmail account before running a sheet.",
        })

    run_id = uuid.uuid4().hex[:12]
    input_path = UPLOADS_DIR / f"{run_id}_{file.filename}"
    input_path.write_bytes(await file.read())
    output_path = OUTPUTS_DIR / f"{run_id}.xlsx"
    run_attachments_dir = ATTACHMENTS_DIR / run_id

    error = None
    result = None
    try:
        service = gmail_search.build_service(creds)
        base_url = str(request.base_url)
        summary = sheet_processor.process_workbook(
            input_path, output_path, run_attachments_dir, base_url, run_id, service
        )
        summary["run_id"] = run_id
        RESULTS[run_id] = {"output_path": str(output_path)}
        result = summary
    except Exception as exc:
        traceback.print_exc()
        error = f"Could not process the sheet: {exc}"

    return templates.TemplateResponse("index.html", {
        "request": request,
        "oauth_configured": gmail_auth.oauth_configured(),
        "connected": gmail_auth.is_connected(),
        "redirect_uri": _redirect_uri(request) if gmail_auth.oauth_configured() else "",
        "result": result,
        "error": error,
    })


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
