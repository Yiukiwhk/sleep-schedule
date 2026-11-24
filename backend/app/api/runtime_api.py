"""Runtime information for the bundled frontend and the desktop shell.

The frontend is plain static files shared by the server and the desktop build, so
it cannot read a build-time flag: it asks this endpoint what kind of instance it is
talking to (hide login-only affordances in desktop mode, show the sync section,
know which version to compare against the updater manifest).
"""
from __future__ import annotations

from fastapi import APIRouter

from ..config import settings
from ..version import MIN_SUPPORTED, SCHEMA_VERSION, VERSION

router = APIRouter(tags=["runtime"])


@router.get("/runtime")
def runtime_info() -> dict:
    return {
        "mode": settings.MODE,
        "desktop": settings.DESKTOP,
        "version": VERSION,
        "min_supported": MIN_SUPPORTED,
        "schema_version": SCHEMA_VERSION,
        "retention_months": settings.RETENTION_MONTHS,
        "write_cooldown_seconds": settings.WRITE_COOLDOWN_SECONDS,
    }
