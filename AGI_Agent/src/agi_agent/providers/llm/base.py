"""模型供应商适配：角色注册表 + 工厂。

调用方只依赖 ``Role`` 和 LangChain 的 ``BaseChatModel``，
不感知背后是哪家供应商、哪个模型。
"""

from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from agi_agent.config import ModelSpec, settings
from agi_agent.schemas.common import Role

# 供应商注册表：供应商名 -> LangChain ChatModel 实现。
# "openai" 走 OpenAI 兼容端点，可覆盖 ChatGPT / DeepSeek / Kimi / 智谱 / 千问。
# 接入 Claude 时在此补 "anthropic": ChatAnthropic（需另装 langchain-anthropic）。
_ADAPTERS: dict[str, type[BaseChatModel]] = {
    "openai": ChatOpenAI,
}


def _resolve_api_key(spec: ModelSpec) -> str:
    """按配置中的环境变量名取回密钥；只读名字，不读内容以外的来源。"""
    key = os.environ.get(spec.api_key_env)
    if not key:
        raise RuntimeError(
            f"环境变量 {spec.api_key_env} 未设置；请在 .env 或系统环境变量中配置。"
        )
    return key


def get_model(role: Role) -> BaseChatModel:
    """按角色取回模型实例。"""
    spec: ModelSpec = getattr(settings.llm, role.value)

    adapter = _ADAPTERS.get(spec.vendor)
    if adapter is None:
        raise ValueError(
            f"未注册的模型供应商：{spec.vendor}（可选：{sorted(_ADAPTERS)}）"
        )

    kwargs: dict[str, object] = {
        "model": spec.model,
        "api_key": _resolve_api_key(spec),
    }
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    if spec.temperature is not None:
        kwargs["temperature"] = spec.temperature
    if spec.extra_body is not None:
        # 供应商私有参数的透传口；为什么需要它、取值依据见 config.ModelSpec 的注释。
        kwargs["extra_body"] = spec.extra_body

    return adapter(**kwargs)
