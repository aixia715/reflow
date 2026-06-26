"""Playwright 测试：插入变更节点的前端交互（暂存式编辑、实时预览、保存、冲突弹窗）。

issue #60：在某已提交节点后插入新节点。编辑界面本地暂存，保存才落库；不保存不建节点。
"""
import os
import sys
import time
import sqlite3
import subprocess
import urllib.request
import urllib.error

import httpx
import pytest
from playwright.sync_api import Page, expect

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18799
BASE = f"http://localhost:{PORT}"


@pytest.fixture(scope="module")
def insert_server(tmp_path_factory):
    db = tmp_path_factory.mktemp("insdb") / "t.sqlite"
    up = tmp_path_factory.mktemp("insup")
    env = {**os.environ, "REFLOW_DB": str(db), "REFLOW_UPLOAD_DIR": str(up)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--port", str(PORT), "--log-level", "warning"],
        env=env, cwd=REPO_ROOT)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    else:
        proc.terminate()
        pytest.fail("insert_server 未能启动")
    yield {"base": BASE, "db": str(db)}
    proc.terminate()
    proc.wait(timeout=5)


_uid = [0]


def _make_chain(server):
    """建一条 root -> c1 -> c2 -> 草稿，c1/c2 的 committed_at 相隔多天。返回 (bid, c1, c2)。"""
    _uid[0] += 1
    uid = f"S{_uid[0]}"
    with httpx.Client(base_url=server["base"], follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "INS", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": uid},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\n", "text/csv")})
        bid = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])
        c.post(f"/board/{bid}/workspace/edit",
               data={"reference": "C9", "op": "add", "part": "100nF"})
        c.post(f"/board/{bid}/commit", data={"message": "c1"})
        c.post(f"/board/{bid}/workspace/edit",
               data={"reference": "R1", "op": "modify", "part": "22k"})
        c.post(f"/board/{bid}/commit", data={"message": "c2"})
    conn = sqlite3.connect(server["db"])
    rows = conn.execute(
        "SELECT id FROM nodes WHERE board_id=? AND is_committed=1 AND parent_id IS NOT NULL ORDER BY id",
        (bid,)).fetchall()
    c1, c2 = rows[0][0], rows[1][0]
    conn.execute("UPDATE nodes SET committed_at=? WHERE id=?", ("2026-06-01T00:00:00+00:00", c1))
    conn.execute("UPDATE nodes SET committed_at=? WHERE id=?", ("2026-06-10T00:00:00+00:00", c2))
    conn.commit()
    conn.close()
    return bid, c1, c2


def test_save_disabled_without_changes(insert_server, page: Page):
    bid, c1, c2 = _make_chain(insert_server)
    page.goto(f"{insert_server['base']}/board/{bid}/node/{c1}/insert")
    expect(page.get_by_role("button", name="保存插入")).to_be_disabled()


def test_add_change_previews_and_saves(insert_server, page: Page):
    bid, c1, c2 = _make_chain(insert_server)
    page.goto(f"{insert_server['base']}/board/{bid}/node/{c1}/insert")
    page.locator("input[placeholder='位号（自动补全）']").fill("D1")
    page.locator(".edit-form .seg label", has_text="新增").click()
    page.locator("input[placeholder='新 Part 值']").fill("1uF")
    page.get_by_role("button", name="添加这条").click()
    # 预览表里出现 D1 行，按钮可用
    expect(page.locator("table.bom code", has_text="D1")).to_be_visible()
    expect(page.get_by_role("button", name="保存插入")).to_be_enabled()
    page.get_by_role("button", name="保存插入").click()
    page.wait_for_load_state("networkidle")
    # HX-Redirect 跳到新节点详情，含 D1 与 toast
    expect(page.locator("body")).to_contain_text("D1")
    expect(page.locator(".toast")).to_contain_text("已插入")


def test_insert_conflict_shows_modal(insert_server, page: Page):
    bid, c1, c2 = _make_chain(insert_server)
    page.goto(f"{insert_server['base']}/board/{bid}/node/{c1}/insert")
    # c2 显式改过 R1；这里也改 R1 → 保存时与下游冲突
    page.locator("input[placeholder='位号（自动补全）']").fill("R1")
    page.locator("input[placeholder='新 Part 值']").fill("47k")
    page.get_by_role("button", name="添加这条").click()
    page.get_by_role("button", name="保存插入").click()
    expect(page.locator(".modal")).to_be_visible()
    expect(page.locator(".modal")).to_contain_text("请确认")


def test_insert_button_only_on_middle_nodes(insert_server, page: Page):
    bid, c1, c2 = _make_chain(insert_server)
    page.goto(f"{insert_server['base']}/board/{bid}")
    # root 与 c1 的子节点都是已提交节点 → 各有插入按钮；c2（最后一个已提交）和草稿没有
    buttons = page.locator("button", has_text="在此后插入")
    expect(buttons).to_have_count(2)
