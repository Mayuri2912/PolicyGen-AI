"""Control mapping and gap-analysis post-processing."""

from __future__ import annotations

from collections import Counter

from app.schemas.analysis import GAP_STATUSES

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def clean_references(items: list[dict], fields: dict[str, set[str]]) -> list[str]:
    """Drop references to ids that do not exist; return a note per removal.

    ``fields`` maps a list-valued field name to the set of valid ids for it.
    """
    notes = []
    for item in items:
        for field, valid in fields.items():
            values = item.get(field) or []
            kept = [v for v in dict.fromkeys(values) if v in valid]
            dropped = [v for v in values if v not in valid]
            if dropped:
                notes.append(
                    f"{item.get('ref_id', 'item')}: removed reference(s) to unknown id(s) {', '.join(sorted(set(dropped)))}."
                )
            item[field] = kept
    return notes


def complete_gap_analysis(gaps: list[dict], requirements: list[dict]) -> list[dict]:
    """Make sure every requirement has exactly one gap entry.

    Entries for unknown requirements are dropped, duplicates are collapsed, and
    requirements the model skipped are added as Not Evidenced and flagged, so
    the gap analysis never silently omits a requirement.
    """
    by_requirement: dict[str, dict] = {}
    requirement_ids = {r["ref_id"]: r for r in requirements}
    for gap in gaps:
        req_id = gap.get("requirement_id")
        if req_id in requirement_ids and req_id not in by_requirement:
            by_requirement[req_id] = gap

    completed = []
    for req_id, requirement in requirement_ids.items():
        gap = by_requirement.get(req_id)
        if gap is None:
            gap = {
                "requirement_id": req_id,
                "existing_evidence": "",
                "status": "not_evidenced",
                "identified_gap": "The analysis returned no assessment for this requirement; it is treated as not evidenced until reviewed.",
                "associated_risk_ids": [],
                "recommended_control_ids": [],
                "recommended_control": "",
                "priority": "medium",
                "suggested_action": "Review this requirement manually and record the supporting evidence.",
                "auto_added": True,
            }
        gap = dict(gap)
        gap["requirement"] = requirement["statement"]
        gap["status_label"] = GAP_STATUSES.get(gap["status"], gap["status"])
        completed.append(gap)
    completed.sort(key=lambda g: (_PRIORITY_ORDER.get(g.get("priority", "medium"), 2), g["requirement_id"]))
    return completed


def summarize_gaps(gaps: list[dict]) -> dict:
    counts = Counter(g.get("status") for g in gaps)
    return {
        "total": len(gaps),
        "by_status": {status: counts.get(status, 0) for status in GAP_STATUSES},
        "by_priority": dict(Counter(g.get("priority", "medium") for g in gaps)),
    }


def control_matrix(requirements: list[dict], risks: list[dict], controls: list[dict], gaps: list[dict]) -> list[dict]:
    """One row per requirement: its gap status, risks, and existing / recommended controls."""
    gap_by_requirement = {g["requirement_id"]: g for g in gaps}
    rows = []
    for requirement in requirements:
        req_id = requirement["ref_id"]
        gap = gap_by_requirement.get(req_id, {})
        linked = [c for c in controls if req_id in (c.get("related_requirements") or [])]
        linked_ids = {c["ref_id"] for c in linked}
        for control_id in gap.get("recommended_control_ids") or []:
            if control_id not in linked_ids:
                match = next((c for c in controls if c["ref_id"] == control_id), None)
                if match:
                    linked.append(match)
                    linked_ids.add(control_id)
        risk_ids = list(dict.fromkeys([*(gap.get("associated_risk_ids") or []), *(r for c in linked for r in c.get("related_risks") or [])]))
        known_risks = {r["ref_id"] for r in risks}
        rows.append(
            {
                "requirement_id": req_id,
                "requirement": requirement["statement"],
                "category": requirement.get("category_label", ""),
                "status": gap.get("status", "not_evidenced"),
                "status_label": gap.get("status_label", GAP_STATUSES["not_evidenced"]),
                "risk_ids": [r for r in risk_ids if r in known_risks],
                "existing_controls": [
                    {"id": c["ref_id"], "name": c["name"], "status": c.get("status", "unknown")}
                    for c in linked
                    if c.get("basis") == "existing"
                ],
                "recommended_controls": [
                    {"id": c["ref_id"], "name": c["name"]} for c in linked if c.get("basis") == "recommended"
                ],
            }
        )
    return rows


def summarize_controls(controls: list[dict]) -> dict:
    return {
        "total": len(controls),
        "existing": sum(1 for c in controls if c.get("basis") == "existing"),
        "recommended": sum(1 for c in controls if c.get("basis") == "recommended"),
        "by_type": dict(Counter(c.get("control_type", "administrative") for c in controls)),
        "by_function": dict(Counter(c.get("control_function", "preventive") for c in controls)),
        "by_status": dict(Counter(c.get("status", "unknown") for c in controls)),
    }
