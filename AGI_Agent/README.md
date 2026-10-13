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

已完成并**实测跑通**：多 Agent 图（含人工确认中断与恢复）、本地只读文件工具、
Steam 工具（搜索 / 查价 / 购物车增删查，首个 `WRITE` 风险能力）、92 个单元测试。

完整状态见仓库根目录的 `Memory.md`。**本文件不重复记录进度**——进度会变，
写在两处必然有一处过期。

## 开发约定

- Python 版本固定为 3.12，由 uv 管理。
- 每次引入框架、模型供应商、MCP Server 或工具前，先在 GitHub 检查可复用实现并确认方案。
- 高风险动作必须经过 `policies` 中的审批策略；不得绕过人工确认。
- 代码注释用中文，写到「只读注释就能看懂并修改」的程度。

## 文档

| 想了解 | 去哪 |
| --- | --- |
| 整体架构、技术栈、目录职责 | [docs/architecture.md](docs/architecture.md) |
| 为什么这样选（ADR） | [docs/decisions/](docs/decisions/README.md) |
| 某个功能怎么运转、要改它先看哪里 | [docs/features/](docs/features/README.md) |
| 项目当前做到哪一步 | 仓库根目录的 `Memory.md` |
| 交接与硬约束 | 仓库根目录的 `CLAUDE.md`、`Memory.md` §3 |

## 运行

```bash
uv run agi-agent "你好，请用一句话介绍自己"
uv run pytest
uv run ruff check .        # 静态检查；规则集与取舍见 pyproject.toml 注释
```
