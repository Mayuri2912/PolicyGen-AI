"""Request bodies for the REST API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.policy import PolicyConfig

def _id():
    return Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9]+$")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnalyzeRequest(_Body):
    file_ids: list[str] = Field(min_length=1, max_length=20)


class FindingUpdate(_Body):
    data: dict | None = None
    excluded: bool | None = None
    restore: bool = False


class RecommendRequest(_Body):
    analysis_id: str = _id()


class GenerateRequest(_Body):
    analysis_id: str = _id()
    config: PolicyConfig


class SectionEdit(_Body):
    markup: str = Field(min_length=1, max_length=60_000)
    note: str = Field(default="", max_length=300)
    base_version: int | None = Field(default=None, ge=1)


class RegenerateRequest(_Body):
    section_ids: list[str] = Field(min_length=1, max_length=30)
    instruction: str = Field(default="", max_length=1500)


class ReviewRequest(_Body):
    action: Literal["submit", "approve", "request_changes", "reopen"]
    acknowledged_checks: list[str] = Field(default_factory=list, max_length=50)
    base_version: int | None = Field(default=None, ge=1)
    actor: str = Field(default="", max_length=120)
    comment: str = Field(default="", max_length=2000)


class MissingInformationRequest(_Body):
    base_version: int = Field(ge=1)
    replacements: dict[str, str] = Field(default_factory=dict, max_length=100)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=12)
