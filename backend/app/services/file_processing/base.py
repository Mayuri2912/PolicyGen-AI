"""Parser contract.

To support a new format, subclass ``BaseParser``, set ``extensions`` and
decorate the class with ``@register_parser``.  Nothing else in the application
needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings
from app.schemas.documents import DocumentSection, DocumentTable


class ParserError(Exception):
    """An extraction failure with a user-safe message.

    ``code`` is one of: empty, corrupted, encrypted, unsupported,
    ocr_unavailable, too_large, timeout.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParseResult:
    title: str = ""
    sections: list[DocumentSection] = field(default_factory=list)
    tables: list[DocumentTable] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParseContext:
    settings: Settings
    file_name: str


class BaseParser(ABC):
    extensions: tuple[str, ...] = ()
    label: str = ""

    @abstractmethod
    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        """Extract content from ``path``.  Must never execute embedded code."""


_REGISTRY: dict[str, BaseParser] = {}


def register_parser(cls: type[BaseParser]) -> type[BaseParser]:
    instance = cls()
    for extension in cls.extensions:
        _REGISTRY[extension.lower()] = instance
    return cls


def get_parser(extension: str) -> BaseParser | None:
    return _REGISTRY.get(extension.lower())


def registered_extensions() -> list[str]:
    return sorted(_REGISTRY)
