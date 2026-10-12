# 功能：Supervisor 多 Agent 图

- 状态：已完成（2026-10-12）
- 涉及文件：`src/agi_agent/graph/`（`builder.py`、`state.py`、`checkpoint.py`、`nodes/`）、
  `src/agi_agent/policies/risk.py`、`src/agi_agent/tools/errors.py`、`schemas/review.py`
- 相关决策：[0001 Supervisor 模式](../decisions/0001-supervisor-multi-agent.md)、
  [0004 SQLite checkpointer](../decisions/0004-sqlite-checkpointer.md)、
  [0007 工具签名与风险](../decisions/0007-tool-registry-signature-and-risk.md)、
  [0010 结果不确定不得重试](../decisions/0010-no-retry-on-uncertain-write.md)、
  [0011 前提不满足时停下](../decisions/0011-halt-on-unmet-precondition.md)

## 要解决什么问题

让一条用户输入走完「判断是闲聊还是任务 → 规划 → 人工审批 → 执行 → 审查 → 回复」，
并且**在中途停下等待人确认后还能恢复**。这是整个系统的主干，其余模块都是挂在这条链上的。

## 最终怎么做

```text
triage（分诊）
  ├─ 闲聊 ─> conversation ─> respond
  └─ 任务 ─> planner ─> authorize ─┬─ 拒绝 ─> respond（正常终止）
                                    └─ 批准 ─> executor ─┬─ 不确定/前提不满足 ─> respond（停下问人）
                                                         └─ 其余 ─> reviewer ─┬─ 通过 ─> respond
                                                                              └─ 不通过 ─> planner（最多 3 轮）
```

几个关键机制：

- **审批是「整批」判定**：计划中只要有**任一步骤**非只读，就中断一次，
  把**完整计划**交给用户。不是每个危险步骤单独弹一次。
- **中断点是 `authorize` 节点**，用 LangGraph 的 `interrupt` 实现；
  恢复依赖 checkpointer 把执行状态落盘（ADR 0004）。
- **`PlanStep` 与 `ToolCall` 分开保存**：前者是「打算做什么」（意图），后者是「实际做了什么」
  （执行记录）。这样 reviewer 审查的是**计划本身**，而不只是执行结果。
- **`Event` 设为 frozen**，作为不可篡改的审计轨迹。
- **两类结局会直接停下，不经过审查也不重规划**（`route_after_execute`）：
  - **结果不确定**：重规划就是重试，而重试一个可能已生效的写操作会叠加上一次动作。
    → [ADR 0010](../decisions/0010-no-retry-on-uncertain-write.md)
  - **前提条件不满足**（代理没开、登录过期）：重规划一百次也修不好，只有人能修。
    → [ADR 0011](../decisions/0011-halt-on-unmet-precondition.md)

  两类都会以 `NEEDS_HUMAN` 结束，但 `respond` 给的措辞**必须不同**——
  一个要用户"去核实是否已生效"，一个要用户"去修好环境再重来"。

## 为什么这么做 / 被否决的方案

- **为什么审批按整批而不是逐步**：逐步弹确认在长计划里会变成连续弹窗，用户会条件反射地点同意，
  反而失去确认的意义。整批展示让用户能看到计划的**全貌**再决定。
- **为什么审查不通过要回到 `planner` 而不是让 `executor` 重试**：`executor` 重试只能修正
  「操作失误」，修正不了「计划本身就错了」。而实测中出现的失败几乎都是后者
  （例如 planner 猜错工具参数名）。
- **为什么必须有 `MAX_REVISIONS`**：`planner` 与 `reviewer` 可以互相打回，
  没有封顶就是死循环，而且每轮都在真实花钱。

## 已知局限与坑

- **纯闲聊也要调用一次最强模型**（分诊在 planner）。已知代价，接受，理由见 ADR 0001。
- **checkpointer 是 SQLite 单文件，不支持多副本共享**。当前只跑单进程，够用。
- 全部跑在本地单进程，没有并发控制——同时跑两个任务会争用同一个 `checkpoints.db`。

## 要改它先看哪里

1. **改流程走向** → `graph/builder.py`，节点之间的连线和条件分支都在那里。
2. **改某个节点的行为** → `graph/nodes/` 下对应的单个文件，一个节点一个文件。
3. **改状态里存什么** → `graph/state.py` 的 `AgentState`；
   如果加了新的数据契约，记得同步 `graph/checkpoint.py` 里的**序列化白名单**，
   否则恢复时会反序列化失败。
4. **改风险判定/审批摘要怎么生成** → `policies/risk.py`。
5. **改"停下"的两类结局** → `src/agi_agent/tools/errors.py`（错误语义）、
   `src/agi_agent/graph/nodes/executor.py`（中止剩余步骤）、
   `src/agi_agent/graph/builder.py` 的 `route_after_execute`、
   `src/agi_agent/graph/nodes/respond.py`（如何告诉人）。**四处要一起改。**
6. **改进度输出** → `src/agi_agent/cli.py` 的 `_drive`/`_report`，
   以及节点里的 `get_stream_writer()` 调用（见 `executor.py` 的 `_progress`）。
   **长耗时工具必须能在开始前推一行进度**，否则用户会当成卡死。
7. **改完之后**：`tests/unit/test_graph_routing.py` 与 `tests/unit/test_uncertain_outcome.py`
   覆盖路由、重规划上限与两类中断，不消耗 API 额度，改流程时先跑它们。
