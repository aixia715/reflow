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


def _channels(rgb: str) -> list[int]:
    import re
    return [int(x) for x in re.findall(r"\d+", rgb)][:3]


def test_dark_mode_input_dark_bg_light_text(live_server, page):
    """夜间模式下文本输入框应是深底浅字（跟随主题），而非浏览器默认白底黑字。"""
    page.goto(f"{live_server}/board/new")
    page.evaluate("localStorage.setItem('theme','dark')")
    page.reload()
    el = page.locator("input.input").first
    bg = el.evaluate("e => getComputedStyle(e).backgroundColor")
    fg = el.evaluate("e => getComputedStyle(e).color")
    bg_sum, fg_sum = sum(_channels(bg)), sum(_channels(fg))
    assert bg_sum < 250, f"夜间输入框底色应为深色，实际 {bg}"
    assert fg_sum > 350, f"夜间输入框文字应为浅色，实际 {fg}"


def test_light_mode_input_light_bg_dark_text(live_server, page):
    """白天模式仍是浅底深字。"""
    page.goto(f"{live_server}/board/new")
    page.evaluate("localStorage.setItem('theme','light')")
    page.reload()
    el = page.locator("input.input").first
    bg = el.evaluate("e => getComputedStyle(e).backgroundColor")
    fg = el.evaluate("e => getComputedStyle(e).color")
    bg_sum, fg_sum = sum(_channels(bg)), sum(_channels(fg))
    assert bg_sum > 600, f"白天输入框底色应为浅色，实际 {bg}"
    assert fg_sum < 300, f"白天输入框文字应为深色，实际 {fg}"
