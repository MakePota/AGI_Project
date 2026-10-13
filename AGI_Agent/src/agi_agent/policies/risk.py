"""风险分级与审批判定。

这里是"允许做什么"的落点。风险等级依据工具注册表赋值，
**不采信模型给出的计划中的 risk 字段**。
"""

from __future__ import annotations

from typing import Any

from agi_agent.schemas import Plan, RiskLevel
from agi_agent.tools import get as get_tool

_RISK_LABEL = {
    RiskLevel.READ: "自动",
    RiskLevel.WRITE: "需确认",
    RiskLevel.DESTRUCTIVE: "需确认·不可逆",
}

#: 摘要里单个参数值的显示上限。既要能看清"发给谁、哪个文件"，
#: 又不能因为某段长文本（消息正文、长路径）把审批摘要撑满整屏。
_SUMMARY_VALUE_LIMIT = 80


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


def _render_arguments(arguments: dict[str, Any]) -> str:
    """把步骤参数渲染成一行紧凑的事实。"""
    if not arguments:
        return ""
    parts = []
    for key, value in arguments.items():
        text = str(value).replace("\n", " ")
        if len(text) > _SUMMARY_VALUE_LIMIT:
            text = text[:_SUMMARY_VALUE_LIMIT] + "…"
        parts.append(f"{key}={text}")
    return "参数：" + "，".join(parts)


def approval_summary(plan: Plan) -> str:
    """渲染给人看的一整批计划说明。

    **为什么必须同时显示描述与参数**：``step.description`` 是**模型写的散文**，
    ``arguments`` 才是**真正会被执行的东西**。只显示散文，人审批的是模型的说法；
    只显示参数，人又不知道这一步想干什么。两者都显示，审批才有意义。

    这不是吹毛求疵：没有参数行时，审批界面会说"查看图片内容（vision.describe_image）"，
    却看不出要把**哪个文件**发出去；说"把游戏加入购物车（steam.add_to_cart）"，
    却看不出是**哪个游戏**。散文可以写错，参数不会（参数就是要发给工具的东西）。
    """
    lines = ["即将执行以下计划："]
    for step in plan.steps:
        tool = f"（{step.tool}）" if step.tool else ""
        lines.append(
            f"  {step.index + 1}. [{_RISK_LABEL[step.risk]}] {step.description}{tool}"
        )
        # 纯推理步骤没有工具也就没有参数，保持单行不啰嗦。
        if step.tool:
            rendered = _render_arguments(step.arguments)
            if rendered:
                lines.append(f"      {rendered}")
    return "\n".join(lines)
