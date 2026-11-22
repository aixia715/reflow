# 多用户登录与按单板授权

日期：2026-11-16（2026-11-22 修订，同日定稿）
状态：已定稿。文末「已决定」十四条均已采纳，正文按这些决定书写。实现以本文和对应计划为准。

## 背景与目标

Reflow 今天没有身份。`app/` 下没有登录、会话或密码；`deploy.sh` 把容器端口直接映射出去，拿到地址的人可以建板、改历史节点、删整组数据。CLAUDE.md 把「多用户 / 并发 / 权限」划在 MVP 之外，是因为当时只有一个人用。

现在要让同一份部署给多个人用，并且按单板分工。本次确认的产品需求：

1. 成员关系落在**单板**上（`boards_hierarchy` 的一行，即 `/board/{boardId}`）。三种角色：所有者（owner）、可编辑（editor）、只读（viewer）。
2. 不开放注册。账号由系统管理员手动创建。系统管理员和普通用户要能区分开。
3. 升级后，库里已有的全部单板归**第一个系统管理员**所有，再由他往下分配。

目标：

- 未登录进不了业务页面；登录后只能看见自己有份的单板。
- 只读成员能看、能下载，不能改。
- 所有者能管成员、转让、删除这块板。
- 系统管理员能建账号、停用、重置密码。管理员身份不自动变成每块板的所有者，也不能打开单板内容。停用导致的强制转让只看名称。
- 审计日志能看出是谁改的。
- 继续能离线跑 Docker：运行时不访问外网，不新增需要编译的密码学依赖。

## 非目标

这次不做，也不要在实现里「顺手」加：

- 自助注册、邀请链接、验证邮箱。
- SSO、OAuth、OIDC、LDAP、企业微信扫码。
- 找回密码、邮件、短信、验证码。
- 多因素认证。
- 位号级、字段级权限；「只能改电阻」这类规则。
- 不登录就能看的分享链接、只读 token。
- 机器账号、API token、给脚本用的长期密钥。
- 硬删除用户（停用即可。审计和成员行要留得住人）。
- 同一块单板多个所有者。
- 把 `initial_bom` 从 BOM 版本下沉到单板。表仍按版本共用。单板页不再写它，只在 BOM 版本页修正。
- 操作变换、实时协同、编辑中的长锁。两人同时改用乐观锁拒绝后一次提交，不合并两个人的位号。SQLite 仍然是单写者，另加 WAL 降低 `database is locked`。
- 登录失败次数限制、验证码、IP 封禁。部署在实验室内网，不对外。
- 恢复已经从仓库删除的 Windows 桌面版（`6a5844d`），也不为它留自动登录开关。

可以以后再做、本次不设计细节的：成员变更单独的审计页、强制首次改密、密码复杂度策略升级、把会话改成可集中吊销的设备列表 UI。

## 角色

两套身份，正交，不要混成一个枚举。

| | 存在哪 | 含义 |
|---|---|---|
| 系统管理员 | `users.is_admin = 1` | 全库的账号管理员：创建用户、停用、重置别人的密码。可以同时在某块板上有成员角色，也可以一块板都没有。停用导致的强制转让是例外，只看名称、不看内容。 |
| 单板角色 | `board_members.role` | 只对这一块板生效：`owner` > `editor` > `viewer`。一块板恰好一名 owner。 |
| BOM 版本负责人 | `bom_versions.owner_user_id` | 对一个「名称 + PCB 版本 + BOM 版本」负责。不是第四种单板角色。能否修正初始清单，还要看他是不是每块板的 editor。 |

一个人可以是管理员，同时是甲板的 owner、乙板的 viewer、丙板的陌生人。丙板上他什么都看不见。管理员身份不会把他在丙板上抬成 owner。

停用（`disabled_at` 非空）之后：不能登录；已有会话立刻作废；成员行保留，重新启用后原来的板还在。不能停用自己，也不能停用最后一位仍可登录的系统管理员。

## 权限矩阵

「未登录」走登录页，不区分资源在不在。「无此板」是 404。「有此板但不是成员」是 403。系统管理员如果不是这块板的成员，和普通人一样 403。

比较规则：`owner` 满足任何要求，`editor` 满足 editor 和 viewer，`viewer` 只满足 viewer。

### 账号与全站

| 操作 | 谁可以 |
|---|---|
| 打开登录页、提交登录 | 任何人 |
| 登出 | 已登录 |
| 修改自己的密码 | 已登录且未停用 |
| `/healthz`、`/version` | 任何人（容器探针和发版核对用，不返回业务数据） |
| `/static/**` | 任何人（样式和已内置的 htmx / Alpine，无业务数据） |
| `/docs`、`/redoc`、`/openapi.json` | 关掉。这不是对外 API，默认文档等于把路由表公开。 |
| 首页单板列表 | 已登录。只列出自己至少是 viewer 的单板。分组、位号数、单板数都按可见单板重算，不把看不见的兄弟板算进去。 |
| 新建单板（全新的「名称 + PCB 版本 + BOM 版本」，上传 CSV） | 任何未停用的已登录用户。创建者成为这块新板的 owner，并成为该 BOM 版本的负责人。 |
| 在已有 BOM 版本下再建一块单板（不上传 CSV） | 只有该 BOM 版本的负责人。创建者成为新板的 owner。其他人 POST 返回 403，不产生新板。这样不会出现「任意成员建一块自己当 owner 的板，把初始 BOM 修正卡死」。 |
| 打开 BOM 版本页，看初始清单 | 该版本的负责人，或该版本下至少一块板的成员。只显示初始清单本身，不列出他看不见的板。 |
| 修正初始 BOM | 必须同时是该版本的负责人，并且对该版本下每一块板至少是 editor。见「初始 BOM 与 BOM 版本页」。 |
| 用户管理（列表、创建、停用、启用、重置他人密码） | 系统管理员 |
| 看见「所有者已停用」的单板和 BOM 版本，并强制转让 | 系统管理员。清单只有名称和登录名，没有进入单板、没有 BOM 内容。转让不授予管理员阅读权。 |
| 哈希跳转 `/hash/{value}`、`/hash-lookup` | 已登录。只在**自己可见**的已提交节点和硬更改里匹配。唯一命中落在看不见的板上时，文案与「未找到」相同，不说「没有权限」（前缀查询会变成存在性探针）。 |

### 一块单板内部

路径都在 `/board/{boardId}` 下。先判断单板在不在（不在 → 404），再判断成员（不是成员 → 403），再判断角色。节点 id 不属于这块板时，成员看到的是 404，不是成员的人在单板这一关就已经 403，不会顺带知道节点在不在。

| 操作 | viewer | editor | owner |
|---|---|---|---|
| 状态图、节点页、对比页（只读部分） | 能 | 能 | 能 |
| 下载完整 BOM、本节点修改项 CSV | 能 | 能 | 能 |
| 下载对比差异 CSV | 能 | 能 | 能 |
| 看审计日志 | 能 | 能 | 能 |
| 看硬更改详情、下载硬更改图片、下载节点附件 | 能 | 能 | 能 |
| 元器件 lint 的异步刷新（`/lint-changes`，只读） | 能 | 能 | 能 |
| 工作区编辑、撤销、清除全部、CSV 导入、复制到草稿、commit | 不能 | 能 | 能 |
| 插入节点（页面、校验、导入预览、保存） | 不能 | 能 | 能 |
| 修正已提交节点、冲突确认、删除节点 | 不能 | 能 | 能 |
| 改节点标题和说明 | 不能 | 能 | 能 |
| 上传、删除节点附件 | 不能 | 能 | 能 |
| 新建、编辑、删除硬更改 | 不能 | 能 | 能 |
| 在单板页修正根节点（写入 `initial_bom`） | 不能 | 不能 | 不能 |
| 重命名这块板的单板 ID、删除这块板 | 不能 | 不能 | 能 |
| 成员管理、转让所有权 | 不能 | 不能 | 能 |

单板页上的根节点对所有角色只读，不渲染编辑入口。直接 POST 原来的编辑路径返回 403，正文「根节点是初始 BOM，请到 BOM 版本页修正。」`initial_bom` 不变。这块板自己的差异用本板上的新节点表达，不改共享的初始清单。

跨板对比（同单板名称 + PCB 版本、可跨 BOM 版本，现有 `list_sibling_boards`）：下拉里只出现当前用户至少是 viewer 的兄弟板。`left` / `right` 指向看不见的板时，按今天的「节点不存在」返回 404，不报出那块板的名字。两侧都看得见才出对比结果和差异 CSV。

### 跨多块板的首页操作

首页的重命名和删除作用在**名称 / PCB 版本 / BOM 版本**上，一条 SQL 会碰到这个范围内的每一块板（`rename_board_name`、`rename_pcb_version`、`rename_bom_version`、`delete_board_name`、`delete_bom_version`）。

两步，缺一不可：

1. 权限：操作者必须是该范围内**每一块**单板的 owner，按钮才渲染。直接 POST / DELETE 时若有任何一块不是，返回 403，toast「这个范围内还有不属于你的单板，不能整体重命名或删除。」不列出那些板的 ID。
2. 权限通过之后，先弹出影响清单，列出将被改名或删除的每一块板（`board_uid`，以及它的 PCB 版本、BOM 版本）。用户看过清单再提交。删除还要在输入框里键入当前名称（整组是单板名称，PCB 那一级是 PCB 版本字符串，BOM 那一级是 BOM 版本字符串），与库里的值一致才执行。`hx-confirm` 一句不够。

清单里的板他本来就都是 owner，列出来不构成额外泄露。权限没过时连清单都没有。

只拥有其中一块时，他仍然可以重命名、删除**那一块自己的**单板 ID。删单块板沿用现在的 `hx-confirm`，不走这份跨范围清单。

重命名会改 `boards_hierarchy` 上的文本，也要在同一事务里改 `bom_versions` 的对应列，否则版本页和负责人会挂在旧名字上。删除范围时删掉已经没有任何板、也没有初始 BOM 的 `bom_versions` 行。

## 初始 BOM 与 BOM 版本页

`initial_bom` 的键是 `(board_name, pcb_version, bom_version, reference)`，不是 `board_id`。同一 BOM 版本下的每一块板折叠时都读这一份。单板页上的根节点若还能改，改的就是别人的板。

已确认的做法：单板页不再写 `initial_bom`。共享清单只在 BOM 版本页修正。

### 单板页上的根节点

根节点（`parent_id is NULL`）一律只读。viewer、editor、owner 都不渲染编辑表单、行内修订、导入。`app/routes/board.py` 里 `edit_node` 的 `parent_id is None` 分支删掉对 `models.update_initial_bom` 的调用，改为 403，正文「根节点是初始 BOM，请到 BOM 版本页修正。」测试锁定：这次 POST 之后 `initial_bom` 与请求前逐行相同。

这块板特有的改动在本板新建节点（工作区或插入）。那条 changeset 在 `node_changes` 里，传播只沿本板的 `parent_id` 链走。

根节点页留一句说明和链接：「初始清单在 BOM 版本页维护。」链到下面的版本页。只读成员也能点开版本页看清单，但看不到修正表单。

### BOM 版本负责人

新表 `bom_versions`，一行对应一个三元组，而不是把负责人塞进 `initial_bom` 的每一行：

```sql
CREATE TABLE IF NOT EXISTS bom_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    board_name    TEXT NOT NULL,
    pcb_version   TEXT NOT NULL,
    bom_version   TEXT NOT NULL,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    revision      INTEGER NOT NULL DEFAULT 0,          -- 乐观锁，见「并发」
    revised_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL,
    UNIQUE(board_name, pcb_version, bom_version)
);
```

- 迁移：每个已存在的三元组一行，`owner_user_id` 是第一个系统管理员。与「现有单板归第一个管理员」同一次完成。
- 用 CSV 新建一个此前不存在的三元组：创建者成为负责人，同时成为那块新板的 owner。
- 负责人可以转让给另一名未停用用户（版本页上的操作，原子更新 `owner_user_id`）。转让**不**自动把他加成各板的 editor，也不把他从板上移除。
- 系统管理员可以在不打开单板的前提下强制更换负责人，见「所有者停用」。
- 一个版本始终有一名负责人。不设「多负责人」。

### 谁可以修正

必须**同时**满足：

1. 是这个 BOM 版本的负责人；
2. 对该版本下的每一块板至少是 editor。零块板时，第 2 条自然成立。

这是合取，不是「负责人或全体 editor 二选一」。理由：

- 影响清单要展开每块板的折叠差异和历史链上的传播冲突。那是板的内容。不是成员就不该看，更不该往别人的链上写传播结果。「采用修正值」会删掉下游显式 op，viewer 不够，所以下限是 editor。
- 只要求「每块板都是 editor」时，负责人对这份共享清单没有决定权。只要求「是负责人」时，已经把某块板转出去、自己不再是 editor 的人，会越过单板权限改那块板的历史。
- 「只有负责人能在该版本下新建单板」堵住了另一条路：任意成员建一块自己当 owner 的板，使「每块板都是 editor」永远达不成。负责人建出来的板，owner 就是他本人，第 2 条对他成立。以后若那块板的新所有者把他从成员里移除或降成 viewer，他暂时不能修正，直到被重新加为 editor。入口不显示，POST 403，文案「你还不能修正这份初始 BOM。」不点名他看不见的板。

### 版本页

路径：`/bom-version/{version_id}`。用表上的 id，不用三个文本拼查询串，避免改名之后旧链接指错，也避免把名称暴露在不必登录就能试的参数里。守卫是 `custom`：负责人或任一成员可 GET；修正用的 POST 再跑上面的合取。

页面：

- 初始清单只读表（位号、值）。
- 有修正权时才有编辑表单：一次改一条位号（增 / 改 / 不贴），交互与节点编辑同一套校验和 lint，但**先不写库**。
- 提交前的影响清单标题是「将影响以下 N 块板」。每一块列出 `board_uid`、折叠结果里会变化的位号（改前 → 改后），以及这条链上的传播冲突预览（下游节点、位号、下游当前值、修正后的值）。预览调用与今天根节点修正相同的冲突检测，只算不写。
- 每块板上的每条冲突都要在本页选出「保留下游值 / 采用修正值」。有未选的，提交按钮不可用，POST 直接 400，不写任何一行。
- 全部选完才 POST。服务端在**一个事务**里：核对 `bom_versions.revision`，更新 `initial_bom`，按各板的选择处理冲突并记审计，然后 `revision + 1`。任一步失败就整单回滚。不允许一部分板已经变了、另一部分还是旧值。
- 没有修正权的人看到清单和那句「你还不能修正这份初始 BOM。」没有表单，没有其他板的 `board_uid`。

版本页自己的乐观锁用 `bom_versions.revision`。隐藏域带上打开页面时的值。不一致时不写库，提示「已被 {用户名} 修改，请刷新」。这与节点上的乐观锁是同一句话，落地分属阶段 5 和阶段 6。

### 审计

`edit_log.source` 的 CHECK 只有 `direct` 和 `propagated`，不为这件事加第三种 source（SQLite 改 CHECK 得重建表）。用 `note` 的固定前缀「BOM 版本修正」：

- 每块受影响板的**根节点**记一行 `source='direct'`，`note` 以「BOM 版本修正」开头，`actor_user_id` 是按下提交的那个人。
- 某块板上「采用修正值」产生的传播，仍是 `source='propagated'`，`note` 同样带这个前缀，操作人还是同一个人。

单板的审计日志因此看得到这件事。根节点页在日志之外再放一句：「原始清单在 {时间} 被 {用户名} 修正。」取该根节点上最新一条带这个前缀的记录。没有这样的记录就不显示（升级前的旧修正没有这句前缀，不回填）。

## 交互

改前端时遵守 `docs/前端风格指南.md`：只用现有组件（`.panel`、`.input`、`.btn-primary`、`.flash-error`、`.badge`、`.empty`），不引入新颜色变量就能完成的，不要新变量。登录页、403 页、成员页、用户管理页都要在白天和夜间、以及 ≤720px 下看过。

### 登录

独立页面 `/login`，**不**继承业务顶栏。顶栏里的哈希跳转和「新建单板」都会打到受保护地址，放在登录页上只会在未登录时自己打自己。

页面上只有品牌、用户名、密码、提交。没有「注册」，没有「忘记密码」。

- 成功：303 到 `next`。`next` 只接受以单个 `/` 开头的站内路径；`//`、含 `://`、含反斜杠的一律丢弃，改去 `/`。防开放重定向。
- 用户名或密码不对：停在本页，`.flash-error`「用户名或密码错误」。不要在 URL 里带原因。
- 账号已停用：停在本页，「账号已停用，请联系管理员」。仍先跑一遍密码校验，避免用响应时间区分「无此用户 / 密码错 / 已停用」。
- 库里一个用户都没有：不显示能提交的表单（谁先打开端口谁就该当管理员，这个口子不能开）。文案说明去服务器执行 `python -m app.cli create-admin`。

已登录再打开 `/login`，直接去首页。

### 登出

顶栏 ⋯ 菜单底部一项「退出」，用 `POST /logout` 的小表单，不用 GET。成功后清会话、回登录页。

顶栏在用户名处显示当前登录名。系统管理员多一项「用户管理」。这两项属于 `base.html` 的常驻菜单，不放进各页的 `ctxlinks`。

### 首页

分组结构不变。每块板的 chip 上加角色徽章：所有者 / 可编辑 / 只读（复用 `.badge`，所有者绿、可编辑蓝、只读用默认灰，不新增颜色变量的话所有者用 `.badge-green`、可编辑用 `.badge-blue`、只读用无色 `.badge`）。

看不见的板不占「N 块单板」的计数。一个分组里一块可见的都没有，整个分组不渲染。

「＋ 新建单板」对所有已登录用户显示。整组 / PCB 版本 / BOM 版本的重命名和删除，只在「范围内每一块都是自己的」时显示。单板自己的重命名和删除只对 owner 显示。

空状态两句：「你还没有被加入任何单板。」以及「点右上角「＋ 新建单板」可以建一块新的；已有的板要请所有者把你加为成员。」

新建页的名称 / PCB / BOM 下拉只含自己已经是成员的版本，另加自己担任负责人的版本。三个字段仍可手输。手输命中已有三元组时，只有负责人能提交；其他人在预览区看到「这个 BOM 版本已存在，只有负责人能在其下新建单板。」手输全新三元组则任何已登录用户都能建。

整组、PCB 版本、BOM 版本的重命名和删除，先走影响清单（见「跨多块板的首页操作」），不要一点就执行。

### 只读成员看到的差别

能看状态图、节点（含工作区草稿的折叠结果）、对比、审计日志、硬更改、附件列表，能下载 CSV 和附件。工作区草稿对他是只读的当前状态，不是隐藏页。

不渲染、也不只是禁用：

- 节点页的编辑表单、行内撤销、清除全部、导入面板、commit 框、编辑节点说明
- BOM 表里的修订 / 撤销图标
- 状态图上的「在此后插入」「复制到草稿」「记录硬更改」
- 硬更改详情的编辑和删除
- 附件的上传和删除
- 首页上不属于他的重命名和删除

节点页顶部一条 `.flash-info`：「你对这块单板是只读，不能修改。」editor 和 owner 不显示这条。根节点页另有一句指向 BOM 版本页的说明，三种角色都看得到；修正表单则谁都没有。

隐藏不够。对应的 POST 仍然按矩阵返回 403。

### 成员管理

`/board/{boardId}/members`，仅 owner。入口放在该板页面包屑旁的 ⋯ 菜单（`ctxlinks`）：「成员」。

表格列：用户名、角色、加入时间。owner 那一行没有角色下拉、没有移除，旁边是「转让」的入口。editor / viewer 行可以改角色（只在这两个之间）或移除。

添加：输入已有用户的登录名 + 角色（editor 或 viewer）。找不到、已停用、已经是成员，都在表单旁说明，不新建账号。不能在这里把人加成 owner，那是转让。

转让：在现有成员里选一个人，`hx-confirm` 写明「转让后你将变为可编辑，对方成为所有者」。成功后自己变成 editor，对方变成 owner，仍留在这块板上。不能转给已停用账号。

移除用 `hx-confirm`，文案沿用风格指南：写明对象和「不可恢复」不适用这里——成员移除不是删数据，文案写成「确认将「某」移出这块单板？他将不能再打开该板。」

### 用户管理

`/admin/users`，仅系统管理员。从顶栏进入。

列表：登录名、是否管理员、是否停用、创建时间。没有删除按钮。

创建：登录名、初始密码、再输一次、是否系统管理员。密码至少 8 位。登录名规则见数据模型。创建成功 toast，密码不明文回显第二次。

停用 / 启用：`hx-confirm`。停用自己、停用最后一位管理员，按钮不渲染，POST 也拒绝，toast 说明原因。停用会立刻使其会话失效。

重置密码：一行里的「重置密码」，展开后两个密码框。成功后那名用户的全部会话失效，他需要用新密码重新登录。管理员自己走「修改自己的密码」，不走重置。

同一页的下方，而不是另一套「全部单板」目录：

- 「所有者已停用的单板」：`board_name`、`pcb_version`、`bom_version`、`board_uid`、已停用的所有者登录名。没有链到 `/board/{id}`，页面上不出现位号、节点或附件。
- 「负责人已停用的 BOM 版本」：三个名称加已停用的负责人登录名。同样不展示初始清单。

每一行一个「转让」：输入已存在且未停用的登录名。单板转让与普通转让一样，把对方设为 owner，原来的那名（已停用）所有者降为 editor，成员行保留。BOM 版本只改 `bom_versions.owner_user_id`，不动各板成员。管理员因此也不变成成员，转让完仍然打不开这些板。

这些动作不进 `edit_log`。打应用日志：logger 名 `reflow.admin`，INFO，内容包含操作者 id、对象种类（单板或 BOM 版本）、对象 id、新的用户 id。不记录 BOM 内容。

### 修改自己的密码

顶栏「修改密码」，`/account/password`。要填当前密码和新密码两次。成功后**保留当前会话**，其他会话（如果有）失效。当前密码不对：「当前密码不正确」。

### 无权限，以及稳定链接

节点 URL `/board/{boardId}/node/{nodeId}` 保持稳定，权限不编码进 URL，编辑内容也不改 URL。三种打开结果：

| 谁打开 | 结果 |
|---|---|
| 未登录 | 整页 303 到 `/login?next=` 原路径。登录成功且有权限，落到这个节点；登录成功但没有权限，再看到下面的 403。 |
| 已登录，单板不存在，或节点不属于该板 | 现有 404 页。 |
| 已登录，单板存在，但不是成员 | 403 页：说明没有这块板的访问权限，并写出所有者的登录名，便于联系。板没有所有者时，改成「请联系系统管理员」。 |
| 已登录，是 viewer，打开的是编辑类 POST | 不换页。见下面 HTMX 一节。 |

403 页用 `base.html`（人已经登录，顶栏要在），主区用 `.empty`。

## 技术方案

### 认证：服务端会话，不签名 cookie

选服务端会话，不选 Starlette `SessionMiddleware`。

签名 cookie 要额外依赖 `itsdangerous`，还要一个 `REFLOW_SECRET_KEY`。停用用户或重置密码时，签名 cookie 在过期前仍然有效，除非再做一张吊销表——那张表做出来，会话就已经是服务端会话了。所以直接做服务端会话，**不引入会话签名密钥**。环境变量里没有 `REFLOW_SECRET_KEY` 是故意的。

流程：

1. 登录成功，`secrets.token_urlsafe(32)` 生成令牌，cookie `reflow_session` 存原文。
2. 库里只存 `sha256(令牌)`，不存原文。库文件泄露不能把会话重放回去。
3. cookie：`HttpOnly`、`SameSite=Lax`、`Path=/`。`Secure` 由 `REFLOW_COOKIE_SECURE` 决定，默认 `0`。现有部署是局域网 HTTP（`deploy.sh` 只做端口映射，README 也是 `http://`），默认 Secure 会导致 cookie 被浏览器丢掉。前面加了 HTTPS 再设为 `1`。
4. 有效期 `REFLOW_SESSION_DAYS`，默认 14，从最近一次请求滑动。实现上不必每次请求都写库：距 `expires_at` 不足一半时再顺延即可。
5. 登出、停用、重置密码、修改密码（其他会话）都是删 `sessions` 行。

密码哈希用标准库 `hashlib.scrypt`，不加 bcrypt / argon2 / passlib。镜像构建已经 `pip install`，但运行时必须零外网，而 scrypt 在 Python 3.11+ 的标准库里，内存困难、参数写在哈希串里，以后调高成本不用迁移旧密码。

存储格式：`scrypt$16384$8$1$<salt_b64>$<hash_b64>`。`n=2**14`，`r=8`，`p=1`，`dklen=32`，salt 16 字节。校验用 `hmac.compare_digest`。用户不存在时，对一条固定的 dummy 哈希再算一次，让响应时间不要明显分成两截。

登录名规范化、角色高低比较、`next` 是否安全、scrypt 的编解码，放在 `app/auth.py`。这个模块不 import FastAPI、不碰数据库，是测试重点。

### 数据模型

沿用 `app/db.py` 的做法：`SCHEMA` 给新库，`_migrate` 给已有库做幂等 `ALTER` / `CREATE TABLE IF NOT EXISTS`。不另建版本表。

```sql
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,          -- 小写后的登录名
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,    -- 1 = 系统管理员
    disabled_at   TEXT,                          -- NULL = 可登录
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    token_hash   TEXT NOT NULL UNIQUE,           -- sha256(cookie 原文)
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_members (
    board_id   INTEGER NOT NULL REFERENCES boards_hierarchy(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    role       TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (board_id, user_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS board_one_owner
    ON board_members(board_id) WHERE role = 'owner';
```

`bom_versions` 的定义在「初始 BOM 与 BOM 版本页」。`nodes` 增加两列，给乐观锁用：

```sql
ALTER TABLE nodes ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE nodes ADD COLUMN revised_by INTEGER REFERENCES users(id);
```

旧行 `revision` 为 0、`revised_by` 为空。空的 `revised_by` 在冲突文案里显示「其他人」，不显示空白。

登录名：规范化成小写后存储。合法形式 `^[a-z0-9][a-z0-9_-]{2,31}$`（3–32 位，字母数字开头）。大小写不算两个账号。没有邮箱、没有显示名，界面上的「谁」就是登录名。

`edit_log` 增加一列，旧行留空：

```sql
ALTER TABLE edit_log ADD COLUMN actor_user_id INTEGER REFERENCES users(id);
```

SQLite 加列时带外键在现有版本可用；旧行是 NULL。用户只停用不删除，这列不会悬空。

`PRAGMA foreign_keys = ON` 已经在 `connect()` 里。再补两句，只在 `connect()` 里执行一次：

- `PRAGMA journal_mode = WAL`
- `timeout=5` 传给 `sqlite3.connect`（等价于 busy timeout）

WAL 的 `-wal` / `-shm` 和 `reflow.sqlite` 同在 `/data` 卷上。README 里「`cp` 出数据库文件」的备份在 WAL 下会抄到一半，改成用 Python `sqlite3.Connection.backup`。

### 现有数据归第一个管理员

函数放在 `app/models.py`，两层，都幂等：

- `assign_boards_without_owner(conn, user_id) -> int`：给**还没有 owner 行**的单板插一行 owner。已有 owner 的不动。返回认领块数。
- `backfill_owners_if_unassigned(conn) -> int`：如果**任何**单板都还没有 owner，就把全部无主单板认领给 `id` 最小的、未停用的系统管理员。只要已经有一块板有了 owner，就什么都不做（避免以后某块板丢了 owner 时，启动过程把别人的板领走）。

调用点：

1. `init_db` 末尾调用 `backfill_owners_if_unassigned`。阶段 1 先有了管理员、阶段 2 才建成员表的话，升级启动会把旧板一次领给最早的管理员。两个阶段一起上线时，这一下通常是空操作。
2. 创建**全库第一个用户**时（只能是管理员，见下），接着调用 `assign_boards_without_owner`，并为每个还没有 `bom_versions` 行的三元组插入一行、负责人设成他。这就是「上线后现有单板和现有 BOM 版本归首个管理员」。
3. 修复入口：`python -m app.cli assign-orphans --username <管理员>`，内部还是 `assign_boards_without_owner`，并补没有负责人的版本。只补空缺，不改已经有主的板或版本。

`models.create_board` 增加参数 `owner_user_id`。路由在建板成功的同一事务里写下 owner。不存在「建出来暂时没有所有者」的窗口。删除单板时 `ON DELETE CASCADE` 把成员行一起删掉。

第一个用户必须是管理员。`users` 为空时，创建非管理员直接拒绝。这样不会出现「有用户，但没有人能进用户管理，旧板也没人领」。

### 第一个管理员从哪来

两条路，都只在 `users` 为空时生效。库里已经有用户之后，环境变量**不会**重置密码，也不是后门。

**命令行（首选）。** 密码走 `getpass`，不进 argv，不进 shell 历史。

```bash
python -m app.cli create-admin --username admin
# 容器里：
docker exec -it reflow python -m app.cli create-admin --username admin
```

创建成功后，若库里已有单板，立刻 `assign_boards_without_owner`。

普通用户用同一模块的 `create-user`（阶段 2 起）：

```bash
python -m app.cli create-user --username huaqing
```

它不是管理员。把人加进某块板仍在网页上做。

**环境变量（无人值守的第一次启动）。** `REFLOW_BOOTSTRAP_USERNAME` 和 `REFLOW_BOOTSTRAP_PASSWORD` 同时设置，且 `users` 为空、密码不少于 8 位、用户名合法，则在 `init_db` 里创建管理员并认领无主单板。缺一个、或密码太短：不创建，打日志，进程照常起来（不要因为配错变量就让容器重启循环，`/healthz` 仍要通）。登录页在零用户时提示改用 CLI。

不要把这两个变量写进 Dockerfile 的 `ENV`。镜像是共享的，口令会留在镜像配置里。`deploy.sh` 只在注释里举例，默认的 `docker run` 不带口令。用过一次就从运行环境里删掉：`docker inspect` 看得到环境变量。

Web 上永远没有「第一个人打开页面就设管理员密码」的表单。

### 权限在 FastAPI 里怎么收口

两层，缺一不可。

**中间件**负责身份，不负责「这块板你是不是 editor」。自写一个 Starlette 中间件，不要用签名会话中间件：

1. 读 `reflow_session`，查出未过期、用户未停用的会话，放到 `request.state.user`（没有就是 `None`）。
2. 非安全方法（POST / PUT / PATCH / DELETE）做 CSRF 校验，失败按下面的 HTMX 规则返回。
3. 未登录且路径不在白名单：整页请求 303 到 `/login?next=`；带 `HX-Request: true` 的请求走 HTMX 规则。

白名单只有：`GET/POST /login`、`GET /healthz`、`GET /version`、`/static` 前缀。其余默认要登录，包括以后新加的路由。

**路由守卫表**负责授权，失败关闭。`app/authz.py` 里一张表，键是 `(方法, 路由模板)`，值是 `login` / `viewer` / `editor` / `owner` / `admin` / `custom`。中间件在路由匹配之后读取 `request.scope["route"].path`：

- 不在表里、也不在白名单：500，正文「路由未登记权限」。新路由忘了登记时测试直接红，而不是默默放行。
- `viewer` / `editor` / `owner`：单板不存在 → 404；角色不够或不是成员 → 403。
- `admin`：`is_admin` 不为 1 → 403。
- `login`：登录即可（首页、新建单板、改自己的密码）。
- `custom`：中间件只保证已登录，细规则在处理函数里。用于哈希跳转、跨范围重命名删除（先权限、再影响清单）、BOM 版本页、在已有版本下建板、跨板对比。这些函数开头调用同一个 `authz` 里的函数，不另写一套判断。单板页上的根节点编辑不进 `custom`：它登记为 `editor` 之后，处理函数对根节点一律 403。

既有路由**不**为了加 `Depends` 把几十个函数签名重写一遍。守卫表加中间件是统一入口；处理函数里需要「当前用户」时读 `request.state.user`。新写的登录、成员、用户管理路由同样登记进这张表，可以用 `Depends`，但运行时仍以表为准。

`app/propagation.py` 继续不碰权限。它是纯折叠链上的算法。操作人只是多出来的一个参数，默认 `None`，由路由传进去。

### HTMX：未登录和没权限

本仓库的 htmx 是 vendored 的 1.9。`HX-Redirect` 按现有写法是放在 **200** 上的（`hierarchy._hx_redirect`、硬更改删除）。4xx 上带 `HX-Redirect`，htmx 会走进错误分支，登录页被塞进局部区域，或者根本不跳。所以：

| 情况 | 整页导航 | `HX-Request: true` |
|---|---|---|
| 未登录 | 303 `/login?next=` | **200** + `HX-Redirect: /login?next=`，空 body |
| 不是成员，或角色不够（GET 页面） | 403 的 `forbidden.html` | 同左（整页 GET 本来也不是局部） |
| 角色不够的局部 POST（编辑、撤销、导入、commit、删除……） | 403 页 | **200** + `HX-Reswap: none` + `HX-Trigger: {"showToast": "没有权限做这个操作"}` |
| CSRF 失败 | 403，请刷新 | **200** + toast「页面已过期，请刷新后再试」 |

toast 的 JSON 继续 `json.dumps`，中文会是 `\u` 转义，和现有 `showToast` 一致。

局部 POST 用 200 而不是 403，是为了让 htmx 把响应头吃掉、不把错误页换进 `#bom`。测试要锁住 `HX-Reswap` 和 toast 文案，不要只断言状态码。

### CSRF

`SameSite=Lax` 挡住跨站带 cookie 的 POST，挡不住「同站点」和以后若有子域的情况。变更请求再做一次双提交 cookie：

- 登录页的 GET 就种下 `reflow_csrf`（**非** HttpOnly，`SameSite=Lax`，`Secure` 与会话 cookie 一致）。值是 `secrets.token_urlsafe(32)`。已有则不轮换，避免多标签互相作废。
- 所有非安全方法：表单字段 `csrf_token` 或请求头 `X-CSRF-Token` 必须与 cookie 一致，`hmac.compare_digest`。
- `base.html` 在 `htmx:configRequest` 里把头设上，这样现有的 `hx-post` / `hx-delete` 不用逐个改。
- 不走 htmx 的 `<form method="post">`（登录、新建单板、commit、节点说明、硬更改、改密、成员和用户管理里的普通表单）加隐藏域。
- GET 不校验。`/healthz` 和 `/version` 是 GET。

登录请求本身也校验。零用户的提示页没有表单，不涉及。

### 审计日志的操作人

`audit.record_edit` 增加仅关键字参数 `actor_user_id: int | None = None`，插进新列。已有调用按位置传 `note` 的不用改。

路由在每次 `record_edit`、以及 `propagation.apply_node_edit` / `delete_node` / `resolve_conflict` 上把当前用户 id 传下去。传播出来的 `source='propagated'` 行记**按下确认或执行这次编辑的那个人**，不记成空、也不虚构一个系统用户。冲突确认和「采用修正值」是同一次请求里的决定。

`propagation` 这些函数的新参数有默认值 `None`，现有纯算法测试不用改行为。

日志页多一列「操作人」。`actor_user_id` 联 `users.username`。NULL 显示「（升级前）」。用户后来被停用，名字仍在，不改成「已删除」。

成员变动、账号管理和强制转让不写进 `edit_log`。那张表是位号变更。强制转让打 `reflow.admin` 日志（见「用户管理」）。

BOM 版本修正是位号变更，写进各板的 `edit_log`，规则见「初始 BOM 与 BOM 版本页」。

### 文件下载与静态路径

对照当前代码，业务文件只有下面这些出口。`/static` 只有样式和内置脚本，保持公开。

| 路径 | 现在怎么来的 | 处理 |
|---|---|---|
| `GET /board/{board_id}/node/{node_id}/download` | `board.py` 在内存里生成 CSV（`scope=full` 折叠 BOM，`scope=changes` 本节点 changeset）。不落盘。 | 守卫 `viewer`。节点不属于该板则 404。 |
| `GET /board/{board_id}/compare/download` | 同样是内存里的差异 CSV。 | 守卫 `viewer`（先对本板）。左右节点所在的板都必须至少是 viewer，否则与对比页一样 404，不报出那块板的名字。 |
| `GET /board/{board_id}/node/{node_id}/import/template` | 固定表头的空模板（`change_csv_template` / `full_bom_csv_template`），没有业务数据。 | 守卫 `editor`。只读成员用不上导入模板。 |
| `GET /board/{board_id}/node/{node_id}/attachments/{aid}/download` | `FileResponse`。已核对附件行的 `node_id`。磁盘文件在 `REFLOW_UPLOAD_DIR/<board_id>/<node_id>/<uuid>`（`attachments.rel_path` + `storage.save_attachment`）。 | 守卫 `viewer`。核对逻辑保留。 |
| `GET /board/{board_id}/node/{node_id}/attachments` | 附件列表 HTML。 | 守卫 `viewer`。上传和删除仍是 `editor`。 |
| 硬更改图片 | 磁盘文件在 `REFLOW_UPLOAD_DIR/<uuid>.<ext>`（`storage.save_image`）。`hard_change_detail.html` 的 `href`、`data-src`、`<img src>`，以及 `hard_change_form.html` 的 `<img src>`，都指向 `/uploads/{{ filename }}`。 | 改为 `GET /board/{board_id}/hard-change/{hc_id}/images/{image_id}`，守卫 `viewer`，核对图片属于这条硬更改、硬更改属于这块板，再 `FileResponse`。灯箱的 `data-src` 一并改。编辑页上的缩略图也走这条路由。 |
| `GET /uploads/**` | `app/main.py` 把整个 `REFLOW_UPLOAD_DIR` 挂成 `StaticFiles`。硬更改图片因此不经成员检查。节点附件虽然页面走的是下载路由，同一文件仍可由 `/uploads/<board_id>/<node_id>/<uuid>` 取到：目录结构是可推的，uuid 难猜，但路径一旦出现在日志或引用里就不再看权限。 | **去掉这个挂载。** 不留「猜不到就算了」的旁路。 |
| `GET /static/**` | `StaticFiles`，htmx、Alpine、css、js。 | 保持公开，写在白名单里。 |
| 新建单板、导入预览用的 CSV | 请求体内解析进 `initial_bom` 或预览，不落盘。 | 没有下载 URL。 |

测试要同时打鉴权路由和旧的静态 URL：viewer 能拿到字节；非成员 403；未登录走登录跳转；`/uploads/<硬更改文件名>` 和 `/uploads/<storage_path>` 都是 404。

### 并发：乐观锁

WAL 和 5 秒 busy timeout 仍然要加，避免「一个人在写、另一个人在读」立刻 `database is locked`。`get_conn()` 继续每次开连接、请求结束不显式关闭。这只解决锁等待，不解决静默覆盖。

静默覆盖用版本号挡。`nodes.revision` 与 `nodes.revised_by` 见数据模型。工作区草稿和已提交节点用同一列。每次成功的写（编辑、撤销、清除、导入、复制到草稿、commit、插入、删除节点、冲突确认、改标题说明、上传或删除附件）在同一事务里执行：

```sql
UPDATE nodes
   SET revision = revision + 1, revised_by = ?
 WHERE id = ? AND revision = ?
```

`rowcount` 为 0 则整单回滚，不改 changeset。响应按 HTMX 规则：200、`HX-Reswap: none`，`#form-error` 与 toast 都是「已被 {用户名} 修改，请刷新」。`revised_by` 为空时用户名用「其他人」。不把后一次提交盖上去。

前端把打开页面时的 `revision` 带回去：

- 普通表单（编辑、commit、改说明、导入）加隐藏域 `revision`。
- 行内 `hx-post`（撤销、删除附件）用 `hx-vals` 带上同一值，属性用单引号、值用 `|tojson`。
- 冲突确认弹窗带上**被编辑节点**的 revision，以及每条下游节点在算出冲突时的 revision。确认请求在同一事务里逐个 `UPDATE ... WHERE revision = ?`，有一个对不上就全部回滚，上游那次修正也不保留——前提是这次修正还没在前一个请求里提交。今天的交互是先写入再弹窗。乐观锁落地后改成：有冲突时第一个请求**不写库**，只返回弹窗和当时读到的 revision；确认请求才在一个事务里写入并传播。取消仍是什么都不做。
- 插入页的改动在浏览器里。打开页面时记下父节点的 `revision`，`/insert/check` 和最终保存都带上。对不上就进 `editError`，不往暂存里塞，也不插节点。

传播的读改写不单独开一个不检查版本的口子。`apply_node_edit`、`delete_node`、`resolve_conflict` 收到的 revision 在它们第一次写之前核对。这些函数今天内部会 `commit`。乐观锁路径要改成路由开事务、内部函数可以不提交、最后一次成功才 `commit`，失败 `rollback`。默认参数保持「自己提交」，避免把所有旧调用一次性改坏；新参数用关键字，例如 `commit: bool = True`。

BOM 版本页用 `bom_versions.revision`，同一句话、同一事务边界，在阶段 5 就做。节点列在阶段 6 加。阶段 5 合并、阶段 6 还没合并时，单板编辑仍是后写覆盖，README 用一句话写明。

### 配置项

| 变量 | 默认 | 作用 |
|---|---|---|
| `REFLOW_DB` | `reflow.sqlite`（镜像里 `/data/reflow.sqlite`） | 不变 |
| `REFLOW_UPLOAD_DIR` | `uploads`（镜像里 `/data/uploads`） | 不变 |
| `REFLOW_BOOTSTRAP_USERNAME` | 空 | 仅当库中无用户时创建第一个管理员 |
| `REFLOW_BOOTSTRAP_PASSWORD` | 空 | 配对使用。用过即从环境中删除 |
| `REFLOW_COOKIE_SECURE` | `0` | `1` 时会话和 CSRF cookie 加 `Secure` |
| `REFLOW_SESSION_DAYS` | `14` | 滑动过期的天数 |

没有 `REFLOW_SECRET_KEY`。

## 需要修改的文件

### 新建

| 文件 | 改什么 |
|---|---|
| `app/auth.py` | 纯逻辑：scrypt 编码与校验、登录名规范化、角色比较、`next` 白名单、CSRF 常量时间比较 |
| `app/authz.py` | 守卫表、按单板取角色、跨范围「是否每块板都是 owner」、能否修正初始 BOM 的合取判断、403/未登录响应（区分 HTMX） |
| `app/cli.py` | `python -m app.cli create-admin` / `create-user` / `assign-orphans`。密码用 `getpass`。`create-user` 到阶段 2 才挂到命令上 |
| `app/routes/auth.py` | 登录、登出、修改自己的密码 |
| `app/routes/members.py` | 成员列表、添加、改角色、移除、转让 |
| `app/routes/admin_users.py` | 用户管理页；「所有者已停用」清单与强制转让 |
| `app/routes/bom_version.py` | BOM 版本页：只读清单、影响预览、单事务提交、转让负责人 |
| `app/templates/login.html` | 登录页，不继承顶栏 |
| `app/templates/forbidden.html` | 403 |
| `app/templates/members.html` | 成员管理 |
| `app/templates/admin_users.html` | 用户管理，含停用所有者清单 |
| `app/templates/password.html` | 修改自己的密码 |
| `app/templates/bom_version.html` | BOM 版本页 |
| `app/templates/_scope_impact.html` | 跨范围重命名/删除的影响清单弹窗 |
| `tests/test_auth.py` | 纯逻辑 |
| `tests/test_authz.py` | 守卫表与真实路由一致、失败关闭 |
| `tests/test_accounts.py` | 用户、会话、认领无主单板 |
| `tests/test_auth_routes.py` | 登录、登出、CSRF、HTMX 未登录 |
| `tests/test_permissions.py` | 矩阵里的允许与拒绝，含稳定链接、根节点只读、只有负责人能在已有版本下建板 |
| `tests/test_admin_routes.py` | 用户管理、强制转让、停用清单不出现板内容 |
| `tests/test_bom_version.py` | 影响清单、逐板确认、单事务回滚、审计前缀 |
| `tests/test_revision.py` | 乐观锁：版本不符不覆盖，传播同一事务 |
| `tests/test_auth_ui.py` | Playwright：登录、只读界面、403、影响清单、版本冲突提示 |

### 修改

| 文件 | 改什么 |
|---|---|
| `app/db.py` | 用户/会话/成员三张表、`bom_versions`、owner 部分唯一索引；`_migrate` 给 `edit_log` 加 `actor_user_id`、给 `nodes` 加 `revision` 与 `revised_by`；`connect` 开 WAL 和 timeout |
| `app/models.py` | 用户与会话 CRUD、成员 CRUD、转让（同一事务）、`bom_versions` 的创建与认领、`create_board(..., owner_user_id)`、无主单板认领、首页用的「某用户可见的板」、日志查询带上用户名、乐观锁的条件更新。重命名/删除范围时同步 `bom_versions` |
| `app/audit.py` | `record_edit` 增加 `actor_user_id` |
| `app/propagation.py` | `apply_node_edit` / `delete_node` / `resolve_conflict` 把 `actor_user_id` 传给每一条 `record_edit`，并接受 revision、支持把提交推迟到外层事务。冲突检测算法不变 |
| `app/main.py` | 挂中间件；关掉 docs/redoc/openapi；去掉 `/uploads` 的公开 `StaticFiles`；注册新路由；401/403 的异常形态以中间件为准，现有 404 页保留 |
| `app/routes/hierarchy.py` | 首页按成员过滤；全新三元组谁都能建并成为负责人；已有三元组只有负责人能建板；整组/版本的改名和删除先出影响清单，删除要键入名称；下拉只含可见版本和自己负责的版本 |
| `app/routes/board.py` | 路由登记进守卫表。根节点的编辑分支改为 403，不再调用 `update_initial_bom`。对比的兄弟板列表按可见性过滤。写路径带 `revision`。`record_edit` 和 propagation 调用带上当前用户 |
| `app/routes/log.py` | 登记为 viewer；模板上下文多操作人 |
| `app/routes/hard_change.py` | 读 viewer、写 editor；新增图片下载路由 |
| `app/routes/hashes.py` | 只在可见板里匹配 |
| `app/templates/base.html` | 用户名、修改密码、用户管理、退出；`htmx:configRequest` 带 CSRF 头 |
| `app/templates/home.html` | 角色徽章；按角色显示重命名和删除；空状态文案；重命名/删除改为先要影响清单 |
| `app/templates/board_new.html` | CSRF 隐藏域 |
| `app/templates/state_graph.html` | viewer 不显示插入、复制到草稿、记录硬更改；owner 才有「成员」 |
| `app/templates/node_detail.html` | viewer 去掉编辑、导入、commit、改说明；只读提示条；根节点对所有人去掉编辑并链到版本页；表单带 CSRF 和 `revision` |
| `app/templates/_edit_form.html`、`_bom_table.html`、`_changes_panel.html`、`_attachments.html` | viewer 不渲染写操作。这些片段被节点页 include，条件用模板变量 `can_edit` |
| `app/templates/compare.html` | 下拉数据已经是过滤后的；无需新交互，确认没有指向不可见板的链接 |
| `app/templates/hard_change_detail.html` | `href`、`data-src`、`<img src>` 都改到鉴权路由；viewer 不显示编辑和删除 |
| `app/templates/hard_change_form.html` | CSRF 隐藏域；已有图片的 `<img src>` 改到鉴权路由 |
| `app/templates/_conflict_modal.html`、`_delete_conflict_modal.html`、`insert_node.html` | 带上节点 revision；冲突确认改为「先选完再写入」 |
| `app/templates/log.html` | 「操作人」列 |
| `app/static/style.css` | 登录页居中所需要的少量布局类。能用现有 `.panel` 就不要新颜色 |
| `README.md` | 多用户怎么初始化、环境变量、WAL 备份、阶段说明里的「登录后在授权完成前人人都能改」若分阶段上线要写明 |
| `CLAUDE.md` | 「MVP 不做多用户」改为指向本设计；补一句新路由必须登记守卫表、HTMX 未登录返回 200 + `HX-Redirect` |
| `deploy.sh` | 不改默认 `docker run`。注释里写可选的引导变量，并写明用完要删 |
| `tests/conftest.py` | live server 用引导变量造出管理员；Playwright 的 `context` 带上登录 cookie；`insert_chain` / `seeded_server` 的 httpx 带会话和 CSRF |
| 现有会打受保护路由的测试 | 改用下面测试计划里的已登录客户端。只打 `/version` 的不用登录 |

不改：`app/bom_engine.py`、`app/csv_import.py`、`app/validation.py`、`app/component_lint.py`、`app/compare.py` 的差分算法。权限不要渗进折叠和校验。

## 测试计划

TDD：每张新表、每个纯函数、每条守卫，先有失败测试再写实现。全量 `pytest` 在每个阶段结束时是绿的。

### 单元

`tests/test_auth.py`：

- scrypt 往返成功；错密码失败；哈希串里的参数被读出来用，而不是写死在校验函数里忽略前缀。
- 用户不存在的校验路径会调用哈希（用 monkeypatch 数调用次数），不提前返回。
- 登录名大小写折叠、非法字符、过短过长。
- `next`：`/board/1/node/2` 通过；`https://evil`、`//evil`、`/\evil` 拒绝。
- 角色比较：owner ≥ editor ≥ viewer；`None` 什么都不满足。

`tests/test_accounts.py`（内存库，`init_db`）：

- 三张表和部分唯一索引存在；第二名 owner 插入失败。
- 第一个用户可以是管理员，并领走事先插入的两块无主单板；第二名管理员不会把已经有 owner 的板领走。
- `backfill_owners_if_unassigned` 在已有 owner 时返回 0。
- 建板时写入的 owner 就是传入的用户。
- 删板后成员行为 0。
- 停用用户后其会话行被删，令牌查不回来。

`tests/test_authz.py`：

- 遍历 `create_app().routes` 的每个路由，`(方法, 模板)` 要么在白名单要么在守卫表。挂载的 `/static` 除外。
- 表里多出来的、应用上已经不存在的键也失败，避免守卫表腐烂。

### 路由

共享夹具放在 `tests/helpers.py`：`auth_client(tmp_path, monkeypatch, username, password, is_admin=True)` 建库、建用户、登录，并让之后的 `post` / `delete` 自动带上 CSRF。现有测试的 `client` fixture 改成返回它（管理员，且按上面的规则拥有测试里新建的板）。只访问 `/version` 的测试保持匿名，并断言匿名访问 `/` 会到登录页，防止白名单扩得太大。

`tests/test_auth_routes.py`：

- 错密码 200 且正文是「用户名或密码错误」，不种会话 cookie。
- 停用账号是「账号已停用，请联系管理员」。
- 登录后 `next` 只接受站内路径。
- 未登录 `GET /` 303；未登录 `POST` 编辑且带 `HX-Request` 时是 200 + `HX-Redirect`，正文不是登录页 HTML。
- 无 CSRF 的 POST 被拒。
- 登出后再访问首页回到登录。

`tests/test_permissions.py` 用三个用户（owner / editor / viewer）和一块他们都不是成员的板，按矩阵抽这些断言（不必每个按钮一条，但每条路由一个角色反例）：

- viewer 打开节点 200，正文没有 `hx-post` 的编辑、没有 commit 框；POST 编辑得到 toast「没有权限做这个操作」，BOM 不变。
- editor 可以改工作区并 commit；不能打开成员页，不能删板。
- owner 可以添加 viewer、把 editor 转成所有者，自己变成 editor。
- 非成员打开 `/board/{id}/node/{nodeId}` 得到 403 页，且正文含所有者登录名；URL 不会被改写成首页。
- 不存在的 board id 是 404。
- 任何人 POST 单板页上的根节点编辑都被拒，`initial_bom` 不变，响应含「请到 BOM 版本页修正」。根节点 HTML 没有编辑用的 `hx-post`。
- 只拥有分组中一块板的人，DELETE 整组被拒，自己那块还在，响应里不出现另一块板的 `board_uid`。范围内每块都是 owner 时，删除前的清单列出全部 `board_uid`；名称没键对则不删。
- 不是负责人的成员在已有 BOM 版本下建板，403，不产生新板。负责人可以，且新板的 owner 是他。
- 哈希前缀只命中别人的板时，toast 与「未找到」相同。
- 硬更改图片：viewer 经鉴权路由拿得到字节；非成员 403；未登录走登录跳转。`hard_change_detail.html` 与 `hard_change_form.html` 的 HTML 里不再出现 `/uploads/`。
- 节点附件：viewer 经 `.../attachments/{aid}/download` 拿得到字节。把 `storage_path` 拼成 `/uploads/<storage_path>` 是 404。硬更改文件名拼成 `/uploads/<filename>` 也是 404。
- 对比差异 CSV：一侧板不可见时 404，正文不含那块板的 `board_uid`。

`tests/test_bom_version.py`：两块板共用一个版本。负责人且两块都是 editor 时，影响清单含两块板的 `board_uid` 和冲突；有冲突未选择时 POST 不改 `initial_bom`；全部确认后两块板的折叠一起变，每块根节点的 `edit_log.note` 以「BOM 版本修正」开头，操作人是提交者。中途让第二块板的 revision 对不上（或在事务里模拟第二块写入失败）时，第一块也保持原值。负责人被从其中一块降为 viewer 时，页面没有表单，POST 403，正文不出现那块板的 `board_uid`。

`tests/test_revision.py`：两个客户端先后编辑同一草稿，后一个带着旧 `revision` 时 changeset 仍是前一个的值，响应含「已被 {前者用户名} 修改，请刷新」。冲突确认请求里下游 revision 过期时，上游修正也不落库。

`tests/test_admin_routes.py` 追加：停用某板 owner 后，管理员页出现该板的名称和 `board_uid`，HTML 不含位号值；强制转让后新用户是 owner，管理员自己仍打不开这块板；日志调用可借 `caplog` 断言 `reflow.admin` 有一条 INFO。

`tests/test_admin_routes.py`：非管理员 403；创建用户；停用后该用户登录失败且旧会话无效；不能停用自己；不能停用最后一位管理员；重置密码后旧会话无效。

`tests/test_audit.py` 补一条：带 `actor_user_id` 写入后，日志查询能带出用户名；不传时列为 NULL。传播测试确认 `resolve_conflict(..., actor_user_id=)` 写在 propagated 那一行上。

### Playwright

`tests/test_auth_ui.py`，沿用 `live_server` + `page`：

- 未登录打开首页，落到登录页；错误密码出现「用户名或密码错误」。
- 管理员登录后看见引导阶段创建出来的板。
- 造一个 viewer，用这个浏览器上下文登录：节点页看得到位号，看不到编辑框和 commit；顶栏可以退出。
- 该 viewer 把地址栏改成（或 `goto`）这块板的节点 URL 能打开；改成另一块自己不是成员的板，看到 403 文案而不是 BOM。
- 白天 / 夜间各看一次登录页（背景和输入框对比度，复用 `test_theme.py` 里对 `.input` 的算法即可，选择器换成登录页的密码框）。

现有 UI 测试不逐个加登录步骤：`conftest` 里覆盖 Playwright 的 `context`，在创建上下文时写入管理员的会话 cookie。httpx 直接建板的辅助函数改为先登录。这样是为了让旧测试继续代表「已授权的所有者在操作」，而不是把每个脚本改成登录教程。

## 分阶段实施

每一阶段单独合并、单独可部署。后一阶段开始时前一阶段已经在 master 上。守卫表从第一阶段就存在，否则「失败关闭」会在中途把未登记的旧路由打成 500。

**阶段 1 — 登录闸门。** 表、哈希、会话、CSRF、中间件、登录/登出/改自己的密码、CLI 与环境变量引导、无主单板认领、`create_board` 写 owner。守卫表里现有业务路由全部先登记为 `login`（登录即可，**还不检查单板角色**）。测试夹具改为已登录，全量 pytest 通过。

这一阶段上线后，未登录进不来，但任一登录用户的权力与今天相同。CLI 只提供 `create-admin`，不要提前发普通用户。README 写明这一点。

**阶段 2 — 按单板授权。** 把守卫表改成矩阵里的角色；首页过滤；只读界面；成员管理与转让；`bom_versions` 与负责人（迁移归第一个管理员，新建版本的人成为负责人，只有负责人能在已有版本下建板）；单板页根节点改为 403，本阶段还没有版本页，拒绝文案先说明不能在单板页改，阶段 5 再改成「请到 BOM 版本页修正」并加上链接；跨范围重命名/删除的权限检查、影响清单和删除时的名称确认；哈希与对比的可见性；按「文件下载与静态路径」关掉 `/uploads` 并把硬更改图片、附件下载放进守卫；CLI `create-user`。本阶段结束时，普通用户可以被命令行造出来，并由所有者在网页上加进单板。初始 BOM 在版本页落地之前不能从网页改，README 写明。

**阶段 3 — 网页上的用户管理。** `/admin/users` 的创建、停用、启用、重置密码，以及停用/重置时吊销会话。规则（不能停用自己、不能停用最后一位管理员）有路由测试。同一页加上「所有者已停用」的单板和 BOM 版本清单（只有名称），以及不打开板内容的强制转让，并打 `reflow.admin` 日志。

**阶段 4 — 审计操作人。** `edit_log.actor_user_id`、propagation 传参、日志页新列。旧数据显示「（升级前）」。放在版本页之前，版本页写审计时直接带上操作人。

**阶段 5 — BOM 版本页。** `/bom-version/{id}` 的只读清单、影响预览、逐板确认、单事务提交、`bom_versions.revision`、每块板的「BOM 版本修正」审计、根节点页上的那句提示、负责人转让。本阶段不做节点乐观锁。

**阶段 6 — 节点乐观锁。** `nodes.revision` / `revised_by`，各写路径带版本号，冲突确认改为先选后写，传播在同一事务里核对下游版本。文案「已被 {用户名} 修改，请刷新」。

阶段 2 是权限真正生效的那一刀。阶段 5 之前，网页上不能改初始 BOM，这是有意的空窗，不要在阶段 2 临时放回单板页上的根节点编辑。

## 已决定

2026-11-22 确认，十四条全部按下列结论实现。

1. **管理员与单板内容。** 系统管理员不因管理员身份看见、修改或删除单板内容，也不自动成为各板所有者。强制转让只出现在「所有者已停用」的名称清单上：只显示名称和登录名，不链进单板，转让后管理员自己仍不是成员。第一个管理员拥有迁移当时的全部单板和 BOM 版本，那是一次性数据归属。
2. **谁可以新建。** 任何未停用用户都可以上传 CSV，新建一个此前不存在的 BOM 版本。创建者成为该版本负责人，并成为那块新板的所有者。已有版本下再建板，只有该版本负责人可以。
3. **一块板一名所有者。** 部分唯一索引保证同时只有一名 owner。普通转让和强制转让都是原子替换。
4. **只读与草稿。** viewer 能看工作区草稿的折叠结果，不能改。稳定链接指向草稿时同样只读打开。
5. **无权限的表现。** 已登录用户打开存在的单板或节点 URL，返回 403，并写出所有者登录名。没有所有者时写「请联系系统管理员」。单板不存在、或节点不属于该板，返回 404。哈希前缀只命中看不见的板时，文案与「未找到」相同。
6. **停用账号的登录文案。** 与密码错误分开，使用「账号已停用，请联系管理员」。校验仍然先跑一遍密码哈希。
7. **密码。** 至少 8 位。不要求字符种类，不过期。
8. **会话。** 14 天，从最近活动滑动。环境变量 `REFLOW_SESSION_DAYS` 的默认值就是 14。
9. **转让后的原所有者。** 降为 editor，成员行保留。强制转让同样处理。
10. **哪些记录进位号审计。** BOM 版本修正写入各板 `edit_log`，`note` 前缀「BOM 版本修正」。成员变更、停用、重置密码、强制转让不进 `edit_log`。强制转让打 `reflow.admin` 的 INFO 日志。
11. **cookie 的 Secure。** 默认关闭（`REFLOW_COOKIE_SECURE=0`），因为当前部署是 HTTP。部署改为在反代上终结 TLS 之后，再把部署脚本的默认改成 `1`。
12. **零用户。** 登录页说明到服务器执行 `python -m app.cli create-admin`，页面上没有设密码的表单。
13. **谁能修正初始 BOM。** 必须同时是该 BOM 版本的负责人，并且对该版本下每一块板至少是 editor。理由见「谁可以修正」。
14. **负责人转让。** 现任负责人可以在版本页把职责转给另一名未停用用户，不改各板成员。负责人已停用时，系统管理员按第 1 条强制更换，两条路径都保留。
