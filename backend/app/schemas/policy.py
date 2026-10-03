"""Schemas for policy configuration and structured policy content."""

from __future__ import annotations

import re
from datetime import date
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.analysis import LLMModel

PLACEHOLDER_PATTERN = re.compile(r"\[To be confirmed:[^\]]*\]", re.IGNORECASE)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

WritingStyle = Literal["formal", "technical", "simplified"]
DetailLevel = Literal["concise", "standard", "comprehensive"]
OutputFormat = Literal["docx", "pdf"]

STYLE_GUIDANCE = {
    "formal": "Write in a formal, authoritative policy register suited to an executive and audit readership.",
    "technical": "Write for a technical audience: be precise about systems, configurations and control mechanisms, and include control and finding ids where they help.",
    "simplified": "Write in plain language for a general audience: short sentences, everyday words, and no jargon without an explanation.",
}
DETAIL_GUIDANCE = {
    "concise": "Keep the policy concise: cover every section, but only the most important requirements, in a few sentences or items per section.",
    "standard": "Aim for a standard level of detail: each section complete enough to act on, without exhaustive enumeration.",
    "comprehensive": "Be comprehensive: address every requirement, significant risk and gap from the analysis, with full implementation guidance.",
}


class PolicyConfig(BaseModel):
    """User-controlled settings for one policy.  Empty strings mean "unknown"."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    policy_type: str = Field(min_length=1, max_length=64)
    organization_name: str = Field(default="", max_length=200)
    department: str = Field(default="", max_length=200)
    policy_owner: str = Field(default="", max_length=200)
    approver: str = Field(default="", max_length=200)
    document_version: str = Field(default="1.0", max_length=20)
    effective_date: str = Field(default="", max_length=10)
    review_frequency: str = Field(default="Annually", max_length=60)
    classification: str = Field(default="Internal", max_length=60)
    custom_title: str = Field(default="", max_length=200)
    writing_style: WritingStyle = "formal"
    detail_level: DetailLevel = "standard"
    include_risk_register: bool = True
    include_control_matrix: bool = True
    include_gap_analysis: bool = True
    include_source_references: bool = True
    output_formats: list[OutputFormat] = Field(default_factory=lambda: ["docx", "pdf"], min_length=1)

    @field_validator(
        "organization_name", "department", "policy_owner", "approver", "document_version", "review_frequency",
        "classification", "custom_title",
    )
    @classmethod
    def _no_control_characters(cls, value: str) -> str:
        if _CONTROL_CHARS.search(value) or "\n" in value or "\r" in value:
            raise ValueError("must be a single line of plain text")
        return value

    @field_validator("effective_date")
    @classmethod
    def _valid_date(cls, value: str) -> str:
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("must be a date in YYYY-MM-DD format") from None
        return value

    @field_validator("output_formats")
    @classmethod
    def _unique_formats(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


# ---------------------------------------------------------- policy content
class PolicyStatement(LLMModel):
    enum_fields: ClassVar[tuple[str, ...]] = ("basis",)
    id: str = ""
    text: str = Field(min_length=3)
    basis: Literal["documented", "proposed"] = "proposed"
    refs: list[str] = Field(default_factory=list)


class PolicyBlock(LLMModel):
    enum_fields: ClassVar[tuple[str, ...]] = ("type",)
    type: Literal["paragraph", "subheading", "bullets", "numbered", "statements", "table"]
    text: str = ""
    items: list[str] = Field(default_factory=list)
    statements: list[PolicyStatement] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)

    def is_empty(self) -> bool:
        if self.type in ("paragraph", "subheading"):
            return not self.text.strip()
        if self.type in ("bullets", "numbered"):
            return not any(i.strip() for i in self.items)
        if self.type == "statements":
            return not self.statements
        return not self.columns or not self.rows

    def plain_text(self) -> str:
        if self.type in ("paragraph", "subheading"):
            return self.text
        if self.type in ("bullets", "numbered"):
            return "\n".join(self.items)
        if self.type == "statements":
            return "\n".join(s.text for s in self.statements)
        return "\n".join([" ".join(self.columns), *(" ".join(row) for row in self.rows)])


class PolicySection(LLMModel):
    id: str = Field(min_length=1)
    heading: str = Field(min_length=1)
    blocks: list[PolicyBlock] = Field(default_factory=list)


class PolicyContent(LLMModel):
    title: str = Field(min_length=3)
    sections: list[PolicySection] = Field(min_length=1)


class SectionRevision(LLMModel):
    sections: list[PolicySection] = Field(min_length=1)
