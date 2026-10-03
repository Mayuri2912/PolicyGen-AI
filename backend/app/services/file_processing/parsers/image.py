"""Images (PNG, JPEG, TIFF) via OCR."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageSequence, UnidentifiedImageError

from app.services.file_processing.base import BaseParser, ParseContext, ParseResult, ParserError, register_parser
from app.services.file_processing.ocr import OCR_UNAVAILABLE_HINT, get_ocr_engine
from app.services.file_processing.structure import split_into_sections

# Refuse absurd dimensions before decoding (decompression-bomb guard).
Image.MAX_IMAGE_PIXELS = 80_000_000
_MAX_FRAMES = 20


@register_parser
class ImageParser(BaseParser):
    extensions = ("png", "jpg", "jpeg", "tif", "tiff")
    label = "Image (OCR)"

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        try:
            with Image.open(path) as probe:
                probe.verify()
            image = Image.open(path)
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            raise ParserError("corrupted", "The image is corrupted or not a valid image file.") from exc

        with image:
            metadata = {"width": image.width, "height": image.height, "format": image.format or ""}
            engine = get_ocr_engine()
            if engine is None:
                raise ParserError("ocr_unavailable", "Text in images is read with OCR. " + OCR_UNAVAILABLE_HINT)
            texts = []
            for index, frame in enumerate(ImageSequence.Iterator(image)):
                if index >= _MAX_FRAMES:
                    break
                texts.append(engine.image_to_text(frame.convert("RGB")))

        text = "\n\n".join(t.strip() for t in texts if t.strip())
        if not text:
            raise ParserError("empty", "No text could be recognized in the image.")
        metadata.update({"ocr_applied": True, "ocr_engine": engine.name, "frame_count": len(texts)})
        return ParseResult(
            sections=split_into_sections(text, id_prefix="img", location="image", default_heading="Image text"),
            metadata=metadata,
            warnings=["Text was recognized with OCR and may contain recognition errors."],
        )
