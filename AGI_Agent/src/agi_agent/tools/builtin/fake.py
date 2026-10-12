"""最小图使用的假工具（测试替身）。

它们不触碰任何真实外部系统，只用于验证图的控制流与审批链路。
返回值按**真实工具的成功契约**构造，而不是带 "FAKE" 标记——否则 reviewer
会正确地判定"动作并未真正发生"，从而触发无意义的重规划。

接入真实实现时替换本模块即可，注册表与图本身不需要改动。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agi_agent.schemas import RiskLevel
from agi_agent.tools.registry import ToolSpec, register


class ReadFileArgs(BaseModel):
    path: str = Field(description="要读取的文件路径")


class SendMessageArgs(BaseModel):
    to: str = Field(description="微信联系人名称，例如 张三")
    text: str = Field(description="要发送的消息正文")


register(
    ToolSpec(
        name="local.read_file",
        description="读取指定路径的本地文件内容",
        risk=RiskLevel.READ,
        parameters=ReadFileArgs,
        func=lambda path: f"已读取 {path}，共 3 行：<模拟文件内容>",
    )
)

register(
    ToolSpec(
        name="wechat.send_message",
        description="向指定联系人发送一条微信消息",
        risk=RiskLevel.WRITE,
        parameters=SendMessageArgs,
        func=lambda to, text: f"消息已发送给「{to}」：{text}",
    )
)
