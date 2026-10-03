"""Context management: split documents into chunks that fit the LLM budget.

Chunks are built from whole sections and tables wherever possible, and every
unit keeps its heading and location label, so findings stay traceable and no
content is dropped to make a document fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.documents import DocumentTable, NormalizedDocument


@dataclass
class Unit:
    kind: str  # section | table
    heading: str
    location: str
    text: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    first_row: int = 1  # number of the first row within the whole table

    def render(self) -> str:
        label = "Table" if self.kind == "table" else "Section"
        return f"[{label}: {self.heading or 'Untitled'} | location: {self.location or 'document'}]\n{self.text}".strip()

    def __len__(self) -> int:
        return len(self.text) + len(self.heading) + len(self.location) + 40


@dataclass
class Chunk:
    document_id: str
    file_id: str
    file_name: str
    file_type: str
    document_title: str
    index: int
    units: list[Unit]
    count: int = 1

    def render(self) -> str:
        return "\n\n".join(unit.render() for unit in self.units)


def _split_text(text: str, limit: int) -> list[str]:
    """Split oversized text at paragraph, then line, then hard boundaries."""
    if len(text) <= limit:
        return [text]
    pieces, current = [], ""
    for paragraph in text.split("\n"):
        while len(paragraph) > limit:  # a single enormous line
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        if len(current) + len(paragraph) + 1 > limit and current:
            pieces.append(current)
            current = paragraph
        else:
            current = f"{current}\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces


def _table_units(table: DocumentTable, limit: int) -> list[Unit]:
    """Split a large table into row ranges, repeating the headers in each."""
    heading = table.title or table.table_id
    budget = max(1000, limit - 500)
    units, rows, size, start = [], [], 0, 1

    def flush(end: int) -> None:
        if rows:
            part = DocumentTable(table_id=table.table_id, title=table.title, headers=table.headers, rows=rows)
            suffix = f" (rows {start}-{end})" if (start > 1 or end < len(table.rows)) else ""
            units.append(
                Unit("table", heading + suffix, table.location, part.as_text(start), table.headers, list(rows), start)
            )

    for index, row in enumerate(table.rows, start=1):
        row_size = sum(len(c) for c in row) + sum(len(h) for h in table.headers) + 20
        if size + row_size > budget and rows:
            flush(index - 1)
            rows, size, start = [], 0, index
        rows.append(row)
        size += row_size
    flush(len(table.rows))
    return units


def document_units(document: NormalizedDocument, limit: int) -> list[Unit]:
    units: list[Unit] = []
    for section in document.sections:
        if not section.text.strip() and not section.heading:
            continue
        parts = _split_text(section.text, max(1000, limit - 500))
        for number, part in enumerate(parts, start=1):
            suffix = f" (part {number} of {len(parts)})" if len(parts) > 1 else ""
            units.append(Unit("section", section.heading + suffix, section.location or section.heading, part))
    for table in document.tables:
        units.extend(_table_units(table, limit))
    return units


def chunk_document(document: NormalizedDocument, max_chars: int) -> list[Chunk]:
    """Group a document's units into chunks of at most ``max_chars``."""
    chunks: list[Chunk] = []
    current: list[Unit] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            chunks.append(
                Chunk(
                    document_id=document.document_id,
                    file_id=document.file_id,
                    file_name=document.file_name,
                    file_type=document.file_type,
                    document_title=document.document_title,
                    index=len(chunks) + 1,
                    units=current,
                )
            )
        current, size = [], 0

    for unit in document_units(document, max_chars):
        if size + len(unit) > max_chars and current:
            flush()
        current.append(unit)
        size += len(unit)
    flush()
    for chunk in chunks:
        chunk.count = len(chunks)
    return chunks
