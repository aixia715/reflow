# Reflow — 单板 BOM 状态管理工具 · 设计文档（Spec）

> 由需求文档 `docs/Reflow-需求文档.md` 经 brainstorming 提炼而来。本文档是实现的依据。
>
> 工具名「Reflow」一语双关：硬件的回流焊（reflow soldering），也描述核心机制——编辑上游节点后，改动向后「重新流动」传播。

## 1. 目标与范围

硬件调试中，记录每块单板每次测试时的 BOM 状态，并像 git 一样对 BOM 演进做**线性版本管理**（无分支、无合并）。每个状态有稳定可分享的 URL，供外部笔记软件引用，保证「每次测试用的是哪个 BOM 状态」始终可追溯。

**单人使用**，不考虑多用户与并发。

## 2. 关键决策

本设计在需求文档基础上锁定了五个实现决策：

| 决策点 | 选择 | 理由 |
|---|---|---|
| 技术栈 | **Python + FastAPI + HTMX** | 服务端渲染 + HTMX 局部交互，几乎不写手写 JS；逻辑重、UI 简单的单人工具的理想选择 |
| 标识符 & URL | **DB 用 surrogate key，URL 用 `/board/{boardId}/node/{nodeId}`** | 名称仅作展示，重命名永不破坏分享链接，满足「URL 稳定」要求 |
| 工作区 | **持久化的未提交草稿节点** | 复用节点/changeset 模型，重启不丢调试中的改动 |
| CSV 导入 | **导入前校验报告** | 初始 BOM 是所有状态的地基，宁可在入口拦住脏数据 |
| 完整 BOM 计算 | **读取时实时折叠（不物化缓存）** | 单板节点量极小，毫秒级算完；无缓存一致性问题，是「差量自动传播」成立的根本 |

## 3. 核心概念与版本模型

### 3.1 数据层级

单板通过四级定位：`单板名称` → `PCB版本` → `BOM版本` → `单板ID`。

- **初始 BOM 绑定在 `BOM版本` 层**：同一 BOM版本 下所有单板共享同一份初始 BOM。
- **调试历史（状态图）绑定在 `单板ID` 层**：每块实物板从共同初始 BOM 出发，各自独立演进。
- `单板名称` 与 `PCB版本` **不是独立实体表**，只是层级行上的文本字段（见 6.1 隐式创建）。

### 3.2 BOM 条目

只有两个有效字段：

- `Reference`（位号，如 `R1`、`C86_PD1`）：唯一标识一个元件位置，按字符串原样处理，不解析下划线/后缀。
- `Part`（器件型号，如 `1kR`、`100nF`）。

**不贴 (DNP)** 的表示：某位号「不贴」就是它**不出现在该状态的 BOM 中**。无单独贴装标记字段——「无此位号」即「不贴」。

### 3.3 差量存储与解析值

- 每块单板从**初始 BOM（根节点）** 开始。每个**节点 = 一次 commit = 一个 BOM 状态**。
- 节点**只存相对父节点的修改集（changeset）**，不存整份快照。
- 修改集对单个位号有三种 op：**add**（新增原本不存在的位号）、**modify**（改 Part）、**remove**（去掉位号 = 不贴）。
- 结构线性，无分支、无合并。

**某位号在某节点的「解析值」**（折叠算法的核心定义）：沿父链从根往下，该位号的值 = **离它最近的、显式操作过该位号的祖先节点（含自身）所决定的值**：

- 最近一次显式 op 是 `add` / `modify` → 解析为那个 `part`
- 最近一次显式 op 是 `remove` → 解析为「不贴」（BOM 中无此位号）
- 一路无任何节点显式操作过 → 继承初始 BOM 的值；初始也没有 → 不贴

「显式操作过」= 该节点的 `node_changes` 里有这条 `reference`。**这是冲突判定的唯一依据。**

**任意节点的完整 BOM** = 初始 BOM + 沿父链按上述规则折叠所有 `node_changes`，**读取时实时计算**。

## 4. 数据模型（SQLite）

```
boards_hierarchy           -- 四级层级，扁平一张表
  id              PK (surrogate)
  board_name      TEXT     -- 单板名称
  pcb_version     TEXT     -- PCB版本
  bom_version     TEXT     -- BOM版本
  board_uid       TEXT     -- 单板ID（用户写在实物板上的标签）

initial_bom                -- 绑定在 BOM版本 层
  id              PK
  board_name, pcb_version, bom_version   -- 定位到 BOM版本
  reference       TEXT
  part            TEXT
  UNIQUE(board_name, pcb_version, bom_version, reference)

nodes                      -- 节点/commit，含未提交草稿
  id              PK (surrogate, 进 URL)
  board_id        FK -> boards_hierarchy.id   -- 绑定到单板ID层
  parent_id       FK -> nodes.id  (nullable = 根)
  message         TEXT     -- 说明文字
  created_at      TIMESTAMP
  is_committed    BOOLEAN  -- false = 工作区草稿节点
  committed_at    TIMESTAMP (nullable)

node_changes               -- 节点的修改集
  id              PK
  node_id         FK -> nodes.id
  reference       TEXT
  op              TEXT     -- 'add' | 'modify' | 'remove'
  part            TEXT     -- remove 时为 NULL
  UNIQUE(node_id, reference)   -- 一个节点对一个位号最多一条

edit_log                   -- append-only 审计日志，永不更新/删除
  id              PK
  node_id         FK
  reference       TEXT
  old_part        TEXT
  new_part        TEXT
  op              TEXT     -- add / modify / remove
  source          TEXT     -- 'direct' | 'propagated'
  created_at      TIMESTAMP
  note            TEXT (nullable)
```

要点：

- **根节点**：每个单板ID新建时，自动以所属 BOM版本 的 `initial_bom` 为起点。根节点 `parent_id=NULL`，`node_changes` 为空（初始 BOM 单独存 `initial_bom`，不进 changeset，避免重复）。
- **工作区**：一个 `is_committed=false` 的草稿节点挂在最新已提交节点之后。commit 时把它翻成 `is_committed=true`、写入 `committed_at`，并新开一个空草稿。
- **`UNIQUE(node_id, reference)`**：保证「一个节点对一个位号只解析为一个值」，是冲突检测的判定依据。

## 5. 传播 & 冲突算法（工具核心）

因采用「读取时实时折叠」，传播分两类，处理完全不同。

### 5.1 编辑历史节点 Sₖ 的某位号 R

用户在节点 `Sₖ` 把位号 `R` 修正为某值（写入/更新/删除 `Sₖ.node_changes` 里 R 这一条）：

1. **`Sₖ` 自身**：直接落库，记一条 `edit_log(source=direct)`。
2. **沿子链往后，找下游第一个「显式操作过 R」的节点 `Sⱼ`**（`node_changes` 里有 R 的第一个 j>k）：
   - **没找到** → 下游全是「继承」R。实时折叠时它们自动解析为 `Sₖ` 的新值，**零额外操作、零冲突**。这是差量「自动传播」的本质。
   - **找到 `Sⱼ`** → `Sⱼ` 及其之后解析为 `Sⱼ` 自己的显式值，**与本次修正冲突**，进入冲突确认。

> 链是线性的，「下游第一个显式节点」最多只有一个，冲突判定干净，不存在多分支合并。

### 5.2 冲突确认（每个冲突位号二选一）

对每个挡住传播的 `Sⱼ` 的位号 `R`：

- **保留下游值**：什么都不动。`Sⱼ` 的显式 op 还在；`Sₖ` 改 `Sₖ`，`Sⱼ` 及之后保持原值。
- **采用修正值并向后传播**：**删除 `Sⱼ.node_changes` 里 R 这一条**。删后 `Sⱼ` 及之后变回「继承」，折叠时自动解析为 `Sₖ` 的新值；给 `Sⱼ` 记一条 `edit_log(source=propagated)`。

### 5.3 三种 op 一视同仁

`add`/`modify`/`remove` 在判定上无差别——唯一标准是「下游节点 changeset 里有没有这条 reference」，与 op 类型无关。

### 5.4 编辑根节点

根节点的初始 BOM 存在 `initial_bom` 而非 `node_changes`。允许编辑根节点（修正初始 BOM）：改的是 `initial_bom` 行，传播/冲突逻辑同上。数据访问层统一抽象「取某节点对某位号的显式值」，让根节点与普通节点走同一套代码。

### 5.5 对照需求文档 4.4 的例子（验收用例）

初始 `R1=10k`，链 `初始 → S1 → S2 → S3`，`S2.node_changes` 有 `R1=47k(modify)`，S1/S3 没碰 R1。

用户在 `S1` 写入 `R1=22k`：

1. `S1` 落库 `R1=22k`，记 `edit_log(direct)`。
2. 往下游找第一个显式操作 R1 的节点 → 找到 `S2`。冲突，弹确认。
3. 用户选项：
   - **保留下游 47k** → 不动 S2。折叠结果：S1=22k，S2=47k，S3 继承 S2=47k。
   - **采用修正值** → 删除 `S2` 的 R1 这条。折叠结果：S1/S2/S3 全部继承 S1=22k；给 S2 记 `edit_log(propagated)`。

## 6. 页面、路由与交互

服务端渲染 HTML（Jinja2），局部交互用 HTMX 片段替换，几乎不写手写 JS。

### 6.1 路由表

| 方法 & 路径 | 用途 |
|---|---|
| `GET /` | 层级浏览：单板名称 → PCB版本 → BOM版本 → 单板ID 级联选择 |
| `GET /bom-version/new` · `POST /bom-version` | 新建 BOM版本：上传 CSV（含隐式创建单板名称/PCB版本） |
| `POST /bom-version/import-preview` | CSV 校验报告（HTMX 局部返回） |
| `POST /board` | 在某 BOM版本 下新建单板ID，自动建根节点 + 空工作区草稿 |
| `GET /board/{boardId}` | 状态图页面（git graph 风格，针对单个单板ID） |
| `GET /board/{boardId}/node/{nodeId}` | 节点详情/编辑页（**稳定 URL**，核心分享链接） |
| `POST /board/{boardId}/node/{nodeId}/edit` | 编辑某位号（增/改/删），触发传播+冲突检测 |
| `POST /board/{boardId}/node/{nodeId}/resolve` | 提交冲突二选一结果 |
| `GET /board/{boardId}/workspace` | 工作区视图（未提交草稿节点） |
| `POST /board/{boardId}/workspace/edit` | 在工作区增/改/删位号 |
| `POST /board/{boardId}/commit` | 把工作区草稿提交成正式节点 |
| `GET /board/{boardId}/log` | 审计日志查询页 |

稳定链接 = `/board/{boardId}/node/{nodeId}`，`nodeId` 是 surrogate key，编辑内容不改 URL。

### 6.2 各页面要点

**① 层级导航 / 新建（`GET /`）**
级联选择四级。

- **新建 BOM版本（隐式创建上层）**：`GET /bom-version/new` 一个表单完成三级——
  - 单板名称：下拉选已有，或「+ 新建」切换输入框
  - PCB版本：下拉选已有（随所选单板名称联动），或「+ 新建」
  - BOM版本：输入新名称
  - 上传 CSV → 校验报告 → 确认入库；提交时校验 `(单板名称, PCB版本, BOM版本)` 三元组不重复。
  - **无单独的「新建单板名称 / 新建 PCB版本」入口。**
- **新建单板ID**：在已存在的 BOM版本 下一键创建，自动继承初始 BOM 为根节点 + 一个空工作区草稿。

**② CSV 导入（两步：预览 → 确认）**
上传后先 `import-preview`：解析（UTF-8 BOM、CRLF、带引号含逗号字段、位号首尾空格、拆分逗号合并位号），再**校验报告**列出问题条目（重复位号、空 Part、空位号）。预览表展示拆分结果（`"R67,R24"|1kR` → R67=1kR, R24=1kR）。无问题或用户确认后 `POST /bom-version` 正式入库。

**③ 状态图页面（`GET /board/{boardId}`）**
线性节点序列（无分支），每节点显示说明文字+时间戳，末端是工作区草稿（虚线等视觉区分）。点击节点 → 详情页。纯 HTML/CSS 画竖直 git-graph（圆点+连线），不引入图形库。

**④ 节点详情 / 编辑页（`GET .../node/{nodeId}`）**

- 顶部：完整 BOM 表（Reference + Part），实时折叠算出。
- **diff 高亮**：相对父节点，标注本节点 changeset 改动的位号——新增/修改/删除三种样式。
- 行内可编辑：改 Part、新增位号、删除位号。每次编辑 `POST .../edit` → 跑第 5 节算法 →
  - 无冲突：HTMX 局部刷新 BOM 表。
  - 有冲突：返回冲突确认片段。
- 文案体现「这是修正记录，不是新改动」（如编辑区标题「修正此节点记录」）。

**⑤ 冲突确认界面（`.../resolve`）**
列出所有冲突位号，每行：位号、下游当前值 vs 本次修正值，二选一单选（保留下游值 / 采用修正值）。一次性提交所有选择 → 按 5.2 落库 → 刷新。

**⑥ 工作区 & commit**
工作区视图复用节点详情渲染（它本质就是草稿节点），同样有 diff 高亮（相对最新已提交节点）。增/改/删累积进草稿。commit 弹说明文字 → 草稿翻正 + 新开空草稿。

**⑦ 审计日志页（`GET .../log`）**
按单板/节点过滤，表格展示：节点、位号、旧值→新值、op、来源(direct/propagated)、时间戳、备注。只读，append-only。

## 7. CSV 导入规则

用于建立某 BOM版本 的初始 BOM（根节点）。示例列：`Item, Quantity, Reference, Part, PCB Footprint, Assembly Type, Linter Info`。

- **只取 `Reference` 和 `Part` 两列**，其余忽略。
- `Reference` 列常把多个位号合并在一格、用逗号分隔并加引号（如 `"R30,R25,R31,R26"`），共用同一 `Part`。导入时**拆成一位号一条**。
- 健壮性：UTF-8 BOM 头、CRLF 行尾、含逗号的带引号字段、位号首尾空格。
- 位号带下划线/后缀（如 `C86_PD1`）按字符串原样处理，不解析。
- **校验报告**（导入前）列出：同一位号出现多次（无论 Part 是否相同）、Part 为空、位号为空。用户确认/修正后再入库。

## 8. 审计日志（append-only）

- **每次编辑追加一行，永不覆盖**（一个节点改 10 次 = 10 条记录）。
- 每行：节点、位号、`Part` 旧值→新值（含 add/remove 标记）、时间戳、**来源**（direct 直接编辑 / propagated 上游传播而来）、可选原因备注。
- 「来源」解释「为什么这个节点在某次测试后变了」。
- **暂不做「按时间回看」界面**；数据结构保证将来可据此回放任意时间点状态。

## 9. 稳定链接

- 每个节点有稳定可分享 URL：`/board/{boardId}/node/{nodeId}`。
- 外部笔记粘贴该链接，点开看到对应状态完整 BOM。
- URL 在节点生命周期内不变（编辑内容不改 URL）。

## 10. 测试策略（TDD）

核心逻辑与 Web 层分层测试，重点压在纯逻辑上。

1. **折叠引擎单测（纯函数，最重）**：给定初始 BOM + 节点链 + changeset，验证任意节点解析值。覆盖继承、add/modify/remove、remove=不贴、初始缺失位号。
2. **传播 & 冲突单测（核心算法）**：把 5.5（即需求 4.4）例子写成测试（保留下游 / 采用修正值两分支各一断言），再补：无下游显式节点=零冲突自动传播、多冲突位号、编辑根节点、三种 op 各自冲突场景。
3. **CSV 解析单测**：UTF-8 BOM、CRLF、带引号含逗号字段拆分、位号首尾空格、空 Part/空位号/重复位号进校验报告。
4. **审计日志单测**：追加不覆盖（改 10 次=10 行）、direct vs propagated 标记正确、传播确认时给下游记 propagated。
5. **Web/路由层测试（FastAPI TestClient）**：建 BOM版本、建单板ID 自动建根节点、commit 翻转草稿、稳定 URL 返回对应状态、编辑触发冲突时返回确认片段。

## 11. 项目结构

```
reflow/
  app/
    main.py              -- FastAPI 应用装配、路由注册
    db.py                -- SQLite 连接、schema 初始化（建表 SQL）
    models.py            -- 数据访问层：boards/nodes/changes/initial_bom/edit_log 的 CRUD
    bom_engine.py        -- ★折叠引擎：解析某节点完整 BOM / 某位号解析值（纯逻辑）
    propagation.py       -- ★传播 & 冲突检测 + 冲突落库（纯逻辑，依赖 models 抽象）
    csv_import.py        -- ★CSV 解析、拆分、校验报告（纯逻辑）
    audit.py             -- 审计日志写入封装
    routes/
      hierarchy.py       -- / 层级导航、新建 BOM版本/单板ID
      board.py           -- 状态图、节点详情/编辑、冲突确认、工作区、commit
      log.py             -- 审计日志查询页
    templates/           -- Jinja2 + HTMX 片段
    static/              -- 少量 CSS（git-graph 竖线、diff 高亮）
  tests/
    test_bom_engine.py
    test_propagation.py   -- 含 5.5 例子
    test_csv_import.py
    test_audit.py
    test_routes.py
  docs/
  pyproject.toml          -- fastapi, uvicorn, jinja2, python-multipart, pytest, httpx
```

带 ★ 的三个文件是纯逻辑、零 Web 依赖，可独立测试与推理，是测试投入重点。Web 路由薄，只做「收请求 → 调逻辑 → 渲染片段」。

## 12. MVP 边界

**本期要做**：CSV 导入建初始 BOM、单板层级与新建、单板状态图、节点查看 + diff 高亮、工作区与 commit、历史节点编辑 + 传播 + 冲突确认、append-only 审计日志、稳定链接。

**暂不做（预留扩展）**：

- 飞线等「位号以外」的修改类型（数据模型留扩展空间）。
- 「按时间回看 / 状态回放」界面。
- 多用户、并发、权限。
- 工具内的测试记录功能（测试记录在外部笔记，本工具只提供链接）。
