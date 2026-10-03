"""CSV (comma, semicolon, tab or pipe separated)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from app.schemas.documents import DocumentSection
from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.structure import decode_text, rows_to_table


@register_parser
class CsvParser(BaseParser):
    extensions = ("csv",)
    label = "CSV"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        text = decode_text(path.read_bytes())
        if not text.strip():
            raise ParserError("empty", "The CSV file contains no data.")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        try:
            rows = []
            for row in csv.reader(io.StringIO(text), dialect):
                rows.append(row)
                if len(rows) > ctx.settings.max_table_rows + 1:
                    break
        except csv.Error as exc:
            raise ParserError("corrupted", "The CSV file is malformed and could not be parsed.") from exc

        table = rows_to_table(rows, table_id="t1", title=ctx.file_name, location="rows", max_rows=ctx.settings.max_table_rows)
        if table is None:
            raise ParserError("empty", "The CSV file contains no data.")
        result = ParseResult(
            tables=[table],
            sections=[
                DocumentSection(
                    section_id="csv-s1",
                    heading="Data",
                    text=f"CSV data with {len(table.rows)} rows and columns: {', '.join(table.headers)}.",
                    location="rows",
                )
            ],
            metadata={"row_count": len(table.rows), "column_count": len(table.headers), "delimiter": dialect.delimiter},
        )
        if table.truncated:
            result.warnings.append(f"The file was truncated at {ctx.settings.max_table_rows} rows.")
        return result
