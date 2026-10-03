"""XML, parsed with defusedxml so entity expansion and external entities are blocked."""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from app.schemas.documents import DocumentSection
from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.structure import clean_cell, rows_to_table

_MAX_DEPTH = 15


def _tag(element: Element) -> str:
    tag = element.tag if isinstance(element.tag, str) else "node"
    return tag.split("}", 1)[-1].replace("_", " ").replace("-", " ")


def _is_leaf(element: Element) -> bool:
    return len(element) == 0


def _repeated_records(element: Element) -> bool:
    children = list(element)
    return (
        len(children) >= 2
        and len({child.tag for child in children}) == 1
        and all(len(child) > 0 and all(_is_leaf(g) for g in child) for child in children)
    )


@register_parser
class XmlParser(BaseParser):
    extensions = ("xml",)
    label = "XML"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        raw = path.read_bytes()
        if not raw.strip():
            raise ParserError("empty", "The XML file is empty.")
        try:
            root = fromstring(raw)
        except DefusedXmlException as exc:
            raise ParserError(
                "unsupported", "The XML file uses entity or DTD features that are blocked for security reasons."
            ) from exc
        except ParseError as exc:
            raise ParserError("corrupted", "The file is not well-formed XML.") from exc

        result = ParseResult(metadata={"root_element": _tag(root)})

        def add_table(element: Element, location: str) -> None:
            children = list(element)
            headers = list(dict.fromkeys(_tag(g) for child in children for g in child))
            rows = [headers]
            for child in children:
                values = {_tag(g): clean_cell(g.text) for g in child}
                rows.append([values.get(h, "") for h in headers])
            table = rows_to_table(
                rows,
                table_id=f"t{len(result.tables) + 1}",
                title=_tag(element),
                location=location,
                max_rows=ctx.settings.max_table_rows,
            )
            if table:
                result.tables.append(table)

        def walk(element: Element, lines: list[str], depth: int, location: str) -> None:
            indent = "  " * min(depth, 6)
            name = _tag(element)
            attrs = "; ".join(f"{k.split('}', 1)[-1]}={v}" for k, v in element.attrib.items())
            text = clean_cell(element.text)
            if depth > _MAX_DEPTH:
                return
            if _repeated_records(element):
                add_table(element, location)
                lines.append(f"{indent}{name}: {len(element)} records (see table '{name}').")
                return
            if _is_leaf(element):
                value = text or attrs
                if value:
                    lines.append(f"{indent}{name}: {value}" + (f" ({attrs})" if text and attrs else ""))
                return
            lines.append(f"{indent}{name}" + (f" ({attrs})" if attrs else "") + (f": {text}" if text else ""))
            for child in element:
                walk(child, lines, depth + 1, f"{location}/{child.tag.split('}', 1)[-1] if isinstance(child.tag, str) else 'node'}")

        root_path = f"/{root.tag.split('}', 1)[-1]}"
        loose: list[str] = []
        for child in root:
            child_path = f"{root_path}/{child.tag.split('}', 1)[-1] if isinstance(child.tag, str) else 'node'}"
            if _is_leaf(child):
                walk(child, loose, 0, child_path)
                continue
            lines: list[str] = []
            walk(child, lines, 0, child_path)
            if lines:
                result.sections.append(
                    DocumentSection(
                        section_id=f"xml-s{len(result.sections) + 1}",
                        heading=_tag(child),
                        text="\n".join(lines),
                        location=child_path,
                    )
                )
        if _repeated_records(root):
            result.sections.clear()
            add_table(root, root_path)
            loose.append(f"{_tag(root)}: {len(root)} records.")
        if _is_leaf(root) and clean_cell(root.text):
            loose.append(clean_cell(root.text))
        if loose:
            result.sections.insert(
                0, DocumentSection(section_id="xml-s0", heading=_tag(root), text="\n".join(loose), location=root_path)
            )
        if not result.sections and not result.tables:
            raise ParserError("empty", "The XML file contains no text content.")
        title = root.find("{*}title") if hasattr(root, "find") else None
        result.title = clean_cell(title.text) if title is not None and title.text else ""
        return result
