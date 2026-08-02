#!/usr/bin/env bash
# PostToolUse hook：创建 PR 之后，提醒 Claude 立刻订阅这个 PR 的评审/CI 事件。
#
# hook 是 shell 命令，调不动 MCP 工具，所以这里只负责「确定性地触发提醒」——
# 把提示经 hookSpecificOutput.additionalContext 回灌给模型，真正的
# subscribe_pr_activity 调用仍由模型发出。
#
# stdin 是 hook 的输入 JSON。**只从 tool_response 里捞 PR 号**：stdin 里还有
# tool_input，PR 正文里写一句「follow-up of …/pull/140」就会让全文匹配抓到旧
# 号，进而订阅错 PR。捞不到就不编号，让模型自己先确认。
set -uo pipefail

num=$(jq -r '.tool_response | tostring' | grep -oE '/pull/[0-9]+' | head -1 |
      grep -oE '[0-9]+' || true)

jq -n --arg n "${num:-}" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: (
      (if $n == ""
       then "刚调用了创建 PR 的工具，但没能从响应里取到 PR 编号——先确认 PR 是否建成、编号是多少。"
       else "PR #" + $n + " 已创建。"
       end)
      + "本仓库约定：开完 PR 立刻调用 subscribe_pr_activity 订阅该 PR 的评审与 CI 事件（.github/workflows/ci.yml 在 PR opened 时会跑 opencode 评审），之后按订阅协议跟进评审意见和 CI 直到绿灯。工具没加载就先 ToolSearch \"select:subscribe_pr_activity\"。")
  }
}'
