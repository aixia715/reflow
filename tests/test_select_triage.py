"""预筛脚本 scripts/select-triage-issues.sh 的回归测试。

用一个假的 `gh`（放到 PATH 最前）喂入固定数据，验证两类输出：
  · 默认模式：候选 issue 的 JSON 数组（剔除「已自动修复」与「末评论为机器人」，按创建时间升序），
    每个候选含完整评论线程 comments 与已问轮数 question_rounds，供阶段A 读问答历史 + 守回合上限。
  · count-pending：未处理条数（已自动修复仍打开 + 末评论为机器人；均不依赖「等待回复」标签）
"""
import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select-triage-issues.sh"

# 提问评论里埋的隐藏标记：预筛据此数「已问轮数」，与脚本/prompt 内常量一致
QUESTION_MARKER = "<!-- triage:question -->"

# 假 gh：list 回固定 issue；view --json comments 回该 issue 的完整评论线程
# （{comments:[{author:{login},body},...]}）。末评论作者与已问轮数都由脚本本地解析。
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
cfg = json.load(open(os.environ["FAKE_GH_CONFIG"]))
a = sys.argv[1:]
if a[:2] == ["issue", "list"]:
    print(json.dumps(cfg["issues"]))
elif a[:2] == ["issue", "view"]:
    cms = cfg["comments"].get(a[2], [])
    print(json.dumps({"comments": [
        {"author": {"login": c["author"]}, "body": c.get("body", "")} for c in cms]}))
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


def _c(author, body=""):
    return {"author": author, "body": body}


def _run(tmp_path, issues, comments, mode=None):
    (tmp_path / "cfg.json").write_text(
        json.dumps({"issues": issues, "comments": comments})
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
    _issue(1, "2026-06-10T00:00:00Z"),                              # 普通（无评论）→ 候选
    _issue(2, "2026-06-11T00:00:00Z", labels=["已自动修复"]),        # PR 待审 → pending
    _issue(3, "2026-06-09T00:00:00Z", labels=["等待回复"]),          # 末评论机器人 → pending
    _issue(4, "2026-06-12T00:00:00Z", labels=["等待回复"]),          # 末评论人类 → 候选
    _issue(5, "2026-06-08T00:00:00Z"),                              # 无标签 + 末评论机器人 → pending
]
COMMENTS = {
    "3": [_c("github-actions[bot]", f"问？{QUESTION_MARKER}")],
    "4": [_c("github-actions[bot]", f"问？{QUESTION_MARKER}"), _c("aixia715", "1. A")],
    # 裸账号 opencode-agent（非 [bot] App）：锁定 is_bot 修复，且无「等待回复」标签也应判 pending
    "5": [_c("opencode-agent", "我已回复")],
}


def test_candidates_excludes_fixed_and_bot_last(tmp_path):
    out = _run(tmp_path, ISSUES, COMMENTS)
    nums = [c["number"] for c in json.loads(out)]
    # 仅普通(#1) 与 人类已回复(#4)；按创建时间升序 1 在前 4 在后
    assert nums == [1, 4]


def test_count_pending_includes_open_fixed_and_bot_last(tmp_path):
    out = _run(tmp_path, ISSUES, COMMENTS, mode="count-pending")
    # #2(已自动修复待审) + #3(末评论机器人) + #5(无标签但末评论裸账号机器人)
    assert out == "3"


def test_candidate_includes_full_comment_thread(tmp_path):
    """候选须带完整评论线程（author+body，按时间顺序），供阶段A 读问答历史。"""
    out = _run(tmp_path, ISSUES, COMMENTS)
    c4 = next(c for c in json.loads(out) if c["number"] == 4)
    assert c4["comments"] == [
        {"author": "github-actions[bot]", "body": f"问？{QUESTION_MARKER}"},
        {"author": "aixia715", "body": "1. A"},
    ]


def test_candidate_question_rounds_counts_marked_comments(tmp_path):
    """question_rounds = 含隐藏提问标记的评论条数；守回合上限用。"""
    issues = [_issue(8, "2026-06-13T00:00:00Z")]
    comments = {"8": [
        _c("github-actions[bot]", f"初问{QUESTION_MARKER}"),
        _c("aixia715", "1. A"),
        _c("github-actions[bot]", f"追问{QUESTION_MARKER}"),
        _c("aixia715", "2. B"),
    ]}
    c8 = json.loads(_run(tmp_path, issues, comments))[0]
    assert c8["question_rounds"] == 2


def test_candidate_no_comments_has_zero_rounds(tmp_path):
    """无评论的普通候选：comments 为空、question_rounds 为 0。"""
    c1 = next(c for c in json.loads(_run(tmp_path, ISSUES, COMMENTS)) if c["number"] == 1)
    assert c1["comments"] == []
    assert c1["question_rounds"] == 0


def test_bot_last_comment_skipped_without_waiting_label(tmp_path):
    """#17 场景回归：无任何标签，但末评论是机器人 → 不作候选、计入 pending。"""
    issues = [_issue(17, "2026-06-15T00:00:00Z")]
    comments = {"17": [_c("opencode-agent", "我已回复")]}
    assert _run(tmp_path, issues, comments) == "[]"
    assert _run(tmp_path, issues, comments, mode="count-pending") == "1"


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
