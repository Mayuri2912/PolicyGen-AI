"""Policy structure rules: no Executive Summary, Abbreviations before Scope, no personal names.

The rules are enforced in one place (``policy_generation/structure.py``) on
every path that creates a version, so each test here drives a different path:
the rule-based mock, a scripted "real" model that breaks the rules, an AI
revision, a regeneration and a manual edit.
"""

from __future__ import annotations

import copy
import io
import json

import docx
import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app.database.session import session_scope
from app.models.entities import PolicyVersion
from app.schemas.policy import PolicyBlock
from app.services import storage
from app.services.policy_generation import abbreviations, people, quality, structure
from app.services.policy_generation.templates import (
    abbreviation_glossary,
    get_template,
    is_retired,
    load_templates,
    retired_sections,
    section_library,
)
from tests.conftest import all_text, analyze, generate, sample, upload_ok

PEOPLE_DOCUMENT = b"""Harbourline Bakery Ltd. - Access Control Notes (fictional test document)

1. Responsibilities
Priya Sharma (IT Manager) is responsible for approving access to the ordering system.
The Information Security Team must review privileged accounts every month.
Mr. Daniel Okafor must sign off every new supplier account.

2. Rules
All staff must use multi-factor authentication for remote access.
Passwords must be at least 12 characters long.
Access for leavers must be removed on their last working day.
"""


@pytest.fixture
def security_analysis(client):
    return analyze(client, upload_ok(client, sample("sample_security.txt"), sample("sample_risk_assessment.xlsx")))


def block(kind: str, **fields) -> dict:
    return PolicyBlock(type=kind, **fields).model_dump()


def section(section_id: str, heading: str, *blocks, origin: str = "ai_generated") -> dict:
    return {"id": section_id, "heading": heading, "origin": origin, "blocks": list(blocks)}


def by_id(report: dict, section_id: str) -> dict:
    return next(s for s in report["document"]["sections"] if s["id"] == section_id)


def narrative(report: dict) -> list[dict]:
    return [s for s in report["document"]["sections"] if s["kind"] == "section"]


def table_of(report: dict, section_id: str) -> dict:
    return next(e for e in by_id(report, section_id)["elements"] if e["type"] == "table")


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def download(client, report_id: str, fmt: str, version: int | None = None) -> bytes:
    response = client.get(f"/api/reports/{report_id}/download?format={fmt}" + (f"&version={version}" if version else ""))
    assert response.status_code == 200, response.text
    return response.content


def scripted_draft(change):
    """A scripted generate_policy entry: the mock's draft, altered by ``change``."""

    def entry(request, result):
        change(result)
        return result

    return entry


# --------------------------------------------------------- section template
def test_template_has_no_executive_summary_and_abbreviations_precede_scope(client):
    ids = [s.id for s in section_library()]
    assert ids[:4] == ["purpose", "abbreviations", "scope", "definitions"]
    assert "executive_summary" not in ids and len(ids) == len(set(ids)) == 15
    assert [s.id for s in retired_sections()] == ["executive_summary"]
    assert is_retired("executive_summary") and is_retired("summary", "Executive  Summary") and not is_retired("purpose", "Purpose")

    headings = {s.id: s.heading for s in section_library()}
    assert headings["abbreviations"] == "Abbreviations" and headings["definitions"] == "Definitions"
    for template in load_templates().values():
        order = [s.id for s in template.sections()]
        assert "executive_summary" not in order
        assert order.index("abbreviations") + 1 == order.index("scope")
        assert not any(term.isupper() for term in template.definitions), "abbreviations belong in the Abbreviations section"

    for policy in client.get("/api/policies/types").json()["policy_types"]:
        listed = [s["id"] for s in policy["sections"]]
        assert "executive_summary" not in listed and listed[:3] == ["purpose", "abbreviations", "scope"]


@pytest.mark.parametrize("policy_type", ["information_security", "access_control", "vendor_management", "business_continuity", "remote_work"])
def test_generated_policy_follows_the_new_structure(client, security_analysis, policy_type):
    report = generate(client, security_analysis["id"], policy_type=policy_type)
    sections = narrative(report)
    titles = [s["title"] for s in sections]
    assert titles[:3] == ["Purpose", "Abbreviations", "Scope"]
    assert "Executive Summary" not in all_text(report)
    assert [s["number"] for s in sections] == [str(n) for n in range(1, len(sections) + 1)]  # renumbered from the new order
    assert titles == [s.heading for s in get_template(policy_type).sections()]
    assert report["quality_report"]["counts"]["error"] == 0, report["quality_report"]["issues"]
    assert [s["id"] for s in report["sections"]][:3] == ["purpose", "abbreviations", "scope"]  # the editable list matches


# -------------------------------------------------------------- documents
def test_docx_and_pdf_have_the_new_order_a_matching_contents_table_and_the_abbreviations_table(client, security_analysis):
    report = generate(client, security_analysis["id"], policy_type="access_control", organization_name="Brightwater Analytics Pvt. Ltd.")
    rows = table_of(report, "abbreviations")["rows"]
    assert ["MFA", "Multi-Factor Authentication"] in rows and len(rows) >= 3
    expected = [f"{s['number']}. {s['title']}" for s in narrative(report)]
    assert expected[:4] == ["1. Purpose", "2. Abbreviations", "3. Scope", "4. Definitions"]

    document = docx.Document(io.BytesIO(download(client, report["id"], "docx")))
    headings = [p.text for p in document.paragraphs if p.style.name == "Heading 1"]
    assert headings[: len(expected)] == expected and headings[len(expected)].startswith("Appendix A - ")
    assert not any("Executive Summary" in p.text for p in document.paragraphs)
    table = next(t for t in document.tables if [c.text for c in t.rows[0].cells] == ["Abbreviation", "Full Form / Description"])
    assert [[c.text for c in row.cells] for row in table.rows[1:]] == rows
    # The table sits between the Abbreviations and Scope headings.
    flow = list(document.iter_inner_content())

    def heading_at(label: str) -> int:
        return next(i for i, item in enumerate(flow) if getattr(item, "text", None) == label and item.style.name == "Heading 1")

    table_at = next(i for i, item in enumerate(flow) if getattr(item, "_tbl", None) is table._tbl)
    assert heading_at("2. Abbreviations") < table_at < heading_at("3. Scope")
    assert 'TOC \\o "1-1"' in document.element.xml  # the contents table is built from the headings

    pdf = download(client, report["id"], "pdf")
    reader = PdfReader(io.BytesIO(pdf))
    outline = [item.title for item in reader.outline]
    assert outline[: len(expected)] == expected
    text = pdf_text(pdf)
    assert "Executive Summary" not in text
    contents = next(page.extract_text() for page in reader.pages if "Table of Contents" in page.extract_text())
    assert [contents.index(label) for label in expected[:4]] == sorted(contents.index(label) for label in expected[:4])
    squashed = "".join(text.split())
    assert "AbbreviationFullForm/Description" in squashed and "MFAMulti-FactorAuthentication" in squashed
    assert squashed.index("2.Abbreviations") < squashed.index("MFAMulti-FactorAuthentication") < squashed.rindex("3.Scope")
    # Appendices still work after the sections moved.
    assert [s["id"] for s in report["document"]["sections"] if s["kind"] == "appendix"][:2] == ["appendix_risk_register", "appendix_control_matrix"]
    assert "Risk matrix" in text and "Control Catalogue" in text


# ------------------------------------------- a model that breaks the rules
def test_executive_summary_written_by_a_model_is_dropped_and_never_requested(client, scripted, security_analysis):
    summary_blocks = [block("paragraph", text="This summary restates the whole policy for senior readers and should not be here at all.")]

    def add_summaries(result):
        result["sections"].insert(0, {"id": "executive_summary", "heading": "Executive Summary", "blocks": summary_blocks})
        result["sections"].append({"id": "overview", "heading": "Executive summary", "blocks": summary_blocks})  # another id, same thing

    provider = scripted(by_task={"generate_policy": [scripted_draft(add_summaries)]})
    report = generate(client, security_analysis["id"], policy_type="information_security")
    assert report["status"] == "completed" and provider.tasks() == ["generate_policy"]  # no correction round was needed
    assert "summary" not in all_text(report).lower().replace("document summary", "")
    assert [s["title"] for s in narrative(report)][:3] == ["Purpose", "Abbreviations", "Scope"]
    assert report["quality_report"]["counts"]["error"] == 0

    request = provider.requests[0]
    assert "executive_summary" not in request.user and "Do not write an Executive Summary" in request.user
    assert "It has no executive summary" in request.system and "never names an individual person" in request.system
    assert request.user.index("id `abbreviations`") < request.user.index("id `scope`")
    assert "`Abbreviation` and `Full Form / Description`" in request.user and "`Role / Department`" in request.user
    assert [s["id"] for s in request.payload["sections"]][:3] == ["purpose", "abbreviations", "scope"]


def test_abbreviations_from_a_model_are_filtered_corrected_and_completed(client, scripted, security_analysis):
    def write_abbreviations(result):
        for item in result["sections"]:
            if item["id"] == "abbreviations":
                item["blocks"] = [
                    block(
                        "table", columns=["Short form", "Meaning", "Notes"],
                        rows=[
                            ["MFA", "Mandatory Firewall", ""],  # an expansion that does not fit the letters
                            ["MFA", "Multi-Factor Authentication", "duplicate"],
                            ["ZTNA", "Zero Trust Network Access", "not used anywhere in the policy"],
                            ["BRW", "Brightwater Regional Warehouse", "invented for the organization"],
                            ["Least privilege", "Granting only the minimum access needed.", "a term, not an abbreviation"],
                        ],
                    )
                ]
            if item["id"] == "purpose":
                item["blocks"].append(block("paragraph", text="Changes to access rules are agreed by the Change Advisory Board (CAB). The QRT team reviews them."))

    scripted(by_task={"generate_policy": [scripted_draft(write_abbreviations)]})
    report = generate(client, security_analysis["id"], policy_type="access_control")
    table = table_of(report, "abbreviations")
    assert table["columns"] == ["Abbreviation", "Full Form / Description"]
    listed = dict(table["rows"])
    assert listed["MFA"] == "Multi-Factor Authentication"  # the standard form replaced the wrong one, and it is listed once
    assert listed["CAB"] == "Change Advisory Board"  # spelled out in the policy text
    assert "ZTNA" not in listed and "BRW" not in listed and "Least privilege" not in listed
    assert "QRT" not in listed  # used, but no full form is known: reported, never invented
    assert list(listed) == sorted(listed, key=str.casefold) and len(table["rows"]) == len(listed)
    issues = {i["code"]: i["message"] for i in report["quality_report"]["issues"]}
    assert "QRT" in issues["unexplained_abbreviation"] and "Least privilege" in issues["misplaced_term"]
    assert report["quality_report"]["counts"]["error"] == 0
    # Definitions keeps the term and does not repeat the abbreviation.
    terms = [row[0] for row in table_of(report, "definitions")["rows"]]
    assert "Least privilege" in terms and "MFA" not in terms and terms.count("Least privilege") == 1


def test_abbreviations_rules_unit():
    glossary = abbreviation_glossary()
    assert glossary["ISMS"] == "Information Security Management System" and glossary["RBAC"] == "Role-Based Access Control"
    assert abbreviations.is_abbreviation("RBAC") and abbreviations.is_abbreviation("SaaS") and not abbreviations.is_abbreviation("Access review")
    assert abbreviations.expands("RBAC", "Role-Based Access Control") and not abbreviations.expands("MFA", "Mandatory Firewall")
    assert abbreviations.spelled_out("Use a virtual private network (VPN). See the note (A). Key risk indicators (KRIs) apply.") == {
        "VPN": "Virtual Private Network"
    }
    known, unknown = abbreviations.in_use("RBAC and MFA apply; see REQ-001, ISP-002 and NOTE III. The ACME unit owns PCI DSS scope. KPIs are reported.")
    assert known == {"RBAC", "MFA", "PCI DSS", "KPI"} and unknown == {"ACME"}

    template = get_template("password")

    def content(*sections):
        return {"title": "T", "sections": list(sections)}

    # Nothing used: one sentence, no invented rows.
    plain = content(section("purpose", "Purpose", block("paragraph", text="Passwords protect accounts and must be kept secret by everyone.")))
    structure.enforce(plain, template)
    listing = next(s for s in plain["sections"] if s["id"] == "abbreviations")
    assert [b["type"] for b in listing["blocks"]] == ["paragraph"] and listing["blocks"][0]["text"] == abbreviations.NONE_USED
    assert [s["id"] for s in plain["sections"]] == ["purpose", "abbreviations"]

    # Definitions that only expand an abbreviation are dropped; real definitions move under the full term.
    mixed = content(
        section("scope", "Scope", block("paragraph", text="MFA and SSO are required for the VPN. The ISMS covers all sites.")),
        section(
            "definitions", "Definitions",
            block("table", columns=["Term", "Definition"], rows=[["MFA", "Multi-factor authentication"], ["VPN", "An encrypted tunnel to the office network."], ["Passphrase", "A long password made of words."]]),
        ),
        section("purpose", "Purpose", block("paragraph", text="This policy sets the rules for passwords across the organization.")),
    )
    notes = structure.enforce(mixed, template)
    assert [s["id"] for s in mixed["sections"]] == ["purpose", "abbreviations", "scope", "definitions"]  # template order restored
    rows = next(b for b in mixed["sections"][1]["blocks"] if b["type"] == "table")["rows"]
    assert rows == [
        ["ISMS", "Information Security Management System"], ["MFA", "Multi-Factor Authentication"],
        ["SSO", "Single Sign-On"], ["VPN", "Virtual Private Network"],
    ]
    assert mixed["sections"][3]["blocks"][0]["rows"] == [
        ["Virtual Private Network", "An encrypted tunnel to the office network."], ["Passphrase", "A long password made of words."],
    ]
    assert any(n["code"] == "duplicate_definition" for n in notes)
    assert quality.validate_policy(mixed, template, valid_ids=set(), source_standards=set())["issues"] is not None
    assert not abbreviations.audit(mixed)

    # A user's own rows are kept, even for an abbreviation the text does not use.
    edited = content(
        section("scope", "Scope", block("paragraph", text="MFA is required for all remote access to systems.")),
        section("abbreviations", "Abbreviations", block("table", columns=["A", "B"], rows=[["HQ", "Head Office"], ["MFA", "Multi-Factor Authentication (two factors)"]]), origin="user_edited"),
    )
    structure.enforce(edited, template)
    assert next(b for b in edited["sections"][0]["blocks"] if b["type"] == "table")["rows"] == [
        ["HQ", "Head Office"], ["MFA", "Multi-Factor Authentication (two factors)"],
    ]


def test_quality_check_reports_structure_problems():
    template = get_template("access_control")
    content = {
        "title": "T",
        "sections": [
            section("executive_summary", "Executive Summary", block("paragraph", text="A summary of the policy for senior readers that should not exist.")),
            section("scope", "Scope", block("paragraph", text="Everyone in the organization who uses MFA to reach its systems.")),
            section("abbreviations", "Abbreviations", block("table", columns=["Abbreviation", "Full Form / Description"], rows=[["MFA", "Multi-Factor Authentication"], ["mfa", "Again"]])),
            section("definitions", "Definitions", block("table", columns=["Term", "Definition"], rows=[["MFA", "Two or more factors."]])),
            section("roles_responsibilities", "Roles and Responsibilities", block("table", columns=["Role", "Responsibilities"], rows=[["Xiu Zhang", "Approves access."], ["Dr. Meera Nair", "Reviews logs."]])),
        ],
    }
    report = quality.validate_policy(content, template, valid_ids=set(), source_standards=set())
    found = {(i["code"], i["section_id"]) for i in report["issues"]}
    assert ("retired_section", "executive_summary") in found
    assert ("invalid_structure", "abbreviations") in found and ("duplicate_definition", "definitions") in found
    messages = [i["message"] for i in report["issues"] if i["code"] == "personal_name"]
    assert any("'Meera Nair'" in m and "names an individual" in m for m in messages)
    assert any("'Xiu Zhang'" in m and "may name an individual" in m for m in messages)
    assert "roles_responsibilities" in quality.sections_needing_correction(report)  # the model gets one chance to fix it


# --------------------------------------------------------- personal names
def test_personal_names_are_recognised_without_touching_other_proper_nouns():
    index = people.PeopleIndex(non_persons=["Harbourline Bakery Ltd.", "Brightwater Analytics"])
    assert index.clean_role_label("Anita Desai (IT Manager)") == ("IT Manager", True)
    assert index.clean_role_label("IT Manager - Anita Desai") == ("IT Manager", True)
    assert index.clean_role_label("Dr. A. K. Sharma, CISO") == ("CISO", True)
    assert index.clean_role_label("Xiu Zhang (Finance Head)") == ("Finance Head", True)  # the role beside it marks it as a name
    assert index.clean_role_label("Mr. Rao") == (people.ROLE_PLACEHOLDER, True)  # a role is never guessed
    assert index.clean_role_label("Anita Desai") == ("IT Manager", True)  # learned from the labels above

    for label in (
        "Chief Information Security Officer", "Information Security Team", "Department Heads", "Employees", "Human Resources Department",
        "Third-Party Service Providers", "Legal, Risk and Compliance Department", "Policy Owner (Head of IT)", "Active Directory Team",
        "Brightwater Analytics", "Data Owner - Analytics", "Microsoft Azure Administrators", "System Administrator",
    ):
        assert index.clean_role_label(label) == (label, False), label
        assert not index.possible_name(label), label
    # With no evidence that it is a person, an unusual name is reported, not rewritten.
    fresh = people.PeopleIndex()
    assert fresh.possible_name("Xiu Zhang") and fresh.clean_role_label("Xiu Zhang") == ("Xiu Zhang", False)
    assert index.clean_role_label("Xiu Zhang") == ("Finance Head", True)  # ...but here a label above said who it is

    untouched = (
        "Brightwater Analytics uses Active Directory, Microsoft Azure and Amazon Web Services under ISO 27001. "
        "Grant access only on request. Mark all records. Harbourline Bakery Ltd. is the data controller."
    )
    assert index.scrub(untouched) == (untouched, 0)
    assert index.scrub("The IT Manager (Anita Desai) approves access.") == ("The IT Manager approves access.", 1)
    assert index.scrub("Anita Desai must review logs. Ask Anita Desai's deputy.") == ("The IT Manager must review logs. Ask the IT Manager's deputy.", 2)
    assert index.scrub("The CISO, Dr. Rohan Mehta, must sign off.") == ("The CISO must sign off.", 1)
    assert index.scrub("Contact Priya Sharma for access.") == (f"Contact {people.ROLE_PLACEHOLDER} for access.", 1)
    assert index.scrub("Rahul Verma (Network Administrator) maintains the firewalls.") == ("Network Administrator maintains the firewalls.", 1)


def test_names_from_documents_and_configuration_never_reach_the_policy_but_stay_in_the_evidence(client):
    analysis = analyze(client, upload_ok(client, ("access_notes.txt", PEOPLE_DOCUMENT)))
    evidence = json.dumps(analysis)
    assert "Priya Sharma" in evidence  # the analysis keeps what the document says
    report = generate(
        client, analysis["id"], policy_type="access_control", organization_name="Harbourline Bakery Ltd.",
        policy_owner="Sunita Rao", approver="Head of Operations",
    )
    policy_text = json.dumps([s for s in narrative(report) if s["id"] != "references"])
    for name in ("Priya Sharma", "Daniel Okafor", "Sunita Rao", "Sharma", "Okafor"):
        assert name not in policy_text, name
    roles = table_of(report, "roles_responsibilities")
    assert roles["columns"][0] == "Role / Department" and "Responsibilities" in roles["columns"]
    labels = [row[0] for row in roles["rows"]]
    assert "IT Manager" in labels and "Policy Owner" in labels and "Information Security Team" in labels
    assert not any(people.confident_name(label) or people.name_shape(label) for label in labels)
    assert "Harbourline Bakery Ltd." in policy_text and "Head of Operations" in policy_text  # organization and role titles are kept
    assert any(i["code"] == "personal_names_removed" for i in report["quality_report"]["issues"])
    assert not any(i["code"] == "personal_name" for i in report["quality_report"]["issues"])

    # What the user entered and what the documents say are not rewritten.
    control = dict(report["document"]["document_control"])
    assert control["Policy owner"] == "Sunita Rao" and control["Approver"] == "Head of Operations"
    assert "Priya Sharma" in json.dumps(client.get(f"/api/analyses/{analysis['id']}").json())

    for fmt in ("docx", "pdf"):
        data = download(client, report["id"], fmt)
        text = pdf_text(data) if fmt == "pdf" else "\n".join(
            [p.text for p in docx.Document(io.BytesIO(data)).paragraphs]
            + [c.text for t in docx.Document(io.BytesIO(data)).tables for r in t.rows for c in r.cells]
        )
        start, end = text.index("Roles and Responsibilities"), text.rindex("Policy Requirements")
        assert "Role / Department" in text
        for name in ("Priya Sharma", "Daniel Okafor", "Sunita Rao"):
            assert name not in text[text.rindex("7. Roles and Responsibilities") : end], (fmt, name)
        assert start < end


def test_a_model_that_names_people_is_corrected_everywhere_in_the_policy(client, scripted, security_analysis):
    def name_people(result):
        for item in result["sections"]:
            if item["id"] == "roles_responsibilities":
                item["blocks"] = [
                    block(
                        "table", columns=["Name", "Responsibilities"],
                        rows=[
                            ["Rahul Verma (Network Administrator)", "Maintains firewall rules. Escalates to Dr. Meera Nair."],
                            ["Chief Information Security Officer - Anita Desai", "Owns this policy."],
                            ["Mr. Suresh Iyer", "Approves exceptions."],
                            ["Brightwater Analytics Service Desk", "Logs access requests in Active Directory."],
                            ["All employees", "Follow this policy."],
                        ],
                    ),
                    block("bullets", items=["Rekha Pillai: reviews access rights every quarter.", "Human Resources Department - notifies IT of leavers."]),
                ]
            if item["id"] == "policy_requirements":
                item["blocks"].append(
                    block("statements", statements=[{"id": "", "text": "Anita Desai must approve every privileged account before it is created.", "basis": "proposed", "refs": []}])
                )
            if item["id"] == "exceptions":
                item["blocks"].append(block("numbered", items=["Requests go to Rahul Verma, who records them."]))

    provider = scripted(by_task={"generate_policy": [scripted_draft(name_people)]})
    report = generate(client, security_analysis["id"], policy_type="access_control", organization_name="Brightwater Analytics Pvt. Ltd.")
    text = json.dumps(narrative(report))
    for name in ("Rahul Verma", "Meera Nair", "Anita Desai", "Suresh Iyer", "Rekha Pillai"):
        assert name not in text, name
    roles = by_id(report, "roles_responsibilities")["elements"]
    assert roles[0]["columns"] == ["Role / Department", "Responsibilities"]
    assert [row[0] for row in roles[0]["rows"]] == [
        "Network Administrator", "Chief Information Security Officer", people.ROLE_PLACEHOLDER,
        "Brightwater Analytics Service Desk", "All employees",
    ]
    assert roles[0]["rows"][0][1] == f"Maintains firewall rules. Escalates to {people.ROLE_PLACEHOLDER}."
    assert roles[1]["items"] == [f"{people.ROLE_PLACEHOLDER}: reviews access rights every quarter.", "Human Resources Department - notifies IT of leavers."]
    assert "The Chief Information Security Officer must approve every privileged account before it is created." in text
    assert "Requests go to the Network Administrator, who records them." in text
    assert "Active Directory" in text and "Brightwater Analytics" in text  # not personal names
    assert provider.tasks() == ["generate_policy"]  # fixed deterministically: no extra model call
    note = next(i for i in report["quality_report"]["issues"] if i["code"] == "personal_names_removed")
    assert "Rahul" not in note["message"]  # the report says how many, not who


def test_an_unrecognised_name_in_a_role_gets_one_correction_round_then_is_reported(client, scripted, security_analysis):
    def unusual_name(result):
        roles = next(s for s in result["sections"] if s["id"] == "roles_responsibilities")
        roles["blocks"] = [block("table", columns=["Role", "Responsibilities"], rows=[["Xiu Zhang", "Approves access requests."], ["IT Security Team", "Reviews accounts."]])]

    def fixed(request, result):
        assert "may name an individual" in request.user and "Do not name individual people" in request.user
        return {"sections": [{"id": "roles_responsibilities", "heading": "Roles and Responsibilities", "blocks": [
            block("table", columns=["Role / Department", "Responsibilities"], rows=[["Access Approver", "Approves access requests."], ["IT Security Team", "Reviews accounts."]])
        ]}]}

    provider = scripted(by_task={"generate_policy": [scripted_draft(unusual_name)], "revise_sections": [fixed]})
    report = generate(client, security_analysis["id"], policy_type="access_control")
    assert provider.tasks() == ["generate_policy", "revise_sections"]
    assert [row[0] for row in table_of(report, "roles_responsibilities")["rows"]] == ["Access Approver", "IT Security Team"]
    assert not any(i["code"] == "personal_name" for i in report["quality_report"]["issues"])

    # If the model does not fix it, the draft is delivered with the warning rather than a rewritten name.
    provider = scripted(by_task={"generate_policy": [scripted_draft(unusual_name)], "revise_sections": [lambda request, result: {"sections": [
        {"id": "roles_responsibilities", "heading": "Roles and Responsibilities", "blocks": [block("table", columns=["Role", "Responsibilities"], rows=[["Xiu Zhang", "Approves access requests."], ["IT Security Team", "Reviews accounts."]])]}
    ]}]})
    kept = generate(client, security_analysis["id"], policy_type="password")
    assert provider.tasks() == ["generate_policy", "revise_sections"]  # bounded: one round
    warning = next(i for i in kept["quality_report"]["issues"] if i["code"] == "personal_name")
    assert warning["severity"] == "warning" and "'Xiu Zhang'" in warning["message"]
    assert table_of(kept, "roles_responsibilities")["rows"][0][0] == "Xiu Zhang"


# ------------------------------------ revision, regeneration, manual edits
def test_structure_survives_ai_revision_regeneration_and_manual_edits(client, scripted, security_analysis):
    report = generate(client, security_analysis["id"], policy_type="access_control")
    rid = report["id"]

    def revision(request, result):
        return {"sections": [
            {"id": "scope", "heading": "Scope", "blocks": [block("paragraph", text="This policy applies to all staff and to systems behind the WAF. Joseph Mwangi decides on exclusions.")]},
            {"id": "executive_summary", "heading": "Executive Summary", "blocks": [block("paragraph", text="An unrequested summary that must not appear in the policy.")]},
        ]}

    scripted(by_task={"revise_sections": [revision]})
    started = client.post(f"/api/reports/{rid}/sections/regenerate", json={"section_ids": ["scope"], "instruction": "Mention the web application firewall."})
    assert client.get(f"/api/jobs/{started.json()['job']['id']}").json()["status"] == "completed"
    revised = client.get(f"/api/reports/{rid}").json()
    assert revised["current_version"] == 2
    assert [s["title"] for s in narrative(revised)][:3] == ["Purpose", "Abbreviations", "Scope"]
    assert "Executive Summary" not in all_text(revised) and "Joseph Mwangi" not in all_text(revised)
    assert f"{people.ROLE_PLACEHOLDER} decides on exclusions." in all_text(revised)
    assert ["WAF", "Web Application Firewall"] in table_of(revised, "abbreviations")["rows"]  # the list follows the text

    # A manual edit that introduces an abbreviation and a name.
    edited = client.put(
        f"/api/reports/{rid}/sections/purpose",
        json={"markup": "This policy controls access. Privileged access uses PAM, approved by Kavita Menon (Security Lead).", "base_version": 2},
    )
    assert edited.status_code == 200, edited.text
    latest = edited.json()["report"]
    assert [s["id"] for s in latest["sections"]][:3] == ["purpose", "abbreviations", "scope"]
    assert ["PAM", "Privileged Access Management"] in table_of(latest, "abbreviations")["rows"]
    purpose = next(s for s in latest["sections"] if s["id"] == "purpose")
    assert purpose["markup"] == "This policy controls access. Privileged access uses PAM, approved by Security Lead." and purpose["origin"] == "user_edited"

    # Editing the Abbreviations section itself keeps it where it is, as a two-column table.
    markup = next(s for s in latest["sections"] if s["id"] == "abbreviations")["markup"] + "\n| HQ | Head Office |"
    own = client.put(f"/api/reports/{rid}/sections/abbreviations", json={"markup": markup, "base_version": 3})
    assert own.status_code == 200, own.text
    final = own.json()["report"]
    table = table_of(final, "abbreviations")
    assert ["HQ", "Head Office"] in table["rows"] and table["columns"] == ["Abbreviation", "Full Form / Description"]
    assert [s["title"] for s in narrative(final)][:3] == ["Purpose", "Abbreviations", "Scope"]
    assert [s["number"] for s in narrative(final)][:3] == ["1", "2", "3"]

    # Regenerating Abbreviations with the mock rebuilds it from the text; nothing moves.
    scripted()
    again = client.post(f"/api/reports/{rid}/sections/regenerate", json={"section_ids": ["abbreviations", "roles_responsibilities"]})
    assert client.get(f"/api/jobs/{again.json()['job']['id']}").json()["status"] == "completed"
    regenerated = client.get(f"/api/reports/{rid}").json()
    assert [s["title"] for s in narrative(regenerated)][:3] == ["Purpose", "Abbreviations", "Scope"]
    assert table_of(regenerated, "roles_responsibilities")["columns"][0] == "Role / Department"
    assert "Executive Summary" not in all_text(regenerated)
    assert "2. Abbreviations" in pdf_text(download(client, rid, "pdf"))


def test_a_policy_saved_with_an_executive_summary_loses_it_only_in_new_versions(client, security_analysis):
    report = generate(client, security_analysis["id"], policy_type="access_control")
    rid = report["id"]
    # Make version 1 look like one saved by the earlier release: Executive Summary first, no Abbreviations.
    with session_scope() as db:
        version = db.scalars(select(PolicyVersion).where(PolicyVersion.policy_id == rid)).one()
        old = copy.deepcopy(version.content)
        old["sections"] = [s for s in old["sections"] if s["id"] != "abbreviations"]
        old["sections"].insert(0, section("executive_summary", "Executive Summary", block("paragraph", text="This Access Control Policy was drafted from two source documents."), origin="simulated"))
        version.content = old
    storage.remove_report_files(rid)  # the documents rendered before the change
    stored = client.get(f"/api/reports/{rid}").json()
    assert [s["title"] for s in narrative(stored)][:3] == ["Executive Summary", "Purpose", "Scope"]  # shown as it was saved
    assert "1. Executive Summary" in pdf_text(download(client, rid, "pdf", version=1))

    for call in (
        lambda: client.put(f"/api/reports/{rid}/sections/executive_summary", json={"markup": "A rewritten summary of the policy."}),
        lambda: client.post(f"/api/reports/{rid}/sections/regenerate", json={"section_ids": ["executive_summary"]}),
    ):
        refused = call()
        assert refused.status_code == 422 and "no longer part of the policy structure" in refused.json()["error"]["message"]
    assert client.get(f"/api/reports/{rid}").json()["current_version"] == 1

    edited = client.put(f"/api/reports/{rid}/sections/purpose", json={"markup": "The purpose of this policy is to control access to systems and data.", "base_version": 1})
    assert edited.status_code == 200, edited.text
    latest = edited.json()["report"]
    assert latest["current_version"] == 2
    assert [s["title"] for s in narrative(latest)][:3] == ["Purpose", "Abbreviations", "Scope"]
    assert "Executive Summary" not in json.dumps([s for s in latest["document"]["sections"]])
    assert any(i["code"] == "retired_section_removed" for i in latest["quality_report"]["issues"])
    # Every other section is carried over untouched.
    kept = {s["id"]: s["markup"] for s in stored["sections"] if s["id"] not in ("executive_summary", "purpose")}
    assert {s["id"]: s["markup"] for s in latest["sections"] if s["id"] in kept} == kept

    # Version 1 is exactly as it was.
    with session_scope() as db:
        first = db.scalars(select(PolicyVersion).where(PolicyVersion.policy_id == rid, PolicyVersion.version_number == 1)).one()
        assert first.content == old
    original = client.get(f"/api/reports/{rid}?version=1").json()
    assert [s["title"] for s in narrative(original)][0] == "Executive Summary"
    comparison = client.get(f"/api/reports/{rid}/compare?from=1&to=2").json()
    statuses = {s["id"]: s["status"] for s in comparison["sections"]}
    assert statuses["executive_summary"] == "removed" and statuses["abbreviations"] == "added" and statuses["purpose"] == "changed"
    assert "1. Executive Summary" in pdf_text(download(client, rid, "pdf", version=1))
    assert "Executive Summary" not in pdf_text(download(client, rid, "pdf"))


def test_regenerating_an_unchanged_abbreviations_section_creates_no_version(client, security_analysis):
    report = generate(client, security_analysis["id"], policy_type="access_control")
    started = client.post(f"/api/reports/{report['id']}/sections/regenerate", json={"section_ids": ["abbreviations"]})
    job = client.get(f"/api/jobs/{started.json()['job']['id']}").json()
    assert job["status"] == "completed" and job["result"]["changed"] is False
    assert client.get(f"/api/reports/{report['id']}").json()["current_version"] == 1

    # Deleting a row for an abbreviation the policy uses changes nothing either: the list follows the text.
    listing = next(s for s in report["sections"] if s["id"] == "abbreviations")
    without_mfa = "\n".join(line for line in listing["markup"].split("\n") if not line.startswith("| MFA "))
    assert without_mfa != listing["markup"]
    refused = client.put(f"/api/reports/{report['id']}/sections/abbreviations", json={"markup": without_mfa, "base_version": 1})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "no_changes"
    assert "Abbreviations" in refused.json()["error"]["message"]
