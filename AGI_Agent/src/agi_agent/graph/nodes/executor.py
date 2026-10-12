"""executor 节点：按计划执行工具步骤。

单个步骤的普通失败不中断整个计划，失败信息写入 ``ToolCall``，交给 reviewer 判定。

**两类失败会当场中断计划**，因为继续走下去没有意义：

1. **结果不确定**——前面的步骤可能已经真实改变了外部世界，在状态不明的世界里
   继续执行只会让事情更难收拾（``UncertainOutcomeError``）。
2. **前提条件不满足**——代理没开、登录态过期。这类问题 planner 改多少版计划
   都修不好，只有人能修（``PreconditionError``）。

两者都交由 ``route_after_execute`` 直接送去 respond，**不进重规划**。
"""

from __future__ import annotations

from langgraph.config import get_stream_writer

from agi_agent.graph.state import AgentState
from agi_agent.schemas import TaskStatus, ToolCall, ToolCallStatus
from agi_agent.tools import PreconditionError, UncertainOutcomeError
from agi_agent.tools import get as get_tool

#: 直接中断计划的两类失败 -> 对应的 ToolCall 状态。
_HALTING = (
    (UncertainOutcomeError, ToolCallStatus.OUTCOME_UNKNOWN),
    (PreconditionError, ToolCallStatus.PRECONDITION_FAILED),
)


def _progress(message: str) -> None:
    """向流式消费方报告进度；不在流式模式下是空操作。

    一次工具调用可能要跑几十秒（开浏览器、导航、轮询复核）。没有这个，
    用户面对的是一个完全不动的窗口——会被当成卡死。
    """
    try:
        writer = get_stream_writer()
    except Exception:  # noqa: BLE001  非流式调用时拿不到 writer，属正常
        return
    writer(message)


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
            calls.append(call)
            continue

        spec = get_tool(step.tool)
        _progress(f"  → 正在调用 {step.tool} …")
        try:
            # 先按声明的签名校验参数，再调用；让失败信息具体到字段名。
            call.result = spec.func(**spec.validate(step.arguments))
            call.status = ToolCallStatus.SUCCEEDED
        except Exception as exc:  # noqa: BLE001  工具失败要留痕，不能吞掉
            # 顺序要紧：两个中断类都是 ToolError 的子类，必须排在兜底之前。
            for error_type, status in _HALTING:
                if isinstance(exc, error_type):
                    call.status = status
                    call.error = str(exc)
                    calls.append(call)
                    return {
                        "tool_calls": calls,
                        "task": state["task"].model_copy(
                            update={"status": TaskStatus.REVIEWING}
                        ),
                    }
            call.status = ToolCallStatus.FAILED
            call.error = f"{type(exc).__name__}: {exc}"

        calls.append(call)

    return {
        "tool_calls": calls,
        "task": state["task"].model_copy(update={"status": TaskStatus.REVIEWING}),
    }
