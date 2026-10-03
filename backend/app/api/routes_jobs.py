"""Background job status, cancellation and retry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serializers import get_job, job_dict
from app.core.errors import ConflictError
from app.core.stages import JobStatus, TERMINAL_JOB_STATUSES
from app.database.session import get_db
from app.models.entities import Job
from app.services import jobs

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}")
def read_job(job_id: str, db: Session = Depends(get_db)):
    return job_dict(get_job(db, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    """Ask a queued or running job to stop at its next checkpoint."""
    job = get_job(db, job_id)
    if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
        raise ConflictError("The job has already finished.", code="job_finished")
    return job_dict(jobs.request_cancel(db, job))


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str, db: Session = Depends(get_db)):
    """Run a failed or cancelled job again."""
    job = get_job(db, job_id)
    if job.status not in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        raise ConflictError("Only failed or cancelled jobs can be retried.", code="job_not_retryable")
    if job.dedupe_key:
        duplicate = db.scalars(
            select(Job).where(Job.dedupe_key == job.dedupe_key, Job.status.in_(jobs.ACTIVE_STATUSES), Job.id != job.id)
        ).first()
        if duplicate:
            raise ConflictError("The same work is already running.", code="duplicate_job", details={"job_id": duplicate.id})
    jobs.requeue(db, job)
    jobs.runner.submit(job.id)
    db.refresh(job)
    return job_dict(job)


@router.get("/history")
def processing_history(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    """Recent background jobs, newest first."""
    rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)).all()
    return {"jobs": [job_dict(j) for j in rows]}
