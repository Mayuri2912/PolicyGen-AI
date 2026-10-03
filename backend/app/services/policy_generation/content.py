"""Helpers for stored policy content.

Stored content is a plain dict::

    {"title": str, "sections": [{"id", "heading", "origin", "blocks": [block, ...]}]}

where ``origin`` records who last wrote the section (ai_generated, ai_revised,
simulated or user_edited) and each block has the fields of
``schemas.policy.PolicyBlock``.

The editable text markup lets a user change a section in a plain textarea::

    ### Subheading
    A paragraph of text.

    - a bullet
    1. a numbered step
    >> [documented] A policy statement. {REQ-001, CTL-002}
    | Column A | Column B |
    | value    | value    |
"""

from __future__ import annotations

import re

from app.schemas.policy import PolicyBlock, PolicySection, PolicyStatement
from app.services.policy_generation.templates import PolicyTemplate, is_retired

REF_PATTERN = re.compile(r"\b(?:REQ|RSK|CTL|GAP|REC)-\d{3,}\b")
_STATEMENT_LINE = re.compile(r"^>>\s*(?:\[(documented|proposed)\]\s*)?(.*?)\s*(?:\{([^}]*)\})?\s*$", re.IGNORECASE)
_NUMBERED_LINE = re.compile(r"^\d+[.)]\s+(.*)$")
_TABLE_RULE = re.compile(r"^\|?[\s:\-|]+\|?$")


def clean_blocks(blocks: list[PolicyBlock]) -> list[dict]:
    """Drop empty blocks and make every table rectangular."""
    cleaned = []
    for block in blocks:
        if block.is_empty():
            continue
        data = block.model_dump()
        if block.type == "table":
            width = len(block.columns)
            data["rows"] = [(row + [""] * width)[:width] for row in block.rows if any(c.strip() for c in row)]
            if not data["rows"]:
                continue
        elif block.type in ("bullets", "numbered"):
            data["items"] = [i.strip() for i in block.items if i.strip()]
        cleaned.append(data)
    return cleaned


def normalise_sections(sections: list[PolicySection], template: PolicyTemplate, origin: str) -> list[dict]:
    """Order sections as the template defines and apply the template headings.

    A retired section (the Executive Summary) is dropped even if the model wrote one.
    """
    specs = {s.id: s for s in template.sections()}
    by_id: dict[str, dict] = {}
    for section in sections:
        section_id = section.id.strip().lower()
        if section_id in by_id or (section_id not in specs and is_retired(section_id, section.heading)):
            continue
        heading = specs[section_id].heading if section_id in specs else section.heading.strip()
        by_id[section_id] = {"id": section_id, "heading": heading, "origin": origin, "blocks": clean_blocks(section.blocks)}
    ordered = [by_id[s] for s in specs if s in by_id]
    ordered += [section for section_id, section in by_id.items() if section_id not in specs]
    return ordered


def number_statements(content: dict, code: str) -> None:
    """Give every policy statement a stable, document-order id such as ACP-007."""
    counter = 0
    for section in content["sections"]:
        for block in section["blocks"]:
            if block["type"] == "statements":
                for statement in block["statements"]:
                    counter += 1
                    statement["id"] = f"{code}-{counter:03d}"


def strip_unknown_refs(content: dict, valid_ids: set[str]) -> list[str]:
    """Remove statement references to finding ids that do not exist."""
    removed = []
    for section in content["sections"]:
        for block in section["blocks"]:
            for statement in block.get("statements", []):
                kept = [r for r in dict.fromkeys(statement.get("refs", [])) if r in valid_ids]
                removed += [r for r in statement.get("refs", []) if r not in valid_ids]
                statement["refs"] = kept
    return sorted(set(removed))


def block_text(block: dict) -> str:
    return PolicyBlock.model_validate(block).plain_text()


def section_text(section: dict) -> str:
    return "\n".join(block_text(b) for b in section["blocks"])


def content_text(content: dict) -> str:
    return "\n\n".join(f"{s['heading']}\n{section_text(s)}" for s in content["sections"])


# ------------------------------------------------------------------ markup
def section_to_markup(section: dict) -> str:
    parts: list[str] = []
    for block in section["blocks"]:
        kind = block["type"]
        if kind == "paragraph":
            parts.append(block["text"].strip())
        elif kind == "subheading":
            parts.append(f"### {block['text'].strip()}")
        elif kind == "bullets":
            parts.append("\n".join(f"- {item}" for item in block["items"]))
        elif kind == "numbered":
            parts.append("\n".join(f"{n}. {item}" for n, item in enumerate(block["items"], start=1)))
        elif kind == "statements":
            lines = []
            for statement in block["statements"]:
                refs = f" {{{', '.join(statement['refs'])}}}" if statement.get("refs") else ""
                lines.append(f">> [{statement.get('basis', 'proposed')}] {statement['text']}{refs}")
            parts.append("\n".join(lines))
        elif kind == "table":
            rows = [block["columns"], *block["rows"]]
            parts.append("\n".join("| " + " | ".join(c.replace("|", "/") for c in row) + " |" for row in rows))
    return "\n\n".join(p for p in parts if p)


def markup_to_blocks(markup: str) -> list[dict]:
    """Parse the editable markup back into blocks (the inverse of ``section_to_markup``)."""
    blocks: list[PolicyBlock] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(PolicyBlock(type="paragraph", text=" ".join(paragraph)))
            paragraph.clear()

    def last(kind: str) -> PolicyBlock | None:
        return blocks[-1] if blocks and blocks[-1].type == kind and not paragraph else None

    for raw in markup.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush_paragraph()
            # A blank line ends a list or table: start a new block next time.
            blocks.append(PolicyBlock(type="paragraph", text=""))
            continue
        if line.startswith("###"):
            flush_paragraph()
            blocks.append(PolicyBlock(type="subheading", text=line.lstrip("#").strip()))
        elif line.startswith(">>"):
            flush_paragraph()
            match = _STATEMENT_LINE.match(line)
            text = (match.group(2) or "").strip()
            if len(text) >= 3:
                refs = [r.strip().upper() for r in (match.group(3) or "").split(",") if r.strip()]
                statement = PolicyStatement(text=text, basis=(match.group(1) or "proposed").lower(), refs=refs)
                target = last("statements")
                if target is None:
                    blocks.append(PolicyBlock(type="statements", statements=[statement]))
                else:
                    target.statements.append(statement)
        elif line.startswith("|"):
            flush_paragraph()
            if _TABLE_RULE.match(line):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            target = last("table")
            if target is None:
                blocks.append(PolicyBlock(type="table", columns=cells))
            else:
                target.rows.append(cells)
        elif line.startswith(("- ", "* ", "• ")):
            flush_paragraph()
            target = last("bullets")
            if target is None:
                blocks.append(PolicyBlock(type="bullets", items=[line[2:].strip()]))
            else:
                target.items.append(line[2:].strip())
        elif _NUMBERED_LINE.match(line):
            flush_paragraph()
            item = _NUMBERED_LINE.match(line).group(1).strip()
            target = last("numbered")
            if target is None:
                blocks.append(PolicyBlock(type="numbered", items=[item]))
            else:
                target.items.append(item)
        else:
            paragraph.append(line)
    flush_paragraph()
    return clean_blocks(blocks)
