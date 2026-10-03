"""Schemas for the AI analysis stage.

Two families live here:

* ``Chunk*`` / ``Consolidation*`` models describe exactly what the LLM must
  return.  Every model response is validated against them before anything
  downstream sees it.
* Vocabulary constants (categories, statuses) shared by the analysis engine,
  the mock provider, the policy generator and the API.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# id -> label, in the order the product presents them.
REQUIREMENT_CATEGORIES: dict[str, str] = {
    "organizational": "Organizational requirements",
    "information_security": "Information security requirements",
    "access_control": "Access control requirements",
    "data_privacy": "Data privacy requirements",
    "vendor_management": "Vendor management requirements",
    "cloud_security": "Cloud security requirements",
    "risk_management": "Risk management requirements",
    "incident_management": "Incident management requirements",
    "business_continuity": "Business continuity requirements",
    "audit_compliance": "Audit and compliance requirements",
    "employee_responsibilities": "Employee responsibilities",
    "technical_operational": "Technical and operational controls",
}

GAP_STATUSES: dict[str, str] = {
    "compliant": "Compliant",
    "partially_addressed": "Partially Addressed",
    "not_evidenced": "Not Evidenced",
    "not_applicable": "Not Applicable",
}

CONTROL_STATUSES: dict[str, str] = {
    "implemented": "Implemented",
    "partially_implemented": "Partially implemented",
    "planned": "Planned",
    "not_implemented": "Not implemented",
    "unknown": "Not stated",
}

PRIORITIES = ("critical", "high", "medium", "low")

ASSESSMENT_DISCLAIMER = (
    "Statuses such as Compliant, Partially Addressed and Not Evidenced are AI-assisted assessments of the "
    "uploaded documents only. They are not a formal audit, legal opinion or compliance certification."
)

Confidence = Literal["high", "medium", "low"]
Priority = Literal["critical", "high", "medium", "low"]


def _slug(value) -> object:
    return value.strip().lower().replace(" ", "_").replace("-", "_") if isinstance(value, str) else value


class LLMModel(BaseModel):
    """Base for LLM-facing models: tolerant of casing, strict about structure."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    enum_fields: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def _normalise_enums(cls, data):
        if isinstance(data, dict):
            for name in cls.enum_fields:
                if name in data:
                    data[name] = _slug(data[name])
        return data


# ----------------------------------------------------------- per-chunk output
class ChunkRequirement(LLMModel):
    enum_fields = ("category", "basis", "confidence")
    local_id: str
    statement: str = Field(min_length=3)
    category: str
    basis: Literal["documented", "inferred"]
    source_location: str = ""
    evidence: str = ""
    confidence: Confidence = "medium"


class ChunkRisk(LLMModel):
    enum_fields = ("origin", "confidence", "scoring_basis")
    local_id: str
    title: str = Field(min_length=3)
    description: str
    category: str
    origin: Literal["documented", "inferred"]
    likelihood: int = Field(ge=0, le=5)
    impact: int = Field(ge=0, le=5)
    scoring_basis: Literal["documented", "estimated"] = "estimated"
    existing_controls: list[str] = Field(default_factory=list)
    recommended_mitigation: str = ""
    residual_likelihood: int = Field(default=0, ge=0, le=5)
    residual_impact: int = Field(default=0, ge=0, le=5)
    source_location: str = ""
    evidence: str = ""
    confidence: Confidence = "medium"


class ChunkControl(LLMModel):
    enum_fields = ("control_type", "control_function", "status", "basis", "confidence")
    local_id: str
    name: str = Field(min_length=3)
    objective: str = ""
    description: str = ""
    control_type: Literal["administrative", "technical", "physical"]
    control_function: Literal["preventive", "detective", "corrective"]
    status: Literal["implemented", "partially_implemented", "planned", "not_implemented", "unknown"]
    basis: Literal["existing", "recommended"]
    owner: str = ""
    related_requirements: list[str] = Field(default_factory=list)
    related_risks: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    source_location: str = ""
    evidence: str = ""
    confidence: Confidence = "medium"


class ChunkResponsibility(LLMModel):
    enum_fields = ("basis",)
    role: str = Field(min_length=2)
    responsibility: str = Field(min_length=3)
    basis: Literal["documented", "proposed"]
    source_location: str = ""
    evidence: str = ""


class ChunkFact(LLMModel):
    """A concrete, checkable statement (a number, frequency, owner, date)."""

    topic: str
    value: str
    source_location: str = ""
    evidence: str = ""


class ChunkFindings(LLMModel):
    document_purpose: str = ""
    document_category: str = ""
    organization_name: str = ""
    stakeholders: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    requirements: list[ChunkRequirement] = Field(default_factory=list)
    risks: list[ChunkRisk] = Field(default_factory=list)
    controls: list[ChunkControl] = Field(default_factory=list)
    responsibilities: list[ChunkResponsibility] = Field(default_factory=list)
    facts: list[ChunkFact] = Field(default_factory=list)


# ------------------------------------------------------- consolidation output
class DocumentSummary(LLMModel):
    file_name: str
    summary: str
    category: str = ""


class AlternativePolicy(LLMModel):
    policy_type: str
    reason: str = ""


class GapItem(LLMModel):
    enum_fields = ("status", "priority")
    requirement_id: str
    requirement: str = ""
    existing_evidence: str = ""
    status: Literal["compliant", "partially_addressed", "not_evidenced", "not_applicable"]
    identified_gap: str = ""
    associated_risk_ids: list[str] = Field(default_factory=list)
    recommended_control_ids: list[str] = Field(default_factory=list)
    recommended_control: str = ""
    priority: Priority = "medium"
    suggested_action: str = ""


class ContradictionSource(LLMModel):
    file_name: str
    detail: str = ""


class Contradiction(LLMModel):
    topic: str
    description: str
    sources: list[ContradictionSource] = Field(default_factory=list)


class MissingInformation(LLMModel):
    item: str
    why_it_matters: str = ""
    suggested_owner: str = ""


class Recommendation(LLMModel):
    enum_fields = ("priority",)
    title: str
    description: str = ""
    priority: Priority = "medium"
    related_ids: list[str] = Field(default_factory=list)


class ConsolidationResult(LLMModel):
    overall_summary: str = Field(min_length=10)
    document_summaries: list[DocumentSummary] = Field(default_factory=list)
    document_category: str = ""
    subject_areas: list[str] = Field(default_factory=list)
    recommended_policy_type: str = ""
    recommendation_rationale: str = ""
    alternative_policy_types: list[AlternativePolicy] = Field(default_factory=list)
    gap_analysis: list[GapItem] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    missing_information: list[MissingInformation] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)


# ------------------------------------------------------------- schema export
_UNSUPPORTED_KEYWORDS = {"default", "title", "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"}


def _clean_schema(node: dict) -> dict:
    cleaned: dict = {}
    for key, value in node.items():
        if key in _UNSUPPORTED_KEYWORDS:
            continue
        if key in ("properties", "$defs"):
            # These map names to schemas; the names themselves (a field may be
            # called "title") must be preserved.
            cleaned[key] = {name: _clean_schema(sub) for name, sub in value.items()}
        elif key == "items" and isinstance(value, dict):
            cleaned[key] = _clean_schema(value)
        elif key in ("anyOf", "allOf", "oneOf"):
            cleaned[key] = [_clean_schema(option) for option in value]
        else:
            cleaned[key] = value
    if cleaned.get("type") == "object" and "properties" in cleaned:
        cleaned["required"] = list(cleaned["properties"])
        cleaned["additionalProperties"] = False
    return cleaned


def llm_json_schema(model: type[BaseModel]) -> dict:
    """JSON Schema for ``model`` in the subset accepted by structured outputs.

    Every property is required and additional properties are forbidden; length
    and range constraints are removed here and enforced by Pydantic instead.
    """
    return _clean_schema(model.model_json_schema())

