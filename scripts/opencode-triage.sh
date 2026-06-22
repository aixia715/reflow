#!/usr/bin/env bash
# 每日 issue 自动 triage 主流程（供 .github/workflows/opencode-scheduled.yml 调用）：
#   预筛候选 → 阶段A 只读评估并排序、取前 3 → 逐个处理：
#     · complex：在 issue 下评论（含每点 2~3 个备选项）并打「等待回复」标签；
#     · simple ：用模型实现修复 → pytest 门禁 → 建分支/commit/push → 开 PR → 打「已自动修复」标签。
#
# 依赖：gh（已登录，env GH_TOKEN）、jq、opencode(CLI)、pytest、git。
# 环境变量：
#   OPENCODE_MODEL    opencode 模型 id（必填）
#   OPENCODE_API_KEY  opencode 鉴权（由 opencode CLI 读取）
#   GH_TOKEN          gh / git push 鉴权
#   DRY_RUN           "true" 时只跑到阶段A 并打印计划，不执行任何写操作
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
SCRIPT_DIR="$ROOT/scripts"
PROMPT_DIR="$SCRIPT_DIR/triage-prompts"

LABEL_FIXED="已自动修复"
LABEL_WAITING="等待回复"
BASE_BRANCH="master"
DRY_RUN="${DRY_RUN:-false}"
PENDING_LIMIT=3   # 待人工处理的 issue 达到此数即本次不再继续，避免问题积压

# 早失败：确认 opencode CLI 与必需环境变量就绪
opencode --version >/dev/null 2>&1 || { echo "未找到 opencode CLI" >&2; exit 1; }
[ -n "${OPENCODE_MODEL:-}" ]   || { echo "OPENCODE_MODEL 未设置" >&2; exit 1; }
[ -n "${OPENCODE_API_KEY:-}" ] || { echo "OPENCODE_API_KEY 未设置" >&2; exit 1; }

# 用 stdin 作为 prompt 调用模型，stdout 即模型输出
run_opencode() { opencode run --model "$OPENCODE_MODEL" "$(cat)"; }

select_issues() {
  LABEL_FIXED="$LABEL_FIXED" \
    bash "$SCRIPT_DIR/select-triage-issues.sh" "$@"
}

echo "==> 检查待人工处理 issue 数量"
pending="$(select_issues count-pending)"
echo "当前待人工处理（等待回复且无人类新回复）：$pending"
if [ "$pending" -ge "$PENDING_LIMIT" ]; then
  echo "已有 $pending 条未处理 issue（≥ $PENDING_LIMIT），本次不再继续。"
  exit 0
fi

echo "==> 预筛候选 issue"
candidates="$(select_issues)"
count="$(jq 'length' <<<"$candidates")"
echo "候选数量：$count"
[ "$count" -eq 0 ] && { echo "无候选，结束。"; exit 0; }

echo "==> 阶段A：评估 + 排序 + 取前 3"
raw="$( { cat "$PROMPT_DIR/stage-a.md"; echo "$candidates"; } | run_opencode )"
# 容错：从模型输出中抓出首个 JSON 数组（容忍前导文字 / ``` 围栏 / 缩进）
plan="$(printf '%s' "$raw" | python3 -c \
  "import re,sys,json; m=re.search(r'\[.*\]', sys.stdin.read(), re.S); \
print(json.dumps(json.loads(m.group(0)), ensure_ascii=False) if m else '', end='')" \
  2>/dev/null || true)"
if ! jq -e 'type=="array"' <<<"$plan" >/dev/null 2>&1; then
  echo "阶段A 未返回合法 JSON，今日跳过。原始输出：" >&2
  printf '%s\n' "$raw" >&2
  exit 1
fi
echo "处理计划："; jq '.' <<<"$plan"

if [ "$DRY_RUN" = "true" ]; then
  echo "DRY_RUN：仅打印计划，不执行写操作。"; exit 0
fi

git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

while IFS= read -r item; do
  number="$(jq -r '.number' <<<"$item")"
  complexity="$(jq -r '.complexity' <<<"$item")"
  echo "==> 处理 #$number（$complexity）"

  if [ "$complexity" = "complex" ]; then
    body="$(jq -r '.comment_body // ""' <<<"$item")"
    if [ -z "$body" ]; then echo "  复杂但无评论草稿，跳过 #$number"; continue; fi
    gh issue comment "$number" --body "$body"
    gh issue edit "$number" --add-label "$LABEL_WAITING"
    echo "  已评论并打标签「$LABEL_WAITING」"
    continue
  fi

  # ---- simple：实现修复 ----
  # 人类此前回复过的话，先摘掉「等待回复」标签
  gh issue edit "$number" --remove-label "$LABEL_WAITING" 2>/dev/null || true

  issue_json="$(gh issue view "$number" --json title,body)"
  title="$(jq -r '.title' <<<"$issue_json")"

  # 干净起点：强制切回基线分支并清掉任何残留（含上一轮模型可能留下的改动）
  git checkout -f "$BASE_BRANCH" >/dev/null 2>&1 \
    || { echo "  无法切回 $BASE_BRANCH，跳过 #$number"; continue; }
  git reset --hard >/dev/null
  git clean -fd >/dev/null

  echo "  阶段B：实现修复"
  if ! { cat "$PROMPT_DIR/stage-b.md"; echo "#$number $title"; echo; jq -r '.body // ""' <<<"$issue_json"; } | run_opencode; then
    echo "  阶段B 运行失败，跳过 #$number"; continue
  fi

  if [ -z "$(git status --porcelain)" ]; then
    echo "  模型未产生改动，跳过 #$number"; continue
  fi

  # GitHub 禁止 App 令牌（CI 的 GITHUB_TOKEN）创建/修改 .github/workflows/ 下的文件，
  # 这类改动 push 必然被拒。提前识别并跳过+留言，交人工处理，避免拖垮整轮。
  if git status --porcelain | grep -q '\.github/workflows/'; then
    git reset --hard >/dev/null 2>&1 || true
    git clean -fd >/dev/null 2>&1 || true
    gh issue comment "$number" --body "本次自动修复改动涉及工作流文件（\`.github/workflows/\`），而 CI 的 GITHUB_TOKEN 无权推送此类改动，已跳过自动处理，需要人工修改。"
    echo "  改动涉及 .github/workflows/，无法用 GITHUB_TOKEN 推送，跳过 #$number"; continue
  fi

  echo "  跑 pytest 门禁"
  if ! pytest -q; then
    git reset --hard >/dev/null 2>&1 || true
    gh issue comment "$number" --body "自动修复尝试未通过测试，已跳过本次自动处理，需要人工介入。"
    echo "  pytest 未通过，已回滚并留言"; continue
  fi

  ts="$(date +%Y%m%d%H%M%S)"
  branch="opencode/issue${number}-${ts}"
  git checkout -b "$branch"
  git add -A
  git commit -m "fix: 自动修复 issue #${number}（${title}）

resolve #${number}"
  # push / 开 PR 失败都不让整轮崩：留言说明并继续处理下一个 issue
  if ! git push -u origin "$branch"; then
    gh issue comment "$number" --body "自动修复已在本地生成并通过测试，但推送失败（可能是权限或冲突），已跳过自动处理，需要人工介入。"
    git checkout "$BASE_BRANCH" >/dev/null 2>&1 || true
    git branch -D "$branch" >/dev/null 2>&1 || true
    echo "  推送失败，已留言并跳过 #$number"; continue
  fi
  if ! gh pr create --head "$branch" --base "$BASE_BRANCH" \
    --title "自动修复 #${number}：${title}" \
    --body "自动修复 issue #${number}。

resolve #${number}

由每日定时 opencode triage 生成，已在本地通过 pytest，请人工审阅后合并。"; then
    gh issue comment "$number" --body "自动修复已推送到分支 \`$branch\`，但开 PR 失败，需要人工手动开 PR。"
    git checkout "$BASE_BRANCH" >/dev/null 2>&1 || true
    echo "  开 PR 失败，已留言并跳过 #$number"; continue
  fi
  gh issue edit "$number" --add-label "$LABEL_FIXED"
  git checkout "$BASE_BRANCH" >/dev/null
  echo "  已开 PR 并打标签「$LABEL_FIXED」"
done < <(jq -c '.[]' <<<"$plan")

echo "全部处理完成。"
