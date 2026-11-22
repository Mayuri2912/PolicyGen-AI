"""Word (.docx) renderer built on python-docx."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.services.document_generation import theme
from app.services.document_generation.model import section_label
from app.services.policy_generation.templates import TEMPLATE_DIR

_FONT = "Calibri"
_CONTENT_WIDTH_CM = 21.00086 - 1.27 - 2.59115


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
        self.doc = Document(TEMPLATE_DIR / "sapres_ims.docx")
        self.cover_pattern = [deepcopy(p._p) for p in self.doc.paragraphs[:14]]
        self.history_pattern = deepcopy(self.doc.tables[0]._tbl)
        for child in list(self.doc._element.body):
            if child.tag != qn("w:sectPr"):
                self.doc._element.body.remove(child)

    # ------------------------------------------------------------- set-up
    def _styles(self) -> None:
        # Headings already include a number in the neutral document model.
        # Disable inherited Word numbering to avoid "11. Purpose" after TOC refresh.
        for name in ("Heading 1", "Heading 2"):
            num = self.doc.styles[name].element.get_or_add_pPr().get_or_add_numPr()
            num.get_or_add_numId().val = 0

    def _page(self) -> None:
        section = self.doc.sections[0]
        for part in (section.header, section.first_page_header, section.even_page_header):
            if not theme.uses_sapres_brand(self.meta["organization"]):
                for paragraph in part.paragraphs:
                    if paragraph._p.xpath(".//w:drawing | .//w:pict"):
                        paragraph.clear()
                        _write(paragraph, self.meta["organization"], bold=True, size=14)
            for node in part._element.xpath(".//w:t"):
                if "Cybercrime" in (node.text or ""):
                    node.text = f"{self.meta['title']} v{self.meta['document_version']}"
        for part in (section.footer, section.first_page_footer, section.even_page_footer):
            for paragraph in part.paragraphs:
                if "Sapres Technologies" in paragraph.text:
                    text = paragraph.text.replace("Sapres Technologies", theme.clean(self.meta["organization"]))
                    nodes = paragraph._p.xpath(".//w:t")
                    for index, node in enumerate(nodes):
                        node.text = text if index == 0 else ""
            if not part.paragraphs:
                continue
            paragraph = part.paragraphs[0]
            paragraph.clear()
            _write(paragraph, self.meta["classification"], size=8)
            paragraph.add_run("\t\tPage ")
            _field(paragraph.add_run(), "PAGE")
            paragraph.add_run(" of ")
            _field(paragraph.add_run(), "NUMPAGES")

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
            _shade(cell, "E7E6E6")
            _write(cell.paragraphs[0], title, bold=True, size=size, color="000000")
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
            # The customer template has no built-in List Bullet/List Number
            # styles. Use its body style with explicit hanging markers.
            for number, item in enumerate(element["items"], start=1):
                paragraph = self.doc.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(0.5)
                paragraph.paragraph_format.first_line_indent = Cm(-0.5)
                marker = "•" if kind == "bullets" else f"{number}."
                _write(paragraph, f"{marker} {item}")
        elif kind == "statements":
            for statement in element["statements"]:
                paragraph = self.doc.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(0.4)
                paragraph.paragraph_format.space_after = Pt(1)
                paragraph.paragraph_format.keep_with_next = False
                if statement.get("id"):
                    _write(paragraph, f"{statement['id']}  ", bold=True, color=theme.ACCENT)
                _write(paragraph, statement["text"])
        elif kind == "table":
            self._table(element["columns"], element["rows"], element.get("widths"), size=8 if len(element["columns"]) > 5 else 9)
        elif kind == "risk_matrix":
            self._risk_matrix(element["cells"])
        elif kind == "note":
            self._notice(element["text"])

    # ---------------------------------------------------------------- parts
    def _cover(self) -> None:
        from docx.text.paragraph import Paragraph

        for index, pattern in enumerate(self.cover_pattern):
            paragraph = Paragraph(deepcopy(pattern), self.doc._body)
            if index in (9, 11):
                value = self.meta["title"] if index == 9 else f"Version {self.meta['document_version']}"
                texts = paragraph._p.xpath(".//w:t")
                if texts:
                    texts[0].text = value
                    for text in texts[1:]:
                        text.text = ""
            self.doc._element.body.insert_element_before(paragraph._p, "w:sectPr")
        self.doc.add_page_break()

    def _front_matter(self) -> None:
        from docx.table import Table

        compact = self.meta.get("compact_document_control", False)
        self.doc.add_paragraph("Document Control" if compact else "Document id", style="Titre 01")
        if not compact:
            self.doc.add_paragraph(f"Document ID: {self.meta['policy_code']}")
            self.doc.add_paragraph(f"Document name: {self.meta['title']}")
        self._table(["Control Parameter" if compact else "Item", "Details"], self.model["document_control"], [28, 72])
        self.doc.add_paragraph("Document change history", style="Titre 01")
        table = Table(deepcopy(self.history_pattern), self.doc._body)
        self.doc._element.body.insert_element_before(table._tbl, "w:sectPr")
        for row in list(table.rows)[1:]:
            table._tbl.remove(row._tr)
        control = dict(self.model["document_control"])
        owner = control.get("Document owner", control.get("Policy owner", ""))
        for values in self.model["version_history"]:
            cells = table.add_row().cells
            for cell, value in zip(cells, [values[0], values[1], values[3], owner]):
                cell.text = value
        self.doc.add_page_break()

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
        properties.author = self.meta["organization"]
        properties.subject = theme.clean(self.meta["policy_type"])
        properties.comments = ""
        properties.last_modified_by = self.meta["organization"]
        update = OxmlElement("w:updateFields")
        update.set(qn("w:val"), "true")
        self.doc.settings.element.append(update)
        self.doc.save(str(path))
        return path


def render_docx(model: dict, path: Path) -> Path:
    return DocxRenderer(model).render(path)
