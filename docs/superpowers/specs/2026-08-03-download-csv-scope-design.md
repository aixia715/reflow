# 下载 CSV 可选「完整 BOM / 本节点修改项」

对应 issue #134。节点页原本只能下载**折叠后的完整 BOM**；本次让下载入口先二选一：完整 BOM，或**本节点相对父节点的 changeset**。

需求在 issue 里已确认：修改项 = 仅本节点 changeset（不是相对初始 BOM 的累计差量）；格式为 `Reference,Part,OP` 三列，remove 行 Part 留空；下载入口处先选再下载。

## 导出格式

`app/bom_export.changes_to_csv(changes)` —— 纯逻辑，输入 `models.get_changeset` 的返回形状（`{"reference", "op", "part"}` 列表）：

- 表头固定 `Reference,Part,OP`，与 `csv_import.change_csv_template()` 完全一致
- 一条 changeset 一行，按位号自然排序（复用 `natural_sort_key`），不合并相同 Part
- OP 取 `add` / `modify` / `remove`，与 `csv_import._VALID_OPS` 同源
- `remove` 行 Part 留空（该 op 的 part 本就不落库）
- 含逗号/引号/换行的字段由 `csv` 模块转义

这套格式使**导出的修改项 CSV 能被「从 CSV 导入修改」原样读回**（`parse_change_csv` → `plan_changes`），即在别的节点/单板上重放同一批修改。往返闭环有测试守着。

## 路由

`GET /board/{board_id}/node/{node_id}/download` 增加查询参数 `scope`：

| scope | 行为 |
|---|---|
| 缺省 / `full` | 现状：折叠后的完整 BOM（`Reference,Part`） |
| `changes` | 本节点 changeset（`Reference,Part,OP`） |
| 其他值 | 400「scope 只能是 full 或 changes」 |

缺省即 `full`，已存在的链接与书签行为不变。

根节点（`parent_id is NULL`）的 changeset 恒为空——它的内容在 `initial_bom` 里，没有「相对父节点的修改」——故 `scope=changes` 对根节点返回 400，界面上也不给这个入口，不引导用户点一个必然为空的东西。

文件名沿用 `_download_filename`，`scope=changes` 时在末尾追加 `_修改项`；净化规则与 `.csv` 后缀不变。

## 前端

`node_detail.html` 的 `ctxlinks`：原来的单个「⬇ 下载 CSV」链接变成可展开项，点开露出两条子项——`完整 BOM`、`本节点修改项`（根节点不渲染第二条）。

`ctxlinks` 的内容渲染在 header ⋯ 菜单（`.menu-pop.topnav-actions`）里，而该菜单挂了 `@click="nav=false"`：任何点击都会关掉整个菜单。因此：

- 展开容器加 `@click.stop` 拦住冒泡，否则点父项时菜单先关了，子项根本没机会露出来
- `@click.stop` 使 `@click.outside` 失效（风格指南「弹出菜单的关闭机制」），改用 `@close-menus.window` 判断点击是否落在自身之外；再加 `@keydown.escape.window` 支持 ESC 关闭
- 两条子链接点击后自行置 `nav=false`，让整个菜单随下载开始一起收起

样式只加一个 `.menu-sub`，负责子项缩进与字号；不引入新颜色变量，两套主题自然一致。

## 测试

纯逻辑（`tests/test_bom_export.py`）：表头、自然排序、remove 行 Part 为空、含逗号/引号字段的转义、导出经 `parse_change_csv` 往返。

路由与页面（`tests/test_download_routes.py`）：`scope=changes` 只含本节点 changeset 且三类 op 都正确；无 `scope` 仍是完整 BOM（回归）；根节点 `scope=changes` 返回 400；非法 scope 返回 400；修改项下载的文件名含「修改项」；节点页两条链接都在、根节点页只有完整 BOM 一条。
