"""Playwright 测试：插入节点页的 CSV 导入（PR B）。

节点页早就有导入，插入页一直只能一条条手敲。这里锁住端到端：选文件 → 预览 →
应用 → 进暂存 → 能一起保存。与节点页的差别是「应用」不落库，而是合进浏览器暂存。
"""
from playwright.sync_api import Page, expect

DIFF_CSV = "Reference,Part\nR1,47k\nD9,1uF\n"


def _goto_insert(page: Page, base: str, chain):
    bid, parent = chain()
    page.goto(f"{base}/board/{bid}/node/{parent}/insert")
    page.wait_for_load_state("networkidle")
    return bid


def _open_import(page: Page):
    page.locator("details.panel", has_text="从 CSV 导入修改").locator("summary").click()


def _upload(page: Page, text: str, name: str = "x.csv"):
    page.locator("#import-form input[type=file]").set_input_files(
        files=[{"name": name, "mimeType": "text/csv", "buffer": text.encode()}])


def test_import_preview_then_apply_merges_into_pending(live_server, insert_chain, page: Page):
    _goto_insert(page, live_server, insert_chain)
    _open_import(page)
    _upload(page, DIFF_CSV)

    apply_btn = page.get_by_role("button", name="应用这 2 条修改")
    expect(apply_btn).to_be_visible()
    # 预览只是预览，还没进暂存（预览片段用的也是 .change-row，断言要限定在暂存面板）
    expect(page.locator("[data-testid=insert-changes] .change-row",
                        has_text="R1")).to_have_count(0)

    apply_btn.click()

    expect(page.locator("[data-testid=insert-changes] .change-row")).to_have_count(2)
    expect(page.locator(".toast", has_text="已导入 2 条修改")).to_be_visible()
    expect(page.get_by_role("button", name="保存插入")).to_be_enabled()


def test_imported_changes_land_in_the_bom_table(live_server, insert_chain, page: Page):
    """导入后主表要跟着变——暂存是同一份数据。"""
    _goto_insert(page, live_server, insert_chain)
    _open_import(page)
    _upload(page, DIFF_CSV)
    page.get_by_role("button", name="应用这 2 条修改").click()

    row = page.locator("table.bom tr", has_text="R1").first
    expect(row).to_contain_text("47k")
    expect(page.locator("table.bom tr", has_text="D9")).to_contain_text("1uF")


def test_import_preview_is_cleared_after_apply(live_server, insert_chain, page: Page):
    """应用完预览要收走，否则会以为还能再点一次。"""
    _goto_insert(page, live_server, insert_chain)
    _open_import(page)
    _upload(page, DIFF_CSV)
    page.get_by_role("button", name="应用这 2 条修改").click()
    expect(page.locator("#import-preview")).to_be_empty()


def test_import_builds_on_existing_pending_changes(live_server, insert_chain, page: Page):
    """先手工加一条，再导入：基准是眼前这张表，两者应当并存。"""
    _goto_insert(page, live_server, insert_chain)
    page.locator("input[placeholder='位号（自动补全）']").fill("C1")
    page.locator("input[placeholder='新 Part 值']").fill("220nF")
    page.get_by_role("button", name="添加这条").click()
    expect(page.locator("[data-testid=insert-changes] .change-row")).to_have_count(1)

    _open_import(page)
    _upload(page, DIFF_CSV)
    page.get_by_role("button", name="应用这 2 条修改").click()

    expect(page.locator("[data-testid=insert-changes] .change-row")).to_have_count(3)


def test_problem_csv_gives_no_apply_button(live_server, insert_chain, page: Page):
    _goto_insert(page, live_server, insert_chain)
    _open_import(page)
    _upload(page, "Reference,Part\nR1,\n")          # 空 Part

    expect(page.locator("#import-preview .flash-error")).to_be_visible()
    expect(page.get_by_role("button", name="应用")).to_have_count(0)
    expect(page.get_by_role("button", name="取消全部")).to_be_visible()


def test_cancel_discards_the_preview(live_server, insert_chain, page: Page):
    _goto_insert(page, live_server, insert_chain)
    _open_import(page)
    _upload(page, DIFF_CSV)
    page.get_by_role("button", name="取消全部").click()

    expect(page.locator("#import-preview")).to_be_empty()
    expect(page.locator("[data-testid=insert-changes] .change-row")).to_have_count(0)


def test_switching_mode_without_file_sends_no_request(live_server, insert_chain, page: Page):
    """没选文件就切「差异 / 全量」不该发注定失败的请求（与节点页同一守卫）。"""
    _goto_insert(page, live_server, insert_chain)
    _open_import(page)
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "import/preview" in r.url else None)
    page.locator("#import-form .seg label", has_text="全量").click()
    page.wait_for_timeout(400)
    assert reqs == [], f"不该发请求：{reqs}"


def test_full_mode_computes_removals(live_server, insert_chain, page: Page):
    """全量模式：目标 BOM 里缺席的位号算不贴。"""
    _goto_insert(page, live_server, insert_chain)
    _open_import(page)
    page.locator("#import-form .seg label", has_text="全量").click()
    # 插入位置是 c1，其折叠结果为 R1=10k, R2=22k, C1=100nF, C9=100nF
    # （R2→33k 在下游的 c2，不算在基准里）；目标里只去掉 C1
    _upload(page, "Reference,Part\nR1,10k\nR2,22k\nC9,100nF\n")

    expect(page.locator("#import-preview")).to_contain_text("C1")
    page.get_by_role("button", name="应用这 1 条修改").click()
    expect(page.locator("[data-testid=insert-changes] .change-row",
                        has_text="C1")).to_contain_text("不贴")


def test_imported_rows_keep_lint_icons(live_server, insert_chain, page: Page):
    """导入进暂存的行也要带 ⓘ/⚠——手工「添加这条」有，预览里也有，
    唯独应用之后消失的话，同一条改动在三个位置观感不一致。"""
    _goto_insert(page, live_server, insert_chain)
    _open_import(page)
    _upload(page, "Reference,Part\nC1,0.22uF\n")     # 归一成 220nF，带 fix
    expect(page.locator("#import-preview .lint-fix")).to_be_visible()

    page.get_by_role("button", name="应用这 1 条修改").click()

    row = page.locator("[data-testid=insert-changes] .change-row", has_text="C1")
    expect(row).to_contain_text("220nF")
    expect(row.locator(".lint-fix")).to_be_visible()


def test_import_of_only_upstream_values_says_so(live_server, insert_chain, page: Page):
    """导入的条目全部回到上游原值时，别报「已导入 0 条修改」。"""
    _goto_insert(page, live_server, insert_chain)
    page.locator("input[placeholder='位号（自动补全）']").fill("R1")
    page.locator("input[placeholder='新 Part 值']").fill("47k")
    page.get_by_role("button", name="添加这条").click()
    expect(page.locator("[data-testid=insert-changes] .change-row")).to_have_count(1)

    _open_import(page)
    _upload(page, "Reference,Part\nR1,10k\n")        # 10k = 父节点原值
    page.get_by_role("button", name="应用这 1 条修改").click()

    expect(page.locator("[data-testid=insert-changes] .change-row")).to_have_count(0)
    # 正向断言新文案：toast 3.5 秒自动消失，「不含旧文案」这种否定断言会等成假绿
    expect(page.locator(".toast").last).to_contain_text("都是上游原值")


def test_preview_request_failure_reports_error(live_server, insert_chain, page: Page):
    """预览请求失败要就地报错。原先指示条一消失、预览区空着，什么都不说。"""
    _goto_insert(page, live_server, insert_chain)
    _open_import(page)
    page.route("**/insert/import/preview",
               lambda route: route.fulfill(status=500, body="boom"))
    _upload(page, DIFF_CSV)

    expect(page.locator("#import-preview")).to_contain_text("导入检查失败")
