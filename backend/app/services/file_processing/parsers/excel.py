"""Excel workbooks: .xlsx via openpyxl, .xls via xlrd."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.schemas.documents import DocumentSection
from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.structure import clean_cell, rows_to_table

_MAX_COLUMNS = 60


def _build(sheets: list[tuple[str, list[list[str]]]], ctx: ParseContext, extra_meta: dict) -> ParseResult:
    result = ParseResult(metadata={"sheet_names": [name for name, _ in sheets], **extra_meta})
    for name, rows in sheets:
        table = rows_to_table(
            rows,
            table_id=f"t{len(result.tables) + 1}",
            title=name,
            location=f"sheet '{name}'",
            max_rows=ctx.settings.max_table_rows,
        )
        if table is None:
            continue
        if table.truncated:
            result.warnings.append(f"Sheet '{name}' was truncated at {ctx.settings.max_table_rows} rows.")
        result.tables.append(table)
        result.sections.append(
            DocumentSection(
                section_id=f"sheet-{len(result.sections) + 1}",
                heading=name,
                level=1,
                text=f"Worksheet '{name}' with {len(table.rows)} data rows and columns: {', '.join(table.headers)}.",
                location=f"sheet '{name}'",
            )
        )
    if not result.tables:
        raise ParserError("empty", "The workbook contains no data.")
    return result


@register_parser
class XlsxParser(BaseParser):
    extensions = ("xlsx",)
    label = "Excel workbook"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        import openpyxl

        try:
            # data_only reads the cached results of formulas; read_only streams
            # rows so large sheets do not load fully into memory.
            workbook = openpyxl.load_workbook(io.BytesIO(path.read_bytes()), read_only=True, data_only=True)
        except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise ParserError("corrupted", "The Excel workbook is corrupted and cannot be opened.") from exc
        except Exception as exc:  # noqa: BLE001 - openpyxl raises library-specific errors
            raise ParserError("corrupted", "The Excel workbook could not be read.") from exc

        sheets = []
        try:
            for sheet in workbook.worksheets:
                rows = []
                for row in sheet.iter_rows(values_only=True):
                    rows.append([clean_cell(v) for v in row[:_MAX_COLUMNS]])
                    if len(rows) > ctx.settings.max_table_rows + 50:
                        break
                sheets.append((sheet.title, rows))
            props = workbook.properties
            meta = {"author": (props.creator or "") if props else "", "title": (props.title or "") if props else ""}
        finally:
            workbook.close()
        result = _build(sheets, ctx, {"author": meta["author"]})
        result.title = meta["title"]
        return result


@register_parser
class XlsParser(BaseParser):
    extensions = ("xls",)
    label = "Excel 97-2003 workbook"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        import xlrd

        try:
            workbook = xlrd.open_workbook(file_contents=path.read_bytes(), on_demand=True)
        except xlrd.biffh.XLRDError as exc:
            if "encrypt" in str(exc).lower() or "password" in str(exc).lower():
                raise ParserError(
                    "encrypted", "The workbook is password-protected. Remove the password and upload it again."
                ) from exc
            raise ParserError("corrupted", "The .xls workbook is corrupted or not a valid Excel 97-2003 file.") from exc
        except Exception as exc:  # noqa: BLE001 - xlrd raises assorted errors on malformed input
            raise ParserError("corrupted", "The .xls workbook could not be read.") from exc

        sheets = []
        try:
            for sheet in workbook.sheets():
                rows = []
                for index in range(min(sheet.nrows, ctx.settings.max_table_rows + 50)):
                    rows.append([clean_cell(v) for v in sheet.row_values(index)[:_MAX_COLUMNS]])
                sheets.append((sheet.name, rows))
        finally:
            workbook.release_resources()
        return _build(sheets, ctx, {"format": "Excel 97-2003"})
