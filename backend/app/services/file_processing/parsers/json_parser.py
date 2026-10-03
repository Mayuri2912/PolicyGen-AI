"""JSON: nested structures become readable sections, record lists become tables."""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.documents import DocumentSection
from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.structure import clean_cell, decode_text, rows_to_table

_MAX_DEPTH = 12


def _is_record_list(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and all(isinstance(item, dict) for item in value)
        and all(not isinstance(v, (dict, list)) for item in value for v in item.values())
    )


def _humanize(key: str) -> str:
    return str(key).replace("_", " ").replace("-", " ").strip()


@register_parser
class JsonParser(BaseParser):
    extensions = ("json",)
    label = "JSON"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        text = decode_text(path.read_bytes())
        if not text.strip():
            raise ParserError("empty", "The JSON file is empty.")
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ParserError("corrupted", "The file is not valid JSON.") from exc
        if data in ({}, [], None, ""):
            raise ParserError("empty", "The JSON file contains no data.")

        result = ParseResult()

        def add_table(key_path: str, records: list[dict]) -> None:
            headers = list(dict.fromkeys(k for record in records for k in record))
            rows = [[_humanize(h) for h in headers]] + [[clean_cell(r.get(h)) for h in headers] for r in records]
            table = rows_to_table(
                rows,
                table_id=f"t{len(result.tables) + 1}",
                title=_humanize(key_path.split(".")[-1]) or "records",
                location=f"$.{key_path}" if key_path else "$",
                max_rows=ctx.settings.max_table_rows,
            )
            if table:
                result.tables.append(table)

        def walk(value, key_path: str, lines: list[str], depth: int) -> None:
            indent = "  " * min(depth, 6)
            label = _humanize(key_path.split(".")[-1]) if key_path else "value"
            if depth > _MAX_DEPTH:
                lines.append(f"{indent}{label}: (nested too deeply to display)")
            elif _is_record_list(value):
                add_table(key_path, value)
                lines.append(f"{indent}{label}: {len(value)} records (see table '{label}').")
            elif isinstance(value, dict):
                if key_path:
                    lines.append(f"{indent}{label}:")
                for key, child in value.items():
                    walk(child, f"{key_path}.{key}" if key_path else str(key), lines, depth + 1)
            elif isinstance(value, list):
                if all(not isinstance(item, (dict, list)) for item in value):
                    lines.append(f"{indent}{label}: " + "; ".join(clean_cell(item) for item in value))
                else:
                    lines.append(f"{indent}{label}:")
                    for index, item in enumerate(value, start=1):
                        walk(item, f"{key_path}[{index}]", lines, depth + 1)
            else:
                lines.append(f"{indent}{label}: {clean_cell(value)}")

        if isinstance(data, dict):
            preamble: list[str] = []
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    lines: list[str] = []
                    walk(value, str(key), lines, 0)
                    result.sections.append(
                        DocumentSection(
                            section_id=f"json-s{len(result.sections) + 1}",
                            heading=_humanize(key),
                            text="\n".join(lines),
                            location=f"$.{key}",
                        )
                    )
                else:
                    preamble.append(f"{_humanize(key)}: {clean_cell(value)}")
            if preamble:
                result.sections.insert(
                    0, DocumentSection(section_id="json-s0", heading="Document", text="\n".join(preamble), location="$")
                )
            title_key = next((k for k in ("title", "name", "document_title") if isinstance(data.get(k), str)), None)
            result.title = data[title_key] if title_key else ""
        else:
            lines = []
            walk(data, "", lines, 0)
            result.sections.append(DocumentSection(section_id="json-s1", heading="Document", text="\n".join(lines), location="$"))

        result.metadata = {"top_level_type": type(data).__name__}
        return result
