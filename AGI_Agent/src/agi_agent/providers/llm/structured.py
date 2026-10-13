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
"""

from __future__ import annotations

import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, SystemMessage
from pydantic import BaseModel

_HINT = (
    "你必须只输出一个 JSON 对象，不含任何解释、前后缀或代码块标记。\n"
    "输出必须满足以下 JSON Schema：\n{schema}"
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
    return model.with_structured_output(schema, method="json_mode").invoke(
        [hint, *messages]
    )
