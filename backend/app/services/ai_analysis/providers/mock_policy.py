"""Rule-based policy writer for mock mode.

Assembles a policy draft from the analysis findings and the policy template
with fixed sentence patterns.  Like the mock analyzer it is NOT a language
model: it cannot weigh evidence, resolve ambiguity or follow free-text editing
instructions.  Its output is labelled as simulated wherever it is shown.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[To be confirmed: {}]"
_LIMITS = {"concise": (4, 5, 6), "standard": (10, 8, 10), "comprehensive": (60, 15, 20)}  # statements/topic, risks, steps
_IMPERATIVE = (
    "implement", "enable", "enforce", "require", "define", "establish", "perform", "conduct", "review", "encrypt", "restrict",
    "deploy", "document", "assign", "provide", "schedule", "classify", "assess", "migrate", "introduce", "complete", "update",
    "remove", "disable", "configure", "monitor", "test", "train", "adopt", "maintain", "ensure", "bring", "retain", "link",
    "agree", "track", "rotate", "apply", "segment", "create", "develop", "limit", "obtain", "renew", "validate", "verify",
    "add", "forward", "automate", "run", "replace", "include", "set", "use", "store", "report", "block", "separate",
    "appoint", "publish", "retire", "disable", "revoke", "sign", "record", "escalate", "notify", "back",
)
_LEVEL_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unrated": 4}


def ph(what: str) -> str:
    return PLACEHOLDER.format(what)


def block(kind: str, **fields) -> dict:
    return {"type": kind, "text": "", "items": [], "statements": [], "columns": [], "rows": [], **fields}


def para(text: str) -> dict:
    # A name ending in a full stop ("... Pvt. Ltd.") must not produce "..".
    return block("paragraph", text=re.sub(r"(?<!\.)\.\.(?!\.)", ".", text))


def _sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip().rstrip(";,")
    if not text:
        return text
    return text if text.endswith((".", "!", "?", "]")) else text + "."


def _as_obligation(action: str, subject: str) -> str:
    """Turn a recommendation ("Enable MFA for admins") into a policy statement."""
    action = _sentence(action)
    first = action.split(" ", 1)[0].lower().strip(",.")
    if first in _IMPERATIVE:
        return f"{subject} must {action[0].lower()}{action[1:]}"
    return f"The following control must be implemented: {action}"


class _Writer:
    def __init__(self, payload: dict):
        self.policy = payload["policy"]
        self.config = payload["config"]
        self.analysis = payload["analysis"]
        self.detail = self.config.get("detail_level", "standard")
        self.style = self.config.get("writing_style", "formal")
        self.max_statements, self.max_risks, self.max_steps = _LIMITS.get(self.detail, _LIMITS["standard"])
        self.org = self.config.get("organization_name") or ph("organization name")
        self.owner = self.config.get("policy_owner") or ph("policy owner")
        self.subject = "The organization" if self.style != "simplified" else "We"
        # A multi-document analysis usually covers more than one subject.  Only
        # findings related to this policy type are written into the body; the
        # rest stay in the appendices.
        self.requirements = [r for r in self.analysis["requirements"] if self._relevant(r["statement"], r.get("category"))]
        self.other_requirements = len(self.analysis["requirements"]) - len(self.requirements)
        self.risks = sorted(
            self.analysis["risks"],
            key=lambda r: (
                not self._relevant(f"{r['title']} {r.get('description', '')}", None),
                _LEVEL_ORDER.get(r.get("level"), 4),
                -r.get("score", 0),
            ),
        )
        self.controls = self.analysis["controls"]
        self.gaps = self.analysis["gaps"]
        self.files = [f["file_name"] for f in self.analysis["source_files"]]

    def _relevant(self, text: str, category: str | None) -> bool:
        if self.policy.get("broad"):
            return True
        categories = self.policy.get("categories") or []
        if category and category in categories:
            return True
        lowered = text.lower()
        return any(keyword in lowered for keyword in self.policy.get("keywords", []))

    def _topic_for(self, statement: str) -> str | None:
        """The template topic whose keywords match the statement best."""
        lowered = statement.lower()
        best, best_hits = None, 0
        for topic in self.policy.get("topics", []):
            hits = sum(1 for keyword in topic["keywords"] if keyword in lowered)
            if hits > best_hits:
                best, best_hits = topic["title"], hits
        return best

    # ------------------------------------------------------------ sections
    def purpose(self) -> list[dict]:
        return [
            para(f"The purpose of this policy is to {self.policy['purpose']} at {self.org}."),
            para(
                "It turns the requirements found in the organization's own documents into a single set of mandatory statements, "
                "and adds proposed requirements where those documents show a risk or a gap."
            ),
        ]

    def abbreviations(self) -> list[dict]:
        # Left empty on purpose: the application lists the abbreviations the finished draft uses
        # (policy_generation/abbreviations.py), for a model's draft and for this one alike.
        return []

    def scope(self) -> list[dict]:
        summary = self.analysis["summary"]
        department = self.config.get("department")
        items = ["All employees, contractors and temporary staff of " + self.org + (f" ({department})" if department else "") + "."]
        if any("vendor" in s.lower() or "supplier" in s.lower() or "contractor" in s.lower() for s in summary.get("stakeholders", [])) or any(
            r["category"] == "vendor_management" for r in self.requirements
        ):
            items.append("Third parties and vendors that access, process or host the organization's information.")
        if summary.get("systems"):
            items.append("Systems and services named in the source documents: " + ", ".join(summary["systems"][:12]) + ".")
        else:
            items.append("Information systems and services: " + ph("list of systems in scope") + ".")
        if summary.get("stakeholders"):
            items.append("Roles and groups named in the source documents: " + ", ".join(summary["stakeholders"][:12]) + ".")
        return [para("This policy applies to:"), block("bullets", items=items), para("Out of scope: " + ph("any exclusions") + ".")]

    def objectives(self) -> list[dict]:
        items = list(self.policy.get("objectives", []))
        if self.risks:
            items.append("Reduce the risks recorded in the risk register, in particular: " + "; ".join(r["title"] for r in self.risks[:3]) + ".")
        return [block("bullets", items=items)]

    def definitions(self) -> list[dict]:
        rows = [[term, meaning] for term, meaning in self.policy.get("definitions", {}).items()]
        if not rows:
            return [para("No specialist terms are used in this policy.")]
        return [block("table", columns=["Term", "Definition"], rows=rows)]

    def principles(self) -> list[dict]:
        return [block("bullets", items=list(self.policy.get("principles", [])))]

    def roles_responsibilities(self) -> list[dict]:
        grouped: dict[str, list[dict]] = {}
        for item in self.analysis["responsibilities"]:
            grouped.setdefault(item["role"], []).append(item)
        rows = []
        for role, items in list(grouped.items())[:14]:
            duties = "; ".join(dict.fromkeys(_sentence(i["responsibility"]).rstrip(".") for i in items[:3]))
            sources = ", ".join(dict.fromkeys(i["source"]["file_name"] for i in items if i.get("source", {}).get("file_name")))
            rows.append([role, duties + ".", f"Documented ({sources})" if sources else "Documented"])
        rows.append(
            [
                f"Policy Owner ({self.owner})",
                f"Maintains this policy, reviews it {self.config.get('review_frequency', 'annually').lower()}, and decides on exceptions.",
                "Proposed",
            ]
        )
        if not any(re.search(r"employee|staff|users", role, re.IGNORECASE) for role in grouped):
            rows.append(["All personnel", "Follow this policy and report suspected breaches of it.", "Proposed"])
        blocks = [
            para(
                "Roles marked 'Documented' are assigned in the source documents. Roles marked 'Proposed' are suggested by this "
                "draft and must be confirmed by the organization."
            ),
            block("table", columns=["Role / Department", "Responsibilities", "Basis"], rows=rows),
        ]
        return blocks

    def policy_requirements(self) -> list[dict]:
        blocks: list[dict] = []
        contradictions = self.analysis.get("contradictions") or []
        if contradictions:
            blocks.append(
                para(
                    "The source documents disagree on the following values. The statements below quote the sources as written; "
                    "the organization must agree the correct value: "
                    + "; ".join(f"{c['topic']} ({ph('agreed ' + c['topic'].lower())})" for c in contradictions)
                    + "."
                )
            )

        related_controls: dict[str, list[str]] = {}
        for control in self.controls:
            for req_id in control.get("related_requirements") or []:
                related_controls.setdefault(req_id, []).append(control["ref_id"])

        if self.other_requirements:
            blocks.append(
                para(
                    f"{self.other_requirements} further requirement(s) identified in the source documents concern subjects outside "
                    f"this {self.policy['name']}. They are not restated here and remain listed in the Control Matrix appendix."
                )
            )

        groups: dict[str, list[dict]] = {t["title"]: [] for t in self.policy.get("topics", [])}
        for requirement in self.requirements:
            title = self._topic_for(requirement["statement"]) or "General Requirements"
            groups.setdefault(title, []).append(requirement)

        for title, items in groups.items():
            if not items:
                continue
            statements = []
            for requirement in items[: self.max_statements]:
                text = _sentence(requirement["statement"])
                text = re.sub(r"\bshall\b", "must", text)
                statements.append(
                    {
                        "id": "",
                        "text": text,
                        "basis": "documented" if requirement.get("basis") == "documented" else "proposed",
                        "refs": [requirement["ref_id"], *related_controls.get(requirement["ref_id"], [])[:2]],
                    }
                )
            blocks += [block("subheading", text=title), block("statements", statements=statements)]
            if len(items) > self.max_statements:
                blocks.append(para(f"{len(items) - self.max_statements} further requirement(s) in this area are listed in the Control Matrix appendix."))

        proposed = []
        risk_by_id = {r["ref_id"]: r for r in self.risks}
        for control in self.controls:
            if control.get("basis") != "recommended":
                continue
            linked = [risk_by_id[r] for r in control.get("related_risks") or [] if r in risk_by_id]
            if self.detail != "comprehensive" and not any(r.get("level") in ("critical", "high", "medium") for r in linked):
                continue
            action = control.get("recommended_action") or control.get("description") or control["name"]
            if not self._relevant(f"{control['name']} {action}", None):
                continue
            proposed.append(
                {
                    "id": "",
                    "text": _as_obligation(action, self.subject),
                    "basis": "proposed",
                    "refs": [control["ref_id"], *[r["ref_id"] for r in linked[:2]]],
                }
            )
        if proposed:
            blocks += [
                block("subheading", text="Proposed Requirements Addressing Identified Risks"),
                para("These requirements are not stated in the source documents. They are proposed to treat the risks and gaps found in the analysis."),
                block("statements", statements=proposed[: self.max_statements]),
            ]
        if not self.requirements and not proposed:
            # Nothing in the documents concerns this policy's subject: offer
            # clearly labelled starting points instead of an empty section.
            starters = [
                {
                    "id": "",
                    "text": f"{self.subject} must define and document its requirements for {topic['title'].lower()}: "
                    + ph(f"requirements for {topic['title'].lower()}"),
                    "basis": "proposed",
                    "refs": [],
                }
                for topic in self.policy.get("topics", [])
            ] or [{"id": "", "text": f"{self.subject} must define its requirements for this policy: " + ph("policy requirements"), "basis": "proposed", "refs": []}]
            blocks += [
                block("subheading", text="Proposed Starting Points"),
                para(
                    f"The source documents contain no requirements or risks on the subject of this {self.policy['name']}. "
                    "The statements below are generic starting points to be completed by the policy owner; none of them comes from the documents."
                ),
                block("statements", statements=starters),
            ]
        return blocks

    def procedures(self) -> list[dict]:
        steps = [
            "Communicate this policy to everyone in scope and record their acknowledgement.",
            "Assign an owner to each requirement in this policy and record it in the Control Matrix.",
        ]
        actions: list[str] = []
        in_scope = {r["ref_id"] for r in self.requirements}
        for gap in self.gaps:
            if gap["status"] != "compliant" and gap.get("suggested_action") and gap["requirement_id"] in in_scope:
                actions.append(f"{_sentence(gap['suggested_action'])} (addresses {gap['requirement_id']})")
        for control in self.controls:
            action = control.get("recommended_action")
            if control.get("basis") == "recommended" and action and self._relevant(f"{control['name']} {action}", None):
                actions.append(f"{_sentence(action)} ({control['ref_id']})")
        seen: set[str] = set()
        for action in actions:
            key = action.split(" (")[0].lower()
            if key not in seen:
                seen.add(key)
                steps.append(action)
        steps = steps[: self.max_steps]
        steps.append("Record evidence that each control operates, so that it can be shown at review or audit.")
        return [
            para("The following steps put the requirements of this policy into practice. Steps that reference a finding id come from the gap analysis and recommended controls."),
            block("numbered", items=steps),
        ]

    def risk_controls(self) -> list[dict]:
        scoring = self.analysis.get("scoring", {})
        bands = ", ".join(f"{b['label']} {b['range']}" for b in scoring.get("levels", []))
        blocks = [
            para(
                "Risks relevant to this policy are scored as likelihood multiplied by impact, each rated from 1 to 5"
                + (f" ({bands})." if bands else ".")
                + " Ratings taken from the source documents are marked as documented; others are estimates that the risk owner must validate."
            )
        ]
        if not self.risks:
            blocks.append(para("The analysis did not identify specific risks in the source documents. A risk assessment should be completed: " + ph("risk assessment date and owner") + "."))
            return blocks
        rows = [
            [
                r["ref_id"],
                r["title"],
                r.get("level", "unrated").title(),
                "Documented" if r.get("origin") == "documented" else "Inferred",
                _sentence(r.get("recommended_mitigation") or ph("treatment decision")),
            ]
            for r in self.risks[: self.max_risks]
        ]
        blocks.append(block("table", columns=["ID", "Risk", "Level", "Origin", "Treatment"], rows=rows))
        if len(self.risks) > self.max_risks:
            blocks.append(para(f"The full list of {len(self.risks)} risks is in the Risk Register appendix."))
        existing = [c for c in self.controls if c.get("basis") == "existing"]
        recommended = [c for c in self.controls if c.get("basis") == "recommended"]

        def label(control: dict) -> str:
            extra = f" [{control.get('control_type', '')}, {control.get('control_function', '')}]" if self.style == "technical" else ""
            return f"{control['ref_id']} {control['name']}{extra}"

        if existing:
            blocks += [
                block("subheading", text="Existing Controls Evidenced in the Source Documents"),
                block("bullets", items=[f"{label(c)} - {c.get('status', 'unknown').replace('_', ' ')}" for c in existing[: self.max_risks + 4]]),
            ]
        if recommended:
            blocks += [
                block("subheading", text="Recommended Controls (Proposed)"),
                block("bullets", items=[label(c) for c in recommended[: self.max_risks + 4]]),
            ]
        return blocks

    def monitoring_compliance(self) -> list[dict]:
        items = list(self.policy.get("monitoring", []))
        counts = {s: sum(1 for g in self.gaps if g["status"] == s) for s in ("not_evidenced", "partially_addressed")}
        blocks = [block("bullets", items=items)]
        if self.gaps:
            blocks.append(
                para(
                    f"The gap analysis recorded {counts['not_evidenced']} requirement(s) as Not Evidenced and "
                    f"{counts['partially_addressed']} as Partially Addressed. The policy owner must track these to closure and report progress to management."
                )
            )
        blocks.append(para("Non-compliance with this policy is handled under " + ph("disciplinary or contractual process") + "."))
        return blocks

    def incident_reporting(self) -> list[dict]:
        window = next((f for f in self.analysis.get("facts", []) if f["topic"] == "Incident reporting window"), None)
        contradicted = any(c["topic"] == "Incident reporting window" for c in self.analysis.get("contradictions") or [])
        if window and not contradicted:
            timing = f"within {window['value']} (as stated in {window['file_name']})"
        else:
            timing = "within " + ph("incident reporting time limit")
        return [
            para(f"Anyone who suspects a breach of this policy or a related security incident must report it {timing} to {ph('incident reporting contact')}."),
            block(
                "numbered",
                items=[
                    "Report the suspected incident through the channel above, with what was observed and when.",
                    "The recipient records the report, assesses its severity and informs the policy owner.",
                    "Incidents that cannot be contained at the first level are escalated to " + ph("escalation contact") + ".",
                    "After resolution, the cause is reviewed and this policy is updated if needed.",
                ],
            ),
        ]

    def exceptions(self) -> list[dict]:
        approver = self.config.get("approver") or self.config.get("policy_owner") or ph("exception approver")
        return [
            para("Where a requirement of this policy cannot be met, an exception must be requested before the requirement is breached."),
            block(
                "numbered",
                items=[
                    "The requester documents the requirement concerned, the reason, the risk and any compensating controls.",
                    f"The exception is assessed and approved or rejected by {approver}.",
                    "Approved exceptions are time-limited, with a maximum duration of " + ph("maximum exception period") + ".",
                    "All exceptions are recorded in a register and reviewed when they expire.",
                ],
            ),
        ]

    def review_maintenance(self) -> list[dict]:
        frequency = self.config.get("review_frequency") or ph("review frequency")
        return [
            para(f"This policy is reviewed {frequency.lower() if not frequency.startswith('[') else frequency} by {self.owner}, and earlier when any of the following occurs:"),
            block(
                "bullets",
                items=[
                    "A significant security incident or audit finding related to this policy.",
                    "A material change to the organization's systems, services, vendors or structure.",
                    "A change in legal, regulatory or contractual obligations.",
                ],
            ),
            para("Changes are recorded in the version history, and each new version requires approval before it takes effect."),
        ]

    def references(self) -> list[dict]:
        items = [f"Source document: {f['file_name']} ({f['file_type'].upper()})" for f in self.analysis["source_files"]]
        blocks = [para("This policy was drafted from the following uploaded documents:"), block("bullets", items=items)]
        standards = self.analysis.get("standards_mentioned") or []
        if standards:
            blocks += [
                para("The source documents themselves refer to the following standards or laws. They are listed because the sources cite them, not as a statement of compliance:"),
                block("bullets", items=[f"{s['name']} (mentioned in {s['file_name']})" for s in standards]),
            ]
        else:
            blocks.append(para("The source documents do not cite external standards or legislation, so none are asserted here."))
        return blocks

    def build(self, section_spec: dict) -> dict:
        builder = getattr(self, section_spec["id"], None)
        blocks = builder() if builder else [para(ph(f"content for {section_spec['heading']}"))]
        return {"id": section_spec["id"], "heading": section_spec["heading"], "blocks": blocks}


def generate_policy(payload: dict) -> dict:
    writer = _Writer(payload)
    title = payload["config"].get("custom_title") or payload["policy"]["name"]
    return {"title": title, "sections": [writer.build(spec) for spec in payload["sections"]]}


def revise_sections(payload: dict) -> dict:
    writer = _Writer(payload)
    instruction = (payload.get("instruction") or "").strip()
    sections = []
    for spec in payload["sections"]:
        section = writer.build(spec)
        if instruction:
            section["blocks"].append(
                para(
                    "[Simulated revision: mock mode rebuilt this section from the analysis but cannot interpret free-text "
                    f"instructions. Requested change, not applied: \"{instruction[:300]}\"]"
                )
            )
        sections.append(section)
    return {"sections": sections}
