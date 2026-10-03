"""The normalized document representation shared by every parser.

Whatever the input format, extraction ends in a ``NormalizedDocument``.  Each
section and table records a human-readable ``location`` (page, worksheet,
slide, heading path) so later stages can trace a finding back to its source.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentSection(BaseModel):
    section_id: str
    heading: str = ""
    level: int = 1
    text: str = ""
    location: str = ""


class DocumentTable(BaseModel):
    table_id: str
    title: str = ""
    location: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    truncated: bool = False

    def row_text(self, row: list[str]) -> str:
        if self.headers and len(self.headers) == len(row):
            return "; ".join(f"{h}: {v}" for h, v in zip(self.headers, row) if v)
        return " | ".join(row)

    def as_text(self, start_row: int = 1) -> str:
        """Render the table as labelled rows so it is analyzable as prose."""
        lines: list[str] = []
        if self.title:
            lines.append(f"Table: {self.title}")
        if self.headers:
            lines.append(" | ".join(self.headers))
        for index, row in enumerate(self.rows, start=start_row):
            lines.append(f"Row {index}: {self.row_text(row)}")
        if self.truncated:
            lines.append("(table truncated at the configured row limit)")
        return "\n".join(lines)


class NormalizedDocument(BaseModel):
    document_id: str
    file_id: str
    file_name: str
    file_type: str
    document_title: str = ""
    extracted_text: str = ""
    sections: list[DocumentSection] = Field(default_factory=list)
    tables: list[DocumentTable] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
