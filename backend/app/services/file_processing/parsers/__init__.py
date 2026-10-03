"""Format parsers.  Importing this package registers every parser."""

from app.services.file_processing.parsers import (  # noqa: F401
    csv_parser,
    doc_legacy,
    docx_parser,
    excel,
    html_parser,
    image,
    json_parser,
    pdf,
    ppt_legacy,
    pptx_parser,
    text,
    xml_parser,
)
