"""Keeping individuals' names out of the policy.

A policy assigns duties to roles, departments and teams, never to named
people.  The model is told so in its prompts; this module makes the rule hold
whatever the model returned, and after manual edits too.

Recognising a personal name without also catching "Brightwater Analytics" or
"Active Directory" needs evidence, not just capital letters.  A name is treated
as a person's with confidence only when one of these holds:

* it carries an honorific ("Mr. Rao", "Dr. Anita Desai");
* it starts with a common given name ("Priya Sharma");
* it sits next to a role title in a role label ("Anita Desai (IT Manager)",
  "IT Manager - Anita Desai");
* the user entered it as the policy owner or approver, or the analysis
  recorded it as the holder of a role.

Such names are replaced by the role they hold when the evidence states it, and
by a placeholder for the role otherwise: a role is never guessed.  A role label
that only looks like a name ("Xiu Zhang") is left as it is and reported to the
reviewer, so that an unusual organization or product name is never rewritten.

The source evidence (findings, quotes, the evidence appendix, the References
section) is not touched.
"""

from __future__ import annotations

import re

from app.services.policy_generation.templates import given_names

ROLE_PLACEHOLDER = "[To be confirmed: role title]"
ROLE_COLUMN = "Role / Department"
ROLES_SECTION = "roles_responsibilities"
# Sections whose text is evidence about the sources, or a list of abbreviations.
UNTOUCHED_SECTIONS = {"references", "abbreviations"}

_HONORIFIC = r"(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Shri|Smt|Sri|Sir|Madam)\b\.?"
_WORD = r"[A-Z][a-z]+(?:[-'’][A-Z]?[a-z]+)*"
_INITIAL = r"[A-Z]\."
_PART = rf"(?:{_WORD}|{_INITIAL})"
_NAME_SHAPE = re.compile(rf"{_PART}(?:[ \t]+{_PART}){{1,3}}")
_LEADING_HONORIFIC = re.compile(rf"^\s*{_HONORIFIC}\s*")
# In running text a name is taken no further than the evidence reaches: "Dr. A. K. Rao", "Dr. Anita Desai",
# "Priya Sharma", "Priya K. Sharma".  A longer match could swallow the capitalised word that follows.
_WITH_HONORIFIC = re.compile(rf"\b{_HONORIFIC}[ \t]+((?:{_INITIAL}[ \t]+){{0,2}}{_WORD}(?:[ \t]+{_WORD})?)")
# A lookahead, so that every word is tried as the start of a name ("Contact Priya Sharma").
_GIVEN_LED = re.compile(rf"\b(?=({_WORD}(?:[ \t]+{_INITIAL})?[ \t]+{_WORD}))")
_LABELLED_ITEM = (re.compile(r"^(.*?)(\s*:\s+)(.*)$"), re.compile(r"^(.*?)(\s+[-–—]\s+)(.*)$"))
_ROLE_HEADERS = {
    "role", "roles", "name", "names", "person", "owner", "responsible", "responsible party", "who", "position", "designation",
    "role/department", "role / department", "role or department", "function", "role / function", "role/function", "team", "department",
    "role / team", "role/team", "job title", "title",
}

# Words that mark a label as a role, department, team or organization.  A
# label containing one of them is never treated as a person's name.
ROLE_WORDS = frozenset(
    """
    manager managers officer officers administrator administrators admin admins director directors head heads chief lead leads
    leader leaders owner owners team teams department departments dept committee board council group unit division office desk
    staff employee employees personnel user users contractor contractors vendor vendors supplier suppliers provider providers
    third party parties management executive executives engineer engineers analyst analysts architect auditor auditors
    controller custodian custodians steward coordinator specialist consultant consultants operator operators operations
    security information technology it hr human resources legal compliance risk finance financial procurement audit internal
    external senior junior service services support helpdesk help system systems network networks data privacy protection
    business continuity incident response change advisory all line president vice ceo cio ciso cto cfo coo dpo secretary
    supervisor supervisors representative representatives function organization organisation company policy process asset
    assets application applications cloud infrastructure development devops quality facilities physical training awareness
    general counsel partner partners customer customers client clients steering governance project program programme product
    platform database backup recovery soc noc centre center corporate communications marketing sales engineering research
    administration accounts accounting payroll logistics production manufacturing maintenance purchasing strategy planning
    delivery design testing release architecture cyber digital records knowledge learning talent people culture workplace
    reception shared central regional global local branch site plant store warehouse member members workforce trustee trustees
    sponsor sponsors champion champions approver approvers reviewer reviewers requester requesters author authors editor
    ltd limited inc llc llp pvt plc corp corporation gmbh co bank university institute technologies solutions analytics labs
    industries enterprises consulting software holdings associates foundation agency authority ministry government
    microsoft azure aws amazon google windows linux oracle sap salesforce cisco okta github gitlab jira slack active directory
    entra defender sentinel splunk crowdstrike vmware kubernetes docker office teams sharepoint exchange outlook
    """.split()
)


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]+", text)


def has_role_word(text: str) -> bool:
    return any(word.lower() in ROLE_WORDS for word in _words(text))


def strip_honorific(text: str) -> str:
    return _LEADING_HONORIFIC.sub("", text.strip()).strip()


def name_shape(text: str) -> bool:
    """Two to four capitalised words (or initials) with no role, organization or technology word."""
    text = strip_honorific(text)
    if not _NAME_SHAPE.fullmatch(text) or has_role_word(text):
        return False
    return any(len(part.rstrip(".")) > 1 for part in text.split())


def confident_name(text: str) -> bool:
    """A personal name on its own evidence: an honorific, or a common given name first."""
    text = text.strip()
    bare = strip_honorific(text)
    if bare != text:
        return bool(re.fullmatch(rf"{_PART}(?:[ \t]+{_PART}){{0,3}}", bare)) and not has_role_word(bare)
    return name_shape(text) and text.split()[0].lower() in given_names()


def _split_label(label: str) -> list[str]:
    """The parts of a role label: "A (B)", "A - B", "A: B" or "A, B"."""
    text = label.strip()
    bracketed = re.fullmatch(r"(.+?)\s*\((.+?)\)\s*", text)
    if bracketed:
        return [bracketed.group(1).strip(), bracketed.group(2).strip()]
    return [part.strip() for part in re.split(r"\s+[-–—]\s+|\s*:\s+|\s*,\s*", text) if part.strip()]


class PeopleIndex:
    """What is known about named individuals: their names and, where stated, the roles they hold."""

    def __init__(self, non_persons: list[str] | tuple[str, ...] = ()):
        self.names: dict[str, str] = {}
        self._non_persons = {" ".join(str(n).lower().split()) for n in non_persons if n and str(n).strip()}

    # ------------------------------------------------------------ learning
    def _is_non_person(self, text: str) -> bool:
        return " ".join(text.lower().split()) in self._non_persons

    def add(self, name: str, role: str = "") -> None:
        name = strip_honorific(name)
        if not name or self._is_non_person(name):
            return
        role = role.strip()
        if role and not self.names.get(name):
            self.names[name] = role
        else:
            self.names.setdefault(name, "")

    def learn(self, label: str) -> None:
        """Record the people named in a role label taken from the evidence ("Anita Desai (IT Manager)")."""
        label = (label or "").strip()
        if not label:
            return
        names, roles = self._sort_parts(_split_label(label))
        for name in names:
            self.add(name, roles[0] if roles else "")

    def learn_field(self, value: str, role: str) -> None:
        """Record a configuration value (policy owner, approver) that holds a person's name rather than a role."""
        value = (value or "").strip()
        if not value or self._is_non_person(value):
            return
        names, roles = self._sort_parts(_split_label(value))
        if names:
            for name in names:
                self.add(name, roles[0] if roles else role)
        elif name_shape(value):
            self.add(value, role)

    def _sort_parts(self, parts: list[str]) -> tuple[list[str], list[str]]:
        """Split label parts into personal names and everything else."""
        names, others = [], []
        for part in parts:
            bare = strip_honorific(part)
            neighbours = [other for other in parts if other is not part]
            is_name = not self._is_non_person(bare) and (
                bare in self.names
                or confident_name(part)
                # "Xiu Zhang (IT Manager)": a name-shaped part beside a role title is that role's holder.
                or (name_shape(bare) and any(has_role_word(other) for other in neighbours))
            )
            (names if is_name else others).append(bare if is_name else part)
        return names, others

    # ------------------------------------------------------------ cleaning
    def clean_role_label(self, label: str) -> tuple[str, bool]:
        """Return the label with any personal name replaced by the role, and whether it changed."""
        names, roles = self._sort_parts(_split_label(label))
        if not names:
            return label, False
        if roles:
            for name in names:
                self.add(name, roles[0])
            return ", ".join(roles), True
        known = next((self.names[name] for name in names if self.names.get(name)), "")
        for name in names:
            self.add(name)
        return known or ROLE_PLACEHOLDER, True

    def possible_name(self, label: str) -> bool:
        """A role label that looks like a personal name but has no evidence either way."""
        label = label.strip()
        return name_shape(label) and not confident_name(label) and not self._is_non_person(label) and label not in self.names

    def _mentions(self, text: str) -> list[str]:
        """Personal names in running text: the known ones, plus honorific and given-name forms."""
        found = {name for name in self.names if re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text)}
        for match in _WITH_HONORIFIC.finditer(text):
            name = _trim(match.group(1))
            if name and not self._is_non_person(name):
                found.add(name)
        given = given_names()
        for match in _GIVEN_LED.finditer(text):
            name = _trim(match.group(1))
            if len(name.split()) >= 2 and name.split()[0].lower() in given and not self._is_non_person(name):
                found.add(name)
        return sorted(found, key=len, reverse=True)

    def find_names(self, text: str) -> list[str]:
        return self._mentions(text or "")

    def scrub(self, text: str) -> tuple[str, int]:
        """Replace personal names in running text by their role (or a placeholder for it)."""
        if not text:
            return text, 0
        count = 0
        for name in self._mentions(text):
            who = rf"(?<![A-Za-z])(?:{_HONORIFIC}[ \t]+)?{re.escape(name)}"
            role = self.names.get(name, "")

            # "the IT Manager (Anita Desai)" and "the IT Manager, Anita Desai, must ..."
            text, n = re.subn(rf"[ \t]*\([ \t]*{who}[ \t]*\)", "", text)
            count += n
            text, n = re.subn(rf",[ \t]*{who}[ \t]*,[ \t]*", " ", text)
            count += n

            # "Anita Desai (IT Manager)": the bracket already names the role.
            def bracket(match: re.Match, role: str = role) -> str:
                inner = match.group(1).strip()
                return inner if has_role_word(inner) else (role or ROLE_PLACEHOLDER)

            text, n = re.subn(rf"{who}[ \t]*\(([^()]{{2,60}})\)", bracket, text)
            count += n

            def plain(match: re.Match, role: str = role) -> str:
                possessive = match.group(1) or ""
                if not role:
                    return ROLE_PLACEHOLDER + possessive
                before = match.string[: match.start()].rstrip()
                if re.search(r"\b(?:the|a|an)$", before, re.IGNORECASE):
                    return role + possessive
                starts_sentence = not before or before[-1] in ".!?:;|"
                return ("The " if starts_sentence else "the ") + role + possessive

            text, n = re.subn(rf"{who}(['’]s)?(?![A-Za-z])", plain, text)
            count += n
        return text, count


def _trim(name: str) -> str:
    """Drop a possessive ending and trailing role or organization words picked up after a name ("Priya Sharma Manager")."""
    parts = re.sub(r"['’]s$", "", name.strip()).split()
    while parts and parts[-1].rstrip(".").lower() in ROLE_WORDS:
        parts.pop()
    return " ".join(parts)


def _role_labels(section: dict):
    """Yield ``(get, set)`` accessors for every role label in a Roles and Responsibilities section."""
    for block in section["blocks"]:
        if block["type"] == "table":
            for row in block["rows"]:
                if row:
                    yield row[0], (lambda value, row=row: row.__setitem__(0, value))
        elif block["type"] in ("bullets", "numbered"):
            for position, item in enumerate(block["items"]):
                # "IT Manager: approves access" or "IT Manager - approves access"; a colon wins when both occur.
                match = next((m for m in (pattern.match(item) for pattern in _LABELLED_ITEM) if m), None)
                if match:
                    label, separator, rest = match.groups()
                    yield label, (
                        lambda value, block=block, position=position, separator=separator, rest=rest: block["items"].__setitem__(
                            position, f"{value}{separator}{rest}"
                        )
                    )


def remove_personal_names(content: dict, people: PeopleIndex) -> int:
    """Replace personal names by roles throughout the policy body.  Returns how many were replaced."""
    replaced = 0
    roles = next((s for s in content["sections"] if s["id"] == ROLES_SECTION), None)
    if roles is not None:
        for block in roles["blocks"]:
            if block["type"] == "table" and block["columns"] and block["columns"][0].strip().lower() in _ROLE_HEADERS:
                block["columns"][0] = ROLE_COLUMN
        # Role labels first: "Anita Desai (IT Manager)" also tells us which role the name stands for elsewhere.
        for label, assign in list(_role_labels(roles)):
            cleaned, changed = people.clean_role_label(label)
            if changed:
                assign(cleaned)
                replaced += 1

    for section in content["sections"]:
        if section["id"] in UNTOUCHED_SECTIONS:
            continue
        for block in section["blocks"]:
            if block["type"] in ("paragraph", "subheading"):
                block["text"], n = people.scrub(block["text"])
                replaced += n
            elif block["type"] in ("bullets", "numbered"):
                for position, item in enumerate(block["items"]):
                    block["items"][position], n = people.scrub(item)
                    replaced += n
            elif block["type"] == "statements":
                for statement in block["statements"]:
                    statement["text"], n = people.scrub(statement["text"])
                    replaced += n
            elif block["type"] == "table":
                for row in block["rows"]:
                    for position, cell in enumerate(row):
                        row[position], n = people.scrub(cell)
                        replaced += n
    return replaced


def audit(content: dict, people: PeopleIndex) -> list[dict]:
    """Personal names still present in the policy body, as quality issues."""
    issues = []
    for section in content.get("sections", []):
        if section["id"] in UNTOUCHED_SECTIONS:
            continue
        text = "\n".join(_block_strings(section))
        names = people.find_names(text)
        if names:
            issues.append(
                {
                    "code": "personal_name", "severity": "warning", "section_id": section["id"],
                    "message": f"'{section['heading']}' names an individual ({', '.join(repr(n) for n in names[:5])}). "
                    "Refer to the role, department or team instead of the person.",
                }
            )
        if section["id"] == ROLES_SECTION:
            doubtful = [label.strip() for label, _ in _role_labels(section) if people.possible_name(label)]
            if doubtful:
                issues.append(
                    {
                        "code": "personal_name", "severity": "warning", "section_id": section["id"],
                        "message": "Roles and Responsibilities may name an individual instead of a role: "
                        f"{', '.join(repr(d) for d in dict.fromkeys(doubtful))}. Use a role title, department or team.",
                    }
                )
    return issues


def _block_strings(section: dict) -> list[str]:
    strings = []
    for block in section["blocks"]:
        strings.append(block.get("text", ""))
        strings += block.get("items", [])
        strings += [s["text"] for s in block.get("statements", [])]
        strings += [cell for row in block.get("rows", []) for cell in row]
    return [s for s in strings if s]
