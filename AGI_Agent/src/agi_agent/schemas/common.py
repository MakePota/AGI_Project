"""跨模型共用的枚举与基础类型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class SchemaModel(BaseModel):
    """所有数据契约的基类：拒绝未声明字段，尽早暴露拼写与结构错误。"""

    model_config = ConfigDict(extra="forbid")


class Role(StrEnum):
    """Agent 的四个协作角色。

    此处只定义"谁来做"，具体由哪个供应商的哪个模型承担属于 providers 层。
    """

    CONVERSATION = "conversation"
    PLANNER = "planner"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"


class RiskLevel(StrEnum):
    """工具调用的风险等级，由 policies 判定是自动放行还是需要人工确认。"""

    READ = "read"                # 只读查询，可自动执行
    WRITE = "write"              # 写文件、发送消息等，需要人工确认
    DESTRUCTIVE = "destructive"  # 不可逆或涉及支付，需人工确认并明确提示后果


def new_id() -> str:
    """生成实体 id。"""
    return uuid4().hex


def utc_now() -> datetime:
    """当前 UTC 时间。"""
    return datetime.now(UTC)
