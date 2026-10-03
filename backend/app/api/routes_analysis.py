"""AI analysis: start, read, edit findings, delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serializers import analysis_summary, get_analysis, get_file, job_dict
from app.core.errors import ConflictError, NotFoundError
from app.core.stages import Stage
from app.database.session import get_db
from app.models.entities import Analysis, AnalysisFinding
from app.schemas.api import AnalyzeRequest, FindingUpdate
from app.services.ai_analysis import engine

router = APIRouter(tags=["analysis"])


def _started(analysis: Analysis, job) -> dict:
    return {"analysis_id": analysis.id, "job": job_dict(job), "analysis": analysis_summary(analysis)}


@router.post("/analyses", status_code=202)
def start_analysis(body: AnalyzeRequest, db: Session = Depends(get_db)):
    """Analyze one or more extracted files together."""
    analysis, job = engine.start_analysis(db, body.file_ids)
    return _started(analysis, job)


@router.post("/analyze/{file_id}", status_code=202)
def analyze_single_file(file_id: str, db: Session = Depends(get_db)):
    """Convenience endpoint: analyze a single file."""
    get_file(db, file_id)
    analysis, job = engine.start_analysis(db, [file_id])
    return _started(analysis, job)


@router.get("/analyses")
def list_analyses(db: Session = Depends(get_db)):
    rows = db.scalars(select(Analysis).order_by(Analysis.created_at.desc()).limit(100)).all()
    return {"analyses": [analysis_summary(a) for a in rows]}


@router.get("/analyses/{analysis_id}")
def read_analysis(analysis_id: str, db: Session = Depends(get_db)):
    analysis = get_analysis(db, analysis_id)
    if analysis.status != Stage.ANALYSIS_COMPLETED.value:
        # Not finished (or failed): return the status only, never partial findings.
        return {**analysis_summary(analysis), "completed": False}
    return {**engine.serialize_analysis(analysis), "status_label": "Analysis Completed", "completed": True}


@router.patch("/analyses/{analysis_id}/findings/{finding_id}")
def update_finding(analysis_id: str, finding_id: int, body: FindingUpdate, db: Session = Depends(get_db)):
    """Edit, exclude or restore one finding before policy generation."""
    analysis = get_analysis(db, analysis_id)
    if analysis.status != Stage.ANALYSIS_COMPLETED.value:
        raise ConflictError("Findings can only be edited once the analysis has completed.", code="analysis_not_ready")
    finding = db.get(AnalysisFinding, finding_id)
    if finding is None or finding.analysis_id != analysis.id:
        raise NotFoundError("The finding was not found.")
    finding = engine.update_finding(db, finding, data=body.data, excluded=body.excluded, restore=body.restore)
    item = {
        "id": finding.id,
        "ref_id": finding.ref_id,
        "kind": finding.kind,
        **finding.data,
        "user_edited": finding.user_edited,
        "excluded": finding.excluded,
    }
    if finding.user_edited:
        item["original"] = finding.original_data
    return item


@router.delete("/analyses/{analysis_id}")
def delete_analysis(analysis_id: str, db: Session = Depends(get_db)):
    analysis = get_analysis(db, analysis_id)
    if analysis.status == Stage.ANALYZING.value:
        raise ConflictError("The analysis is still running. Cancel the job first.", code="analysis_running")
    engine.delete_analysis(db, analysis)
    return {"deleted": True, "id": analysis_id}
