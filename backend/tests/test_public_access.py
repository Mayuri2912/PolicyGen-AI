from app.database.session import session_scope
from app.models.entities import Account, Analysis


def test_direct_access_ignores_accounts_and_keeps_private_workspaces_closed(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "public_access", True)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "api_access_token", "private-setup-token")
    with session_scope() as db:
        db.add(Account(email="admin@example.test", name="Administrator", password_hash="unused", role="admin", active=True, workspace_id="default"))
        shared = Analysis(status="analysis_completed", workspace_id="default")
        private = Analysis(status="analysis_completed", workspace_id="private-customer")
        db.add_all([shared, private])
        db.flush()
        shared_id, private_id = shared.id, private.id
    assert client.get("/api/health").json()["auth_required"] is False
    assert client.get("/api/config").status_code == 200
    user = client.get("/api/auth/me").json()["user"]
    assert user["public_access"] and user["workspace"] == "default"
    assert client.get(f"/api/analyses/{shared_id}").status_code == 200
    assert client.get(f"/api/analyses/{private_id}").status_code == 404
    assert client.get("/api/auth/accounts").status_code == 403
    assert client.post("/api/auth/setup", json={}).status_code == 403
    assert client.post("/api/auth/login", json={}).status_code == 403
    assert client.post("/api/auth/change-password", json={}).status_code == 403


def test_direct_access_is_opt_in(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "public_access", False)
    monkeypatch.setattr(settings, "api_access_token", "private-token")
    assert client.get("/api/config").status_code == 401
