"""Policy template registry.

A policy type is a JSON object in ``app/templates/policy_types/`` (or in the
directory named by POLICY_TEMPLATES_DIR).  Dropping a new JSON file there adds
a policy type: the selector, the recommender, the generator and the renderers
all read from this registry and need no code change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.core.config import APP_DIR, get_settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger

logger = get_logger("templates")

TEMPLATE_DIR = APP_DIR / "templates"
POLICY_TYPES_DIR = TEMPLATE_DIR / "policy_types"
_REQUIRED_KEYS = ("id", "name", "code", "description", "purpose", "keywords")


@dataclass(frozen=True)
class SectionSpec:
    id: str
    heading: str
    mandatory: bool
    guidance: str
    # A retired section is no longer written.  It stays in sections.json so
    # that it is recognised, and left out, when it turns up in a policy saved
    # by an earlier version of the application or in a model's answer.
    retired: bool = False


@dataclass(frozen=True)
class PolicyTemplate:
    id: str
    name: str
    code: str
    description: str
    purpose: str
    keywords: tuple[str, ...]
    categories: tuple[str, ...] = ()
    exclude_sections: tuple[str, ...] = ()
    topics: tuple[dict, ...] = ()
    objectives: tuple[str, ...] = ()
    principles: tuple[str, ...] = ()
    definitions: dict = field(default_factory=dict)
    monitoring: tuple[str, ...] = ()
    # True for umbrella policies that take requirements from every category.
    broad: bool = False
    # Extra evidence the recommender looks for: "breadth" (requirements spread
    # over many areas) and "scored_risks" (risks rated in the documents).
    signals: tuple[str, ...] = ()

    def sections(self) -> list[SectionSpec]:
        return [s for s in section_library() if s.id not in self.exclude_sections]

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "sections": [{"id": s.id, "heading": s.heading, "mandatory": s.mandatory} for s in self.sections()],
        }


@lru_cache
def _all_sections() -> tuple[SectionSpec, ...]:
    raw = json.loads((TEMPLATE_DIR / "sections.json").read_text(encoding="utf-8"))
    return tuple(
        SectionSpec(s["id"], s["heading"], bool(s.get("mandatory")), s.get("guidance", ""), bool(s.get("retired"))) for s in raw
    )


def section_library() -> tuple[SectionSpec, ...]:
    """The sections a policy is written with, in document order."""
    return tuple(s for s in _all_sections() if not s.retired)


def retired_sections() -> tuple[SectionSpec, ...]:
    return tuple(s for s in _all_sections() if s.retired)


def is_retired(section_id: str, heading: str = "") -> bool:
    """True for a retired section, matched by id or (for model output using another id) by heading."""
    section_id, heading = (section_id or "").strip().lower(), " ".join((heading or "").lower().split())
    return any(s.id == section_id or (heading and s.heading.lower() == heading) for s in retired_sections())


@lru_cache
def abbreviation_glossary() -> dict[str, str]:
    """Standard abbreviations and their full forms (``templates/abbreviations.json``)."""
    raw = json.loads((TEMPLATE_DIR / "abbreviations.json").read_text(encoding="utf-8"))
    return {str(k).strip(): str(v).strip() for k, v in raw.items() if str(k).strip() and str(v).strip()}


@lru_cache
def given_names() -> frozenset[str]:
    raw = json.loads((TEMPLATE_DIR / "given_names.json").read_text(encoding="utf-8"))
    return frozenset(str(name).strip().lower() for name in raw.get("given_names", []) if str(name).strip())


def _build(entry: dict, source: Path) -> PolicyTemplate | None:
    missing = [key for key in _REQUIRED_KEYS if not entry.get(key)]
    if missing:
        logger.warning("policy template skipped", extra={"file": source.name, "missing": missing})
        return None
    return PolicyTemplate(
        id=str(entry["id"]).strip().lower(),
        name=entry["name"],
        code=str(entry["code"]).upper(),
        description=entry["description"],
        purpose=entry["purpose"],
        keywords=tuple(k.lower() for k in entry["keywords"]),
        categories=tuple(entry.get("categories", [])),
        exclude_sections=tuple(entry.get("exclude_sections", [])),
        topics=tuple(entry.get("topics", [])),
        objectives=tuple(entry.get("objectives", [])),
        principles=tuple(entry.get("principles", [])),
        definitions=dict(entry.get("definitions", {})),
        monitoring=tuple(entry.get("monitoring", [])),
        broad=bool(entry.get("broad", False)),
        signals=tuple(entry.get("signals", [])),
    )


@lru_cache
def load_templates() -> dict[str, PolicyTemplate]:
    directories = [POLICY_TYPES_DIR]
    extra = get_settings().policy_templates_dir
    if extra:
        directories.append(Path(extra))

    templates: dict[str, PolicyTemplate] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("policy template file unreadable", extra={"file": path.name})
                continue
            for entry in data if isinstance(data, list) else [data]:
                template = _build(entry, path) if isinstance(entry, dict) else None
                if template:
                    templates[template.id] = template
    return templates


def reload_templates() -> None:
    load_templates.cache_clear()
    _all_sections.cache_clear()
    abbreviation_glossary.cache_clear()
    given_names.cache_clear()


def get_template(policy_type: str) -> PolicyTemplate:
    template = load_templates().get((policy_type or "").strip().lower())
    if template is None:
        raise NotFoundError(f"Unknown policy type '{policy_type}'.", code="unknown_policy_type")
    return template


def list_policy_types() -> list[dict]:
    return [template.summary() for template in load_templates().values()]
