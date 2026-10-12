"""组装 Agent 图。

结构见 docs/architecture.md 与 Memory.md §4：
triage 分诊 -> 闲聊分支 / 任务分支（plan -> authorize -> execute -> review 回环）。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agi_agent.graph.nodes.authorize import authorize
from agi_agent.graph.nodes.conversation import conversation
from agi_agent.graph.nodes.executor import executor
from agi_agent.graph.nodes.planner import planner
from agi_agent.graph.nodes.respond import respond
from agi_agent.graph.nodes.reviewer import reviewer
from agi_agent.graph.nodes.triage import triage
from agi_agent.graph.state import AgentState
from agi_agent.schemas import ApprovalDecision, TaskKind, ToolCallStatus

#: planner 与 reviewer 之间允许的最大往返次数。
#: 没有这个上限，两者会互相打回并把 API 额度烧光。
MAX_REVISIONS = 3


def route_after_triage(state: AgentState) -> str:
    return "task" if state["task"].kind is TaskKind.TASK else "chat"


def route_after_authorize(state: AgentState) -> str:
    approval = state.get("approval")
    if approval is not None and approval.decision is ApprovalDecision.REJECTED:
        return "rejected"
    return "execute"


def route_after_execute(state: AgentState) -> str:
    """结果不确定时**不进审查、不进重规划**，直接停下问人。

    这一条是安全边界，不是优化：重规划意味着重试，而重试一个结果未知的写操作
    会在真实世界里叠加动作。2026-10-12 的重复加购事故就是少了这道闸——
    工具报「失败」，planner 照常重规划，购物车里最后躺着 3 份同一个游戏。
    """
    if any(c.status is ToolCallStatus.OUTCOME_UNKNOWN for c in state["tool_calls"]):
        return "halt"
    return "review"


def route_after_review(state: AgentState) -> str:
    review = state["review"]
    if review.passed:
        return "done"
    if state["revision"] >= MAX_REVISIONS:
        return "give_up"
    return "replan"


def build_graph() -> StateGraph:
    """构造未编译的图；调用方负责 compile 并注入 checkpointer。"""
    graph = StateGraph(AgentState)

    graph.add_node("triage", triage)
    graph.add_node("conversation", conversation)
    graph.add_node("planner", planner)
    graph.add_node("authorize", authorize)
    graph.add_node("executor", executor)
    graph.add_node("reviewer", reviewer)
    graph.add_node("respond", respond)

    graph.add_edge(START, "triage")
    graph.add_conditional_edges(
        "triage", route_after_triage, {"chat": "conversation", "task": "planner"}
    )
    graph.add_edge("conversation", "respond")

    graph.add_edge("planner", "authorize")
    graph.add_conditional_edges(
        "authorize",
        route_after_authorize,
        {"execute": "executor", "rejected": "respond"},
    )

    graph.add_conditional_edges(
        "executor",
        route_after_execute,
        {"review": "reviewer", "halt": "respond"},
    )
    graph.add_conditional_edges(
        "reviewer",
        route_after_review,
        {"done": "respond", "replan": "planner", "give_up": "respond"},
    )

    graph.add_edge("respond", END)

    return graph
