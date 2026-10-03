"""Request-level safeguards: access token check, rate limiting, safe file names."""

from __future__ import annotations

import hmac
import re
import threading
import time
import unicodedata
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import Settings
from app.core.errors import error_body

# Endpoints that start expensive work get the stricter "heavy" budget.
HEAVY_PATH_PREFIXES = ("/api/upload", "/api/analy", "/api/policies/generate", "/api/extract")
_WINDOW_SECONDS = 60.0


class RateLimiter:
    """In-memory sliding-window limiter keyed by client address.

    Suitable for the single-process deployment this app targets.  A multi-node
    deployment should move the counters to a shared store.
    """

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, bucket: str, limit: int) -> tuple[bool, int]:
        if limit <= 0:
            return True, 0
        now = time.monotonic()
        with self._lock:
            hits = self._hits[(key, bucket)]
            while hits and now - hits[0] > _WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                return False, max(1, int(_WINDOW_SECONDS - (now - hits[0])) + 1)
            hits.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class ApiGuardMiddleware(BaseHTTPMiddleware):
    """Applies the optional access token and the rate limits to /api routes."""

    def __init__(self, app, settings: Settings, limiter: RateLimiter):
        super().__init__(app)
        self.settings = settings
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api") or request.method == "OPTIONS":
            return await call_next(request)

        token = self.settings.api_access_token
        if token and path != "/api/health":
            supplied = request.headers.get("authorization", "")
            supplied = supplied[7:] if supplied.lower().startswith("bearer ") else ""
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                return JSONResponse(
                    status_code=401,
                    content=error_body("unauthorized", "A valid access token is required."),
                )

        client = request.client.host if request.client else "unknown"
        heavy = request.method == "POST" and path.startswith(HEAVY_PATH_PREFIXES)
        bucket, limit = (
            ("heavy", self.settings.rate_limit_heavy_per_minute)
            if heavy
            else ("default", self.settings.rate_limit_per_minute)
        )
        allowed, retry_after = self.limiter.allow(client, bucket, limit)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content=error_body("rate_limited", f"Too many requests. Try again in {retry_after} seconds."),
                headers={"Retry-After": str(retry_after)},
            )

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        return response


_UNSAFE_CHARS = re.compile(r"[^\w\-. ()\[\]]+", re.UNICODE)
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def sanitize_filename(name: str, max_length: int = 150) -> str:
    """Return a display-safe file name with no path components.

    The result is only ever used for display and for Content-Disposition
    headers; files are stored on disk under generated identifiers, so a hostile
    name can never influence a storage path.
    """
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    if not name:
        name = "file"
    stem, dot, ext = name.rpartition(".")
    if dot and stem.lower() in _WINDOWS_RESERVED:
        name = f"_{stem}.{ext}"
    if len(name) > max_length:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: max_length - len(ext) - 1] + "." + ext) if dot else name[:max_length]
    return name
