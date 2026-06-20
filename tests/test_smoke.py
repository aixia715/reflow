from fastapi.testclient import TestClient


def test_smoke_core_flow(tmp_path, monkeypatch):
    """冒烟：应用装配 + 建板 + 状态图 + 审计日志全程 200。"""
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "smoke.sqlite"))
    from app.main import create_app
    client = TestClient(create_app())

    assert client.get("/").status_code == 200                       # 首页 / 建表
    r = client.post(
        "/board/new",
        data={"board_name": "Smoke", "pcb_version": "v1",
              "bom_version": "bomA", "board_uid": "1"},
        files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
        follow_redirects=False,
    )
    loc = r.headers["location"].split("?")[0]                       # /board/{id}
    assert client.get(loc).status_code == 200                      # 状态图
    assert client.get(loc + "/log").status_code == 200             # 审计日志
