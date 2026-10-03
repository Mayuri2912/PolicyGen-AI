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

from app.services.policy_generation import abbreviations, people
from app.services.policy_generation.templates import PolicyTemplate, is_retired


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

    specs = {spec.id: spec for spec in template.sections()}
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
