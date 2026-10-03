"""Module 4 - integration: the whole workflow, failure recovery, API behaviour."""

from __future__ import annotations

import io
import logging
import re
import time
from datetime import timedelta

import docx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.core.stages import InvalidTransition, Stage, advance, can_transition
from app.database.session import session_scope
from app.main import app
from app.models.entities import Analysis, Job, Policy, UploadedFile, utcnow
from app.services import retention
from app.services.ai_analysis.providers.base import LLMUnavailableError
from app.services.document_generation import rendering
from app.services.jobs import RESTART_MESSAGE, JobContext, recover_interrupted_jobs, runner
from tests.conftest import BACKEND, analyze, generate, sample, upload, upload_ok

FIVE_SAMPLES = (
    "sample_security.txt",
    "sample_risk_assessment.xlsx",
    "sample_vendor_assessment.docx",
    "sample_cloud_security.pdf",
    "sample_access_records.csv",
)


# ------------------------------------------------------------ end to end
def test_complete_workflow_from_upload_to_download(client, settings):
    # Upload -> validation -> extraction
    results = upload(client, *[sample(name) for name in FIVE_SAMPLES])
    assert [r["file"]["status"] for r in results] == ["extracted"] * 5
    file_ids = [r["file"]["id"] for r in results]
    extraction_jobs = [client.get(f"/api/jobs/{r['file']['extraction_job_id']}").json() for r in results]
    assert all(j["type"] == "extraction" and j["status"] == "completed" and j["stage"] == "extracted" and j["progress"] == 100 for j in extraction_jobs)

    # Analysis
    started = client.post("/api/analyses", json={"file_ids": file_ids}).json()
    assert started["job"]["status"] == "completed" and started["job"]["stage"] == "analysis_completed"
    analysis = client.get(f"/api/analyses/{started['analysis_id']}").json()
    assert analysis["completed"] and [f["file_name"] for f in analysis["source_files"]] == list(FIVE_SAMPLES)
    assert len(analysis["requirements"]) >= 20 and len(analysis["risks"]) >= 25 and len(analysis["controls"]) >= 20
    assert {r["source"]["file_name"] for r in analysis["risks"] if r["source"]["file_name"]} == set(FIVE_SAMPLES)
    assert len(analysis["contradictions"]) >= 2

    # Review findings: exclude one, edit one
    client.patch(f"/api/analyses/{analysis['id']}/findings/{analysis['risks'][0]['id']}", json={"excluded": True})

    # Recommend -> configure -> generate
    recommended = client.post("/api/policies/recommend", json={"analysis_id": analysis["id"]}).json()
    assert recommended["recommended"]["policy_type"] in {t["id"] for t in client.get("/api/policies/types").json()["policy_types"]}
    generation = client.post(
        "/api/policies/generate",
        json={
            "analysis_id": analysis["id"],
            "config": {
                "policy_type": recommended["recommended"]["policy_type"],
                "organization_name": recommended["detected_organization_name"],
                "policy_owner": "Chief Information Security Officer",
            },
        },
    )
    assert generation.status_code == 202
    job = client.get(f"/api/jobs/{generation.json()['job']['id']}").json()
    assert job["type"] == "generation" and job["status"] == "completed" and job["stage"] == "completed"
    assert job["result"]["version"] == 1

    # Preview
    report_id = generation.json()["report_id"]
    report = client.get(f"/api/reports/{report_id}").json()
    assert report["status"] == "completed" and report["current_version"] == 1 and report["source_files"] == list(FIVE_SAMPLES)
    assert dict(report["document"]["document_control"])["Organization"] == "Brightwater Analytics Pvt. Ltd."
    assert analysis["risks"][0]["ref_id"] not in str(report["document"])  # the excluded risk is gone

    # Download: real, readable documents
    pdf = client.get(f"/api/reports/{report_id}/download?format=pdf")
    word = client.get(f"/api/reports/{report_id}/download?format=docx")
    assert pdf.status_code == 200 and word.status_code == 200
    assert len(PdfReader(io.BytesIO(pdf.content)).pages) >= 10
    assert len(docx.Document(io.BytesIO(word.content)).paragraphs) > 100
    assert pdf.headers["cache-control"] == "no-store" and pdf.headers["x-content-type-options"] == "nosniff"

    # History and dashboard
    listed = client.get("/api/reports").json()["reports"]
    assert [r["id"] for r in listed] == [report_id] and listed[0]["quality"]["passed"] is True
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["stats"] == {
        "files_uploaded": 5, "files_processed": 5, "analyses_completed": 1, "policies_generated": 1,
        "policies_approved": 0, "jobs_active": 0, "jobs_failed": 0,
    }
    assert dashboard["recent_reports"][0]["id"] == report_id
    history = client.get("/api/history").json()["jobs"]
    assert sorted(j["type"] for j in history) == ["analysis"] + ["extraction"] * 5 + ["generation"]

    # Delete the report: versions and rendered files go, the analysis stays
    assert len(list(settings.reports_dir.glob(f"{report_id}_*"))) == 2
    assert client.delete(f"/api/reports/{report_id}").json()["deleted"] is True
    assert list(settings.reports_dir.glob(f"{report_id}_*")) == []
    assert client.get(f"/api/reports/{report_id}").status_code == 404
    assert client.get(f"/api/reports/{report_id}/download?format=pdf").status_code == 404
    assert client.get(f"/api/analyses/{analysis['id']}").json()["completed"] is True


@pytest.mark.parametrize(
    "files, policy_type",
    [
        (("sample_vendor_assessment.docx",), "vendor_management"),
        (("sample_risk_assessment.xlsx",), "risk_management"),
        (("sample_cloud_security.pdf", "sample_access_records.csv"), "cloud_security"),
        (("sample_incident_log.json", "sample_backup_procedure.html", "sample_remote_work_guidelines.md", "sample_asset_inventory.xml", "sample_security_awareness.pptx"), "information_security"),
    ],
)
def test_workflow_with_different_file_combinations(client, files, policy_type):
    analysis = analyze(client, upload_ok(client, *[sample(name) for name in files]))
    report = generate(client, analysis["id"], policy_type=policy_type)
    assert report["status"] == "completed" and report["quality_report"]["counts"]["error"] == 0
    assert report["source_files"] == list(files)
    pdf = client.get(f"/api/reports/{report['id']}/download?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages[:4])
    assert all(name in text for name in files)


def test_partial_upload_failure_still_allows_the_workflow(client, fake_ocr):
    fake_ocr(None)
    results = upload(client, sample("sample_security.txt"), sample("sample_notice.png"), ("notes.xyz", b"data"))
    good = [r["file"]["id"] for r in results if r["accepted"] and r["file"]["status"] == "extracted"]
    assert len(good) == 1
    report = generate(client, analyze(client, good)["id"], policy_type="information_security")
    assert report["status"] == "completed" and report["source_files"] == ["sample_security.txt"]


def test_jobs_really_run_in_the_background_in_thread_mode(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "job_execution_mode", "thread")
    try:
        (result,) = upload(client, sample("sample_vendor_assessment.docx"))
        file_id = result["file"]["id"]
        deadline = time.time() + 30
        status = result["file"]["status"]
        while status != "extracted" and time.time() < deadline:
            time.sleep(0.1)
            status = client.get(f"/api/files/{file_id}").json()["status"]
        assert status == "extracted"

        started = client.post("/api/analyses", json={"file_ids": [file_id]}).json()
        assert started["job"]["status"] in ("queued", "running", "completed")
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{started['job']['id']}").json()
            if job["status"] in ("completed", "failed"):
                break
            assert job["stage"] in ("extracted", "analyzing", "analysis_completed")
            time.sleep(0.1)
        assert job["status"] == "completed" and job["stage"] == "analysis_completed" and job["progress"] == 100
    finally:
        runner.shutdown()


# ------------------------------------------------------ state machine
def test_processing_state_machine_only_allows_valid_transitions():
    path = [Stage.UPLOADED, Stage.VALIDATING, Stage.EXTRACTING, Stage.EXTRACTED, Stage.ANALYZING, Stage.ANALYSIS_COMPLETED,
            Stage.GENERATING_POLICY, Stage.FORMATTING_DOCUMENT, Stage.COMPLETED]
    for current, target in zip(path, path[1:]):
        assert advance(current, target) == target
    assert all(can_transition(stage, Stage.FAILED) for stage in path[:-1])
    for current, target in ((Stage.UPLOADED, Stage.COMPLETED), (Stage.EXTRACTING, Stage.ANALYSIS_COMPLETED), (Stage.ANALYZING, Stage.COMPLETED), (Stage.FAILED, Stage.COMPLETED)):
        with pytest.raises(InvalidTransition):
            advance(current, target)
    assert not can_transition(Stage.FAILED, Stage.COMPLETED), "a failed task can never be shown as completed"
    assert can_transition(Stage.FAILED, Stage.ANALYZING)  # retry
    assert set(client_stages()) == {s.value for s in Stage}


def client_stages():
    with TestClient(app) as test_client:
        return test_client.get("/api/config").json()["stages"]


# ------------------------------------------------- failure and recovery
def test_generation_failure_keeps_the_analysis_and_can_be_retried(client, scripted):
    analysis = analyze(client, upload_ok(client, sample("sample_security.txt")))
    provider = scripted(by_task={"generate_policy": [LLMUnavailableError("The AI provider is temporarily unavailable. Retry in a few minutes.")]})
    response = client.post("/api/policies/generate", json={"analysis_id": analysis["id"], "config": {"policy_type": "password"}})
    job = client.get(f"/api/jobs/{response.json()['job']['id']}").json()
    assert job["status"] == "failed" and job["stage"] == "failed" and job["result"]["failed_stage"] == "generating_policy"
    report = client.get(f"/api/reports/{response.json()['report_id']}").json()
    assert report["status"] == "failed" and report["document"] is None and "temporarily unavailable" in report["error_message"]

    # The analysis survived untouched; nothing has to be re-uploaded or re-analyzed.
    kept = client.get(f"/api/analyses/{analysis['id']}").json()
    assert kept["completed"] and kept["requirements"] == analysis["requirements"]

    retried = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert retried["status"] == "completed" and retried["attempts"] == 2
    assert client.get(f"/api/reports/{report['id']}").json()["status"] == "completed"
    assert provider.tasks().count("generate_policy") == 2
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409  # a completed job cannot be retried


def test_formatting_failure_keeps_the_draft_and_retry_only_re_renders(client, scripted, monkeypatch):
    analysis = analyze(client, upload_ok(client, sample("sample_security.txt")))
    provider = scripted()

    def broken_renderer(model, path):
        raise RuntimeError("disk full at C:\\secret\\path")

    monkeypatch.setitem(rendering.RENDERERS, "pdf", broken_renderer)
    response = client.post("/api/policies/generate", json={"analysis_id": analysis["id"], "config": {"policy_type": "password"}})
    job = client.get(f"/api/jobs/{response.json()['job']['id']}").json()
    assert job["status"] == "failed" and job["result"]["failed_stage"] == "formatting_document"
    assert "policy content was saved" in job["error_message"]
    assert "secret" not in job["error_message"] and "RuntimeError" not in job["error_message"]

    report = client.get(f"/api/reports/{response.json()['report_id']}").json()
    assert report["status"] == "failed" and report["current_version"] == 1
    assert report["document"] is not None and report["sections"], "the generated draft must be retained"
    assert client.get(f"/api/reports/{report['id']}/download?format=pdf").status_code == 500

    monkeypatch.undo()
    retried = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert retried["status"] == "completed" and retried["stage"] == "completed"
    after = client.get(f"/api/reports/{report['id']}").json()
    assert after["status"] == "completed" and after["current_version"] == 1  # no second draft was generated
    assert provider.tasks().count("generate_policy") == 1
    assert client.get(f"/api/reports/{report['id']}/download?format=pdf").status_code == 200


def test_interrupted_work_is_detected_after_a_restart_and_is_retryable(client):
    (file_id,) = upload_ok(client, sample("sample_security.txt"))
    analysis = analyze(client, [file_id])
    report = generate(client, analysis["id"], policy_type="password")
    # Simulate a crash while everything was mid-flight.
    with session_scope() as db:
        file = db.get(UploadedFile, file_id)
        job_id = file.extraction_job_id
        db.get(Job, job_id).status = "running"
        file.status = Stage.EXTRACTING.value
        db.get(Job, analysis["job_id"]).status = "queued"
        db.get(Analysis, analysis["id"]).status = Stage.ANALYZING.value
        db.get(Policy, report["id"]).status = Stage.FORMATTING_DOCUMENT.value

    assert recover_interrupted_jobs() == 2
    assert recover_interrupted_jobs() == 0  # idempotent
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed" and job["error_message"] == RESTART_MESSAGE
    assert client.get(f"/api/files/{file_id}").json()["status"] == "failed"
    assert client.get(f"/api/analyses/{analysis['id']}").json()["status"] == "failed"
    assert client.get(f"/api/reports/{report['id']}").json()["status"] == "failed"
    assert client.get("/api/dashboard").json()["stats"]["jobs_failed"] == 2

    assert client.post(f"/api/jobs/{job_id}/retry").json()["status"] == "completed"
    assert client.get(f"/api/files/{file_id}").json()["status"] == "extracted"
    assert client.post(f"/api/jobs/{analysis['job_id']}/retry").json()["status"] == "completed"
    assert client.get(f"/api/analyses/{analysis['id']}").json()["completed"] is True


def test_job_cancellation(client):
    def handler(ctx: JobContext):
        ctx.update(stage=Stage.ANALYZING, progress=10, message="working")
        with session_scope() as db:
            db.get(Job, ctx.job_id).cancel_requested = True
        ctx.update(progress=50, message="should not get here")
        return {}

    runner.register("cancel-probe", handler)
    with session_scope() as db:
        job = Job(type="cancel-probe", stage=Stage.EXTRACTED.value, payload={})
        db.add(job)
        db.flush()
        job_id = job.id
    runner.submit(job_id)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "cancelled" and job["stage"] == "cancelled" and job["progress"] == 10
    assert job["result"]["failed_stage"] == "analyzing"
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409  # already finished

    with session_scope() as db:
        queued = Job(type="cancel-probe", stage=Stage.EXTRACTED.value, payload={})
        db.add(queued)
        db.flush()
        queued_id = queued.id
    assert client.post(f"/api/jobs/{queued_id}/cancel").json()["cancel_requested"] is True
    runner.submit(queued_id)
    assert client.get(f"/api/jobs/{queued_id}").json()["status"] == "cancelled"


def test_unexpected_handler_crash_is_reported_safely(client):
    def handler(ctx: JobContext):
        raise ZeroDivisionError("internal detail: C:\\Users\\someone\\secret.key")

    runner.register("crash-probe", handler)
    with session_scope() as db:
        job = Job(type="crash-probe", stage=Stage.EXTRACTED.value, payload={})
        db.add(job)
        db.flush()
        job_id = job.id
    runner.submit(job_id)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed" and job["stage"] == "failed"
    assert "secret" not in job["error_message"] and "ZeroDivisionError" not in job["error_message"]
    assert "unexpected error" in job["error_message"]


def test_file_in_use_cannot_be_deleted(client):
    (file_id,) = upload_ok(client, sample("sample_security.txt"))
    with session_scope() as db:
        db.add(Job(type="analysis", status="running", stage=Stage.ANALYZING.value, payload={"subject_id": "x", "file_ids": [file_id]}))
    response = client.delete(f"/api/files/{file_id}")
    assert response.status_code == 409 and response.json()["error"]["code"] == "file_in_use"


# ---------------------------------------------------------- API errors
def test_unknown_resources_return_consistent_404s(client):
    for method, path in (
        ("get", "/api/files/nope"), ("get", "/api/files/nope/content"), ("post", "/api/extract/nope"), ("delete", "/api/files/nope"),
        ("get", "/api/analyses/nope"), ("delete", "/api/analyses/nope"), ("get", "/api/jobs/nope"), ("post", "/api/jobs/nope/cancel"),
        ("get", "/api/reports/nope"), ("get", "/api/reports/nope/versions"), ("delete", "/api/reports/nope"),
        ("get", "/api/reports/nope/download"), ("get", "/api/no/such/endpoint"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 404, (method, path, response.text)
        body = response.json()
        assert set(body) == {"error"} and {"code", "message"} <= set(body["error"]), (path, body)


def test_invalid_request_bodies_are_rejected_with_field_details(client):
    cases = (
        ("/api/analyses", {}),
        ("/api/analyses", {"file_ids": "abc"}),
        ("/api/analyses", {"file_ids": ["a"], "extra": 1}),
        ("/api/policies/recommend", {"analysis_id": "../../etc"}),
        ("/api/policies/generate", {"analysis_id": "a" * 32}),
        ("/api/policies/generate", {"analysis_id": "a" * 32, "config": {"policy_type": ""}}),
    )
    for path, body in cases:
        response = client.post(path, json=body)
        assert response.status_code == 422, (path, body, response.text)
        error = response.json()["error"]
        assert error["code"] == "validation_failed" and error["details"] and "field" in error["details"][0]
    assert client.post("/api/analyses", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post("/api/upload").status_code == 422
    assert client.put("/api/health").status_code == 405


def test_unhandled_errors_return_a_generic_500_without_internals(settings, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("database password is hunter2 at D:\\OIBSIP\\secret")

    monkeypatch.setattr("app.api.routes_system.list_policy_types", explode)
    with TestClient(app, raise_server_exceptions=False) as quiet_client:
        response = quiet_client.get("/api/config")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "An unexpected error occurred. Please try again."}}
    assert "hunter2" not in response.text and "Traceback" not in response.text


def test_rate_limiting(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    assert [client.get("/api/health").status_code for _ in range(4)] == [200, 200, 200, 429]
    limited = client.get("/api/health")
    assert limited.json()["error"]["code"] == "rate_limited" and int(limited.headers["retry-after"]) >= 1


def test_expensive_endpoints_have_a_stricter_rate_limit(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_heavy_per_minute", 1)
    files = [("files", ("a.txt", b"Access must be approved.", "text/plain"))]
    assert client.post("/api/upload", files=files).status_code == 200
    assert client.post("/api/upload", files=files).status_code == 429
    assert client.get("/api/files").status_code == 200  # reads are not affected


def test_optional_access_token_protects_the_api(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "api_access_token", "s3cret-token")
    assert client.get("/api/health").json()["auth_required"] is True  # health stays open so the UI can ask for the token
    for path in ("/api/files", "/api/reports", "/api/dashboard", "/api/config"):
        assert client.get(path).status_code == 401
    assert client.get("/api/files", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/files", headers={"Authorization": "s3cret-token"}).status_code == 401
    assert client.get("/api/files", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200
    assert client.post("/api/upload", files=[("files", ("a.txt", b"x"))]).status_code == 401


def test_cors_is_limited_to_configured_origins(client):
    allowed = client.options("/api/files", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.options("/api/files", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in other.headers
    assert "access-control-allow-origin" not in client.get("/api/health", headers={"Origin": "https://evil.example"}).headers


def test_api_documentation_is_served(client):
    assert client.get("/api/docs").status_code == 200
    paths = client.get("/api/openapi.json").json()["paths"]
    for expected in ("/api/upload", "/api/files/{file_id}", "/api/extract/{file_id}", "/api/analyze/{file_id}", "/api/policies/recommend",
                     "/api/policies/generate", "/api/jobs/{job_id}", "/api/reports", "/api/reports/{report_id}", "/api/reports/{report_id}/download"):
        assert expected in paths


def test_every_frontend_api_call_matches_a_backend_route(client):
    """The paths used in frontend/src/services/api.js must all exist in the API."""
    source = (BACKEND.parent / "frontend" / "src" / "services" / "api.js").read_text(encoding="utf-8")
    calls = re.findall(r"(?:json|send)\(\s*[`'](/[^`'?]*)", source)
    assert len(calls) >= 20
    routes = {re.sub(r"\{[^}]+\}", "{}", path.removeprefix("/api")) for path in client.get("/api/openapi.json").json()["paths"]}
    for call in calls:
        normalised = re.sub(r"\$\{(?!query)[^}]+\}", "{}", call)
        normalised = re.sub(r"\$\{query.*$", "", normalised)
        assert normalised in routes, f"frontend calls {call!r}, which is not a backend route"


# ------------------------------------------------------ privacy, retention
def test_logs_never_contain_document_content(client, caplog):
    marker = "ZEBRA-QUARTZ-7731"
    text = f"Project {marker} is confidential.\nAccess to {marker} data must be approved by the System Owner.\n".encode()
    with caplog.at_level(logging.DEBUG, logger="policygen"):
        analysis = analyze(client, upload_ok(client, (f"{marker}.txt", text)))
        report = generate(client, analysis["id"], policy_type="access_control")
        client.get(f"/api/reports/{report['id']}/download?format=pdf")
    assert caplog.records, "the pipeline is expected to log its progress"
    for record in caplog.records:
        rendered = record.getMessage() + " " + " ".join(str(v) for v in record.__dict__.values())
        assert marker not in rendered, f"document content leaked into a log record: {record.getMessage()}"


def test_retention_sweep_removes_expired_data_and_temp_files(client, settings, monkeypatch):
    (old_id,) = upload_ok(client, sample("sample_security.txt"))
    (new_id,) = upload_ok(client, sample("sample_access_records.csv"))
    analysis = analyze(client, [old_id])
    report = generate(client, analysis["id"], policy_type="password")
    stale = settings.tmp_dir / "orphan.upload"
    stale.write_bytes(b"x")
    import os

    os.utime(stale, (time.time() - 7200, time.time() - 7200))

    assert retention.sweep() == {"temp_files": 1, "files": 0, "analyses": 0}  # retention disabled: only temp files go
    assert not stale.exists()

    monkeypatch.setattr(settings, "retention_hours", 24)
    with session_scope() as db:
        db.get(UploadedFile, old_id).created_at = utcnow() - timedelta(hours=30)
        db.get(Analysis, analysis["id"]).created_at = utcnow() - timedelta(hours=30)
    result = retention.sweep()
    assert result["files"] == 1 and result["analyses"] == 1
    assert client.get(f"/api/files/{old_id}").status_code == 404
    assert client.get(f"/api/analyses/{analysis['id']}").status_code == 404
    assert client.get(f"/api/reports/{report['id']}").status_code == 404
    assert list(settings.reports_dir.glob("*")) == []
    assert client.get(f"/api/files/{new_id}").status_code == 200  # recent data is kept
    assert "24 hours" in client.get("/api/health").json()["privacy"]["retention"]


def test_reference_data_and_privacy_notice(client):
    config = client.get("/api/config").json()
    assert config["limits"] == {"max_upload_mb": 25, "max_files_per_upload": 10}
    extensions = {t["extension"] for t in config["file_types"]}
    assert {"txt", "pdf", "doc", "docx", "xls", "xlsx", "csv", "json", "xml", "html", "md", "ppt", "pptx", "png", "jpg", "jpeg", "tiff"} <= extensions
    assert len(config["policy_types"]) >= 15 and len(config["requirement_categories"]) == 12
    assert [s["id"] for s in config["writing_styles"]] == ["formal", "technical", "simplified"]
    privacy = client.get("/api/health").json()["privacy"]
    assert set(privacy) == {"storage", "llm", "ocr", "retention", "source_files", "logs"}


def test_no_secrets_or_keys_in_the_repository():
    root = BACKEND.parent
    # Anthropic keys start with "sk-ant-", Google API keys with "AIza", OpenRouter keys with "sk-or-".
    pattern = re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{30,}|sk-or-[A-Za-z0-9_\-]{20,}")
    checked = 0
    for path in root.rglob("*"):
        if not path.is_file() or any(part in {"node_modules", ".venv", "dist", "data", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        if path.name == ".env":
            continue  # the local secrets file: the one place a key belongs (checked below to be git-ignored)
        if path.suffix.lower() in {".py", ".js", ".jsx", ".json", ".md", ".txt", ".yml", ".yaml", ".example", ".html", ".css", ".ini", ".csv", ".xml"} or path.name.startswith(".env"):
            checked += 1
            assert not pattern.search(path.read_text(encoding="utf-8", errors="ignore")), f"possible API key in {path}"
    assert checked > 80
    ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored and "!.env.example" in ignored, "backend/.env must be git-ignored"
    example = (BACKEND / ".env.example").read_text(encoding="utf-8")
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        assert re.search(rf"^{name}=\s*$", example, re.MULTILINE), f".env.example must not contain a value for {name}"
    frontend_sources = "\n".join(p.read_text(encoding="utf-8") for p in (root / "frontend" / "src").rglob("*.js*"))
    for forbidden in (
        "ANTHROPIC_API_KEY=", "GEMINI_API_KEY=", "OPENROUTER_API_KEY=", "x-api-key", "x-goog-api-key",
        "generativelanguage.googleapis.com", "openrouter.ai/api",
    ):
        assert forbidden.lower() not in frontend_sources.lower(), f"the frontend must not handle provider credentials ({forbidden})"
