"""风险分级与审批判定。

这里是"允许做什么"的落点。风险等级依据工具注册表赋值，
**不采信模型给出的计划中的 risk 字段**。
"""

from __future__ import annotations

from agi_agent.schemas import Plan, RiskLevel
from agi_agent.tools import get as get_tool

_RISK_LABEL = {
    RiskLevel.READ: "自动",
    RiskLevel.WRITE: "需确认",
    RiskLevel.DESTRUCTIVE: "需确认·不可逆",
}


def assign_risk(plan: Plan) -> Plan:
    """按工具注册表重新标注每个步骤的风险，覆盖计划自带的值。"""
    for step in plan.steps:
        step.risk = RiskLevel.READ if step.tool is None else get_tool(step.tool).risk
    return plan


def requires_approval(plan: Plan) -> bool:
    """整批判定：任一步骤有副作用，就需要一次人工确认。"""
    return any(step.risk is not RiskLevel.READ for step in plan.steps)


def highest_risk(plan: Plan) -> RiskLevel:
    """取计划中的最高风险等级。"""
    if any(s.risk is RiskLevel.DESTRUCTIVE for s in plan.steps):
        return RiskLevel.DESTRUCTIVE
    if any(s.risk is RiskLevel.WRITE for s in plan.steps):
        return RiskLevel.WRITE
    return RiskLevel.READ


def approval_summary(plan: Plan) -> str:
    """渲染给人看的一整批计划说明。"""
    lines = ["即将执行以下计划："]
    for step in plan.steps:
        tool = f"（{step.tool}）" if step.tool else ""
        lines.append(
            f"  {step.index + 1}. [{_RISK_LABEL[step.risk]}] {step.description}{tool}"
        )
    return "\n".join(lines)
