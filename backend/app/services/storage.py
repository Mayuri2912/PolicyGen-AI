"""Controlled file storage.

All paths are built from generated identifiers inside the configured data
directory, which is outside anything the web server serves statically.  User
supplied names never reach the file system.
"""

from __future__ import annotations

import json
import mimetypes
import re
import uuid
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.core.errors import ServiceUnavailable

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _remote_enabled() -> bool:
    backend = get_settings().storage_backend
    if backend not in ("local", "supabase"):
        raise ServiceUnavailable("The storage backend is not configured correctly.")
    return backend == "supabase"


def _object_name(path: Path) -> str | None:
    settings = get_settings()
    try:
        relative = path.resolve().relative_to(settings.data_dir.resolve())
    except ValueError:
        raise ValueError("Storage path escapes the data directory.") from None
    if len(relative.parts) != 2 or relative.parts[0] not in ("uploads", "extracted", "generated_reports", "cache"):
        return None  # temporary files are intentionally local
    if not _SAFE_NAME.fullmatch(relative.name) or ".." in relative.name:
        raise ValueError("Unsafe storage name.")
    if relative.name.endswith((".part", ".upload")):
        return None
    return relative.as_posix()


def _request(method: str, endpoint: str, **kwargs) -> httpx.Response:
    settings = get_settings()
    if not settings.supabase_url.startswith("https://") or not settings.supabase_service_key:
        raise ServiceUnavailable("External file storage is not configured. Contact the administrator.")
    headers = {"apikey": settings.supabase_service_key, "Authorization": f"Bearer {settings.supabase_service_key}"}
    headers.update(kwargs.pop("headers", {}))
    # Response bodies and exceptions can contain credentials; never propagate them.
    for attempt in range(3):
        try:
            response = httpx.request(method, f"{settings.supabase_url.rstrip('/')}/storage/v1/{endpoint}",
                                     headers=headers, timeout=60, **kwargs)
        except httpx.TransportError:
            if attempt < 2:
                continue
            raise ServiceUnavailable("External file storage could not be reached. Please retry.") from None
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            continue
        if response.status_code >= 400 and response.status_code != 404:
            # Supabase represents missing objects as HTTP 400 with statusCode 404.
            if response.status_code == 400:
                try:
                    if str(response.json().get("statusCode")) == "404":
                        return httpx.Response(404)
                except (ValueError, AttributeError):
                    pass
            raise ServiceUnavailable("External file storage rejected the operation. Check its configuration and quota.")
        return response
    raise ServiceUnavailable("External file storage is temporarily unavailable.")


def persist(path: Path) -> None:
    """Publish a completed local file before its database record is committed."""
    if not _remote_enabled():
        return
    name = _object_name(path)
    if name:
        response = _request("POST", f"object/{get_settings().supabase_storage_bucket}/{name}",
                            content=path.read_bytes(), headers={"x-upsert": "true", "Cache-Control": "no-store",
                            "Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"})
        if response.status_code == 404:
            raise ServiceUnavailable("The external storage bucket was not found.")


def materialize(path: Path) -> bool:
    """Restore a file after Render loses its local cache. Remote storage is authoritative."""
    if not _remote_enabled():
        return path.exists()
    name = _object_name(path)
    if not name:
        return path.exists()
    response = _request("GET", f"object/authenticated/{get_settings().supabase_storage_bucket}/{name}")
    if response.status_code == 404:
        path.unlink(missing_ok=True)
        return False
    _write_local(path, response.content)
    return True


def _write_local(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")
    try:
        tmp.write_bytes(content)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


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
    _write_local(path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    persist(path)


def read_json(path: Path):
    materialize(path)
    return json.loads(path.read_text(encoding="utf-8"))


def remove(path: Path) -> None:
    if _remote_enabled():
        name = _object_name(path)
        if name:
            _request("DELETE", f"object/{get_settings().supabase_storage_bucket}", json={"prefixes": [name]})
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # A locked file on Windows must not break the request; the retention
        # sweep will pick it up later.
        pass


def remove_report_files(policy_id: str) -> None:
    if not _SAFE_NAME.fullmatch(policy_id) or ".." in policy_id:
        raise ValueError("Unsafe policy identifier.")
    if _remote_enabled():
        bucket = get_settings().supabase_storage_bucket
        # Repeatedly fetch the first page: deleting it moves the next page forward.
        while True:
            response = _request("POST", f"object/list/{bucket}",
                                json={"prefix": "generated_reports", "search": f"{policy_id}_v", "limit": 100})
            if response.status_code == 404:
                break
            names = [f"generated_reports/{item['name']}" for item in response.json()
                     if item.get("name", "").startswith(f"{policy_id}_v")]
            if not names:
                break
            _request("DELETE", f"object/{bucket}", json={"prefixes": names})
    for path in get_settings().reports_dir.glob(f"{policy_id}_v*"):
        remove(path)
