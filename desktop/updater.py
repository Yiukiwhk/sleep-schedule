"""Update check for the desktop build (docs/DESKTOP.md section 9).

The app never downloads or replaces its own files: it only reads the published
manifest and, when a newer version exists, points the user at the download URL.
The manifest lives next to the installer on the site, e.g.
https://yiuki.me/desktop/latest.json

Shape of the manifest::

    {"version": "0.2.0", "min_supported": "0.2.0", "notes": "...",
     "url": "https://yiuki.me/desktop/SleepSchedule-setup-0.2.0.exe",
     "sha256": "…", "size": 123456}

Pure standard library so the update check also works in the frozen bundle.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field

DEFAULT_MANIFEST_URL = "https://yiuki.me/desktop/latest.json"
CHECK_TIMEOUT = 6.0
USER_AGENT = "SleepSchedule-Desktop"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def parse_version(value: str) -> tuple[int, int, int]:
    """Return (major, minor, patch); unparsable versions sort as 0.0.0."""
    match = _VERSION_RE.match((value or "").strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


@dataclass
class UpdateInfo:
    current: str
    latest: str = ""
    notes: str = ""
    url: str = ""
    sha256: str = ""
    size: int = 0
    min_supported: str = ""
    update_available: bool = False
    error: str = ""
    ok: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "current": self.current,
            "latest": self.latest,
            "notes": self.notes,
            "url": self.url,
            "sha256": self.sha256,
            "size": self.size,
            "min_supported": self.min_supported,
            "update_available": self.update_available,
            "error": self.error,
            "warnings": list(self.warnings),
        }


def fetch_manifest(url: str = DEFAULT_MANIFEST_URL, *, timeout: float = CHECK_TIMEOUT) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        payload = response.read(64 * 1024)
    # utf-8-sig：发布脚本可能带 BOM（Windows PowerShell 的 -Encoding utf8 会写 BOM），
    # json.loads 不认 BOM，先剥掉再解析。
    manifest = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest is not an object")
    return manifest


def check(current: str, *, url: str = DEFAULT_MANIFEST_URL, timeout: float = CHECK_TIMEOUT) -> UpdateInfo:
    """Best effort: network or format failures come back as info.error, never raise."""
    info = UpdateInfo(current=current)
    try:
        manifest = fetch_manifest(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - update checks must never break startup
        info.error = f"network: {exc}"
        return info
    info.latest = str(manifest.get("version") or "")
    info.notes = str(manifest.get("notes") or "")
    info.url = str(manifest.get("url") or "")
    info.sha256 = str(manifest.get("sha256") or "")
    info.min_supported = str(manifest.get("min_supported") or "")
    try:
        info.size = int(manifest.get("size") or 0)
    except (TypeError, ValueError):
        info.size = 0
    if info.latest:
        info.update_available = is_newer(info.latest, current)
    else:
        info.error = "manifest: missing version"
        return info
    if not info.url.startswith("https://"):
        info.warnings.append("update.url_not_https")
    if info.sha256 and not re.fullmatch(r"[0-9a-f]{64}", info.sha256):
        info.warnings.append("update.bad_sha256")
    info.ok = True
    return info
