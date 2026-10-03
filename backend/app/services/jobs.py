"""Background job runner.

Long-running work (extraction, AI analysis, policy generation, rendering) runs
on a small thread pool so HTTP requests return immediately and the UI can poll
``GET /api/jobs/{id}`` for the real stage, progress and outcome.

Job state is persisted, which gives three guarantees:

* a job is only ever marked ``completed`` after its handler returned normally;
* jobs that were queued or running when the process stopped are detected on
  the next start and marked failed (retryable) instead of hanging forever;
* an identical request made while a job is active returns that job instead of
  starting a duplicate.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.stages import JobStatus, Stage, TERMINAL_JOB_STATUSES, advance
from app.database.session import session_scope
from app.models.entities import Analysis, Job, Policy, ProcessingError, UploadedFile, utcnow

logger = get_logger("jobs")

ACTIVE_STATUSES = (JobStatus.QUEUED.value, JobStatus.RUNNING.value)
RESTART_MESSAGE = "Processing was interrupted by an application restart. Retry to run it again."


class JobCancelled(Exception):
    """Raised inside a handler when the user asked to cancel the job."""


class JobFailure(Exception):
    """A failure with a message that is safe to show to the user."""

    def __init__(self, message: str, code: str = "processing_failed"):
        super().__init__(message)
        self.message = message
        self.code = code


class JobContext:
    """Handle given to a job handler for reporting progress and cancellation."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        with session_scope() as db:
            job = db.get(Job, job_id)
            self.payload: dict = dict(job.payload or {})
            self.attempt: int = job.attempts

    def update(self, *, stage: Stage | None = None, progress: int | None = None, message: str | None = None) -> None:
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            if job.cancel_requested:
                raise JobCancelled()
            if stage is not None:
                job.stage = advance(job.stage, stage).value
            if progress is not None:
                job.progress = max(0, min(100, int(progress)))
            if message is not None:
                job.message = message

    def check_cancelled(self) -> None:
        with session_scope() as db:
            if db.get(Job, self.job_id).cancel_requested:
                raise JobCancelled()

    def merge_result(self, **values) -> None:
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            job.result = {**(job.result or {}), **values}


Handler = Callable[[JobContext], dict | None]


class JobRunner:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._on_failure: dict[str, Callable[[Session, Job, str, str], None]] = {}
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    def register(self, job_type: str, handler: Handler, on_failure=None) -> None:
        self._handlers[job_type] = handler
        if on_failure:
            self._on_failure[job_type] = on_failure

    def _pool(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=max(1, get_settings().job_workers), thread_name_prefix="policygen-job"
                )
            return self._executor

    def submit(self, job_id: str) -> None:
        if get_settings().job_execution_mode == "inline":
            self._run(job_id)
        else:
            self._pool().submit(self._run, job_id)

    def shutdown(self) -> None:
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

    # ------------------------------------------------------------------ run
    def _run(self, job_id: str) -> None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None or job.status != JobStatus.QUEUED.value:
                return
            if job.cancel_requested:
                self._finish(db, job, JobStatus.CANCELLED, "Cancelled before it started.")
                return
            job.status = JobStatus.RUNNING.value
            job.attempts += 1
            job.error_message = None
            job_type = job.type

        handler = self._handlers.get(job_type)
        try:
            if handler is None:
                raise JobFailure(f"No handler is registered for job type '{job_type}'.")
            result = handler(JobContext(job_id)) or {}
        except JobCancelled:
            self._fail(job_id, JobStatus.CANCELLED, "Cancelled by the user.", "cancelled")
        except (JobFailure, AppError) as exc:
            logger.warning("job failed", extra={"job_id": job_id, "job_type": job_type, "code": exc.code})
            self._fail(job_id, JobStatus.FAILED, exc.message, exc.code)
        except Exception as exc:  # noqa: BLE001 - last line of defence for a worker
            logger.error(
                "job crashed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"job_id": job_id, "job_type": job_type},
            )
            self._fail(
                job_id,
                JobStatus.FAILED,
                "An unexpected error occurred while processing. The details were written to the server log.",
                "internal_error",
            )
        else:
            with session_scope() as db:
                job = db.get(Job, job_id)
                job.result = {**(job.result or {}), **result}
                job.progress = 100
                self._finish(db, job, JobStatus.COMPLETED, job.message or "Completed.")
            logger.info("job completed", extra={"job_id": job_id, "job_type": job_type})

    def _fail(self, job_id: str, status: JobStatus, message: str, code: str) -> None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            failed_stage = job.stage
            # Kept so the UI can show exactly which stage the work stopped at.
            job.result = {**(job.result or {}), "failed_stage": failed_stage}
            job.stage = (Stage.CANCELLED if status == JobStatus.CANCELLED else Stage.FAILED).value
            job.error_message = message
            self._finish(db, job, status, message)
            db.add(
                ProcessingError(
                    job_id=job.id,
                    subject_type=job.type,
                    subject_id=(job.payload or {}).get("subject_id"),
                    stage=failed_stage,
                    code=code,
                    message=message,
                )
            )
            callback = self._on_failure.get(job.type)
            if callback:
                callback(db, job, message, code)

    @staticmethod
    def _finish(db: Session, job: Job, status: JobStatus, message: str) -> None:
        job.status = status.value
        job.message = message
        job.finished_at = utcnow()


runner = JobRunner()


# ---------------------------------------------------------------- helpers
def create_job(
    db: Session,
    job_type: str,
    *,
    stage: Stage,
    payload: dict,
    dedupe_key: str | None = None,
    message: str = "Queued.",
) -> tuple[Job, bool]:
    """Create a queued job, or return the active job with the same dedupe key.

    Returns ``(job, created)``.
    """
    if dedupe_key:
        existing = db.scalars(
            select(Job).where(Job.dedupe_key == dedupe_key, Job.status.in_(ACTIVE_STATUSES))
        ).first()
        if existing:
            return existing, False
    job = Job(type=job_type, stage=stage.value, payload=payload, dedupe_key=dedupe_key, message=message)
    db.add(job)
    db.commit()
    return job, True


def request_cancel(db: Session, job: Job) -> Job:
    if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
        return job
    job.cancel_requested = True
    job.message = "Cancellation requested."
    db.commit()
    return job


def requeue(db: Session, job: Job) -> Job:
    job.status = JobStatus.QUEUED.value
    job.cancel_requested = False
    job.error_message = None
    job.progress = 0
    job.finished_at = None
    job.message = "Queued for retry."
    db.commit()
    return job


def recover_interrupted_jobs() -> int:
    """Mark work that was in flight during a restart as failed and retryable."""
    with session_scope() as db:
        jobs = db.scalars(select(Job).where(Job.status.in_(ACTIVE_STATUSES))).all()
        for job in jobs:
            job.status = JobStatus.FAILED.value
            job.stage = Stage.FAILED.value
            job.error_message = RESTART_MESSAGE
            job.message = RESTART_MESSAGE
            job.finished_at = utcnow()
            db.add(ProcessingError(job_id=job.id, subject_type=job.type, stage="restart", code="interrupted", message=RESTART_MESSAGE))

        in_flight_file = (Stage.VALIDATING.value, Stage.EXTRACTING.value)
        for file in db.scalars(select(UploadedFile).where(UploadedFile.status.in_(in_flight_file))).all():
            file.status = Stage.FAILED.value
            file.error_code = "interrupted"
            file.error_message = RESTART_MESSAGE
        for analysis in db.scalars(select(Analysis).where(Analysis.status == Stage.ANALYZING.value)).all():
            analysis.status = Stage.FAILED.value
            analysis.error_message = RESTART_MESSAGE
        in_flight_policy = (Stage.GENERATING_POLICY.value, Stage.FORMATTING_DOCUMENT.value)
        for policy in db.scalars(select(Policy).where(Policy.status.in_(in_flight_policy))).all():
            policy.status = Stage.FAILED.value
            policy.error_message = RESTART_MESSAGE
        count = len(jobs)
    if count:
        logger.warning("recovered interrupted jobs", extra={"count": count})
    return count
