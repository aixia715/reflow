"""Playwright 测试：导入面板切换「差异 / 全量」时的预览触发时机。

导入表单挂的是 `hx-trigger="change"`（整表单级），mode 单选按钮的切换同样
是一次 change。还没选 CSV 文件就切模式，会带着空文件发预览请求，用户被
「请选择 CSV 文件」打断——他本来就正准备去选。守卫加在触发条件上：没文件
就不发请求；有文件时切模式必须照样重新预览（两种模式语义完全不同）。
"""
import httpx
import pytest
from playwright.sync_api import Page, expect


def _api_create_board(base, name="ModeBoard", pcb="v1", bom="bomA", uid="MD1",
                      csv="Reference,Part\nR1,10k\nC1,100nF\n"):
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": name, "pcb_version": pcb,
                         "bom_version": bom, "board_uid": uid},
                   files={"file": ("bom.csv", csv.encode(), "text/csv")})
    return r.headers.get("location", "").split("/board/")[-1].split("?")[0]


def _open_import_panel(page: Page, base: str, bid: str):
    """进入工作区草稿节点页并展开「从 CSV 导入修改…」面板。"""
    page.goto(f"{base}/board/{bid}")
    page.locator("a.tl-card", has_text="工作区草稿").click()
    page.wait_for_load_state("networkidle")
    page.locator("summary", has_text="从 CSV 导入修改").click()


def _switch_mode(page: Page, label: str):
    """radio 本体被 `.seg` 样式隐藏（display:none），只能点它的 label。"""
    page.locator("#import-form .seg label", has_text=label).click()


def _pick_csv(page: Page, text: str):
    page.locator("#import-form input[type=file]").set_input_files(files=[{
        "name": "changes.csv", "mimeType": "text/csv",
        "buffer": text.encode(),
    }])


def _count_preview_requests(page: Page):
    """收集发往 /import/preview 的请求。直接数请求，而不是等一会儿看有没有
    渲染出错误——后者在请求恰好慢过等待时间时会假通过。"""
    hits: list[str] = []
    page.on("request",
            lambda r: hits.append(r.url) if "/import/preview" in r.url else None)
    return hits


def test_switch_mode_without_file_does_not_complain(live_server, page: Page):
    """没选文件时切换差异↔全量：不发预览请求，也就没有「请选择 CSV 文件」。"""
    bid = _api_create_board(live_server, uid="MD1")
    _open_import_panel(page, live_server, bid)

    hits = _count_preview_requests(page)
    preview = page.locator("#import-preview")
    _switch_mode(page, "全量")
    page.wait_for_timeout(500)
    assert hits == []
    assert "请选择 CSV 文件" not in preview.inner_text()

    _switch_mode(page, "差异")
    page.wait_for_timeout(500)
    assert hits == []
    assert "请选择 CSV 文件" not in preview.inner_text()
    assert preview.inner_text().strip() == ""


def test_switch_mode_after_cancel_all_does_not_complain(live_server, page: Page):
    """「取消全部」清空文件框后再切模式：同样不发请求、不报错。"""
    bid = _api_create_board(live_server, name="ModeBoard4", bom="bomD", uid="MD4")
    _open_import_panel(page, live_server, bid)

    _pick_csv(page, "Reference,Part,OP\nR9,1k,add\n")
    preview = page.locator("#import-preview")
    expect(preview).to_contain_text("共 1 条修改")

    page.locator("#import-preview button", has_text="取消全部").click()
    hits = _count_preview_requests(page)
    _switch_mode(page, "全量")
    page.wait_for_timeout(500)
    assert hits == []
    assert preview.inner_text().strip() == ""


def test_switch_mode_with_file_repreviews_in_new_mode(live_server, page: Page):
    """已选文件时切模式仍要重新预览：全量模式下缺席的 C1 应被算成「不贴」。"""
    bid = _api_create_board(live_server, name="ModeBoard2", bom="bomB", uid="MD2")
    _open_import_panel(page, live_server, bid)

    # 差异模式：CSV 只列 R1 → 只有 1 条修改，C1 不受影响
    _pick_csv(page, "Reference,Part\nR1,22k\n")
    preview = page.locator("#import-preview")
    expect(preview).to_contain_text("共 1 条修改")
    assert "C1" not in preview.inner_text()

    # 切到全量：同一份 CSV 变成完整目标 BOM，缺席的 C1 自动算不贴
    _switch_mode(page, "全量")
    expect(preview).to_contain_text("共 2 条修改")
    expect(preview).to_contain_text("C1")


def test_selecting_file_still_triggers_preview(live_server, page: Page):
    """守卫不能误伤正常路径：选文件这一次 change 必须照样发预览请求。"""
    bid = _api_create_board(live_server, name="ModeBoard3", bom="bomC", uid="MD3")
    _open_import_panel(page, live_server, bid)

    _pick_csv(page, "Reference,Part,OP\nR9,1k,add\n")
    expect(page.locator("#import-preview")).to_contain_text("共 1 条修改")
