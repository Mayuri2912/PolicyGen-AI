"""Pattern-based extraction of checkable facts (lengths, frequencies, limits).

Shared by the mock analyzer, which uses it to find contradictions between
source documents, and by the policy quality check, which uses it to find
contradictions between sections of a generated policy.
"""

from __future__ import annotations

import re

FACT_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (topic, re.compile(pattern, re.IGNORECASE))
    for topic, pattern in (
        ("Minimum password length", r"(?:passwords?[^.\n]{0,70}?(?:at least|minimum(?: length)?(?: of)?)\s*(\d+\s*characters)|minimum password length[^.\n\d]{0,25}(\d+(?:\s*characters)?))"),
        ("Password expiry", r"passwords?[^.\n]{0,60}?(?:expire|changed|rotated)[^.\n]{0,30}?every\s+(\d+\s*days)"),
        ("Access review frequency", r"(?:access (?:rights? |privileges? )?reviews?[^.\n]{0,80}?\b(monthly|quarterly|semi-annually|bi-annually|twice a year|every six months|annually|yearly|every \d+ (?:days|months))|\b(monthly|quarterly|semi-annually|bi-annually|annually|yearly|annual) (?:user )?access reviews?)"),
        ("Incident reporting window", r"incidents?[^.\n]{0,90}?reported[^.\n]{0,50}?within\s+(\d+\s*(?:hours?|minutes|days?))"),
        ("Backup frequency", r"backups?[^.\n]{0,60}?\b(hourly|daily|nightly|weekly|monthly)"),
        ("Vendor assessment frequency", r"(?:vendors?|suppliers?|third[- ]part(?:y|ies))[^.\n]{0,90}?(?:assess|review)[^.\n]{0,50}?\b(annually|yearly|quarterly|semi-annually|every \d+ months|every two years)"),
        ("Account lockout threshold", r"(\d+)\s+(?:consecutive\s+)?(?:failed|unsuccessful|invalid)\s+(?:login|logon|sign-in)?\s*attempts"),
        ("Session timeout", r"(?:session|screen|idle)[^.\n]{0,50}?(\d+\s*minutes)"),
        ("Recovery time objective", r"\bRTO\b[^.\n\d]{0,40}(\d+\s*(?:hours?|minutes|days?))"),
        ("Recovery point objective", r"\bRPO\b[^.\n\d]{0,40}(\d+\s*(?:hours?|minutes|days?))"),
        ("Log retention period", r"logs?[^.\n]{0,60}?retained[^.\n]{0,40}?(\d+\s*(?:days|months|years?))"),
        ("Policy review frequency", r"polic(?:y|ies)[^.\n]{0,60}?reviewed[^.\n]{0,30}?\b(annually|yearly|every \d+ months|every two years|semi-annually|quarterly)"),
    )
)

_SYNONYMS = {
    "yearly": "annually", "annual": "annually", "every 12 months": "annually", "nightly": "daily",
    "every six months": "semi-annually", "every 6 months": "semi-annually", "twice a year": "semi-annually",
    "bi-annually": "semi-annually",
}


def normalise_value(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip().lower())
    value = _SYNONYMS.get(value, value)
    return re.sub(r"\s*characters?$", "", value)


def extract_facts(text: str) -> list[dict]:
    """Return ``{"topic", "value", "evidence"}`` for every fact pattern found."""
    facts = []
    for topic, pattern in FACT_PATTERNS:
        for match in pattern.finditer(text or ""):
            value = next((g for g in match.groups() if g), "").strip()
            if value:
                facts.append(
                    {
                        "topic": topic,
                        "value": re.sub(r"\s+", " ", value.lower()),
                        "evidence": re.sub(r"\s+", " ", match.group(0))[:300],
                    }
                )
    return facts
