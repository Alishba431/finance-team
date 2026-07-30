"""SOFP Tracer - FastAPI application.

Upload the financial statements, the trial balance and the general ledger, then
trace every Statement of Financial Position head down to the trial balance
accounts behind it and the individual ledger entries behind those.
"""

from __future__ import annotations

import json
import os
import shutil
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from openpyxl import load_workbook

from . import export as export_module
from .parsers import (
    ParseError,
    nkey,
    parse_general_ledger,
    parse_sofp,
    parse_trial_balance,
)
from .tracing import PL_BUCKET, UNMAPPED, build_trace

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SOFP_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
MAPPING_FILE = DATA_DIR / "mapping.json"
STATIC_DIR = BASE_DIR / "app" / "static"

MAX_UPLOAD_BYTES = 80 * 1024 * 1024  # 80 MB

KINDS = {
    "financials": {
        "label": "Financial statements (SOFP)",
        "filename": "financials.xlsx",
        "parser": parse_sofp,
    },
    "trial_balance": {
        "label": "Trial balance",
        "filename": "trial_balance.xlsx",
        "parser": parse_trial_balance,
    },
    "general_ledger": {
        "label": "General ledger",
        "filename": "general_ledger.xlsx",
        "parser": parse_general_ledger,
    },
}

# Directories that may already hold the workbooks when the app first starts.
SEED_DIRS = [
    os.environ.get("SOFP_SEED_DIR"),
    BASE_DIR.parent / "uploads",
    BASE_DIR / "uploads",
    BASE_DIR / "sample_data",
]

STATE: Dict[str, Any] = {
    "sources": {kind: None for kind in KINDS},
    "parsed": {kind: None for kind in KINDS},
    "errors": {kind: None for kind in KINDS},
    "overrides": {},
    "seed_notes": [],
}


# --------------------------------------------------------------------------
# persistence helpers
# --------------------------------------------------------------------------


def _ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _load_overrides() -> Dict[str, str]:
    if not MAPPING_FILE.exists():
        return {}
    try:
        payload = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    assignments = payload.get("assignments", {})
    return {str(k): str(v) for k, v in assignments.items() if isinstance(v, str)}


def _save_overrides(overrides: Dict[str, str]) -> None:
    _ensure_dirs()
    MAPPING_FILE.write_text(
        json.dumps({"assignments": overrides}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _classify_workbook(path: Path) -> Optional[str]:
    """Work out which of the three inputs a workbook is, from its sheet names."""
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheets = [nkey(name) for name in workbook.sheetnames]
        workbook.close()
    except Exception:
        sheets = []

    if any("sofp" in s or "statement of financial position" in s for s in sheets):
        return "financials"
    if any("trial balance" in s or s == "tb" for s in sheets):
        return "trial_balance"
    if any("general ledger" in s or "ledger" in s for s in sheets):
        return "general_ledger"

    name = nkey(path.stem)
    if "financial" in name or name.startswith("fs"):
        return "financials"
    if name.startswith("tb") or "trial" in name:
        return "trial_balance"
    if name.startswith("gl") or "ledger" in name:
        return "general_ledger"
    return None


def _seed_from_disk() -> None:
    """Pick up workbooks that were already shared with the project, if any."""
    _ensure_dirs()
    if any(UPLOAD_DIR.glob("*.xlsx")):
        return

    for candidate in SEED_DIRS:
        if not candidate:
            continue
        directory = Path(candidate)
        if not directory.is_dir():
            continue
        found = sorted(
            p for p in directory.glob("*.xlsx") if not p.name.startswith("~$")
        )
        if not found:
            continue
        for path in found:
            kind = _classify_workbook(path)
            if not kind:
                STATE["seed_notes"].append(
                    f"Could not tell what '{path.name}' is - please upload it explicitly."
                )
                continue
            target = UPLOAD_DIR / KINDS[kind]["filename"]
            if target.exists():
                continue
            shutil.copyfile(path, target)
            (UPLOAD_DIR / f"{kind}.origin").write_text(path.name, encoding="utf-8")
            STATE["seed_notes"].append(f"Loaded '{path.name}' as {KINDS[kind]['label']}.")
        break


def _reload_all() -> None:
    """(Re)parse whatever workbooks are currently stored."""
    _ensure_dirs()
    for kind, config in KINDS.items():
        path = UPLOAD_DIR / config["filename"]
        if not path.exists():
            STATE["parsed"][kind] = None
            STATE["sources"][kind] = None
            STATE["errors"][kind] = None
            continue

        origin_file = UPLOAD_DIR / f"{kind}.origin"
        original_name = (
            origin_file.read_text(encoding="utf-8").strip()
            if origin_file.exists()
            else config["filename"]
        )
        try:
            STATE["parsed"][kind] = config["parser"](str(path))
            STATE["errors"][kind] = None
        except ParseError as exc:
            STATE["parsed"][kind] = None
            STATE["errors"][kind] = str(exc)
        except Exception as exc:  # a corrupt or unreadable workbook
            STATE["parsed"][kind] = None
            STATE["errors"][kind] = f"Could not read this workbook: {exc}"
            traceback.print_exc()
        STATE["sources"][kind] = {
            "name": original_name,
            "size": path.stat().st_size,
        }


@asynccontextmanager
async def lifespan(_: FastAPI):
    _ensure_dirs()
    STATE["overrides"] = _load_overrides()
    _seed_from_disk()
    _reload_all()
    yield


app = FastAPI(title="SOFP Tracer", version="1.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------
# trace construction
# --------------------------------------------------------------------------


def _current_trace(year: Optional[str], tolerance: float) -> Dict[str, Any]:
    return build_trace(
        sofp=STATE["parsed"]["financials"],
        tb=STATE["parsed"]["trial_balance"],
        gl=STATE["parsed"]["general_ledger"],
        overrides=STATE["overrides"],
        tolerance=tolerance,
        year_label=year,
    )


def _state_payload() -> Dict[str, Any]:
    warnings: List[Dict[str, str]] = []
    for kind, parsed in STATE["parsed"].items():
        if not parsed:
            continue
        for message in parsed.get("warnings", []):
            warnings.append({"source": KINDS[kind]["label"], "message": message})

    financials = STATE["parsed"]["financials"]
    return {
        "sources": {
            kind: {
                "label": KINDS[kind]["label"],
                "loaded": STATE["parsed"][kind] is not None,
                "file": STATE["sources"][kind],
                "sheet": (STATE["parsed"][kind] or {}).get("sheet"),
                "error": STATE["errors"][kind],
            }
            for kind in KINDS
        },
        "ready": all(STATE["parsed"][kind] is not None for kind in ("financials", "trial_balance")),
        "years": financials["years"] if financials else [],
        "warnings": warnings,
        "notes": STATE["seed_notes"],
    }


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "loaded": {kind: STATE["parsed"][kind] is not None for kind in KINDS},
    }


@app.get("/api/state")
def get_state() -> Dict[str, Any]:
    return _state_payload()


@app.post("/api/upload")
async def upload(
    financials: Optional[UploadFile] = File(None),
    trial_balance: Optional[UploadFile] = File(None),
    general_ledger: Optional[UploadFile] = File(None),
    files: Optional[List[UploadFile]] = File(None),
) -> Dict[str, Any]:
    """Accept the workbooks, either in named slots or as a loose set to sort out."""
    _ensure_dirs()
    stored: List[Dict[str, str]] = []
    rejected: List[Dict[str, str]] = []

    async def store(upload_file: UploadFile, kind: Optional[str]) -> None:
        content = await upload_file.read()
        name = upload_file.filename or "upload.xlsx"
        if len(content) > MAX_UPLOAD_BYTES:
            rejected.append({"file": name, "reason": "File is larger than 80 MB."})
            return
        if not name.lower().endswith(".xlsx"):
            rejected.append(
                {
                    "file": name,
                    "reason": "Only .xlsx workbooks can be read. Re-save .xls or .csv files as .xlsx.",
                }
            )
            return

        temp_path = UPLOAD_DIR / f"_incoming_{Path(name).name}"
        temp_path.write_bytes(content)

        resolved = kind or _classify_workbook(temp_path)
        if resolved not in KINDS:
            temp_path.unlink(missing_ok=True)
            rejected.append(
                {
                    "file": name,
                    "reason": "Could not tell whether this is the financials, the trial "
                    "balance or the general ledger - upload it into a specific box.",
                }
            )
            return

        target = UPLOAD_DIR / KINDS[resolved]["filename"]
        shutil.move(str(temp_path), str(target))
        (UPLOAD_DIR / f"{resolved}.origin").write_text(name, encoding="utf-8")
        stored.append({"file": name, "kind": resolved, "label": KINDS[resolved]["label"]})

    for kind, upload_file in (
        ("financials", financials),
        ("trial_balance", trial_balance),
        ("general_ledger", general_ledger),
    ):
        if upload_file is not None and upload_file.filename:
            await store(upload_file, kind)

    for upload_file in files or []:
        if upload_file is not None and upload_file.filename:
            await store(upload_file, None)

    if not stored and not rejected:
        raise HTTPException(status_code=400, detail="No files were received.")

    _reload_all()
    return {"stored": stored, "rejected": rejected, "state": _state_payload()}


@app.post("/api/clear")
def clear() -> Dict[str, Any]:
    """Remove the uploaded workbooks (mapping overrides are kept)."""
    _ensure_dirs()
    for path in UPLOAD_DIR.iterdir():
        if path.is_file():
            path.unlink()
    STATE["seed_notes"] = []
    _reload_all()
    return {"state": _state_payload()}


@app.get("/api/trace")
def get_trace(
    year: Optional[str] = None, tolerance: float = Query(1.0, ge=0)
) -> Dict[str, Any]:
    if STATE["parsed"]["financials"] is None:
        raise HTTPException(
            status_code=409,
            detail="The financial statements workbook has not been loaded yet.",
        )
    if STATE["parsed"]["trial_balance"] is None:
        raise HTTPException(
            status_code=409, detail="The trial balance workbook has not been loaded yet."
        )

    trace = _current_trace(year, tolerance)
    return {
        "summary": trace["summary"],
        "years": trace["years"],
        "heads": trace["heads"],
        "head_options": trace["head_options"],
        "counts": {
            "unmapped": len(trace["unmapped"]),
            "income_expenditure": len(trace["pl_accounts"]),
            "gl_only": len(trace["gl_only"]),
        },
        "state": _state_payload(),
    }


@app.get("/api/head")
def get_head(
    head_id: str, year: Optional[str] = None, tolerance: float = Query(1.0, ge=0)
) -> Dict[str, Any]:
    """The trial balance accounts sitting behind one SOFP head."""
    trace = _current_trace(year, tolerance)

    accounts = sorted(
        (a for a in trace["accounts"].values() if a["head_id"] == head_id),
        key=lambda a: (a["code"] or "", a["label"]),
    )
    head = next((h for h in trace["heads"] if h["id"] == head_id), None)
    if head is None and head_id not in (PL_BUCKET, UNMAPPED):
        raise HTTPException(status_code=404, detail="Unknown SOFP head.")

    return {"head": head, "head_id": head_id, "accounts": accounts}


@app.get("/api/account")
def get_account(
    key: str, year: Optional[str] = None, tolerance: float = Query(1.0, ge=0)
) -> Dict[str, Any]:
    """One account: its trial balance figures and every ledger entry behind it."""
    trace = _current_trace(year, tolerance)
    account = trace["accounts"].get(key)
    if account is None:
        raise HTTPException(status_code=404, detail="Unknown trial balance account.")

    gl = STATE["parsed"]["general_ledger"]
    ledger = (gl or {}).get("accounts", {}).get(key)
    return {
        "account": account,
        "transactions": ledger["transactions"] if ledger else [],
        "ledger": (
            {
                "opening": ledger["opening"],
                "debit": ledger["debit"],
                "credit": ledger["credit"],
                "closing": ledger["closing"],
                "reported_balance": ledger["reported_balance"],
                "summary_difference": ledger["summary_difference"],
            }
            if ledger
            else None
        ),
    }


@app.get("/api/review")
def get_review(
    year: Optional[str] = None, tolerance: float = Query(1.0, ge=0)
) -> Dict[str, Any]:
    """Everything a reviewer needs to look at before relying on the trace."""
    trace = _current_trace(year, tolerance)
    mapped = [
        a
        for a in trace["accounts"].values()
        if a["head_id"] not in (UNMAPPED, PL_BUCKET)
    ]
    return {
        "unmapped": trace["unmapped"],
        "low_confidence": sorted(
            (
                a
                for a in mapped
                if a["map_source"] == "auto" and a["map_confidence"] == "low"
            ),
            key=lambda a: (a["code"] or "", a["label"]),
        ),
        "gl_mismatch": sorted(
            (a for a in mapped if a["ties_to_gl"] is False),
            key=lambda a: -abs(a["closing_difference"] or 0),
        ),
        "missing_gl": sorted(
            (a for a in mapped if not a["in_gl"]),
            key=lambda a: (a["code"] or "", a["label"]),
        ),
        "gl_only": trace["gl_only"],
        "income_expenditure": trace["pl_accounts"],
        "head_options": trace["head_options"],
        "summary": trace["summary"],
    }


@app.post("/api/mapping")
async def set_mapping(request: Request) -> Dict[str, Any]:
    """Save user overrides of the account -> SOFP head mapping."""
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Expected a JSON body.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object.")

    if payload.get("reset"):
        STATE["overrides"] = {}
        _save_overrides(STATE["overrides"])
        return {"assignments": STATE["overrides"], "reset": True}

    assignments = payload.get("assignments")
    if not isinstance(assignments, dict) or not assignments:
        raise HTTPException(
            status_code=400,
            detail="Provide {\"assignments\": {\"<account key>\": \"<head id>\"}}.",
        )

    overrides = dict(STATE["overrides"])
    for key, head_id in assignments.items():
        if head_id in (None, "", "__auto__"):
            overrides.pop(str(key), None)  # fall back to the automatic suggestion
        elif isinstance(head_id, str):
            overrides[str(key)] = head_id
        else:
            raise HTTPException(
                status_code=400, detail=f"Invalid head id for account '{key}'."
            )

    STATE["overrides"] = overrides
    _save_overrides(overrides)
    return {"assignments": overrides, "count": len(overrides)}


@app.get("/api/export.xlsx")
def export_workbook(
    year: Optional[str] = None, tolerance: float = Query(1.0, ge=0)
) -> Response:
    if STATE["parsed"]["financials"] is None or STATE["parsed"]["trial_balance"] is None:
        raise HTTPException(
            status_code=409,
            detail="Load the financial statements and the trial balance before exporting.",
        )
    trace = _current_trace(year, tolerance)
    sources = {
        kind: (STATE["sources"][kind] or {}).get("name") for kind in KINDS
    }
    content = export_module.build_workbook(
        trace, STATE["parsed"]["general_ledger"], sources
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="sofp-trace.xlsx"',
        },
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(ParseError)
def parse_error_handler(request: Request, exc: ParseError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def run() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run()
