"""Plain text and Markdown."""

from __future__ import annotations

from pathlib import Path

from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.structure import decode_text, rows_to_table, split_into_sections


def _markdown_tables(text: str, max_rows: int):
    """Pull pipe tables out of Markdown so they are also available as tables."""
    tables, block = [], []
    for line in text.splitlines() + [""]:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            block.append(stripped)
            continue
        if len(block) >= 2:
            rows = [[c.strip() for c in row.strip("|").split("|")] for row in block]
            rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
            table = rows_to_table(rows, table_id=f"t{len(tables) + 1}", location=f"table {len(tables) + 1}", max_rows=max_rows)
            if table:
                tables.append(table)
        block = []
    return tables


@register_parser
class TextParser(BaseParser):
    extensions = ("txt", "md")
    label = "Text / Markdown"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        text = decode_text(path.read_bytes())
        if not text.strip():
            raise ParserError("empty", "The file contains no text.")
        sections = split_into_sections(text, id_prefix="txt", default_heading="Document")
        title = next((s.heading for s in sections if s.heading and s.heading != "Document"), "")
        tables = _markdown_tables(text, ctx.settings.max_table_rows) if path.suffix.lower() == ".md" else []
        return ParseResult(
            title=title,
            sections=sections,
            tables=tables,
            metadata={"line_count": text.count("\n") + 1},
        )
