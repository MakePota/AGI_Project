"""任务：一次用户请求及其整体生命周期。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from agi_agent.schemas.common import SchemaModel, new_id, utc_now


class TaskKind(StrEnum):
    """planner 分诊的结果。"""

    CHAT = "chat"  # 闲聊，交给 conversation 角色直接回复
    TASK = "task"  # 需要规划与执行的任务


class TaskStatus(StrEnum):
    """任务在生命周期中的位置。"""

    RECEIVED = "received"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task(SchemaModel):
    """一次用户请求的持久记录。"""

    id: str = Field(default_factory=new_id)
    goal: str
    kind: TaskKind | None = None
    status: TaskStatus = TaskStatus.RECEIVED
    result: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
