"""checkpointer 装配。

LangGraph 1.x 在把状态写入 checkpointer 时要求自定义 Pydantic 类型显式注册，
否则未来版本会直接拒绝反序列化。这里集中登记 ``schemas`` 中的全部类型，
新增 schema 时记得同步 ``_ALLOWED``。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

#: 状态中可能出现、需要被 checkpointer 序列化的自定义类型。
_ALLOWED: list[tuple[str, str]] = [
    ("agi_agent.schemas.common", "Role"),
    ("agi_agent.schemas.common", "RiskLevel"),
    ("agi_agent.schemas.artifact", "Artifact"),
    ("agi_agent.schemas.artifact", "ArtifactKind"),
    ("agi_agent.schemas.task", "Task"),
    ("agi_agent.schemas.task", "TaskKind"),
    ("agi_agent.schemas.task", "TaskStatus"),
    ("agi_agent.schemas.plan", "Plan"),
    ("agi_agent.schemas.plan", "PlanStep"),
    ("agi_agent.schemas.tool_call", "ToolCall"),
    ("agi_agent.schemas.tool_call", "ToolCallStatus"),
    ("agi_agent.schemas.approval", "Approval"),
    ("agi_agent.schemas.approval", "ApprovalDecision"),
    ("agi_agent.schemas.review", "Review"),
]


@contextmanager
def sqlite_checkpointer(db_path: Path) -> Iterator[SqliteSaver]:
    """打开 SQLite checkpointer，退出时关闭连接。

    仅适用于单机单进程；SQLite 不支持多副本共享写入。
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        saver = SqliteSaver(
            conn,
            serde=JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED),
        )
        saver.setup()
        yield saver
    finally:
        conn.close()
