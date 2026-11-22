
import httpx
import pytest

from app.core.errors import ServiceUnavailable
from app.services import storage
from tests.conftest import analyze, generate, upload_ok


@pytest.fixture
def remote(settings, monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "https://storage.example.test")
    monkeypatch.setattr(settings, "supabase_service_key", "test-server-key")
    objects = {}

    def request(method, url, **kwargs):
        endpoint = url.split("/storage/v1/")[1]
        assert kwargs["headers"]["apikey"] == "test-server-key"
        if endpoint.startswith("object/list/"):
            query = kwargs["json"]
            names = [k.split("/", 1)[1] for k in sorted(objects)
                     if k.startswith(query["prefix"] + "/") and query["search"] in k]
            return httpx.Response(200, json=[{"name": n} for n in names[:query["limit"]]])
        if method == "DELETE":
            for name in kwargs["json"]["prefixes"]:
                objects.pop(name, None)
            return httpx.Response(200, json=[])
        prefix = "object/authenticated/policygen-files/" if method == "GET" else "object/policygen-files/"
        name = endpoint.removeprefix(prefix)
        if method == "POST":
            objects[name] = kwargs["content"]
            return httpx.Response(200, json={"Key": name})
        if name not in objects:
            return httpx.Response(400, json={"statusCode": "404", "error": "not_found"})
        return httpx.Response(200, content=objects[name])

    monkeypatch.setattr(storage.httpx, "request", request)
    return objects


def test_json_checkpoint_survives_local_cache_loss(remote):
    path = storage.cache_path("analysis_test.json")
    storage.write_json(path, {"completed_chunk": 1})
    path.unlink()
    assert storage.read_json(path) == {"completed_chunk": 1}
    storage.remove(path)
    assert not remote and not path.exists()


def test_full_workflow_restores_upload_extraction_and_reports(client, remote, settings):
    files = upload_ok(client, ("security.txt", b"Staff must use MFA for remote access. Backup copies must be encrypted."))
    upload = next(settings.uploads_dir.glob("*.txt"))
    upload.unlink()
    assert storage.materialize(upload) and upload.exists()
    for extracted in settings.extracted_dir.glob("*.json"):
        extracted.unlink()
    analysis = analyze(client, files)
    report = generate(client, analysis["id"], detail_level="concise")
    assert any(k.startswith("generated_reports/") for k in remote)
    for local in settings.reports_dir.glob("*"):
        local.unlink()
    response = client.get(f"/api/reports/{report['id']}/download?format=pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")


def test_delete_reports_includes_uncached_versions_and_multiple_pages(remote):
    for number in range(105):
        remote[f"generated_reports/policy_v{number}.pdf"] = b"report"
    remote["generated_reports/other_v1.pdf"] = b"keep"
    storage.remove_report_files("policy")
    assert remote == {"generated_reports/other_v1.pdf": b"keep"}


def test_failed_remote_write_is_not_silently_local(remote, monkeypatch):
    def rejected(*args, **kwargs):
        return httpx.Response(403, text="test-server-key internal details")
    monkeypatch.setattr(storage.httpx, "request", rejected)
    with pytest.raises(ServiceUnavailable) as error:
        storage.write_json(storage.cache_path("test.json"), {"x": 1})
    assert "test-server-key" not in str(error.value)


def test_remote_path_validation(remote, settings):
    with pytest.raises(ValueError):
        storage.persist(settings.data_dir.parent / "outside.txt")
    temporary = settings.tmp_dir / "fragment.upload"
    temporary.write_bytes(b"temporary")
    storage.remove(temporary)
    assert not remote


def test_remote_missing_file_does_not_use_stale_local_copy(remote):
    path = storage.upload_path("gone.txt")
    path.write_bytes(b"stale")
    assert storage.materialize(path) is False
    assert not path.exists()
