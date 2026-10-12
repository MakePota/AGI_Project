"""审批：高风险动作的人工确认记录。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from agi_agent.schemas.common import RiskLevel, SchemaModel, new_id, utc_now


class ApprovalDecision(StrEnum):
    """人工确认的结果。"""

    APPROVED = "approved"
    REJECTED = "rejected"


class Approval(SchemaModel):
    """一次人工确认请求及其结果。

    graph 在需要确认时中断并生成本记录；用户回复后写回 decision 再恢复执行。
    工具真正执行前，必须存在一条 decision == APPROVED 的记录。
    """

    id: str = Field(default_factory=new_id)
    task_id: str
    tool_call_id: str | None = None  # 整批审批时不对应单个调用，故可为空
    summary: str  # 给人看的说明：将要做什么、会影响什么
    risk: RiskLevel
    requested_at: datetime = Field(default_factory=utc_now)
    decision: ApprovalDecision | None = None
    decided_at: datetime | None = None
    reason: str | None = None  # 用户拒绝的理由或补充说明
