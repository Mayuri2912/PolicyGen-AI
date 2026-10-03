"""Word (.docx): headings, paragraphs, lists and tables in document order."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.schemas.documents import DocumentSection
from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.structure import rows_to_table

_HEADING_STYLE = re.compile(r"heading\s*(\d+)", re.IGNORECASE)


@register_parser
class DocxParser(BaseParser):
    extensions = ("docx",)
    label = "Word document"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        try:
            # Parsed from memory so a failed parse cannot leave the stored file locked.
            document = docx.Document(io.BytesIO(path.read_bytes()))
        except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise ParserError("corrupted", "The Word document is corrupted and cannot be opened.") from exc
        except Exception as exc:  # noqa: BLE001 - python-docx raises library-specific errors
            raise ParserError("corrupted", "The Word document could not be read.") from exc

        result = ParseResult()
        heading, level, buffer = "Document", 1, []

        def flush() -> None:
            body = "\n".join(buffer).strip()
            if body or heading != "Document":
                result.sections.append(
                    DocumentSection(
                        section_id=f"docx-s{len(result.sections) + 1}",
                        heading=heading,
                        level=level,
                        text=body,
                        location=heading,
                    )
                )

        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if not text:
                    continue
                style = (paragraph.style.name if paragraph.style is not None else "") or ""
                match = _HEADING_STYLE.search(style)
                if style.lower() == "title" and not result.title:
                    result.title = text
                elif match:
                    flush()
                    heading, level, buffer = text, int(match.group(1)), []
                else:
                    is_list = "list" in style.lower() or child.find(".//" + qn("w:numPr")) is not None
                    buffer.append(f"- {text}" if is_list else text)
            elif child.tag == qn("w:tbl"):
                rows = [[cell.text for cell in row.cells] for row in Table(child, document).rows]
                table = rows_to_table(
                    rows,
                    table_id=f"t{len(result.tables) + 1}",
                    title=heading if heading != "Document" else "",
                    location=f"{heading} > table {len(result.tables) + 1}",
                    max_rows=ctx.settings.max_table_rows,
                )
                if table:
                    result.tables.append(table)
        flush()

        props = document.core_properties
        result.title = result.title or (props.title or "").strip() or next(
            (s.heading for s in result.sections if s.heading != "Document"), ""
        )
        result.metadata = {
            "author": props.author or "",
            "created": props.created.isoformat() if props.created else "",
            "modified": props.modified.isoformat() if props.modified else "",
            "paragraph_count": len(document.paragraphs),
        }
        return result
