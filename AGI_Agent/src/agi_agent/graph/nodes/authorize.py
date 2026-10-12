"""authorize 节点：不可省略的边界。

整批判定：计划里只要有带副作用的步骤，就在这里中断一次，
把完整计划交给用户确认。风险等级由 ``policies`` 重新赋值。
"""

from __future__ import annotations

from langgraph.types import interrupt

from agi_agent.graph.state import AgentState
from agi_agent.policies.risk import (
    approval_summary,
    assign_risk,
    highest_risk,
    requires_approval,
)
from agi_agent.schemas import Approval, ApprovalDecision, TaskStatus


def authorize(state: AgentState) -> dict:
    plan = assign_risk(state["plan"])

    if not requires_approval(plan):
        return {
            "plan": plan,
            "task": state["task"].model_copy(update={"status": TaskStatus.EXECUTING}),
        }

    summary = approval_summary(plan)
    decision = interrupt(
        {
            "type": "approval_requested",
            "summary": summary,
            "steps": [step.model_dump(mode="json") for step in plan.steps],
        }
    )

    approved = bool(decision.get("approved"))
    approval = Approval(
        task_id=state["task"].id,
        summary=summary,
        risk=highest_risk(plan),
        decision=ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED,
        reason=decision.get("reason"),
    )
    status = TaskStatus.EXECUTING if approved else TaskStatus.CANCELLED
    return {
        "plan": plan,
        "approval": approval,
        "task": state["task"].model_copy(update={"status": status}),
    }
