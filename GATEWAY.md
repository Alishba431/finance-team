# Single-service gateway

This repo can now be deployed as **one** service (one Railway app, one
`$PORT`) with `gateway.py`, instead of running three separate deployments.

## What it does

- Serves the dashboard (`index.html`) at `/` and `/index.html`.
- On startup, launches each tool as its own **subprocess**, in its own
  working directory, on its own dynamically-picked internal port
  (`127.0.0.1:<port>`, found via `socket.bind(("127.0.0.1", 0))`):
  - `subscription-invoice-fetcher` — `python -m app.main`
  - `balance-sheet-audit-trail` — `python main.py`
  - `fixed-asset-register-auto-updater` — `python app.py`
- Reverse-proxies everything under each tool's path prefix
  (`/subscription-invoice-fetcher/...`, `/balance-sheet-audit-trail/...`,
  `/fixed-asset-register-auto-updater/...`) to that subprocess, stripping
  the prefix before forwarding — the subprocess only ever sees the paths it
  always has (`/`, `/static/app.js`, `/api/upload`, ...).
- Redirects the bare prefix (no trailing slash) to the trailing-slash form
  with a 307, so each tool's now-relative asset/API paths resolve against
  the right base URL.
- Forwards method, headers (minus hop-by-hop ones), body, and status code
  byte-for-byte, including multipart uploads and streamed file downloads,
  via `httpx.AsyncClient` with `follow_redirects=False`.
- On its own SIGTERM/SIGINT, kills every subprocess's whole process group
  (`os.killpg`) so nothing is left listening on an internal port — even if
  the signal arrives while a subprocess is still starting up.

## Why subprocesses, not one merged Python process

The three tools were built independently: separate `requirements.txt`
files, separate FastAPI `app` objects, and separate in-memory global state
(`RESULTS`/`PROGRESS` in subscription-invoice-fetcher, `SESSIONS` in
balance-sheet-audit-trail). Importing all three into one process risks
Python module-name collisions (`app.py` vs `app/` package) and shared
global-state bugs. Running each as its own OS process keeps them fully
isolated while still shipping as a single deployable service.

Each tool's in-memory global state (`SESSIONS`, `RESULTS`/`PROGRESS`) is
safe under this architecture precisely because it stays inside that tool's
own subprocess — nothing about the gateway pools or shares state across
tools.

## Dependencies

`requirements.txt` at the repo root is the union of all three tools'
`requirements.txt` files plus `httpx` (used by the gateway's proxy). Only
`subscription-invoice-fetcher/requirements.txt` pins exact versions; the
other two tools leave their pins open, so there is currently no real
version conflict — `pip install -r requirements.txt` at the repo root
installs one environment that satisfies all three tools plus the gateway,
and that's the environment the gateway's subprocesses inherit (same
Python interpreter, `sys.executable`). If you add a new pinned dependency
to any one tool, re-check this file for a conflict before deploying.

## subscription-invoice-fetcher's absolute attachment links

That tool's `/attachments/{run_id}/{filename}` links get embedded both in
the live page and inside the downloadable `.xlsx` (where a relative path
can't work — Excel has no "current page" to resolve it against), so they
have to stay fully-qualified absolute URLs built from `request.base_url`.
The gateway sets `PUBLIC_PATH_PREFIX=/subscription-invoice-fetcher` in that
subprocess's environment; `app/sheet_processor.py` inserts it between the
host and the path:
`f"{base_url.rstrip('/')}{public_path_prefix}/attachments/{run_id}/{fname}"`.
This only works because the gateway forwards the original `Host` header
unchanged, so `request.base_url` inside the subprocess reflects the
gateway's real public host, not `127.0.0.1:<internal-port>`.

## Running it

```
pip install -r requirements.txt
python gateway.py          # binds $PORT, defaults to 8000
```

Per-subprocess stdout/stderr is captured under `.gateway-logs/<slug>.log`
(gitignored) for debugging startup failures.

## Deploying (e.g. Railway)

- Build: `pip install -r requirements.txt`
- Start: `python gateway.py`
- Railway sets `$PORT`; the gateway binds it and each tool's own `$PORT`
  is overridden per-subprocess to its internal port, so there's no
  collision.
