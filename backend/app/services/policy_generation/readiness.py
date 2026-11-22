"""Approval readiness is separate from structural validation and ISO conformity."""

from __future__ import annotations

import json
import re
from datetime import timedelta

from app.schemas.policy import PLACEHOLDER_PATTERN
from app.services.policy_generation.content import section_text


def professional_checks(content: dict, policy_type: str) -> list[dict]:
    sections = {s["id"]: section_text(s).lower() for s in content.get("sections", [])}
    text = "\n".join(sections.values())
    issues = [
        {
            "code": "supporting_evidence",
            "severity": "warning",
            "section_id": None,
            "message": "Verify assigned roles, applicable obligations, approved supporting records and implementation evidence. Automated checks do not establish conformity or operational effectiveness.",
        }
    ]

    def check(code, message, section, patterns):
        if not all(re.search(pattern, text) for pattern in patterns):
            issues.append(
                {
                    "code": code,
                    "severity": "warning",
                    "section_id": section,
                    "message": message,
                }
            )

    if policy_type in ("bcdr", "business_continuity", "ims_i12", "ims_s10"):
        check(
            "continuity_security",
            "Review security during disruption: controlled emergency access, logging, secure alternate working and validation before recovery.",
            "policy_requirements",
            [
                r"emergency access",
                r"logg|audit trail",
                r"alternate|remote",
                r"secur\w* (?:valid|check)|valid\w* secur",
            ],
        )
        check(
            "recovery_authority",
            "Confirm declaration authority, deputies and customer recovery authorization.",
            "roles_responsibilities",
            [
                r"declar|activat",
                r"deput|backup (?:role|contact)|alternate authority",
                r"customer.*authori",
            ],
        )
    if policy_type == "configuration_management":
        check(
            "change_independence",
            "Define independent change approval or a documented compensating review for a small team.",
            "policy_requirements",
            [r"independent|segregat|self.approv|compensating review"],
        )
        check(
            "emergency_change",
            "Define emergency change authority, retained evidence and retrospective review, including customer approval boundaries.",
            "procedures",
            [
                r"emergency",
                r"retrospective|post.change review",
                r"customer.*approv|approv.*customer",
            ],
        )
        check(
            "change_closure",
            "Include implementation validation, configuration-record updates and change closure.",
            "procedures",
            [
                r"post.implementation|post.change|after implementation",
                r"clos(?:e|ure)",
                r"updat\w*.*(?:record|baseline|cmdb)",
            ],
        )
        check(
            "customer_control_boundary",
            "Clarify internal tools versus approved customer tools and who authorizes customer-system changes.",
            "scope",
            [
                r"customer",
                r"approved customer|customer.approved|customer.authorized",
                r"internal|internally",
            ],
        )
    if re.search(r"\b(?:within \d+|\d+ (?:hours?|days?|months?)|weekly|daily)\b", text):
        issues.append(
            {
                "code": "operational_commitments",
                "severity": "warning",
                "section_id": "policy_requirements",
                "message": "Confirm fixed deadlines, retention periods and frequencies are approved, feasible and consistent with customer contracts; they are not automatically ISO-prescribed.",
            }
        )
    if re.search(r"\b(?:gdpr|hipaa)\b", text):
        issues.append(
            {
                "code": "legal_applicability",
                "severity": "warning",
                "section_id": "references",
                "message": "Verify applicability of named legislation against the approved obligations register. A source mention is not proof of applicability.",
            }
        )
    if re.search(r"\buse .{0,160} for unknown values|where evidence is absent", text):
        issues.append(
            {
                "code": "authoring_instruction",
                "severity": "error",
                "section_id": "policy_requirements",
                "message": "Replace drafting instructions about unknown values with an operational requirement to record and approve the information.",
            }
        )
    return issues


def assess(policy, version, quality: dict) -> dict:
    config = version.content.get("_config", policy.config)
    missing = {}
    for field, label in [
        ("organization_name", "organization name"),
        ("department", "department"),
        ("policy_owner", "policy owner"),
        ("approver", "approver"),
        ("effective_date", "effective date"),
        ("review_frequency", "review frequency"),
    ]:
        value = config.get(field, "")
        if not value or PLACEHOLDER_PATTERN.search(value):
            missing[label] = {
                "label": label,
                "placeholder": value
                if PLACEHOLDER_PATTERN.search(value)
                else f"[To be confirmed: {label}]",
                "sections": ["document_control"],
                "field": field,
            }
    for section in version.content.get("sections", []):
        for placeholder in PLACEHOLDER_PATTERN.findall(
            json.dumps(section, ensure_ascii=False)
        ):
            label = placeholder.split(":", 1)[1][:-1].strip()
            item = missing.setdefault(
                label.lower(),
                {
                    "label": label,
                    "placeholder": placeholder,
                    "sections": [],
                    "field": None,
                },
            )
            if section["id"] not in item["sections"]:
                item["sections"].append(section["id"])
    issues = quality.get("issues", []) + professional_checks(
        version.content, policy.policy_type
    )
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    unique = list({(i["code"], i.get("section_id")): i for i in warnings}.values())
    return {
        "structure_checked": not any(
            i["severity"] == "error" for i in quality.get("issues", [])
        ),
        "ready_for_approval": not errors and not missing and not policy.simulated,
        "missing_information": list(missing.values()),
        "blocking_issues": errors,
        "review_checks": unique,
        "simulated": policy.simulated,
        "notice": "These checks support review; they do not establish ISO 27001 conformity.",
    }


def review_due(policy) -> str | None:
    event = next(
        (
            e
            for e in reversed(policy.review_events)
            if e.action == "approve" and e.version_number == policy.approved_version
        ),
        None,
    )
    if not event:
        return None
    days = {
        "Quarterly": 90,
        "Every six months": 182,
        "Annually": 365,
        "Every two years": 730,
    }.get(policy.config.get("review_frequency"))
    return (
        (event.created_at.date() + timedelta(days=days)).isoformat() if days else None
    )
