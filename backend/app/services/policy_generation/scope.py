"""Select evidence for a document without turning an umbrella assessment into every policy."""
from __future__ import annotations

import copy
import re

from app.services.policy_generation.templates import PolicyTemplate


PROJECT_CONTEXT = re.compile(r"\b(?:customer|client)[ -](?:managed|owned|controlled|hosted|sites?|projects?|environments?|premises)\b|\b(?:customer|client)\b.{0,60}\b(?:projects?|contractual|delivery|continuity|recovery|configuration)\b", re.IGNORECASE)


def relevant(item: dict, template: PolicyTemplate) -> bool:
    if template.broad:
        return True
    if item.get("category") in template.categories:
        return True
    # Match finding content rather than source filename/evidence: a workbook's
    # title can mention vendors while an individual row concerns internal HR.
    text = " ".join(str(v) for k, v in item.items() if k not in (
        "source", "additional_sources", "ref_id", "id", "category", "original",
    ) and isinstance(v, (str, list))).lower()
    if template.customer_project_sections and PROJECT_CONTEXT.search(text):
        return True
    return any(re.search(r"(?<!\w)" + re.escape(k.lower()), text) for k in template.keywords)


def scoped_context(context: dict, template: PolicyTemplate) -> dict:
    result = copy.deepcopy(context)
    if template.broad:
        return result
    for kind in ("requirements", "risks"):
        result[kind] = [i for i in context.get(kind, []) if relevant(i, template)]
    selected = {i["ref_id"] for kind in ("requirements", "risks") for i in result[kind]}
    result["controls"] = [i for i in context.get("controls", []) if relevant(i, template) or
                          selected.intersection(i.get("related_requirements", []) + i.get("related_risks", []))]
    selected.update(i["ref_id"] for i in result["controls"])
    result["gaps"] = [i for i in context.get("gaps", []) if i.get("requirement_id") in selected]
    selected.update(i["ref_id"] for i in result["gaps"])
    for kind in ("responsibilities", "recommendations"):
        result[kind] = [i for i in context.get(kind, []) if relevant(i, template) or selected.intersection(i.get("related_ids", []))]
    if template.id in ("access_control", "ims_s_05"):
        result["responsibilities"] = [i for i in context.get("responsibilities", []) if
                                      re.search(r"\b(access|privilege|account|identity|permission)", str(i.get("responsibility", "")), re.IGNORECASE)]
    for kind in ("contradictions", "missing_information", "facts"):
        result[kind] = [i for i in context.get(kind, []) if relevant(i, template)]
    result["assumptions"] = [s for s in context.get("assumptions", []) if relevant({"text": s}, template)]
    # Keep organization identity, but do not prime the writer with an unrelated
    # global summary or list of internal systems and stakeholders.
    result["summary"] = {"organization_name": context.get("summary", {}).get("organization_name"),
                         "document_category": template.management_system or template.name,
                         "subject_areas": [t["title"] for t in template.topics],
                         "overall_summary": f"Evidence selected for {template.name}.",
                         "stakeholders": [], "systems": []}
    if template.customer_project_sections:
        summary_text = " ".join(str(context.get("summary", {}).get(k) or "") for k in ("overall_summary", "systems", "stakeholders"))
        result["project_delivery_context"] = [sentence for sentence in re.split(r"(?<=[.!?])\s+|\n", summary_text) if PROJECT_CONTEXT.search(sentence)]
    result["scope_note"] = f"Write only {template.name}. Unrelated findings remain available in the application."
    return result
