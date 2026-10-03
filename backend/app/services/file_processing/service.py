"""Upload intake and the extraction job."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import sanitize_filename
from app.core.stages import JobStatus, Stage
from app.database.session import session_scope
from app.models.entities import ExtractedDocument, Job, UploadedFile, new_id
from app.schemas.documents import NormalizedDocument
from app.services import storage
from app.services.file_processing import parsers  # noqa: F401  (registers the parsers)
from app.services.file_processing.base import ParseContext, ParseResult, ParserError, get_parser
from app.services.file_processing.validation import (
    FILE_TYPES,
    UploadRejected,
    check_declared_mime,
    check_extension,
    check_signature,
)
from app.services.jobs import JobContext, JobFailure, create_job, runner

logger = get_logger("files")

_CHUNK = 1024 * 1024
EXTRACTION_JOB = "extraction"


@dataclass
class UploadOutcome:
    file_name: str
    accepted: bool
    duplicate: bool = False
    file: UploadedFile | None = None
    error_code: str | None = None
    error_message: str | None = None


# ------------------------------------------------------------------ intake
async def store_upload(db: Session, upload: UploadFile) -> UploadOutcome:
    """Validate one upload, store it under a generated name and queue extraction."""
    settings = get_settings()
    display_name = sanitize_filename(upload.filename or "")
    try:
        file_type = check_extension(display_name)
        check_declared_mime(file_type, upload.content_type or "")
    except UploadRejected as exc:
        return UploadOutcome(display_name, False, error_code=exc.code, error_message=exc.message)

    file_id = new_id()
    storage_name = f"{file_id}.{file_type.extension}"
    tmp_path = settings.tmp_dir / f"{storage_name}.upload"
    digest, size = hashlib.sha256(), 0
    try:
        with tmp_path.open("wb") as handle:
            while chunk := await upload.read(_CHUNK):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise UploadRejected(
                        "too_large", f"The file is larger than the {settings.max_upload_mb} MB limit."
                    )
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise UploadRejected("empty_file", "The file is empty.")
        check_signature(tmp_path, file_type, settings)
    except UploadRejected as exc:
        storage.remove(tmp_path)
        logger.info("upload rejected", extra={"code": exc.code, "extension": file_type.extension, "size": size})
        return UploadOutcome(display_name, False, error_code=exc.code, error_message=exc.message)

    sha256 = digest.hexdigest()
    existing = db.scalars(select(UploadedFile).where(UploadedFile.sha256 == sha256)).first()
    if existing is not None:
        # Same bytes as an earlier upload: reuse the stored copy instead of
        # keeping two, and retry its extraction if that had failed.
        storage.remove(tmp_path)
        if existing.status == Stage.FAILED.value and not existing.source_deleted:
            queue_extraction(db, existing)
            db.refresh(existing)
        return UploadOutcome(display_name, True, duplicate=True, file=existing)

    tmp_path.replace(storage.upload_path(storage_name))
    record = UploadedFile(
        id=file_id,
        original_name=display_name,
        extension=file_type.extension,
        declared_mime=(upload.content_type or "")[:120],
        size_bytes=size,
        sha256=sha256,
        storage_name=storage_name,
        status=Stage.UPLOADED.value,
    )
    db.add(record)
    db.commit()
    logger.info("upload stored", extra={"file_id": file_id, "extension": file_type.extension, "size": size})
    queue_extraction(db, record)
    db.refresh(record)  # the job may already have advanced the status
    return UploadOutcome(display_name, True, file=record)


def queue_extraction(db: Session, file: UploadedFile) -> Job:
    job, created = create_job(
        db,
        EXTRACTION_JOB,
        stage=Stage.UPLOADED,
        payload={"subject_id": file.id, "file_name": file.original_name},
        dedupe_key=f"extract:{file.id}",
        message="Waiting to be processed.",
    )
    if created:
        file.status = Stage.UPLOADED.value
        file.error_code = None
        file.error_message = None
        file.extraction_job_id = job.id
        db.commit()
        runner.submit(job.id)
    return job


# -------------------------------------------------------------- extraction
def _parse_with_timeout(parser, path, ctx: ParseContext, timeout: int) -> ParseResult:
    """Run the parser on a daemon thread and stop waiting after ``timeout``.

    Python cannot kill a thread, so a runaway parser keeps its thread until it
    returns; the hard limits (pages, rows, characters, archive size) bound how
    long that can be.  The job itself fails promptly.
    """
    outcome: dict = {}

    def target() -> None:
        try:
            outcome["result"] = parser.parse(path, ctx)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True, name="policygen-parse")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise ParserError("timeout", f"Extraction took longer than {timeout} seconds and was stopped.")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def normalize(file: UploadedFile, document_id: str, result: ParseResult) -> NormalizedDocument:
    """Fold a parser result into the common document representation."""
    settings = get_settings()
    warnings = list(result.warnings)
    sections, tables, parts, used = [], [], [], 0
    limit = settings.max_extracted_chars

    for section in result.sections:
        block = f"{section.heading}\n{section.text}".strip() if section.heading else section.text.strip()
        if used + len(block) > limit:
            warnings.append(
                f"The document exceeds the {limit:,} character processing limit; content after "
                f"'{section.heading or section.location}' was not included."
            )
            break
        sections.append(section)
        parts.append(block)
        used += len(block)
    else:
        for table in result.tables:
            block = table.as_text()
            if used + len(block) > limit:
                warnings.append(f"Table '{table.title or table.table_id}' and later tables exceeded the processing limit.")
                break
            tables.append(table)
            parts.append(block)
            used += len(block)

    text = "\n\n".join(p for p in parts if p)
    title = (result.title or "").strip() or file.original_name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
    metadata = {
        "page_count": None,
        "sheet_names": [],
        **result.metadata,
        "upload_timestamp": (file.created_at or datetime.now(timezone.utc)).isoformat(),
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
        "extraction_status": "completed",
        "source_sha256": file.sha256,
        "size_bytes": file.size_bytes,
        "warnings": warnings,
    }
    return NormalizedDocument(
        document_id=document_id,
        file_id=file.id,
        file_name=file.original_name,
        file_type=file.extension,
        document_title=title[:300],
        extracted_text=text,
        sections=sections,
        tables=tables,
        metadata=metadata,
    )


def run_extraction(ctx: JobContext) -> dict:
    settings = get_settings()
    file_id = ctx.payload["subject_id"]

    ctx.update(stage=Stage.VALIDATING, progress=10, message="Validating file.")
    with session_scope() as db:
        file = db.get(UploadedFile, file_id)
        if file is None:
            raise JobFailure("The file no longer exists.", "not_found")
        if file.source_deleted:
            raise JobFailure("The stored source file was deleted and cannot be extracted again.", "source_deleted")
        file.status = Stage.VALIDATING.value
        extension, storage_name, name = file.extension, file.storage_name, file.original_name

    path = storage.upload_path(storage_name)
    if not path.exists():
        raise JobFailure("The stored file is missing. Upload it again.", "missing_file")
    parser = get_parser(extension)
    if parser is None:
        raise JobFailure(f"No parser is available for .{extension} files.", "unsupported")
    try:
        check_signature(path, FILE_TYPES[extension], settings)
    except UploadRejected as exc:
        raise JobFailure(exc.message, exc.code) from exc

    ctx.update(stage=Stage.EXTRACTING, progress=35, message="Extracting content.")
    with session_scope() as db:
        db.get(UploadedFile, file_id).status = Stage.EXTRACTING.value
    try:
        result = _parse_with_timeout(
            parser, path, ParseContext(settings=settings, file_name=name), settings.extraction_timeout_seconds
        )
    except ParserError as exc:
        raise JobFailure(exc.message, exc.code) from exc

    ctx.update(progress=80, message="Normalizing extracted content.")
    with session_scope() as db:
        file = db.get(UploadedFile, file_id)
        document_id = file.document.id if file.document else new_id()
        document = normalize(file, document_id, result)
        if not document.extracted_text.strip():
            raise JobFailure("No readable content was found in the file.", "empty")
        content_name = f"{document_id}.json"
        storage.write_json(storage.extracted_path(content_name), document.model_dump())
        if file.document is None:
            file.document = ExtractedDocument(id=document_id, file_type=extension, content_name=content_name)
        record = file.document
        record.title = document.document_title
        record.char_count = len(document.extracted_text)
        record.section_count = len(document.sections)
        record.table_count = len(document.tables)
        record.doc_metadata = document.metadata
        file.warnings = document.metadata["warnings"]
        file.status = Stage.EXTRACTED.value
        file.error_code = None
        file.error_message = None
        if settings.delete_source_after_extraction:
            storage.remove(path)
            file.source_deleted = True
        stats = {"characters": record.char_count, "sections": record.section_count, "tables": record.table_count}

    ctx.update(stage=Stage.EXTRACTED, progress=100, message="Extraction completed.")
    logger.info("extraction completed", extra={"file_id": file_id, "extension": extension, **stats})
    return {"file_id": file_id, "document_id": document_id, **stats}


def on_extraction_failed(db: Session, job: Job, message: str, code: str) -> None:
    file = db.get(UploadedFile, (job.payload or {}).get("subject_id"))
    if file is not None:
        file.status = Stage.FAILED.value if job.status == JobStatus.FAILED.value else Stage.UPLOADED.value
        file.error_code = code
        file.error_message = message


runner.register(EXTRACTION_JOB, run_extraction, on_extraction_failed)


# ------------------------------------------------------------------ access
def load_document(file: UploadedFile) -> NormalizedDocument:
    if file.document is None:
        raise JobFailure(f"'{file.original_name}' has not been extracted yet.", "not_extracted")
    return NormalizedDocument.model_validate(storage.read_json(storage.extracted_path(file.document.content_name)))


def delete_file(db: Session, file: UploadedFile) -> None:
    """Remove a file's stored bytes, extracted content and database records."""
    storage.remove(storage.upload_path(file.storage_name))
    if file.document is not None:
        storage.remove(storage.extracted_path(file.document.content_name))
    db.delete(file)
    db.commit()
    logger.info("file deleted", extra={"file_id": file.id})
