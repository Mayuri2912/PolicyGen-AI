"""PDF: text and metadata via pypdf, tables via pdfplumber, OCR for scans."""

from __future__ import annotations

import io

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.logging import get_logger
from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.ocr import OCR_UNAVAILABLE_HINT, get_ocr_engine
from app.services.file_processing.structure import clean_extracted_text, reflow_lines, rows_to_table, split_into_sections

logger = get_logger("parser.pdf")

# Below this many characters per page on average, the PDF is treated as scanned.
_SCANNED_THRESHOLD = 25
_MAX_TABLE_PAGES = 60
_MAX_OCR_PAGES = 40


@register_parser
class PdfParser(BaseParser):
    extensions = ("pdf",)
    label = "PDF"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        try:
            reader = PdfReader(io.BytesIO(path.read_bytes()))
            if reader.is_encrypted:
                # Some PDFs are "encrypted" with an empty user password only to
                # restrict printing; those can still be read.
                if not reader.decrypt(""):
                    raise ParserError(
                        "encrypted",
                        "The PDF is password-protected. Remove the password and upload it again.",
                    )
            page_count = len(reader.pages)
        except ParserError:
            raise
        except (PdfReadError, ValueError, OSError, KeyError, AttributeError, TypeError) as exc:
            raise ParserError("corrupted", "The PDF is corrupted or not a valid PDF file.") from exc

        if page_count == 0:
            raise ParserError("empty", "The PDF has no pages.")

        result = ParseResult(metadata={"page_count": page_count})
        limit = min(page_count, ctx.settings.max_pdf_pages)
        if page_count > limit:
            result.warnings.append(f"Only the first {limit} of {page_count} pages were processed (configured limit).")

        page_texts: list[str] = []
        for index in range(limit):
            try:
                page_texts.append(reader.pages[index].extract_text() or "")
            except Exception:  # noqa: BLE001 - one malformed page must not sink the file
                page_texts.append("")
                result.warnings.append(f"Page {index + 1} could not be read and was skipped.")

        total_chars = sum(len(t.strip()) for t in page_texts)
        if total_chars < _SCANNED_THRESHOLD * limit:
            page_texts = self._ocr_pages(path, limit, result)
            result.metadata["ocr_applied"] = True

        if not result.metadata.get("ocr_applied"):
            # Table cells are extracted as tables, so they are left out of the
            # page text to avoid reporting the same content twice.
            result.tables, text_outside_tables = self._tables(path, limit, ctx)
            for number, text in text_outside_tables.items():
                page_texts[number - 1] = text

        for number, text in enumerate(page_texts, start=1):
            if text.strip():
                result.sections.extend(
                    split_into_sections(
                        reflow_lines(clean_extracted_text(text)), id_prefix=f"p{number}", location=f"page {number}", default_heading=f"Page {number}"
                    )
                )

        info = reader.metadata or {}
        result.title = str(info.get("/Title") or "").strip()
        if not result.title:
            result.title = next((s.heading for s in result.sections if not s.heading.startswith("Page ")), "")
        for key, label in (("/Author", "author"), ("/Creator", "creator"), ("/CreationDate", "created")):
            if info.get(key):
                result.metadata[label] = str(info.get(key))

        return result

    @staticmethod
    def _tables(path: Path, page_limit: int, ctx: ParseContext):
        """Return the tables found and, for pages with tables, the text outside them."""
        import pdfplumber

        tables, outside_text = [], {}
        try:
            with pdfplumber.open(str(path)) as pdf:
                for number, page in enumerate(pdf.pages[: min(page_limit, _MAX_TABLE_PAGES)], start=1):
                    boxes = []
                    for found in page.find_tables():
                        table = rows_to_table(
                            found.extract(),
                            table_id=f"t{len(tables) + 1}",
                            location=f"page {number}",
                            max_rows=ctx.settings.max_table_rows,
                        )
                        if table and len(table.headers) > 1:
                            tables.append(table)
                            boxes.append(found.bbox)
                    if boxes:

                        def outside(obj, boxes=boxes) -> bool:
                            if obj.get("object_type") != "char":
                                return True
                            x, y = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
                            return not any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)

                        outside_text[number] = page.filter(outside).extract_text() or ""
        except Exception:  # noqa: BLE001 - tables are an enhancement over the text
            logger.info("pdf table extraction skipped")
            return [], {}
        return tables, outside_text

    @staticmethod
    def _ocr_pages(path: Path, limit: int, result: ParseResult) -> list[str]:
        engine = get_ocr_engine()
        if engine is None:
            raise ParserError(
                "ocr_unavailable",
                "The PDF appears to be scanned (it contains no selectable text). " + OCR_UNAVAILABLE_HINT,
            )
        import pypdfium2 as pdfium

        texts: list[str] = []
        document = pdfium.PdfDocument(str(path))
        try:
            pages = min(limit, _MAX_OCR_PAGES)
            if limit > pages:
                result.warnings.append(f"OCR was applied to the first {pages} pages only.")
            for index in range(pages):
                image = document[index].render(scale=2).to_pil()
                texts.append(engine.image_to_text(image))
        finally:
            document.close()
        result.metadata["ocr_engine"] = engine.name
        if not any(t.strip() for t in texts):
            raise ParserError("empty", "No text could be recognized in the scanned PDF.")
        return texts
