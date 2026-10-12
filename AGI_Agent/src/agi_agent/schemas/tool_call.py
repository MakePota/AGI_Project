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
    #: 可能已经产生了副作用，但无法确认。**与 FAILED 严格区分**：
    #: FAILED 确定没做成，可以重试；OUTCOME_UNKNOWN 不确定，重试会叠加动作。
    #: 见 ``agi_agent.tools.errors`` 里记录的那次真实事故。
    OUTCOME_UNKNOWN = "outcome_unknown"
    #: 确定没做成，但**重规划也修不好**：代理没开、登录态过期这类前提条件问题。
    #: 与 FAILED 的区别：FAILED 是计划的问题，planner 改一改也许能成；
    #: 这个是环境的问题，只有人能修。
    PRECONDITION_FAILED = "precondition_failed"


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
