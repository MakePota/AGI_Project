# 0014 拆分为 Server 与 Agent，Agent 提供内部服务 API

- 日期：2026-10-13
- 状态：已采用

## 背景

到 2026-10-13 为止，`AGI_Agent` 只有一条使用入口：`uv run agi-agent "…"`。能力已经攒到五个（Steam 查价 / 搜索 / 加购 / 移除、图像理解），但每次都要在终端里敲命令。

用户因此决定开始做 `AGI_Client` 与 `AGI_Server`——这两个目录从建仓库第一天起就是为它们预留的（见 `Memory.md` §2）。用户同时定下了形态：

- **`AGI_Server` 管用户状态与数据库 CRUD，`AGI_Agent` 管智能体能力，两者是独立服务，Server 通过 API 调用 Agent。**
- 后端用 Java + Spring Boot（想要 Java 技术栈的真实项目实践，且 Server 这一层正是企业 Java 最典型的工作面）。

这里有一处**与既有文档冲突**：`Memory.md` §2 与 `AGI_Agent/docs/architecture.md` 都曾写明

> `AGI_Agent` 当前**不承担 HTTP API**、用户认证或客户端页面，避免与 `AGI_Server`、`AGI_Client` 职责重叠。

按新方案，Agent 必须对外提供 API 才能被 Java 调用。这条边界**不是被悄悄破坏了，而是需要被重新划定**。

## 决策

1. **三个可独立运行的部分**：`AGI_Client`（界面）、`AGI_Server`（用户与业务数据、对外 API）、`AGI_Agent`（智能体引擎）。前两者与后者之间只通过网络通信。

2. **`AGI_Agent` 对外提供「内部服务 API」**，边界表述改为：

   > `AGI_Agent` 提供**内部服务 API**（无认证、无界面，供 `AGI_Server` 调用）；
   > 仍然**不**承担用户认证、不提供客户端页面。

   用 FastAPI 手写窄服务，**不复用**项目里任何 HTTP 层的既有抽象（本来也没有）。

3. **权威数据只有一份，且分属不同层**：

   | 数据 | 权威在哪 |
   | --- | --- |
   | 执行状态（计划、工具调用、审批记录） | **Agent 的 checkpointer** |
   | 业务数据（用户、任务列表、用户可见状态） | **Server 的数据库** |

   Server 存的是**镜像**。两者不一致时**以 Agent 为准**，且 **Server 绝不自己推断任务状态**——它只记录 Agent 告诉它的。

## 被否决的方案

- **Agent 不提供 API，Server 用子进程调 CLI**。省掉一层，但：状态留在子进程里、流式输出要解析终端文本、审批要伪造 stdin，任何一处出问题都极难诊断。等于把 CLI 当成协议用，而 CLI 从没为这个用途设计过。

- **让 `AGI_Agent` 也用 Java 重写**。智能体逻辑（LangGraph 编排、Playwright 驱动的 Steam 工具、Pydantic 数据契约、千问/DeepSeek 适配）全部在 Python，重写等于重做整个项目。Java 的价值在 Server 层——用户、数据库、网关——而不是这一层。

- **用 LangGraph 官方 Server 作为 Agent 的对外形态**。它确实白送「中断 + 流式 + 线程管理」的水管，但代价是项目的运行时被第三方约定接管、`AGI_Server` 会退化成薄壳，而且 `langgraph dev` 是**开发**服务器，生产部署要上它们的云或自建 Docker。本项目在 ADR 0005、ADR 0006 里已经两次选择「手写窄工具」而非套框架，此处保持一致。细节见 ADR 0016。

- **维持旧边界（Agent 不提供 HTTP）**。那等于放弃「Server 与 Agent 分离」这个形态本身，也就放弃了用户想要的服务化。

## 后果

- **故障面变大**：多了一层进程与网络。必须能区分「Agent 服务挂了」与「任务本身失败了」——前者是 `PreconditionError` 那一类（停下让人修环境，ADR 0011），后者才是正常的任务结局。这个区分要在 API 的错误设计里体现出来。

- **Agent 的 API 成为一个跨语言契约**，因此必须窄、稳定、带版本前缀。它不是「把内部模型暴露出去」，具体见 [ADR 0015](0015-interrupt-as-queryable-state.md)。

- **文档里那句旧话必须同步**，包括 `Memory.md` §2 与 `AGI_Agent/docs/architecture.md`。过期的边界描述比没有更糟：下一个人会以为代码写错了。

- 执行状态仍然只能由**单进程**持有（SQLite checkpointer 无并发控制，见 `graph/checkpoint.py` 的注释）。因此第一版任务**串行执行**，这是本次接受的硬约束，不是疏漏。

- **重新审视的条件**：若将来 Agent 需要多个实例并行，checkpointer 必须换成 Redis/Postgres（ADR 0004 已铺垫），届时本决策的「单进程」前提作废。
