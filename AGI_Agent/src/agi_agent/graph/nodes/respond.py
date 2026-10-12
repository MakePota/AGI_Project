"""respond 节点：把状态整理成最终回复。不调用模型。"""

from __future__ import annotations

from agi_agent.graph.state import AgentState
from agi_agent.schemas import ApprovalDecision, TaskStatus


def respond(state: AgentState) -> dict:
    approval = state.get("approval")
    review = state.get("review")
    existing = state.get("reply")

    if approval is not None and approval.decision is ApprovalDecision.REJECTED:
        reply = f"已取消执行——你拒绝了这次操作。原因：{approval.reason or '未说明'}"
        status = TaskStatus.CANCELLED
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
