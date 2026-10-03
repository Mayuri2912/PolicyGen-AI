"""Live integration test against the real OpenRouter API.

Deselected by default.  It needs OPENROUTER_API_KEY (from the environment or
from backend/.env), uses OPENROUTER_MODEL (default ``openrouter/free``), and
sends one small, fictional, non-sensitive document to OpenRouter.

    PowerShell (from backend/):  python -m pytest -m live -k openrouter -s
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from app.services.ai_analysis.providers.factory import set_provider_override
from app.services.ai_analysis.providers.openrouter_provider import OpenRouterProvider
from tests.conftest import analyze, assert_policy_structure, generate, upload_ok

pytestmark = pytest.mark.live

DOCUMENT = b"""Harbourline Bakery Ltd. - IT Access Notes (fictional test document)

1. Rules
All staff must use their own account; shared passwords are prohibited.
Remote access to the ordering system must use multi-factor authentication (MFA).
Passwords must be at least 12 characters long.
Meena Pillai (Shop Manager) is responsible for approving access to the ordering system.
Access for leavers must be removed on their last working day.

2. Observations
Two former employees still have active accounts on the ordering system.
Multi-factor authentication is not enabled for the email service.
"""


@pytest.fixture
def live_provider(settings, live_openrouter, monkeypatch):
    key, model = live_openrouter
    if not key:
        pytest.skip("OPENROUTER_API_KEY is not set; the live OpenRouter test was not run")
    monkeypatch.setattr(settings, "openrouter_api_key", key)
    monkeypatch.setattr(settings, "openrouter_model", model)
    provider = OpenRouterProvider(settings)
    set_provider_override(provider)
    return provider


def test_live_openrouter_analysis_and_policy_generation(client, live_provider):
    analysis = analyze(client, upload_ok(client, ("harbourline_access_notes.txt", DOCUMENT)))
    assert analysis["simulated"] is False and analysis["provider"] == "openrouter"
    assert len(analysis["requirements"]) >= 2 and len(analysis["risks"]) >= 1
    assert len(analysis["gaps"]) == len(analysis["requirements"])
    print(
        f"\nlive OpenRouter ({live_provider.model}): {len(analysis['requirements'])} requirements, {len(analysis['risks'])} risks, "
        f"{len(analysis['controls'])} controls, response format in use: {live_provider._format}"
    )

    report = generate(client, analysis["id"], policy_type="access_control", detail_level="concise")
    assert report["status"] == "completed", report["error_message"]
    assert report["simulated"] is False and report["provider"] == "openrouter"
    # No Executive Summary, Abbreviations before Scope, and the person the document names appears as a role only.
    assert_policy_structure(report, forbidden_names=("Meena Pillai", "Pillai"))
    sections = {s["id"]: s for s in report["document"]["sections"]}
    roles = [row[0] for e in sections["roles_responsibilities"]["elements"] if e["type"] == "table" for row in e["rows"]]
    listed = [row for e in sections["abbreviations"]["elements"] if e["type"] == "table" for row in e["rows"]]
    print(f"sections: {[s['title'] for s in report['document']['sections'] if s['kind'] == 'section'][:4]}")
    print(f"abbreviations: {listed}")
    print(f"roles: {roles}")
    print(f"structure notes: {[i['code'] for i in report['quality_report']['issues']]}")
    pdf = client.get(f"/api/reports/{report['id']}/download?format=pdf")
    assert pdf.status_code == 200 and len(PdfReader(io.BytesIO(pdf.content)).pages) >= 5
    print(f"policy generated: {report['title']}, quality {report['quality_report']['score']}/100")
