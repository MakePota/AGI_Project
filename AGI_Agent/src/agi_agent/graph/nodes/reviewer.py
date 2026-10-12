"""reviewer 节点：审查执行记录是否真正完成了任务。

审查只看执行记录中的实际结果，不假设未记录的动作发生过。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agi_agent.graph.state import AgentState
from agi_agent.providers.llm import get_model
from agi_agent.providers.llm.structured import invoke_structured
from agi_agent.schemas import Review, Role, ToolCallStatus

_SYSTEM = """你是执行结果的审查者。判断执行记录是否真正完成了用户任务。

要求：
- 只看执行记录里的实际结果，不要假设没有记录的动作发生过。
- 任一关键步骤失败，或结果明显不满足用户目标，即判定不通过。
- 不通过时必须给出具体、可据以重新规划的原因，不要只说"再试一次"。"""


class _Verdict(BaseModel):
    passed: bool
    reason: str


def _render(calls: list) -> str:
    if not calls:
        return "  （没有任何执行记录）"
    lines = []
    for i, call in enumerate(calls, start=1):
        if call.status is ToolCallStatus.SUCCEEDED:
            outcome = f"成功：{call.result}"
        else:
            outcome = f"{call.status.value}：{call.error or call.result}"
        lines.append(f"  {i}. {call.tool} -> {outcome}")
    return "\n".join(lines)


def reviewer(state: AgentState) -> dict:
    model = get_model(Role.REVIEWER)
    plan = state["plan"]

    steps = "\n".join(f"  {s.index + 1}. {s.description}" for s in plan.steps) or "  （空计划）"
    verdict = invoke_structured(
        model,
        _Verdict,
        [
            SystemMessage(_SYSTEM),
            HumanMessage(
                f"用户任务：{state['task'].goal}\n\n"
                f"计划：\n{steps}\n\n"
                f"执行记录：\n{_render(state['tool_calls'])}"
            ),
        ],
    )

    review = Review(
        task_id=state["task"].id,
        plan_id=plan.id,
        revision=plan.revision,
        passed=verdict.passed,
        reason=verdict.reason,
    )
    return {"review": review}
