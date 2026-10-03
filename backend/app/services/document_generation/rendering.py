"""Rendering service: turns a stored policy version into DOCX / PDF files."""

from __future__ import annotations

from pathlib import Path

from app.core.logging import get_logger
from app.models.entities import Analysis, Policy, PolicyVersion
from app.services import storage
from app.services.document_generation.docx_renderer import render_docx
from app.services.document_generation.model import build_document_model
from app.services.document_generation.pdf_renderer import render_pdf

logger = get_logger("rendering")

RENDERERS = {"docx": render_docx, "pdf": render_pdf}
MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


class RenderError(Exception):
    pass


def render_version(policy: Policy, version: PolicyVersion, analysis: Analysis, formats: list[str]) -> dict[str, Path]:
    """Render ``version`` in each requested format, replacing any cached file."""
    model = build_document_model(policy, version, analysis)
    paths: dict[str, Path] = {}
    for fmt in formats:
        renderer = RENDERERS.get(fmt)
        if renderer is None:
            raise RenderError(f"Unsupported output format '{fmt}'.")
        target = storage.report_path(policy.id, version.version_number, fmt)
        partial = target.with_name(target.name + ".part")
        try:
            renderer(model, partial)
            partial.replace(target)
        except Exception as exc:  # noqa: BLE001 - any library failure becomes a clear rendering error
            storage.remove(partial)
            logger.error(
                "document rendering failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"policy_id": policy.id, "format": fmt},
            )
            raise RenderError(f"The {fmt.upper()} document could not be created.") from exc
        paths[fmt] = target
    return paths


def ensure_rendered(policy: Policy, version: PolicyVersion, analysis: Analysis, fmt: str) -> Path:
    """Return the rendered file for a version, creating it if it is not cached."""
    target = storage.report_path(policy.id, version.version_number, fmt)
    if not target.exists():
        render_version(policy, version, analysis, [fmt])
    return target
