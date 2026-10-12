"""计划：planner 对任务给出的执行方案。

计划步骤是"意图"，与"实际执行记录"（ToolCall）分开保存，
以便 reviewer 能审查策划本身，而不只是审查执行结果。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from agi_agent.schemas.common import RiskLevel, SchemaModel, new_id, utc_now


class PlanStep(SchemaModel):
    """计划中的一步。带 tool 的是工具调用意图，不带的是纯推理步骤。"""

    index: int
    description: str
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.READ
    depends_on: list[int] = Field(default_factory=list)


class Plan(SchemaModel):
    """可被审查、可被否决并重做的执行方案。"""

    id: str = Field(default_factory=new_id)
    task_id: str
    steps: list[PlanStep] = Field(default_factory=list)
    rationale: str | None = None
    revision: int = 1  # reviewer 打回后递增
    created_at: datetime = Field(default_factory=utc_now)
