"""header 三点菜单收纳（2026-07-18 设计）的模板级检验。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def test_topnav_has_kebab_menu(client):
    """首页 header 含 ⋯ 菜单按钮与收纳面板，旧的行内功能区容器已移除。"""
    r = client.get("/")
    assert r.status_code == 200
    assert "topnav-menu-btn" in r.text
    assert "topnav-actions" in r.text
    assert '<nav class="ctx"' not in r.text


def test_ctxlinks_still_render(client):
    """页面 ctxlinks 内容（首页的「＋ 新建单板」）仍然渲染，主题切换按钮也在。"""
    r = client.get("/")
    assert "＋ 新建单板" in r.text
    assert "theme-toggle" in r.text
