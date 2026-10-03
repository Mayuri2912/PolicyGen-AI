"""Upload validation: extension, declared MIME type, and file signature.

The extension decides which parser will run, so the content must actually be
of that type.  The browser-supplied MIME type is treated as a hint only (it is
client controlled and varies by operating system); the signature check on the
stored bytes is authoritative.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings

GENERIC_MIMES = {"", "application/octet-stream", "binary/octet-stream"}
OOXML_MIMES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@dataclass(frozen=True)
class FileType:
    extension: str
    label: str
    kind: str  # text | pdf | ooxml | ole | image
    mimes: frozenset[str]
    ooxml_part: str = ""
    magic: tuple[bytes, ...] = ()


def _t(ext, label, kind, mimes, ooxml_part="", magic=()):
    return FileType(ext, label, kind, frozenset(mimes), ooxml_part, magic)


_TEXT_PLAIN = {"text/plain"}
FILE_TYPES: dict[str, FileType] = {
    t.extension: t
    for t in (
        _t("txt", "Plain text", "text", _TEXT_PLAIN),
        _t("md", "Markdown", "text", _TEXT_PLAIN | {"text/markdown", "text/x-markdown"}),
        _t("csv", "CSV", "text", _TEXT_PLAIN | {"text/csv", "application/csv", "application/vnd.ms-excel"}),
        _t("json", "JSON", "text", _TEXT_PLAIN | {"application/json", "text/json"}),
        _t("xml", "XML", "text", _TEXT_PLAIN | {"application/xml", "text/xml"}),
        _t("html", "HTML", "text", _TEXT_PLAIN | {"text/html", "application/xhtml+xml"}),
        _t("htm", "HTML", "text", _TEXT_PLAIN | {"text/html", "application/xhtml+xml"}),
        _t("pdf", "PDF", "pdf", {"application/pdf", "application/x-pdf"}, magic=(b"%PDF-",)),
        _t("docx", "Word document", "ooxml", {OOXML_MIMES["docx"], "application/zip"}, "word/document.xml"),
        _t("xlsx", "Excel workbook", "ooxml", {OOXML_MIMES["xlsx"], "application/zip"}, "xl/workbook.xml"),
        _t("pptx", "PowerPoint presentation", "ooxml", {OOXML_MIMES["pptx"], "application/zip"}, "ppt/presentation.xml"),
        _t("doc", "Word 97-2003 document", "ole", {"application/msword"}),
        _t("xls", "Excel 97-2003 workbook", "ole", {"application/vnd.ms-excel", "application/msexcel"}),
        _t("ppt", "PowerPoint 97-2003 presentation", "ole", {"application/vnd.ms-powerpoint", "application/mspowerpoint"}),
        _t("png", "PNG image", "image", {"image/png"}, magic=(b"\x89PNG\r\n\x1a\n",)),
        _t("jpg", "JPEG image", "image", {"image/jpeg", "image/pjpeg"}, magic=(b"\xff\xd8\xff",)),
        _t("jpeg", "JPEG image", "image", {"image/jpeg", "image/pjpeg"}, magic=(b"\xff\xd8\xff",)),
        _t("tif", "TIFF image", "image", {"image/tiff"}, magic=(b"II*\x00", b"MM\x00*")),
        _t("tiff", "TIFF image", "image", {"image/tiff"}, magic=(b"II*\x00", b"MM\x00*")),
    )
}

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")


class UploadRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def supported_extensions() -> list[str]:
    return sorted(FILE_TYPES)


def extension_of(file_name: str) -> str:
    return file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""


def check_extension(file_name: str) -> FileType:
    extension = extension_of(file_name)
    file_type = FILE_TYPES.get(extension)
    if file_type is None:
        shown = f".{extension}" if extension else "no extension"
        raise UploadRejected(
            "unsupported_type",
            f"Unsupported file type ({shown}). Supported formats: {', '.join(e.upper() for e in supported_extensions())}.",
        )
    return file_type


def check_declared_mime(file_type: FileType, declared: str) -> None:
    declared = (declared or "").split(";")[0].strip().lower()
    if declared in GENERIC_MIMES or declared in file_type.mimes:
        return
    # Text formats are reported inconsistently across browsers and platforms;
    # any text/* declaration is acceptable for them.
    if file_type.kind == "text" and declared.startswith("text/"):
        return
    raise UploadRejected(
        "mime_mismatch",
        f"The file was sent as '{declared}', which does not match a .{file_type.extension} file.",
    )


def check_signature(path: Path, file_type: FileType, settings: Settings) -> None:
    """Verify that the stored bytes really are of the claimed type."""
    with path.open("rb") as handle:
        head = handle.read(8192)

    if not head.strip(b"\x00 \t\r\n"):
        raise UploadRejected("empty_file", "The file is empty.")

    kind = file_type.kind
    if kind == "pdf":
        if b"%PDF-" not in head[:1024]:
            raise UploadRejected("signature_mismatch", "The file is not a valid PDF (missing PDF header).")
    elif kind == "image":
        if not head.startswith(file_type.magic):
            raise UploadRejected("signature_mismatch", f"The file is not a valid {file_type.label}.")
    elif kind == "ole":
        if not head.startswith(OLE_MAGIC):
            raise UploadRejected(
                "signature_mismatch",
                f"The file is not a valid {file_type.label}. If it was saved in the newer format, rename it to .{file_type.extension}x.",
            )
    elif kind == "ooxml":
        if head.startswith(OLE_MAGIC):
            raise UploadRejected(
                "encrypted",
                "The file is password-protected or encrypted (or is a legacy Office file with a new-format extension). "
                "Remove the password and upload it again.",
            )
        if not head.startswith(ZIP_MAGIC):
            raise UploadRejected("signature_mismatch", f"The file is not a valid {file_type.label}.")
        _check_ooxml_container(path, file_type, settings)
    elif kind == "text":
        if head.startswith((OLE_MAGIC, b"%PDF-", *ZIP_MAGIC)) or (
            b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff"))
        ):
            raise UploadRejected(
                "signature_mismatch",
                f"The file contains binary data and is not a valid {file_type.label} file.",
            )


def _check_ooxml_container(path: Path, file_type: FileType, settings: Settings) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            total = sum(info.file_size for info in infos)
    except zipfile.BadZipFile:
        raise UploadRejected("corrupted", f"The {file_type.label} is corrupted and cannot be opened.") from None

    if file_type.ooxml_part not in names:
        raise UploadRejected(
            "signature_mismatch",
            f"The file is a ZIP archive but not a valid {file_type.label}.",
        )
    limit = settings.max_archive_uncompressed_mb * 1024 * 1024
    if total > limit:
        # Guards against decompression bombs before any parser touches the file.
        raise UploadRejected(
            "too_large",
            f"The file expands to more than {settings.max_archive_uncompressed_mb} MB when unpacked and was rejected.",
        )
