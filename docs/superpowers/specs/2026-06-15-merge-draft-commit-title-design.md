# 设计：合并草稿节点的「标题」二次输入

> 日期：2026-06-15　范围：草稿节点右栏 UI + commit 流程的小幅调整

## 背景与问题

在 #18/#21 实现后，**工作区草稿**节点的右栏出现了语义重复：

- 「编辑节点信息」面板里有「标题（提交说明）」输入框（写 `nodes.message`）；
- 下方提交框又有「commit 说明」输入框（提交时也写 `nodes.message`）。

两者是**同一个 `message`**，用户要填两次。更糟的是：`commit_workspace` 在提交时会用提交框的值**覆盖** `message`，所以草稿上「编辑节点信息」里填的标题根本无效。长说明 `description` 同理只在「编辑节点信息」里能填，与提交动作割裂。

目标：草稿上**只填一次**标题与长说明，且都在「提交那一刻」录入，随提交自然落到新节点。

## 方案（A：提交框统一承载）

### 草稿节点（`not node.is_committed`）

- **移除**独立的「编辑节点信息」面板。
- 提交框成为标题 + 长说明的唯一入口：

  ```
  提交为新节点                       ← panel-title
  ┌───────────────────────────┐
  │ commit 说明                 │   name=message, required
  ├───────────────────────────┤
  │ 长文本说明（背景、注意事项…）  │   name=description, textarea rows=4
  │                            │
  └───────────────────────────┘
            [ 提交 ]                 ← 按钮文案「提交」
  ```

- 文案：面板标题「提交为新节点」，提交按钮「提交」（避免与标题重复）。

### 已提交节点（committed 且非根）

- 维持现有「编辑节点信息」面板（标题 + 长说明 + 「保存信息」按钮）不变。提交后想再改走这里。

### 根节点

- 仍无任何编辑入口（不变）。

### 面板显隐条件变化

「编辑节点信息」面板的渲染条件：
- 现状：`{% if node.parent_id is not none %}`（草稿也显示 → 产生重复）
- 改为：`{% if node.is_committed and node.parent_id is not none %}`（仅已提交非根节点）

## 涉及改动

| 层 | 文件 | 改动 |
|---|---|---|
| 模板 | `app/templates/node_detail.html` | ①「编辑节点信息」面板显隐条件改为「已提交且非根」；② 提交框加 `panel-title=提交为新节点`、加 `description` textarea、按钮文案改「提交」 |
| 路由 | `app/routes/board.py` | `commit` 路由新增 `description: str = Form("")`，透传给 `commit_workspace` |
| 数据层 | `app/models.py` | `commit_workspace(conn, board_id, message, description="")`：把 `description` 与 `message` 一并写入被提交的草稿行（同一条 UPDATE） |

**不改动**：`nodes.description` 列与迁移（已存在）、`update_node_info`、`edit-info` 路由（继续服务已提交节点）、`_edit_form.html` / `_bom_table.html`（#19 键盘流不受影响）。

### commit_workspace 细节

当前 `commit_workspace` 对被提交的草稿行执行：
```sql
UPDATE nodes SET is_committed=1, committed_at=?, message=? WHERE id=?
```
改为同时写 description：
```sql
UPDATE nodes SET is_committed=1, committed_at=?, message=?, description=? WHERE id=?
```
新建的空草稿行不变（description 默认 ''）。签名加默认参数 `description=""`，保证既有调用兼容。

## 测试（TDD）

1. **数据层**：`commit_workspace(..., message, description)` 后，被提交节点的 `message` 与 `description` 均正确落库；新空草稿 `description == ''`。
2. **路由**：POST `/board/{id}/commit` 带 `description` → 303；提交后该节点详情页展示长说明。不带 `description`（既有测试）仍通过。
3. **模板渲染**：草稿详情页**不含**「编辑节点信息」summary，但提交框**含** `name=description` 的 textarea；已提交非根节点详情页**含**「编辑节点信息」面板。
4. **浏览器（Playwright）**：草稿页在提交框填标题+说明 → 提交 → 新节点详情页标题更新且展示长说明；两套主题查看无异常。

## 验证

```bash
. .venv/bin/activate
pytest                         # 全绿
uvicorn app.main:app --reload
```
手动：草稿右栏只有一个提交区块（标题+说明+「提交」），无重复标题框；提交后标题/说明落到新节点；已提交节点仍可用「编辑节点信息」改；根节点无入口。白天/夜间两套主题自检。
