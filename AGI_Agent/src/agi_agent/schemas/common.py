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
    """模型绑定的**命名槽位**：此处只定义"什么位置需要一个模型"。

    具体由哪个供应商的哪个模型承担属于 providers 层（见 ADR 0002）。
    一个成员的值必须与 ``config.toml`` 里的 ``[llm.<值>]`` 段名一致——
    这是 ``get_model`` 用 ``getattr(settings.llm, role.value)`` 取配置的前提。

    槽位分两类，不要混淆：

    - **协作角色**（conversation / planner / executor / reviewer）：图里的节点按此取值。
    - **能力档位**（vision_quick / vision_deep / image_generation）：只有工具用得到，
      没有任何节点取值。分档位而不是一个带参数的档位，是为了让"用哪个模型"由
      **注册表与配置**决定，而不是由计划里的参数（即模型的输出）决定，见 ADR 0012。

    ``image_generation`` 是一个特殊档位：它绑定的模型**不是聊天模型**，
    不能交给 ``get_model``（那会造出一个 ChatOpenAI 去调 /chat/completions）。
    只有 ``tools/builtin/image.py`` 直接读它并使用图片生成端点，见 ADR 0018。
    """

    CONVERSATION = "conversation"
    PLANNER = "planner"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"
    #: 图像理解的"快"档：便宜、够用于看图说话与 OCR。
    VISION_QUICK = "vision_quick"
    #: 图像理解的"准"档：用于需要推理的图像问题（图表、比对、长文档截图）。
    VISION_DEEP = "vision_deep"
    #: 图片生成：绑定 Qwen-Image-3.0。**不要传给 get_model**，理由见上面的类注释。
    IMAGE_GENERATION = "image_generation"


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
