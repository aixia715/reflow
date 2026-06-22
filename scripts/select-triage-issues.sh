#!/usr/bin/env bash
# 预筛：单次遍历 open issue 并分类，供定时工作流使用。
#
# 用法：
#   select-triage-issues.sh                  输出候选 issue 的 JSON 数组（供阶段A）
#   select-triage-issues.sh count-pending    输出「待人工处理」issue 的条数（供 backlog 闸门）
#
# 分类规则（gh issue list 只列 open issue，故出现的都仍处于打开状态）：
#   · 带「已自动修复」标签 —— 已开过修复 PR、仍等人类审阅/合并，算作「未处理（pending）」；不作候选。
#   · 最后一条评论作者是机器人 —— 我们（定时/按需任一流程）已回复、正在等人类答复，
#     算作「未处理（pending）」；不作候选、不占当天名额。不依赖「等待回复」标签：/oc 等
#     按需流程回复后即便没打标签也能被正确跳过。人类回复后（末评论变人类）会重新纳入候选。
#   · 其余 —— 候选。
#
# 候选输出（stdout）：JSON 数组 [{"number":N,"title":"...","body":"..."}]，按创建时间升序。
# 依赖：gh（已登录，env GH_TOKEN）、jq。
# 可选环境变量：LABEL_FIXED 覆盖标签名。
set -euo pipefail

MODE="${1:-candidates}"          # candidates | count-pending
LABEL_FIXED="${LABEL_FIXED:-已自动修复}"

# 逐条诊断日志走 stderr（stdout 是被调用方 $(...) 捕获的 JSON/数字，不能混入）。
# 只在 candidates 这一遍打印：count-pending 遍同样遍历全部 issue，若两遍都打会重复刷屏。
log_pick() { [ "$MODE" = "count-pending" ] || printf '%s\n' "$*" >&2; }

# 判断某个评论作者是否为「机器人」——其评论代表「我们已回复、在等人类」。
is_bot() {
  local login="$1"
  case "$login" in
    opencode-agent|opencode-agent'[bot]'|github-actions'[bot]'|github-actions) return 0 ;;
  esac
  # 任意以 [bot] 结尾的 GitHub App 账号
  [ "${login%\[bot\]}" != "$login" ] && return 0
  return 1
}

# 拉取全部 open issue（gh issue list 默认不含 PR）
issues_json="$(gh issue list --state open --limit 100 \
  --json number,title,body,labels,createdAt)"

candidates='[]'
pending=0
# 注意：done < <(...) 让循环在当前 shell 执行，candidates/pending 的累加得以保留
while IFS= read -r row; do
  number="$(jq -r '.number' <<<"$row")"
  labels="$(jq -r '.labels[].name' <<<"$row")"

  # 已自动修复但 issue 仍开着（PR 待审/合并）→ 未处理（pending），不作候选
  if grep -qxF "$LABEL_FIXED" <<<"$labels"; then
    pending=$((pending + 1))
    log_pick "  预筛跳过 #$number（带「$LABEL_FIXED」标签，PR 待审/合并）"
    continue
  fi

  # 末条评论作者是机器人 → 我们已回复、在等人类 → 未处理（pending），不作候选。
  # 不依赖「等待回复」标签：/oc 等按需流程回复后即便没打标签也能被正确跳过。
  last_author="$(gh issue view "$number" --json comments \
    -q '.comments[-1].author.login // ""')"
  if [ -n "$last_author" ] && is_bot "$last_author"; then
    pending=$((pending + 1))
    log_pick "  预筛跳过 #$number（末评论作者为机器人：$last_author，等待人类答复）"
    continue
  fi

  log_pick "  预筛入选 #$number（纳入候选）"
  candidates="$(jq -c --argjson c "$candidates" \
    '$c + [{number, title, body}]' <<<"$row")"
done < <(jq -c 'sort_by(.createdAt) | .[]' <<<"$issues_json")

case "$MODE" in
  count-pending) printf '%s\n' "$pending" ;;
  *)             printf '%s\n' "$candidates" ;;
esac
