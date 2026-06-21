"""Playwright 测试：编辑表单回车提交后焦点回到筛选位号输入框。

issue #26：在「新 Part 值」输入完按下回车后，除了执行原有的应用修正，
还将光标回到「筛选位号」输入框，方便用户继续纯键盘操作。
"""
import httpx
import pytest
from playwright.sync_api import Page, expect


def _api_create_board(base, name="FocusBoard", pcb="v1", bom="bomA", uid="FZ1",
                      csv="Reference,Part\nR1,10k\n"):
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": name, "pcb_version": pcb,
                         "bom_version": bom, "board_uid": uid},
                   files={"file": ("bom.csv", csv.encode(), "text/csv")})
    return r.headers.get("location", "").split("/board/")[-1].split("?")[0]


def _goto_workspace_node(page: Page, base: str, bid: str):
    """从状态图点击「工作区草稿」卡片进入节点详情页。"""
    page.goto(f"{base}/board/{bid}")
    page.locator("a.tl-card", has_text="工作区草稿").click()
    page.wait_for_load_state("networkidle")


def test_enter_in_part_input_refocuses_filter(live_server, page: Page):
    """在「新 Part 值」按回车提交成功后，焦点回到「筛选位号」输入框。"""
    bid = _api_create_board(live_server, uid="FZ1")
    _goto_workspace_node(page, live_server, bid)

    filter_input = page.locator("input[placeholder='筛选位号 / Part…']")
    ref_input = page.locator("input[placeholder='位号（自动补全）']")
    part_input = page.locator("input[placeholder='新 Part 值']")

    # 填写一次修改：R1 -> 22k，在 Part 输入框按回车提交
    ref_input.fill("R1")
    part_input.fill("22k")
    part_input.press("Enter")

    # 提交成功后会弹出 toast，以此作为 HTMX 交换完成的信号
    expect(page.locator("#toast-zone .toast")).to_be_visible()
    # 焦点应回到筛选位号输入框
    expect(filter_input).to_be_focused()


def test_filter_focus_not_moved_on_validation_error(live_server, page: Page):
    """校验失败时不应抢焦点：用户需留在 Part 输入框修正输入。"""
    bid = _api_create_board(live_server, name="ErrBoard", bom="bomErr", uid="ER1")
    _goto_workspace_node(page, live_server, bid)

    part_input = page.locator("input[placeholder='新 Part 值']")
    ref_input = page.locator("input[placeholder='位号（自动补全）']")

    # 修改一个不存在的位号 → 校验失败
    ref_input.fill("Z9")
    part_input.fill("1k")
    part_input.press("Enter")

    # 校验失败就地显示错误，不弹 toast
    expect(page.locator("#form-error")).not_to_be_empty()
    # 焦点仍在 Part 输入框（未被抢走）
    expect(part_input).to_be_focused()
