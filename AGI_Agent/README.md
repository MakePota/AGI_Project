# AGI Agent

`AGI_Agent` 是 AGI Project 的智能体执行引擎。它只负责任务理解、计划、工具编排、执行状态与安全策略；不承担用户认证、HTTP API 或客户端界面。

## 系统边界

```text
AGI_Client -> AGI_Server -> AGI_Agent -> Models / MCP Servers / Local Tools
```

- `AGI_Client`：用户交互与执行过程展示。
- `AGI_Server`：用户、权限、任务持久化和对外 API。
- `AGI_Agent`：根据任务规划并执行经过授权的工具。

## 当前状态

当前仅完成 uv 项目初始化和目录骨架，尚未安装 LangGraph、模型 SDK、MCP SDK 或其他运行时依赖。

## 开发约定

- Python 版本固定为 3.12，由 uv 管理。
- 每次引入框架、模型供应商、MCP Server 或工具前，先在 GitHub 检查可复用实现并确认方案。
- 高风险动作必须经过 `policies` 中的审批策略；不得绕过人工确认。

详细设计见 [架构说明](docs/architecture.md)。
