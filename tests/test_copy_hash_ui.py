"""复制哈希值按钮的浏览器测试。

issue #53：在状态演进和提交点详情页的哈希值旁添加复制按钮。
页面运行在 HTTP 协议上，navigator.clipboard 在非安全上下文不可用，
需有 fallback（document.execCommand('copy')）。
"""
import httpx
from playwright.sync_api import Page, expect


def _make_board_with_committed_node(base: str, uid: str = "CP1") -> str:
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "CopyBoard", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": uid},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\n", "text/csv")})
        bid = r.headers["location"].split("?")[0].rsplit("/", 1)[-1]
        c.post(f"/board/{bid}/workspace/edit",
               data={"reference": "R1", "op": "modify", "part": "47k"})
        c.post(f"/board/{bid}/commit", data={"message": "改 R1"})
    return bid


def test_copy_hash_button_copies_and_shows_toast(live_server, page: Page):
    """点击复制按钮后应复制短哈希到剪贴板并显示 toast。"""
    bid = _make_board_with_committed_node(live_server)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])

    page.goto(f"{live_server}/board/{bid}")
    page.locator(".copy-hash").first.click()

    expect(page.locator("#toast-zone .toast")).to_contain_text("复制")

    clipboard = page.evaluate("navigator.clipboard.readText()")
    assert len(clipboard) == 8, f"剪贴板内容不是 8 位短哈希: {clipboard}"
    assert all(c in "0123456789abcdef" for c in clipboard)


def test_copy_hash_fallback_when_clipboard_api_unavailable(live_server, page: Page):
    """非安全上下文（HTTP）下 navigator.clipboard 不可用时，fallback 仍能复制。"""
    bid = _make_board_with_committed_node(live_server, uid="CP2")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])

    page.goto(f"{live_server}/board/{bid}")
    # 保存原始 clipboard，然后模拟非安全上下文（navigator.clipboard 不可用）
    page.evaluate(
        "window.__realClipboard = navigator.clipboard;"
        "Object.defineProperty(navigator, 'clipboard', "
        "{get: () => undefined, configurable: true})")

    page.locator(".copy-hash").first.click()

    expect(page.locator("#toast-zone .toast")).to_contain_text("复制")

    # 恢复 clipboard 以读取验证
    page.evaluate(
        "Object.defineProperty(navigator, 'clipboard', "
        "{get: () => window.__realClipboard, configurable: true})")
    clipboard = page.evaluate("navigator.clipboard.readText()")
    assert len(clipboard) == 8, f"fallback 复制内容不是 8 位短哈希: {clipboard}"


def test_copy_hash_on_node_detail_page(live_server, page: Page):
    """节点详情页的复制按钮也能复制哈希。"""
    bid = _make_board_with_committed_node(live_server, uid="CP3")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])

    # 从状态图进入节点详情页
    page.goto(f"{live_server}/board/{bid}")
    page.locator("a.tl-card", has_text="改 R1").click()
    page.wait_for_load_state("networkidle")

    page.locator(".copy-hash").first.click()

    expect(page.locator("#toast-zone .toast")).to_contain_text("复制")

    clipboard = page.evaluate("navigator.clipboard.readText()")
    assert len(clipboard) == 8
