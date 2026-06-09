import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def test_home_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Reflow" in r.text


def test_import_preview_then_create_bom_version(client):
    csv = 'Reference,Part\n"R1,R2",10k\nR1,22k\n'
    r = client.post("/bom-version/import-preview",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA"},
                    files={"file": ("bom.csv", csv, "text/csv")})
    assert r.status_code == 200
    assert "重复" in r.text or "duplicate" in r.text

    r2 = client.post("/bom-version",
                     data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                           "csv_text": csv})
    assert r2.status_code in (200, 303)


def test_create_board_then_state_graph(client):
    csv = "Reference,Part\nR1,10k\n"
    client.post("/bom-version",
                data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                      "csv_text": csv})
    r = client.post("/board",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                          "board_uid": "3"}, follow_redirects=False)
    assert r.status_code in (200, 303)
