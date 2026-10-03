"""Policy types, recommendation and generation."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.serializers import get_analysis, job_dict
from app.core.errors import ConflictError
from app.core.stages import Stage
from app.database.session import get_db
from app.schemas.api import GenerateRequest, RecommendRequest
from app.services.ai_analysis.engine import findings_by_kind
from app.services.policy_generation import generator
from app.services.policy_generation.recommender import recommend
from app.services.policy_generation.templates import list_policy_types

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("/types")
def policy_types():
    return {"policy_types": list_policy_types()}


@router.post("/recommend")
def recommend_policy(body: RecommendRequest, db: Session = Depends(get_db)):
    """Rank policy types for an analysis, with the evidence behind each."""
    analysis = get_analysis(db, body.analysis_id)
    if analysis.status != Stage.ANALYSIS_COMPLETED.value:
        raise ConflictError("The analysis has not completed yet.", code="analysis_not_ready")
    ranked = recommend(findings_by_kind(analysis, include_excluded=False), analysis.summary or {}, simulated=analysis.simulated)
    return {
        "analysis_id": analysis.id,
        "recommended": ranked[0] if ranked else None,
        "recommendations": ranked,
        "detected_organization_name": (analysis.summary or {}).get("organization_name", ""),
        "simulated": analysis.simulated,
    }


@router.post("/generate", status_code=202)
def generate_policy(body: GenerateRequest, db: Session = Depends(get_db)):
    """Start generating a policy from a completed analysis."""
    analysis = get_analysis(db, body.analysis_id)
    policy, job = generator.start_generation(db, analysis, body.config)
    return {"report_id": policy.id, "job": job_dict(job), "report": generator.policy_summary(policy)}
