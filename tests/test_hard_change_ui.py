"""硬更改关键路径的浏览器测试。"""
import re
import struct
import zlib

import httpx
from playwright.sync_api import Page, expect

PNG_1PX = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
           b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def _solid_png(w=640, h=420, rgb=(70, 130, 200)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def _chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw))
            + _chunk(b"IEND", b""))


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


def test_detail_image_lightbox(live_server, page: Page):
    bid = _make_board(live_server, uid="HC3")
    # 记录一条带附图的硬更改（使用大尺寸图片，以便灯箱可正常放大显示）
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.fill("input[name=title]", "带图硬更改")
    page.set_input_files("input[name=files]", files=[
        {"name": "p.png", "mimeType": "image/png", "buffer": _solid_png()}])
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")

    # 进入详情页
    page.locator(".tl-item.hard").first.click()
    page.wait_for_load_state("networkidle")

    # 初始：灯箱不可见
    expect(page.locator(".lightbox")).to_be_hidden()
    # 点击缩略图 → 灯箱与大图可见
    page.locator(".hc-gallery img").first.click()
    expect(page.locator(".lightbox")).to_be_visible()
    expect(page.locator(".lightbox-img")).to_be_visible()
    box = page.locator(".lightbox-img").bounding_box()
    assert box and box["width"] > 200, box  # 放大后远大于 120px 缩略图，防止被缩略图样式误压
    # 点关闭按钮 → 灯箱隐藏
    page.locator(".lightbox-close").click()
    expect(page.locator(".lightbox")).to_be_hidden()

    # 点遮罩空白 → 灯箱隐藏
    page.locator(".hc-gallery img").first.click()
    expect(page.locator(".lightbox")).to_be_visible()
    page.locator(".lightbox").click(position={"x": 5, "y": 5})  # 角落空白处属于遮罩
    expect(page.locator(".lightbox")).to_be_hidden()

    # 按 ESC → 灯箱隐藏
    page.locator(".hc-gallery img").first.click()
    expect(page.locator(".lightbox")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".lightbox")).to_be_hidden()
