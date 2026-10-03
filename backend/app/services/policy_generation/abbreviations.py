"""The Abbreviations section.

The model proposes the rows; this module decides what is finally listed, so
the section is right whatever the model returned and stays right after edits,
revisions and regeneration.

An abbreviation is listed when the policy's other sections actually use it and
its full form is known: given in the section by the model or the user, spelled
out in the policy text itself ("Change Advisory Board (CAB)"), or found in the
glossary of standard abbreviations (``templates/abbreviations.json``).  Nothing
is made up: an abbreviation that is used but cannot be expanded is reported to
the reviewer instead of being given an invented meaning.
"""

from __future__ import annotations

import re

from app.schemas.policy import PLACEHOLDER_PATTERN
from app.services.policy_generation.templates import abbreviation_glossary

SECTION_ID = "abbreviations"
DEFINITIONS_ID = "definitions"
COLUMNS = ["Abbreviation", "Full Form / Description"]
NONE_USED = "This policy does not use any abbreviations that need to be explained."
# Not searched for abbreviations in use: the list itself, and the list of source files.
_SKIPPED_SECTIONS = {SECTION_ID, "references"}
_LINKING_WORDS = {"a", "an", "and", "as", "for", "in", "of", "on", "or", "the", "to"}
_CAPITALS = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,5})s?(?![A-Za-z0-9])")
_IN_BRACKETS = re.compile(r"\(([A-Z][A-Za-z0-9&]{1,9})\)")
_ROMAN = re.compile(r"^[IVXLC]+$")
# Capitals that are not abbreviations needing a full form: finding and document
# ids, file types, emphasis, units, company suffixes.
_IGNORED = frozenset(
    """
    ID OK NA TBD TBC NO YES NOT MUST SHALL MAY AND OR THE ALL ANY NOTE HIGH LOW NEW END USE REQ RSK CTL GAP REC FAQ AM PM
    UTC IST GMT USD INR EUR GBP Q1 Q2 Q3 Q4 LTD INC LLC LLP PVT PLC GMBH PDF DOCX DOC XLSX XLS CSV TXT PPT PPTX JSON XML HTML
    PNG JPG JPEG
    """.split()
)


def is_abbreviation(text: str) -> bool:
    """True for a short form such as MFA, PCI DSS, SaaS or 2FA; false for an ordinary term."""
    text = text.strip()
    if text in abbreviation_glossary():
        return True
    if not 2 <= len(text) <= 12 or PLACEHOLDER_PATTERN.search(text):
        return False
    letters = [c for c in text if c.isalnum()]
    capitals = sum(1 for c in letters if c.isupper() or c.isdigit())
    return len(letters) >= 2 and len(text.split()) <= 2 and capitals >= 2 and capitals * 2 >= len(letters)


def _initials(phrase: str, *, linking: bool) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", phrase) if w]
    return "".join(w[0].lower() for w in words if linking or w.lower() not in _LINKING_WORDS)


def _letters(abbreviation: str) -> str:
    return "".join(c.lower() for c in abbreviation if c.isalnum())


def expands(abbreviation: str, phrase: str) -> bool:
    """True when the phrase's initials begin with the abbreviation ("Role-Based Access Control" for RBAC)."""
    letters = _letters(abbreviation)
    return bool(letters) and any(_initials(phrase, linking=flag).startswith(letters) for flag in (False, True))


def _spells(abbreviation: str, phrase: str) -> bool:
    """True when the phrase's initials are exactly the abbreviation."""
    return _letters(abbreviation) in (_initials(phrase, linking=False), _initials(phrase, linking=True))


def _body_text(content: dict) -> str:
    """The text of every section that can use an abbreviation."""
    parts = []
    for section in content["sections"]:
        if section["id"] in _SKIPPED_SECTIONS:
            continue
        for block in section["blocks"]:
            parts.append(block.get("text", ""))
            parts += block.get("items", [])
            parts += [statement["text"] for statement in block.get("statements", [])]
            parts += block.get("columns", [])
            parts += [cell for row in block.get("rows", []) for cell in row]
    return "\n".join(part for part in parts if part)


def spelled_out(text: str) -> dict[str, str]:
    """Abbreviations the text itself expands: "... a Change Advisory Board (CAB) ..."."""
    found: dict[str, str] = {}
    for match in _IN_BRACKETS.finditer(text):
        abbreviation = match.group(1)
        before = text[max(0, match.start() - 120) : match.start()]
        if not before.endswith(" "):
            continue
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", re.split(r"[.;:!?()\n|]", before)[-1])
        # The phrase is the shortest run of words before the bracket whose initials spell the abbreviation.
        for size in range(1, min(len(words), len(_letters(abbreviation)) + 3) + 1):
            phrase = " ".join(words[-size:])
            if phrase.split()[0].lower() not in _LINKING_WORDS and _spells(abbreviation, phrase):
                standard = abbreviation_glossary().get(abbreviation, "")
                found.setdefault(abbreviation, standard if _same_words(standard, phrase) else phrase[0].upper() + phrase[1:])
                break
    return found


def in_use(text: str, ignore: set[str] = frozenset()) -> tuple[set[str], set[str]]:
    """Return ``(known, unknown)`` abbreviations used in the text.

    ``known`` are glossary entries; ``unknown`` are other short runs of
    capitals that look like abbreviations and have no known full form.
    """
    known = set()
    remaining = text
    for abbreviation in sorted(abbreviation_glossary(), key=len, reverse=True):
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(abbreviation)}s?(?![A-Za-z0-9])")
        if pattern.search(remaining):
            known.add(abbreviation)
            remaining = pattern.sub(" ", remaining)
    unknown = set()
    for match in _CAPITALS.finditer(remaining):
        token = match.group(1)
        followed_by_number = re.match(r"-\d", remaining[match.end() : match.end() + 2])  # REQ-001, ISP-004
        if token in _IGNORED or token in ignore or followed_by_number or _ROMAN.match(token):
            continue
        unknown.add(token)
    return known, unknown


def _listed(section: dict | None) -> tuple[list[tuple[str, str]], list[dict]]:
    """Rows already in the section (from the model or the user) and its introductory paragraphs."""
    rows, paragraphs = [], []
    for block in (section or {}).get("blocks", []):
        if block["type"] == "table":
            rows += [(row[0].strip(), row[1].strip() if len(row) > 1 else "") for row in block["rows"] if row and row[0].strip()]
        elif block["type"] in ("bullets", "numbered"):
            for item in block["items"]:
                parts = re.split(r"\s*[:–—-]\s+", item, maxsplit=1)
                if len(parts) == 2 and parts[0].strip():
                    rows.append((parts[0].strip(), parts[1].strip()))
        elif block["type"] == "paragraph" and block["text"].strip() and block["text"].strip() != NONE_USED:
            paragraphs.append(block)
    return rows, paragraphs


def _block(kind: str, **fields) -> dict:
    return {"type": kind, "text": "", "items": [], "statements": [], "columns": [], "rows": [], **fields}


def rebuild(content: dict, heading: str, *, ignore: set[str] = frozenset()) -> list[dict]:
    """Rewrite the Abbreviations section from what the policy uses.  Returns notes for the quality report.

    The section must already be at its place in ``content`` (it is created
    there, empty, by the caller when missing).
    """
    notes: list[dict] = []
    section = next(s for s in content["sections"] if s["id"] == SECTION_ID)
    section["heading"] = heading
    listed, paragraphs = _listed(section)
    by_user = section.get("origin") == "user_edited"
    glossary = abbreviation_glossary()
    text = _body_text(content)
    known, unknown = in_use(text, ignore)
    defined = spelled_out(text)

    rows: dict[str, str] = {}
    misplaced: list[tuple[str, str]] = []
    for abbreviation, full_form in listed:
        if not is_abbreviation(abbreviation):
            misplaced.append((abbreviation, full_form))
            continue
        if abbreviation in rows:
            continue  # each abbreviation once
        used = re.search(rf"(?<![A-Za-z0-9]){re.escape(abbreviation)}s?(?![A-Za-z0-9])", text)
        if not used and not by_user:
            continue  # the model listed something the policy does not use
        standard = glossary.get(abbreviation)
        if not full_form or (standard and not expands(abbreviation, full_form)):
            # No full form given, or one that does not fit the letters when a standard one exists.
            full_form = defined.get(abbreviation) or standard or ""
        if full_form:
            rows[abbreviation] = full_form
    for abbreviation in sorted(known | set(defined)):
        if abbreviation not in rows:
            rows[abbreviation] = defined.get(abbreviation) or glossary[abbreviation]

    unexplained = sorted(unknown - set(rows))
    if unexplained:
        notes.append(
            {
                "code": "unexplained_abbreviation", "severity": "info", "section_id": SECTION_ID,
                "message": "These abbreviations are used in the policy but have no known full form, so they are not listed "
                f"in the Abbreviations section: {', '.join(unexplained)}. Add them there if readers need them explained.",
            }
        )

    ordered = sorted(rows.items(), key=lambda row: row[0].casefold())
    if ordered:
        section["blocks"] = [*paragraphs, _block("table", columns=list(COLUMNS), rows=[[a, f] for a, f in ordered])]
    else:
        section["blocks"] = [*paragraphs, _block("paragraph", text=NONE_USED)]

    notes += _tidy_definitions(content, dict(ordered), misplaced)
    return notes


def _tidy_definitions(content: dict, abbreviations: dict[str, str], misplaced: list[tuple[str, str]]) -> list[dict]:
    """Keep Definitions and Abbreviations from saying the same thing twice."""
    notes: list[dict] = []
    definitions = next((s for s in content["sections"] if s["id"] == DEFINITIONS_ID), None)
    table = next((b for b in (definitions or {}).get("blocks", []) if b["type"] == "table"), None)

    if table is not None:
        kept, dropped = [], 0
        for row in table["rows"]:
            term, meaning = row[0].strip(), (row[1].strip() if len(row) > 1 else "")
            full_form = abbreviations.get(term)
            if full_form is None:
                kept.append(row)
            elif _same_words(meaning, full_form) or not meaning:
                dropped += 1  # the row only expands the abbreviation, which the Abbreviations section already does
            else:
                kept.append([full_form, *row[1:]])  # keep the definition, under the full term
        table["rows"] = kept
        if dropped:
            notes.append(
                {
                    "code": "duplicate_definition", "severity": "info", "section_id": DEFINITIONS_ID,
                    "message": f"{dropped} entr{'y' if dropped == 1 else 'ies'} in Definitions only expanded an abbreviation "
                    "already listed in the Abbreviations section and was left out.",
                }
            )
        terms = {row[0].strip().casefold() for row in table["rows"]}
        for term, meaning in misplaced:
            if meaning and term.casefold() not in terms and len(table["columns"]) == 2:
                table["rows"].append([term, meaning])
                terms.add(term.casefold())
        if not table["rows"]:
            definitions["blocks"] = [b for b in definitions["blocks"] if b is not table]
            if not definitions["blocks"]:
                content["sections"] = [s for s in content["sections"] if s is not definitions]
    if misplaced:
        notes.append(
            {
                "code": "misplaced_term", "severity": "info", "section_id": SECTION_ID,
                "message": "Entries that are terms rather than abbreviations were taken out of the Abbreviations section: "
                + ", ".join(term for term, _ in misplaced[:8]) + ".",
            }
        )
    return notes


def _same_words(first: str, second: str) -> bool:
    def normalise(text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", text.lower()))

    return normalise(first) == normalise(second)


def audit(content: dict) -> list[dict]:
    """Structural checks on the section, as quality issues."""
    issues: list[dict] = []
    ids = [s["id"] for s in content.get("sections", [])]
    if SECTION_ID not in ids:
        return issues
    position = ids.index(SECTION_ID)
    section = content["sections"][position]
    if "scope" in ids and ids.index("scope") != position + 1:
        issues.append(
            {
                "code": "invalid_structure", "severity": "error", "section_id": SECTION_ID,
                "message": "The Abbreviations section must come immediately before Scope.",
            }
        )
    seen: set[str] = set()
    for block in section["blocks"]:
        if block["type"] != "table":
            continue
        if len(block["columns"]) != 2:
            issues.append(
                {
                    "code": "invalid_structure", "severity": "error", "section_id": SECTION_ID,
                    "message": "The Abbreviations table must have two columns: Abbreviation and Full Form / Description.",
                }
            )
        for row in block["rows"]:
            key = row[0].strip().casefold() if row else ""
            if key in seen:
                issues.append(
                    {
                        "code": "invalid_structure", "severity": "error", "section_id": SECTION_ID,
                        "message": f"The abbreviation '{row[0].strip()}' is listed more than once.",
                    }
                )
            seen.add(key)
    definitions = next((s for s in content["sections"] if s["id"] == DEFINITIONS_ID), None)
    for block in (definitions or {}).get("blocks", []):
        if block["type"] == "table":
            repeated = [row[0].strip() for row in block["rows"] if row and row[0].strip().casefold() in seen]
            if repeated:
                issues.append(
                    {
                        "code": "duplicate_definition", "severity": "warning", "section_id": DEFINITIONS_ID,
                        "message": "Definitions repeats entries of the Abbreviations section: " + ", ".join(repeated) + ".",
                    }
                )
    return issues
