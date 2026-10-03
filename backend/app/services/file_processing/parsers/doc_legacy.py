"""Word 97-2003 (.doc) text extraction.

Reads the binary format directly: the File Information Block in the
``WordDocument`` stream points at the piece table (CLX) in the table stream,
which maps character positions to runs of 8-bit or UTF-16 text.  Only text is
read; macros and embedded objects are never touched or executed.

Formatting (heading styles) is not decoded, so sections are inferred from the
text itself.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import olefile

from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.structure import split_into_sections

_FIELD_CODE = re.compile(r"\x13[^\x14\x15]*\x14|\x13[^\x15]*\x15")
_CONTROL = re.compile(r"[\x00-\x06\x08\x0e-\x1f]")


def _read_piece_table(word: bytes, table: bytes) -> str:
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)
    if lcb_clx == 0 or fc_clx + lcb_clx > len(table):
        raise ValueError("missing piece table")
    clx = table[fc_clx : fc_clx + lcb_clx]

    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:  # Prc entries precede the Pcdt
        (size,) = struct.unpack_from("<H", clx, pos + 1)
        pos += 3 + size
    if pos >= len(clx) or clx[pos] != 0x02:
        raise ValueError("piece table not found")
    (lcb,) = struct.unpack_from("<I", clx, pos + 1)
    plc = clx[pos + 5 : pos + 5 + lcb]

    count = (lcb - 4) // 12
    cps = struct.unpack_from(f"<{count + 1}I", plc, 0)
    pieces = []
    for index in range(count):
        (fc_raw,) = struct.unpack_from("<I", plc, (count + 1) * 4 + index * 8 + 2)
        length = cps[index + 1] - cps[index]
        if fc_raw & 0x40000000:  # compressed: one byte per character
            start = (fc_raw & 0x3FFFFFFF) // 2
            pieces.append(word[start : start + length].decode("cp1252", errors="replace"))
        else:
            start = fc_raw & 0x3FFFFFFF
            pieces.append(word[start : start + length * 2].decode("utf-16-le", errors="replace"))
    return "".join(pieces)


@register_parser
class DocLegacyParser(BaseParser):
    extensions = ("doc",)
    label = "Word 97-2003 document"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        try:
            ole = olefile.OleFileIO(str(path))
        except (OSError, ValueError) as exc:
            raise ParserError("corrupted", "The .doc file is corrupted or not a Word 97-2003 document.") from exc
        try:
            if not ole.exists("WordDocument"):
                raise ParserError("corrupted", "The file is not a Word 97-2003 document.")
            word = ole.openstream("WordDocument").read()
            if len(word) < 0x01AA:
                raise ParserError("corrupted", "The .doc file is truncated.")
            (flags,) = struct.unpack_from("<H", word, 0x000A)
            if flags & 0x0100:
                raise ParserError(
                    "encrypted", "The Word document is password-protected. Remove the password and upload it again."
                )
            table_name = "1Table" if flags & 0x0200 else "0Table"
            if not ole.exists(table_name):
                raise ParserError("corrupted", "The .doc file is missing its table stream.")
            table = ole.openstream(table_name).read()
            (ccp_text,) = struct.unpack_from("<I", word, 0x004C)
            try:
                text = _read_piece_table(word, table)
            except (ValueError, struct.error) as exc:
                raise ParserError("corrupted", "The text of the .doc file could not be located.") from exc
            meta = ole.get_metadata()
        finally:
            ole.close()

        # ccpText is the length of the main story; the rest is footnotes,
        # headers and other auxiliary text.
        text = text[:ccp_text] if ccp_text else text
        text = _FIELD_CODE.sub("", text).replace("\x15", "")
        text = text.replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n").replace("\x07", " | ")
        text = _CONTROL.sub("", text)
        if not text.strip():
            raise ParserError("empty", "The Word document contains no text.")

        def _meta(value) -> str:
            if isinstance(value, bytes):
                return value.decode("cp1252", errors="replace").strip()
            return str(value or "").strip()

        sections = split_into_sections(text, id_prefix="doc", default_heading="Document")
        title = _meta(meta.title) or next((s.heading for s in sections if s.heading != "Document"), "")
        return ParseResult(
            title=title,
            sections=sections,
            metadata={"author": _meta(meta.author), "format": "Word 97-2003"},
            warnings=["Legacy .doc file: text was extracted without heading styles; tables appear as ' | ' separated cells."],
        )
