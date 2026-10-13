"""跨供应商的结构化输出。

LangChain 的默认路径在 DeepSeek 上不可用，实测遇到两个限制：

1. ``method="function_calling"`` 会返回 400
   "Thinking mode does not support this tool_choice"
   —— thinking 模型不接受被强制的 tool_choice。
2. 默认方法走 ``response_format`` 的 ``json_schema`` 类型，DeepSeek 返回 400
   "This response_format type is unavailable now"。

因此统一改走 ``json_mode``：协议层只保证"输出是合法 JSON"，schema 由我们自己
写进提示词。换供应商时只需要改这一个文件——这正是把供应商差异关在
``providers`` 层、不让它渗进节点的原因。

**json_mode 的代价：合法 JSON 只能靠模型自觉。** 2026-10-13 实测撞到过一次：
reviewer 在 `reason` 里用**未转义的英文双引号**括住一句中文，整个 JSON 因此非法，
解析直接抛错。后果比"报错"更糟——那次任务**已经成功执行了**，却被报成失败。

两处针对性处理：提示词里先堵住最常见的坏法，出错后再给模型一次改正的机会。
完整复盘见 ADR 0010（"不许把不同结局混为一谈"）的精神在这里同样适用：
**"解析不了"不等于"事情没做成"**。
"""

from __future__ import annotations

import json

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

_HINT = (
    "你必须只输出一个 JSON 对象，不含任何解释、前后缀或代码块标记。\n"
    "字符串内部若需要引用词语，请用「」或『』，**不要使用英文双引号**——\n"
    "未转义的英文双引号会让整个 JSON 变成非法，是最常见的失败原因。\n"
    "输出必须满足以下 JSON Schema：\n{schema}"
)

#: 解析失败后允许的总尝试次数（1 次正常 + 1 次改正）。
#: 只给一次机会：模型连续两次犯同一个格式错误很罕见，而每多一次就多一次真实调用的钱。
_MAX_ATTEMPTS = 2

#: 解析失败时喂回去的那一轮。**必须带上解析器的原话**——只说"你错了"没有信息量。
_REPAIR = (
    "你上一次的输出不是合法 JSON，解析器报错如下：\n{error}\n"
    "请重新输出一个**合法**的 JSON 对象，内容保持不变。"
    "特别注意：字符串内部的引用不要用英文双引号。"
)


# 泛型参数用 PEP 695 的内联写法（`[T: BaseModel]`）而不是模块级 ``TypeVar``：
# 类型变量只在这一个函数里用得到，内联写法的作用域就是它该有的范围。
# 代价是需要 Python 3.12+，而本项目 requires-python 已经锁死 >=3.12。
def invoke_structured[T: BaseModel](
    model: BaseChatModel, schema: type[T], messages: list[AnyMessage]
) -> T:
    """调用模型并返回 ``schema`` 类型的结构化结果。"""
    hint = SystemMessage(
        _HINT.format(schema=json.dumps(schema.model_json_schema(), ensure_ascii=False))
    )
    structured = model.with_structured_output(schema, method="json_mode")
    return _invoke_with_repair(structured, [hint, *messages])


def _invoke_with_repair[T: BaseModel](runnable, convo: list[AnyMessage]) -> T:
    """调用并解析；解析失败时**带着报错原文再试一次**。

    拆成独立函数是为了能测：它只要求 ``runnable`` 有 ``invoke``，
    因此单测可以塞一个假对象进去，不必真的调用模型（要花钱且结果不确定）。

    为什么不是"原样重试"：同一段提示词再问一遍，很可能原样吐回同样的坏 JSON。
    把解析器的报错作为一轮对话喂回去，模型才有机会知道**具体哪里**不合法。

    为什么不做"本地自动修复 JSON"：那会**静默改变语义**——修复器替模型猜它想说什么，
    而猜错不会有任何提示。宁可让它自己重写，也不要一个我们看不懂的修正。
    """
    last_error: OutputParserException | None = None
    for _ in range(_MAX_ATTEMPTS):
        try:
            return runnable.invoke(convo)
        except OutputParserException as exc:
            last_error = exc
            convo = [*convo, HumanMessage(_REPAIR.format(error=str(exc)[:500]))]

    # 两次都不合法：原样抛出解析异常。这是模型的输出问题，不在这里假装成功——
    # 但错误信息里要能看出"试过了、还是不行"。
    assert last_error is not None  # 循环至少跑过一次，这里不可能为 None
    raise last_error

