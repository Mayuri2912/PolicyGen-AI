"""PowerPoint (.pptx): one section per slide, with tables and speaker notes."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.schemas.documents import DocumentSection
from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.structure import clean_cell, rows_to_table


def _iter_shapes(shapes):
    for shape in shapes:
        if shape.shape_type == 6 and hasattr(shape, "shapes"):  # group
            yield from _iter_shapes(shape.shapes)
        else:
            yield shape


@register_parser
class PptxParser(BaseParser):
    extensions = ("pptx",)
    label = "PowerPoint presentation"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        from pptx import Presentation

        try:
            presentation = Presentation(io.BytesIO(path.read_bytes()))
        except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise ParserError("corrupted", "The presentation is corrupted and cannot be opened.") from exc
        except Exception as exc:  # noqa: BLE001 - python-pptx raises library-specific errors
            raise ParserError("corrupted", "The presentation could not be read.") from exc

        result = ParseResult(metadata={"slide_count": len(presentation.slides)})
        for number, slide in enumerate(presentation.slides, start=1):
            title_shape = slide.shapes.title
            title = clean_cell(title_shape.text) if title_shape is not None and title_shape.has_text_frame else ""
            lines: list[str] = []
            for shape in _iter_shapes(slide.shapes):
                if getattr(shape, "has_table", False) and shape.has_table:
                    rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
                    table = rows_to_table(
                        rows,
                        table_id=f"t{len(result.tables) + 1}",
                        title=title,
                        location=f"slide {number}",
                        max_rows=ctx.settings.max_table_rows,
                    )
                    if table:
                        result.tables.append(table)
                elif shape.has_text_frame and shape is not title_shape:
                    for paragraph in shape.text_frame.paragraphs:
                        text = clean_cell("".join(run.text for run in paragraph.runs) or paragraph.text)
                        if text:
                            lines.append(f"{'  ' * paragraph.level}- {text}")
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    lines.append(f"Speaker notes: {notes}")
            if title or lines:
                result.sections.append(
                    DocumentSection(
                        section_id=f"slide-{number}",
                        heading=title or f"Slide {number}",
                        text="\n".join(lines),
                        location=f"slide {number}",
                    )
                )

        if not result.sections and not result.tables:
            raise ParserError("empty", "The presentation contains no text.")
        result.title = (presentation.core_properties.title or "").strip() or (
            result.sections[0].heading if result.sections else ""
        )
        return result
