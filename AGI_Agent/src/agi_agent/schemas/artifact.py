"""产物（artifact）：工具生成、需要留给用户或后续步骤使用的东西。

**为什么单独一个类型，而不是把路径塞进 ``ToolCall.result``**：

- ``result`` 是给 reviewer / 最终回复 / 事件预览看的**文本**，它们的共同假设是
  “这是一段可读的话”。把结构体塞进去，这些地方会看到 Python repr，而且
  checkpoint 会多出一个未登记的类型。
- 产物需要被**结构化地**送到界面（图片名、MIME、大小），不能靠从回复里
  正则抠路径——那是一种“碰巧能用”的约定，换个措辞就失效。

**为什么只存文件名、不存绝对路径**：文件位置由“产物目录 + name”唯一确定。
存绝对路径会让 checkpoint 和数据库跟着机器/目录变化，也会把本机路径泄露到
对外契约里。真正读文件时由 ``artifacts.resolve_artifact`` 重新拼接并校验。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from agi_agent.schemas.common import SchemaModel


class ArtifactKind(StrEnum):
    """产物类别。目前只有图片；将来加文件/音频时在这里扩展。"""

    IMAGE = "image"


class Artifact(SchemaModel):
    """一个已经落盘的产物。

    ``name`` 由代码生成（时间戳 + 随机后缀 + 按文件头判定的扩展名），
    **不接受外部输入**。``mime`` 也是按文件头嗅探出来的，不采信供应商响应里的
    Content-Type——那是元数据，文件头才是事实。
    """

    name: str = Field(description="产物文件名；不含目录路径")
    kind: ArtifactKind = ArtifactKind.IMAGE
    mime: str = Field(description="按文件头判定的 MIME")
    size: int = Field(ge=0, description="字节数")
