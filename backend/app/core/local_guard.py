"""Loopback guard for desktop mode: a per-launch token plus an Origin check.

The desktop shell generates a random token on every start and injects it into the
browser URL as ``?t=<token>``.  The frontend moves that value into sessionStorage
and sends it back as ``X-Local-Token`` on every API call, so another local process
(another user account on the same machine, a browser tab on a hostile page) cannot
drive the API even though the server listens on 127.0.0.1.

Only ``/api/*`` is guarded: the HTML shell and static assets have to load before
any JavaScript can attach a header (that is why the token travels in the URL once).
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import settings

_FORBIDDEN = {"detail": "error.forbidden", "code": "error.forbidden"}

_LOOPBACK_ORIGINS = ("http://127.0.0.1:{port}", "http://localhost:{port}")


def _origin_allowed(origin: str, port: int) -> bool:
    return origin in {template.format(port=port) for template in _LOOPBACK_ORIGINS}


def install_local_guard(app: FastAPI) -> None:
    """Install the guard; a no-op when no token was injected (tests, dev servers)."""
    token = settings.LOCAL_TOKEN
    if not token:
        return

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        path = request.url.path
        guarded = path == "/api" or path.startswith("/api/")
        if guarded:
            if request.headers.get("x-local-token") != token:
                return JSONResponse(status_code=403, content=_FORBIDDEN)
            origin = request.headers.get("origin")
            port = request.url.port or 80
            if origin and not _origin_allowed(origin, port):
                return JSONResponse(status_code=403, content=_FORBIDDEN)
        return await call_next(request)
