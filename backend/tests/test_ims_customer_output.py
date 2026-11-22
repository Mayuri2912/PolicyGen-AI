"""Customer format, catalogue coverage and specialist evidence regression checks."""
import copy

import pytest

from app.schemas.policy import PolicyConfig, PolicyContent
from app.services.ai_analysis.providers.mock_policy import generate_policy
from app.services.document_generation.customer_text import clean_customer_text
from app.services.policy_generation.content import normalise_sections
from app.services.policy_generation.generator import build_generate_request
from app.services.policy_generation.structure import enforce
from app.services.policy_generation.templates import (
    get_template,
    load_templates,
)


def context():
    return {"summary": {"organization_name": "Sapres", "overall_summary": "Missing all ISMS objectives and audits."},
                "source_files": [], "requirements": [], "controls": [], "gaps": [], "responsibilities": [], "recommendations": [],
                "risks": [{"ref_id": "RSK-001", "title": "No ISMS management review", "description": "Internal governance", "category": "organizational"},
                       {"ref_id": "RSK-002", "title": "Vendor cloud service failure", "description": "Supplier dependency", "category": "vendor_management"}],
                "contradictions": [], "missing_information": [], "facts": [], "assumptions": [], "standards_mentioned": [], "scoring": {}}


def test_vendor_scoping_removes_umbrella_risks_and_keeps_linked_treatments():
    data=context()
    data["controls"]=[{"ref_id":"CTL-001","name":"Supplier failover assurance","related_risks":["RSK-002"]},
                      {"ref_id":"CTL-002","name":"Internal audit programme","related_risks":["RSK-001"]}]
    original=copy.deepcopy(data)
    request=build_generate_request(get_template("vendor_management"),PolicyConfig(policy_type="vendor_management"),data)
    scoped=request.payload["analysis"]
    assert [r["ref_id"] for r in scoped["risks"]]==["RSK-002"]
    assert [c["ref_id"] for c in scoped["controls"]]==["CTL-001"]
    assert "No ISMS management review" not in request.user and "Missing all ISMS objectives" not in request.user
    assert data==original
    assert "Suggested objectives" in request.user and "third parties" in request.user


def test_old_objectives_and_principles_merge_without_losing_content():
    draft=PolicyContent.model_validate({"title":"Vendor Policy","sections":[
        {"id":"principles","heading":"Policy Principles","blocks":[{"type":"paragraph","text":"Supplier assessment is proportionate to criticality."}]},
        {"id":"objectives","heading":"Policy Objectives","blocks":[{"type":"paragraph","text":"Assess suppliers before onboarding."}]}]})
    template=get_template("vendor_management")
    content={"title":draft.title,"sections":normalise_sections(draft.sections,template,"ai_generated")}
    enforce(content,template)
    combined=next(s for s in content["sections"] if s["id"]=="objectives")
    assert combined["heading"]=="Policy Objectives and Principles"
    assert len(combined["blocks"])==2
    assert not any(s["id"]=="principles" for s in content["sections"])


def test_generation_commentary_is_removed_but_ai_governance_requirements_survive():
    assert clean_customer_text("AI-assisted draft. Suppliers must notify security incidents.")=="Suppliers must notify security incidents."
    assert clean_customer_text("Recruitment AI systems must have human oversight.")=="Recruitment AI systems must have human oversight."
    assert clean_customer_text("See the Risk Register appendix.")==""


CATALOGUE=[t for t in load_templates().values() if t.id.startswith("ims_")]


@pytest.mark.parametrize("template",CATALOGUE + [get_template("configuration_management"), get_template("bcdr")],ids=lambda t:t.code)
def test_each_catalogue_document_has_specific_guidance_and_a_valid_draft(template):
    request=build_generate_request(template,PolicyConfig(policy_type=template.id),context())
    assert template.document_kind in request.user
    assert template.topics and template.section_guidance["procedures"]
    result=PolicyContent.model_validate(generate_policy(request.payload))
    assert {s.id for s in result.sections}=={s.id for s in template.sections()}
    assert "principles" not in {s.id for s in result.sections}


def test_catalogue_contains_all_45_distinct_customer_ids():
    assert {t.code for t in CATALOGUE}=={f"I-{i:02}" for i in range(1,16)}|{f"Q-{i:02}" for i in range(1,11)}|{f"S-{i:02}" for i in range(1,16)}|{f"EU-{i:02}" for i in range(1,6)}
    assert len(CATALOGUE)==45


def test_mixed_paragraph_lists_survive_generation_and_legacy_exports(tmp_path):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import docx
    from pypdf import PdfReader

    from app.services.document_generation.docx_renderer import render_docx
    from app.services.document_generation.model import build_document_model
    from app.services.document_generation.pdf_renderer import render_pdf

    objectives = ['Identify critical services and the resources they depend on.',
                  'Define recovery objectives (RTO/RPO) for critical services.',
                  'Maintain plans that enable recovery within those objectives.',
                  'Prove the plans work through regular testing.',
                  'Coordinate business continuity with technology recovery and verify service and data integrity before returning to normal operations.']
    principles = ['The safety of people comes first in any disruption.',
                  'Recovery priorities follow business impact.',
                  'Plans are kept current, accessible and tested.',
                  'Lessons from tests and real events are fed back into the plans.']
    legacy = {'title': 'Sapres BCDR Policy', 'sections': [{'id': 'objectives', 'heading': 'Policy Objectives and Principles',
              'blocks': [{'type': 'paragraph', 'text': 'The following objectives and principles guide the BCDR program at Sapres:'},
                         {'type': 'paragraph', 'text': 'Objectives:', 'items': objectives},
                         {'type': 'paragraph', 'text': 'Principles:', 'items': principles}]}]}
    draft = PolicyContent.model_validate(legacy)
    normalized = normalise_sections(draft.sections, get_template('bcdr'), 'ai_generated')
    assert [b['items'] for b in normalized[0]['blocks'] if b['type'] == 'bullets'] == [objectives, principles]
    version = SimpleNamespace(version_number=1, created_at=datetime.now(timezone.utc), content=legacy, origin='generated', change_note='Initial version')
    config = PolicyConfig(policy_type='bcdr', organization_name='Sapres')
    policy = SimpleNamespace(config=config.model_dump(), policy_type='bcdr', source_files=[], approved_version=None,
                             current_version=1, review_status='draft', review_events=[], simulated=False, versions=[version])
    before = copy.deepcopy(legacy)
    model = build_document_model(policy, version, None)
    assert legacy == before
    for fmt, renderer in [('docx', render_docx), ('pdf', render_pdf)]:
        path = tmp_path / f'policy.{fmt}'
        renderer(model, path)
        if fmt == 'docx':
            text = '\n'.join(p.text for p in docx.Document(path).paragraphs)
        else:
            text = '\n'.join(p.extract_text() for p in PdfReader(path).pages)
        text = ' '.join(text.split())
        assert all(item in text for item in objectives + principles)


def test_objectives_with_only_intro_and_labels_requires_correction():
    from app.services.policy_generation.quality import sections_needing_correction, validate_policy

    content = {'title': 'BCDR Policy', 'sections': [{'id': 'objectives', 'heading': 'Policy Objectives and Principles',
               'blocks': [{'type': 'paragraph', 'text': 'The following objectives and principles guide the BCDR program at Sapres:'},
                          {'type': 'paragraph', 'text': 'Objectives:'}, {'type': 'paragraph', 'text': 'Principles:'}]}]}
    report = validate_policy(content, get_template('bcdr'), valid_ids=set(), source_standards=set())
    assert any(i['code'] == 'empty_section' and i['section_id'] == 'objectives' for i in report['issues'])
    assert 'objectives' in sections_needing_correction(report)


@pytest.mark.parametrize('policy_type', ['bcdr', 'configuration_management'])
def test_customer_project_evidence_guidance_and_draft_coverage(policy_type):
    from app.services.policy_generation.quality import validate_policy

    template = get_template(policy_type)
    data = context()
    data['facts'] = [{'topic': 'Delivery model', 'value': 'Contractors deliver work in customer-managed projects.'}]
    data['risks'].append({'ref_id': 'RSK-003', 'title': 'Customer-managed project responsibility gap',
                          'description': 'The customer controls project permissions and approvals.', 'category': 'organizational'})
    data['summary']['overall_summary'] = 'Contractors deliver work in customer-managed projects. No ISMS internal audit exists.'
    request = build_generate_request(template, PolicyConfig(policy_type=policy_type), data)
    assert data['facts'][0] in request.payload['analysis']['facts']
    assert any(r['ref_id'] == 'RSK-003' for r in request.payload['analysis']['risks'])
    assert all(r['ref_id'] != 'RSK-001' for r in request.payload['analysis']['risks'])
    assert 'Contractors deliver work in customer-managed projects.' in request.payload['analysis']['project_delivery_context']
    assert 'No ISMS internal audit exists.' not in request.payload['analysis']['project_delivery_context']
    specs = {s['id']: s['guidance'] for s in request.payload['sections']}
    assert template.section_guidance['policy_requirements'] in specs['policy_requirements']
    assert 'shared-responsibility matrix' in specs['policy_requirements']
    assert 'customer' in specs['scope'].lower()
    draft = PolicyContent.model_validate(generate_policy(request.payload))
    content = {'title': draft.title, 'sections': normalise_sections(draft.sections, template, 'simulated')}
    enforce(content, template)
    quality = validate_policy(content, template, valid_ids={'RSK-002', 'RSK-003'}, source_standards=set())
    assert not any(i['code'] in ('missing_customer_project_coverage', 'empty_subsection') for i in quality['issues'])
    by_id = {s['id']: s for s in content['sections']}
    for sid in template.customer_project_sections:
        assert 'customer' in str(by_id[sid]['blocks']).lower()


def test_empty_subsections_and_labels_are_correctable():
    from app.services.policy_generation.quality import validate_policy, sections_needing_correction

    content = {'title': 'BCDR Policy', 'sections': [
        {'id': 'procedures', 'heading': 'Procedures', 'blocks': [
            {'type': 'paragraph', 'text': 'These procedures describe the operational continuity arrangements.'},
            {'type': 'subheading', 'text': 'Activation Criteria'},
            {'type': 'subheading', 'text': 'Customer Recovery Coordination'},
            {'type': 'numbered', 'items': ['Obtain customer authorization before recovery actions on customer systems.']},
            {'type': 'subheading', 'text': 'Failback'}]},
        {'id': 'incident_reporting', 'heading': 'Incident Reporting', 'blocks': [
            {'type': 'paragraph', 'text': 'Contact Points:'}]},
        {'id': 'exceptions', 'heading': 'Exceptions', 'blocks': [
            {'type': 'paragraph', 'text': 'An authorized project representative may request a policy exception when a documented customer dependency prevents implementation. The request must include:'},
            {'type': 'paragraph', 'text': 'The policy owner reviews the request and records the decision.'}]}]}
    report = validate_policy(content, get_template('bcdr'), valid_ids=set(), source_standards=set())
    messages = [i['message'] for i in report['issues'] if i['code'] == 'empty_subsection']
    assert any('Activation Criteria' in m for m in messages)
    assert any('Failback' in m for m in messages)
    assert any('Contact Points' in m for m in messages)
    assert any('request must include' in m for m in messages)
    assert not any('Customer Recovery Coordination' in m for m in messages)
    assert {'procedures', 'incident_reporting'} <= set(sections_needing_correction(report))
    assert any(i['code'] == 'missing_customer_project_coverage' and i['section_id'] == 'scope' for i in report['issues'])
