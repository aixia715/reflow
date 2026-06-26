import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("REFLOW_UPLOAD_DIR", str(tmp_path / "uploads"))
    from app.main import create_app
    return TestClient(create_app())


def _new_board(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "SN1"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]   # board_id


def test_hard_change_new_form_loads(client):
    bid = _new_board(client)
    r = client.get(f"/board/{bid}/hard-change/new")
    assert r.status_code == 200
    assert "记录硬更改" in r.text or "硬更改" in r.text


def test_create_hard_change_redirects_and_persists(client):
    bid = _new_board(client)
    r = client.post(f"/board/{bid}/hard-change",
                    data={"title": "飞线 A", "occurred_at": "2026-06-01T10:30",
                          "description": "把 R1 飞到 R9"},
                    files=[("files", ("p.png", b"\x89PNG\r\n", "image/png"))],
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/board/{bid}")
    rg = client.get(f"/board/{bid}")
    assert "飞线 A" in rg.text


def test_create_hard_change_rejects_empty_title(client):
    bid = _new_board(client)
    r = client.post(f"/board/{bid}/hard-change",
                    data={"title": "  ", "occurred_at": "2026-06-01T10:30",
                          "description": ""},
                    follow_redirects=False)
    assert r.status_code == 200
    assert "标题不能为空" in r.text


def test_hard_change_detail_and_delete(client):
    bid = _new_board(client)
    client.post(f"/board/{bid}/hard-change",
                data={"title": "割线 X", "occurred_at": "2026-06-02T09:00",
                      "description": "割断 net5"},
                follow_redirects=False)
    rg = client.get(f"/board/{bid}")
    assert "割线 X" in rg.text
    import re
    m = re.search(rf"/board/{bid}/hard-change/(\d+)", rg.text)
    assert m, "状态图未渲染硬更改详情链接"
    hid = m.group(1)
    rd = client.get(f"/board/{bid}/hard-change/{hid}")
    assert rd.status_code == 200 and "割线 X" in rd.text
    rdel = client.post(f"/board/{bid}/hard-change/{hid}/delete")
    assert rdel.status_code == 200 and rdel.headers.get("HX-Redirect", "").startswith(f"/board/{bid}")
    rg2 = client.get(f"/board/{bid}")
    assert "割线 X" not in rg2.text


def _img_id_from_edit_form(client, bid, hid):
    import re
    r = client.get(f"/board/{bid}/hard-change/{hid}/edit")
    m = re.search(r'name="delete_image_ids" value="(\d+)"', r.text)
    assert m, "编辑表单未渲染图片复选框"
    return m.group(1)


def test_create_hard_change_stores_utc(client):
    # 直接 POST canonical UTC（模拟前端已转换），断言存库即为该值
    bid = _new_board(client)
    client.post(f"/board/{bid}/hard-change",
                data={"title": "飞线", "occurred_at": "2026-06-13T01:10:00+00:00",
                      "description": "x"})
    from app.main import get_conn
    from app import models
    hcs = models.list_hard_changes(get_conn(), int(bid))
    assert hcs[-1]["occurred_at"] == "2026-06-13T01:10:00+00:00"


def test_edit_ignores_foreign_image_ids(client):
    bid = _new_board(client)
    # 建两条带图的硬更改 A、B
    for title in ("HCA", "HCB"):
        client.post(f"/board/{bid}/hard-change",
                    data={"title": title, "occurred_at": "2026-06-01T10:30", "description": ""},
                    files=[("files", (f"{title}.png", b"\x89PNG\r\n", "image/png"))],
                    follow_redirects=False)
    rg = client.get(f"/board/{bid}")
    import re
    hids = re.findall(rf"/board/{bid}/hard-change/(\d+)", rg.text)
    hids = sorted(set(hids))
    assert len(hids) >= 2
    hid_a, hid_b = hids[0], hids[1]
    img_b = _img_id_from_edit_form(client, bid, hid_b)
    # 编辑 A，却传入 B 的图片 id —— 不应删除 B 的图
    client.post(f"/board/{bid}/hard-change/{hid_a}/edit",
                data={"title": "HCA", "occurred_at": "2026-06-01T10:30",
                      "description": "", "delete_image_ids": img_b},
                follow_redirects=False)
    # B 的图仍在
    img_b_after = _img_id_from_edit_form(client, bid, hid_b)
    assert img_b_after == img_b


def test_create_error_rerender_keeps_specified_time(client):
    """新建校验失败重渲染：若用户已指定时间（occurred_at 非空），'指定时间'
    应保持勾选、时间不丢（issue #74 问题一）。"""
    bid = _new_board(client)
    # title 为空触发校验失败，但带了 occurred_at
    r = client.post(f"/board/{bid}/hard-change",
                    data={"title": "", "occurred_at": "2026-06-01T02:30:00+00:00",
                          "description": "x"})
    assert r.status_code == 200          # 错误就地重渲染
    assert "specify: true" in r.text     # 勾选状态保留 → 时间不会被 sync() 清空


def test_create_error_rerender_no_time_stays_unchecked(client):
    """新建校验失败重渲染：未指定时间时，'指定时间'保持未勾选。"""
    bid = _new_board(client)
    r = client.post(f"/board/{bid}/hard-change",
                    data={"title": "", "occurred_at": "", "description": "x"})
    assert r.status_code == 200
    assert "specify: false" in r.text


def test_create_hard_change_empty_occurred_at_uses_canonical_utc(client):
    """POST 硬更改时若 occurred_at 为空，服务端兜底应使用 canonical UTC (+00:00)。"""
    bid = _new_board(client)
    r = client.post(f"/board/{bid}/hard-change",
                    data={"title": "服端兜底时间", "occurred_at": "",
                          "description": "empty occurred_at"},
                    follow_redirects=False)
    assert r.status_code == 303
    from app.main import get_conn
    from app import models
    import re
    hcs = models.list_hard_changes(get_conn(), int(bid))
    assert len(hcs) > 0
    stored = hcs[-1]["occurred_at"]
    # 应为 canonical UTC 格式：YYYY-MM-DDTHH:MM:SS+00:00
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$", stored), \
        f"expected canonical UTC format, got: {stored}"
