"""respond 节点：把状态整理成最终回复。不调用模型。"""

from __future__ import annotations

from agi_agent.graph.state import AgentState
from agi_agent.schemas import ApprovalDecision, TaskStatus, ToolCallStatus


def _render_calls(calls: list) -> str:
    return "\n".join(f"  · {c.tool}\n    {c.error}" for c in calls)


def _artifact_lines(state: AgentState) -> str:
    """把产物清单附在最终回复后面。

    **结构化事实仍然以 ``Task.artifacts`` 为准**；这里只是让人读回复时能看到
    “生成了什么”。界面不能从这段文本里解析产物——那会把展示文案变成接口。
    """
    artifacts = state["task"].artifacts
    if not artifacts:
        return ""
    lines = "\n".join(
        f"  · {artifact.name}（{artifact.mime}，{artifact.size:,} 字节）"
        for artifact in artifacts
    )
    return f"\n\n已生成文件：\n{lines}"


def _halt_reply(state: AgentState) -> str:
    """执行被中断时的回复。

    **两类中断要说完全不同的话**，因为它们要求用户做的事不一样：
    一类是「去核实可能已经发生的事」，另一类是「去修好环境再重来」。
    说混了，用户就不知道该去核实还是该去修复。
    """
    calls = state.get("tool_calls", [])
    unknown = [c for c in calls if c.status is ToolCallStatus.OUTCOME_UNKNOWN]
    blocked = [c for c in calls if c.status is ToolCallStatus.PRECONDITION_FAILED]

    parts = []
    if unknown:
        parts.append(
            "已停止执行，需要你人工确认。\n\n"
            "以下操作**可能已经生效，但无法确认结果**，因此不会自动重试——"
            "重试会在真实世界里再叠加一次同样的动作：\n"
            f"{_render_calls(unknown)}\n\n"
            "请先到实际系统里核对该操作的真实结果，确认后再决定下一步。"
        )
    if blocked:
        parts.append(
            "已停止执行。以下步骤**确实没有执行**，而且重试也不会有用——"
            "问题出在环境或前提条件上，不是计划本身：\n"
            f"{_render_calls(blocked)}\n\n"
            "请先按上面的提示处理好环境（例如开启代理、重新登录），"
            "然后重新发起这次任务即可。"
        )
    return "\n\n".join(parts) or "执行被中断。"


def respond(state: AgentState) -> dict:
    approval = state.get("approval")
    review = state.get("review")
    existing = state.get("reply")

    _halted = {
        ToolCallStatus.OUTCOME_UNKNOWN,
        ToolCallStatus.PRECONDITION_FAILED,
    }

    if approval is not None and approval.decision is ApprovalDecision.REJECTED:
        reply = f"已取消执行——你拒绝了这次操作。原因：{approval.reason or '未说明'}"
        status = TaskStatus.CANCELLED
    elif any(c.status in _halted for c in state.get("tool_calls", [])):
        # 从 executor 直接跳过来（route_after_execute -> halt），没经过 reviewer。
        reply, status = _halt_reply(state), TaskStatus.NEEDS_HUMAN
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

    reply += _artifact_lines(state)

    return {
        "reply": reply,
        "task": state["task"].model_copy(update={"status": status, "result": reply}),
    }
