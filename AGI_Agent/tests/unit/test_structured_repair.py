"""结构化输出的解析失败处理。

回归的是 2026-10-13 实测撞到的一次真实故障：

    任务**已经成功执行**（工具正常返回了结果），但 reviewer 把模型输出里
    带**未转义英文双引号**的 `reason` 交给解析器 → JSON 非法 → 抛错 →
    整个任务被报成 `task_failed`。

也就是说，"解析不了"被当成了"事情没做成"——这正是 ADR 0010 反复强调
不许混为一谈的那类混淆，只不过这次混淆发生在模型输出这一层。

**测试不碰真实模型**：`_invoke_with_repair` 只要求入参有 `invoke`，
所以这里塞假对象，能精确控制"第几次失败、失败后拿到什么"。
"""

from __future__ import annotations

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agi_agent.providers.llm.structured import (
    _HINT,
    _MAX_ATTEMPTS,
    _invoke_with_repair,
)


class _FakeRunnable:
    """按剧本依次返回结果或抛错，并记录每次收到的对话。"""

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.convos: list[list[object]] = []

    def invoke(self, convo):
        self.convos.append(list(convo))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _parse_error() -> OutputParserException:
    return OutputParserException(
        "Invalid json output: {\"passed\": true, \"reason\": \"用户\"列出文件\"的目标\"}",
        llm_output="...",
    )


def test_success_on_first_try_makes_exactly_one_call() -> None:
    """正常情况不许付出重试的代价。"""
    runnable = _FakeRunnable([AIMessage("ok")])
    assert _invoke_with_repair(runnable, []) == AIMessage("ok")
    assert len(runnable.convos) == 1


def test_parse_failure_retries_and_succeeds() -> None:
    """第一次坏 JSON、第二次好 JSON：应当成功，而不是把任务判死。"""
    runnable = _FakeRunnable([_parse_error(), AIMessage("修好了")])
    assert _invoke_with_repair(runnable, []) == AIMessage("修好了")
    assert len(runnable.convos) == 2


def test_repair_turn_carries_the_parser_error_verbatim() -> None:
    """喂回去的那一轮必须带上解析器的原话。

    只说"你错了"没有信息量——模型需要知道**具体哪里**不合法才修得动。
    """
    runnable = _FakeRunnable([_parse_error(), AIMessage("ok")])
    _invoke_with_repair(runnable, [])

    repair = runnable.convos[1][-1]
    assert isinstance(repair, HumanMessage)
    assert "Invalid json output" in str(repair.content)


def test_persistent_failure_raises_after_the_limit() -> None:
    """两次都不合法时**原样抛出解析异常**。

    这里刻意不"假装成功"、也不自己编一个空结果：解析失败是真实结局，
    要让它继续往上冒，由调用方（节点）决定怎么处理。
    同时确认重试次数有上限——每多一次都是一次真实的model调用。
    """
    runnable = _FakeRunnable([_parse_error() for _ in range(_MAX_ATTEMPTS)])
    with pytest.raises(OutputParserException):
        _invoke_with_repair(runnable, [])
    assert len(runnable.convos) == _MAX_ATTEMPTS


def test_hint_warns_against_bare_double_quotes() -> None:
    """从源头堵最常见的坏法：提示词必须明确禁止裸英文双引号。

    这是对本次真实故障的针对性处理——模型当时就是拿英文双引号去括一句中文。
    如果没有这一条，重试只是在原地赌运气。
    """
    assert "不要使用英文双引号" in _HINT
    assert "{schema}" in _HINT, "占位符丢了会让提示词渲染失败"


def test_hint_still_carries_the_schema() -> None:
    rendered = _HINT.format(schema='{"type": "object"}')
    assert '{"type": "object"}' in rendered
    assert isinstance(SystemMessage(rendered), SystemMessage)
