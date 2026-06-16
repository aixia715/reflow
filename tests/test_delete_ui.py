"""
Playwright 浏览器自动化测试——验证删除功能的 UI 交互。

运行：
    pytest tests/test_delete_ui.py -v
    pytest tests/test_delete_ui.py -v --headed   # 可视化模式
"""
import httpx
import pytest
from playwright.sync_api import Page, expect


# ── 工具函数 ─────────────────────────────────────────────────────────

def _api_create_board(base: str, board_name: str, pcb: str, bom: str, uid: str,
                      csv_content: str = "Reference,Part\nR1,10k\nC1,100nF\n") -> str:
    """通过 httpx 直接 POST 创建单板，返回 board_id。"""
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": board_name, "pcb_version": pcb,
                         "bom_version": bom, "board_uid": uid},
                   files={"file": ("bom.csv", csv_content.encode(), "text/csv")})
    loc = r.headers.get("location", "")
    return loc.split("/board/")[-1].split("?")[0]


def _click_accept_and_check(page: Page, btn, gone_text: str, base: str):
    """
    点击删除按钮，接受 confirm 弹窗，等待操作完成后确认数据消失。
    用 page.goto 强制刷新确认服务端数据真正删除。
    """
    dialog_fired = []
    def handle(d):
        dialog_fired.append(True)
        d.accept()

    page.once("dialog", handle)
    btn.click()
    # 等待 dialog 触发和 HTMX 请求完成
    page.wait_for_timeout(2000)

    assert dialog_fired, f"hx-confirm 弹窗未触发（按钮 hx-delete={btn.get_attribute('hx-delete')!r}）"

    # 强制刷新获取服务端最新状态
    page.goto(base + "/")
    page.wait_for_load_state("networkidle")
    assert gone_text not in page.content(), f"删除后 {gone_text!r} 仍出现在页面"


# ── 测试：按钮可见性 ─────────────────────────────────────────────────

def test_delete_actions_present_in_menus(seeded_server, page: Page):
    """三个层级的删除按钮都在各自 ⋯ 菜单内（默认隐藏）。"""
    page.goto(seeded_server)
    dels = page.locator(".menu-pop button.del")
    count = dels.count()
    assert count >= 3, f"期望 ≥3 个菜单内删除按钮，实际 {count}"


# ── 测试：确认弹窗交互 ────────────────────────────────────────────────

def test_board_delete_cancel_keeps_data(live_server, page: Page):
    """点单板 ⋯ → 删除单板 → dismiss confirm → 无 DELETE 请求 → 单板芯片仍在。"""
    board_id = _api_create_board(live_server, "CancelBoard", "v1", "bomCancel", "CN001")
    page.goto(live_server)

    chip = page.locator(".chip-wrap", has_text="CN001")
    chip.hover()
    chip.locator(".menu-btn").click()
    del_btn = chip.locator("button.del")

    page.once("dialog", lambda d: d.dismiss())
    del_btn.click()
    page.wait_for_timeout(500)

    # 单板芯片仍然可见
    expect(chip).to_be_visible()


def test_board_delete_confirm_removes_board(live_server, page: Page):
    """点单板 ⋯ → 删除单板 → accept → 单板消失。"""
    board_id = _api_create_board(live_server, "DelBoard", "v1", "bomDel", "DB002")
    page.goto(live_server)

    chip = page.locator(".chip-wrap", has_text="DB002")
    chip.hover()
    chip.locator(".menu-btn").click()
    del_btn = chip.locator("button.del")

    _click_accept_and_check(page, del_btn, "DB002", live_server)


def test_bom_version_delete_confirm(live_server, page: Page):
    """点 BOM 版本 ⋯ → 删除 BOM 版本 → accept → 版本及其单板消失。"""
    _api_create_board(live_server, "BomDelBoard", "v1", "bomDel2", "BD002")
    page.goto(live_server)

    version_head = page.locator(".version-head", has=page.locator("[hx-delete*='BomDelBoard']"))
    version_head.hover()
    version_head.locator(".menu-btn").click()
    del_btn = version_head.locator("button.del")

    _click_accept_and_check(page, del_btn, "BomDelBoard", live_server)


def test_board_group_delete_confirm(live_server, page: Page):
    """点组 ⋯ → 删除整组 → accept → 整组消失。"""
    _api_create_board(live_server, "GroupDelBoard", "v1", "bomG", "GD002")
    page.goto(live_server)

    group = page.locator(".group-title", has_text="GroupDelBoard")
    group.hover()
    group.locator(".menu-btn").click()
    del_btn = group.locator("button.del")

    _click_accept_and_check(page, del_btn, "GroupDelBoard", live_server)
