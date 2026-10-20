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
    render_arguments,
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


def test_approval_summary_shows_the_actual_arguments():
    """审批必须看到**真正会被执行的参数**，而不只是模型写的散文。

    场景：description 说"看看这张图"，但真正决定要把哪个文件上传到云端的是
    arguments 里的路径。没有这一行，人批准的是模型的说法，不是将要发生的事。
    """
    plan = assign_risk(
        _plan(
            PlanStep(
                index=0,
                description="看看这张图",
                tool="vision.describe_image",
                arguments={"path": "E:/pics/私密截图.png"},
            )
        )
    )
    summary = approval_summary(plan)
    assert "path=E:/pics/私密截图.png" in summary


def test_approval_summary_truncates_long_values():
    """长参数要截断，否则一段正文就能把审批摘要撑满整屏。"""
    plan = assign_risk(
        _plan(
            PlanStep(
                index=0,
                description="发长消息",
                tool="wechat.send_message",
                arguments={"to": "张三", "text": "喂" * 200},
            )
        )
    )
    summary = approval_summary(plan)
    assert "to=张三" in summary
    assert "…" in summary
    # 截断后不该出现连续的长正文
    assert "喂" * 100 not in summary


def test_reasoning_steps_have_no_argument_line():
    """纯推理步骤没有工具，也就不该多出一行空参数。"""
    summary = approval_summary(_plan(PlanStep(index=0, description="先想清楚")))
    assert "参数：" not in summary


def test_prompt_gets_the_longer_limit():
    """prompt 是图片生成的核心，不能被 80 字符截掉。

    用户批准的就是这段提示词；截断后只剩前半句，审批就变成批准一个残缺的请求。
    """
    prompt = "猫" * 200
    rendered = render_arguments({"prompt": prompt})
    assert prompt in rendered


def test_long_prompt_is_still_bounded():
    """但也不能无限长：摘要要能读，500 字符是刻意的上界。"""
    rendered = render_arguments({"prompt": "猫" * 600})
    assert "猫" * 500 in rendered
    assert "猫" * 501 not in rendered
