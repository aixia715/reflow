import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """/version 保持匿名。匿名打不开首页在 test_auth_routes 里锁住。"""
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.delenv("REFLOW_BOOTSTRAP_USERNAME", raising=False)
    monkeypatch.delenv("REFLOW_BOOTSTRAP_PASSWORD", raising=False)
    from app.main import create_app
    return TestClient(create_app())


def test_version_defaults_to_dev(client):
    r = client.get("/version")
    assert r.status_code == 200
    assert r.json() == {"version": "dev"}


def test_version_reads_from_env(client, monkeypatch):
    monkeypatch.setenv("REFLOW_VERSION", "v0.1.0")
    r = client.get("/version")
    assert r.status_code == 200
    assert r.json() == {"version": "v0.1.0"}
