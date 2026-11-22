"""Policy recommendation engine.

Ranks the available policy types for an analysis using evidence that can be
shown to the user:

* what the documents call themselves (title and document type),
* how many requirements fall in the categories a policy covers,
* which of the policy's keywords occur in the findings,
* template-declared signals (requirements spread across many areas, risks
  rated in the documents),
* the suggestion made by the AI analysis, which read the documents in full.

Every recommendation lists its reasons; none of them assert anything about the
organization beyond what the findings contain.
"""

from __future__ import annotations

from collections import Counter

from app.schemas.analysis import REQUIREMENT_CATEGORIES
from app.services.policy_generation.templates import PolicyTemplate, load_templates

_MAX_RESULTS = 6
_KEYWORD_CAP = 4  # a keyword repeated many times counts no more than this
_TITLE_WEIGHT = 12
_CATEGORY_WEIGHT = 3
_BREADTH_MIN_CATEGORIES = 4
_SCORED_RISKS_MIN = 5


def _signal_score(template: PolicyTemplate, requirements: list[dict], risks: list[dict], category_counts: Counter) -> tuple[int, list[str]]:
    score, reasons = 0, []
    if "breadth" in template.signals and requirements:
        distinct = len(category_counts)
        top_share = category_counts.most_common(1)[0][1] / len(requirements)
        if distinct >= _BREADTH_MIN_CATEGORIES and top_share <= 0.5:
            score += 4 * distinct
            reasons.append(
                f"The requirements span {distinct} different areas with none dominating, which suits an overarching policy."
            )
    if "scored_risks" in template.signals:
        rated = sum(1 for r in risks if r.get("scoring_basis") == "documented")
        if rated >= _SCORED_RISKS_MIN and rated >= len(requirements):
            score += 20 + rated
            reasons.append(
                f"{rated} risks carry likelihood and impact ratings taken from the documents, as in a risk register."
            )
    return score, reasons


def recommend(findings: dict[str, list[dict]], summary: dict, *, simulated: bool) -> list[dict]:
    """Return ranked policy recommendations with their supporting reasons.

    ``findings`` holds the active (non-excluded) findings grouped by kind.
    """
    requirements, risks = findings.get("requirement", []), findings.get("risk", [])
    corpus = " ".join(
        [r.get("statement", "") for r in requirements]
        + [f"{r.get('title', '')} {r.get('description', '')} {r.get('category', '')}" for r in risks]
        + [f"{c.get('name', '')} {c.get('description', '')}" for c in findings.get("control", [])]
    ).lower()
    titles = list(summary.get("document_titles") or [])
    identity = " | ".join(
        titles + [d.get("category", "") for d in summary.get("document_summaries") or []] + [summary.get("document_category") or ""]
    ).lower()
    category_counts = Counter(r.get("category") for r in requirements)

    suggestion = summary.get("policy_recommendation") or {}
    suggested = suggestion.get("policy_type") or ""
    alternatives = {a["policy_type"]: a.get("reason", "") for a in suggestion.get("alternatives") or []}
    source = "Rule-based suggestion (mock mode)" if simulated else "AI analysis"

    scored = []
    for template in load_templates().values():
        reasons: list[str] = []

        title_terms = [k for k in template.keywords if k in identity]
        if title_terms:
            reasons.append("The documents' titles or types match this policy (" + ", ".join(title_terms[:4]) + ").")

        in_category = sum(category_counts.get(c, 0) for c in template.categories)
        if in_category:
            labels = ", ".join(
                f"{category_counts[c]} in {REQUIREMENT_CATEGORIES[c].lower()}"
                for c in template.categories
                if category_counts.get(c) and c in REQUIREMENT_CATEGORIES
            )
            reasons.append(f"{in_category} identified requirement(s) fall in areas this policy covers ({labels}).")

        hits = {k: corpus.count(k) for k in template.keywords}
        matched = [k for k, n in sorted(hits.items(), key=lambda kv: -kv[1]) if n]
        if matched:
            reasons.append("Related terms appear in the findings: " + ", ".join(matched[:6]) + ".")

        signal_score, signal_reasons = _signal_score(template, requirements, risks, category_counts)
        reasons += signal_reasons
        score = (
            _TITLE_WEIGHT * len(title_terms)
            + _CATEGORY_WEIGHT * in_category
            + sum(min(n, _KEYWORD_CAP) for n in hits.values())
            + signal_score
        )
        # Long catalogue guidance must not win merely by supplying many more
        # keywords. Its identity must be present to outrank a focused policy
        # match from an otherwise general assessment.
        if template.management_system and template.code.lower() not in identity and template.name.lower() not in identity:
            score = int(score * 0.5)
        if template.id == suggested:
            reasons.insert(0, f"{source}: {suggestion.get('rationale') or 'suggested as the best fit for these documents.'}")
        elif template.id in alternatives:
            reasons.append(f"{source} (alternative): {alternatives[template.id] or 'also supported by these documents.'}")
        scored.append({"template": template, "score": score, "reasons": reasons})

    if not simulated:
        # A real model read the documents in full, so its pick leads unless the
        # countable evidence for another type is overwhelming.  The mock's
        # suggestion is itself keyword-based and gets no extra weight.
        top = max((s["score"] for s in scored), default=0)
        for entry in scored:
            if entry["template"].id == suggested:
                entry["score"] += max(5, int(0.5 * top))
    scored.sort(key=lambda s: -s["score"])

    results = []
    best = scored[0]["score"] if scored else 0
    for entry in scored[:_MAX_RESULTS]:
        if entry["score"] <= 0 and results:
            break
        template = entry["template"]
        ratio = entry["score"] / best if best else 0
        results.append(
            {
                "policy_type": template.id,
                "name": template.name,
                "description": template.description,
                "score": entry["score"],
                "fit": "strong" if ratio >= 0.75 and entry["score"] > 0 else "moderate" if ratio >= 0.4 else "weak",
                "recommended": not results,
                "reasons": entry["reasons"]
                or ["No specific evidence for this policy type was found; it is listed as a general default."],
            }
        )
    return results
