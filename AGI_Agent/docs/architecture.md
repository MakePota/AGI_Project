# AGI Agent 架构

## 目标

将用户任务转换为可审计、可中断、可恢复的执行流程，并通过统一的工具边界接入本地能力与 MCP Server。

## 分层

```text
runtime
  └─ graph          任务状态、规划、执行、审查、回复
       ├─ schemas    Task、Plan、ToolCall、Approval、Event
       ├─ tools      内置工具与 MCP 工具注册
       ├─ providers  模型与可观测性供应商适配
       └─ policies   允许、确认、拒绝的安全决策
```

## 目录职责

| 目录 | 责任 |
| --- | --- |
| `schemas/` | 稳定的数据契约，不包含供应商代码。所有契约 `extra="forbid"`，未声明字段直接报错。 |
| `graph/` | LangGraph 工作流定义；不直接写入文件或调用外部服务。 |
| `tools/` | 工具元数据、执行包装和 MCP Client 适配。 |
| `providers/` | 模型、追踪等第三方 SDK 的隔离层。 |
| `policies/` | 风险分级、权限检查、人工确认中断。 |

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
| 数据建模与配置 | `Pydantic`、`pydantic-settings` | 角色 → 模型绑定写在 `config.toml` |
| 浏览器自动化 | `Playwright` | 见 [ADR 0005](decisions/0005-playwright-for-browser-automation.md) |
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
6. ⬜ 微信（个人版 UI 自动化）。
7. ⬜ 通过 MCP Client 接入首个经过审查的外部工具（**等真实需求出现再做**，见 ADR 0006）。

`langgraph.json` **仍未创建**：当前由自带的 `cli.py` 驱动，未使用 LangGraph CLI / Server，
因此不需要它。将来要用 `langgraph dev` 或部署到 LangGraph Server 时再补，
避免在骨架阶段留下无效配置。
