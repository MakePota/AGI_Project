"""executor 节点：按计划执行工具步骤。

单个步骤失败不中断整个计划，失败信息写入 ``ToolCall``，交给 reviewer 判定。

**唯一的例外是「结果不确定」**：一旦某次调用可能已经产生了副作用但我们无法确认，
就立刻停止执行剩余步骤。理由是前面的步骤可能已经真实改变了外部世界，
在状态不明的世界里继续往下走，只会把事情弄得更难收拾。

停止之后由 ``route_after_execute`` 直接送去 respond——**不进重规划**，
因为重规划意味着重试，而重试一个结果未知的写操作会叠加动作。
"""

from __future__ import annotations

from agi_agent.graph.state import AgentState
from agi_agent.schemas import TaskStatus, ToolCall, ToolCallStatus
from agi_agent.tools import UncertainOutcomeError
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
            calls.append(call)
            continue

        spec = get_tool(step.tool)
        try:
            # 先按声明的签名校验参数，再调用；让失败信息具体到字段名。
            call.result = spec.func(**spec.validate(step.arguments))
            call.status = ToolCallStatus.SUCCEEDED
        except UncertainOutcomeError as exc:
            # 必须排在 except Exception 之前：它是 ToolError 的子类。
            call.status = ToolCallStatus.OUTCOME_UNKNOWN
            call.error = str(exc)
            calls.append(call)
            break
        except Exception as exc:  # 工具失败要留痕，不能吞掉
            call.status = ToolCallStatus.FAILED
            call.error = f"{type(exc).__name__}: {exc}"

        calls.append(call)

    return {
        "tool_calls": calls,
        "task": state["task"].model_copy(update={"status": TaskStatus.REVIEWING}),
    }
