# AGI Agent 架构

## 目标

将用户任务转换为可审计、可中断、可恢复的执行流程，并通过统一的工具边界接入本地能力与 MCP Server。

## 分层

```text
runtime
  └─ graph          任务状态、规划、执行、审查、回复
       ├─ schemas    Task、Plan、ToolCall、Approval、Event
       ├─ tools      内置工具与 MCP 工具注册
       ├─ providers  LLM 与 Langfuse 等第三方适配
       └─ policies   允许、确认、拒绝的安全决策
```

## 目录职责

| 目录 | 责任 |
| --- | --- |
| `schemas/` | 稳定的数据契约，不包含供应商代码。 |
| `graph/` | LangGraph 工作流定义；不直接写入文件或调用外部服务。 |
| `tools/` | 工具元数据、执行包装和 MCP Client 适配。 |
| `providers/` | 模型、追踪等第三方 SDK 的隔离层。 |
| `policies/` | 风险分级、权限检查、人工确认中断。 |

## 任务生命周期

```text
receive -> understand -> plan -> authorize -> execute -> review -> respond
                              ^                  |
                              |---- interrupt ---|
```

`authorize` 是不可省略的边界：发送消息、修改文件、登录账号、下单和支付必须由策略层判定，并在需要时返回人工确认请求。

## 近期实现顺序

1. ✅ 确认基础依赖与模型供应商抽象。
2. ✅ 定义任务、事件、审批等核心数据模型（`schemas/`）。
3. 完成只读的首个 LangGraph 工作流与单元测试。
4. 通过 MCP Client 接入首个经过审查的低风险工具。

`langgraph.json` 将在实际创建可加载的图后再加入，避免在骨架阶段留下无效配置。
