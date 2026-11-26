"""Desktop shell for Yiuki Sleep (pywebview window around a local uvicorn server).

Layout:
  desktop/runtime.py   paths, port, token, health polling (stdlib only)
  desktop/security.py  DPAPI helpers for the cloud device token
  desktop/app.py       entry point (window mode, --smoke self-check)
  desktop/updater.py   update manifest check
  desktop/importers/   JSON / CSV / PDF / screenshot importers
"""

from __future__ import annotations

__version__ = "0.1.4"

__all__ = ["__version__"]
