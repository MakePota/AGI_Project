"""审查：reviewer 对一次执行结果的判定。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from agi_agent.schemas.common import SchemaModel, new_id, utc_now


class Review(SchemaModel):
    """reviewer 对某一次执行的判定。

    不通过时由 planner 重新规划（``Plan.revision`` 递增），达到重试上限后
    整体以失败结束。保留本记录是为了满足可审计要求：审查为什么放行或打回，
    必须事后可查。
    """

    id: str = Field(default_factory=new_id)
    task_id: str
    plan_id: str
    revision: int = 1  # 与所审 Plan 的 revision 对应
    passed: bool
    reason: str
    at: datetime = Field(default_factory=utc_now)
