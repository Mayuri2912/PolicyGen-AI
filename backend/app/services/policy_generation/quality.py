"""AI quality validation for generated policy content.

Runs deterministic checks on a draft and returns a report.  ``error`` issues
make a section eligible for one bounded correction round; ``warning`` and
``info`` issues are surfaced to the reviewer.  Nothing here calls an LLM.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from app.schemas.policy import PLACEHOLDER_PATTERN
from app.services.ai_analysis.facts import extract_facts, normalise_value
from app.services.ai_analysis.standards import CLAIM_PATTERN, STANDARD_PATTERNS
from app.services.policy_generation import abbreviations, people as people_rules
from app.services.policy_generation.content import REF_PATTERN, block_text, section_text
from app.services.policy_generation.templates import PolicyTemplate, is_retired

MIN_SECTION_CHARS = 40
REPETITION_THRESHOLD = 0.85
# Issue codes that a correction round may be able to fix.
CORRECTABLE = {
    "missing_section", "empty_section", "unsupported_claim", "repetitive_section", "no_statements", "unclear_responsibilities",
    "personal_name",
}


def _issue(code: str, severity: str, message: str, section_id: str | None = None) -> dict:
    return {"code": code, "severity": severity, "section_id": section_id, "message": message}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def validate_policy(
    content: dict,
    template: PolicyTemplate,
    *,
    valid_ids: set[str],
    source_standards: set[str],
    people: people_rules.PeopleIndex | None = None,
) -> dict:
    """Check ``content`` and return a quality report.

    ``valid_ids`` are the finding ids that exist in the analysis;
    ``source_standards`` are the standards the source documents mention;
    ``people`` is what is known about named individuals (see ``people.py``).
    """
    issues: list[dict] = []
    sections = {s["id"]: s for s in content.get("sections", [])}
    texts = {sid: section_text(s) for sid, s in sections.items()}

    # 1. Mandatory sections present, 2. no empty sections.
    for spec in template.sections():
        if spec.id not in sections:
            if spec.mandatory:
                issues.append(_issue("missing_section", "error", f"The mandatory section '{spec.heading}' is missing.", spec.id))
        elif len(texts[spec.id].strip()) < MIN_SECTION_CHARS:
            issues.append(_issue("empty_section", "error", f"The section '{spec.heading}' has little or no content.", spec.id))

    # 3. Repetition: near-identical sections and paragraphs reused across sections.
    ids = [sid for sid, text in texts.items() if len(text) >= 200]
    for i, first in enumerate(ids):
        for second in ids[i + 1 :]:
            if SequenceMatcher(None, texts[first], texts[second]).quick_ratio() > REPETITION_THRESHOLD and (
                SequenceMatcher(None, texts[first], texts[second]).ratio() > REPETITION_THRESHOLD
            ):
                issues.append(
                    _issue(
                        "repetitive_section", "warning",
                        f"'{sections[second]['heading']}' largely repeats '{sections[first]['heading']}'.", second,
                    )
                )
    seen_paragraphs: dict[str, str] = {}
    for sid, section in sections.items():
        for block in section["blocks"]:
            if block["type"] == "paragraph" and len(block["text"]) >= 80:
                key = _norm(block["text"])
                if key in seen_paragraphs and seen_paragraphs[key] != sid:
                    issues.append(
                        _issue(
                            "repetitive_section", "warning",
                            f"A paragraph in '{section['heading']}' duplicates one in '{sections[seen_paragraphs[key]]['heading']}'.", sid,
                        )
                    )
                seen_paragraphs.setdefault(key, sid)

    # 4. Unsupported claims: standards the sources never mention, and assertions of formal status.
    for sid, text in texts.items():
        if sid == abbreviations.SECTION_ID:
            continue  # the list only expands what other sections say; the claim is flagged where it is made
        for name, pattern in STANDARD_PATTERNS:
            if pattern.search(text) and name not in source_standards:
                issues.append(
                    _issue(
                        "unsupported_claim", "error",
                        f"'{sections[sid]['heading']}' refers to {name}, which the source documents do not mention.", sid,
                    )
                )
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if CLAIM_PATTERN.search(sentence) and not PLACEHOLDER_PATTERN.search(sentence):
                issues.append(
                    _issue(
                        "unsupported_claim", "warning",
                        f"'{sections[sid]['heading']}' asserts a certification or compliance status that should be checked "
                        f"against evidence: \"{sentence.strip()[:160]}\"", sid,
                    )
                )

    # 5. References to findings must exist; statement ids must be unique.
    statement_ids: list[str] = []
    statement_count = 0
    for sid, section in sections.items():
        mentioned = set(REF_PATTERN.findall(texts[sid]))
        for block in section["blocks"]:
            for statement in block.get("statements", []):
                statement_count += 1
                mentioned.update(statement.get("refs", []))
                if statement.get("id"):
                    statement_ids.append(statement["id"])
                if statement.get("basis") == "documented" and not statement.get("refs"):
                    issues.append(
                        _issue(
                            "missing_source_reference", "warning",
                            f"A statement marked as documented in '{section['heading']}' has no supporting reference: "
                            f"\"{statement['text'][:120]}\"", sid,
                        )
                    )
        unknown = sorted(mentioned - valid_ids)
        if unknown:
            issues.append(
                _issue(
                    "unknown_reference", "error",
                    f"'{section['heading']}' refers to finding id(s) that do not exist in the analysis: {', '.join(unknown)}.", sid,
                )
            )
    if len(statement_ids) != len(set(statement_ids)):
        issues.append(_issue("duplicate_statement_id", "error", "Two or more policy statements share the same id."))

    # 6. The requirements section must contain actual statements.
    if "policy_requirements" in sections and not any(
        b["type"] == "statements" and b["statements"] for b in sections["policy_requirements"]["blocks"]
    ):
        issues.append(
            _issue("no_statements", "error", "The Policy Requirements section contains no policy statements.", "policy_requirements")
        )

    # 7. Responsibilities must name roles.
    roles = sections.get("roles_responsibilities")
    if roles is not None:
        entries = sum(len(b["rows"]) for b in roles["blocks"] if b["type"] == "table") + sum(
            len(b["items"]) for b in roles["blocks"] if b["type"] in ("bullets", "numbered")
        )
        if entries < 2:
            issues.append(
                _issue(
                    "unclear_responsibilities", "warning",
                    "Roles and Responsibilities names fewer than two roles; responsibilities may be unclear.", "roles_responsibilities",
                )
            )

    # 7a. Role references must not name individuals.
    issues += people_rules.audit(content, people or people_rules.PeopleIndex())

    # 7b. Abbreviations sit immediately before Scope, in a two-column table, and are not repeated in Definitions.
    issues += abbreviations.audit(content)

    # 7c. A retired section must not come back.
    for sid, section in sections.items():
        if is_retired(sid, section.get("heading", "")):
            issues.append(
                _issue("retired_section", "error", f"'{section['heading']}' is no longer part of the policy structure and must be removed.", sid)
            )

    # 8. Contradictions between sections (same fact, different value).
    values: dict[str, dict[str, str]] = {}
    for sid, text in texts.items():
        if PLACEHOLDER_PATTERN.search(text) and "disagree" in text:
            continue  # the section is explicitly reporting a source contradiction
        for fact in extract_facts(text):
            values.setdefault(fact["topic"], {}).setdefault(normalise_value(fact["value"]), sid)
    for topic, found in values.items():
        if len(found) > 1:
            where = "; ".join(f"'{value}' in {sections[sid]['heading']}" for value, sid in found.items())
            issues.append(_issue("contradiction", "warning", f"The policy gives different values for {topic.lower()}: {where}."))

    # 9. Structure.
    for sid, section in sections.items():
        if not section.get("heading", "").strip():
            issues.append(_issue("invalid_structure", "error", "A section has no heading.", sid))
        for block in section["blocks"]:
            if block["type"] == "table" and any(len(row) != len(block["columns"]) for row in block["rows"]):
                issues.append(_issue("invalid_structure", "error", f"A table in '{section['heading']}' has uneven rows.", sid))

    # 10. Placeholders are expected where information is missing; list them for the reviewer.
    placeholders = []
    for sid, section in sections.items():
        for block in section["blocks"]:
            for match in PLACEHOLDER_PATTERN.finditer(block_text(block)):
                placeholders.append({"section_id": sid, "section": section["heading"], "text": match.group(0)})
    if placeholders:
        issues.append(
            _issue(
                "unresolved_placeholders", "info",
                f"{len(placeholders)} placeholder(s) mark information the documents did not provide. Complete them before approval.",
            )
        )

    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = sum(1 for i in issues if i["severity"] == "warning")
    return {
        "passed": errors == 0,
        "score": max(0, 100 - 12 * errors - 4 * warnings),
        "counts": {"error": errors, "warning": warnings, "info": len(issues) - errors - warnings},
        "issues": issues,
        "placeholders": placeholders,
        "statement_count": statement_count,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def sections_needing_correction(report: dict) -> list[str]:
    """Section ids with issues a correction round might fix, in first-seen order."""
    return list(
        dict.fromkeys(
            i["section_id"] for i in report["issues"] if i["code"] in CORRECTABLE and i["severity"] != "info" and i["section_id"]
        )
    )
