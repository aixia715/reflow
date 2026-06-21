# opencode issue 自动化

仓库有两类 opencode 自动化：

## 1. 事件驱动（已有）

- `.github/workflows/opencode.yml`：issue 标题/正文带 `/opencode` 或 `/oc` → 开分支实现并开 PR；
  评论里 `/opencode`、`/oc` → 执行评论指令。
- `.github/workflows/ci.yml` 的 `review` job：PR 通过测试+冒烟后自动评审。

## 2. 每日定时扫描（`opencode-scheduled.yml`）

每天 **北京时间 02:00**（cron `0 18 * * *` UTC）自动扫描 open issue，最多处理 3 个：

0. **Backlog 闸门**：若当前「待人工处理」的 issue 已达 **3 条**，本次直接结束、不再处理新 issue，避免积压等人类清理。「待人工处理」= 带 `等待回复` 且最后一条评论是机器人（等人类答复）**或** 带 `已自动修复` 且 issue 仍打开（PR 等人类审阅/合并）。
1. **预筛**（`scripts/select-triage-issues.sh`）：列出 open issue，剔除
   - 带 `已自动修复` 标签的（已开过修复 PR）；
   - 带 `等待回复` 标签且最后一条评论作者是机器人的（在等人类答复，**不占名额**；人类回复后会重新纳入）。
2. **阶段A**（只读模型评估，`scripts/triage-prompts/stage-a.md`）：通读全部候选，**先按技术依赖关系排序（前置项在前），再在此前提下按从简单到复杂排序**，取前 3，逐个判定 `simple` / `complex`，并为 `complex` 起草中文评论（每个待定点给 2~3 个备选项）。输出严格 JSON。
3. **执行**（`scripts/opencode-triage.sh`）：
   - `complex` → 在 issue 下发表评论 + 打 `等待回复` 标签；
   - `simple` → 模型实现修复（阶段B，`stage-b.md`）→ `pytest` 必须通过 → 建 `opencode/issue<N>-<时间戳>` 分支 + commit + push → 开 PR（`resolve #N`）→ 打 `已自动修复` 标签。

手动测试：Actions → `opencode-scheduled` → Run workflow，勾选 `dry_run` 可只跑到阶段A 并打印计划，不做任何写操作。

### 标签（工作流首次运行会自动创建）

| 标签 | 含义 | 解除 |
|---|---|---|
| `等待回复` | opencode 已就该 issue 提问，等待人类答复 | 人类在 issue 下回复后自动重新纳入；下次自动修复时也会摘除 |
| `已自动修复` | opencode 已为该 issue 开出修复 PR | PR 合并关闭 issue 即离开范围；PR 被拒时手动移除标签可重触发 |

### 需要的 Secrets / Variables

复用事件驱动工作流已有的配置，**无需新增**：

| 类型 | 名称 | 用途 |
|---|---|---|
| Secret | `OPENCODE_API_KEY` | opencode CLI / action 鉴权 |
| Variable | `OPENCODE_MODEL` | 使用的模型 id |

`GITHUB_TOKEN` 由 Actions 自动提供（工作流已声明 `contents/issues/pull-requests: write`）。

### 已知权衡

- 自动修复 PR 由 `GITHUB_TOKEN` 创建，**不会触发** `ci.yml`（GitHub 防工作流嵌套）——与现状「bot 开的 PR 本就跳过评审」一致；阶段B 已在开 PR 前本地跑过 `pytest`。
  若希望自动修复 PR 也跑测试+评审，可改用 PAT（如 `secrets.GH_PAT`）创建 PR。
- 阶段A 要求模型输出严格 JSON；解析失败则当天跳过（不做任何写操作）。
