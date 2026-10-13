# AGI Agent 架构

## 目标

将用户任务转换为可审计、可中断、可恢复的执行流程，并通过统一的工具边界接入本地能力与 MCP Server。

## 分层

```text
api                   对外 HTTP 接口：任务、事件流、图片上传（唯一面向进程外的入口）
  └─ graph            任务状态、规划、执行、审查、回复
       ├─ schemas     Task、Plan、ToolCall、Approval、Event
       ├─ tools       内置工具与 MCP 工具注册
       ├─ providers   模型与可观测性供应商适配
       └─ policies    允许、确认、拒绝的安全决策
```

## 目录职责

| 目录 | 责任 |
| --- | --- |
| `api/` | 对外 HTTP 接口（FastAPI）。含串行任务运行器、事件缓冲、图片落盘。**它把「中断」落成一个可查询的状态**（ADR 0015），这是整套服务化最要紧的一处设计。不含业务规则——那些在 `graph` / `tools` / `policies`。 |
| `schemas/` | 稳定的数据契约，不包含供应商代码。所有契约 `extra="forbid"`，未声明字段直接报错。 |
| `graph/` | LangGraph 工作流定义；不直接写入文件或调用外部服务。 |
| `tools/` | 工具元数据、执行包装和 MCP Client 适配。**注意**：自 ADR 0012 起，工具也可以直接调用 `providers` 取模型（图像理解就是这么做的）——看到这条依赖边不要当成笔误，它没有绕过 `policies`。 |
| `providers/` | 模型、追踪等第三方 SDK 的隔离层。 |
| `policies/` | 风险分级、权限检查、人工确认中断。 |

**`api` 的存在改变了这个项目的边界**：它原先完全不提供 HTTP，现在提供一个不含认证、
不含界面的**内部服务 API**，供 `AGI_Server` 调用（ADR 0014）。`images.py` 是
上传与读图共用的图片规则——**同一套判断只能有一份**，否则会出现"上传时接受、
调用时被拒"这种自相矛盾的行为（ADR 0017）。

**核心原则：三者必须保持分离。**

- `graph` 决定「怎么思考」
- `tools` 决定「能做什么」
- `policies` 决定「允许做什么」

## 技术栈

| 领域 | 选型 | 说明 |
| --- | --- | --- |
| 项目管理 | `uv` | |
| Python | 3.12 | |
| 工作流编排 | `LangGraph` | 执行状态用 SQLite 持久化，见 [ADR 0004](decisions/0004-sqlite-checkpointer.md) |
| 模型抽象 | `LangChain Core` 的 `BaseChatModel` | 唯一模型接口；供应商适配见 [ADR 0002](decisions/0002-pluginable-llm.md) |
| 图像理解 | 千问 `qwen3.8-flash` / `qwen3.8-max`（DashScope 兼容端点） | **只由图像工具调用**，不占用任何 agent 角色槽位；见 [ADR 0012](decisions/0012-vision-via-dashscope.md) |
| 数据建模与配置 | `Pydantic`、`pydantic-settings` | 角色 → 模型绑定写在 `config.toml` |
| 浏览器自动化 | `Playwright` | 见 [ADR 0005](decisions/0005-playwright-for-browser-automation.md) |
| 对外接口 | `FastAPI` + `uvicorn` | 内部服务 API，供 `AGI_Server` 调用；**不含认证、不含界面**（[ADR 0014](decisions/0014-split-server-and-agent.md)）。SSE 用 Starlette 自带的 `StreamingResponse` 手写，不引额外依赖 |
| 测试 | `pytest` | `tests/unit/` 不消耗 API 额度 |
| 代码质量 | `Ruff` | 已装为 dev 依赖，`uv run ruff check .`。规则集取舍（含为何关掉中文标点误报）写在 `pyproject.toml` 注释里；**只做检查、不做格式化** |
| 跨工具协议 | MCP Client | **尚未接入**，时机见 [ADR 0006](decisions/0006-external-capability-policy.md) |
| 可观测性 | `Langfuse` | **尚未接入**，等出现值得观察的对象再引入 |

## 任务生命周期

```text
receive -> understand -> plan -> authorize -> execute -> review -> respond
                              ^                  |
                              |---- interrupt ---|
```

`authorize` 是不可省略的边界：发送消息、修改文件、登录账号、下单和支付必须由策略层判定，
并在需要时返回人工确认请求。

审批为**整批判定**——计划中任一步骤非只读，就中断一次并把完整计划交给用户。

## 文档地图

本文件回答「整体长什么样」。其余两类知识各有归属：

| 想了解 | 去哪 |
| --- | --- |
| 为什么引入某个依赖、为什么否掉某个方案 | [`decisions/`](decisions/README.md)（ADR） |
| 某个功能怎么运转、要改它先看哪里 | [`features/`](features/README.md) |
| 项目当前做到哪一步（跨子项目） | 仓库根目录的 `Memory.md` |

## 近期实现顺序

1. ✅ 确认基础依赖与模型供应商抽象。
2. ✅ 定义任务、事件、审批等核心数据模型（`schemas/`）。
3. ✅ 完成首个 LangGraph 工作流与单元测试，含 `interrupt` 人工确认与恢复。
4. ✅ 首个真实只读工具（`local.list_dir`、`local.read_file`）。
5. ✅ 首个 `WRITE` 风险工具（`steam.add_to_cart`，浏览器自动化）。
6. ✅ 图像理解（千问）：以**工具**形态接入，不占用 agent 角色槽位。
7. ✅ 对外 HTTP 服务：四个任务端点 + 图片上传。核心是**把「中断」落成一个可查询的状态**
   （[ADR 0015](decisions/0015-interrupt-as-queryable-state.md)），
   以及**任务串行执行**（checkpointer 不支持并发）。图片上传见
   [ADR 0017](decisions/0017-image-upload-without-object-storage.md)。
8. ⬜ 微信（个人版 UI 自动化）：**已调研并决定不做**，理由见
   [ADR 0013](decisions/0013-no-wechat-automation.md)。
9. ⬜ 通过 MCP Client 接入首个经过审查的外部工具（**等真实需求出现再做**，见 ADR 0006）。

`langgraph.json` **仍未创建**：当前有两条入口——自带的 `cli.py`（调试用）
与 `api/` 里的 FastAPI 服务（给 `AGI_Server` 调用），都没用到 LangGraph CLI / Server，
因此不需要它。将来要用 `langgraph dev` 或部署到 LangGraph Server 时再补，
避免在骨架阶段留下无效配置。
