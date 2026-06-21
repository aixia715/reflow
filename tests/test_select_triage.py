"""预筛脚本 scripts/select-triage-issues.sh 的回归测试。

用一个假的 `gh`（放到 PATH 最前）喂入固定数据，验证两类输出：
  · 默认模式：候选 issue 的 JSON 数组（剔除「已自动修复」与「等待回复+末评论为机器人」，按创建时间升序）
  · count-pending：未处理条数（已自动修复仍打开 + 等待回复且末评论为机器人）
"""
import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select-triage-issues.sh"

# 假 gh：list 回固定 issue；view 按编号回末评论作者；忽略 --json/-q 细节（契约即返回 login）
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
cfg = json.load(open(os.environ["FAKE_GH_CONFIG"]))
a = sys.argv[1:]
if a[:2] == ["issue", "list"]:
    print(json.dumps(cfg["issues"]))
elif a[:2] == ["issue", "view"]:
    print(cfg["last_authors"].get(a[2], ""))
else:
    sys.exit(0)
"""


def _issue(number, created, labels=(), title="t", body="b"):
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": n} for n in labels],
        "createdAt": created,
    }


def _run(tmp_path, issues, last_authors, mode=None):
    (tmp_path / "cfg.json").write_text(
        json.dumps({"issues": issues, "last_authors": last_authors})
    )
    gh = tmp_path / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "FAKE_GH_CONFIG": str(tmp_path / "cfg.json")}
    args = ["bash", str(SCRIPT)] + ([mode] if mode else [])
    r = subprocess.run(args, capture_output=True, text=True, env=env, check=True)
    return r.stdout.strip()


# 一份覆盖各分类的数据集
ISSUES = [
    _issue(1, "2026-06-10T00:00:00Z"),                              # 普通 → 候选
    _issue(2, "2026-06-11T00:00:00Z", labels=["已自动修复"]),        # PR 待审 → pending
    _issue(3, "2026-06-09T00:00:00Z", labels=["等待回复"]),          # 末评论机器人 → pending
    _issue(4, "2026-06-12T00:00:00Z", labels=["等待回复"]),          # 末评论人类 → 候选
    _issue(5, "2026-06-08T00:00:00Z", labels=["等待回复"]),          # 末评论机器人 → pending
]
LAST_AUTHORS = {
    "3": "github-actions[bot]",
    "4": "aixia715",
    "5": "opencode-agent[bot]",
}


def test_candidates_excludes_fixed_and_bot_waiting(tmp_path):
    out = _run(tmp_path, ISSUES, LAST_AUTHORS)
    nums = [c["number"] for c in json.loads(out)]
    # 仅普通(#1) 与 人类已回复(#4)；按创建时间升序 1 在前 4 在后
    assert nums == [1, 4]


def test_count_pending_includes_open_fixed_and_bot_waiting(tmp_path):
    out = _run(tmp_path, ISSUES, LAST_AUTHORS, mode="count-pending")
    # #2(已自动修复待审) + #3 + #5（等待回复且末评论机器人）
    assert out == "3"


def test_empty_returns_empty_array_and_zero(tmp_path):
    assert _run(tmp_path, [], {}) == "[]"
    assert _run(tmp_path, [], {}, mode="count-pending") == "0"
