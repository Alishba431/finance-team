"""Single-service gateway for the finance-team monorepo.

Serves the root dashboard (index.html) at "/" and reverse-proxies each of
the three independent FastAPI tools, running as its own subprocess on its
own dynamically-picked internal port, under its own path prefix:

  /subscription-invoice-fetcher/   -> subscription-invoice-fetcher (python -m app.main)
  /balance-sheet-audit-trail/      -> balance-sheet-audit-trail (python main.py)
  /fixed-asset-register-auto-updater/ -> fixed-asset-register-auto-updater (python app.py)

Why subprocesses instead of one merged Python process: the three tools were
built independently, each with its own requirements.txt, its own in-memory
global state (RESULTS/PROGRESS in subscription-invoice-fetcher, SESSIONS in
balance-sheet-audit-trail), and its own FastAPI `app` object. Importing all
three into one process risks module-name collisions and shared global state
bugs. Running each as its own OS process keeps them fully isolated while
still letting the whole thing deploy and run as a single service (one
`python gateway.py`, one PORT) on platforms like Railway that expect a
single web process.

Run: `python gateway.py` (binds $PORT, default 8000).
See GATEWAY.md for details.
"""

import asyncio
import os
import signal
import socket
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.responses import StreamingResponse

BASE_DIR = Path(__file__).resolve().parent

# slug -> (working directory, start command)
TOOLS = {
    "subscription-invoice-fetcher": {
        "cwd": BASE_DIR / "subscription-invoice-fetcher",
        "cmd": [sys.executable, "-m", "app.main"],
    },
    "balance-sheet-audit-trail": {
        "cwd": BASE_DIR / "balance-sheet-audit-trail",
        "cmd": [sys.executable, "main.py"],
    },
    "fixed-asset-register-auto-updater": {
        "cwd": BASE_DIR / "fixed-asset-register-auto-updater",
        "cmd": [sys.executable, "app.py"],
    },
}

# Hop-by-hop headers that must not be blindly forwarded between the proxy
# and either side of it (RFC 7230 6.1); everything else, including Host,
# is passed through unchanged so a tool's own request.base_url reflects the
# gateway's real public host.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

app = FastAPI(title="Finance Team Gateway")

# slug -> {"port": int, "proc": asyncio.subprocess.Process, "logfile": file}
_procs = {}

_client = httpx.AsyncClient(follow_redirects=False, timeout=None)


_shutting_down = False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if _shutting_down:
            # A signal already killed every subprocess (see
            # _kill_all_children_sync); stop polling for a port that will
            # never open instead of dragging out process exit until this
            # timeout elapses.
            return False
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except OSError:
            await asyncio.sleep(0.2)
    return False


async def _start_tool(slug: str, info: dict):
    port = _free_port()
    log_dir = BASE_DIR / ".gateway-logs"
    log_dir.mkdir(exist_ok=True)
    logfile = open(log_dir / f"{slug}.log", "ab", buffering=0)

    env = os.environ.copy()
    env["PORT"] = str(port)
    if slug == "subscription-invoice-fetcher":
        env["PUBLIC_PATH_PREFIX"] = f"/{slug}"

    print(f"[gateway] starting {slug} on 127.0.0.1:{port} ...", flush=True)
    # start_new_session=True puts the child in its own process group, so on
    # shutdown we can signal the whole group (some tools may fork/exec a
    # shell wrapper internally) instead of orphaning a grandchild that keeps
    # the port bound.
    proc = await asyncio.create_subprocess_exec(
        *info["cmd"],
        cwd=str(info["cwd"]),
        env=env,
        stdout=logfile,
        stderr=logfile,
        start_new_session=True,
    )
    _procs[slug] = {"port": port, "proc": proc, "logfile": logfile}

    ok = await _wait_for_port(port)
    if not ok:
        print(f"[gateway] WARNING: {slug} did not open port {port} within "
              f"timeout; see .gateway-logs/{slug}.log", flush=True)
    else:
        print(f"[gateway] {slug} is up on 127.0.0.1:{port}", flush=True)


def _kill_all_children_sync():
    """Best-effort, synchronous kill of every subprocess's whole process
    group. Called from a raw OS signal handler (see below) so a SIGTERM
    that arrives while we're still inside the asyncio startup gather (e.g.
    a slow-importing tool like fixed-asset-register-auto-updater's
    rapidocr) still reaps every child immediately, instead of waiting for
    the ASGI lifespan 'shutdown' event, which only fires after 'startup'
    has fully finished."""
    global _shutting_down
    _shutting_down = True
    for slug, entry in list(_procs.items()):
        proc = entry["proc"]
        if proc.returncode is not None:
            continue
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            continue
        print(f"[gateway] stopping {slug} (pid {proc.pid}) ...", flush=True)
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass


@app.on_event("startup")
async def startup():
    await asyncio.gather(*(_start_tool(slug, info) for slug, info in TOOLS.items()))


@app.on_event("shutdown")
async def shutdown():
    await _client.aclose()
    for slug, entry in _procs.items():
        proc = entry["proc"]
        if proc.returncode is not None:
            continue
        print(f"[gateway] stopping {slug} (pid {proc.pid}) ...", flush=True)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            continue
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    for entry in _procs.values():
        try:
            entry["logfile"].close()
        except Exception:
            pass


@app.get("/", response_class=FileResponse)
async def dashboard():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/index.html", response_class=FileResponse)
async def dashboard_html():
    return FileResponse(BASE_DIR / "index.html")


class StreamingResponseWithClose(StreamingResponse):
    """Streams an httpx response body through and closes it afterwards."""

    def __init__(self, upstream_resp, status_code, headers):
        super().__init__(
            content=upstream_resp.aiter_raw(),
            status_code=status_code,
            headers=dict(headers),
        )
        self._upstream_resp = upstream_resp

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._upstream_resp.aclose()


async def _proxy(request: Request, slug: str, sub_path: str) -> Response:
    entry = _procs.get(slug)
    if entry is None:
        return Response("Tool not started.", status_code=502)

    target_url = httpx.URL(
        f"http://127.0.0.1:{entry['port']}/{sub_path}",
        query=request.url.query.encode("utf-8"),
    )

    headers = [
        (k, v) for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
    ]

    body = request.stream()
    upstream_req = _client.build_request(
        request.method, target_url, headers=headers, content=body,
    )
    upstream_resp = await _client.send(upstream_req, stream=True)

    response_headers = [
        (k, v) for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP
    ]

    return StreamingResponseWithClose(
        upstream_resp,
        status_code=upstream_resp.status_code,
        headers=response_headers,
    )


def _register_tool_routes(slug: str):
    prefix = f"/{slug}"

    @app.api_route(prefix, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def bare_prefix(request: Request, slug=slug):
        # Redirect the bare prefix (no trailing slash) to the trailing-slash
        # form so the tool's own relative asset/API paths resolve against
        # the right base. 307 preserves method + body for non-GET requests.
        target = request.url.path + "/"
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(url=target, status_code=307)

    @app.api_route(
        prefix + "/{sub_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxied(request: Request, sub_path: str = "", slug=slug):
        return await _proxy(request, slug, sub_path)


for _slug in TOOLS:
    _register_tool_routes(_slug)


if __name__ == "__main__":
    import uvicorn

    # uvicorn's own SIGTERM/SIGINT handling just flips a `should_exit` flag
    # that's only checked once the main serve loop is running; a signal
    # that arrives while we're still inside the (potentially slow, e.g.
    # rapidocr's import) subprocess-startup gather would otherwise sit
    # unnoticed until that finishes, orphaning already-started children in
    # the meantime. Patch in an immediate, synchronous kill of every
    # tracked subprocess's process group as the very first thing any
    # handled signal does, on top of uvicorn's normal graceful shutdown.
    _orig_handle_exit = uvicorn.Server.handle_exit

    def _handle_exit(self, sig, frame):
        _kill_all_children_sync()
        return _orig_handle_exit(self, sig, frame)

    uvicorn.Server.handle_exit = _handle_exit

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
