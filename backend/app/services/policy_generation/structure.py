"""Rules about a policy's structure that hold on every path.

``enforce`` runs whenever content is about to become a version: after the
initial draft, after a quality correction, after an AI revision or a
regeneration, and after a manual edit.  It makes the content follow the
section template whatever the model returned or the user typed:

* retired sections (the Executive Summary) are left out, including when a
  policy saved by an earlier version of the application gets a new version;
* sections are in template order, so Abbreviations sits immediately before
  Scope and the numbering follows from the order;
* the Abbreviations section lists exactly the abbreviations the policy uses;
* role references name roles, departments and teams, never individuals.

Stored versions are never modified: callers pass a copy.
"""

from __future__ import annotations

from copy import deepcopy

from app.schemas.policy import PolicyBlock
from app.services.policy_generation import abbreviations, people
from app.services.policy_generation.content import clean_blocks
from app.services.policy_generation.templates import (
    PolicyTemplate,
    is_retired,
    section_library,
)


def _note(code: str, message: str, section_id: str | None = None) -> dict:
    return {"code": code, "severity": "info", "section_id": section_id, "message": message}


def order_sections(sections: list[dict], template: PolicyTemplate) -> list[dict]:
    """Template sections in template order, then any others in the order given."""
    order = [spec.id for spec in template.sections()]
    by_id = {section["id"]: section for section in sections}
    return [by_id[i] for i in order if i in by_id] + [s for s in sections if s["id"] not in order]


def enforce(content: dict, template: PolicyTemplate, index: people.PeopleIndex | None = None, *, organization: str = "") -> list[dict]:
    """Apply the structure rules to ``content`` in place.  Returns notes for the quality report."""
    notes: list[dict] = []
    index = index or people.PeopleIndex()
    for section in content["sections"]:
        section["blocks"] = clean_blocks([PolicyBlock.model_validate(block) for block in section["blocks"]])

    # Also migrate saved content on revision/export without rewriting its
    # immutable stored version. Keep both sets of substantive statements.
    combined = next((s for s in content["sections"] if s["id"] == "objectives"), None)
    for section in list(content["sections"]):
        if section["id"] == "principles":
            if combined is None:
                section["id"] = "objectives"
                combined = section
            else:
                combined["blocks"].extend(section["blocks"])
                content["sections"].remove(section)
        if section["id"].startswith(("appendix", "annex")) or section.get("heading", "").lower().startswith(("appendix", "annex")):
            content["sections"].remove(section)

    retired = [s for s in content["sections"] if is_retired(s["id"], s.get("heading", ""))]
    if retired:
        content["sections"] = [s for s in content["sections"] if s not in retired]
        notes.append(
            _note(
                "retired_section_removed",
                f"{', '.join(s['heading'] for s in retired)} is no longer part of the policy structure and is left out of this "
                "version. Earlier versions are unchanged.",
            )
        )

    if template.id == "ims_s_15":
        # Preserve and explain used abbreviations in the single Definitions table.
        temporary = deepcopy(content)
        if not any(s["id"] == "abbreviations" for s in temporary["sections"]):
            temporary["sections"].append({"id": "abbreviations", "heading": "Abbreviations", "blocks": []})
        abbreviations.rebuild(temporary, "Abbreviations", ignore={template.code})
        short_forms = next(s for s in temporary["sections"] if s["id"] == "abbreviations")
        rows = [row for b in short_forms["blocks"] if b["type"] == "table" for row in b["rows"]]
        definitions = next((s for s in content["sections"] if s["id"] == "definitions"), None)
        if rows and definitions is not None:
            table = next((b for b in definitions["blocks"] if b["type"] == "table" and len(b["columns"]) == 2), None)
            if table is None:
                table = PolicyBlock(type="table", columns=["Term", "Definition"], rows=[]).model_dump()
                definitions["blocks"] = [b for b in definitions["blocks"] if b.get("text", "") != "No specialist terms are used in this policy."]
                definitions["blocks"].append(table)
            terms = {r[0].casefold() for r in table["rows"] if r}
            for row in rows:
                if row[0].casefold() not in terms:
                    table["rows"].append(row)
                    terms.add(row[0].casefold())

    excluded_headings = {s.heading.casefold() for s in section_library() if s.id in template.exclude_sections}
    content["sections"] = [s for s in content["sections"] if s["id"] not in template.exclude_sections
                           and s.get("heading", "").strip().casefold() not in excluded_headings]
    specs = {spec.id: spec for spec in template.sections()}
    for section in content["sections"]:
        if section["id"] in specs:
            section["heading"] = specs[section["id"]].heading
    if abbreviations.SECTION_ID in specs and not any(s["id"] == abbreviations.SECTION_ID for s in content["sections"]):
        content["sections"].append(
            {"id": abbreviations.SECTION_ID, "heading": specs[abbreviations.SECTION_ID].heading, "origin": "system", "blocks": []}
        )
    content["sections"] = order_sections(content["sections"], template)

    replaced = people.remove_personal_names(content, index)
    if replaced:
        notes.append(
            _note(
                "personal_names_removed",
                f"{replaced} reference(s) to named individuals were replaced by the role they hold, or by a placeholder where "
                "the documents do not state the role. Policies assign duties to roles, not to people.",
            )
        )

    if abbreviations.SECTION_ID in specs:
        ignore = {word for word in organization.replace(".", " ").split() if word.isupper()} | {template.code}
        notes += abbreviations.rebuild(content, specs[abbreviations.SECTION_ID].heading, ignore=ignore)
    return notes
