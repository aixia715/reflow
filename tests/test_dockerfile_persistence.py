"""Dockerfile 持久化契约：硬更改上传图片必须和数据库存放在同一持久化卷。

issue #24：容器重部署后图片 404，根因是 REFLOW_UPLOAD_DIR 未配置，
默认落在 WORKDIR 下的相对目录（容器临时层），随旧容器销毁，
而 DB 在 /data 卷上存活、仍引用这些文件 → 取图 404。
本测试守护「上传目录与 DB 同在持久化卷」这条契约，防回归。
"""
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _docker_env() -> dict[str, str]:
    """解析 Dockerfile 中的 ENV 指令，返回 {KEY: VALUE}。"""
    env: dict[str, str] = {}
    with open(os.path.join(REPO_ROOT, "Dockerfile"), encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*ENV\s+(\w+)\s*=?\s*(.+)", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"')
    return env


def _persist_root() -> str:
    """REFLOW_DB 所在目录即持久化卷根（VOLUME 声明的目录）。"""
    db = _docker_env()["REFLOW_DB"]
    return os.path.dirname(db)


def test_upload_dir_configured_in_dockerfile():
    env = _docker_env()
    assert "REFLOW_UPLOAD_DIR" in env, \
        "Dockerfile 必须显式设置 REFLOW_UPLOAD_DIR，否则图片落在容器临时层，重部署即丢"


def test_upload_dir_under_persistent_volume():
    env = _docker_env()
    upload_dir = env["REFLOW_UPLOAD_DIR"]
    persist_root = _persist_root()
    assert upload_dir == persist_root or upload_dir.startswith(persist_root + "/"), \
        f"REFLOW_UPLOAD_DIR({upload_dir}) 必须位于持久化卷 {persist_root} 下，与 REFLOW_DB 同卷"
