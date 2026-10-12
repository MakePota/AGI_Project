# AGI Project Memory

> 用途：为人类开发者和不同 AI 工具提供稳定的项目上下文与任务交接。
>
> 最后更新：2026-10-12
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

重要边界：`AGI_Agent` 当前不承担 HTTP API、用户认证或客户端页面，避免与 `AGI_Server`、`AGI_Client` 职责重叠。

## 3. 已确认的协作与安全守则

1. 任何开发动作前，先讨论方案并等待用户明确确认。
2. 引入任何工具、流程、Skill、MCP、框架或第三方实现前，先在 GitHub 检查是否存在可复用的成熟方案；适合时优先使用，不重复造轮子。
3. 默认使用中文沟通；先给结论；代码、命令、变量名和路径保持英文。
4. 禁止自动执行 `git commit` 或 `git push`；提交前必须展示变更摘要并等待确认。
5. 删除文件/目录/Git 历史、修改 `.env` 或密钥、`git push`、`git rebase`、`git reset --hard`、强制推送、公开发布或生产部署，必须另行取得明确许可。
6. 涉及发送消息、账号登录、文件写入、下单和支付的 Agent 能力，必须经过权限策略与必要的人工确认，并保留可审计记录。
7. 不向对话、代码、日志或本文件写入真实 API Key、Token、密码或账号信息。
8. 每次提交代码时，必须把本文件 `Memory.md` 一并提交并推送，保持交接文档与实际状态同步。

## 4. 已确认的架构方向

### AGI_Agent 基础技术方向

- 项目管理：`uv`
- Python：`3.12`
- 工作流编排：`LangGraph`；等第一个真实可加载的图出现时再安装
- 模型抽象：以 `LangChain Core` 的 `BaseChatModel` 作为唯一模型接口，配合 Provider 适配包（首个为 `langchain-openai`）
- 跨工具协议：后续以 MCP Client 方式接入经过审查的 MCP Server
- 可观测性：后续接入 `Langfuse`（等图跑起来后再引入）
- 数据建模与配置：`Pydantic`、`pydantic-settings`
- 测试与代码质量：`pytest`、`Ruff`

当前阶段仍不接入向量数据库、RAG、浏览器自动化或桌面自动化；它们属于后续独立 Skill/Tool 能力。

### 多 Agent 协作模式：中心化协调（Supervisor）

`AGI_Agent` 采用 Supervisor 模式：由一个中心节点负责决策与调度，其余角色作为 worker 被指派、向中心汇报，而不是平级 Agent 之间互相对话。

四个角色与模型等级：

| 角色 | 职责 | 模型等级 |
| --- | --- | --- |
| `conversation` | 语义理解与直接对话回复 | 普通 |
| `planner` | 分诊 + 规划 + 调度（即 Supervisor 本体） | 最强 |
| `executor` | 执行具体步骤与工具调用 | 普通 |
| `reviewer` | 审查执行结果，不合格则打回 `planner` | 较强 |

执行流：

```text
用户输入
  └─ planner 分诊（最强）
       ├─ 闲聊 ─> conversation（普通）─> 回复
       └─ 任务 ─> planner 规划 ─> executor（普通）─> reviewer（较强）
                                                       │
                                          ┌── 通过 ────┴─> 回复
                                          └── 不通过 ───> 回到 planner 重新规划
```

已确认的设计决策：

- 分诊由 `planner`（最强模型）在第一步完成，不由 `conversation` 承担。已知代价：纯闲聊也需要一次强模型调用。
- 角色与模型解耦：角色只定义能力等级，具体模型通过配置绑定。
- `providers` 层返回 LangChain 的 `BaseChatModel`，不自造模型协议。

注意：本节原先写有“当前阶段不接入……多 Agent”。该条已变更——多 Agent（Supervisor 模式）现纳入当前范围。

### LLM 可插拔设计

与 `tools` 同构：注册表 + 工厂 + 配置驱动。

- 接口：`get_model(role) -> BaseChatModel`；节点只认角色，不认供应商。
- 供应商注册表：`openai` → `ChatOpenAI`（OpenAI 兼容端点，可覆盖 ChatGPT / DeepSeek / Kimi / 智谱 / 千问）；`anthropic` → `ChatAnthropic`（Claude，需要时再装包）。
- 配置：每个角色一份 `ModelSpec`（`vendor`、`model`、`base_url`、`api_key_env`、`temperature`），统一写在项目根目录的 `config.toml`。**换供应商或换模型 = 改 `config.toml`，代码零改动**（已实测验证）。
- API Key 只以环境变量名的形式出现在 `config.toml` 中，真实密钥放在 `.env` 或系统环境变量，不写入代码或本文件。
- 注意：不要用 `AGI_LLM__<角色>__MODEL` 这类嵌套环境变量做局部覆盖——pydantic-settings 对嵌套模型是整体替换而非逐字段合并，只给一个字段会因其余字段缺失而校验失败。环境变量只用于完整覆盖或提供密钥。
- `config.toml` 在进程导入时读取一次，修改后需要重启进程。

候选供应商：Claude、ChatGPT、Kimi、智谱、千问、DeepSeek。**当前四个角色全部绑定 DeepSeek `deepseek-flash`。**

已知局限（不要假设可以完全无感替换）——以下两条已于 2026-10-12 实测确认：

- **结构化输出**：LangChain 的默认路径在 DeepSeek 上不可用。
  - 默认方法走 `response_format` 的 `json_schema` 类型，DeepSeek 返回
    `400 This response_format type is unavailable now`；
  - `method="function_calling"` 亦不可用，DeepSeek 返回
    `400 Thinking mode does not support this tool_choice`——`deepseek-flash` 是思考型模型，不接受被强制的 `tool_choice`。
  - 解法：统一走 `json_mode`，并把 JSON Schema 写进提示词。已封装为
    `providers/llm/structured.py` 的 `invoke_structured()`，供应商差异只集中在这一处。
- **工具调用**：同样因为思考模式不接受强制 `tool_choice`，本项目**不使用** LangChain 的自动工具绑定。
  改由 `tools` 注册表发布签名、planner 产出 `arguments`、executor 按签名校验后再调用。
- 上下文长度、并发限制与错误码各家不同，尚未逐个验证。

换供应商的目标是**低成本**，而不是**无感**。

### 持久化与检索方向（2026-10-12 确认）

**checkpointer（LangGraph 执行状态）：选用 SQLite（`langgraph-checkpoint-sqlite`）。**

- 理由：本阶段的核心验证目标是 `interrupt` 与恢复。内存 saver 无法验证"重启后能否恢复"，会造成"以为验证过了"的假象。SQLite 零运维、单文件、无需常驻服务，且由 LangChain 官方维护（位于 langgraph 主仓库内）。
- 该选择**可逆**：LangGraph 对 checkpointer 抽象良好，将来更换只需替换 saver 类与连接串，图的代码不变。
- 开发者本人只熟悉 MySQL 与 Redis，未用过 SQLite/Postgres。这不构成障碍：checkpointer 自带建表与读写，使用方不需要写任何 SQL；SQLite 是文件型数据库，概念上比 MySQL 更简单。

**已核实但暂时搁置的备选：Redis。**

- `langgraph-checkpoint-redis`（0.5.2，Redis Inc. 官方维护，2026-10-12 经 PyPI 核实）是真实可用的选项。它额外依赖 `redisvl`（Redis 向量库），可同时覆盖 **LangGraph Store（长期记忆）与向量相似度检索（RAG 存储层）**——一个后端解决三件事，这是它相对 SQLite 的实质优势，也部分回答了下方"RAG 何时引入"的问题。
- 2026-10-12 用户决定：本阶段仍先用 SQLite，原因是不愿为此引入常驻服务，后期再优化。
- **若将来切换到 Redis，必须开启 AOF 持久化**（`--appendonly yes`）。Redis 默认只开 RDB 定时快照、AOF 关闭，裸容器重启可能丢失最近的 checkpoint，会导致"重启后能否恢复"的验证**因存储配置而非代码失败**。这是最容易踩的坑。
- 附带待评估项：Redis 是内存优先的存储，承载长期中断（例如等待人工确认数天）的执行状态是否合适，需要连同 TTL 与持久化策略一起考虑。

**RAG 与向量数据库：本阶段不引入。**

- 理由：RAG 解决的是"模型不知道的事实太多、塞不进 prompt"。当前任务的瓶颈是"无法操作微信与 Steam"，属于工具能力问题，检索无法替代。当前也没有可检索的语料。
- 触发条件（出现任一再评估）：本地文件检索与总结、长期记忆、网页查询结果沉淀复用。
- 注意定位：这些在本文档 §1 中属于**后续独立 Skill/Tool 能力**，将来应作为 `tools/` 下的能力接入，**而不是先改造数据库层**。顺序反了会白做。
- 若将来 RAG 与 checkpointer 都需要 Postgres，可用 `pgvector` 扩展共用一个实例；但这只是运维层面的省事，不代表两者在架构上耦合。

**尚未解决的边界问题（待 `AGI_Server` 有设计时确认）：**

`AGI_Server` 的职责包含任务持久化（见 §2），而 checkpointer 同样会写库。当前判定两者不冲突——checkpointer 存的是**执行状态**（图的中间态、待审批的中断点，属于执行引擎的内部实现细节），`AGI_Server` 存的是**业务记录**（`Task`、`Event`、`Approval`，需要对外展示与审计）。

但将来 `AGI_Server` 若要向客户端暴露"任务当前进行到哪一步"，这两层就需要设计同步方式。现在不解决，但不要等 Server 动手时才发现。

### AGI_Agent 内部边界

```text
runtime
  └─ graph          任务状态、规划、执行、审查、回复
       ├─ schemas    Task、Plan、ToolCall、Approval、Event
       ├─ tools      内置工具与 MCP 工具注册
       ├─ providers  模型与可观测性供应商适配
       └─ policies   风险分级、权限检查、人工确认
```

核心原则：

- `graph` 决定“怎么思考”；
- `tools` 决定“能做什么”；
- `policies` 决定“允许做什么”。

三者必须保持分离。

工具注册的额外约束（2026-10-12 实测教训）：

- `ToolSpec` **必须公布参数签名**（一个 Pydantic 模型）。不公布签名，planner 只能猜参数名，
  会在重规划里反复猜测并最终失败——实测中出现过连续三轮分别猜 `recipient`、`contact`、`message`。
- 风险等级由**注册表**定义、由 `policies` 赋值，**不采信模型在计划里自报的 `risk`**。
- executor 在调用前按签名校验参数，让失败信息具体到字段名，而不是抛出无意义的 `TypeError`。
- **MCP 工具是同一个注册表的一种"来源"，不是另一套体系。** 将来接入 MCP Server 时，
  必须把 MCP 发现的工具**登记进 `tools` 注册表**（补齐 `ToolSpec` 的签名与风险等级），
  使 `planner` 看到的始终是一份统一的工具清单。**禁止为 MCP 另建平行的工具调用路径**——
  那会让 MCP 工具绕过 `policies` 的风险判定与人工确认，是最危险的一种重构。
- **接入 MCP 的时机：某能力已有成熟可信的第三方实现时。** 像读文件这种 20 行就能手写的，
  不值得为它引入 MCP Client 层（子进程管理、协议握手、Node 运行时、跨进程调试）。
  MCP 层应当被一个真实需求一次性逼出来并认真设计，而不是被小需求顺手带出来。
- 关于 Claude Code 的 Skill：那是 Claude Code 自身的能力，**不是本 Agent 运行时的能力**。
  本 Agent 是独立进程，不加载 Skill。除非将来明确决定让 Agent 反向驱动 Claude Code，
  否则不要把它当作工具来源。

## 5. 当前实际进度

### 已完成

`AGI_Agent` 已使用以下命令初始化为 uv Python 包项目：

```powershell
uv init --package --name agi-agent --python 3.12
```

已存在的重要文件和目录：

```text
AGI_Agent/
├── pyproject.toml
├── .python-version
├── README.md
├── docs/
│   ├── architecture.md
│   └── decisions/README.md
├── src/agi_agent/
│   ├── schemas/
│   ├── graph/nodes/
│   ├── tools/builtin/
│   ├── tools/mcp/
│   ├── providers/llm/
│   ├── providers/observability/
│   └── policies/
└── tests/
    ├── unit/
    └── integration/
```

`AGI_Agent/docs/architecture.md` 记录了当前 Agent 的目录职责、任务生命周期和近期实现顺序。

补充核实（以实际文件状态为准）：`uv.lock` 与 `.venv` 在 `uv init --package` 时即已生成（早期本文档曾误记为"尚未生成"）。此外 `AGI_Agent/` 下存在 `.idea/`（PyCharm 配置），当前未被 `.gitignore` 忽略。

**最小闭环已完成（2026-10-12）**：`uv run agi-agent "..."` 已真实调用 DeepSeek 并返回回复。

已落地的文件：

```text
AGI_Agent/
├── config.toml               # 角色 -> 模型绑定（可提交，不含密钥）
├── .env                      # 仅含 DEEPSEEK_API_KEY，已被 .gitignore 忽略
└── src/agi_agent/
    ├── config.py             # ModelSpec / LLMSettings / Settings，从 config.toml 读取
    ├── cli.py                # 入口：把一条消息交给 conversation 角色并打印回复
    ├── schemas/              # 稳定数据契约，不含任何供应商代码
    │   ├── common.py         # Role, RiskLevel, SchemaModel, new_id, utc_now
    │   ├── task.py           # Task, TaskKind, TaskStatus
    │   ├── plan.py           # Plan, PlanStep
    │   ├── tool_call.py      # ToolCall, ToolCallStatus
    │   ├── approval.py       # Approval, ApprovalDecision
    │   └── event.py          # Event, EventType
    └── providers/llm/
        ├── base.py           # 供应商注册表 + get_model(role) 工厂
        └── __init__.py
```

其他改动：`pyproject.toml` 入口改为 `agi_agent.cli:main`；`agi_agent/__init__.py` 中原有的 `main()` 已移除；`.gitignore` 已加入 `.env` 与 `.idea/`（均已实测 `git check-ignore` 生效）。

`Role` 定义在 `schemas/common.py`，由 `providers` 反向依赖——角色是领域概念，不是供应商细节。

数据契约的关键设计：`PlanStep`（意图）与 `ToolCall`（实际执行记录）分开保存，使 reviewer 能审查策划本身而不只是执行结果；`Event` 设为 frozen，作为不可篡改的审计轨迹；所有契约 `extra="forbid"`，未声明字段直接报错。

**第一个最小图已完成（2026-10-12）**：`uv run agi-agent "..."` 走完整多 Agent 图，支持人工确认中断与恢复。

新增模块：

```text
src/agi_agent/
├── graph/
│   ├── builder.py       # 装配：triage -> 闲聊 / 任务(plan -> authorize -> execute -> review 回环)
│   ├── checkpoint.py    # SQLite checkpointer 装配 + 序列化白名单
│   ├── state.py         # AgentState
│   └── nodes/           # triage, conversation, planner, authorize, executor, reviewer, respond
├── policies/risk.py     # 风险赋值、整批审批判定、审批摘要
├── providers/llm/structured.py  # 跨供应商结构化输出
├── schemas/review.py    # 第六个契约：Review
└── tools/
    ├── registry.py      # 注册表：名称 -> 实现 / 参数签名 / 风险等级
    └── builtin/fake.py  # 假工具（测试替身，非真实能力）
```

关键决策：

- **风险等级由 `policies` 依据 `tools` 注册表赋值，不采信模型自报的 `risk`。** 已实测：模型谎报 `read` 会被覆盖为 `write` 并触发审批。
- 审批为**整批判定**——计划中任一步骤非只读，就中断一次并把完整计划交给用户。
- 审查不通过回到 `planner` 重新规划，`MAX_REVISIONS = 3` 封顶。
- 闲聊分支不经过审批，一次 `conversation` 调用即返回。

已实测四条路径：闲聊回复、任务获批后执行成功、用户拒绝后正常终止、只读任务自动放行（不弹确认）。

### 尚未完成 / 未执行

- `langgraph` 尚未安装；等第一个真实可加载的图出现时再装。
- 尚未创建 `langgraph.json`：必须等到存在真实且可加载的图之后再创建，避免保留无效配置。
- 尚未编写任何测试；`pytest` 与 `Ruff` 未安装。
- 多 Agent 图、工具注册表、MCP Client、`policies` 审批链路均未实现；`graph/`、`tools/`、`policies/` 仍是空骨架。
- 尚未编写 `.gitattributes`；当前换行符依赖 `core.autocrlf`，跨平台协作前应固定。

### 仓库结构（2026-10-12 起改为 Monorepo）

`E:\AGI_Project` 现在是唯一的 Git 仓库，包含 `AGI_Agent`、`AGI_Client`、`AGI_Server` 与本文档。

- 分支：`main`
- 远端：`origin` → `https://github.com/MakePota/AGI_Project.git`（私密仓库）
- 首次提交：`654fd4e` "chore: initialize AGI_Project monorepo (agent, client, server)"
- `.env` 与 `.idea/` 均在根 `.gitignore` 中，未进入版本库
- `AGI_Client` 与 `AGI_Server` 目前是空目录，靠 `.gitkeep` 占位

变更说明：`AGI_Agent` 原先是被 `uv init --package` 自动初始化的独立嵌套 Git 仓库，并有独立远端 `MakePota/AGI_Agent`。经用户明确授权，该嵌套 `.git` 已删除且**历史不保留**，改为在 `E:\AGI_Project` 根目录重建为单一 Monorepo。旧的 `MakePota/AGI_Agent` 仓库由用户自行处理。

依赖结构上刻意**未**引入 uv workspace：`AGI_Client` 未必是 Python 项目，且 `AGI_Server` 尚无代码。等 server 有真实 Python 代码后再讨论是否共享 lockfile。

## 6. 当前任务

### 上一阶段：接入 DeepSeek 并建立可插拔 LLM 层 —— 已完成（2026-10-12）

最小闭环已跑通：`uv run agi-agent "你好，请用一句话介绍自己"` 真实调用 DeepSeek 并返回回复。

已核实的信息：

| 项目 | 已确认值 |
| --- | --- |
| 产品显示名 | `DeepSeek-V4.1-Flash` |
| DeepSeek API 模型 ID | `deepseek-flash` |
| OpenAI 兼容 Base URL | `https://api.deepseek.com` |
| API Key 环境变量 | `DEEPSEEK_API_KEY` |

已安装依赖：`langchain-core`、`langchain-openai`、`pydantic-settings`、`python-dotenv`。

后续按触发条件再装：`langgraph`（写第一个图时）、`langfuse`（出现可观察对象时）、`langchain-anthropic`（接入 Claude 时）、`pytest` 与 `Ruff`（开始写测试时）。

明确不装：`langchain`（meta 包，本项目用 LangGraph 编排，不需要）、`langchain-deepseek`（对 `deepseek-flash` 的覆盖未经核实，`ChatOpenAI` + `base_url` 路径已核实）。

### 安全事项（重要）

- `.env` 中的 DeepSeek API Key 曾在对话中明文传递过，**建议尽快在 DeepSeek 控制台轮换该 Key**。
- `.env` 已加入 `.gitignore` 并实测生效；`.idea/` 尚未忽略。
- 密钥最终要迁到服务端密钥库。届时只需改变 `ModelSpec.api_key_env` 的解析来源，角色与模型配置不受影响。

### 下一步

1. ✅ 核心数据模型（`schemas/`）
2. ✅ 第一个最小图（`graph/`），含 `interrupt` 人工确认与恢复
3. 把假工具换成真实工具，建议顺序：
   - 先做一个**只读**工具（例如真实的 `local.read_file`，必要时限定可访问目录）。它不需审批，能立刻验证"审查通过"的完整成功路径——**目前所有只读任务都会因为假工具返回占位符而被 reviewer 正确驳回**，这条路径还没被真正走通。
   - 再做 Steam（走 Web，低风险、易验证）。
   - 最后做微信（个人版 UI 自动化，高风险且脆弱）。
4. 补测试（`pytest`）与 `Ruff`。

关于 WeChat / Steam 的成熟 MCP Server 与开源实现调研，用户已明确暂缓，实施前需先做。

## 7. 新 AI 工具的启动与交接规则

新的 AI 工具或新会话开始时，应按以下顺序工作：

1. 阅读本文件 `Memory.md`。
2. 检查目标目录的实际文件、Git 状态、依赖锁文件和测试结果，不能仅依赖本文档。
3. 在实施新功能前，先讨论方案、检索 GitHub 上的可复用 Skill/MCP/成熟实现，并等待用户确认。
4. 完成有意义的阶段后，更新本文件的“当前实际进度”“当前任务”和“待执行事项”。

当状态发生变化时，应更新本文档，但不要把它写成逐轮聊天记录；它应保持简洁、准确，并突出下一位开发者或 AI 可以立即继续的内容。
