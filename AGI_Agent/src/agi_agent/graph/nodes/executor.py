"""executor 节点：按计划执行工具步骤。

本阶段注册的是假工具，不触碰真实外部系统。单个步骤失败不中断整个计划，
失败信息写入 ``ToolCall``，交给 reviewer 判定。
"""

from __future__ import annotations

from agi_agent.graph.state import AgentState
from agi_agent.schemas import TaskStatus, ToolCall, ToolCallStatus
from agi_agent.tools import get as get_tool


def executor(state: AgentState) -> dict:
    plan = state["plan"]
    calls: list[ToolCall] = []

    for step in plan.steps:
        call = ToolCall(
            task_id=state["task"].id,
            step_index=step.index,
            tool=step.tool or "internal.reasoning",
            arguments=step.arguments,
            risk=step.risk,
        )

        if step.tool is None:
            call.status = ToolCallStatus.SUCCEEDED
            call.result = "（纯推理步骤，无外部调用）"
        else:
            spec = get_tool(step.tool)
            try:
                # 先按声明的签名校验参数，再调用；让失败信息具体到字段名。
                call.result = spec.func(**spec.validate(step.arguments))
                call.status = ToolCallStatus.SUCCEEDED
            except Exception as exc:  # 工具失败要留痕，不能吞掉
                call.status = ToolCallStatus.FAILED
                call.error = f"{type(exc).__name__}: {exc}"

        calls.append(call)

    return {
        "tool_calls": calls,
        "task": state["task"].model_copy(update={"status": TaskStatus.REVIEWING}),
    }
