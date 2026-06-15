"""硬更改关键路径的浏览器测试。"""
import re

import httpx
from playwright.sync_api import Page, expect

PNG_1PX = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
           b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def _make_board(base: str, uid: str = "HC1") -> str:
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "HCBoard", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": uid},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\n", "text/csv")})
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]


def test_record_hard_change_flow(live_server, page: Page):
    bid = _make_board(live_server)
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.fill("input[name=title]", "飞线 R1→R9")
    page.fill("textarea[name=description]", "演示飞线")
    page.set_input_files("input[name=files]", files=[
        {"name": "p.png", "mimeType": "image/png", "buffer": PNG_1PX}])
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    expect(page.locator(".tl-item.hard")).to_contain_text("飞线 R1→R9")
    page.locator(".tl-item.hard").first.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".hc-gallery img").first).to_be_visible()


def test_new_form_time_is_browser_local(live_server, page: Page):
    bid = _make_board(live_server, uid="HC2")

    # 服务器不再注入硬编码时间：原始 HTML 中 occurred_at 的 value 为空
    with httpx.Client(base_url=live_server) as c:
        html = c.get(f"/board/{bid}/hard-change/new").text
    m = re.search(r'name="occurred_at"[^>]*\bvalue="([^"]*)"', html)
    assert m is not None, "未找到 occurred_at 字段"
    assert m.group(1) == "", f"服务器仍注入了时间：{m.group(1)!r}"

    # 浏览器加载后由客户端填入本地当前时间（datetime-local 格式）
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.wait_for_function(
        "document.querySelector('input[name=occurred_at]').value !== ''")
    val = page.input_value("input[name=occurred_at]")
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", val), val
    browser_now = page.evaluate(
        "() => { const d = new Date();"
        " return new Date(d.getTime() - d.getTimezoneOffset()*60000)"
        ".toISOString().slice(0,16); }")
    # 到「年月日时」一致即可证明取的是浏览器本地时间（容忍跨分钟；极端跨小时可忽略）
    assert val[:13] == browser_now[:13], f"{val} vs {browser_now}"
