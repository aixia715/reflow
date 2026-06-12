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


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """启动真实 uvicorn 进程，供 Playwright 测试访问。"""
    db = tmp_path_factory.mktemp("livedb") / "test.sqlite"
    env = {**os.environ, "REFLOW_DB": str(db)}
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
