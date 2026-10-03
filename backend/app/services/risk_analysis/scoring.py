"""Risk scoring method.

Score = likelihood x impact, each on a 1-5 scale.  The level bands are
configurable through RISK_LEVEL_THRESHOLDS (default "4,9,15"):

    1-4   Low
    5-9   Medium
    10-15 High
    16-25 Critical

A likelihood or impact of 0 means "not assessable from the documents"; such a
risk gets the level "unrated" instead of a misleading score.
"""

from __future__ import annotations

from collections import Counter

from app.core.config import get_settings

LIKELIHOOD_SCALE = {
    1: "Rare - not expected to occur",
    2: "Unlikely - could occur but not expected",
    3: "Possible - might occur at some time",
    4: "Likely - will probably occur",
    5: "Almost certain - expected to occur",
}
IMPACT_SCALE = {
    1: "Negligible - no measurable effect",
    2: "Minor - limited, easily recovered",
    3: "Moderate - noticeable disruption or loss",
    4: "Major - serious disruption, loss or exposure",
    5: "Severe - critical damage to operations, data or reputation",
}
LEVELS = ("critical", "high", "medium", "low", "unrated")
LEVEL_LABELS = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "unrated": "Unrated"}


def risk_level(score: int) -> str:
    if score <= 0:
        return "unrated"
    low, medium, high = get_settings().risk_thresholds
    if score <= low:
        return "low"
    if score <= medium:
        return "medium"
    if score <= high:
        return "high"
    return "critical"


def score(likelihood: int, impact: int) -> tuple[int, str]:
    likelihood = max(0, min(5, int(likelihood or 0)))
    impact = max(0, min(5, int(impact or 0)))
    value = likelihood * impact
    return value, risk_level(value)


def apply_scores(risk: dict) -> dict:
    """Return ``risk`` with score/level (and residual score/level) recomputed."""
    risk = dict(risk)
    risk["score"], risk["level"] = score(risk.get("likelihood", 0), risk.get("impact", 0))
    risk["residual_score"], risk["residual_level"] = score(
        risk.get("residual_likelihood", 0), risk.get("residual_impact", 0)
    )
    return risk


def scoring_method() -> dict:
    low, medium, high = get_settings().risk_thresholds
    return {
        "formula": "score = likelihood x impact",
        "likelihood_scale": LIKELIHOOD_SCALE,
        "impact_scale": IMPACT_SCALE,
        "levels": [
            {"level": "low", "label": "Low", "range": f"1-{low}"},
            {"level": "medium", "label": "Medium", "range": f"{low + 1}-{medium}"},
            {"level": "high", "label": "High", "range": f"{medium + 1}-{high}"},
            {"level": "critical", "label": "Critical", "range": f"{high + 1}-25"},
        ],
        "note": "A value of 0 means the documents gave no basis for assessment; the risk is shown as Unrated.",
    }


def describe_method() -> str:
    """Plain-text description of the method, embedded in the analysis prompt."""
    method = scoring_method()
    lines = ["Rate likelihood and impact from 1 to 5.", "Likelihood:"]
    lines += [f"  {k} = {v}" for k, v in LIKELIHOOD_SCALE.items()]
    lines += ["Impact:"] + [f"  {k} = {v}" for k, v in IMPACT_SCALE.items()]
    lines.append(
        "The application computes score = likelihood x impact and the level ("
        + ", ".join(f"{b['label']} {b['range']}" for b in method["levels"])
        + "), so do not compute them yourself."
    )
    return "\n".join(lines)


def risk_matrix(risks: list[dict]) -> list[list[int]]:
    """5x5 grid of risk counts: rows are impact 5..1, columns likelihood 1..5."""
    grid = [[0] * 5 for _ in range(5)]
    for risk in risks:
        likelihood, impact = int(risk.get("likelihood") or 0), int(risk.get("impact") or 0)
        if 1 <= likelihood <= 5 and 1 <= impact <= 5:
            grid[5 - impact][likelihood - 1] += 1
    return grid


def summarize_risks(risks: list[dict]) -> dict:
    levels = Counter(r.get("level", "unrated") for r in risks)
    return {
        "total": len(risks),
        "by_level": {level: levels.get(level, 0) for level in LEVELS},
        "by_category": dict(Counter(r.get("category") or "Uncategorized" for r in risks).most_common()),
        "by_origin": dict(Counter(r.get("origin", "inferred") for r in risks)),
        "matrix": risk_matrix(risks),
    }
