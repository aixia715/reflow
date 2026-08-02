"""Playwright 测试：「位号」输入框按回车 = 先把 Part 补上，别急着提交。

原先两边行为不同也都不好：工作区的编辑区是真 `<form>`，位号框回车走浏览器隐式
提交，Part 还空着就吃一条「修改必须填写新 Part 值」；插入页则是直接触发校验请求，
同样闪一条错。而位号框里按回车，多半是刚在 datalist 补全列表里选中了候选。

新行为（两页一致）：回车把焦点送到 Part 框，并带出该位号的现有值（选中态，敲一下
就覆盖）；位号在 BOM 里不存在时 Part 留空；op=不贴 时 Part 框本就禁用，直接提交。
"""
import httpx
import pytest
from playwright.sync_api import Page, expect

CSV = "Reference,Part\nR1,10k\nR2,22k\nC1,100nF\n"
_uid = [0]


def _board(base):
    _uid[0] += 1
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "RE", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": f"RE{_uid[0]}"},
                   files={"file": ("bom.csv", CSV.encode(), "text/csv")})
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])


def _goto_workspace(page: Page, base: str):
    bid = _board(base)
    page.goto(f"{base}/board/{bid}")
    page.locator("a.tl-card", has_text="工作区草稿").click()
    page.wait_for_load_state("networkidle")
    return bid


def _goto_insert(page: Page, base: str, chain):
    """进「在此后插入」页（链与时间由 insert_chain fixture 备好）。"""
    bid, parent = chain()
    page.goto(f"{base}/board/{bid}/node/{parent}/insert")
    page.wait_for_load_state("networkidle")
    return bid


def _boxes(page: Page):
    return (page.locator("input[placeholder='位号（自动补全）']"),
            page.locator("input[placeholder='新 Part 值']"))


# ---------------------------------------------------------------- 工作区草稿

def test_workspace_ref_enter_fills_current_part(live_server, page: Page):
    ref_in, part_in = _boxes(page)
    _goto_workspace(page, live_server)
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if r.url.endswith("/edit") else None)

    ref_in.fill("R1")
    ref_in.press("Enter")

    expect(part_in).to_be_focused()
    expect(part_in).to_have_value("10k")       # 带出现有值
    assert reqs == [], f"位号框回车不该提交：{reqs}"


def test_workspace_ref_enter_leaves_part_empty_for_new_ref(live_server, page: Page):
    ref_in, part_in = _boxes(page)
    _goto_workspace(page, live_server)

    ref_in.fill("D9")                           # BOM 里没有 → 将是新增
    ref_in.press("Enter")

    expect(part_in).to_be_focused()
    expect(part_in).to_have_value("")


def test_workspace_ref_enter_submits_when_op_is_remove(live_server, page: Page):
    """不贴时 Part 框禁用，没有可补的值，回车直接提交。"""
    ref_in, _ = _boxes(page)
    _goto_workspace(page, live_server)
    page.locator("form.edit-form .seg label", has_text="不贴").click()

    ref_in.fill("R1")
    ref_in.press("Enter")

    expect(page.locator(".change-row", has_text="R1")).to_contain_text("不贴")


def test_workspace_ref_enter_keeps_typed_part(live_server, page: Page):
    """Part 已经填了就别覆盖，只把焦点送过去。"""
    ref_in, part_in = _boxes(page)
    _goto_workspace(page, live_server)

    part_in.fill("99k")
    ref_in.fill("R1")
    ref_in.press("Enter")

    expect(part_in).to_have_value("99k")


# ---------------------------------------------------------------- 插入节点页

def test_insert_ref_enter_fills_current_part(live_server, insert_chain, page: Page):
    _goto_insert(page, live_server, insert_chain)
    ref_in, part_in = _boxes(page)
    reqs = []
    page.on("request",
            lambda r: reqs.append(r.url) if "/insert/check" in r.url else None)

    ref_in.fill("R1")
    ref_in.press("Enter")

    expect(part_in).to_be_focused()
    expect(part_in).to_have_value("10k")
    assert reqs == [], f"位号框回车不该发校验请求：{reqs}"


def test_insert_ref_enter_leaves_part_empty_for_new_ref(live_server, insert_chain, page: Page):
    _goto_insert(page, live_server, insert_chain)
    ref_in, part_in = _boxes(page)

    ref_in.fill("D9")
    ref_in.press("Enter")

    expect(part_in).to_be_focused()
    expect(part_in).to_have_value("")


def test_insert_ref_enter_submits_when_op_is_remove(live_server, insert_chain, page: Page):
    _goto_insert(page, live_server, insert_chain)
    ref_in, _ = _boxes(page)
    page.locator(".edit-form .seg label", has_text="不贴").click()

    ref_in.fill("R1")
    ref_in.press("Enter")

    expect(page.locator(".change-row", has_text="R1")).to_contain_text("不贴")
