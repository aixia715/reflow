import os
import sys
import time
import subprocess
import pytest
import urllib.request
import urllib.error

# 项目根目录（tests/ 的上一级）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 18765
BASE_URL = f"http://localhost:{PORT}"


_LIVE_DB = [None]


@pytest.fixture(scope="session")
def live_db(live_server):
    """live_server 那个 sqlite 的路径。

    个别测试要直接改存储层字段——比如把两个已提交节点的 committed_at 拉开，
    好让「在此后插入」有可选的整分钟；界面上没有改节点时间的入口。
    """
    return _LIVE_DB[0]


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """启动真实 uvicorn 进程，供 Playwright 测试访问。"""
    db = tmp_path_factory.mktemp("livedb") / "test.sqlite"
    _LIVE_DB[0] = str(db)
    up = tmp_path_factory.mktemp("uploads")
    env = {**os.environ, "REFLOW_DB": str(db), "REFLOW_UPLOAD_DIR": str(up)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--port", str(PORT), "--log-level", "warning"],
        env=env,
        cwd=REPO_ROOT,
    )
    # 等待服务就绪（最多 15 秒）
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE_URL + "/", timeout=1)
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    else:
        proc.terminate()
        pytest.fail("live_server 在 15 秒内未能启动")

    yield BASE_URL

    proc.terminate()
    proc.wait(timeout=5)


_chain_uid = [0]


@pytest.fixture
def insert_chain(live_server, live_db):
    """造一条可插入的链，返回 factory() -> (board_id, parent_id)。

    parent_id 是「在此后插入」的那个节点（链上最后一个有已提交子节点的节点）。

    连续两次 commit 只差毫秒，中间没有可选的整分钟，插入页会走「此处无法插入」
    分支——那样测的就不是正常状态。界面上没有改已提交节点时间的入口，只能直接
    改库；三个已提交节点全部改成固定日期，免得依赖「今天」。
    """
    import re
    import sqlite3

    import httpx

    def _make(csv="Reference,Part\nR1,10k\nR2,22k\nC1,100nF\n"):
        _chain_uid[0] += 1
        with httpx.Client(base_url=live_server, follow_redirects=False) as c:
            r = c.post("/board/new",
                       data={"board_name": "CHAIN", "pcb_version": "v1",
                             "bom_version": "bomA", "board_uid": f"CH{_chain_uid[0]}"},
                       files={"file": ("bom.csv", csv.encode(), "text/csv")})
            bid = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])
            c.post(f"/board/{bid}/workspace/edit",
                   data={"reference": "C9", "op": "add", "part": "100nF"})
            c.post(f"/board/{bid}/commit", data={"message": "c1"})
            c.post(f"/board/{bid}/workspace/edit",
                   data={"reference": "R2", "op": "modify", "part": "33k"})
            c.post(f"/board/{bid}/commit", data={"message": "c2"})

        conn = sqlite3.connect(live_db)
        ids = [row[0] for row in conn.execute(
            "SELECT id FROM nodes WHERE board_id=? AND is_committed=1 ORDER BY id",
            (bid,))]
        stamps = ["2026-06-01T00:00:00+00:00", "2026-06-10T00:00:00+00:00",
                  "2026-06-20T00:00:00+00:00"]
        for nid, ts in zip(ids, stamps):
            conn.execute("UPDATE nodes SET committed_at=? WHERE id=?", (ts, nid))
        conn.commit()
        conn.close()

        with httpx.Client(base_url=live_server) as c:
            html = c.get(f"/board/{bid}").text
        insertable = sorted({int(m) for m in re.findall(r"/node/(\d+)/insert", html)})
        return bid, insertable[-1]

    return _make


@pytest.fixture(scope="session")
def seeded_server(live_server, tmp_path_factory):
    """在 live_server 中预建一块单板，返回 base_url。"""
    import httpx

    with httpx.Client(base_url=live_server, follow_redirects=False) as c:
        c.post("/board/new", data={
            "board_name": "TestBoard",
            "pcb_version": "v1",
            "bom_version": "bomA",
            "board_uid": "SN001",
        }, files={
            "file": ("bom.csv", b"Reference,Part\nR1,10k\nC1,100nF\n", "text/csv"),
        })
    return live_server
