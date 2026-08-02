"""Playwright 测试：提交/保存时若附件已选未上传，先提示确认。

issue #133：附件表单的文件选择框里已选文件、但还没点「上传附件」就提交，
弹确认框——「是」＝放弃上传继续提交，「否」＝停留在原页面手动上传。

附件面板在**每个节点页**上都有（工作区草稿 + 已提交的修改节点），所以凡是
「一按就整页跳走、把已选文件悄悄丢掉」的入口都要拦：
  - 工作区草稿的「提交为新节点」（/commit）
  - 已提交节点的「编辑节点信息 → 保存信息」（/edit-info）
  - 修正历史节点后弹出的冲突确认框「确认」（/resolve）
  - 已提交节点的「📥 添加到草稿」（/copy-to-draft，HX-Redirect 跳去草稿页）
插入节点页没有附件功能，不在范围内；删除节点不拦（节点连同附件本来就要没）。
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


# ---------------------------------------------------------------- 已提交节点页

def _api_commit_chain(base, uid, msgs_parts):
    """建板并连着提交若干个节点，返回 (board_id, [已提交节点 id…]（不含根节点）)。

    msgs_parts 是 [(commit 说明, R1 的新值), …]。
    """
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "AttachBoard", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": uid},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\nC1,100nF\n",
                                   "text/csv")})
        bid = r.headers.get("location", "").split("/board/")[-1].split("?")[0]
        for msg, part in msgs_parts:
            c.post(f"/board/{bid}/workspace/edit",
                   data={"reference": "R1", "op": "modify", "part": part})
            c.post(f"/board/{bid}/commit", data={"message": msg})
        html = c.get(f"/board/{bid}", follow_redirects=True).text
    # 只要普通的已提交节点：根节点（初始状态）与工作区草稿都没有 edit-info 表单
    ids = sorted({int(m) for m in re.findall(r"/node/(\d+)", html)})
    with httpx.Client(base_url=base) as c:
        committed = [i for i in ids
                     if "/edit-info" in c.get(f"/board/{bid}/node/{i}").text]
    return bid, committed


def _goto_node(page: Page, base: str, bid: str, node_id: int):
    page.goto(f"{base}/board/{bid}/node/{node_id}")
    page.wait_for_load_state("networkidle")


def _open_details(page: Page, label: str):
    page.locator("details > summary", has_text=label).click()


def test_edit_info_prompts_when_attachment_pending(live_server, page: Page):
    """已提交节点：选了附件没上传就点「保存信息」→ 弹框；选「否」不保存、留在原页。"""
    bid, committed = _api_commit_chain(live_server, "AT5", [("n1", "22k")])
    node_id = committed[-1]
    _goto_node(page, live_server, bid, node_id)
    _pick_attachment(page)

    seen = []
    page.once("dialog", lambda d: (seen.append(d.message), d.dismiss()))
    _open_details(page, "编辑节点信息")
    page.locator("form[action$='/edit-info'] input[name=message]").fill("改过的标题")
    page.locator("form[action$='/edit-info'] button").click()
    page.wait_for_timeout(500)

    assert seen, "已提交节点上选了附件却没上传时保存信息，应弹出确认框"
    assert "附件" in seen[0]
    expect(page.locator("#attachments input[type=file]")).to_be_focused()
    with httpx.Client(base_url=live_server) as c:
        assert "改过的标题" not in c.get(f"/board/{bid}/node/{node_id}").text


def test_edit_info_confirm_saves_and_drops_upload(live_server, page: Page):
    """已提交节点：选「是」＝放弃上传，信息照常保存，附件不会被带上。"""
    bid, committed = _api_commit_chain(live_server, "AT6", [("n1", "22k")])
    node_id = committed[-1]
    _goto_node(page, live_server, bid, node_id)
    _pick_attachment(page)

    page.once("dialog", lambda d: d.accept())
    _open_details(page, "编辑节点信息")
    page.locator("form[action$='/edit-info'] input[name=message]").fill("改过的标题")
    page.locator("form[action$='/edit-info'] button").click()
    page.wait_for_url(re.compile(rf"/node/{node_id}\?flash="))

    with httpx.Client(base_url=live_server) as c:
        html = c.get(f"/board/{bid}/node/{node_id}").text
    assert "改过的标题" in html
    assert "附件（0）" in html
    assert "spec.txt" not in html


def test_edit_info_no_prompt_without_pending(live_server, page: Page):
    """已提交节点：没选附件时不拦，直接保存。"""
    bid, committed = _api_commit_chain(live_server, "AT7", [("n1", "22k")])
    node_id = committed[-1]
    _goto_node(page, live_server, bid, node_id)

    seen = []
    page.on("dialog", lambda d: (seen.append(d.message), d.accept()))
    _open_details(page, "编辑节点信息")
    page.locator("form[action$='/edit-info'] input[name=message]").fill("干净标题")
    page.locator("form[action$='/edit-info'] button").click()
    page.wait_for_url(re.compile(rf"/node/{node_id}\?flash="))

    assert not seen, f"没有待上传附件时不该弹确认框，却弹了：{seen}"


def _apply_fix(page: Page, ref: str, part: str):
    """在已提交节点上「应用修正」，触发下游冲突弹窗。"""
    _open_details(page, "修订")
    page.locator(".edit-form input[name=reference]").fill(ref)
    page.locator(".edit-form input[name=part]").fill(part)
    page.locator(".edit-form button.btn-primary").click()


def test_conflict_resolve_prompts_when_attachment_pending(live_server, page: Page):
    """冲突确认框的「确认」也整页跳走：附件已选未上传时先拦一道，取消则不解决冲突。"""
    bid, committed = _api_commit_chain(live_server, "AT8",
                                       [("n1", "22k"), ("n2", "33k")])
    node_id = committed[0]
    _goto_node(page, live_server, bid, node_id)
    _pick_attachment(page)
    _apply_fix(page, "R1", "44k")
    expect(page.locator("#modal .modal")).to_be_visible()

    seen = []
    page.once("dialog", lambda d: (seen.append(d.message), d.dismiss()))
    node_url = page.url
    page.locator("#modal button.btn-primary").click()
    page.wait_for_timeout(500)

    assert seen, "冲突确认框提交时也该检查待上传的附件"
    assert "附件" in seen[0]
    assert page.url == node_url
    expect(page.locator("#modal .modal")).to_be_visible()


def test_conflict_resolve_confirm_proceeds(live_server, page: Page):
    """选「是」＝放弃上传，冲突照常确认。"""
    bid, committed = _api_commit_chain(live_server, "AT9",
                                       [("n1", "22k"), ("n2", "33k")])
    node_id = committed[0]
    _goto_node(page, live_server, bid, node_id)
    _pick_attachment(page)
    _apply_fix(page, "R1", "44k")
    expect(page.locator("#modal .modal")).to_be_visible()

    page.once("dialog", lambda d: d.accept())
    page.locator("#modal button.btn-primary").click()
    page.wait_for_url(re.compile(rf"/node/{node_id}\?flash="))

    with httpx.Client(base_url=live_server) as c:
        assert "附件（0）" in c.get(f"/board/{bid}/node/{node_id}").text


def test_conflict_resolve_no_prompt_without_pending(live_server, page: Page):
    """没选附件时冲突确认框不该被拦。"""
    bid, committed = _api_commit_chain(live_server, "AT10",
                                       [("n1", "22k"), ("n2", "33k")])
    node_id = committed[0]
    _goto_node(page, live_server, bid, node_id)
    _apply_fix(page, "R1", "44k")
    expect(page.locator("#modal .modal")).to_be_visible()

    seen = []
    page.on("dialog", lambda d: (seen.append(d.message), d.accept()))
    page.locator("#modal button.btn-primary").click()
    page.wait_for_url(re.compile(rf"/node/{node_id}\?flash="))

    assert not seen, f"没有待上传附件时不该弹确认框，却弹了：{seen}"


def _click_copy_to_draft(page: Page):
    """顶部菜单里的「📥 添加到草稿」：hx-post 回 HX-Redirect，一按就跳去草稿页。"""
    page.click(".topnav-menu-btn")
    page.locator("button", has_text="添加到草稿").click()


def test_copy_to_draft_prompts_when_attachment_pending(live_server, page: Page):
    """「添加到草稿」也会整页跳走（还是跳去别的节点页）：附件已选未上传时先拦一道。"""
    bid, committed = _api_commit_chain(live_server, "AT11", [("n1", "22k")])
    node_id = committed[-1]
    _goto_node(page, live_server, bid, node_id)
    _pick_attachment(page)

    seen = []
    page.once("dialog", lambda d: (seen.append(d.message), d.dismiss()))
    node_url = page.url
    _click_copy_to_draft(page)
    page.wait_for_timeout(500)

    assert seen, "「添加到草稿」会跳走并丢掉已选附件，应先弹确认框"
    assert "附件" in seen[0]
    assert page.url == node_url
    expect(page.locator("#attachments input[type=file]")).to_be_focused()


def test_copy_to_draft_confirm_proceeds(live_server, page: Page):
    """选「是」＝放弃上传，照常复制到草稿。"""
    bid, committed = _api_commit_chain(live_server, "AT12", [("n1", "22k")])
    node_id = committed[-1]
    _goto_node(page, live_server, bid, node_id)
    _pick_attachment(page)

    page.once("dialog", lambda d: d.accept())
    _click_copy_to_draft(page)
    page.wait_for_url(re.compile(r"/node/\d+\?flash="))

    assert "已复制" in page.url or "复制" in page.locator("#toast-zone").inner_text()
    assert str(node_id) not in page.url.split("/node/")[1].split("?")[0]


def test_copy_to_draft_no_prompt_without_pending(live_server, page: Page):
    """没选附件时「添加到草稿」不该被拦。"""
    bid, committed = _api_commit_chain(live_server, "AT13", [("n1", "22k")])
    node_id = committed[-1]
    _goto_node(page, live_server, bid, node_id)

    seen = []
    page.on("dialog", lambda d: (seen.append(d.message), d.accept()))
    _click_copy_to_draft(page)
    page.wait_for_url(re.compile(r"/node/\d+\?flash="))

    assert not seen, f"没有待上传附件时不该弹确认框，却弹了：{seen}"
