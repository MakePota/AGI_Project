"""任务：一次用户请求及其整体生命周期。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from agi_agent.schemas.artifact import Artifact
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
    #: 执行产生了无法确认的副作用，已停止，等人核实真实状态。
    #: 与 FAILED 的区别：任务本身没有失败，是**结果未知**，需要人介入才能继续。
    NEEDS_HUMAN = "needs_human"


class Task(SchemaModel):
    """一次用户请求的持久记录。"""

    id: str = Field(default_factory=new_id)
    goal: str
    kind: TaskKind | None = None
    status: TaskStatus = TaskStatus.RECEIVED
    result: str | None = None
    error: str | None = None
    #: 任务级产物清单。由 executor 把各次 ToolCall.artifacts 合并进来。
    #: 放在 Task 上而不是 AgentState 上，是为了让“最终状态”只有一个来源：
    #: checkpoint 读回 task 就能拿到全部产物，不必再遍历 tool_calls 去拼。
    artifacts: list[Artifact] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
