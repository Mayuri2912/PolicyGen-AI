"""The neutral document model shared by the DOCX, PDF and preview renderers."""

from __future__ import annotations

import copy
import re
from datetime import date

from app.models.entities import Analysis, Policy, PolicyVersion, iso
from app.schemas.policy import PolicyConfig
from app.services.document_generation.customer_text import (
    clean_customer_text,
    clean_elements,
)
from app.services.policy_generation import structure
from app.services.policy_generation.templates import get_template

ORIGIN_LABELS = {
    "generated": "Initial version",
    "regenerated": "Sections revised",
    "ai_revision": "Revision",
    "user_edit": "Edited by user",
}
_UNKNOWN = "[To be confirmed: {}]"


def _or_placeholder(value: str, what: str) -> str:
    return value.strip() if value and value.strip() else _UNKNOWN.format(what)


def _format_date(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%d %B %Y")
    except (TypeError, ValueError):
        return _UNKNOWN.format("effective date")


def _status(policy: Policy, version: PolicyVersion) -> str:
    if policy.approved_version == version.version_number:
        event = next(
            (e for e in reversed(policy.review_events) if e.action == "approve" and e.version_number == version.version_number),
            None,
        )
        if event:
            return f"Approved by {event.actor} on {event.created_at.strftime('%d %B %Y')}"
        return "Approved"
    if version.version_number == policy.current_version:
        if policy.review_status == "in_review":
            return "Draft - submitted for review"
        if policy.review_status == "changes_requested":
            return "Draft - changes requested by reviewer"
    return "Draft - not approved"


def _source_text(item: dict) -> str:
    source = item.get("source") or {}
    if not source.get("file_name"):
        return "Inferred - no direct source"
    return f"{source['file_name']}" + (f" ({source['location']})" if source.get("location") else "")


# Column proportions for the tables whose shape is fixed by the template.
_TABLE_WIDTHS = {("abbreviations", 2): [24, 76], ("definitions", 2): [28, 72], ("roles_responsibilities", 3): [24, 50, 26]}


def _with_widths(section_id: str, block: dict) -> dict:
    widths = _TABLE_WIDTHS.get((section_id, len(block.get("columns") or []))) if block.get("type") == "table" else None
    return {**block, "widths": widths} if widths else block


def build_document_model(policy: Policy, version: PolicyVersion, analysis: Analysis) -> dict:
    """Assemble everything a renderer needs for one policy version."""
    config = PolicyConfig.model_validate(version.content.get("_config", policy.config))
    template = get_template(policy.policy_type)
    content = copy.deepcopy(version.content)
    structure.enforce(content, template, organization=config.organization_name)

    organization = _or_placeholder(config.organization_name, "organization name")
    files = policy.source_files or []
    meta = {
        "title": content.get("title") or template.name,
        "policy_type": template.name,
        "policy_code": template.code,
        "organization": organization,
        "department": config.department,
        "classification": config.classification or "Internal",
        "document_version": config.document_version or "1.0",
        "revision": version.version_number,
        "status": _status(policy, version),
        "generated_on": iso(version.created_at),
        "simulated": policy.simulated,
        "source_files": [f["file_name"] for f in files],
    }
    document_control = [
        ["Document title", meta["title"]],
        ["Organization", organization],
        ["Department", _or_placeholder(config.department, "department")],
        ["Policy owner", _or_placeholder(config.policy_owner, "policy owner")],
        ["Approver", _or_placeholder(config.approver, "approver")],
        ["Document version", f"{meta['document_version']} (draft revision {version.version_number})"],
        ["Status", meta["status"]],
        ["Effective date", _format_date(config.effective_date)],
        ["Review frequency", _or_placeholder(config.review_frequency, "review frequency")],
        ["Classification", meta["classification"]],
        ["Source documents", ", ".join(meta["source_files"]) or "-"],
        ["Document ID", template.code],
    ]
    if template.id == "ims_s_15":
        meta["compact_document_control"] = True
        document_control = [
            ["Document ID", template.code], ["Document name", meta["title"]],
            ["Document owner", _or_placeholder(config.policy_owner, "policy owner")],
            ["Review cycle", _or_placeholder(config.review_frequency, "review frequency")],
            ["Classification", meta["classification"]],
        ]
    history = [
        [
            str(v.version_number),
            v.created_at.strftime("%d %b %Y"),
            ORIGIN_LABELS.get(v.origin, v.origin),
            clean_customer_text(v.change_note or "") or ("Initial version" if v.version_number == 1 else "Document revised"),
        ]
        for v in policy.versions
        if v.version_number <= version.version_number
    ]

    # Headings are numbered from the order of the sections, so the numbers and
    # the table of contents always follow the template order.
    sections = []
    for number, section in enumerate(content["sections"], start=1):
        sections.append(
            {
                "id": section["id"],
                "number": str(number),
                "title": section["heading"],
                "kind": "section",
                "origin": section.get("origin", "ai_generated"),
                "elements": clean_elements([_with_widths(section["id"], block) for block in section["blocks"]]),
            }
        )

    if template.id == "ims_s_15":
        # Keep traceability in stored statements and review APIs, outside customer exports.
        def customer_text(value):
            value = re.sub(r"\s*\([^()]*\b(?:REQ|RSK|CTL|GAP)-\d+\b[^()]*\)", "", value)
            return re.sub(r"\b(?:REQ|RSK|CTL|GAP)-\d+\b", "", value).strip()
        for section in sections:
            for block in section["elements"]:
                if "text" in block:
                    block["text"] = customer_text(block["text"])
                if "items" in block:
                    block["items"] = [customer_text(item) for item in block["items"]]
                for statement in block.get("statements", []):
                    statement["id"] = ""
                    statement["text"] = customer_text(statement["text"])
                if block["type"] == "table":
                    keep = [i for i, col in enumerate(block["columns"]) if col.casefold() != "basis"]
                    block["columns"] = [block["columns"][i] for i in keep]
                    block["rows"] = [[customer_text(row[i]) if i < len(row) else "" for i in keep] for row in block["rows"]]
                    block.pop("widths", None)
    return {
        "meta": meta,
        "notices": [],
        "document_control": document_control,
        "version_history": history,
        "sections": sections,
    }


def section_label(section: dict) -> str:
    """Heading text as rendered: '3. Scope' or 'Appendix A - Risk Register'."""
    if section["kind"] == "appendix":
        return f"{section['number']} - {section['title']}"
    return f"{section['number']}. {section['title']}"
