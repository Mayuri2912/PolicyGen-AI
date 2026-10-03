"""Helpers that turn flat text and raw rows into sections and tables."""

from __future__ import annotations

import re

from charset_normalizer import from_bytes

from app.schemas.documents import DocumentSection, DocumentTable

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+){0,3})[.)]?\s+([A-Z][^\n]{2,90})$")
_UNDERLINE = re.compile(r"^(=+|-{3,})$")


_LIST_ITEM = re.compile(r"^\s*(?:[^\w\s\[(\"'<]|\(?\d{1,2}[.)]|\(?[a-zA-Z][.)])\s+")
_SENTENCE_END = (".", "!", "?", ":", ";")


_UNMAPPED_GLYPH = re.compile(r"\(cid:\d+\)|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f�]")


def clean_extracted_text(text: str) -> str:
    """Normalize glyphs a PDF could not map to characters.

    At the start of a line such a glyph is almost always a list bullet, so it
    becomes one; elsewhere it is dropped.
    """
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.lstrip()
        match = _UNMAPPED_GLYPH.match(stripped)
        if match:
            line = "- " + stripped[match.end() :].lstrip()
        lines.append(_UNMAPPED_GLYPH.sub("", line))
    return "\n".join(lines)


def reflow_lines(text: str) -> str:
    """Rejoin lines that were only broken by page width (PDF and OCR text).

    A line is treated as wrapped when it is nearly as long as the longest line,
    does not end a sentence, and the next line does not start a list item.
    Short lines (headings, list ends, paragraph ends) are left alone.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    longest = max((len(line.rstrip()) for line in lines), default=0)
    if longest < 40:
        return "\n".join(lines)
    out: list[str] = []
    last_width = 0  # length of the last physical line merged into out[-1]
    for line in lines:
        current = line.strip()
        previous = out[-1].rstrip() if out else ""
        wrapped = (
            bool(current)
            and bool(previous)
            and last_width >= 0.6 * longest
            and not previous.endswith(_SENTENCE_END)
            and not _LIST_ITEM.match(current)
        )
        if wrapped:
            out[-1] = previous + ("" if previous.endswith("-") else " ") + current
        else:
            out.append(line.rstrip())
        last_width = len(current)
    return "\n".join(out)


def decode_text(raw: bytes) -> str:
    """Decode bytes to text, honouring BOMs and falling back to detection."""
    for bom, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            return raw.decode(encoding, errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        best = from_bytes(raw).best()
        return str(best) if best is not None else raw.decode("latin-1", errors="replace")


def clean_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def _heading_of(line: str, next_line: str) -> tuple[int, str] | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 110:
        return None
    match = _MD_HEADING.match(stripped)
    if match:
        return len(match.group(1)), match.group(2).strip()
    if _UNDERLINE.match(next_line.strip()) and not stripped.startswith(("-", "*", "|")):
        return (1 if next_line.strip().startswith("=") else 2), stripped
    match = _NUMBERED_HEADING.match(stripped)
    if match and not stripped.endswith((".", ";", ",")) and len(stripped.split()) <= 12:
        return match.group(1).count(".") + 1, stripped
    letters = [c for c in stripped if c.isalpha()]
    if (
        len(letters) >= 4
        and stripped.upper() == stripped
        and len(stripped.split()) <= 10
        and not stripped.endswith((".", ",", ";"))
        and not stripped.startswith(("-", "*", "|"))
    ):
        return 1, stripped
    return None


def split_into_sections(text: str, *, id_prefix: str, location: str = "", default_heading: str = "") -> list[DocumentSection]:
    """Split text into sections at detected headings.

    Recognizes Markdown headings, underlined headings, numbered headings
    ("3.2 Access Reviews") and short ALL-CAPS lines.  Text before the first
    heading becomes a section named ``default_heading``.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: list[DocumentSection] = []
    heading, level, buffer = default_heading, 1, []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body or (heading and heading != default_heading):
            where = f"{location} > {heading}" if location and heading and heading != location else (location or heading)
            sections.append(
                DocumentSection(
                    section_id=f"{id_prefix}-s{len(sections) + 1}",
                    heading=heading,
                    level=level,
                    text=re.sub(r"\n{3,}", "\n\n", body),
                    location=where,
                )
            )

    skip_next = False
    for index, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        found = _heading_of(line, next_line)
        if found:
            flush()
            level, heading = found
            buffer = []
            skip_next = bool(_UNDERLINE.match(next_line.strip()))
        else:
            buffer.append(line.rstrip())
    flush()
    return sections


def rows_to_table(
    rows: list[list[str]],
    *,
    table_id: str,
    title: str = "",
    location: str = "",
    max_rows: int = 5000,
) -> DocumentTable | None:
    """Build a table from raw rows, treating the first non-empty row as headers."""
    rows = [[clean_cell(c) for c in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return None
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    # Drop columns that are empty in every row (common in exported sheets).
    keep = [i for i in range(width) if any(row[i] for row in rows)]
    rows = [[row[i] for i in keep] for row in rows]
    headers, body = rows[0], rows[1:]
    if not body:
        # A single row is data, not a header.
        headers, body = [f"Column {i + 1}" for i in range(len(rows[0]))], rows
    headers = [h or f"Column {i + 1}" for i, h in enumerate(headers)]
    truncated = len(body) > max_rows
    return DocumentTable(
        table_id=table_id,
        title=title,
        location=location,
        headers=headers,
        rows=body[:max_rows],
        truncated=truncated,
    )
