# AGI Agent

`AGI_Agent` 是 AGI Project 的智能体执行引擎。它负责任务理解、计划、工具编排、
执行状态与安全策略，并对外提供**内部服务 API** 供 `AGI_Server` 调用。
它**不**承担用户认证，也**不**提供客户端页面。

> 边界在 2026-10-13 改过一次：原先写的是「不承担 HTTP API」，服务化之后那条不成立了
> （Java 侧必须调它）。改动的理由与新的边界见 [ADR 0014](docs/decisions/0014-split-server-and-agent.md)。

## 系统边界

```text
AGI_Client -> AGI_Server -> AGI_Agent -> Models / MCP Servers / Local Tools
```

- `AGI_Client`：用户交互与执行过程展示。
- `AGI_Server`：用户、任务持久化和对外 API。
- `AGI_Agent`：根据任务规划并执行经过授权的工具；对外只提供内部服务 API。

**权威数据分属不同层**：执行状态（计划、工具调用、审批记录）权威在**本项目的
checkpointer**；业务数据（用户、任务列表）在 `AGI_Server` 的数据库。
后者存的是镜像，不一致时以这里为准。

## 当前状态

已完成并**实测跑通**：多 Agent 图（含人工确认中断与恢复）、本地只读文件工具、
Steam 工具（搜索 / 查价 / 购物车增删查，首个 `WRITE` 风险能力）、
图像理解工具（千问，档位由配置决定）、对外 HTTP 服务（4 + 1 个端点）、
161 个单元测试。

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
| 整个仓库的入口、怎么把三层都跑起来 | 仓库根目录的 `README.md` |

## 运行

两条入口：命令行（调试用）和 HTTP 服务（给 `AGI_Server` 调用）。

```bash
# 命令行：跑一个任务
uv run agi-agent "你好，请用一句话介绍自己"

# 内部服务 API（监听 127.0.0.1:8123，默认端口可用 AGI_API_PORT 改）
uv run agi-agent-api
```

```bash
uv run pytest               # 161 个单测，不消耗 API 额度
uv run ruff check .         # 静态检查；规则集与取舍见 pyproject.toml 注释
```

**验证脚本**（都在 `scripts/`）：

| 脚本 | 验什么 | 前置条件 |
| --- | --- | --- |
| `steam_login.py` | Steam 人工登录一次，并保存会话状态（`--verify` 只检查） | 代理开着 |
| `smoke_api.py` | 接口层端到端：建任务 → 等审批 → 提交 → 跑完 | `agi-agent-api` 在跑 |
| `ui_smoke.py` | 界面层端到端（真实浏览器，含"发图看模型能否读出数字"） | 四个服务都在跑 |

`smoke_api.py` 可以对着 `AGI_Server` 跑（`--base-url http://127.0.0.1:8090/api`）——
两个服务的接口形状一致，同一套脚本能验两边。
