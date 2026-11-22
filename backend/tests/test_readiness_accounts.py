"""Security boundaries and approval readiness, using isolated offline data."""

from app.services.policy_generation.readiness import professional_checks
from tests.conftest import analyze, generate, sample, upload_ok


def setup_admin(client, monkeypatch, settings):
    monkeypatch.setattr(settings, "api_access_token", "test-administrator-bootstrap")
    client.headers["Authorization"] = "Bearer test-administrator-bootstrap"
    response = client.post(
        "/api/auth/setup",
        json={
            "email": "admin@example.test",
            "name": "Test Administrator",
            "password": "A-long-test-password!42",
        },
    )
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = "Bearer " + response.json()["token"]
    return response.json()


def test_account_sessions_roles_and_workspace_isolation(client, settings, monkeypatch):
    admin = setup_admin(client, monkeypatch, settings)
    own_files = upload_ok(client, sample("sample_security.txt"))
    own_analysis = analyze(client, own_files)
    own_report = generate(client, own_analysis["id"], policy_type="access_control")
    own_job = own_report["job_id"]
    assert client.get("/api/dashboard").json()["stats"]["files_uploaded"] == 1
    assert (
        client.post(
            "/api/auth/setup",
            json={
                "email": "other@example.test",
                "name": "Other",
                "password": "A-long-test-password!42",
            },
        ).status_code
        == 403
    )
    response = client.post(
        "/api/auth/accounts",
        json={
            "email": "client@example.test",
            "name": "Client Viewer",
            "password": "A-long-test-password!42",
            "role": "viewer",
            "new_workspace": True,
        },
    )
    assert response.status_code == 200, response.text
    login = client.post(
        "/api/auth/login",
        json={"email": "client@example.test", "password": "A-long-test-password!42"},
    )
    assert login.status_code == 200
    viewer_token = login.json()["token"]
    client.headers["Authorization"] = "Bearer " + viewer_token
    assert client.get("/api/files").json()["files"] == []
    assert client.get("/api/reports").json()["reports"] == []
    assert client.get("/api/dashboard").json()["stats"]["files_uploaded"] == 0
    for path in [
        f"/api/files/{own_files[0]}",
        f"/api/analyses/{own_analysis['id']}",
        f"/api/reports/{own_report['id']}",
        f"/api/jobs/{own_job}",
        f"/api/reports/{own_report['id']}/download?format=pdf",
    ]:
        assert client.get(path).status_code == 404, path
    assert client.post("/api/upload").status_code == 403
    assert (
        client.post(
            "/api/auth/accounts",
            json={
                "email": "x@example.test",
                "name": "Other",
                "password": "A-long-test-password!42",
            },
        ).status_code
        == 403
    )
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/files").status_code == 401
    client.headers["Authorization"] = "Bearer " + admin["token"]
    account_id = response.json()["user"]["id"]
    assert client.post(f"/api/auth/accounts/{account_id}/disable").status_code == 403
    local = client.post(
        "/api/auth/accounts",
        json={
            "email": "local@example.test",
            "name": "Local Viewer",
            "password": "A-long-test-password!42",
            "role": "viewer",
        },
    ).json()["user"]
    assert client.post(f"/api/auth/accounts/{local['id']}/disable").status_code == 200
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "local@example.test", "password": "A-long-test-password!42"},
        ).status_code
        == 401
    )


def test_approval_cannot_use_typed_identity_or_unresolved_information(
    client, monkeypatch, settings
):
    analysis = analyze(client, upload_ok(client, sample("sample_security.txt")))
    report = generate(client, analysis["id"], policy_type="access_control")
    url = f"/api/reports/{report['id']}/review"
    assert report["readiness"]["missing_information"]
    assert not report["readiness"]["ready_for_approval"]
    assert (
        client.post(
            url,
            json={"action": "approve", "actor": "Invented Reviewer", "base_version": 1},
        ).status_code
        == 403
    )
    setup_admin(client, monkeypatch, settings)
    assert (
        client.post(
            url,
            json={"action": "approve", "actor": "Invented Reviewer", "base_version": 1},
        ).status_code
        == 409
    )


def test_complete_information_preserves_old_metadata_and_rejects_stale_save(client):
    analysis = analyze(client, upload_ok(client, sample("sample_security.txt")))
    report = generate(client, analysis["id"], policy_type="access_control")
    url = f"/api/reports/{report['id']}/missing-information"
    item = next(i for i in report["readiness"]["missing_information"] if not i["field"])
    body = {
        "base_version": 1,
        "metadata": {
            "policy_owner": "Security Manager",
            "effective_date": "2026-10-09",
        },
        "replacements": {item["placeholder"]: "Approved service inventory INV-01"},
    }
    result = client.post(url, json=body)
    assert result.status_code == 200, result.text
    updated = result.json()
    assert updated["current_version"] == 2 and updated["review_status"] == "draft"
    assert (
        dict(updated["document"]["document_control"])["Policy owner"]
        == "Security Manager"
    )
    assert not any(
        i["placeholder"] == item["placeholder"]
        for i in updated["readiness"]["missing_information"]
    )
    old = client.get(f"/api/reports/{report['id']}?version=1").json()
    assert (
        dict(old["document"]["document_control"])["Policy owner"]
        == "[To be confirmed: policy owner]"
    )
    assert client.post(url, json=body).status_code == 409
    assert (
        client.post(
            url, json={"base_version": 2, "metadata": {"effective_date": "invalid"}}
        ).status_code
        == 422
    )


def test_professional_policy_specific_checks():
    content = {
        "sections": [
            {
                "id": "policy_requirements",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "Investigate drift within 48 hours. GDPR applies to all customer projects.",
                    }
                ],
            }
        ]
    }
    config_codes = {
        i["code"] for i in professional_checks(content, "configuration_management")
    }
    assert {
        "change_independence",
        "emergency_change",
        "customer_control_boundary",
        "operational_commitments",
        "legal_applicability",
    } <= config_codes
    assert "continuity_security" in {
        i["code"] for i in professional_checks(content, "bcdr")
    }


def test_saved_policy_sections_survive_mid_generation_failure(client, scripted):
    from app.services.ai_analysis.providers.base import LLMUnavailableError
    from app.services.ai_analysis.providers.mock_policy import generate_policy

    analysis = analyze(client, upload_ok(client, sample("sample_security.txt")))

    def first(request, result):
        return generate_policy(request.payload)

    provider = scripted(
        by_task={
            "generate_policy": [
                first,
                LLMUnavailableError("Temporary provider outage."),
            ]
        }
    )
    started = client.post(
        "/api/policies/generate",
        json={
            "analysis_id": analysis["id"],
            "config": {"policy_type": "access_control"},
        },
    ).json()
    job_id = started["job"]["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"
    assert client.post(f"/api/jobs/{job_id}/retry").json()["status"] == "completed"
    batches = [
        r.payload["sections"] for r in provider.requests if r.task == "generate_policy"
    ]
    assert sum(batch[0]["id"] == "purpose" for batch in batches) == 1


def test_additive_workspace_migration_preserves_existing_rows(tmp_path, monkeypatch):
    from app.database import session
    from app.models.entities import UploadedFile
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    session.Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(UploadedFile(id='legacy-record', original_name='legacy.txt', extension='txt', size_bytes=10,
                            sha256='a' * 64, storage_name='legacy.txt'))
        db.commit()
    with engine.begin() as connection:
        connection.execute(text('DROP INDEX ix_uploaded_files_workspace_id'))
        connection.execute(text('ALTER TABLE uploaded_files DROP COLUMN workspace_id'))
    monkeypatch.setattr(session, 'get_engine', lambda: engine)
    session.init_db()
    session.init_db()
    with engine.connect() as connection:
        assert connection.execute(text('SELECT original_name, workspace_id FROM uploaded_files')).one() == ('legacy.txt', 'default')
    engine.dispose()


def test_password_change_revokes_previous_sessions(client, settings, monkeypatch):
    admin = setup_admin(client, monkeypatch, settings)
    result = client.post('/api/auth/password', json={'current_password': 'A-long-test-password!42', 'new_password': 'Another-long-password!57'})
    assert result.status_code == 200
    assert client.get('/api/files').status_code == 401
    client.headers['Authorization'] = 'Bearer ' + result.json()['token']
    assert client.get('/api/auth/me').json()['user']['id'] == admin['user']['id']
    assert client.post('/api/auth/login', json={'email': 'admin@example.test', 'password': 'A-long-test-password!42'}).status_code == 401
