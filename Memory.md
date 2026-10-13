# AGI Project Memory

> 用途：为人类开发者和不同 AI 工具提供稳定的项目上下文与任务交接。
>
> 最后更新：2026-10-13
>
> 约束：本文档不记录 API Key、密码、Token、真实账号信息、私有 URL 或其他敏感数据；其中的状态描述应以源码、Git 状态和实际验证结果为准。

## 1. 项目愿景

本项目的最终目标是构建一个长期演进的、个人可控的通用型 AI 智能体应用，而不是一次性 Demo。

智能体未来应能理解自然语言任务、制定计划、选择工具、分步执行、处理失败、请求人工确认，并向用户报告最终结果。长期能力方向包括：

- 网页查询、整理结果并写入文件；
- 本地文件检索、读取与总结；
- 调用图像生成模型；
- 浏览器及桌面应用操作，例如 Steam、微信；
- 多步骤任务执行、恢复与追踪；
- 未来接入嵌入式桌面机器人，作为机器人的“大脑”。

项目的个人目标是将 AI Agent 理论转化为真实工程实践，形成可持续迭代、可展示技术能力的长期作品。

## 2. 工作区与职责边界

根目录：`E:\AGI_Project`

```text
AGI_Client  ->  AGI_Server  ->  AGI_Agent  ->  Models / MCP Servers / Local Tools
   客户端          服务端           智能体引擎          外部能力
```

| 子项目 | 当前定义的职责 |
| --- | --- |
| `AGI_Client` | 用户交互界面、任务输入、执行过程和结果展示。 |
| `AGI_Server` | 用户、鉴权、任务持久化、对外 API 与服务协调。 |
| `AGI_Agent` | 任务理解、计划、工具编排、执行状态和安全策略。 |

重要边界（**2026-10-13 修订**，见 ADR 0014）：

> `AGI_Agent` 提供**内部服务 API**（无认证、无界面，供 `AGI_Server` 调用）；
> 仍然**不**承担用户认证、不提供客户端页面。

修订前的表述是「不承担 HTTP API」——那条在服务化之后已经不成立（Java 侧要调它）。
但**职责重叠的红线没有变**：认证与界面仍然只属于 Server 与 Client。

**权威数据分属不同层**：执行状态（计划、工具调用、审批记录）权威在 **Agent 的
checkpointer**；业务数据（用户、任务列表）在 **Server 的数据库**。Server 存的是镜像，
两者不一致时**以 Agent 为准**，且 Server 绝不自己推断任务状态。

## 3. 已确认的协作与安全守则

1. 任何开发动作前，先讨论方案并等待用户明确确认。
2. 引入任何工具、流程、Skill、MCP、框架或第三方实现前，先在 GitHub 检查是否存在可复用的成熟方案；适合时优先使用，不重复造轮子。
3. 默认使用中文沟通；先给结论；代码、命令、变量名和路径保持英文。
4. 禁止自动执行 `git commit` 或 `git push`；提交前必须展示变更摘要并等待确认。
5. 删除文件/目录/Git 历史、修改 `.env` 或密钥、`git push`、`git rebase`、`git reset --hard`、强制推送、公开发布或生产部署，必须另行取得明确许可。
6. 涉及发送消息、账号登录、文件写入、下单和支付的 Agent 能力，必须经过权限策略与必要的人工确认，并保留可审计记录。
7. 不向对话、代码、日志或本文件写入真实 API Key、Token、密码或账号信息。
8. 每次提交代码时，必须把本文件 `Memory.md` 一并提交并推送，保持交接文档与实际状态同步。
9. **代码注释按「用户本人看得懂」的标准写，不能只满足于「能跑」。** 注释用中文，读者是未来的用户本人和接手的 AI。除了「做了什么」，更要写清「为什么这么做」：不直观的判断、外部系统的坑（选择器依据、超时取值的来由、接口限制）、被否决的方案及其原因。目标是用户只读注释就能看懂并自行修改这段代码，不需要重新逆向一遍逻辑。
10. **改功能时必须同步改对应的文档。** 文档只写「为什么」与「怎么运转」，实现细节留在代码注释里（分工规则见 `AGI_Agent/docs/features/README.md`）。**过期的文档比没有文档更糟**——它会主动误导下一个人。判断某段知识该放哪的法则：**删掉这段代码就失效的知识写进注释，跨越多个文件才成立的知识写进文档。**

## 4. 架构与设计（已外移，此处只留索引）

> 本节原先承载全部设计论证。为了让本文件专注于「**交接**」——这是什么项目、做到哪一步——
> 设计与决策已全部外移到 `AGI_Agent/docs/`。**本文件不再重复它们，只保留指针**，
> 避免同一件事写在两处、然后其中一处过期。

| 想了解 | 去哪 |
| --- | --- |
| 项目整体架构、技术栈、目录职责 | `AGI_Agent/docs/architecture.md` |
| 为什么引入某个依赖、为什么否掉某个方案 | `AGI_Agent/docs/decisions/`（ADR，16 篇） |
| 某个功能怎么运转、要改它先看哪里 | `AGI_Agent/docs/features/` |

几条**约束性质**的结论。先知道结论，需要细节再点进 ADR：

- 多 Agent 用中心化 Supervisor，`planner` 兼任分诊与调度 → ADR 0001
- 模型层可插拔：角色 → 配置，换供应商只改 `config.toml`，代码零改动 → ADR 0002
- 四个角色当前全部绑定 DeepSeek `deepseek-flash`（思考型模型）→ ADR 0003
- LangGraph 执行状态用 SQLite 持久化 → ADR 0004
- 浏览器自动化用 Playwright 手写窄工具 → ADR 0005
- **外部能力只走公开接口；需要登录的一律「人工登录一次 + 复用会话」，代码不接触凭据** → ADR 0006
- 工具注册表必须公布参数签名；风险等级由 `policies` 赋值，不采信模型自报 → ADR 0007
- 本阶段不引入 RAG 与向量数据库 → ADR 0008
- 价格查询用公开 JSON 端点（划清了「公开稳定端点」与「逆向私有接口」的界线）→ ADR 0009
- **工具调用的结局按「谁来修」分类，不许混为一谈**：结果不确定 → 停下让人**核实**（ADR 0010）；
  前提条件不满足（代理没开、登录过期）→ 停下让人**修环境**（ADR 0011）。
  两者都绝不自动重试。
- 图像理解走千问，**但只以「工具」形态接入**，不占用任何 agent 角色槽位；
  模型档位由「工具名 → 槽位 → `config.toml`」两级静态绑定，**不由模型自报** → ADR 0012
- **不做微信（个人版）自动化**：UIA 是唯一合规路线，但它的地基正被微信 4.x 拆掉，
  且撞腾讯 ToS；各条路线为何出局见 ADR 0013
- **拆成 Server 与 Agent 两个独立服务**，Agent 提供内部服务 API（边界见 §2）→ ADR 0014
- **「等待批准」是可查询的状态，不是挂着的连接**：断线、刷新、换客户端都不丢；
  事件带单调 `seq`，跨服务契约只有三个形状（内部模型不外传）→ ADR 0015
- **服务化选型**：Java 21 + Spring MVC(`SseEmitter`) + MySQL + uni-app + SSE；
  推迟的组件（MongoDB / Redis / Qdrant）各自写明触发条件，不许以"以后可能用得上"进来 → ADR 0016
- `graph`（怎么思考）/ `tools`（能做什么）/ `policies`（允许做什么）三者必须分离 → `architecture.md`

## 5. 当前实际进度

> 本节只记「**做到哪一步了**」。设计理由不在这里重复，见上方 §4 的指针。

### 已完成

`AGI_Agent` 是一个可运行的 uv Python 包项目（`uv init --package --name agi-agent --python 3.12`）。

下列能力均已**实测跑通**——不是「代码写完了」，是真的跑过：

| 能力 | 状态 | 实测内容 |
| --- | --- | --- |
| 最小闭环 | ✅ | `uv run agi-agent "..."` 真实调用 DeepSeek 并返回回复 |
| 多 Agent 图 + 人工确认中断/恢复 | ✅ 四条路径 | 闲聊回复、任务获批后执行成功、用户拒绝后正常终止、只读任务自动放行 |
| 本地只读文件工具 | ✅ | `local.list_dir`、`local.read_file`（首个真实工具） |
| Steam 工具（首个 `WRITE` 风险能力） | ✅ 五个工具 | 搜索 / 查价 / 购物车列表 / 加购 / 删除；加购与删除**幂等** |
| 「结果不确定」的写操作不再被重试 | ✅ 已修复并回归 | 一次复核失败曾让购物车出现 3 份同一游戏 → ADR 0010 |
| 网络/登录问题当场停下并给出下一步 | ✅ 已实测 | 45 秒静默等待 → 8.6 秒明确报错；登录过期会在**点击前**被发现 → ADR 0011 |
| CLI 过程输出（流式） | ✅ 已实测 | 工具调用在**开始前**就打印进度，不再是一个不动的窗口 |
| 单元测试 | ✅ 150 个 | `uv run pytest`，不消耗任何 API 额度 |
| 代码质量检查 | ✅ 已实测 | `uv run ruff check .` 全绿（首次开启时实测 786 条 → 逐条判定后修掉 8 条真问题） |
| 换行符基线 | ✅ 已实测 | 根 `.gitattributes` 固定入库 LF；实测全仓 0 个 CRLF，不再依赖各机器的 `core.autocrlf` |
| **图像理解（千问）** | ✅ 已实测 | 端到端：读一张写着随机数字的图，最终回复与图中数字**一致**；Key 缺失时正确**停下**（不进重规划）；只读任务未受影响 → ADR 0012 |
| **对外 HTTP 服务（Phase 1）** | ✅ 已实测 | `uv run agi-agent-api` 起服务，4 个端点；curl 实测：建任务 → 停下等人批准 → **断掉一切事件流后仍能查到审批、从另一条连接提交并跑完**；404/409 语义正确；任务串行（单测断言并发数恒为 1）→ ADR 0014/0015 |

关键入口（完整清单以实际目录为准，目录职责见 `AGI_Agent/docs/architecture.md`）：

```text
AGI_Agent/
├── config.toml                  # 模型槽位 -> 模型绑定（可提交，不含密钥）
├── .env                         # DEEPSEEK_API_KEY、DASHSCOPE_API_KEY、AGI_BROWSER_PROXY，已被 .gitignore 忽略
├── docs/                        # architecture.md + decisions/(ADR) + features/
├── scripts/steam_login.py       # Steam 人工登录辅助 + --verify
└── src/agi_agent/
    ├── cli.py                   # 入口
    ├── config.py                # 配置与常量读取
    ├── schemas/                 # 稳定数据契约（6 个：task/plan/tool_call/approval/event/review）
    ├── graph/                   # builder / state / checkpoint / nodes
    ├── api/                     # 对外 HTTP 接口（FastAPI）：contract / runner / app
    ├── tools/                   # registry + errors + builtin(files, steam, vision, fake)
    ├── providers/llm/           # 供应商注册表 + structured.py
    └── policies/                # risk.py
```

假工具只剩 `wechat.send_message` 一个。它**不再是待实现的能力**（ADR 0013 已决定不做微信），
保留它只是因为它恰好是验证审批链路的现成样本。

### 尚未完成 / 未执行

- ~~微信（个人版 UI 自动化）尚未开始~~ → **已调研并决定不做**，各条路线为何出局见 ADR 0013。
- **MCP Client 层有意未实现**：目前没有出现「需要一个成熟第三方 MCP Server」的真实需求。
  判据见 ADR 0006。
- `langgraph.json` 未创建；当前由自带 `cli.py` 驱动，暂不需要，理由见 `AGI_Agent/docs/architecture.md`。
- `Ruff` **只做检查、不做格式化**（未启用 `ruff format`）：格式化会重排全部源文件，
  应单独一次提交，不要混进功能改动。规则集取舍写在 `AGI_Agent/pyproject.toml` 的注释里。

### 仓库结构（2026-10-12 起改为 Monorepo）

`E:\AGI_Project` 现在是唯一的 Git 仓库，包含 `AGI_Agent`、`AGI_Client`、`AGI_Server` 与本文档。

- 分支：`main`
- 远端：`origin` → `https://github.com/MakePota/AGI_Project.git`（私密仓库）
- 首次提交：`654fd4e` "chore: initialize AGI_Project monorepo (agent, client, server)"
- 根 `.gitignore` 覆盖 `.env`、`.secrets/`、`.idea/`（均已实测 `git check-ignore` 生效）
- `AGI_Client` 已不再是空目录（2026-10-13 起由用户在 HBuilderX 中创建为 uni-app 项目，
  Vue 3 + 默认模板；`unpackage/` 构建产物由它自带的 `.gitignore` 忽略）
- `AGI_Server` **仍是空目录**，靠 `.gitkeep` 占位（Phase 2 才动）

变更说明：`AGI_Agent` 原先是被 `uv init --package` 自动初始化的独立嵌套 Git 仓库，并有独立远端
`MakePota/AGI_Agent`。经用户明确授权，该嵌套 `.git` 已删除且**历史不保留**，改为在
`E:\AGI_Project` 根目录重建为单一 Monorepo。旧的 `MakePota/AGI_Agent` 仓库由用户自行处理。

**不引入 uv workspace**（这个问题在 2026-10-13 有了确定的答案）：`AGI_Server` 用 Java、
`AGI_Client` 用 uni-app，只有 `AGI_Agent` 是 Python。共享 Python lockfile 的前提
（多个 Python 子项目）根本不成立。`AGI_Server` 要调 Agent 时走 HTTP，
而不是把 Python 包当依赖引进去。

## 6. 当前任务

### 当前供应商

**主力（文本）：DeepSeek** —— 四个协作角色**全部**绑定它。

| 项目 | 已确认值 |
| --- | --- |
| 产品显示名 | `DeepSeek-V4.1-Flash` |
| DeepSeek API 模型 ID | `deepseek-flash`（**思考型模型**，限制见 ADR 0003） |
| OpenAI 兼容 Base URL | `https://api.deepseek.com` |
| API Key 环境变量 | `DEEPSEEK_API_KEY` |

**图像理解：千问（阿里云百炼 / DashScope）** —— 只被图像工具使用，**不占用任何角色槽位**。

| 项目 | 已确认值 |
| --- | --- |
| 快档模型 ID | `qwen3.8-flash`（**思考模式在 config 里显式关闭**） |
| 准档模型 ID | `qwen3.8-max`（思考模式显式开启） |
| OpenAI 兼容 Base URL | `https://dashscope.aliyuncs.com/compatible-mode/v1`（**必须带 `/compatible-mode/v1`**，与 DeepSeek 的写法不同） |
| API Key 环境变量 | `DASHSCOPE_API_KEY` |

换供应商或换模型都只需改 `AGI_Agent/config.toml`（ADR 0002）。

**明确不装**：`langchain`（meta 包，本项目用 LangGraph 编排，不需要）、
`langchain-deepseek`（对 `deepseek-flash` 的覆盖未经核实，而 `ChatOpenAI` + `base_url` 路径已核实）。

后续按触发条件再装：`langfuse`（出现值得观察的对象时）、`langchain-anthropic`（接入 Claude 时）。

`Ruff` 已于 2026-10-13 装为 dev 依赖并跑通（见 §6 下一步第 9 条）。

### 安全事项（重要）

- `.env` 中的 DeepSeek API Key 曾在对话中明文传递过，**建议尽快在 DeepSeek 控制台轮换该 Key**。
- **`.env` 中的千问 Key（`DASHSCOPE_API_KEY`）同样在对话中明文传递过（2026-10-13），
  建议尽快在阿里云百炼控制台轮换。** 已写入 `.env`（实测被 gitignore 忽略），
  但"没进版本控制"不等于"没泄露"——它已经出现在对话记录里。
- `.env` 已加入 `.gitignore` 并实测生效；`.idea/` 已由 `AGI_Agent/.gitignore` 忽略；
  `.secrets/`（Steam 登录态）已加入根 `.gitignore`，并被本地文件工具列入黑名单，两条均实测生效。
- 密钥最终要迁到服务端密钥库。届时只需改变 `ModelSpec.api_key_env` 的解析来源，角色与模型配置不受影响。

### 下一步

1. ✅ 核心数据模型（`schemas/`）
2. ✅ 第一个最小图（`graph/`），含 `interrupt` 人工确认与恢复
3. ✅ 首个真实只读工具（`local.list_dir`、`local.read_file`）
4. ✅ **Steam 工具（走 Web，Playwright）—— 五个工具已完成并实测跑通**：
   搜索、查价、购物车列表、加购、删除。详见
   [功能文档](AGI_Agent/docs/features/steam-tools.md) 与
   [ADR 0005](AGI_Agent/docs/decisions/0005-playwright-for-browser-automation.md)
   ——后者含完整调研结论：为什么没有现成轮子可复用、为什么不走 Steam 桌面客户端。
5. ✅ **修复「假失败 → 自动重试 → 重复副作用」事故（2026-10-12）**：
   工具把「验证不了」当成「操作失败」，planner 据此重试，购物车里留下了 3 份同一个游戏。
   现在写操作的失败按「谁来修」分类，**结果不确定时停下问人、不进重规划**，且加购/删除**幂等**。
   完整复盘见 [ADR 0010](AGI_Agent/docs/decisions/0010-no-retry-on-uncertain-write.md)。
6. ✅ **前提条件失败当场停下、CLI 过程输出（2026-10-12）**：实测反馈"断代理后等 45 秒
   毫无输出""该报网络不可达却报不确定"。现在开跑前用 8 秒探测通路（实测 45 秒 → 8.6 秒），
   **登录过期会在点击之前就被识别**并提示重新扫码，网络与登录问题一律停下而非重规划；
   CLI 改为流式，工具调用在开始前就打印进度。
   见 [ADR 0011](AGI_Agent/docs/decisions/0011-halt-on-unmet-precondition.md)。
7. ✅ **项目知识留存方案已落地（2026-10-12）**：`AGI_Agent/docs/` 下分三块——
   `architecture.md`（整体长什么样）、`decisions/`（ADR：为什么这样选）、
   `features/`（功能怎么运转、要改它先看哪里）。本文件随之收窄为**纯交接用途**。
   `AGI_Client`、`AGI_Server` 将来各自建立自己的 `docs/`。
8. ⛔ **微信（个人版 UI 自动化）：已调研，决定不做（2026-10-13）**。调研把可行路线
   收敛成七类，**唯一合规的 UIA 路线**依然被否——微信 4.x 改成自绘渲染正在拆掉它的地基，
   且撞腾讯 ToS、存在账号被限制风险。各条路线的出局原因见
   [ADR 0013](AGI_Agent/docs/decisions/0013-no-wechat-automation.md)，
   **不要重跑这份调研**。
9. ✅ **代码质量与换行符基线（2026-10-13）**：装上 `Ruff` 并定下规则集，实测从
   786 条收敛到 0——其中 756 条是「中文全角标点」误报（`RUF001/002/003`），
   **正确做法是关掉规则而不是改代码**，因为本项目规定注释用中文。真问题只有 8 条，
   已全部修掉。同日加入根 `.gitattributes` 固定入库 LF。
   两条踩出来的坑记在 `AGI_Agent/pyproject.toml` 的注释里：RUF100 的自动修复会
   把整行注释**连同中文解释一起删掉**（故设为只报不修）；注释里写出「井号 + noqa」
   字样会被 Ruff 当成真指令解析。**未启用 `ruff format`**，理由见 §5 末尾。
10. ✅ **图像理解（千问，2026-10-13）**：以**工具**形态接入，不占用角色槽位；
    档位是「工具名 → 槽位 → `config.toml`」两级绑定，**不由模型自报**。
    端到端实测通过（读出图中的随机数字，与图中一致），Key 缺失时正确停下。
    关键实测数据：兼容端点**确实接受 base64 图**（响应里 `image_tokens=154` 为证），
    思考模式默认开启、关掉后 token 从 73 降到 5。详见
    [功能文档](AGI_Agent/docs/features/vision-tools.md) 与
    [ADR 0012](AGI_Agent/docs/decisions/0012-vision-via-dashscope.md)。
    顺带修掉一个既有漏洞：审批摘要原先只显示模型写的描述、不显示**参数**，
    于是看不出要把哪个文件发出去。

11. ✅ **Agent 对外服务化 Phase 1（2026-10-13）**：`AGI_Agent` 现在是一个可被调用的
    HTTP 服务（FastAPI，4 个端点：建任务 / 查状态 / SSE 事件流 / 提交审批）。
    **核心设计是「等待批准」为可查询状态**（ADR 0015）——实测：断开所有事件流之后
    仍能查到审批请求、并从另一条连接提交决定让任务跑完。
    顺带修掉一个**把成功报成失败**的既有缺陷：reviewer 输出的 JSON 里带未转义双引号
    导致解析失败，于是"任务实际做成了却报 failed"；现在提示词先堵住这种坏法，
    失败时带着解析器原话重试一次（详见 `providers/llm/structured.py`）。

**下一步是 Phase 2：`AGI_Server`（Java 21 + Spring Boot 3 + Maven + MySQL），
带完整的任务 CRUD**。之后才是 Phase 3（`AGI_Client` 页面）。分三期是因为
**每期都能独立验证**——Phase 1 已用 curl 验完，Phase 2 同样只用 curl。

Phase 2 的**环境已就绪**（2026-10-13 实测）：本机 Java **21.0.7 LTS** + Maven **3.9.10**；
MySQL **8.0.44** 由 `AGI_Server/docker-compose.yml` 跑在 `127.0.0.1:3307`（刻意避开
3306 与其他项目冲突），密码在已被 gitignore 忽略的 `AGI_Server/.env` 里、**不写进 compose**。
验证方式是容器 healthy + 应用账号实连（字符集 `utf8mb4`）。
`AGI_Client` 的骨架用户已经建好（uni-app / Vue 3），**先不要动它的代码**，
等 Phase 2 的接口形状定下来再写页面，免得返工。

**已知遗留（不阻塞，但别忘了）**：

- `PRECONDITION_FAILED` 与不确定结局目前共用 `TaskStatus.NEEDS_HUMAN`，
  用户可见的措辞区分了，状态枚举没区分。需要按状态统计失败类型时再拆。
- 图像理解的错误分类里，**HTTP 400 与 429 两条分支尚未实测**（只实测到 401 与 404）。
  两条都是保守推断，`vision.py` 的 `_classify_provider_error` 里已就地在场标注。
  真撞上时按实况修正，不要照抄推断。

**已关闭的遗留（不要再花时间验证）**：

- ~~`steam.remove_from_cart` 的「一次清除多份同名条目」路径未经真实购物车验证。~~
  **结论：不用管了。** 用户于 2026-10-13 判定：重复条目已不会由 Agent 产生
  （加购已幂等），真出现也不影响正确性，不值得再花时间验证。
  代码里的多轮循环 + 复核**保持现状**（防御性写法，不是待办）。
  完整口径（含与 ADR 0010 那次事故的观察差异）见
  [功能文档](AGI_Agent/docs/features/steam-tools.md) 的「已知局限」——**已同步，无需再改**。

## 7. 新 AI 工具的启动与交接规则

新的 AI 工具或新会话开始时，应按以下顺序工作：

1. 阅读本文件 `Memory.md`。它的职责**只有一个**：让人和 AI 快速知道「这是什么项目、做到哪一步了」。
   它不承载设计论证——那些在 `AGI_Agent/docs/`。
2. 检查目标目录的实际文件、Git 状态、依赖锁文件和测试结果，**不能仅依赖本文档**。
3. 需要动某个功能之前，先读它的功能文档与相关 ADR：
   `AGI_Agent/docs/architecture.md` → `docs/features/README.md` → 对应功能文档 → 其中的 ADR 链接。
4. 在实施新功能前，先讨论方案、检索 GitHub 上的可复用 Skill/MCP/成熟实现，并等待用户确认。
5. 完成有意义的阶段后，更新本文件的「当前实际进度」「当前任务」；
   **如果这次改动改变了某个功能的行为或设计，同步更新它的功能文档 / ADR**（守则 §3 第 10 条）。

更新本文件时，只写**状态**，不要把它写成逐轮聊天记录，也不要把设计理由抄回来——
那正是它上一版变得又长又自相矛盾的原因。
