"""会「当场停下」的两类工具结局。

一次工具调用有三种失败结局，其中两种**都不该被重试**，但要求用户做的事完全不同：

- `UncertainOutcomeError` —— 可能已经生效，不知道 → 让人**去核实**。
- `PreconditionError` —— 确定没做，但重规划也修不好（代理没开、登录过期）→ 让人**去修环境**。

本文件是 2026-10-12 那次重复加购事故的回归测试。事故的三个环节各锁一条：

1. 工具把「点击已成功、复核失败」报成了普通失败；
2. planner 看到失败就重新规划，重试又加了一次；
3. 全程没有报错，只有一句看起来合理的失败文案。

浏览器与网络无法在单测里复现，但**判断逻辑**可以——而事故的根因正是判断逻辑。
"""

from __future__ import annotations

from pydantic import BaseModel

from agi_agent.graph.nodes.executor import executor
from agi_agent.graph.nodes.respond import respond
from agi_agent.schemas import (
    Plan,
    PlanStep,
    RiskLevel,
    Task,
    TaskStatus,
    ToolCall,
    ToolCallStatus,
)
from agi_agent.tools import (
    PreconditionError,
    ToolError,
    UncertainOutcomeError,
    register,
)
from agi_agent.tools.registry import ToolSpec


class _NoArgs(BaseModel):
    """测试工具不需要参数。"""


def _raise_uncertain() -> str:
    raise UncertainOutcomeError("已点击加购按钮，但随后无法读取购物车")


def _raise_certain() -> str:
    raise ToolError("页面打不开")


def _raise_precondition() -> str:
    raise PreconditionError("Steam 登录态已过期，请重新扫码登录")


def _ok() -> str:
    return "成功"


register(
    ToolSpec(
        name="test.uncertain",
        description="总是产生不确定结果",
        risk=RiskLevel.WRITE,
        parameters=_NoArgs,
        func=_raise_uncertain,
    )
)
register(
    ToolSpec(
        name="test.certain_failure",
        description="总是确定失败",
        risk=RiskLevel.WRITE,
        parameters=_NoArgs,
        func=_raise_certain,
    )
)
register(
    ToolSpec(
        name="test.precondition",
        description="总是前提条件不满足",
        risk=RiskLevel.WRITE,
        parameters=_NoArgs,
        func=_raise_precondition,
    )
)
register(
    ToolSpec(
        name="test.ok",
        description="总是成功",
        risk=RiskLevel.READ,
        parameters=_NoArgs,
        func=_ok,
    )
)


def _step(index: int, tool: str) -> PlanStep:
    return PlanStep(index=index, description=f"步骤 {index}", tool=tool)


def _plan(*tools: str) -> Plan:
    return Plan(task_id="t", steps=[_step(i, t) for i, t in enumerate(tools)])


def _state(plan: Plan) -> dict:
    return {"task": Task(goal="把游戏加入购物车"), "plan": plan, "tool_calls": []}


# ------------------------------------------------------------------ executor


def test_uncertain_call_is_recorded_as_uncertain_not_failed():
    """第 1 条：它是 OUTCOME_UNKNOWN，不是 FAILED。两者处理方式完全不同。"""
    calls = executor(_state(_plan("test.uncertain")))["tool_calls"]
    assert calls[0].status is ToolCallStatus.OUTCOME_UNKNOWN
    assert calls[0].status is not ToolCallStatus.FAILED
    assert "不要重试" in calls[0].error


def test_uncertain_call_stops_the_remaining_steps():
    """前面可能已经改变了真实世界，继续往下走只会更难收拾。"""
    calls = executor(_state(_plan("test.uncertain", "test.ok")))["tool_calls"]
    assert len(calls) == 1
    assert calls[0].tool == "test.uncertain"


def test_determinate_failure_still_runs_the_rest_and_stays_retryable():
    """确定失败没有副作用，可以继续、可以重试——不要矫枉过正。"""
    calls = executor(_state(_plan("test.certain_failure", "test.ok")))["tool_calls"]
    assert [c.status for c in calls] == [
        ToolCallStatus.FAILED,
        ToolCallStatus.SUCCEEDED,
    ]


# ------------------------------------------------------------------ respond


def test_respond_tells_the_human_to_verify_instead_of_retrying():
    """第 3 条：不能再给出一句含糊的「任务未完成」。"""
    call = ToolCall(
        task_id="t",
        step_index=0,
        tool="steam.add_to_cart",
        status=ToolCallStatus.OUTCOME_UNKNOWN,
        error="已点击加购按钮，但无法确认结果",
    )
    out = respond(
        {
            "task": Task(goal="加购"),
            "tool_calls": [call],
            "approval": None,
            "review": None,
            "reply": None,
            "revision": 1,
        }
    )
    assert out["task"].status is TaskStatus.NEEDS_HUMAN
    assert "人工确认" in out["reply"]
    assert "steam.add_to_cart" in out["reply"]


def test_respond_leaves_ordinary_success_alone():
    call = ToolCall(
        task_id="t", step_index=0, tool="test.ok", status=ToolCallStatus.SUCCEEDED
    )
    out = respond(
        {
            "task": Task(goal="g"),
            "tool_calls": [call],
            "approval": None,
            "review": None,
            "reply": "闲聊回复",
            "revision": 0,
        }
    )
    assert out["task"].status is TaskStatus.COMPLETED


# ------------------------------------------------------------------ 前提条件不满足


def test_precondition_failure_is_not_an_ordinary_failure():
    """登录过期、代理没开不是"计划的问题"，混进 FAILED 会让 planner 白跑几轮。"""
    calls = executor(_state(_plan("test.precondition")))["tool_calls"]
    assert calls[0].status is ToolCallStatus.PRECONDITION_FAILED
    assert calls[0].status is not ToolCallStatus.FAILED


def test_precondition_failure_also_stops_the_plan():
    calls = executor(_state(_plan("test.precondition", "test.ok")))["tool_calls"]
    assert len(calls) == 1


def test_respond_tells_the_user_to_fix_the_environment_not_to_verify():
    """两类中断要说完全不同的话——说混了用户不知道该核实还是该修复。"""
    call = ToolCall(
        task_id="t",
        step_index=0,
        tool="steam.add_to_cart",
        status=ToolCallStatus.PRECONDITION_FAILED,
        error="Steam 登录态已过期，请重新扫码登录",
    )
    out = respond(
        {
            "task": Task(goal="加购"),
            "tool_calls": [call],
            "approval": None,
            "review": None,
            "reply": None,
            "revision": 1,
        }
    )
    assert out["task"].status is TaskStatus.NEEDS_HUMAN
    assert "确实没有执行" in out["reply"]
    assert "重新登录" in out["reply"]
    # 关键区别：确定没做，就不能让人去"核实是否已经生效"。
    assert "可能已经生效" not in out["reply"]


def test_respond_reports_both_kinds_when_both_happen():
    unknown = ToolCall(
        task_id="t", step_index=0, tool="a",
        status=ToolCallStatus.OUTCOME_UNKNOWN, error="无法确认",
    )
    blocked = ToolCall(
        task_id="t", step_index=1, tool="b",
        status=ToolCallStatus.PRECONDITION_FAILED, error="代理未开启",
    )
    out = respond(
        {
            "task": Task(goal="g"),
            "tool_calls": [unknown, blocked],
            "approval": None,
            "review": None,
            "reply": None,
            "revision": 1,
        }
    )
    assert "可能已经生效" in out["reply"]
    assert "确实没有执行" in out["reply"]
