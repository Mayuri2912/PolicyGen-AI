"""HTML: visible text only.  Scripts, styles and embedded frames are discarded, never run."""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from app.schemas.documents import DocumentSection
from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.structure import clean_cell, decode_text, rows_to_table

_DROP = ("script", "style", "noscript", "template", "iframe", "object", "embed", "svg", "head", "form")
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_BLOCKS = ("p", "li", "blockquote", "pre", "dt", "dd", "figcaption")


@register_parser
class HtmlParser(BaseParser):
    extensions = ("html", "htm")
    label = "HTML"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        markup = decode_text(path.read_bytes())
        if not markup.strip():
            raise ParserError("empty", "The HTML file is empty.")
        soup = BeautifulSoup(markup, "html.parser")
        title = clean_cell(soup.title.string) if soup.title and soup.title.string else ""
        for tag in soup.find_all(_DROP):
            tag.decompose()

        result = ParseResult(title=title)
        for index, table_tag in enumerate(soup.find_all("table"), start=1):
            if table_tag.find_parent("table") is not None:
                continue
            rows = [
                [clean_cell(cell.get_text(" ")) for cell in row.find_all(["th", "td"])]
                for row in table_tag.find_all("tr")
            ]
            caption = table_tag.find("caption")
            table = rows_to_table(
                rows,
                table_id=f"t{index}",
                title=clean_cell(caption.get_text(" ")) if caption else "",
                location=f"table {index}",
                max_rows=ctx.settings.max_table_rows,
            )
            if table:
                result.tables.append(table)
            table_tag.decompose()

        heading, level, buffer = "Document", 1, []

        def flush() -> None:
            body = "\n".join(buffer).strip()
            if body or heading != "Document":
                result.sections.append(
                    DocumentSection(
                        section_id=f"html-s{len(result.sections) + 1}",
                        heading=heading,
                        level=level,
                        text=body,
                        location=heading,
                    )
                )

        body = soup.body or soup
        for element in body.descendants:
            if isinstance(element, Tag):
                if element.name in _HEADINGS:
                    flush()
                    heading, level, buffer = clean_cell(element.get_text(" ")) or "Untitled", int(element.name[1]), []
                elif element.name in _BLOCKS and not element.find(_BLOCKS):
                    text = clean_cell(element.get_text(" "))
                    if text:
                        buffer.append(f"- {text}" if element.name == "li" else text)
            elif isinstance(element, NavigableString):
                parent = element.parent
                # Loose text directly inside containers (div, td, body...).
                if parent is not None and parent.name not in (*_HEADINGS, *_BLOCKS) and not parent.find_parent(
                    [*_HEADINGS, *_BLOCKS]
                ):
                    text = clean_cell(str(element))
                    if text:
                        buffer.append(text)
        flush()

        if not result.sections and not result.tables:
            raise ParserError("empty", "The HTML file contains no visible text.")
        result.title = result.title or next((s.heading for s in result.sections if s.heading != "Document"), "")
        return result
