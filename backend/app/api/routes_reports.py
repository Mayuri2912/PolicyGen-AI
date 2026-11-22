"""Reports: history, preview, versions, editing, review and download."""

from __future__ import annotations

import json
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
from app.schemas.api import (
    MissingInformationRequest,
    RegenerateRequest,
    ReviewRequest,
    SectionEdit,
)
from app.services import storage
from app.services.document_generation.rendering import (
    MEDIA_TYPES,
    RenderError,
    ensure_rendered,
)
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
    db.refresh(policy, with_for_update=True)
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
    from app.api.routes_auth import require_role
    from app.core.identity import principal
    user = principal.get()
    if body.action in ("approve", "request_changes"):
        user = require_role("admin", "reviewer")
    if body.action == "approve" and body.base_version is None:
        raise ConflictError("Reload this version before approving.", code="version_conflict")
    db.refresh(policy, with_for_update=True)
    generator.record_review(db, policy, body.action, (user["name"] if user.get("public_access") else user["name"] + " <" + user["email"] + ">") if user else body.actor,
                            body.comment, body.acknowledged_checks, body.base_version)
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


@router.post("/{report_id}/missing-information")
def complete_missing(report_id: str, body: MissingInformationRequest, db: Session = Depends(get_db)):
    import copy

    from app.core.errors import ValidationFailed
    from app.schemas.policy import PLACEHOLDER_PATTERN, PolicyConfig
    from app.services.policy_generation.templates import get_template
    policy = get_policy(db, report_id)
    db.refresh(policy, with_for_update=True)
    if policy.current_version != body.base_version or not policy.versions:
        raise ConflictError("The policy changed. Reload before completing information.", code="version_conflict")
    latest = policy.versions[-1]
    # Freeze legacy metadata before updating the current configuration.
    latest.content = {**latest.content, "_config": copy.deepcopy(latest.content.get("_config", policy.config))}
    allowed = {"organization_name", "department", "policy_owner", "approver", "effective_date", "review_frequency", "classification"}
    if set(body.metadata) - allowed:
        raise ValidationFailed("Only document-control fields can be updated here.")
    candidate = copy.deepcopy(latest.content)
    available = set(PLACEHOLDER_PATTERN.findall(json.dumps(candidate['sections'])))
    if set(body.replacements) - available:
        raise ValidationFailed("A placeholder changed. Reload before saving.")
    if any(not v.strip() or len(v) > 2000 or PLACEHOLDER_PATTERN.search(v) for v in body.replacements.values()):
        raise ValidationFailed("Enter a confirmed value, up to 2000 characters, for every replacement.")
    def replace(value):
        if isinstance(value, str):
            return PLACEHOLDER_PATTERN.sub(lambda match: body.replacements.get(match.group(0), match.group(0)).strip(), value)
        if isinstance(value, list):
            return [replace(v) for v in value]
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        return value
    candidate['sections'] = replace(candidate['sections'])
    try:
        config = PolicyConfig.model_validate({**policy.config, **body.metadata})
    except ValueError:
        raise ValidationFailed("Document-control values are invalid. Use a valid date and the displayed field limits.") from None
    policy.config = config.model_dump()
    candidate, report = generator._finalise(candidate, get_template(policy.policy_type), policy.analysis, config)
    generator.add_version(db, policy, candidate, report, origin="user_edit", note="Completed confirmed document information.")
    db.commit()
    storage.remove_report_files(policy.id)
    db.refresh(policy)
    return generator.policy_detail(policy)
