"""Word (.docx) renderer built on python-docx."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.services.document_generation import theme
from app.services.document_generation.model import section_label

_FONT = "Calibri"
_CONTENT_WIDTH_CM = 16.5


def _rgb(hex_value: str) -> RGBColor:
    return RGBColor.from_string(hex_value)


def _shade(cell, fill: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _field(run, instruction: str) -> None:
    """Insert a simple Word field (PAGE, NUMPAGES) into a run."""
    for kind, text in (("begin", None), (None, instruction), ("end", None)):
        if kind:
            element = OxmlElement("w:fldChar")
            element.set(qn("w:fldCharType"), kind)
        else:
            element = OxmlElement("w:instrText")
            element.set(qn("xml:space"), "preserve")
            element.text = text
        run._r.append(element)


def _write(paragraph, text: str, *, bold: bool = False, italic: bool = False, size: float | None = None, color: str | None = None):
    """Add text to a paragraph, highlighting '[To be confirmed: ...]' placeholders."""
    for fragment, is_placeholder in theme.split_placeholders(theme.clean(text)):
        if not fragment:
            continue
        run = paragraph.add_run(fragment)
        run.bold = bold or is_placeholder
        run.italic = italic
        if size:
            run.font.size = Pt(size)
        if color:
            run.font.color.rgb = _rgb(color)
        if is_placeholder:
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    return paragraph


def _repeat_header(row) -> None:
    properties = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    properties.append(header)


class DocxRenderer:
    def __init__(self, model: dict):
        self.model = model
        self.meta = model["meta"]
        self.doc = Document()

    # ------------------------------------------------------------- set-up
    def _styles(self) -> None:
        styles = self.doc.styles
        normal = styles["Normal"]
        normal.font.name = _FONT
        normal.font.size = Pt(10.5)
        normal.element.rPr.rFonts.set(qn("w:eastAsia"), _FONT)
        normal.paragraph_format.space_after = Pt(6)
        normal.paragraph_format.line_spacing = 1.15
        for name, size, before in (("Heading 1", 16, 18), ("Heading 2", 12.5, 12), ("Title", 28, 0)):
            style = styles[name]
            style.font.name = _FONT
            style.font.size = Pt(size)
            style.font.bold = True
            style.font.color.rgb = _rgb(theme.PRIMARY)
            style.element.rPr.rFonts.set(qn("w:eastAsia"), _FONT)
            style.element.rPr.rFonts.set(qn("w:ascii"), _FONT)
            style.element.rPr.rFonts.set(qn("w:hAnsi"), _FONT)
            style.paragraph_format.space_before = Pt(before)
            style.paragraph_format.space_after = Pt(6)
            style.paragraph_format.keep_with_next = True

    def _page(self) -> None:
        section = self.doc.sections[0]
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width, section.page_height = Cm(21.0), Cm(29.7)
        section.left_margin = section.right_margin = Cm(2.25)
        section.top_margin, section.bottom_margin = Cm(2.3), Cm(2.0)
        section.different_first_page_header_footer = True  # the cover has no header or footer

        header = section.header.paragraphs[0]
        _write(header, f"{self.meta['organization']}  |  {self.meta['title']}", size=8, color=theme.MUTED)
        # The built-in Header and Footer styles define a centre and a right tab
        # stop, so two tabs reach the right edge.
        header.add_run("\t\t")
        _write(header, self.meta["classification"], size=8, color=theme.MUTED, bold=True)

        footer = section.footer.paragraphs[0]
        _write(footer, f"Version {self.meta['document_version']} (rev. {self.meta['revision']})  |  {self.meta['status']}", size=8, color=theme.MUTED)
        footer.add_run("\t\t")
        for text, instruction in (("Page ", "PAGE"), (" of ", "NUMPAGES")):
            run = footer.add_run(text)
            run.font.size = Pt(8)
            run.font.color.rgb = _rgb(theme.MUTED)
            number = footer.add_run()
            number.font.size = Pt(8)
            number.font.color.rgb = _rgb(theme.MUTED)
            _field(number, instruction)

    # ------------------------------------------------------------ elements
    def _notice(self, text: str) -> None:
        table = self.doc.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.style = "Table Grid"
        cell = table.rows[0].cells[0]
        _shade(cell, theme.NOTICE_FILL)
        _write(cell.paragraphs[0], text, size=9)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def _table(self, columns: list[str], rows: list[list[str]], widths: list[int] | None = None, size: float = 9) -> None:
        table = self.doc.add_table(rows=1, cols=len(columns))
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        weights = theme.column_weights(columns, rows, widths)
        cm = [_CONTENT_WIDTH_CM * w / sum(weights) for w in weights]

        header = table.rows[0]
        _repeat_header(header)
        for index, title in enumerate(columns):
            cell = header.cells[index]
            cell.width = Cm(cm[index])
            _shade(cell, theme.HEADER_FILL)
            _write(cell.paragraphs[0], title, bold=True, size=size, color="FFFFFF")
        for number, row in enumerate(rows):
            cells = table.add_row().cells
            for index in range(len(columns)):
                cell = cells[index]
                cell.width = Cm(cm[index])
                if number % 2:
                    _shade(cell, theme.ZEBRA_FILL)
                paragraph = cell.paragraphs[0]
                paragraph.paragraph_format.space_after = Pt(2)
                _write(paragraph, row[index] if index < len(row) else "", size=size)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def _risk_matrix(self, cells: list[list[int]]) -> None:
        table = self.doc.add_table(rows=6, cols=6)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for row_index in range(5):
            impact = 5 - row_index
            label = table.rows[row_index].cells[0]
            _shade(label, theme.ZEBRA_FILL)
            _write(label.paragraphs[0], f"Impact {impact}", bold=True, size=8)
            for column in range(5):
                likelihood = column + 1
                cell = table.rows[row_index].cells[column + 1]
                _shade(cell, theme.matrix_fill(likelihood, impact))
                count = cells[row_index][column]
                paragraph = cell.paragraphs[0]
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _write(paragraph, str(count) if count else "-", bold=bool(count), size=9)
        _shade(table.rows[5].cells[0], theme.ZEBRA_FILL)
        for column in range(5):
            cell = table.rows[5].cells[column + 1]
            _shade(cell, theme.ZEBRA_FILL)
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            _write(cell.paragraphs[0], f"Likelihood {column + 1}", bold=True, size=8)
        caption = self.doc.add_paragraph()
        _write(caption, "Risk matrix: number of risks at each likelihood and impact rating.", italic=True, size=8.5, color=theme.MUTED)

    def _element(self, element: dict) -> None:
        kind = element["type"]
        if kind == "paragraph":
            _write(self.doc.add_paragraph(), element["text"])
        elif kind == "subheading":
            self.doc.add_heading(theme.clean(element["text"]), level=2)
        elif kind in ("bullets", "numbered"):
            style = "List Bullet" if kind == "bullets" else "List Number"
            for item in element["items"]:
                _write(self.doc.add_paragraph(style=style), item)
        elif kind == "statements":
            for statement in element["statements"]:
                paragraph = self.doc.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(0.4)
                paragraph.paragraph_format.space_after = Pt(1)
                paragraph.paragraph_format.keep_with_next = True
                if statement.get("id"):
                    _write(paragraph, f"{statement['id']}  ", bold=True, color=theme.ACCENT)
                _write(paragraph, statement["text"])
                detail = self.doc.add_paragraph()
                detail.paragraph_format.left_indent = Cm(0.4)
                detail.paragraph_format.space_after = Pt(7)
                _write(detail, theme.basis_label(statement), italic=True, size=8.5, color=theme.MUTED)
        elif kind == "table":
            self._table(element["columns"], element["rows"], element.get("widths"), size=8 if len(element["columns"]) > 5 else 9)
        elif kind == "risk_matrix":
            self._risk_matrix(element["cells"])
        elif kind == "note":
            self._notice(element["text"])

    # ---------------------------------------------------------------- parts
    def _cover(self) -> None:
        for _ in range(5):
            self.doc.add_paragraph()
        _write(self.doc.add_paragraph(), self.meta["organization"], size=14, color=theme.MUTED, bold=True)
        title = self.doc.add_paragraph(style="Title")
        _write(title, self.meta["title"])
        for label, value in (
            ("Document version", f"{self.meta['document_version']} (draft revision {self.meta['revision']})"),
            ("Status", self.meta["status"]),
            ("Classification", self.meta["classification"]),
            ("Policy owner", next(v for k, v in self.model["document_control"] if k == "Policy owner")),
            ("Effective date", next(v for k, v in self.model["document_control"] if k == "Effective date")),
        ):
            paragraph = self.doc.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(2)
            _write(paragraph, f"{label}: ", bold=True, color=theme.PRIMARY, size=11)
            _write(paragraph, value, size=11)
        for _ in range(3):
            self.doc.add_paragraph()
        for notice in self.model["notices"]:
            self._notice(notice)
        self.doc.paragraphs[-1].add_run().add_break(WD_BREAK.PAGE)

    def _front_matter(self) -> None:
        heading = self.doc.add_paragraph()
        _write(heading, "Document Control", bold=True, size=16, color=theme.PRIMARY)
        self._table(["Item", "Details"], self.model["document_control"], [28, 72])
        heading = self.doc.add_paragraph()
        _write(heading, "Version History", bold=True, size=16, color=theme.PRIMARY)
        self._table(["Revision", "Date", "Change type", "Change note"], self.model["version_history"], [12, 18, 25, 45])
        self.doc.paragraphs[-1].add_run().add_break(WD_BREAK.PAGE)

    def _contents(self) -> None:
        heading = self.doc.add_paragraph()
        _write(heading, "Table of Contents", bold=True, size=16, color=theme.PRIMARY)
        entries = [section_label(s) for s in self.model["sections"]]

        # A real TOC field whose stored result is the list of headings, so the
        # entries are visible immediately and Word can refresh page numbers.
        paragraph = self.doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(3)
        run = paragraph.add_run()
        for kind, text in (("begin", None), (None, 'TOC \\o "1-1" \\h \\z \\u'), ("separate", None)):
            if kind:
                element = OxmlElement("w:fldChar")
                element.set(qn("w:fldCharType"), kind)
            else:
                element = OxmlElement("w:instrText")
                element.set(qn("xml:space"), "preserve")
                element.text = text
            run._r.append(element)
        for index, entry in enumerate(entries):
            if index:
                paragraph = self.doc.add_paragraph()
                paragraph.paragraph_format.space_after = Pt(3)
            _write(paragraph, entry)
        end = paragraph.add_run()
        closing = OxmlElement("w:fldChar")
        closing.set(qn("w:fldCharType"), "end")
        end._r.append(closing)

        hint = self.doc.add_paragraph()
        _write(hint, "To add page numbers in Word, right-click the list above and choose Update Field.", italic=True, size=8.5, color=theme.MUTED)
        self.doc.paragraphs[-1].add_run().add_break(WD_BREAK.PAGE)

    def _body(self) -> None:
        first_appendix = True
        for section in self.model["sections"]:
            if section["kind"] == "appendix" and first_appendix:
                self.doc.paragraphs[-1].add_run().add_break(WD_BREAK.PAGE)
                first_appendix = False
            self.doc.add_heading(theme.clean(section_label(section)), level=1)
            if not section["elements"]:
                _write(self.doc.add_paragraph(), "[To be confirmed: content for this section]")
            for element in section["elements"]:
                self._element(element)

    def _fix_numbered_lists(self) -> None:
        """Give each numbered list its own numbering instance so it starts at 1."""
        numbering = self.doc.part.numbering_part.numbering_definitions._numbering
        previous_was_number, current_num_id = False, None
        for paragraph in self.doc.paragraphs:
            is_number = paragraph.style.name == "List Number"
            if is_number and not previous_was_number:
                style_num_pr = paragraph.style.element.pPr.numPr if paragraph.style.element.pPr is not None else None
                if style_num_pr is None or style_num_pr.numId is None:
                    previous_was_number = is_number
                    continue
                base = numbering.num_having_numId(style_num_pr.numId.val)
                new_num = numbering.add_num(base.abstractNumId.val)
                override = new_num.add_lvlOverride(ilvl=0)
                override.add_startOverride(1)
                current_num_id = new_num.numId
            if is_number and current_num_id is not None:
                num_pr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
                num_pr.get_or_add_numId().val = current_num_id
                num_pr.get_or_add_ilvl().val = 0
            previous_was_number = is_number

    def render(self, path: Path) -> Path:
        self._styles()
        self._page()
        self._cover()
        self._front_matter()
        self._contents()
        self._body()
        self._fix_numbered_lists()
        properties = self.doc.core_properties
        properties.title = theme.clean(self.meta["title"])
        properties.author = "PolicyGen AI"
        properties.subject = theme.clean(self.meta["policy_type"])
        properties.comments = "AI-assisted draft. Review and approval required before adoption."
        self.doc.save(str(path))
        return path


def render_docx(model: dict, path: Path) -> Path:
    return DocxRenderer(model).render(path)
