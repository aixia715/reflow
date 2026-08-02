"""Playwright 测试：插入节点页与工作区草稿的功能对齐（PR A）。

插入页是工作区编辑器的客户端复刻版，复刻时漏了一批东西：清除全部修改、撤销
反馈、筛选框回车取首行、点「修改」滚动到编辑面板、审计日志入口。另外「添加
这条」改走服务端校验后多了一个新失败态——请求失败时页面毫无反应。
这里逐条锁住。位号框回车（两页共同的新行为）在 test_ref_enter_ui.py。
"""
from playwright.sync_api import Page, expect

CSV = "Reference,Part\nR1,10k\nR2,22k\nC1,100nF\n"
_uid = [0]


def _goto_insert(page: Page, base: str, chain):
    bid, parent = chain()
    page.goto(f"{base}/board/{bid}/node/{parent}/insert")
    page.wait_for_load_state("networkidle")
    return bid


def _add(page: Page, ref: str, part: str, op_label: str | None = None,
         expect_row: bool = True):
    """加一条修改，默认等它真的进了暂存面板再返回。

    「添加这条」要等 /insert/check 回来才写进暂存。不等就接着敲下一条，慢机器上
    两次会叠在一起（CI 上就是这么挂的）。expect_row=False 给「本来就不会出现行」
    的用例用（请求失败、抵消）。
    """
    page.locator("input[placeholder='位号（自动补全）']").fill(ref)
    if op_label:
        page.locator(".edit-form .seg label", has_text=op_label).click()
    if part is not None:
        page.locator("input[placeholder='新 Part 值']").fill(part)
    page.get_by_role("button", name="添加这条").click()
    if expect_row:
        expect(page.locator(".change-row", has_text=ref)).to_be_visible()


def test_clear_all_changes_empties_the_pending_panel(live_server, insert_chain, page: Page):
    """插入页要有「清除全部修改」，与工作区草稿一致（确认后清空）。"""
    _goto_insert(page, live_server, insert_chain)
    _add(page, "R1", "47k")
    _add(page, "D9", "1uF", "新增")
    expect(page.locator(".change-row")).to_have_count(2)

    page.on("dialog", lambda d: d.accept())
    page.locator("[data-testid=clear-all-changes]").click()

    expect(page.locator(".change-row")).to_have_count(0)
    expect(page.get_by_role("button", name="保存插入")).to_be_disabled()


def test_clear_all_hidden_when_nothing_pending(live_server, insert_chain, page: Page):
    """没有修改时不显示清除按钮——与 _changes_panel.html 的条件一致。"""
    _goto_insert(page, live_server, insert_chain)
    # x-show 只是隐藏、不移除节点（工作区那边是 Jinja 条件，DOM 里直接没有）
    expect(page.locator("[data-testid=clear-all-changes]")).to_be_hidden()


def test_undo_shows_toast(live_server, insert_chain, page: Page):
    """撤销一条要给反馈：工作区回「↩ 已撤销 …」，插入页原先是静默的。"""
    _goto_insert(page, live_server, insert_chain)
    _add(page, "R1", "47k")
    page.locator(".change-row .icon-btn.danger").first.click()
    # 加那条的 toast 还在（3.5 秒才消失），按文案挑出撤销这条
    expect(page.locator(".toast", has_text="已撤销 R1")).to_be_visible()


def test_add_shows_toast(live_server, insert_chain, page: Page):
    """加一条同样要有 toast，文案与工作区编辑一致。"""
    _goto_insert(page, live_server, insert_chain)
    _add(page, "R1", "47k")
    expect(page.locator(".toast", has_text="已修改：R1 → 47k")).to_be_visible()


def test_filter_enter_takes_first_visible_row(live_server, insert_chain, page: Page):
    """筛选框回车取筛选结果第一行的位号与 Part，与工作区 issue #65 的行为一致。"""
    _goto_insert(page, live_server, insert_chain)
    filt = page.locator("input[placeholder='筛选位号 / Part…']")
    ref_in = page.locator("input[placeholder='位号（自动补全）']")
    part_in = page.locator("input[placeholder='新 Part 值']")

    filt.fill("r")            # 匹配 R1(10k)、R2(22k)，首行 R1
    filt.press("Enter")

    expect(ref_in).to_have_value("R1")
    expect(part_in).to_have_value("10k")
    expect(part_in).to_be_focused()


def test_filter_enter_no_match_keeps_typed(live_server, insert_chain, page: Page):
    """无匹配时退回键入值，同工作区。"""
    _goto_insert(page, live_server, insert_chain)
    filt = page.locator("input[placeholder='筛选位号 / Part…']")
    filt.fill("Z9")
    filt.press("Enter")
    expect(page.locator("input[placeholder='位号（自动补全）']")).to_have_value("Z9")


def test_row_edit_scrolls_edit_panel_into_view(live_server, insert_chain, page: Page):
    """点表格行的「修改」要把编辑面板滚进视野——工作区有，插入页没有。"""
    page.set_viewport_size({"width": 900, "height": 400})
    _goto_insert(page, live_server, insert_chain)
    row = page.locator("table.bom tr", has_text="C1").first
    row.hover()                      # 行内按钮是 .hover-only，不悬停点不到
    row.locator(".icon-btn").first.click()
    page.wait_for_timeout(600)      # smooth 滚动
    box = page.locator(".edit-form").first.bounding_box()
    assert box is not None and box["y"] < 400, f"编辑面板未进入视野：{box}"


def test_audit_log_link_present(live_server, insert_chain, page: Page):
    """顶部快捷链接补上「审计日志」，与节点页一致。"""
    _goto_insert(page, live_server, insert_chain)
    page.locator(".topnav-menu-btn").click()      # 快捷链接收在 ⋯ 菜单里
    expect(page.get_by_role("link", name="审计日志")).to_be_visible()


def test_check_request_failure_reports_error(live_server, insert_chain, page: Page):
    """/insert/check 失败时必须报错。原先收不到 HX-Trigger 就什么都不做，
    点「添加这条」毫无反应，用户只会以为没点上。"""
    _goto_insert(page, live_server, insert_chain)
    page.route("**/insert/check", lambda route: route.fulfill(status=500, body="boom"))
    _add(page, "R1", "47k", expect_row=False)
    expect(page.locator(".edit-form .flash-error")).to_contain_text("检查失败")


def _guard_blocks_unload(page: Page) -> bool:
    """派一个可取消的 beforeunload，看本页的守卫有没有拦下来。

    浏览器那个「确定离开？」对话框归浏览器管，也测不稳（自动化里 Chromium 只在
    page.close(run_before_unload=True) 时才浮出来，且在 pytest-playwright 的
    page 上收不到）。我们能负责、也该锁住的是：该拦的时候 preventDefault。
    """
    return page.evaluate(
        "() => { const e = new Event('beforeunload', {cancelable: true});"
        "        window.dispatchEvent(e); return e.defaultPrevented; }")


def test_unsaved_changes_arm_the_unload_guard(live_server, insert_chain, page: Page):
    """有暂存改动时离开页面要拦一下——本页改动只在浏览器里，误刷一次全丢。"""
    _goto_insert(page, live_server, insert_chain)
    assert _guard_blocks_unload(page) is False      # 空手离开不该拦

    _add(page, "R1", "47k")
    # 「添加这条」要等 /insert/check 回来才写进暂存，不等则守卫还没武装
    expect(page.locator(".change-row")).to_have_count(1)
    assert _guard_blocks_unload(page) is True


def test_guard_released_when_save_starts(live_server, insert_chain, page: Page):
    """保存一开始就解除守卫：HX-Redirect 也是一次 unload，存成功还问「要不要离开」
    纯属恶心人；更糟的是选「留下」时节点其实已经建好，再点一次会重复插入。

    直接派 htmx 的 `htmx:beforeRequest`（camelCase，vendored htmx 只发这一种）到
    保存表单来断言机制本身。不靠「点保存后看有没有弹框」——headless Chromium 对
    htmx 的 window.location 跳转根本不弹 beforeunload，那样测出来的绿与机制无关。
    """
    _goto_insert(page, live_server, insert_chain)
    _add(page, "R1", "47k")
    assert _guard_blocks_unload(page) is True

    page.evaluate(
        "document.querySelector('form.panel')"
        ".dispatchEvent(new CustomEvent('htmx:beforeRequest', {bubbles: true}))")
    assert _guard_blocks_unload(page) is False


def test_save_completes_and_redirects(live_server, insert_chain, page: Page):
    """保存插入照常落库并跳到新节点。"""
    _goto_insert(page, live_server, insert_chain)
    _add(page, "R1", "47k")

    page.get_by_role("button", name="保存插入").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".toast")).to_contain_text("已插入")


def _late_checked(page: Page, reference: str, op: str, part: str):
    """补派一次「迟到的」/insert/check 回执（形状同 HX-Trigger: insertChecked）。

    真实竞态是「请求在飞的时候接着敲」。用 route + sleep 去复现会阻塞 Playwright
    同步驱动、交错不确定（整文件跑时偶发假失败），这里直接把回执补派出来——
    要锁的本来就是「回执迟到时不许抹掉用户新敲的内容」这条逻辑。
    """
    page.evaluate(
        """([reference, op, part]) => {
             document.querySelector('[x-ref=addBtn]').dispatchEvent(
               new CustomEvent('insertChecked', {
                 bubbles: true,
                 detail: {ok: true, action: 'set', reference: reference,
                          op: op, part: part, fix: '', warning: ''}}));
           }""", [reference, op, part])


def test_late_response_does_not_wipe_next_ref(live_server, insert_chain, page: Page):
    """回执迟到时用户已在敲下一条位号，不能把它抹掉。"""
    _goto_insert(page, live_server, insert_chain)
    _add(page, "R1", "47k")

    ref_in = page.locator("input[placeholder='位号（自动补全）']")
    ref_in.fill("D9")
    _late_checked(page, "R1", "modify", "47k")

    expect(ref_in).to_have_value("D9")


def test_late_response_does_not_wipe_new_part_for_same_ref(
        live_server, insert_chain, page: Page):
    """同一位号只改了 Part 的情况同样不能抹——只比位号的话这一路会漏。"""
    _goto_insert(page, live_server, insert_chain)
    _add(page, "R1", "47k")

    ref_in = page.locator("input[placeholder='位号（自动补全）']")
    part_in = page.locator("input[placeholder='新 Part 值']")
    ref_in.fill("R1")
    part_in.fill("48k")
    _late_checked(page, "R1", "modify", "47k")

    expect(part_in).to_have_value("48k")
    expect(ref_in).to_have_value("R1")
