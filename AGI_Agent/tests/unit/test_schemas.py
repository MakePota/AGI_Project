"""数据契约的守卫。

这些不变量一旦被破坏，下游不会报错而是静默出错，所以必须钉住。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agi_agent.schemas import (
    Approval,
    Event,
    EventType,
    Plan,
    RiskLevel,
    Task,
    TaskStatus,
    ToolCall,
    ToolCallStatus,
)


def test_task_defaults():
    task = Task(goal="测试")
    assert task.status is TaskStatus.RECEIVED
    assert task.kind is None
    assert task.result is None
    assert task.id


def test_ids_are_unique_per_instance():
    assert Task(goal="a").id != Task(goal="a").id


def test_extra_fields_are_rejected():
    """模型常会多吐字段，静默忽略会埋雷，所以契约必须拒绝。"""
    with pytest.raises(ValidationError):
        Task(goal="测试", typo_field=1)


def test_event_is_frozen():
    """审计轨迹不得被改写。"""
    event = Event(task_id="t", type=EventType.ROUTED)
    with pytest.raises(ValidationError):
        event.type = EventType.TASK_COMPLETED


def test_plan_revision_starts_at_one():
    assert Plan(task_id="t").revision == 1


def test_tool_call_defaults():
    call = ToolCall(task_id="t", step_index=0, tool="x")
    assert call.status is ToolCallStatus.PENDING
    assert call.risk is RiskLevel.READ


def test_approval_tool_call_id_is_optional():
    """整批判定不对应某一次工具调用，因此该字段可为空。"""
    approval = Approval(task_id="t", summary="s", risk=RiskLevel.WRITE)
    assert approval.tool_call_id is None
    assert approval.decision is None
