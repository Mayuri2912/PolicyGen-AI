"""Upload, file status, extracted content and deletion."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serializers import file_dict, get_file, job_dict
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError
from app.core.stages import Stage
from app.database.session import get_db
from app.models.entities import UploadedFile
from app.services.file_processing import service
from app.services.jobs import ACTIVE_STATUSES
from app.models.entities import Job

router = APIRouter(tags=["files"])

_PREVIEW_SECTION_CHARS = 6000
_PREVIEW_TABLE_ROWS = 60


@router.post("/upload")
async def upload_files(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    """Validate and store one or more files; extraction starts in the background.

    Each file gets its own result, so one bad file never blocks the others.
    """
    settings = get_settings()
    if len(files) > settings.max_files_per_upload:
        raise AppError(
            f"Upload at most {settings.max_files_per_upload} files at a time.", code="too_many_files", status_code=400
        )
    results = []
    for upload in files:
        outcome = await service.store_upload(db, upload)
        results.append(
            {
                "file_name": outcome.file_name,
                "accepted": outcome.accepted,
                "duplicate": outcome.duplicate,
                "file": file_dict(outcome.file) if outcome.file else None,
                "error": {"code": outcome.error_code, "message": outcome.error_message} if not outcome.accepted else None,
            }
        )
        await upload.close()
    return {
        "results": results,
        "accepted": sum(1 for r in results if r["accepted"]),
        "rejected": sum(1 for r in results if not r["accepted"]),
    }


@router.get("/files")
def list_files(ids: str | None = Query(default=None, max_length=2000), db: Session = Depends(get_db)):
    query = select(UploadedFile).order_by(UploadedFile.created_at.desc())
    if ids:
        query = query.where(UploadedFile.id.in_([i for i in ids.split(",") if i][:50]))
    else:
        query = query.limit(200)
    return {"files": [file_dict(f) for f in db.scalars(query).all()]}


@router.get("/files/{file_id}")
def get_file_status(file_id: str, db: Session = Depends(get_db)):
    return file_dict(get_file(db, file_id))


@router.get("/files/{file_id}/content")
def get_file_content(file_id: str, db: Session = Depends(get_db)):
    """Extracted content for review before analysis (long text is shortened)."""
    file = get_file(db, file_id)
    if file.status != Stage.EXTRACTED.value or file.document is None:
        raise ConflictError("This file has not been extracted yet.", code="not_extracted")
    document = service.load_document(file)
    return {
        "file": file_dict(file),
        "document_title": document.document_title,
        "characters": len(document.extracted_text),
        "sections": [
            {
                "heading": s.heading,
                "location": s.location,
                "text": s.text[:_PREVIEW_SECTION_CHARS],
                "truncated": len(s.text) > _PREVIEW_SECTION_CHARS,
            }
            for s in document.sections[:300]
        ],
        "tables": [
            {
                "title": t.title,
                "location": t.location,
                "headers": t.headers,
                "rows": t.rows[:_PREVIEW_TABLE_ROWS],
                "row_count": len(t.rows),
                "truncated": len(t.rows) > _PREVIEW_TABLE_ROWS or t.truncated,
            }
            for t in document.tables[:60]
        ],
    }


@router.post("/extract/{file_id}")
def extract_file(file_id: str, db: Session = Depends(get_db)):
    """Run (or re-run) extraction for a stored file."""
    file = get_file(db, file_id)
    if file.source_deleted:
        raise ConflictError("The stored source file was deleted, so it cannot be extracted again.", code="source_deleted")
    job = service.queue_extraction(db, file)
    db.refresh(file)
    return {"job": job_dict(job), "file": file_dict(file)}


@router.delete("/files/{file_id}")
def delete_file(file_id: str, db: Session = Depends(get_db)):
    file = get_file(db, file_id)
    active = db.scalars(
        select(Job).where(Job.status.in_(ACTIVE_STATUSES), Job.type.in_(("extraction", "analysis")))
    ).all()
    if any(file.id == (j.payload or {}).get("subject_id") or file.id in (j.payload or {}).get("file_ids", []) for j in active):
        raise ConflictError("This file is being processed. Wait for it to finish or cancel the job first.", code="file_in_use")
    service.delete_file(db, file)
    return {"deleted": True, "id": file_id}
