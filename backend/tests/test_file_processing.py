"""Module 1 - input file processing: validation, extraction, normalization."""

from __future__ import annotations

import io
import time
import zipfile

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.core.security import sanitize_filename
from app.services.file_processing.base import BaseParser, ParseResult, get_parser, register_parser, registered_extensions
from app.services.file_processing.ocr import get_ocr_engine
from app.services.file_processing.structure import clean_extracted_text, reflow_lines, rows_to_table, split_into_sections
from tests.conftest import FIXTURES, sample, upload, upload_ok


def content(client, file_id: str) -> dict:
    response = client.get(f"/api/files/{file_id}/content")
    assert response.status_code == 200, response.text
    return response.json()


def full_text(doc: dict) -> str:
    parts = [f"{s['heading']}\n{s['text']}" for s in doc["sections"]]
    parts += [" ".join(t["headers"]) + "\n" + "\n".join(" ".join(r) for r in t["rows"]) for t in doc["tables"]]
    return "\n".join(parts)


# ------------------------------------------------------------ per format
def test_txt_extraction_detects_headings_and_text(client):
    (file_id,) = upload_ok(client, sample("sample_security.txt"))
    doc = content(client, file_id)
    headings = [s["heading"] for s in doc["sections"]]
    assert "3. Access Control" in headings and "6. Incident Management" in headings
    assert "Passwords must be at least 14 characters long" in full_text(doc)
    assert doc["file"]["document"]["metadata"]["extraction_status"] == "completed"
    assert doc["file"]["document"]["metadata"]["upload_timestamp"]


def test_pdf_extraction_text_tables_and_page_locations(client):
    (file_id,) = upload_ok(client, sample("sample_cloud_security.pdf"))
    doc = content(client, file_id)
    text = full_text(doc)
    assert doc["file"]["document"]["metadata"]["page_count"] >= 1
    assert doc["document_title"] == "Cloud Security Requirements and Assessment"
    # Wrapped lines are rejoined into whole sentences.
    assert "Storage buckets must not be publicly accessible unless the CISO has approved a documented exception." in text
    assert all(s["location"].startswith("page ") for s in doc["sections"])
    (table,) = doc["tables"]
    assert table["headers"] == ["Finding ID", "Area", "Finding", "Severity", "Status"]
    assert table["row_count"] == 5
    # Table cells are not repeated in the page text, and unmapped bullet glyphs are cleaned.
    assert "CF-02" not in "\n".join(s["text"] for s in doc["sections"])
    assert "(cid:" not in text and "\x7f" not in text


def test_docx_extraction_headings_lists_tables_metadata(client):
    (file_id,) = upload_ok(client, sample("sample_vendor_assessment.docx"))
    doc = content(client, file_id)
    assert doc["document_title"] == "Vendor Security Assessment Summary"
    assert [s["heading"] for s in doc["sections"]][:3] == ["Document", "Purpose", "Vendor Requirements"]
    requirements = next(s for s in doc["sections"] if s["heading"] == "Vendor Requirements")
    assert requirements["text"].startswith("- Every vendor")
    (table,) = doc["tables"]
    assert table["headers"][0] == "Vendor" and table["row_count"] == 6
    assert "Vendor Register" in table["location"]
    assert doc["file"]["document"]["metadata"]["author"]


def test_xlsx_extraction_sheets_headers_and_cells(client):
    (file_id,) = upload_ok(client, sample("sample_risk_assessment.xlsx"))
    doc = content(client, file_id)
    assert doc["file"]["document"]["metadata"]["sheet_names"] == ["Risk Register", "Scoring Guide"]
    register = doc["tables"][0]
    assert register["title"] == "Risk Register" and register["location"] == "sheet 'Risk Register'"
    assert register["headers"][:2] == ["Risk ID", "Risk Title"]
    assert register["row_count"] == 12
    assert register["rows"][0][0] == "R-01" and register["rows"][0][4] == "4"  # numeric cell kept as a clean integer


def test_csv_extraction(client):
    (file_id,) = upload_ok(client, sample("sample_access_records.csv"))
    doc = content(client, file_id)
    (table,) = doc["tables"]
    assert table["headers"][0] == "User ID" and "MFA Enabled" in table["headers"]
    assert table["row_count"] == 20
    assert doc["file"]["document"]["metadata"]["delimiter"] == ","


def test_csv_with_semicolons_and_bom(client):
    data = "\ufeffName;Role;Active\nAsha;Admin;Yes\nBen;User;No\n".encode("utf-8")
    (file_id,) = upload_ok(client, ("people.csv", data, "text/csv"))
    (table,) = content(client, file_id)["tables"]
    assert table["headers"] == ["Name", "Role", "Active"] and table["rows"][1] == ["Ben", "User", "No"]


def test_json_extraction_builds_sections_and_tables(client):
    (file_id,) = upload_ok(client, sample("sample_incident_log.json"))
    doc = content(client, file_id)
    assert doc["document_title"] == "Security Incident Log 2026"
    (table,) = doc["tables"]
    assert table["location"] == "$.incidents" and table["row_count"] == 4
    assert "reported to the CISO within 24 hours" in full_text(doc)


def test_xml_extraction(client):
    (file_id,) = upload_ok(client, sample("sample_asset_inventory.xml"))
    doc = content(client, file_id)
    assert doc["document_title"] == "Asset Inventory Extract"
    (table,) = doc["tables"]
    assert table["headers"] == ["id", "name", "type", "classification", "owner", "support status"]
    assert table["row_count"] == 5
    assert "Every asset must have a named owner" in full_text(doc)


def test_html_extraction_ignores_scripts(client):
    (file_id,) = upload_ok(client, sample("sample_backup_procedure.html"))
    doc = content(client, file_id)
    text = full_text(doc)
    assert doc["document_title"] == "Backup and Recovery Procedure"
    assert "Production databases must be backed up daily." in text
    assert "console.log" not in text and "font-family" not in text
    assert doc["tables"][0]["headers"] == ["System", "RTO", "RPO", "Last Restore Test"]


def test_markdown_extraction(client):
    (file_id,) = upload_ok(client, sample("sample_remote_work_guidelines.md"))
    doc = content(client, file_id)
    assert doc["document_title"] == "Remote Work Guidelines"
    assert {"Remote Access", "Devices"} <= {s["heading"] for s in doc["sections"]}
    assert doc["tables"][0]["headers"] == ["Control", "Status", "Owner"]


def test_pptx_extraction_slides_tables(client):
    (file_id,) = upload_ok(client, sample("sample_security_awareness.pptx"))
    doc = content(client, file_id)
    assert doc["file"]["document"]["metadata"]["slide_count"] == 5
    assert [s["location"] for s in doc["sections"]][:2] == ["slide 1", "slide 2"]
    assert "All employees must complete security awareness training annually." in full_text(doc)
    assert doc["tables"][0]["headers"] == ["Department", "Completion", "Overdue staff"]


def test_legacy_doc_extraction(client):
    """Real Word 97-2003 file saved by Microsoft Word."""
    (file_id,) = upload_ok(client, FIXTURES / "legacy_vendor_assessment.doc")
    doc = content(client, file_id)
    text = full_text(doc)
    assert doc["document_title"] == "Vendor Security Assessment Summary"
    assert "A data processing agreement must be signed with any vendor that processes personal data." in text
    assert "Northpeak Payroll Services" in text
    assert any("Legacy .doc" in w for w in doc["file"]["warnings"])


def test_legacy_xls_extraction(client):
    """Real Excel 97-2003 file saved by Microsoft Excel."""
    (file_id,) = upload_ok(client, FIXTURES / "legacy_risk_assessment.xls")
    doc = content(client, file_id)
    assert doc["file"]["document"]["metadata"]["sheet_names"] == ["Risk Register", "Scoring Guide"]
    assert doc["tables"][0]["rows"][0][:2] == ["R-01", "Unauthorized access through shared engineering accounts"]


def test_legacy_ppt_extraction(client):
    """Real PowerPoint 97-2003 file saved by Microsoft PowerPoint."""
    (file_id,) = upload_ok(client, FIXTURES / "legacy_security_awareness.ppt")
    doc = content(client, file_id)
    headings = [s["heading"] for s in doc["sections"]]
    assert headings[:3] == ["Security Awareness Programme 2026", "Requirements", "Current Status"]
    text = full_text(doc)
    assert "New joiners must complete the training within 30 days." in text
    assert "Click to edit Master" not in text  # master-slide boilerplate is skipped


# ------------------------------------------------------------------- OCR
def test_image_without_ocr_engine_fails_with_clear_message(client, fake_ocr):
    fake_ocr(None)
    (result,) = upload(client, sample("sample_notice.png"))
    assert result["accepted"] is True  # the file itself is valid
    assert result["file"]["status"] == "failed"
    assert result["file"]["error_code"] == "ocr_unavailable"
    assert "Tesseract" in result["file"]["error_message"]


def test_image_ocr_with_engine(client, fake_ocr):
    fake_ocr("SERVER ROOM ACCESS NOTICE\nVisitors must be escorted at all times.")
    (file_id,) = upload_ok(client, sample("sample_notice.png"))
    doc = content(client, file_id)
    assert "Visitors must be escorted at all times." in full_text(doc)
    meta = doc["file"]["document"]["metadata"]
    assert meta["ocr_applied"] is True and meta["ocr_engine"] == "fake-ocr" and meta["width"] == 1400


def _scanned_pdf() -> bytes:
    """A PDF whose only content is an image, like a scan."""
    image = Image.new("RGB", (600, 300), "white")
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.drawImage(ImageReader(image), 50, 400, width=400, height=200)
    pdf.save()
    return buffer.getvalue()


def test_scanned_pdf_uses_ocr_when_available(client, fake_ocr):
    fake_ocr("Scanned policy page. Backups must be tested quarterly.")
    (file_id,) = upload_ok(client, ("scan.pdf", _scanned_pdf(), "application/pdf"))
    doc = content(client, file_id)
    assert "Backups must be tested quarterly." in full_text(doc)
    assert doc["file"]["document"]["metadata"]["ocr_applied"] is True


def test_scanned_pdf_without_ocr_is_reported_not_silently_empty(client, fake_ocr):
    fake_ocr(None)
    (result,) = upload(client, ("scan.pdf", _scanned_pdf(), "application/pdf"))
    assert result["file"]["status"] == "failed" and result["file"]["error_code"] == "ocr_unavailable"
    assert "scanned" in result["file"]["error_message"]


@pytest.mark.skipif(get_ocr_engine() is None, reason="Tesseract OCR is not installed on this machine")
def test_real_tesseract_ocr(client):
    (file_id,) = upload_ok(client, sample("sample_notice.png"))
    assert "server room" in full_text(content(client, file_id)).lower()


# --------------------------------------------------- invalid and hostile
def test_empty_file_rejected(client):
    (result,) = upload(client, ("empty.txt", b""))
    assert result["accepted"] is False and result["error"]["code"] == "empty_file"


def test_whitespace_only_file_rejected(client):
    (result,) = upload(client, ("blank.txt", b"   \n\n  \t "))
    assert result["accepted"] is False and result["error"]["code"] == "empty_file"


def test_unsupported_format_rejected(client):
    results = upload(client, ("tool.exe", b"MZ\x90\x00"), ("archive.zip", b"PK\x03\x04"), ("noextension", b"text"))
    assert [r["accepted"] for r in results] == [False, False, False]
    assert all(r["error"]["code"] == "unsupported_type" for r in results)
    assert "Supported formats" in results[0]["error"]["message"]
    assert client.get("/api/files").json()["files"] == []


def test_signature_mismatch_rejected(client):
    results = upload(
        client,
        ("fake.pdf", b"MZ this is not a pdf at all"),
        ("fake.png", b"not an image"),
        ("fake.docx", b"plain text pretending to be word"),
        ("fake.txt", b"\x00\x01\x02binary\x00data"),
        ("fake.xls", b"a,b,c\n1,2,3\n"),
    )
    assert all(not r["accepted"] and r["error"]["code"] == "signature_mismatch" for r in results), results


def test_zip_that_is_not_the_claimed_office_type(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")
    (result,) = upload(client, ("not_really.xlsx", buffer.getvalue()))
    assert not result["accepted"] and result["error"]["code"] == "signature_mismatch"


def test_corrupted_docx_and_xlsx_fail_extraction_cleanly(client):
    for name, part in (("broken.docx", "word/document.xml"), ("broken.xlsx", "xl/workbook.xml")):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(part, "<not-valid")
        (result,) = upload(client, (name, buffer.getvalue()))
        assert result["accepted"] and result["file"]["status"] == "failed", result
        assert result["file"]["error_code"] == "corrupted"
        assert "Traceback" not in result["file"]["error_message"]


def test_corrupted_pdf_fails_cleanly(client):
    data = sample("sample_cloud_security.pdf").read_bytes()
    (result,) = upload(client, ("truncated.pdf", data[:300]))
    assert result["file"]["status"] == "failed" and result["file"]["error_code"] == "corrupted"


def test_truncated_zip_office_file_rejected(client):
    data = sample("sample_vendor_assessment.docx").read_bytes()
    (result,) = upload(client, ("cut.docx", data[: len(data) // 2]))
    assert not result["accepted"] and result["error"]["code"] == "corrupted"


def test_password_protected_pdf_detected(client):
    writer = PdfWriter()
    for page in PdfReader(str(sample("sample_cloud_security.pdf"))).pages:
        writer.add_page(page)
    writer.encrypt("s3cret")
    buffer = io.BytesIO()
    writer.write(buffer)
    (result,) = upload(client, ("locked.pdf", buffer.getvalue()))
    assert result["file"]["status"] == "failed" and result["file"]["error_code"] == "encrypted"
    assert "password" in result["file"]["error_message"].lower()


def test_password_protected_office_file_detected(client):
    # Encrypted OOXML files are OLE containers, not ZIP archives.
    ole_bytes = (FIXTURES / "legacy_vendor_assessment.doc").read_bytes()
    (result,) = upload(client, ("protected.docx", ole_bytes))
    assert not result["accepted"] and result["error"]["code"] == "encrypted"


def test_oversized_upload_rejected(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    (result,) = upload(client, ("big.txt", b"A requirement must be met.\n" * 60_000))
    assert not result["accepted"] and result["error"]["code"] == "too_large"
    assert "1 MB" in result["error"]["message"]
    assert not list(settings.tmp_dir.glob("*")) and not list(settings.uploads_dir.glob("*"))


def test_too_many_files_in_one_upload(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "max_files_per_upload", 2)
    body = upload(client, ("a.txt", b"one"), ("b.txt", b"two"), ("c.txt", b"three"), expect_status=400)
    assert body["error"]["code"] == "too_many_files"


def test_decompression_bomb_rejected_before_parsing(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "max_archive_uncompressed_mb", 1)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w:document/>")
        archive.writestr("word/media/padding.bin", b"\x00" * (3 * 1024 * 1024))
    (result,) = upload(client, ("bomb.docx", buffer.getvalue()))
    assert not result["accepted"] and result["error"]["code"] == "too_large"


def test_xml_entity_expansion_blocked(client):
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]><root>&lol2;</root>'
    (result,) = upload(client, ("bomb.xml", bomb))
    assert result["file"]["status"] == "failed" and result["file"]["error_code"] == "unsupported"
    assert "security" in result["file"]["error_message"]


def test_malformed_json_and_xml(client):
    results = upload(client, ("bad.json", b'{"a": 1,,}'), ("bad.xml", b"<root><open></root>"))
    assert [r["file"]["error_code"] for r in results] == ["corrupted", "corrupted"]


def test_mime_type_mismatch_rejected(client):
    (result,) = upload(client, ("report.pdf", sample("sample_cloud_security.pdf").read_bytes(), "image/png"))
    assert not result["accepted"] and result["error"]["code"] == "mime_mismatch"


def test_path_traversal_file_names_are_neutralised(client, settings):
    (result,) = upload(client, ("..\\..\\..\\windows\\system32\\evil.txt", b"Access must be approved by the owner."))
    assert result["accepted"] and result["file"]["file_name"] == "evil.txt"
    stored = [p.name for p in settings.uploads_dir.glob("*")]
    assert stored == [f"{result['file']['id']}.txt"]  # stored under a generated id only


def test_sanitize_filename():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\Users\\x\\report final.docx") == "report final.docx"
    assert sanitize_filename("con.txt") == "_con.txt"
    assert sanitize_filename("a<b>c|d?.pdf") == "a_b_c_d_.pdf"
    assert sanitize_filename("") == "file"
    assert len(sanitize_filename("x" * 400 + ".txt")) <= 150


def test_extraction_timeout(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "extraction_timeout_seconds", 1)
    parser = get_parser("txt")
    monkeypatch.setattr(type(parser), "parse", lambda self, path, ctx: time.sleep(3))
    (result,) = upload(client, ("slow.txt", b"Backups must be tested."))
    assert result["file"]["status"] == "failed" and result["file"]["error_code"] == "timeout"


# -------------------------------------------------- multi-file behaviour
def test_multiple_files_one_failure_does_not_block_the_rest(client, fake_ocr):
    fake_ocr(None)
    results = upload(
        client,
        sample("sample_security.txt"),
        ("virus.exe", b"MZ"),
        sample("sample_notice.png"),
        sample("sample_access_records.csv"),
    )
    statuses = [(r["accepted"], (r["file"] or {}).get("status")) for r in results]
    assert statuses == [(True, "extracted"), (False, None), (True, "failed"), (True, "extracted")]
    listed = client.get("/api/files").json()["files"]
    assert {f["file_name"] for f in listed} == {"sample_security.txt", "sample_notice.png", "sample_access_records.csv"}


def test_duplicate_upload_is_detected_and_not_stored_twice(client, settings):
    first = upload(client, sample("sample_security.txt"))[0]
    renamed = ("copy_of_security.txt", sample("sample_security.txt").read_bytes())
    second = upload(client, renamed)[0]
    assert first["duplicate"] is False and second["duplicate"] is True
    assert second["file"]["id"] == first["file"]["id"]
    assert len(list(settings.uploads_dir.glob("*"))) == 1
    assert len(client.get("/api/files").json()["files"]) == 1


def test_file_status_endpoint_and_reextraction(client):
    (file_id,) = upload_ok(client, sample("sample_security.txt"))
    status = client.get(f"/api/files/{file_id}").json()
    assert status["status"] == "extracted" and status["status_label"] == "Extracted"
    again = client.post(f"/api/extract/{file_id}")
    assert again.status_code == 200 and again.json()["file"]["status"] == "extracted"


def test_delete_file_removes_stored_bytes_and_extracted_content(client, settings):
    (file_id,) = upload_ok(client, sample("sample_security.txt"))
    assert list(settings.uploads_dir.glob("*")) and list(settings.extracted_dir.glob("*"))
    assert client.delete(f"/api/files/{file_id}").json() == {"deleted": True, "id": file_id}
    assert not list(settings.uploads_dir.glob("*")) and not list(settings.extracted_dir.glob("*"))
    assert client.get(f"/api/files/{file_id}").status_code == 404
    assert client.delete(f"/api/files/{file_id}").status_code == 404


def test_delete_source_after_extraction_option(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "delete_source_after_extraction", True)
    (file_id,) = upload_ok(client, sample("sample_security.txt"))
    assert not list(settings.uploads_dir.glob("*"))
    assert client.get(f"/api/files/{file_id}").json()["source_deleted"] is True
    assert client.get(f"/api/files/{file_id}/content").status_code == 200
    assert client.post(f"/api/extract/{file_id}").status_code == 409


# ------------------------------------------------------ building blocks
def test_parser_registry_is_extensible():
    @register_parser
    class _LogParser(BaseParser):
        extensions = ("policytestlog",)

        def parse(self, path, ctx):
            return ParseResult(title="log")

    assert "policytestlog" in registered_extensions()
    assert isinstance(get_parser("POLICYTESTLOG"), _LogParser)


def test_split_into_sections_recognises_heading_styles():
    text = "Intro line.\n\n# Markdown Title\nBody one.\n\n2.1 Numbered Heading\nBody two.\n\nUPPERCASE HEADING\nBody three.\n\nUnderlined\n=========\nBody four."
    sections = split_into_sections(text, id_prefix="x", default_heading="Document")
    assert [s.heading for s in sections] == ["Document", "Markdown Title", "2.1 Numbered Heading", "UPPERCASE HEADING", "Underlined"]
    assert sections[2].level == 2 and sections[2].text == "Body two."


def test_reflow_joins_wrapped_lines_but_keeps_lists_and_headings():
    text = (
        "2. Requirements\n"
        "Storage buckets must not be publicly accessible unless the CISO has approved a\n"
        "documented exception.\n"
        "- First item\n"
        "- Second item that is long enough to be wrapped onto the following line of the page\n"
        "continues here.\n"
    )
    lines = reflow_lines(text).split("\n")
    assert lines[0] == "2. Requirements"
    assert lines[1] == "Storage buckets must not be publicly accessible unless the CISO has approved a documented exception."
    assert lines[2] == "- First item"
    assert lines[3].endswith("of the page continues here.")


def test_clean_extracted_text_turns_unmapped_glyphs_into_bullets():
    assert clean_extracted_text("(cid:127) Item one\n\x7f Item two\nPlain (cid:3)text") == "- Item one\n- Item two\nPlain text"


def test_rows_to_table_pads_drops_empty_columns_and_truncates():
    table = rows_to_table([["A", "", "C"], ["1", "", "3"], [], ["4"]], table_id="t", max_rows=1)
    assert table.headers == ["A", "C"] and table.rows == [["1", "3"]] and table.truncated is True
    assert rows_to_table([[], [""]], table_id="t") is None
