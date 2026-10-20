"""工具返回值：文本结果 + 可选产物。

**为什么需要这个类型**：绝大多数工具只需返回一句话（``str``），但图片生成
还要带回落盘文件。直接把一个 dict 或 dataclass 塞进 ``ToolCall.result`` 会同时
破坏四处的共同假设——reviewer 会把 repr 当结果读、respond 会把 repr 写进最终回复、
事件预览 ``_preview`` 会把它压成一行、checkpoint 会多出一个未登记的类型。

所以约定是：**``ToolCall.result`` 永远是 str**，产物走 ``ToolCall.artifacts``。
这个类型只是把“两样东西一起返回”这件事表达出来，由 executor 负责拆开。
"""

from __future__ import annotations

from dataclasses import dataclass

from agi_agent.schemas import Artifact


@dataclass(frozen=True)
class ToolResult:
    """一次成功的工具调用：给人读的文本，以及（可选的）产物。"""

    text: str
    artifacts: tuple[Artifact, ...] = ()
