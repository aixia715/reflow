import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def test_theme_toggle_present(client):
    """首页含主题初始化脚本与切换按钮（守护亮/夜间切换不被误删）。"""
    r = client.get("/")
    assert r.status_code == 200
    assert "data-theme" in r.text
    assert "theme-toggle" in r.text
