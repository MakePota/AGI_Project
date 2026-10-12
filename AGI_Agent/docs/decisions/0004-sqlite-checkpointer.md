# 0004 LangGraph 执行状态用 SQLite 持久化

- 日期：2026-10-12
- 状态：已采用

## 背景

图的执行状态（中间步骤、待审批的中断点）需要一个 checkpointer 来持久化，否则
「人工确认中断 → 等待 → 恢复」这条链路无法成立。

本阶段的核心验证目标是 **`interrupt` 与恢复**，所以存储选型直接决定了能不能验证成功。

## 决策

**选用 SQLite（`langgraph-checkpoint-sqlite`）。**

- 零运维、单文件、无需常驻服务，由 LangChain 官方维护（位于 langgraph 主仓库内）。
- 开发者只熟悉 MySQL 与 Redis，未用过 SQLite——这不构成障碍：checkpointer 自带建表与读写，
  使用方**不需要写任何 SQL**；SQLite 是文件型数据库，概念上比 MySQL 更简单。
- 该选择**可逆**：LangGraph 对 checkpointer 抽象良好，将来更换只需替换 saver 类与连接串，
  图的代码不变。

## 被否决的方案

- **内存 saver（`MemorySaver`）**：最省事，但**它无法验证「进程重启后能否恢复」**。
  用它会造成「以为验证过了」的假象——测的是恢复，实际测的是同一个进程里的内存。
  这是本项目**唯一必须排除**的选项。
- **Redis（`langgraph-checkpoint-redis`）**：真实可用（0.5.2，Redis Inc. 官方维护，
  2026-10-12 经 PyPI 核实），并且额外依赖 `redisvl`，可同时覆盖 LangGraph Store（长期记忆）
  与向量相似度检索——**一个后端解决三件事**，这是它相对 SQLite 的实质优势。
  本阶段不选的原因只有一个：**不愿为此引入常驻服务**。后期再优化。
- **Postgres**：现阶段同样属于「为一个小目标引入一个服务」。

## 后果

- **若将来切换到 Redis，必须开启 AOF 持久化（`--appendonly yes`）。**
  Redis 默认只开 RDB 定时快照、AOF 关闭，裸容器重启可能丢失最近的 checkpoint，
  会导致「重启后能否恢复」的验证**因存储配置而非代码失败**。这是最容易踩的坑。
- 附带待评估项：Redis 是内存优先的存储，用它承载**长期**中断（例如等待人工确认数天）
  的执行状态是否合适，需要连同 TTL 与持久化策略一起考虑。
- `checkpoints.db` 是运行时产物，不提交版本库。
- **尚未解决的边界问题**（等 `AGI_Server` 有设计时确认）：`AGI_Server` 的职责包含任务持久化，
  与 checkpointer 同样写库。当前判定两者不冲突——checkpointer 存的是**执行状态**（图的中间态、
  待审批的中断点，属执行引擎内部细节），`AGI_Server` 存的是**业务记录**（`Task`、`Event`、`Approval`，
  需要对外展示与审计）。但将来 `AGI_Server` 若要向客户端暴露「任务进行到哪一步」，
  这两层就需要设计同步方式。**现在不解决，但不要等 Server 动手时才发现。**
