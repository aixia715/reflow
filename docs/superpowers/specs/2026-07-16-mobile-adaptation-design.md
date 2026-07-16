# Reflow 移动端适配 设计文档

日期：2026-07-16
状态：已确认

## 背景与目标

Reflow 至今按「单人桌面工具」设计，`style.css` 没有任何 `@media` 断点，风格指南也明文「不做响应式」。实际使用中会在手机上打开分享链接查看，部分页面体验很差。本次目标：

- **使用场景**：查看 + 轻量编辑（编辑位号、撤销、确认冲突）。新建单板、CSV 导入、对比等重操作仍以桌面为主，不做移动专项优化，只保证不烂。
- **优先页面**：header 导航栏、节点详情页；其余页面顺带受益。

## 方案（已选定）

**纯 CSS 响应式 + 极少量模板调整**。不引入框架、不加构建步骤、不做汉堡菜单/底部抽屉等移动专用交互层。

`style.css` 末尾新增「移动端适配」分区，包含两组相互独立的规则：

1. `@media (max-width: 720px)` —— 布局适配，单断点。
2. `@media (hover: none)` —— 触屏适配，与屏宽无关（触屏平板同样受益）。

桌面样式一行不动，全部为追加的覆盖规则。颜色继续只用 CSS 变量，天然兼容夜间模式。

## 细节设计

### 1. Header（`.topnav`）

≤720px 时：

- `flex-wrap: wrap`，左右 padding 24px → 12px，行/列 gap 收紧。
- 第一行：品牌 + 主题切换按钮（用 `order` 把 `.theme-toggle` 保持在首行右端）。
- 面包屑 `.crumbs`、上下文链接 `.ctx` 自然换行，字号略缩；`.ctx` 取消 `margin-left:auto`。
- 哈希跳转框 `.hash-jump` 整行拉通（`flex:1` / `width:100%`），保留功能不隐藏。

### 2. 节点详情页（`.two-col` + BOM 表）

- ≤720px 时 `.two-col` 改 `flex-direction: column`，`aside` 全宽，保持 DOM 顺序（BOM 表在前、侧栏面板在后）。
- **唯一 JS 改动**：`node_detail.html` 的 `setFrom()` 末尾加编辑面板 `scrollIntoView({behavior:'smooth', block:'nearest'})`——小屏下点行内「修改」回填表单后表单在视口外，需滚过去；桌面上面板本就在视口内，`block:'nearest'` 不产生位移。
- 工具栏 `.toolbar`：允许换行，筛选输入框 `flex:1` 自适应（替代固定 220px）。
- 表格：`#bom`（及其他用 `table.bom` 的容器）`overflow-x:auto` 兜底；≤720px 时单元格 padding 收窄。

### 3. 触屏规则（`hover: none`）

- `.hover-only` 常显（覆盖 `visibility:hidden`）。
- 三点菜单按钮 `.menu-btn` 常显为半透明（`opacity:.45`）。
- `.icon-btn` 点按区域加大（padding 2px → 6px 左右）。

### 4. 其他顺带修复（≤720px）

- `main` padding 收窄（20px 24px → 16px 12px）。
- `.cmp-picker .input` 的 `max-width:240px` 放宽为可收缩；`.compare-bar` 允许换行。
- `.modal-overlay` 顶距 `padding-top:10vh` 减小，加左右 padding，避免弹窗贴边。
- `#toast-zone` 改为左右 12px 拉通，toast 不超宽。

### 5. 文档更新（`docs/前端风格指南.md`）

- 删除「不做响应式断点（单人桌面工具）」，改为写明上述两组 media 规则的约定：移动端规则集中在 style.css「移动端适配」分区、单断点 720px、触屏规则用 `hover: none`、只加覆盖不改桌面样式。
- 自检清单加一条：「≤720px 窄屏（DevTools 手机模拟）两套主题都实际查看过改动页面」。

## 不做的事（YAGNI）

- 不做多级断点、不做汉堡菜单/底部抽屉/移动专用组件。
- 不针对新建单板、CSV 导入、对比页做移动专项设计。
- 不引入任何 CSS 框架或构建步骤。

## 测试与验证

- 纯 CSS 无法用 pytest 覆盖；`pytest` 保持全绿（222 passed）即可确认无回归。
- 验证方式：浏览器 375px 宽度逐页目检（首页、状态图、节点详情、日志、对比、硬更改详情），白天 + 夜间两套主题都看。
- `scrollIntoView` 改动手工验证：小屏点行内「修改」后页面滚到编辑面板。
