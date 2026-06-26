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


def test_picked_images_accumulate_across_dialogs(live_server, page: Page):
    """多次「选择文件」应叠加，而非覆盖（issue #69 需求 1）。"""
    bid = _make_board(live_server, uid="HC4")
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.set_input_files("input[name=files]", files=[
        {"name": "a.png", "mimeType": "image/png", "buffer": PNG_1PX}])
    expect(page.locator(".hc-pending-thumb")).to_have_count(1)
    # 第二次选择不同文件：应累积到 2 张，而不是被覆盖成 1 张
    page.set_input_files("input[name=files]", files=[
        {"name": "b.png", "mimeType": "image/png", "buffer": PNG_1PX}])
    expect(page.locator(".hc-pending-thumb")).to_have_count(2)


def test_pending_image_can_be_removed_before_submit(live_server, page: Page):
    """提交前每张待上传图片可单独删除（issue #69 需求 3）。"""
    bid = _make_board(live_server, uid="HC5")
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.set_input_files("input[name=files]", files=[
        {"name": "a.png", "mimeType": "image/png", "buffer": PNG_1PX},
        {"name": "b.png", "mimeType": "image/png", "buffer": PNG_1PX}])
    expect(page.locator(".hc-pending-thumb")).to_have_count(2)
    page.locator(".hc-pending-del").first.click()
    expect(page.locator(".hc-pending-thumb")).to_have_count(1)
    # 隐藏 file input 也应同步只剩 1 个
    assert page.evaluate(
        "document.querySelector('input[name=files]').files.length") == 1


def test_accumulated_images_are_all_submitted(live_server, page: Page):
    """两次选择累积的图片，提交后应全部入库（验证 DataTransfer→multipart 端到端）。"""
    bid = _make_board(live_server, uid="HC8")
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.fill("input[name=title]", "累积上传")
    page.set_input_files("input[name=files]", files=[
        {"name": "a.png", "mimeType": "image/png", "buffer": PNG_1PX}])
    page.set_input_files("input[name=files]", files=[
        {"name": "b.png", "mimeType": "image/png", "buffer": PNG_1PX}])
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    page.locator(".tl-item.hard").first.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".hc-gallery .hc-photo")).to_have_count(2)


def _paste_event_js():
    """构造一个携带 clipboardData 的 paste 事件并 dispatch；返回 dispatchEvent 结果
    （false 表示被 preventDefault 拦截）。"""
    return """({bytes, mime, name, target, text}) => {
        const dt = new DataTransfer();
        if (bytes) {
          const file = new File([new Uint8Array(bytes)], name, {type: mime});
          dt.items.add(file);
        }
        if (text) { dt.setData('text/plain', text); }
        const el = target ? document.querySelector(target) : document;
        const ev = new ClipboardEvent('paste',
            {clipboardData: dt, bubbles: true, cancelable: true});
        return el.dispatchEvent(ev);
    }"""


def test_paste_image_adds_to_pending(live_server, page: Page):
    """粘贴图片应追加到待上传列表并拦截默认行为（issue #69 需求 2）。"""
    bid = _make_board(live_server, uid="HC6")
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    not_cancelled = page.evaluate(_paste_event_js(),
        {"bytes": list(PNG_1PX), "mime": "image/png", "name": "clip.png"})
    expect(page.locator(".hc-pending-thumb")).to_have_count(1)
    assert not_cancelled is False, "抓到图片时应 preventDefault"


def test_paste_multiple_images_get_distinct_names(live_server, page: Page):
    """一次粘贴多张无扩展名图片，应分别得到不重复的文件名（issue #69 需求 2）。"""
    bid = _make_board(live_server, uid="HC9")
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.evaluate("""(bytes) => {
        const dt = new DataTransfer();
        for (let i = 0; i < 2; i++) {
          dt.items.add(new File([new Uint8Array(bytes)], '', {type: 'image/png'}));
        }
        document.dispatchEvent(new ClipboardEvent('paste',
            {clipboardData: dt, bubbles: true, cancelable: true}));
    }""", list(PNG_1PX))
    expect(page.locator(".hc-pending-thumb")).to_have_count(2)
    names = page.evaluate(
        "[...document.querySelector('input[name=files]').files].map(f => f.name)")
    assert len(set(names)) == 2, f"粘贴的多张图片文件名重复：{names}"


def test_paste_unsupported_image_type_rejected(live_server, page: Page):
    """粘贴非允许格式（如 SVG）应被忽略，与 accept 四类对齐（issue #69）。"""
    bid = _make_board(live_server, uid="HC10")
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.evaluate(_paste_event_js(),
        {"bytes": list(b"<svg/>"), "mime": "image/svg+xml", "name": "x.svg"})
    expect(page.locator(".hc-pending-thumb")).to_have_count(0)


def test_paste_text_into_textarea_not_swallowed(live_server, page: Page):
    """纯文字粘贴不应被拦截，文本框照常接收（issue #69 需求 2：只对图片响应）。"""
    bid = _make_board(live_server, uid="HC7")
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.click("textarea[name=description]")
    not_cancelled = page.evaluate(_paste_event_js(),
        {"text": "纯文字", "target": "textarea[name=description]"})
    assert not_cancelled is True, "纯文字粘贴不应被 preventDefault"
    expect(page.locator(".hc-pending-thumb")).to_have_count(0)


def test_new_form_defaults_to_submit_time(live_server, page: Page):
    """新建表单默认用提交时间（issue #74, #80）：'指定'未勾选，occurred_at 为空，
    时间输入禁用并呈灰色——服务端将用提交时刻（精确到秒）。"""
    bid = _make_board(live_server, uid="HC2")

    # 服务器侧 HTML：仍含 occurred_at_local 可见输入与 occurred_at 隐藏字段
    with httpx.Client(base_url=live_server) as c:
        html = c.get(f"/board/{bid}/hard-change/new").text
    assert 'name="occurred_at_local"' in html, "未找到 occurred_at_local 可见输入"
    assert 'name="occurred_at"' in html, "未找到 occurred_at 隐藏字段"
    assert 'name="specify_time"' in html, "未找到 specify_time 复选框"
    # issue #80：标题由「指定时间 发生时间」改为「时间 ✅指定」
    assert "指定</label>" in html, "未找到 '指定' 复选框文案（issue #80）"
    assert "指定时间" not in html, "旧文案 '指定时间' 应拆为 '时间'+'指定'（issue #80）"

    # 浏览器加载后：复选框未勾选、隐藏 occurred_at 为空、可见输入禁用
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    expect(page.locator("input[name=specify_time]")).not_to_be_checked()
    assert page.evaluate("document.querySelector('input[name=occurred_at]').value") == ""
    expect(page.locator("input[name=occurred_at_local]")).to_be_disabled()
    # issue #80 需求 2：未勾选时时间输入框应呈灰色 disable 视觉
    opacity = page.evaluate(
        "parseFloat(getComputedStyle("
        "document.querySelector('input[name=occurred_at_local]')).opacity)")
    assert opacity < 1, f"未勾选时时间输入应半透明灰色，实际 opacity={opacity}"


def test_new_form_specify_time_fills_input(live_server, page: Page):
    """勾选'指定时间'后，时间输入启用并填入浏览器本地时间，occurred_at 转为 UTC。"""
    bid = _make_board(live_server, uid="HC2B")
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.check("input[name=specify_time]")
    page.wait_for_function(
        "document.querySelector('input[name=occurred_at_local]').value !== ''")
    expect(page.locator("input[name=occurred_at_local]")).to_be_enabled()
    local_val = page.input_value("input[name=occurred_at_local]")
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", local_val), local_val
    browser_now = page.evaluate(
        "() => { const d = new Date();"
        " return new Date(d.getTime() - d.getTimezoneOffset()*60000)"
        ".toISOString().slice(0,16); }")
    # 到「年月日时」一致即可证明取的是浏览器本地时间（容忍跨分钟；极端跨小时可忽略）
    assert local_val[:13] == browser_now[:13], f"{local_val} vs {browser_now}"
    # 隐藏字段应已被 sync() 转成 canonical UTC 格式
    utc_val = page.evaluate("document.querySelector('input[name=occurred_at]').value")
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$", utc_val), utc_val
    # 再次取消勾选：occurred_at 应清空（回退为提交时间）
    page.uncheck("input[name=specify_time]")
    assert page.evaluate("document.querySelector('input[name=occurred_at]').value") == ""


def test_edit_form_specify_time_checked_with_existing(live_server, page: Page):
    """编辑表单：已有 occurred_at 时，'指定时间'默认勾选并准确展示原时间（含秒）。"""
    bid = _make_board(live_server, uid="HC2C")
    # 先建一条硬更改（指定时间含非零秒：2026-06-01T10:30:45 UTC）
    with httpx.Client(base_url=live_server, follow_redirects=False) as c:
        c.post(f"/board/{bid}/hard-change",
               data={"title": "原记录", "occurred_at": "2026-06-01T10:30:45+00:00",
                     "description": ""})
    import re as _re
    with httpx.Client(base_url=live_server) as c:
        rg = c.get(f"/board/{bid}").text
    hid = _re.search(rf"/board/{bid}/hard-change/(\d+)", rg).group(1)

    page.goto(f"{live_server}/board/{bid}/hard-change/{hid}/edit")
    cb = page.locator("input[name=specify_time]")
    expect(cb).to_be_checked()
    expect(page.locator("input[name=occurred_at_local]")).to_be_enabled()
    # 可见输入应保留原时间的秒（不被抹成 00）
    local_val = page.input_value("input[name=occurred_at_local]")
    assert local_val.endswith(":45"), local_val
    # 隐藏 occurred_at 应回填原 UTC 值（含秒），证明往返不丢秒
    utc_val = page.evaluate("document.querySelector('input[name=occurred_at]').value")
    assert utc_val == "2026-06-01T10:30:45+00:00", utc_val


def test_edit_uncheck_specify_uses_submit_time(live_server, page: Page):
    """编辑时取消'指定时间'：occurred_at 清空，服务端改用提交时刻（方案1）。"""
    bid = _make_board(live_server, uid="HC2D")
    with httpx.Client(base_url=live_server, follow_redirects=False) as c:
        c.post(f"/board/{bid}/hard-change",
               data={"title": "原记录", "occurred_at": "2020-01-01T00:00:00+00:00",
                     "description": ""})
    import re as _re
    with httpx.Client(base_url=live_server) as c:
        rg = c.get(f"/board/{bid}").text
    hid = _re.search(rf"/board/{bid}/hard-change/(\d+)", rg).group(1)

    page.goto(f"{live_server}/board/{bid}/hard-change/{hid}/edit")
    page.uncheck("input[name=specify_time]")
    # 取消勾选后隐藏字段应清空
    assert page.evaluate("document.querySelector('input[name=occurred_at]').value") == ""
    page.click("button[type=submit]")
    page.wait_for_url(f"{live_server}/board/{bid}/hard-change/{hid}*")
    # 存库时间不再是 2020 的旧值，而是提交时刻（>= 当前年份）
    with httpx.Client(base_url=live_server) as c:
        detail = c.get(f"/board/{bid}/hard-change/{hid}").text
    assert "2020-01-01" not in detail


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
