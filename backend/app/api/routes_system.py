"""Health, reference data and the dashboard summary."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import __version__
from app.api.serializers import analysis_summary, job_dict
from app.core.config import get_settings
from app.core.stages import STAGE_LABELS, JobStatus, Stage
from app.database.session import get_db
from app.models.entities import Analysis, Job, Policy, UploadedFile
from app.schemas.analysis import ASSESSMENT_DISCLAIMER, CONTROL_STATUSES, GAP_STATUSES, PRIORITIES, REQUIREMENT_CATEGORIES
from app.schemas.policy import DETAIL_GUIDANCE, STYLE_GUIDANCE
from app.services.ai_analysis.providers.factory import PROVIDER_LABELS, provider_status
from app.services.document_generation.model import REVIEW_NOTICE
from app.services.file_processing.ocr import ocr_status
from app.services.file_processing.validation import FILE_TYPES
from app.services.policy_generation import generator
from app.services.policy_generation.templates import list_policy_types
from app.services.risk_analysis.scoring import scoring_method

router = APIRouter(tags=["system"])


def _privacy(llm: dict, ocr: dict) -> dict:
    settings = get_settings()
    return {
        "storage": "Uploaded files, extracted text, findings and generated documents are stored on the server running this "
        "application, in a data directory that is not publicly served.",
        "llm": (
            "Mock mode is active: no document content leaves this server."
            if llm.get("simulated")
            else f"Extracted document text and analysis findings are sent to the {llm.get('service_name') or 'configured AI provider'} "
            "to produce the analysis and the policy."
        ),
        "ocr": (
            "Images and scanned pages are sent to the Anthropic API for text recognition."
            if ocr.get("external")
            else "OCR, when available, runs locally on this server."
        ),
        "retention": (
            f"Data is deleted automatically after {settings.retention_hours} hours."
            if settings.retention_hours > 0
            else "Data is kept until you delete it. Deleting a file, analysis or report also removes its stored files."
        ),
        "source_files": (
            "Uploaded source files are deleted as soon as their content has been extracted."
            if settings.delete_source_after_extraction
            else "Uploaded source files are kept so they can be re-extracted, until deleted."
        ),
        "logs": "Application logs record identifiers, sizes and stages only, never document content.",
    }


@router.get("/health")
def health(db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001 - report, do not raise, from a health check
        database = "unavailable"
    llm, ocr = provider_status(), ocr_status()
    return {
        "status": "ok" if database == "ok" and llm["ready"] else "degraded",
        "app": settings.app_name,
        "version": __version__,
        "database": database,
        "llm": llm,
        "ocr": ocr,
        "auth_required": bool(settings.api_access_token),
        "privacy": _privacy(llm, ocr),
    }


@router.get("/config")
def reference_data():
    """Static reference data the frontend needs to render forms and labels."""
    settings = get_settings()
    return {
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_files_per_upload": settings.max_files_per_upload,
        },
        "file_types": [{"extension": t.extension, "label": t.label, "ocr": t.kind == "image"} for t in FILE_TYPES.values()],
        "policy_types": list_policy_types(),
        "llm_providers": PROVIDER_LABELS,
        "writing_styles": [{"id": k, "description": v} for k, v in STYLE_GUIDANCE.items()],
        "detail_levels": [{"id": k, "description": v} for k, v in DETAIL_GUIDANCE.items()],
        "requirement_categories": REQUIREMENT_CATEGORIES,
        "gap_statuses": GAP_STATUSES,
        "control_statuses": CONTROL_STATUSES,
        "priorities": list(PRIORITIES),
        "stages": {stage.value: label for stage, label in STAGE_LABELS.items()},
        "scoring_method": scoring_method(),
        "assessment_disclaimer": ASSESSMENT_DISCLAIMER,
        "review_notice": REVIEW_NOTICE,
    }


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    def count(model, *conditions) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0

    reports = db.scalars(select(Policy).order_by(Policy.created_at.desc()).limit(5)).all()
    analyses = db.scalars(select(Analysis).order_by(Analysis.created_at.desc()).limit(5)).all()
    recent_jobs = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(8)).all()
    active = (JobStatus.QUEUED.value, JobStatus.RUNNING.value)
    return {
        "stats": {
            "files_uploaded": count(UploadedFile),
            "files_processed": count(UploadedFile, UploadedFile.status == Stage.EXTRACTED.value),
            "analyses_completed": count(Analysis, Analysis.status == Stage.ANALYSIS_COMPLETED.value),
            "policies_generated": count(Policy, Policy.status == Stage.COMPLETED.value),
            "policies_approved": count(Policy, Policy.review_status == "approved"),
            "jobs_active": count(Job, Job.status.in_(active)),
            "jobs_failed": count(Job, Job.status == JobStatus.FAILED.value),
        },
        "recent_reports": [generator.policy_summary(p) for p in reports],
        "recent_analyses": [analysis_summary(a) for a in analyses],
        "recent_jobs": [job_dict(j) for j in recent_jobs],
    }
