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
