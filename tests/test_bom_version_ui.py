"""BOM 版本页：预览、逐板确认、根节点链接，以及白天 / 夜间 / 窄屏。"""
import re
import uuid

from playwright.sync_api import expect

from app.db import connect
from app.models import get_bom_version_row, list_nodes
from tests.helpers import auth_httpx


def _channels(rgb: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", rgb)][:3]


def _contrast_ok(page) -> None:
    field = page.locator("input[name=reference]")
    background = field.evaluate("e => getComputedStyle(e).backgroundColor")
    foreground = field.evaluate("e => getComputedStyle(e).color")
    assert sum(_channels(background)) != sum(_channels(foreground)), (background, foreground)
    # 夜间是浅字深底，白天是深字浅底；两者都不能贴在一起看不清
    assert abs(sum(_channels(background)) - sum(_channels(foreground))) > 80, (background, foreground)


def test_preview_confirm_root_link_and_themes(page, live_server, live_db):
    suffix = uuid.uuid4().hex[:8]
    name = "Ver" + suffix
    uid_a = "VA" + suffix
    uid_b = "VB" + suffix
    with auth_httpx(live_server, follow_redirects=False) as client:
        created = client.post(
            "/board/new",
            data={"board_name": name, "pcb_version": "v1", "bom_version": "bomA",
                  "board_uid": uid_a},
            files={"file": ("bom.csv", b"Reference,Part\nR1,10k\nC1,100nF\n", "text/csv")},
        )
        assert created.status_code == 303, created.text
        board_a = int(created.headers["location"].split("?")[0].rsplit("/", 1)[-1])
        second = client.post(
            "/board/new",
            data={"board_name": name, "pcb_version": "v1", "bom_version": "bomA",
                  "board_uid": uid_b},
        )
        assert second.status_code == 303, second.text
        edited = client.post(
            f"/board/{board_a}/workspace/edit",
            data={"reference": "R1", "op": "modify", "part": "47k"},
        )
        assert edited.status_code == 303, edited.text
        committed = client.post(f"/board/{board_a}/commit", data={"message": "改 R1"})
        assert committed.status_code == 303, committed.text

    version = get_bom_version_row(connect(live_db), name, "v1", "bomA")
    page.goto(f"{live_server}/bom-version/{version['id']}")
    expect(page.locator("h1")).to_have_text("BOM 版本")
    expect(page.locator("table.bom")).to_contain_text("10k")
    expect(page.get_by_text(uid_a)).to_have_count(0)
    expect(page.get_by_text(uid_b)).to_have_count(0)

    _contrast_ok(page)
    page.evaluate("localStorage.setItem('theme','dark')")
    page.reload()
    _contrast_ok(page)
    page.evaluate("localStorage.setItem('theme','light')")
    page.set_viewport_size({"width": 375, "height": 800})
    page.reload()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )
    page.set_viewport_size({"width": 1280, "height": 800})

    page.locator("input[name=reference]").fill("R1")
    page.locator("input[name=part]").fill("22k")
    page.get_by_role("button", name="查看影响").click()
    modal = page.locator(".modal")
    expect(modal).to_contain_text("将影响以下 2 块板")
    expect(modal).to_contain_text(uid_a)
    expect(modal).to_contain_text(uid_b)
    expect(modal).to_contain_text("保留下游值")
    expect(modal).to_contain_text("采用修正值")
    modal.get_by_text("采用修正值").click()
    modal.get_by_role("button", name="确认修正").click()

    row = page.locator("table.bom tr", has_text="R1")
    expect(row).to_contain_text("22k")
    expect(page.locator(".toast")).to_contain_text("已修正初始清单")

    root = next(n["id"] for n in list_nodes(connect(live_db), board_a) if n["parent_id"] is None)
    page.goto(f"{live_server}/board/{board_a}/node/{root}")
    expect(page.locator("body")).to_contain_text("初始清单在 BOM 版本页维护")
    link = page.locator(f"a[href='/bom-version/{version['id']}']")
    expect(link).to_be_visible()
    expect(page.locator("body")).to_contain_text("原始清单在")
    expect(page.locator("body")).to_contain_text("被 admin 修正")
    link.click()
    expect(page.locator("h1")).to_have_text("BOM 版本")
    expect(page.locator("table.bom tr", has_text="R1")).to_contain_text("22k")
