"""预筛脚本 scripts/select-triage-issues.sh 的回归测试。

用一个假的 `gh`（放到 PATH 最前）喂入固定数据，验证两类输出：
  · 默认模式：候选 issue 的 JSON 数组（剔除「已自动修复」与「末评论为机器人」，按创建时间升序）
  · count-pending：未处理条数（已自动修复仍打开 + 末评论为机器人；均不依赖「等待回复」标签）
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
    _issue(1, "2026-06-10T00:00:00Z"),                              # 普通（无末评论）→ 候选
    _issue(2, "2026-06-11T00:00:00Z", labels=["已自动修复"]),        # PR 待审 → pending
    _issue(3, "2026-06-09T00:00:00Z", labels=["等待回复"]),          # 末评论机器人 → pending
    _issue(4, "2026-06-12T00:00:00Z", labels=["等待回复"]),          # 末评论人类 → 候选
    _issue(5, "2026-06-08T00:00:00Z"),                              # 无标签 + 末评论机器人（#17 场景）→ pending
]
LAST_AUTHORS = {
    "3": "github-actions[bot]",
    "4": "aixia715",
    # 裸账号 opencode-agent（非 [bot] App）：锁定 is_bot 修复，且无「等待回复」标签也应判 pending
    "5": "opencode-agent",
}


def test_candidates_excludes_fixed_and_bot_last(tmp_path):
    out = _run(tmp_path, ISSUES, LAST_AUTHORS)
    nums = [c["number"] for c in json.loads(out)]
    # 仅普通(#1) 与 人类已回复(#4)；按创建时间升序 1 在前 4 在后
    assert nums == [1, 4]


def test_count_pending_includes_open_fixed_and_bot_last(tmp_path):
    out = _run(tmp_path, ISSUES, LAST_AUTHORS, mode="count-pending")
    # #2(已自动修复待审) + #3(末评论机器人) + #5(无标签但末评论裸账号机器人)
    assert out == "3"


def test_bot_last_comment_skipped_without_waiting_label(tmp_path):
    """#17 场景回归：无任何标签，但末评论是机器人 → 不作候选、计入 pending。"""
    issues = [_issue(17, "2026-06-15T00:00:00Z")]
    authors = {"17": "opencode-agent"}
    assert _run(tmp_path, issues, authors) == "[]"
    assert _run(tmp_path, issues, authors, mode="count-pending") == "1"


def test_empty_returns_empty_array_and_zero(tmp_path):
    assert _run(tmp_path, [], {}) == "[]"
    assert _run(tmp_path, [], {}, mode="count-pending") == "0"


def test_ai_ignore_label_excluded_from_candidates_and_pending(tmp_path):
    """带「AI忽略」标签的 issue 被定时流程完全跳过：既不作候选，也不计入 pending。"""
    issues = [
        _issue(1, "2026-06-10T00:00:00Z"),                       # 普通 → 候选
        _issue(7, "2026-06-11T00:00:00Z", labels=["AI忽略"]),     # 人工标记忽略 → 完全跳过
    ]
    assert [c["number"] for c in json.loads(_run(tmp_path, issues, {}))] == [1]
    assert _run(tmp_path, issues, {}, mode="count-pending") == "0"
