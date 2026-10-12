"""事件：可审计的执行轨迹。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field

from agi_agent.schemas.common import Role, SchemaModel, new_id, utc_now


class EventType(StrEnum):
    """执行过程中值得留痕的节点。"""

    TASK_RECEIVED = "task_received"
    ROUTED = "routed"
    PLAN_CREATED = "plan_created"
    PLAN_REVISED = "plan_revised"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    REVIEW_PASSED = "review_passed"
    REVIEW_REJECTED = "review_rejected"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"


class Event(SchemaModel):
    """追加写的审计记录：不可修改，不参与业务逻辑，只用于追踪与回放。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=new_id)
    task_id: str
    type: EventType
    at: datetime = Field(default_factory=utc_now)
    role: Role | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
