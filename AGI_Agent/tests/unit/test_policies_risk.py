"""安全边界。

这里一旦失效，后果是**未经确认就执行了有副作用的动作**，
所以每条断言都对应一个真实的危险场景。
"""

from __future__ import annotations

import pytest

from agi_agent.policies.risk import (
    approval_summary,
    assign_risk,
    highest_risk,
    requires_approval,
)
from agi_agent.schemas import Plan, PlanStep, RiskLevel


def _plan(*steps: PlanStep) -> Plan:
    return Plan(task_id="t", steps=list(steps))


def test_llm_supplied_risk_is_overwritten():
    """模型自报 read 必须被注册表覆盖——本项目最关键的一条安全属性。"""
    plan = _plan(
        PlanStep(
            index=0,
            description="给张三发消息",
            tool="wechat.send_message",
            risk=RiskLevel.READ,  # 谎报
        )
    )
    assign_risk(plan)
    assert plan.steps[0].risk is RiskLevel.WRITE
    assert requires_approval(plan)


def test_read_only_plan_needs_no_approval():
    plan = assign_risk(
        _plan(PlanStep(index=0, description="读配置", tool="local.read_file"))
    )
    assert plan.steps[0].risk is RiskLevel.READ
    assert not requires_approval(plan)


def test_reasoning_step_is_read_only():
    plan = assign_risk(_plan(PlanStep(index=0, description="先想清楚")))
    assert plan.steps[0].risk is RiskLevel.READ
    assert not requires_approval(plan)


def test_unknown_tool_is_rejected():
    """计划里出现未注册的工具必须失败，不能让未登记的能力溜过去。"""
    with pytest.raises(KeyError):
        assign_risk(_plan(PlanStep(index=0, description="乱来", tool="not.registered")))


def test_highest_risk_prefers_destructive():
    plan = _plan(
        PlanStep(index=0, description="a", risk=RiskLevel.READ),
        PlanStep(index=1, description="b", risk=RiskLevel.WRITE),
        PlanStep(index=2, description="c", risk=RiskLevel.DESTRUCTIVE),
    )
    assert highest_risk(plan) is RiskLevel.DESTRUCTIVE


def test_highest_risk_defaults_to_read():
    assert highest_risk(_plan(PlanStep(index=0, description="a"))) is RiskLevel.READ


def test_approval_summary_marks_every_step():
    plan = assign_risk(
        _plan(
            PlanStep(index=0, description="读文件", tool="local.read_file"),
            PlanStep(index=1, description="发给张三", tool="wechat.send_message"),
        )
    )
    summary = approval_summary(plan)
    assert "自动" in summary
    assert "需确认" in summary
    assert "local.read_file" in summary
    assert "wechat.send_message" in summary
