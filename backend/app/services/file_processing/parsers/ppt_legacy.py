"""PowerPoint 97-2003 (.ppt) text extraction.

Walks the record tree of the ``PowerPoint Document`` stream and collects the
text atoms.  Text is taken from each Slide container (where current versions
of PowerPoint store it) and, for older files, from the slide list in the
Document container.  Master slides are skipped because they only hold layout
boilerplate.  Nothing is executed.
"""

from __future__ import annotations

import struct
from pathlib import Path

import olefile

from app.schemas.documents import DocumentSection
from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser

_SLIDE = 0x03EE
_NOTES = 0x03F0
_MAIN_MASTER = 0x03F8
_SLIDE_PERSIST_ATOM = 0x03F3
_TEXT_CHARS_ATOM = 0x0FA0  # UTF-16LE
_TEXT_BYTES_ATOM = 0x0FA8  # 8-bit
_SLIDE_LIST_WITH_TEXT = 0x0FF0
_MAX_DEPTH = 30


class _Collected:
    def __init__(self) -> None:
        self.slides: list[list[str]] = []  # text found inside Slide containers
        self.outline: list[list[str]] = []  # text found in the slide list (older files)
        self.notes: list[str] = []


def _walk(data: bytes, start: int, end: int, depth: int, out: _Collected, target: list[str] | None, in_outline: bool) -> None:
    pos = start
    while pos + 8 <= end:
        ver_instance, rec_type, rec_len = struct.unpack_from("<HHI", data, pos)
        body_start, body_end = pos + 8, pos + 8 + rec_len
        if body_end > end:
            break
        is_container = (ver_instance & 0x000F) == 0x000F
        if is_container and depth < _MAX_DEPTH:
            if rec_type == _MAIN_MASTER:
                pass
            elif rec_type == _SLIDE:
                out.slides.append([])
                _walk(data, body_start, body_end, depth + 1, out, out.slides[-1], False)
            elif rec_type == _NOTES:
                _walk(data, body_start, body_end, depth + 1, out, out.notes, False)
            elif rec_type == _SLIDE_LIST_WITH_TEXT:
                # Instance 0 is the slide list; 1 is masters and 2 is notes.
                if (ver_instance >> 4) == 0:
                    _walk(data, body_start, body_end, depth + 1, out, None, True)
            else:
                _walk(data, body_start, body_end, depth + 1, out, target, in_outline)
        elif rec_type == _SLIDE_PERSIST_ATOM and in_outline:
            out.outline.append([])
        elif rec_type in (_TEXT_CHARS_ATOM, _TEXT_BYTES_ATOM):
            raw = data[body_start:body_end]
            text = raw.decode("utf-16-le" if rec_type == _TEXT_CHARS_ATOM else "cp1252", errors="replace")
            text = text.replace("\r", "\n").replace("\x0b", "\n").strip()
            if text and text != "*":
                if in_outline and out.outline:
                    out.outline[-1].append(text)
                elif target is not None:
                    target.append(text)
        pos = body_end


@register_parser
class PptLegacyParser(BaseParser):
    extensions = ("ppt",)
    label = "PowerPoint 97-2003 presentation"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        try:
            ole = olefile.OleFileIO(str(path))
        except (OSError, ValueError) as exc:
            raise ParserError("corrupted", "The .ppt file is corrupted or not a PowerPoint 97-2003 presentation.") from exc
        try:
            if ole.exists("EncryptedSummary"):
                raise ParserError(
                    "encrypted", "The presentation is password-protected. Remove the password and upload it again."
                )
            if not ole.exists("PowerPoint Document"):
                raise ParserError("corrupted", "The file is not a PowerPoint 97-2003 presentation.")
            data = ole.openstream("PowerPoint Document").read()
            meta = ole.get_metadata()
        finally:
            ole.close()

        collected = _Collected()
        try:
            _walk(data, 0, len(data), 0, collected, None, False)
        except struct.error as exc:
            raise ParserError("corrupted", "The .ppt file is damaged and could not be read.") from exc

        slides = collected.slides if any(collected.slides) else collected.outline
        result = ParseResult(metadata={"slide_count": len(slides), "format": "PowerPoint 97-2003"})
        for number, texts in enumerate(slides, start=1):
            if not texts:
                continue
            first_lines = texts[0].split("\n")
            heading = first_lines[0][:120]
            lines = first_lines[1:] + [line for text in texts[1:] for line in text.split("\n")]
            body = "\n".join(f"- {line.strip()}" for line in lines if line.strip())
            result.sections.append(
                DocumentSection(section_id=f"slide-{number}", heading=heading, text=body, location=f"slide {number}")
            )
        if collected.notes:
            result.sections.append(
                DocumentSection(
                    section_id="slide-notes",
                    heading="Speaker notes",
                    text="\n".join(dict.fromkeys(collected.notes)),
                    location="speaker notes",
                )
            )
        if not result.sections:
            raise ParserError("empty", "The presentation contains no text.")

        title = meta.title
        if isinstance(title, bytes):
            title = title.decode("cp1252", errors="replace")
        result.title = (title or "").strip() or result.sections[0].heading
        result.warnings.append(
            "Legacy .ppt file: text was extracted per slide; tables are flattened and slide order follows the file's storage order."
        )
        return result
