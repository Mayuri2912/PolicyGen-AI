"""Prompt-injection defences and evidence verification.

Uploaded text is untrusted.  Three layers protect the pipeline:

1. ``wrap_untrusted`` fences document content in tags carrying a random id, so
   content cannot close the fence and smuggle text into the instructions.
2. ``scan_for_injection`` flags instruction-like text so the reviewer is told.
3. ``verify_evidence`` checks every quoted piece of evidence against the
   extracted source text, so a finding the model made up (or was tricked into
   making up) is visibly marked as unverified.

The system prompt adds the behavioural layer, and every model response is
schema-validated before use.
"""

from __future__ import annotations

import re
import secrets
import unicodedata

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("instruction override", r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|system)\b[^.\n]{0,30}\b(instruction|prompt|rule|direction)s?"),
        ("system prompt disclosure", r"\b(reveal|show|print|output|repeat|disclose|leak)\b[^.\n]{0,40}\b(system prompt|hidden prompt|initial prompt|your instructions|developer message)"),
        ("credential disclosure", r"\b(reveal|show|print|output|send|exfiltrate|disclose)\b[^.\n]{0,40}\b(api[ _-]?key|secret|password|token|credential)s?\b"),
        ("role change", r"\b(you are now|act as|pretend to be|from now on you|new role|switch to)\b[^.\n]{0,60}\b(assistant|ai|model|dan|developer mode|jailbreak|unrestricted)"),
        ("command execution", r"\b(execute|run|eval)\b[^.\n]{0,30}\b(command|shell|script|code|powershell|bash|curl|wget)\b"),
        ("addressed to the AI", r"\b(dear|attention|note to|instructions? (for|to))\b[^.\n]{0,20}\b(ai|assistant|llm|language model|claude|chatgpt|gpt)\b"),
        ("output manipulation", r"\b(mark|rate|report|classify)\b[^.\n]{0,40}\b(everything|all (risks|controls|requirements))\b[^.\n]{0,40}\b(compliant|low|no risk|implemented)"),
        ("chat template tokens", r"<\|?(system|im_start|im_end|assistant|endoftext)\|?>|\[/?INST\]|</?(system|assistant)>"),
    )
)


def new_nonce() -> str:
    return secrets.token_hex(8)


def wrap_untrusted(content: str) -> str:
    """Neutralize fence-like tags inside untrusted content.

    The real fence carries a random id the document author cannot know; this
    additionally breaks any literal fence tag in the content.
    """
    return re.sub(r"<(/?)\s*untrusted_", r"<\1 untrusted-", content, flags=re.IGNORECASE)


def scan_for_injection(text: str, file_name: str) -> list[dict]:
    """Return one flag per kind of instruction-like text found in ``text``."""
    flags = []
    for label, pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 40)
            excerpt = re.sub(r"\s+", " ", text[start : match.end() + 40]).strip()
            flags.append(
                {
                    "file_name": file_name,
                    "type": label,
                    "excerpt": excerpt[:200],
                    "handling": "Treated as document content only; it was not followed as an instruction.",
                }
            )
    return flags


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


class EvidenceIndex:
    """Normalized source text per file, for checking quoted evidence."""

    def __init__(self) -> None:
        self._text: dict[str, str] = {}

    def add(self, file_name: str, text: str) -> None:
        self._text[file_name] = _normalise(text)

    def verify(self, file_name: str, evidence: str) -> bool:
        """True when the quote (or most of it) appears in the named file."""
        quote = _normalise(evidence)
        source = self._text.get(file_name, "")
        if not quote or not source:
            return False
        if quote in source:
            return True
        # Models occasionally trim or join a quote; accept it when a long
        # contiguous run of its words is present.
        words = quote.split()
        if len(words) < 6:
            return False
        window = max(6, int(len(words) * 0.6))
        return any(" ".join(words[i : i + window]) in source for i in range(0, len(words) - window + 1))

    def contains(self, phrase: str) -> bool:
        needle = _normalise(phrase)
        return bool(needle) and any(needle in text for text in self._text.values())
