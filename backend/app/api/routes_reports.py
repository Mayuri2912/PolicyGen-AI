"""Reports: history, preview, versions, editing, review and download."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serializers import get_policy, job_dict
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.security import sanitize_filename
from app.database.session import get_db
from app.models.entities import Policy, PolicyVersion
from app.schemas.api import RegenerateRequest, ReviewRequest, SectionEdit
from app.services.document_generation.rendering import MEDIA_TYPES, RenderError, ensure_rendered
from app.services.policy_generation import generator
from app.services.policy_generation.versioning import compare_versions

router = APIRouter(prefix="/reports", tags=["reports"])


def _version(policy: Policy, number: int | None) -> PolicyVersion:
    if not policy.versions:
        raise ConflictError("This report has no content yet.", code="no_content")
    if number is None:
        return policy.versions[-1]
    match = next((v for v in policy.versions if v.version_number == number), None)
    if match is None:
        raise NotFoundError(f"Version {number} does not exist for this report.")
    return match


@router.get("")
def list_reports(db: Session = Depends(get_db)):
    rows = db.scalars(select(Policy).order_by(Policy.created_at.desc()).limit(200)).all()
    return {"reports": [generator.policy_summary(p) for p in rows]}


@router.get("/{report_id}")
def read_report(report_id: str, version: int | None = Query(default=None, ge=1), db: Session = Depends(get_db)):
    """A report with the document model of one version (latest by default)."""
    policy = get_policy(db, report_id)
    if not policy.versions:
        return generator.policy_detail(policy)
    return generator.policy_detail(policy, _version(policy, version))


@router.get("/{report_id}/versions")
def list_versions(report_id: str, db: Session = Depends(get_db)):
    policy = get_policy(db, report_id)
    return {"current_version": policy.current_version, "versions": [generator.version_summary(v) for v in policy.versions]}


@router.get("/{report_id}/compare")
def compare(
    report_id: str,
    from_version: int = Query(alias="from", ge=1),
    to_version: int = Query(alias="to", ge=1),
    db: Session = Depends(get_db),
):
    """Section-by-section differences between two versions."""
    policy = get_policy(db, report_id)
    old, new = _version(policy, from_version), _version(policy, to_version)
    return {"from": from_version, "to": to_version, **compare_versions(old.content, new.content)}


@router.put("/{report_id}/sections/{section_id}")
def edit_section(report_id: str, section_id: str, body: SectionEdit, db: Session = Depends(get_db)):
    """Save a user's edit of one section as a new version."""
    policy = get_policy(db, report_id)
    version = generator.save_user_edit(db, policy, section_id, body.markup, body.note, body.base_version)
    db.refresh(policy)
    return {"version": version.version_number, "report": generator.policy_detail(policy)}


@router.post("/{report_id}/sections/regenerate", status_code=202)
def regenerate_sections(report_id: str, body: RegenerateRequest, db: Session = Depends(get_db)):
    """Regenerate selected sections, optionally following an instruction."""
    policy = get_policy(db, report_id)
    job = generator.start_revision(db, policy, body.section_ids, body.instruction)
    return {"job": job_dict(job)}


@router.post("/{report_id}/review")
def review(report_id: str, body: ReviewRequest, db: Session = Depends(get_db)):
    """Record a human review decision: submit, approve, request changes, reopen."""
    policy = get_policy(db, report_id)
    generator.record_review(db, policy, body.action, body.actor, body.comment)
    db.refresh(policy)
    return generator.policy_detail(policy)


@router.get("/{report_id}/download")
def download(
    report_id: str,
    format: str = Query(default="pdf", pattern="^(docx|pdf)$"),
    version: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
):
    policy = get_policy(db, report_id)
    target = _version(policy, version)
    try:
        path = ensure_rendered(policy, target, policy.analysis, format)
    except RenderError as exc:
        raise AppError(str(exc), code="formatting_failed", status_code=500) from exc
    stem = re.sub(r"\s+", "_", sanitize_filename(policy.title or "policy", max_length=80)).strip("._") or "policy"
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[format],
        filename=f"{stem}_v{target.version_number}.{format}",
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/{report_id}")
def delete_report(report_id: str, db: Session = Depends(get_db)):
    """Delete a report with all its versions and rendered documents."""
    policy = get_policy(db, report_id)
    generator.delete_policy(db, policy)
    return {"deleted": True, "id": report_id}
