"""respond 节点：把状态整理成最终回复。不调用模型。"""

from __future__ import annotations

from agi_agent.graph.state import AgentState
from agi_agent.schemas import ApprovalDecision, TaskStatus, ToolCallStatus


def _uncertain_reply(state: AgentState) -> str:
    """结果不确定时的回复：说清「哪些动作可能已经生效」，并明确不要再试。"""
    lines = []
    for call in state["tool_calls"]:
        if call.status is ToolCallStatus.OUTCOME_UNKNOWN:
            lines.append(f"  · {call.tool}\n    {call.error}")
    return (
        "已停止执行，需要你人工确认。\n\n"
        "以下操作**可能已经生效，但无法确认结果**，因此不会自动重试——"
        "重试会在真实世界里再叠加一次同样的动作：\n"
        + "\n".join(lines)
        + "\n\n请先到实际系统里核对该操作的真实结果，确认后再决定下一步。"
    )


def respond(state: AgentState) -> dict:
    approval = state.get("approval")
    review = state.get("review")
    existing = state.get("reply")

    if approval is not None and approval.decision is ApprovalDecision.REJECTED:
        reply = f"已取消执行——你拒绝了这次操作。原因：{approval.reason or '未说明'}"
        status = TaskStatus.CANCELLED
    elif any(
        c.status is ToolCallStatus.OUTCOME_UNKNOWN for c in state.get("tool_calls", [])
    ):
        # 从 executor 直接跳过来（route_after_execute -> halt），没经过 reviewer。
        reply, status = _uncertain_reply(state), TaskStatus.NEEDS_HUMAN
    elif review is None and existing:
        # 闲聊分支：conversation 已经产出了回复，这里只补上任务状态。
        reply, status = existing, TaskStatus.COMPLETED
    elif review is not None and review.passed:
        done = "\n".join(
            f"  {i + 1}. {c.tool} -> {c.result}" for i, c in enumerate(state["tool_calls"])
        )
        reply = f"任务已完成：\n{done}" if done else "任务已完成，但没有产生执行记录。"
        status = TaskStatus.COMPLETED
    elif review is not None:
        reply = (
            f"任务未完成。已尝试 {state['revision']} 轮规划，仍被审查驳回：\n"
            f"  {review.reason}"
        )
        status = TaskStatus.FAILED
    else:
        reply, status = existing or "任务未能完成。", TaskStatus.FAILED

    return {
        "reply": reply,
        "task": state["task"].model_copy(update={"status": status, "result": reply}),
    }
