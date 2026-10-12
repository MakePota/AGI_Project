"""planner 节点：产出计划，也是重规划的回环入口。

计划只描述"做什么、用哪个工具、什么参数"。**风险等级不在这里决定**，
由 ``policies`` 依据工具注册表重新赋值——模型不能自报某个动作安全。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agi_agent.graph.state import AgentState
from agi_agent.providers.llm import get_model
from agi_agent.providers.llm.structured import invoke_structured
from agi_agent.schemas import Plan, PlanStep, Role
from agi_agent.tools import catalog_for_prompt

_SYSTEM = """你是任务规划器。把用户任务拆解成步骤最少的可执行计划。

可用工具：
{catalog}

规则：
- 只能使用上面列出的工具，禁止臆造工具名。
- arguments 的字段名必须与工具参数说明**逐字一致**，禁止猜测、翻译或改名。
- 不需要调用工具的纯推理步骤，把 tool 留空。
- 不要判断风险等级，不要执行任何动作，只输出计划。
- 如果任务本身无法用现有工具完成，就输出一个空的 steps 列表。{revision_hint}"""


class _StepDraft(BaseModel):
    description: str
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class _PlanDraft(BaseModel):
    steps: list[_StepDraft] = Field(default_factory=list)
    rationale: str | None = None


def planner(state: AgentState) -> dict:
    model = get_model(Role.PLANNER)
    revision = state.get("revision", 0) + 1

    review = state.get("review")
    revision_hint = ""
    if review is not None and not review.passed:
        revision_hint = (
            f"\n\n注意：上一轮计划被审查驳回，原因是：{review.reason}\n"
            "请针对这个原因重新规划，不要重复同样的做法。"
        )

    draft = invoke_structured(
        model,
        _PlanDraft,
        [
            SystemMessage(
                _SYSTEM.format(
                    catalog=catalog_for_prompt(), revision_hint=revision_hint
                )
            ),
            HumanMessage(state["task"].goal),
        ],
    )

    plan = Plan(
        task_id=state["task"].id,
        revision=revision,
        rationale=draft.rationale,
        steps=[
            PlanStep(
                index=i,
                description=s.description,
                tool=s.tool,
                arguments=s.arguments,
            )
            for i, s in enumerate(draft.steps)
        ],
    )
    return {"plan": plan, "revision": revision}
