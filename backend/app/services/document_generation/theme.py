"""Colours and helpers shared by the DOCX and PDF renderers."""

from __future__ import annotations

import re

from app.schemas.policy import PLACEHOLDER_PATTERN
from app.services.risk_analysis.scoring import risk_level

PRIMARY = "1F3A5F"  # headings, table headers
ACCENT = "2F6F8F"
MUTED = "5B6770"
RULE = "C9D1D9"
HEADER_FILL = "1F3A5F"
ZEBRA_FILL = "F4F7FA"
NOTICE_FILL = "FFF8E1"
NOTICE_BORDER = "E0A800"
PLACEHOLDER_FILL = "FFF2A8"

LEVEL_FILL = {"low": "D7EFD9", "medium": "FFF1B8", "high": "FFD8A8", "critical": "F7B4AE", "unrated": "ECEFF1"}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(text) -> str:
    """Remove characters that are invalid in XML (and so in DOCX and PDF markup)."""
    return _CONTROL.sub("", "" if text is None else str(text))


def uses_sapres_brand(organization: str) -> bool:
    """Use the supplied logo only when the configured organization is Sapres."""
    words = re.findall(r"[a-z0-9]+", clean(organization).lower())
    return bool(words and words[0] == "sapres")


def split_placeholders(text: str) -> list[tuple[str, bool]]:
    """Split text into (fragment, is_placeholder) pairs."""
    parts, position = [], 0
    for match in PLACEHOLDER_PATTERN.finditer(text):
        if match.start() > position:
            parts.append((text[position : match.start()], False))
        parts.append((match.group(0), True))
        position = match.end()
    if position < len(text):
        parts.append((text[position:], False))
    return parts or [("", False)]


def column_weights(columns: list[str], rows: list[list[str]], widths: list[int] | None = None) -> list[float]:
    """Relative column widths: the given ones, or sized from the content."""
    if widths and len(widths) == len(columns):
        return [float(w) for w in widths]
    weights = []
    for index, title in enumerate(columns):
        lengths = sorted(len(str(row[index])) for row in rows if index < len(row)) or [0]
        if lengths[-1] <= 14:
            # Short values (ids, levels, dates): wide enough never to wrap.
            weights.append(float(max(len(title), lengths[-1]) + 5))
        else:
            typical = lengths[int(0.8 * (len(lengths) - 1))]
            weights.append(float(min(46, max(len(title) + 2, typical, 12))))
    return weights


def matrix_fill(likelihood: int, impact: int) -> str:
    return LEVEL_FILL[risk_level(likelihood * impact)]


def basis_label(statement: dict) -> str:
    basis = "Documented in source material" if statement.get("basis") == "documented" else "Proposed by this draft"
    refs = ", ".join(statement.get("refs") or [])
    return f"Basis: {basis}" + (f"  |  References: {refs}" if refs else "")
