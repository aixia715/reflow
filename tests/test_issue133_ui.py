"""Playwright 测试：工作区提交时若附件已选未上传，先提示确认。

issue #133：附件表单的文件选择框里已选文件、但还没点「上传附件」就提交 commit，
弹确认框——「是」＝放弃上传继续提交，「否」＝停留在原页面手动上传。
只在工作区草稿的「提交为新节点」上生效（插入节点页没有附件功能）。
"""
import re

import httpx
from playwright.sync_api import Page, expect


def _api_create_board(base, uid, csv="Reference,Part\nR1,10k\nC1,100nF\n"):
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "AttachBoard", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": uid},
                   files={"file": ("bom.csv", csv.encode(), "text/csv")})
        bid = r.headers.get("location", "").split("/board/")[-1].split("?")[0]
        # 草稿里放一条修改，贴近真实提交场景
        c.post(f"/board/{bid}/workspace/edit",
               data={"reference": "R1", "op": "modify", "part": "22k"})
    return bid


def _goto_workspace(page: Page, base: str, bid: str):
    page.goto(f"{base}/board/{bid}")
    page.locator("a.tl-card", has_text="工作区草稿").click()
    page.wait_for_load_state("networkidle")


def _pick_attachment(page: Page, name="spec.txt"):
    """只在文件选择框里选文件，不点「上传附件」。"""
    page.locator("#attachments input[type=file]").set_input_files(
        {"name": name, "mimeType": "text/plain", "buffer": b"hello"})


def _submit_commit(page: Page, message: str):
    page.locator(".commit-box input[name=message]").fill(message)
    page.locator(".commit-box button").click()


def _state_graph_html(base: str, bid: str) -> str:
    with httpx.Client(base_url=base) as c:
        return c.get(f"/board/{bid}").text


def test_pending_attachment_prompts_and_cancel_stays(live_server, page: Page):
    """选了附件没上传就提交 → 弹确认框；选「否」留在页面，未提交。"""
    bid = _api_create_board(live_server, "AT1")
    _goto_workspace(page, live_server, bid)
    _pick_attachment(page)

    seen = []

    def on_dialog(d):
        seen.append(d.message)
        d.dismiss()

    page.once("dialog", on_dialog)
    node_url = page.url
    _submit_commit(page, "c-cancel")
    page.wait_for_timeout(500)

    assert seen, "选了附件却没上传时提交，应弹出确认框"
    assert "附件" in seen[0]
    # 停留在原页面，文件仍在选择框里，等用户手动上传
    assert page.url == node_url
    expect(page.locator("#attachments input[type=file]")).to_be_visible()
    # 焦点落到文件选择框：键盘用户不用自己 Tab 回附件面板
    expect(page.locator("#attachments input[type=file]")).to_be_focused()
    assert "c-cancel" not in _state_graph_html(live_server, bid)


def test_pending_attachment_confirm_commits_and_drops_upload(live_server, page: Page):
    """选「是」＝放弃上传，正常提交为新节点，附件不会被带上。"""
    bid = _api_create_board(live_server, "AT2")
    _goto_workspace(page, live_server, bid)
    _pick_attachment(page)

    page.once("dialog", lambda d: d.accept())
    _submit_commit(page, "c-go")
    page.wait_for_url(re.compile(r"/board/\d+(\?.*)?$"))

    html = _state_graph_html(live_server, bid)
    assert "c-go" in html
    # 附件被放弃：新提交的节点上没有附件
    node_id = sorted(int(m) for m in re.findall(r"/node/(\d+)", html))[-1]
    with httpx.Client(base_url=live_server) as c:
        node_html = c.get(f"/board/{bid}/node/{node_id}").text
    assert "附件（0）" in node_html
    assert "spec.txt" not in node_html


def test_no_pending_attachment_commits_without_prompt(live_server, page: Page):
    """没选附件时不该拦一道：直接提交。"""
    bid = _api_create_board(live_server, "AT3")
    _goto_workspace(page, live_server, bid)

    seen = []
    page.on("dialog", lambda d: (seen.append(d.message), d.accept()))
    _submit_commit(page, "c-clean")
    page.wait_for_url(re.compile(r"/board/\d+(\?.*)?$"))

    assert not seen, f"没有待上传附件时不该弹确认框，却弹了：{seen}"
    assert "c-clean" in _state_graph_html(live_server, bid)


def test_uploaded_attachment_does_not_prompt(live_server, page: Page):
    """已经点过「上传附件」的（选择框已清空）不算待上传，不拦。"""
    bid = _api_create_board(live_server, "AT4")
    _goto_workspace(page, live_server, bid)
    _pick_attachment(page, name="done.txt")
    page.locator(".attach-form button").click()
    expect(page.locator(".attach-name", has_text="done.txt")).to_be_visible()

    seen = []
    page.on("dialog", lambda d: (seen.append(d.message), d.accept()))
    _submit_commit(page, "c-uploaded")
    page.wait_for_url(re.compile(r"/board/\d+(\?.*)?$"))

    assert not seen, f"附件已上传完毕，不该弹确认框，却弹了：{seen}"
