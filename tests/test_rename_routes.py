import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _setup(client, name="B", pcb="v1", bom="bomA", uid="SN1"):
    r = client.post("/board/new",
                    data={"board_name": name, "pcb_version": pcb,
                          "bom_version": bom, "board_uid": uid},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]  # board_id


def test_rename_board_group_success_redirects(client):
    _setup(client, "Old")
    r = client.post("/board-group/rename",
                    data={"board_name": "Old", "new_name": "New"})
    assert r.headers.get("HX-Redirect") == "/"
    assert "New" in client.get("/").text
    assert "Old" not in client.get("/").text


def test_rename_board_group_conflict_returns_toast(client):
    _setup(client, "A")
    _setup(client, "B")
    r = client.post("/board-group/rename",
                    data={"board_name": "A", "new_name": "B"})
    assert r.status_code == 200
    assert "HX-Redirect" not in r.headers
    trig = json.loads(r.headers["HX-Trigger"])
    assert "已存在" in trig["showToast"]


def test_rename_empty_name_returns_toast(client):
    _setup(client, "A")
    r = client.post("/board-group/rename",
                    data={"board_name": "A", "new_name": "   "})
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert "不能为空" in trig["showToast"]


def test_rename_pcb_version_success(client):
    _setup(client, "MB", "p1", "bomA", "SN1")
    r = client.post("/pcb-version/rename",
                    data={"board_name": "MB", "pcb_version": "p1", "new_name": "p2"})
    assert r.headers.get("HX-Redirect") == "/"
    assert "PCB p2" in client.get("/").text


def test_rename_bom_version_success(client):
    _setup(client, "MB", "v1", "b1", "SN1")
    r = client.post("/bom-version/rename",
                    data={"board_name": "MB", "pcb_version": "v1",
                          "bom_version": "b1", "new_name": "b2"})
    assert r.headers.get("HX-Redirect") == "/"
    assert "b2" in client.get("/").text


def test_rename_board_uid_success(client):
    bid = _setup(client, "MB", "v1", "bomA", "SN1")
    r = client.post(f"/board/{bid}/rename", data={"new_name": "SN9"})
    assert r.headers.get("HX-Redirect") == "/"
    assert "SN9" in client.get("/").text


def test_rename_board_uid_conflict_returns_toast(client):
    bid = _setup(client, "MB", "v1", "bomA", "SN1")
    _setup(client, "MB", "v1", "bomA", "SN2")
    r = client.post(f"/board/{bid}/rename", data={"new_name": "SN2"})
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert "已存在" in trig["showToast"]
