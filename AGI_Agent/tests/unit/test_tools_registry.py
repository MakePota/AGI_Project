"""工具注册表。

planner 全靠它拿参数签名，executor 靠它拦下坏参数——
签名不发布的那一版曾让 planner 连续三轮猜错参数名。
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from agi_agent.schemas import RiskLevel
from agi_agent.tools import all_specs, catalog_for_prompt, get, register
from agi_agent.tools.registry import ToolSpec


class _Args(BaseModel):
    name: str = Field(description="名字")
    times: int = 1


def _spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="测试用工具",
        risk=RiskLevel.READ,
        parameters=_Args,
        func=lambda name, times=1: f"{name}x{times}",
    )


def test_builtin_tools_are_registered():
    """导入 agi_agent.tools 就应完成内置工具注册，不依赖调用方的额外动作。"""
    names = {s.name for s in all_specs()}
    assert {"local.read_file", "wechat.send_message"} <= names


def test_duplicate_registration_is_rejected():
    register(_spec("test.duplicate"))
    with pytest.raises(ValueError, match="重复注册"):
        register(_spec("test.duplicate"))


def test_unknown_tool_raises():
    with pytest.raises(KeyError, match="未注册"):
        get("definitely.not.registered")


def test_validate_reports_actual_field_names():
    """报错必须点出字段名，否则 planner 只能瞎猜。"""
    with pytest.raises(ValueError) as exc:
        get("wechat.send_message").validate({"recipient": "张三", "text": "x"})
    message = str(exc.value)
    assert "recipient" in message
    assert "to" in message
    assert "text" in message


def test_validate_requires_missing_argument():
    with pytest.raises(ValueError, match="text"):
        get("wechat.send_message").validate({"to": "张三"})


def test_mixed_correct_and_wrong_names_are_rejected():
    """最危险的一种：正确与错误的参数名同时出现。

    若多余字段被静默丢弃，就会按正确那个执行、错误那个无声消失，
    调用方还以为两个都生效了。
    """
    with pytest.raises(ValueError, match="recipient"):
        get("wechat.send_message").validate(
            {"to": "张三", "recipient": "李四", "text": "晚点到"}
        )


def test_validate_applies_defaults():
    spec = _spec("test.defaults")
    register(spec)
    assert spec.validate({"name": "a"}) == {"name": "a", "times": 1}


def test_validate_returns_plain_dict():
    assert get("wechat.send_message").validate({"to": "张三", "text": "hi"}) == {
        "to": "张三",
        "text": "hi",
    }


def test_catalog_publishes_signatures():
    catalog = catalog_for_prompt()
    assert "参数：" in catalog
    for param in ("to", "text", "path"):
        assert param in catalog
