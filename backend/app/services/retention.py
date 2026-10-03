"""Data retention: remove expired uploads, analyses, reports and temp files.

With RETENTION_HOURS=0 (the default) nothing expires and data stays until the
user deletes it.  Orphaned temporary files are always removed.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.stages import JobStatus
from app.database.session import session_scope
from app.models.entities import Analysis, Job, ProcessingError, UploadedFile
from app.services import storage
from app.services.ai_analysis.engine import delete_analysis
from app.services.file_processing.service import delete_file

logger = get_logger("retention")

_SWEEP_INTERVAL_SECONDS = 30 * 60
_TEMP_MAX_AGE_SECONDS = 60 * 60
_timer: threading.Timer | None = None
_ACTIVE = (JobStatus.QUEUED.value, JobStatus.RUNNING.value)


def clean_temp_files() -> int:
    """Delete leftover upload fragments and stale analysis caches."""
    settings = get_settings()
    cutoff = time.time() - _TEMP_MAX_AGE_SECONDS
    removed = 0
    for directory, pattern in ((settings.tmp_dir, "*"), (settings.reports_dir, "*.part"), (settings.extracted_dir, "*.part")):
        for path in directory.glob(pattern):
            if path.is_file() and path.stat().st_mtime < cutoff:
                storage.remove(path)
                removed += 1
    return removed


def sweep() -> dict:
    """Run one retention pass and return what was removed."""
    settings = get_settings()
    result = {"temp_files": clean_temp_files(), "files": 0, "analyses": 0}
    if settings.retention_hours <= 0:
        return result
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.retention_hours)
    with session_scope() as db:
        busy = db.scalars(select(Job).where(Job.status.in_(_ACTIVE))).first() is not None
        if busy:
            return result  # never delete data out from under a running job
        for analysis in db.scalars(select(Analysis).where(Analysis.created_at < cutoff)).all():
            delete_analysis(db, analysis)
            result["analyses"] += 1
        for file in db.scalars(select(UploadedFile).where(UploadedFile.created_at < cutoff)).all():
            delete_file(db, file)
            result["files"] += 1
        for model in (Job, ProcessingError):
            for row in db.scalars(select(model).where(model.created_at < cutoff)).all():
                db.delete(row)
    if result["files"] or result["analyses"]:
        logger.info("retention sweep removed expired data", extra=result)
    return result


def _tick() -> None:
    try:
        sweep()
    except Exception as exc:  # noqa: BLE001 - the timer must survive a failed pass
        logger.error("retention sweep failed", exc_info=(type(exc), exc, exc.__traceback__))
    start()


def start() -> None:
    global _timer
    _timer = threading.Timer(_SWEEP_INTERVAL_SECONDS, _tick)
    _timer.daemon = True
    _timer.start()


def stop() -> None:
    if _timer is not None:
        _timer.cancel()
