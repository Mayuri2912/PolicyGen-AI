"""Prompt templates.

Prompts are Markdown files next to this module so they can be reviewed and
edited without touching Python.  ``render`` fills ``{{name}}`` placeholders.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@lru_cache
def load(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def render(name: str, **values) -> str:
    template = load(name)

    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"Prompt '{name}' needs a value for '{key}'.")
        return str(values[key])

    # A single pass: placeholders inside substituted values are left untouched,
    # so document text containing "{{...}}" cannot pull in other values.
    return _PLACEHOLDER.sub(replace, template)
