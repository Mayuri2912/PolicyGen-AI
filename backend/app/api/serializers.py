"""Shared lookups and JSON shapes for API responses."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.stages import STAGE_LABELS, Stage
from app.models.entities import Analysis, Job, Policy, UploadedFile, iso
from app.services.file_processing.validation import FILE_TYPES


def get_file(db: Session, file_id: str) -> UploadedFile:
    file = db.get(UploadedFile, file_id)
    if file is None:
        raise NotFoundError("The file was not found. It may have been deleted.")
    return file


def get_analysis(db: Session, analysis_id: str) -> Analysis:
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise NotFoundError("The analysis was not found. It may have been deleted.")
    return analysis


def get_policy(db: Session, report_id: str) -> Policy:
    policy = db.get(Policy, report_id)
    if policy is None:
        raise NotFoundError("The report was not found. It may have been deleted.")
    return policy


def get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError("The job was not found.")
    return job


def stage_label(stage: str) -> str:
    try:
        return STAGE_LABELS[Stage(stage)]
    except ValueError:
        return stage


def file_dict(file: UploadedFile) -> dict:
    document = file.document
    file_type = FILE_TYPES.get(file.extension)
    return {
        "id": file.id,
        "file_name": file.original_name,
        "extension": file.extension,
        "type_label": file_type.label if file_type else file.extension.upper(),
        "size_bytes": file.size_bytes,
        "status": file.status,
        "status_label": stage_label(file.status),
        "error_code": file.error_code,
        "error_message": file.error_message,
        "warnings": file.warnings or [],
        "extraction_job_id": file.extraction_job_id,
        "source_deleted": file.source_deleted,
        "created_at": iso(file.created_at),
        "document": {
            "id": document.id,
            "title": document.title,
            "characters": document.char_count,
            "sections": document.section_count,
            "tables": document.table_count,
            "metadata": {k: v for k, v in (document.doc_metadata or {}).items() if k != "source_sha256"},
        }
        if document
        else None,
    }


def job_dict(job: Job) -> dict:
    payload = job.payload or {}
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "stage": job.stage,
        "stage_label": stage_label(job.stage),
        "progress": job.progress,
        "message": job.message,
        "error_message": job.error_message,
        "result": job.result or {},
        "subject_id": payload.get("subject_id"),
        "subject_name": payload.get("file_name"),
        "cancel_requested": job.cancel_requested,
        "attempts": job.attempts,
        "created_at": iso(job.created_at),
        "updated_at": iso(job.updated_at),
        "finished_at": iso(job.finished_at),
    }


def analysis_summary(analysis: Analysis) -> dict:
    counts: dict[str, int] = {}
    for finding in analysis.findings:
        if not finding.excluded:
            counts[finding.kind] = counts.get(finding.kind, 0) + 1
    return {
        "id": analysis.id,
        "job_id": analysis.job_id,
        "status": analysis.status,
        "status_label": stage_label(analysis.status),
        "error_message": analysis.error_message,
        "simulated": analysis.simulated,
        "provider": analysis.provider,
        "model": analysis.model,
        "source_files": [f["file_name"] for f in analysis.source_files or []],
        "counts": counts,
        "report_count": len(analysis.policies),
        "created_at": iso(analysis.created_at),
    }
