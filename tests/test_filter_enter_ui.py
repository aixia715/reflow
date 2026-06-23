"""Playwright 测试：位号筛选框按回车后，修改区取筛选结果第一项的位号/Part。

issue #65：在节点状态界面/工作区草稿界面的「筛选位号」输入框输入内容后按回车，
光标会跳到右侧修改区并把位号放进修改区位号输入框。原先放入的是用户键入的原始
字符串；现改为自动取下方筛选结果第一行的位号与 Part，加快输入速度。
"""
import httpx
import pytest
from playwright.sync_api import Page, expect


def _api_create_board(base, name="FilterBoard", pcb="v1", bom="bomA", uid="FB1",
                      csv="Reference,Part\nR1,10k\nR2,22k\nC1,100nF\n"):
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


def test_filter_enter_uses_first_result_ref(live_server, page: Page):
    """筛选框输入部分字符串按回车，修改区应填入筛选结果第一行的位号与 Part，
    而不是用户键入的原始字符串。"""
    bid = _api_create_board(live_server, uid="FB1")
    _goto_workspace_node(page, live_server, bid)

    filter_input = page.locator("input[placeholder='筛选位号 / Part…']")
    ref_input = page.locator("input[placeholder='位号（自动补全）']")
    part_input = page.locator("input[placeholder='新 Part 值']")

    # 只输入小写 "r"，会同时匹配 R1、R2 两行；筛选结果第一行是 R1(10k)
    filter_input.fill("r")
    filter_input.press("Enter")

    # 回车后焦点跳到右侧修改区的 Part 输入框
    expect(part_input).to_be_focused()
    # 修改区位号应取筛选结果第一项 R1，而非用户键入的 "r"
    expect(ref_input).to_have_value("R1")
    # Part 也应同步为该行 Part（10k），与点击该行「修改」一致
    expect(part_input).to_have_value("10k")


def test_filter_enter_no_match_keeps_typed(live_server, page: Page):
    """筛选无匹配结果时按回车，退回到旧行为：使用用户键入的值，避免空操作。"""
    bid = _api_create_board(live_server, name="NoMatch", uid="NM1", bom="bomB")
    _goto_workspace_node(page, live_server, bid)

    filter_input = page.locator("input[placeholder='筛选位号 / Part…']")
    ref_input = page.locator("input[placeholder='位号（自动补全）']")
    part_input = page.locator("input[placeholder='新 Part 值']")

    filter_input.fill("Z9")
    filter_input.press("Enter")

    expect(part_input).to_be_focused()
    # 无匹配项，回退使用键入值
    expect(ref_input).to_have_value("Z9")