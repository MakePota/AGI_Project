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
9. **代码注释按「用户本人看得懂」的标准写，不能只满足于「能跑」。** 注释用中文，读者是未来的用户本人和接手的 AI。除了「做了什么」，更要写清「为什么这么做」：不直观的判断、外部系统的坑（选择器依据、超时取值的来由、接口限制）、被否决的方案及其原因。目标是用户只读注释就能看懂并自行修改这段代码，不需要重新逆向一遍逻辑。
10. **改功能时必须同步改对应的文档。** 文档只写「为什么」与「怎么运转」，实现细节留在代码注释里（分工规则见 `AGI_Agent/docs/features/README.md`）。**过期的文档比没有文档更糟**——它会主动误导下一个人。判断某段知识该放哪的法则：**删掉这段代码就失效的知识写进注释，跨越多个文件才成立的知识写进文档。**

## 4. 架构与设计（已外移，此处只留索引）

> 本节原先承载全部设计论证。为了让本文件专注于「**交接**」——这是什么项目、做到哪一步——
> 设计与决策已全部外移到 `AGI_Agent/docs/`。**本文件不再重复它们，只保留指针**，
> 避免同一件事写在两处、然后其中一处过期。

| 想了解 | 去哪 |
| --- | --- |
| 项目整体架构、技术栈、目录职责 | `AGI_Agent/docs/architecture.md` |
| 为什么引入某个依赖、为什么否掉某个方案 | `AGI_Agent/docs/decisions/`（ADR，10 篇） |
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
- **写操作的失败必须分三类；结果不确定时停下问人、绝不自动重试** → ADR 0010
- `graph`（怎么思考）/ `tools`（能做什么）/ `policies`（允许做什么）三者必须分离 → `architecture.md`

## 5. 当前实际进度

> 本节只记「**做到哪一步了**」。设计理由不在这里重复，见上方 §4 的指针。

### 已完成

`AGI_Agent` 是一个可运行的 uv Python 包项目（`uv init --package --name agi-agent --python 3.12`）。

五项能力均已**实测跑通**——不是「代码写完了」，是真的跑过：

| 能力 | 状态 | 实测内容 |
| --- | --- | --- |
| 最小闭环 | ✅ | `uv run agi-agent "..."` 真实调用 DeepSeek 并返回回复 |
| 多 Agent 图 + 人工确认中断/恢复 | ✅ 四条路径 | 闲聊回复、任务获批后执行成功、用户拒绝后正常终止、只读任务自动放行 |
| 本地只读文件工具 | ✅ | `local.list_dir`、`local.read_file`（首个真实工具） |
| Steam 工具（首个 `WRITE` 风险能力） | ✅ 五个工具 | 搜索 / 查价 / 购物车列表 / 加购 / 删除；加购与删除**幂等** |
| 「结果不确定」的写操作不再被重试 | ✅ 已修复并回归 | 一次复核失败曾让购物车出现 3 份同一游戏 → ADR 0010 |
| 单元测试 | ✅ 87 个 | `uv run pytest`，不消耗任何 API 额度 |

关键入口（完整清单以实际目录为准，目录职责见 `AGI_Agent/docs/architecture.md`）：

```text
AGI_Agent/
├── config.toml                  # 角色 -> 模型绑定（可提交，不含密钥）
├── .env                         # DEEPSEEK_API_KEY、AGI_BROWSER_PROXY，已被 .gitignore 忽略
├── docs/                        # architecture.md + decisions/(ADR) + features/
├── scripts/steam_login.py       # Steam 人工登录辅助 + --verify
└── src/agi_agent/
    ├── cli.py                   # 入口
    ├── config.py                # 配置与常量读取
    ├── schemas/                 # 稳定数据契约（6 个：task/plan/tool_call/approval/event/review）
    ├── graph/                   # builder / state / checkpoint / nodes
    ├── tools/                   # registry + errors + builtin(files, steam, fake)
    ├── providers/llm/           # 供应商注册表 + structured.py
    └── policies/                # risk.py
```

假工具只剩 `wechat.send_message` 一个，属**待实现的占位**。

### 尚未完成 / 未执行

- **`Ruff` 未安装**（`langgraph`、`langgraph-checkpoint-sqlite`、`playwright` 均已安装）。
- **微信（个人版 UI 自动化）尚未开始**，功能开发的下一步就是它。详见 §6。
- **MCP Client 层有意未实现**：目前没有出现「需要一个成熟第三方 MCP Server」的真实需求。
  判据见 ADR 0006。
- `langgraph.json` 未创建；当前由自带 `cli.py` 驱动，暂不需要，理由见 `AGI_Agent/docs/architecture.md`。
- **尚未编写 `.gitattributes`**：换行符依赖 `core.autocrlf`，跨平台协作前应固定。
  已观察到实际影响：Windows 下 git 反复提示 LF→CRLF 转换警告。

### 仓库结构（2026-10-12 起改为 Monorepo）

`E:\AGI_Project` 现在是唯一的 Git 仓库，包含 `AGI_Agent`、`AGI_Client`、`AGI_Server` 与本文档。

- 分支：`main`
- 远端：`origin` → `https://github.com/MakePota/AGI_Project.git`（私密仓库）
- 首次提交：`654fd4e` "chore: initialize AGI_Project monorepo (agent, client, server)"
- 根 `.gitignore` 覆盖 `.env`、`.secrets/`、`.idea/`（均已实测 `git check-ignore` 生效）
- `AGI_Client` 与 `AGI_Server` 目前是空目录，靠 `.gitkeep` 占位

变更说明：`AGI_Agent` 原先是被 `uv init --package` 自动初始化的独立嵌套 Git 仓库，并有独立远端
`MakePota/AGI_Agent`。经用户明确授权，该嵌套 `.git` 已删除且**历史不保留**，改为在
`E:\AGI_Project` 根目录重建为单一 Monorepo。旧的 `MakePota/AGI_Agent` 仓库由用户自行处理。

依赖结构上刻意**未**引入 uv workspace：`AGI_Client` 未必是 Python 项目，且 `AGI_Server` 尚无代码。
等 server 有真实 Python 代码后再讨论是否共享 lockfile。

## 6. 当前任务

### 当前供应商：DeepSeek

| 项目 | 已确认值 |
| --- | --- |
| 产品显示名 | `DeepSeek-V4.1-Flash` |
| DeepSeek API 模型 ID | `deepseek-flash`（**思考型模型**，限制见 ADR 0003） |
| OpenAI 兼容 Base URL | `https://api.deepseek.com` |
| API Key 环境变量 | `DEEPSEEK_API_KEY` |

四个角色目前**全部**绑定它。换供应商只需改 `AGI_Agent/config.toml`（ADR 0002）。

**明确不装**：`langchain`（meta 包，本项目用 LangGraph 编排，不需要）、
`langchain-deepseek`（对 `deepseek-flash` 的覆盖未经核实，而 `ChatOpenAI` + `base_url` 路径已核实）。

后续按触发条件再装：`langfuse`（出现值得观察的对象时）、`langchain-anthropic`（接入 Claude 时）、
`Ruff`（开始做代码质量检查时）。

### 安全事项（重要）

- `.env` 中的 DeepSeek API Key 曾在对话中明文传递过，**建议尽快在 DeepSeek 控制台轮换该 Key**。
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
   现在写操作分三类结局，**结果不确定时停下问人、不进重规划**，且加购/删除**幂等**。
   完整复盘见 [ADR 0010](AGI_Agent/docs/decisions/0010-no-retry-on-uncertain-write.md)。
6. ✅ **项目知识留存方案已落地（2026-10-12）**：`AGI_Agent/docs/` 下分三块——
   `architecture.md`（整体长什么样）、`decisions/`（ADR：为什么这样选）、
   `features/`（功能怎么运转、要改它先看哪里）。本文件随之收窄为**纯交接用途**。
   `AGI_Client`、`AGI_Server` 将来各自建立自己的 `docs/`。
7. **下一步是微信（个人版 UI 自动化）**。这块除工具本身外还需单独设计：环境依赖
   （客户端需开启并已登录）、凭据管理、以及操作中途失败时如何不留半成品状态。
   实施前仍需先做守则 §2 的调研（WeChat 的 MCP Server 与开源实现）。
8. 尚未安装 `Ruff`。

> 2026-10-12 会话结束时的未提交改动：Steam 相关实现（`steam.py`、`steam_login.py`、
> `config.py` 的 `.env` 载入顺序修复、`.secrets` 黑名单、Playwright 依赖）、
> 不确定结果的处理链路（`tools/errors.py`、`executor`/`builder`/`respond`、`schemas` 新状态）、
> 文档（`AGI_Agent/docs/` 与根 `CLAUDE.md`）与 `Memory.md` 本身，**均尚未提交**。

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
