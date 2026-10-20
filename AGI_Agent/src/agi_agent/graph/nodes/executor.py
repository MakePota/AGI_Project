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
from agi_agent.schemas import Artifact, TaskStatus, ToolCall, ToolCallStatus
from agi_agent.tools import PreconditionError, UncertainOutcomeError
from agi_agent.tools import get as get_tool
from agi_agent.tools.result import ToolResult

#: 直接中断计划的两类失败 -> 对应的 ToolCall 状态。
_HALTING = (
    (UncertainOutcomeError, ToolCallStatus.OUTCOME_UNKNOWN),
    (PreconditionError, ToolCallStatus.PRECONDITION_FAILED),
)


def _record_outcome(call: ToolCall, outcome: object) -> None:
    """把工具返回值拆成“文本结果”与“产物”。

    ``ToolCall.result`` 必须保持 str：reviewer、respond、CLI 与事件预览都按字符串
    处理它。``ToolResult`` 只是工具层表达“我还带回了文件”的方式，拆包在这里完成，
    下游看不到这个类型。
    """
    if isinstance(outcome, ToolResult):
        call.result = outcome.text
        call.artifacts = list(outcome.artifacts)
    else:
        call.result = outcome


def _merge_artifacts(existing: list[Artifact], calls: list[ToolCall]) -> list[Artifact]:
    """把本次执行的产物合并进任务级清单，按 name 去重并保持出现顺序。

    按 name 去重而不是按对象去重：同一个产物可能因为重规划被执行到两次，
    但它指向的是同一个文件，在界面上应该只出现一张。
    """
    merged = list(existing)
    seen = {artifact.name for artifact in merged}
    for call in calls:
        for artifact in call.artifacts:
            if artifact.name not in seen:
                merged.append(artifact)
                seen.add(artifact.name)
    return merged


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
            _record_outcome(call, spec.func(**spec.validate(step.arguments)))
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
                            update={
                                "status": TaskStatus.REVIEWING,
                                # 中断前的步骤可能已经产出了文件，不能因为最后一步
                                # 失败就把它们从任务结果里丢掉。
                                "artifacts": _merge_artifacts(
                                    state["task"].artifacts, calls
                                ),
                            }
                        ),
                    }
            call.status = ToolCallStatus.FAILED
            call.error = f"{type(exc).__name__}: {exc}"

        calls.append(call)

    return {
        "tool_calls": calls,
        "task": state["task"].model_copy(
            update={
                "status": TaskStatus.REVIEWING,
                "artifacts": _merge_artifacts(state["task"].artifacts, calls),
            }
        ),
    }
