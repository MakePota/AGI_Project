"""工具调用：一次实际发生的工具执行及其结果。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from agi_agent.schemas.common import RiskLevel, SchemaModel, new_id, utc_now


class ToolCallStatus(StrEnum):
    """一次工具调用的状态。"""

    PENDING = "pending"      # 已产生，等待审批
    APPROVED = "approved"
    REJECTED = "rejected"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolCall(SchemaModel):
    """计划步骤到实际执行的落地记录，用于审计与中断后恢复。"""

    id: str = Field(default_factory=new_id)
    task_id: str
    step_index: int
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.READ
    status: ToolCallStatus = ToolCallStatus.PENDING
    result: Any | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
