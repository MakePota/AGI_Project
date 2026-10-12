"""图的路由决策。

重点是重规划上限：它失效会让 planner 与 reviewer 无限互相打回，
把 API 额度烧光。
"""

from __future__ import annotations

from agi_agent.graph.builder import (
    MAX_REVISIONS,
    route_after_authorize,
    route_after_execute,
    route_after_review,
    route_after_triage,
)
from agi_agent.schemas import (
    Approval,
    ApprovalDecision,
    Review,
    RiskLevel,
    Task,
    TaskKind,
    ToolCall,
    ToolCallStatus,
)


def _state(**overrides) -> dict:
    base = {"task": Task(goal="g"), "approval": None, "review": None, "revision": 0}
    base.update(overrides)
    return base


def _review(passed: bool) -> Review:
    return Review(task_id="t", plan_id="p", passed=passed, reason="r")


def _approval(decision: ApprovalDecision) -> Approval:
    return Approval(
        task_id="t", summary="s", risk=RiskLevel.WRITE, decision=decision
    )


def test_triage_routes_by_kind():
    assert route_after_triage(_state(task=Task(goal="g", kind=TaskKind.TASK))) == "task"
    assert route_after_triage(_state(task=Task(goal="g", kind=TaskKind.CHAT))) == "chat"


def test_authorize_routes_to_execute_when_no_approval_needed():
    assert route_after_authorize(_state()) == "execute"


def test_authorize_routes_approved_to_execute():
    approval = _approval(ApprovalDecision.APPROVED)
    assert route_after_authorize(_state(approval=approval)) == "execute"


def test_authorize_routes_rejected_to_respond():
    approval = _approval(ApprovalDecision.REJECTED)
    assert route_after_authorize(_state(approval=approval)) == "rejected"


def _call(status: ToolCallStatus) -> ToolCall:
    return ToolCall(task_id="t", step_index=0, tool="x", status=status)


def test_execute_halts_when_an_outcome_is_unknown():
    """结果不确定时**绝不能**进重规划——重规划就是重试，会叠加真实动作。"""
    state = _state(tool_calls=[_call(ToolCallStatus.OUTCOME_UNKNOWN)])
    assert route_after_execute(state) == "halt"


def test_execute_halts_when_a_precondition_is_not_met():
    """代理没开、登录过期：重规划也修不好，只有人能修，所以同样要停。"""
    state = _state(tool_calls=[_call(ToolCallStatus.PRECONDITION_FAILED)])
    assert route_after_execute(state) == "halt"


def test_execute_proceeds_when_every_outcome_is_known():
    """明确失败**不算**不确定：它确定没做成，重试是安全的。"""
    state = _state(
        tool_calls=[_call(ToolCallStatus.SUCCEEDED), _call(ToolCallStatus.FAILED)]
    )
    assert route_after_execute(state) == "review"
    assert route_after_execute(_state(tool_calls=[])) == "review"


def test_review_pass_finishes():
    assert route_after_review(_state(review=_review(True))) == "done"


def test_review_fail_triggers_replan():
    assert route_after_review(_state(review=_review(False), revision=1)) == "replan"


def test_review_fail_stops_at_max_revisions():
    assert route_after_review(_state(review=_review(False), revision=MAX_REVISIONS)) == "give_up"
    assert (
        route_after_review(_state(review=_review(False), revision=MAX_REVISIONS + 5))
        == "give_up"
    )


def test_max_revisions_is_three():
    assert MAX_REVISIONS == 3
