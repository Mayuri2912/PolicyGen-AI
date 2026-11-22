"""PDF renderer built on ReportLab (pure Python, no external binaries)."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from app.services.document_generation import theme
from app.services.document_generation.model import section_label
from app.services.policy_generation.templates import TEMPLATE_DIR

_PAGE_WIDTH, _PAGE_HEIGHT = A4
_MARGIN = 1.27 * cm
_RIGHT_MARGIN = 2.59115 * cm
_CONTENT_WIDTH = _PAGE_WIDTH - _MARGIN - _RIGHT_MARGIN

# A Unicode TrueType font is used when one is installed so characters outside
# Latin-1 render correctly; otherwise the built-in Helvetica is used.
_FONT_CANDIDATES = (
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf"),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
    ),
    (
        "/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/Library/Fonts/Arial Italic.ttf",
    ),
)
_fonts: tuple[str, str, str] | None = None


def _resolve_fonts() -> tuple[str, str, str]:
    global _fonts
    if _fonts is None:
        _fonts = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")
        for regular, bold, italic in _FONT_CANDIDATES:
            if all(Path(p).exists() for p in (regular, bold, italic)):
                try:
                    pdfmetrics.registerFont(TTFont("PolicyGen", regular))
                    pdfmetrics.registerFont(TTFont("PolicyGen-Bold", bold))
                    pdfmetrics.registerFont(TTFont("PolicyGen-Italic", italic))
                    pdfmetrics.registerFontFamily(
                        "PolicyGen", normal="PolicyGen", bold="PolicyGen-Bold", italic="PolicyGen-Italic", boldItalic="PolicyGen-Bold"
                    )
                    _fonts = ("PolicyGen", "PolicyGen-Bold", "PolicyGen-Italic")
                    break
                except (TTFError, OSError):
                    continue
    return _fonts


def _hex(value: str):
    return colors.HexColor(f"#{value}")


def _markup(text) -> str:
    """Escape text for a Paragraph and highlight placeholders."""
    out = []
    for fragment, is_placeholder in theme.split_placeholders(theme.clean(text)):
        safe = escape(fragment).replace("\n", "<br/>")
        out.append(f'<font backColor="#{theme.PLACEHOLDER_FILL}"><b>{safe}</b></font>' if is_placeholder else safe)
    return "".join(out)


class _NumberedCanvas(canvas.Canvas):
    """Defers page output so the footer can show 'Page X of Y'.

    Pages are only written out in ``save``.  A bookmark points at the page
    being written when it is made, so headings are remembered per page here
    and bookmarked while that page is written; made during layout, every
    bookmark, and every contents link with it, would lead to the first page.
    """

    def __init__(self, *args, footer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_states: list[dict] = []
        self._footer = footer
        self._headings: list[tuple[str, str]] = []

    def heading(self, key: str, text: str) -> None:
        """Remember a heading on the current page, to be bookmarked when the page is written."""
        self._headings.append((key, text))

    def showPage(self):
        self._saved_states.append(dict(self.__dict__))
        self._headings = []
        self._startPage()

    def save(self):
        total = len(self._saved_states)
        for state in self._saved_states:
            self.__dict__.update(state)
            for key, text in self._headings:
                self.bookmarkPage(key)
                self.addOutlineEntry(text, key, level=0, closed=False)
            if self._footer and self._pageNumber > 1:
                self._footer(self, self._pageNumber, total)
            super().showPage()
        super().save()


class _PolicyDoc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == "PGHeading1":
            text = flowable.getPlainText()
            key = f"h-{self.seq.nextf('heading')}"
            self.canv.heading(key, text)
            self.notify("TOCEntry", (0, text, self.page, key))


class PdfRenderer:
    def __init__(self, model: dict):
        self.model = model
        self.meta = model["meta"]
        regular, bold, italic = _resolve_fonts()
        self.regular, self.bold, self.italic = regular, bold, italic
        primary, muted = _hex(theme.PRIMARY), _hex(theme.MUTED)
        base = ParagraphStyle("PGBody", fontName=regular, fontSize=10, leading=14.5, spaceAfter=6, textColor=colors.HexColor("#1b1f23"))
        self.styles = {
            "body": base,
            "small": ParagraphStyle("PGSmall", parent=base, fontSize=8, leading=10.5, spaceAfter=0),
            "cell": ParagraphStyle("PGCell", parent=base, fontSize=8.5, leading=11, spaceAfter=0),
            "cell_tight": ParagraphStyle("PGCellTight", parent=base, fontSize=7.3, leading=9.3, spaceAfter=0),
            "cell_head": ParagraphStyle("PGCellHead", parent=base, fontName=bold, fontSize=8.5, leading=11, spaceAfter=0, textColor=colors.white),
            "cell_head_tight": ParagraphStyle("PGCellHeadTight", parent=base, fontName=bold, fontSize=7.3, leading=9.3, spaceAfter=0, textColor=colors.white),
            "h1": ParagraphStyle("PGHeading1", parent=base, fontName=bold, fontSize=15.5, leading=20, spaceBefore=16, spaceAfter=8, textColor=primary, keepWithNext=True),
            "h2": ParagraphStyle("PGHeading2", parent=base, fontName=bold, fontSize=11.5, leading=15, spaceBefore=10, spaceAfter=5, textColor=primary, keepWithNext=True),
            "front": ParagraphStyle("PGFront", parent=base, fontName=bold, fontSize=15.5, leading=20, spaceBefore=4, spaceAfter=8, textColor=primary),
            "title": ParagraphStyle("PGTitle", parent=base, fontName=bold, fontSize=22, leading=27, spaceAfter=10, textColor=colors.black, alignment=TA_CENTER),
            "org": ParagraphStyle("PGOrg", parent=base, fontName=bold, fontSize=13.5, leading=18, textColor=muted, spaceAfter=10),
            "meta": ParagraphStyle("PGMeta", parent=base, fontSize=11, leading=16, spaceAfter=2),
            "detail": ParagraphStyle("PGDetail", parent=base, fontName=italic, fontSize=8, leading=10.5, textColor=muted, leftIndent=11, spaceAfter=8),
            "statement": ParagraphStyle("PGStatement", parent=base, leftIndent=11, spaceAfter=1),
            "notice": ParagraphStyle("PGNotice", parent=base, fontSize=9, leading=12.5, spaceAfter=0),
            "caption": ParagraphStyle("PGCaption", parent=base, fontName=italic, fontSize=8, textColor=muted),
            "matrix": ParagraphStyle("PGMatrix", parent=base, fontSize=8.5, leading=11, alignment=TA_CENTER, spaceAfter=0),
            "toc": ParagraphStyle("PGToc", parent=base, fontSize=10.5, leading=17, spaceAfter=0),
        }

    # ------------------------------------------------------------ elements
    def _notice(self, text: str):
        table = Table([[Paragraph(_markup(text), self.styles["notice"])]], colWidths=[_CONTENT_WIDTH])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _hex(theme.NOTICE_FILL)),
                    ("BOX", (0, 0), (-1, -1), 0.8, _hex(theme.NOTICE_BORDER)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return [table, Spacer(1, 8)]

    def _table(self, columns: list[str], rows: list[list[str]], widths: list[int] | None = None):
        tight = len(columns) > 5
        cell, head = (self.styles["cell_tight"], self.styles["cell_head_tight"]) if tight else (self.styles["cell"], self.styles["cell_head"])
        if self.meta.get("compact_document_control"):
            head = ParagraphStyle("HRTableHead", parent=head, textColor=colors.black)
        weights = theme.column_weights(columns, rows, widths)
        col_widths = [_CONTENT_WIDTH * w / sum(weights) for w in weights]
        data = [[Paragraph(_markup(c), head) for c in columns]]
        for row in rows:
            data.append([Paragraph(_markup(row[i] if i < len(row) else ""), cell) for i in range(len(columns))])
        table = Table(data, colWidths=col_widths, repeatRows=1, splitByRow=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), _hex("E7E6E6" if self.meta.get("compact_document_control") else theme.HEADER_FILL)),
            ("GRID", (0, 0), (-1, -1), 0.4, _hex(theme.RULE)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]
        style += [("BACKGROUND", (0, r), (-1, r), _hex(theme.ZEBRA_FILL)) for r in range(2, len(data), 2)]
        table.setStyle(TableStyle(style))
        return [table, Spacer(1, 9)]

    def _risk_matrix(self, cells: list[list[int]]):
        style = self.styles["matrix"]
        data, commands = [], [("GRID", (0, 0), (-1, -1), 0.4, _hex(theme.RULE)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        for row_index in range(5):
            impact = 5 - row_index
            row = [Paragraph(f"<b>Impact {impact}</b>", style)]
            for column in range(5):
                count = cells[row_index][column]
                row.append(Paragraph(f"<b>{count}</b>" if count else "-", style))
                commands.append(("BACKGROUND", (column + 1, row_index), (column + 1, row_index), _hex(theme.matrix_fill(column + 1, impact))))
            data.append(row)
        data.append([Paragraph("", style)] + [Paragraph(f"<b>Likelihood {n}</b>", style) for n in range(1, 6)])
        commands += [("BACKGROUND", (0, 0), (0, -1), _hex(theme.ZEBRA_FILL)), ("BACKGROUND", (0, 5), (-1, 5), _hex(theme.ZEBRA_FILL))]
        table = Table(data, colWidths=[2.6 * cm] + [2.3 * cm] * 5, rowHeights=[0.85 * cm] * 6)
        table.setStyle(TableStyle(commands))
        caption = Paragraph("Risk matrix: number of risks at each likelihood and impact rating.", self.styles["caption"])
        return [KeepTogether([table, Spacer(1, 3), caption]), Spacer(1, 8)]

    def _element(self, element: dict) -> list:
        kind = element["type"]
        if kind == "paragraph":
            return [Paragraph(_markup(element["text"]), self.styles["body"])]
        if kind == "subheading":
            return [Paragraph(_markup(element["text"]), self.styles["h2"])]
        if kind in ("bullets", "numbered"):
            items = [ListItem(Paragraph(_markup(i), self.styles["body"]), leftIndent=16) for i in element["items"]]
            options = {"bulletType": "bullet", "start": "•"} if kind == "bullets" else {"bulletType": "1"}
            return [
                ListFlowable(items, leftIndent=16, bulletFontName=self.regular, bulletFontSize=9.5, **options),
                Spacer(1, 4),
            ]
        if kind == "statements":
            flowables = []
            for statement in element["statements"]:
                prefix = f'<font color="#{theme.ACCENT}"><b>{escape(statement["id"])}</b></font>&nbsp;&nbsp;' if statement.get("id") else ""
                flowables.append(
                    KeepTogether(
                        [
                            Paragraph(prefix + _markup(statement["text"]), self.styles["statement"]),
                        ]
                    )
                )
            return flowables
        if kind == "table":
            return self._table(element["columns"], element["rows"], element.get("widths"))
        if kind == "risk_matrix":
            return self._risk_matrix(element["cells"])
        if kind == "note":
            return self._notice(element["text"])
        return []

    # --------------------------------------------------------------- pages
    def _decorate(self, pdf: canvas.Canvas, page: int, total: int) -> None:
        pdf.saveState()
        pdf.setStrokeColor(_hex(theme.RULE))
        pdf.setLineWidth(0.5)
        pdf.setFont(self.regular, 8)
        pdf.setFillColor(_hex(theme.MUTED))
        top = _PAGE_HEIGHT - 1.35 * cm
        header = theme.clean(f"{self.meta['organization']}  |  {self.meta['title']}")
        pdf.drawString(_MARGIN, top, header[:110])
        pdf.setFont(self.bold, 8)
        pdf.drawRightString(_PAGE_WIDTH - _RIGHT_MARGIN, top, theme.clean(self.meta["classification"])[:40])
        pdf.line(_MARGIN, top - 4, _PAGE_WIDTH - _MARGIN, top - 4)
        pdf.setFont(self.regular, 8)
        bottom = 1.15 * cm
        pdf.line(_MARGIN, bottom + 10, _PAGE_WIDTH - _MARGIN, bottom + 10)
        footer = theme.clean(f"Version {self.meta['document_version']} (rev. {self.meta['revision']})  |  {self.meta['status']}")
        pdf.drawString(_MARGIN, bottom, footer[:120])
        pdf.drawRightString(_PAGE_WIDTH - _RIGHT_MARGIN, bottom, f"Page {page} of {total}")
        pdf.setFont(self.regular, 7)
        from reportlab.lib.utils import simpleSplit
        ownership = theme.clean(f"{self.meta['organization']} is the owner of this document - Total or partial reproduction cannot be done without prior approval from owner.")
        lines = simpleSplit(ownership, self.regular, 7, _PAGE_WIDTH - _MARGIN - _RIGHT_MARGIN)
        for index, line in enumerate(lines):
            pdf.drawString(_MARGIN, 0.35 * cm + (len(lines) - index - 1) * 9, line)
        pdf.restoreState()

    def _story(self) -> list:
        styles = self.styles
        control = dict(self.model["document_control"])
        from reportlab.platypus import Image
        if theme.uses_sapres_brand(self.meta["organization"]):
            brand = Image(str(TEMPLATE_DIR / "sapres_logo.png"), width=5.21 * cm, height=1.12 * cm)
        else:
            brand_style = ParagraphStyle("PGOrganizationBrand", parent=styles["meta"], fontSize=14, leading=18)
            brand = Paragraph(_markup(self.meta["organization"]), brand_style)
        story: list = [brand, Spacer(1, 4.2 * cm)]
        story.append(Paragraph(_markup(self.meta["title"]), styles["title"]))
        version_style = ParagraphStyle("PGCoverVersion", parent=styles["meta"], fontSize=14, leading=18, alignment=TA_CENTER)
        story.append(Paragraph(_markup(f"Version {self.meta['document_version']}"), version_style))
        story += [NextPageTemplate("body"), PageBreak()]

        compact = self.meta.get("compact_document_control", False)
        story.append(Paragraph("Document Control" if compact else "Document id", styles["front"]))
        story += self._table(["Control Parameter" if compact else "Item", "Details"], self.model["document_control"], [28, 72])
        story.append(Paragraph("Document change history", styles["front"]))
        story += self._table(["Version", "Date", "Action / Modifications", "Author"],
                             [[r[0], r[1], r[3], control.get("Document owner", control.get("Policy owner", ""))] for r in self.model["version_history"]], [10, 16, 44, 30])
        story.append(PageBreak())

        story.append(Paragraph("Table of Contents", styles["front"]))
        toc = TableOfContents()
        toc.levelStyles = [styles["toc"]]
        toc.dotsMinLevel = 0
        story += [toc, PageBreak()]

        first_appendix = True
        for section in self.model["sections"]:
            if section["kind"] == "appendix" and first_appendix:
                story.append(PageBreak())
                first_appendix = False
            story.append(Paragraph(_markup(section_label(section)), styles["h1"]))
            if not section["elements"]:
                story.append(Paragraph(_markup("[To be confirmed: content for this section]"), styles["body"]))
            for element in section["elements"]:
                story += self._element(element)
        return story

    def render(self, path: Path) -> Path:
        document = _PolicyDoc(
            str(path),
            pagesize=A4,
            leftMargin=_MARGIN,
            rightMargin=_RIGHT_MARGIN,
            topMargin=2.5012 * cm,
            bottomMargin=2.5012 * cm,
            title=theme.clean(self.meta["title"]),
            author=theme.clean(self.meta["organization"]),
            subject=theme.clean(self.meta["policy_type"]),
        )
        frame = Frame(_MARGIN, 2.5012 * cm, _CONTENT_WIDTH, _PAGE_HEIGHT - 5.0024 * cm, id="main", leftPadding=0, rightPadding=0)
        document.addPageTemplates([PageTemplate(id="cover", frames=[frame]), PageTemplate(id="body", frames=[frame])])

        def make_canvas(*args, **kwargs):
            return _NumberedCanvas(*args, footer=self._decorate, **kwargs)

        # multiBuild runs layout until the table of contents page numbers settle.
        document.multiBuild(self._story(), canvasmaker=make_canvas)
        return path


def render_pdf(model: dict, path: Path) -> Path:
    return PdfRenderer(model).render(path)
