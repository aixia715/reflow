"""issue #122：header 上的哈希输入框测试。

用户在 header 输入哈希（长/短）后提交，应跳转到对应提交节点详情页。
底层 `/hash/{value}` 路由已实现，本测试聚焦 header 输入框的存在与提交跳转。
"""
import httpx
from playwright.sync_api import Page, expect


def _make_board_with_committed_node(base: str, uid: str = "H1") -> tuple[str, str]:
    import re
    with httpx.Client(base_url=base, follow_redirects=True) as c:
        r = c.post("/board/new",
                   data={"board_name": "HashBox", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": uid},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\n", "text/csv")},
                   follow_redirects=False)
        bid = r.headers["location"].split("?")[0].rsplit("/", 1)[-1]
        c.post(f"/board/{bid}/workspace/edit",
               data={"reference": "R1", "op": "modify", "part": "47k"})
        c.post(f"/board/{bid}/commit", data={"message": "改 R1"})
        # 取已提交节点 id 与短哈希
        page = c.get(f"/board/{bid}").text
        m = re.search(r"copyHash\('([0-9a-f]{8})'\)", page)
        assert m, "未在状态图找到节点短哈希"
        short = m.group(1)
    return bid, short


def test_header_has_hash_input_on_home(live_server):
    """首页 header 应包含哈希输入框。"""
    page = httpx.get(live_server + "/").text
    assert 'id="hash-jump"' in page, "header 缺少哈希输入框"
    assert "placeholder" in page


def test_hash_input_jumps_to_node_detail(live_server, page: Page):
    """在 header 输入短哈希提交后，应跳转到对应节点详情页。"""
    bid, short = _make_board_with_committed_node(live_server)

    page.goto(f"{live_server}/")
    page.locator("#hash-jump").fill(short)
    page.locator("#hash-jump-form").evaluate("el => el.requestSubmit()")

    page.wait_for_load_state("networkidle")
    assert f"/board/{bid}/node/" in page.url, f"未跳转到节点详情页: {page.url}"


def test_hash_input_unknown_shows_error(live_server, page: Page):
    """输入不存在的哈希应给出可见错误提示，不静默失败。"""
    page.goto(f"{live_server}/")
    page.locator("#hash-jump").fill("deadbeef")
    page.locator("#hash-jump-form").evaluate("el => el.requestSubmit()")

    # 触发跳转后 /hash/deadbeef 返回 404，页面应有错误提示
    page.wait_for_load_state("networkidle")
    expect(page.locator("#hash-jump-error")).to_be_visible()
