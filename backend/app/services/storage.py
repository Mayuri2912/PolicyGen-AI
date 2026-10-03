"""Controlled file storage.

All paths are built from generated identifiers inside the configured data
directory, which is outside anything the web server serves statically.  User
supplied names never reach the file system.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.config import get_settings

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _resolve(base: Path, name: str) -> Path:
    if not _SAFE_NAME.match(name) or ".." in name:
        raise ValueError("Unsafe storage name.")
    path = (base / name).resolve()
    if base.resolve() not in path.parents:
        raise ValueError("Storage path escapes its directory.")
    return path


def upload_path(storage_name: str) -> Path:
    return _resolve(get_settings().uploads_dir, storage_name)


def extracted_path(content_name: str) -> Path:
    return _resolve(get_settings().extracted_dir, content_name)


def report_path(policy_id: str, version: int, fmt: str) -> Path:
    return _resolve(get_settings().reports_dir, f"{policy_id}_v{version}.{fmt}")


def cache_path(name: str) -> Path:
    return _resolve(get_settings().cache_dir, name)


def write_json(path: Path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # A locked file on Windows must not break the request; the retention
        # sweep will pick it up later.
        pass


def remove_report_files(policy_id: str) -> None:
    for path in get_settings().reports_dir.glob(f"{policy_id}_v*"):
        remove(path)
