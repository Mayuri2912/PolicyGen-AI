"""Comparison of policy versions."""

from __future__ import annotations

from difflib import SequenceMatcher

from app.services.policy_generation.content import section_to_markup


def _lines(section: dict | None) -> list[str]:
    return [line for line in section_to_markup(section).split("\n") if line.strip()] if section else []


def compare_versions(old: dict, new: dict) -> dict:
    """Section-by-section line diff between two versions of policy content."""
    old_sections = {s["id"]: s for s in old.get("sections", [])}
    new_sections = {s["id"]: s for s in new.get("sections", [])}
    order = list(new_sections) + [sid for sid in old_sections if sid not in new_sections]

    sections, counts = [], {"unchanged": 0, "changed": 0, "added": 0, "removed": 0}
    for section_id in order:
        before, after = old_sections.get(section_id), new_sections.get(section_id)
        old_lines, new_lines = _lines(before), _lines(after)
        if before is None:
            status = "added"
        elif after is None:
            status = "removed"
        else:
            status = "unchanged" if old_lines == new_lines else "changed"
        counts[status] += 1

        diff = []
        if status != "unchanged":
            for tag, i1, i2, j1, j2 in SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
                if tag == "equal":
                    diff += [{"type": "equal", "text": line} for line in old_lines[i1:i2]]
                else:
                    diff += [{"type": "delete", "text": line} for line in old_lines[i1:i2]]
                    diff += [{"type": "insert", "text": line} for line in new_lines[j1:j2]]
        sections.append(
            {
                "id": section_id,
                "heading": (after or before)["heading"],
                "status": status,
                "diff": diff,
            }
        )
    return {
        "title_changed": old.get("title") != new.get("title"),
        "old_title": old.get("title"),
        "new_title": new.get("title"),
        "counts": counts,
        "sections": sections,
    }


def same_content(first: dict, second: dict) -> bool:
    """True when two versions read the same (ignoring origin flags and ids)."""
    comparison = compare_versions(first, second)
    return not comparison["title_changed"] and all(s["status"] == "unchanged" for s in comparison["sections"])
