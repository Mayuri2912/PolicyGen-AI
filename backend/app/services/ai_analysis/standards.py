"""Recognition of named standards, laws and certifications in text.

Used in two places: the analysis records which of these the *source documents*
mention, and the policy quality check flags any that a generated policy
mentions without the sources doing so (an unsupported claim).
"""

from __future__ import annotations

import re

STANDARD_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("ISO/IEC 27001", r"\bISO(?:/IEC)?\s*27001\b"),
        ("ISO/IEC 27002", r"\bISO(?:/IEC)?\s*27002\b"),
        ("ISO/IEC 27017", r"\bISO(?:/IEC)?\s*27017\b"),
        ("ISO/IEC 27018", r"\bISO(?:/IEC)?\s*27018\b"),
        ("ISO/IEC 27701", r"\bISO(?:/IEC)?\s*27701\b"),
        ("ISO 22301", r"\bISO\s*22301\b"),
        ("ISO 31000", r"\bISO\s*31000\b"),
        ("SOC 2", r"\bSOC\s*2\b"),
        ("SOC 1", r"\bSOC\s*1\b"),
        ("PCI DSS", r"\bPCI[\s-]*DSS\b"),
        ("GDPR", r"\bGDPR\b|General Data Protection Regulation"),
        ("HIPAA", r"\bHIPAA\b"),
        ("CCPA", r"\bCCPA\b"),
        ("DPDP Act", r"\bDPDP(?:\s*Act)?\b|Digital Personal Data Protection"),
        ("SOX", r"\bSOX\b|Sarbanes[- ]Oxley"),
        ("NIST CSF", r"\bNIST\s*(?:CSF|Cybersecurity Framework)\b"),
        ("NIST SP 800-53", r"\b(?:NIST\s*)?(?:SP\s*)?800-53\b"),
        ("CIS Controls", r"\bCIS\s*(?:Critical Security )?(?:Controls|Benchmarks?)\b"),
        ("COBIT", r"\bCOBIT\b"),
        ("ITIL", r"\bITIL\b"),
        ("CSA STAR", r"\bCSA\s*(?:STAR|CCM)\b"),
        ("FedRAMP", r"\bFedRAMP\b"),
        ("Cyber Essentials", r"\bCyber Essentials\b"),
    )
)

# Phrases asserting a formal status.  A generated policy may only use these
# when the source documents do.
CLAIM_PATTERN = re.compile(
    r"\b(is|are|has been|have been|remains?|maintains?|holds?|achieved|obtained)\s+(?:fully\s+|formally\s+)?"
    r"(certified|accredited|compliant with|in compliance with|audited against|attested)\b",
    re.IGNORECASE,
)


def find_standards(text: str) -> list[str]:
    return [name for name, pattern in STANDARD_PATTERNS if pattern.search(text or "")]
