"""Playwright 测试：⋯ 菜单与四级定位内联重命名。"""
import httpx
import pytest
from playwright.sync_api import Page, expect


def _api_create_board(base, name, pcb, bom, uid,
                      csv="Reference,Part\nR1,10k\n"):
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": name, "pcb_version": pcb,
                         "bom_version": bom, "board_uid": uid},
                   files={"file": ("bom.csv", csv.encode(), "text/csv")})
    return r.headers.get("location", "").split("/board/")[-1].split("?")[0]


def test_menu_button_present_and_hidden(seeded_server, page: Page):
    """⋯ 菜单按钮存在且默认隐藏（opacity == 0）。"""
    page.goto(seeded_server)
    btn = page.locator(".menu-btn").first
    expect(btn).to_be_attached()
    assert float(btn.evaluate("el => getComputedStyle(el).opacity")) == 0


def test_menu_opens_and_shows_rename(seeded_server, page: Page):
    """悬停容器 → 点 ⋯ → 菜单出现「重命名」「删除」项。"""
    page.goto(seeded_server)
    group = page.locator(".group-title").first
    group.hover()
    group.locator(".menu-btn").click()
    pop = group.locator(".menu-pop")
    expect(pop).to_be_visible()
    assert "重命名" in pop.inner_text()
    assert "删除" in pop.inner_text()


def test_inline_rename_board_group(live_server, page: Page):
    """点重命名 → 名字变输入框 → 改值回车 → 整组改名成功。"""
    _api_create_board(live_server, "RenameMe", "v1", "bomA", "SN1")
    page.goto(live_server)
    group = page.locator(".group-title", has_text="RenameMe")
    group.hover()
    group.locator(".menu-btn").click()
    group.get_by_text("重命名").click()
    inp = group.locator(".rename-input")
    expect(inp).to_be_visible()
    inp.fill("Renamed")
    inp.press("Enter")
    page.wait_for_load_state("networkidle")
    page.goto(live_server)
    assert "Renamed" in page.content()
    assert "RenameMe" not in page.content()


def test_version_menu_has_two_rename_items(seeded_server, page: Page):
    """版本行菜单含「重命名 PCB版本」「重命名 BOM版本」两项。"""
    page.goto(seeded_server)
    vh = page.locator(".version-head").first
    vh.hover()
    vh.locator(".menu-btn").click()
    pop = vh.locator(".menu-pop")
    assert "重命名 PCB版本" in pop.inner_text()
    assert "重命名 BOM版本" in pop.inner_text()
