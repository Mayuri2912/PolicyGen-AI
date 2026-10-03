"""Live integration test against the real Google Gemini API.

Deselected by default.  It needs GEMINI_API_KEY (from the environment or from
backend/.env), sends one small, fictional, non-sensitive document to Google and
uses a little of your quota.

    PowerShell (from backend/):  python -m pytest -m live -k gemini -s
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from app.services.ai_analysis.providers.factory import set_provider_override
from app.services.ai_analysis.providers.gemini_provider import GeminiProvider
from tests.conftest import analyze, assert_policy_structure, generate, upload_ok

pytestmark = pytest.mark.live

DOCUMENT = b"""Harbourline Bakery Ltd. - IT Access Notes (fictional test document)

1. Rules
All staff must use their own account; shared passwords are prohibited.
Passwords must be at least 12 characters long.
The Shop Manager is responsible for approving access to the ordering system.
Access for leavers must be removed on their last working day.

2. Observations
Two former employees still have active accounts on the ordering system.
Multi-factor authentication is not enabled for the email service.
"""


@pytest.fixture
def live_provider(settings, live_gemini, monkeypatch):
    key, model = live_gemini
    if not key:
        pytest.skip("GEMINI_API_KEY is not set; the live Gemini test was not run")
    monkeypatch.setattr(settings, "gemini_api_key", key)
    monkeypatch.setattr(settings, "gemini_model", model)
    provider = GeminiProvider(settings)
    set_provider_override(provider)
    return provider


def test_live_gemini_analysis_and_policy_generation(client, live_provider):
    analysis = analyze(client, upload_ok(client, ("harbourline_access_notes.txt", DOCUMENT)))
    assert analysis["simulated"] is False and analysis["provider"] == "gemini"
    assert len(analysis["requirements"]) >= 3 and len(analysis["risks"]) >= 2
    assert any("12 characters" in r["statement"] for r in analysis["requirements"])
    # Documented findings must quote the document; fabricated quotes would show as unverified.
    documented = [r for r in analysis["requirements"] if r["basis"] == "documented"]
    assert documented and sum(r["source"]["evidence_verified"] for r in documented) >= len(documented) - 1
    assert len(analysis["gaps"]) == len(analysis["requirements"])
    print(
        f"\nlive Gemini ({live_provider.model}): {len(analysis['requirements'])} requirements, {len(analysis['risks'])} risks, "
        f"{len(analysis['controls'])} controls, schema constraint used: {live_provider._structured}"
    )

    report = generate(client, analysis["id"], policy_type="access_control", detail_level="concise")
    assert report["status"] == "completed", report["error_message"]
    assert report["simulated"] is False and report["provider"] == "gemini"
    assert_policy_structure(report)
    assert report["quality_report"]["counts"]["error"] == 0, report["quality_report"]["issues"]
    pdf = client.get(f"/api/reports/{report['id']}/download?format=pdf")
    assert pdf.status_code == 200 and len(PdfReader(io.BytesIO(pdf.content)).pages) >= 5
    print(f"policy generated: {report['title']}, quality {report['quality_report']['score']}/100")
