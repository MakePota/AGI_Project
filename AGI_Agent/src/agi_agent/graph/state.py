"""LangGraph 状态定义。

状态只承载数据。节点不直接访问文件、网络或第三方 SDK——
工具调用走 ``tools``，风险判定走 ``policies``。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from agi_agent.schemas import Approval, Plan, Review, Task, ToolCall


class AgentState(TypedDict):
    """图在执行过程中传递的全部数据。"""

    task: Task
    messages: Annotated[list[AnyMessage], add_messages]
    plan: Plan | None
    approval: Approval | None
    tool_calls: list[ToolCall]
    review: Review | None
    revision: int  # 已产出的计划轮次，用于限制重规划次数
    reply: str | None


def initial_state(goal: str) -> AgentState:
    """构造一次新执行的初始状态。"""
    return {
        "task": Task(goal=goal),
        "messages": [],
        "plan": None,
        "approval": None,
        "tool_calls": [],
        "review": None,
        "revision": 0,
        "reply": None,
    }
