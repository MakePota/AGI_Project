"""运行时配置。

模型角色与具体供应商在此解耦：

- 角色到模型的绑定写在 ``config.toml``（可提交，不含密钥）；
- 密钥放在 ``.env`` 或系统环境变量中，本文件只记录环境变量的"名字"。

替换供应商或模型只需修改 ``config.toml``，不需要改动代码。
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: LangGraph checkpointer 的 SQLite 文件位置；不支持部署多副本共享。
CHECKPOINT_DB = PROJECT_ROOT / "checkpoints.db"

# 把 .env 载入进程环境变量，使 ModelSpec.api_key_env 能统一按名字解析。
# load_dotenv 默认不覆盖已存在的环境变量，因此系统环境变量优先级高于 .env。
load_dotenv(PROJECT_ROOT / ".env")


class ModelSpec(BaseModel):
    """单个角色所使用的模型规格。"""

    vendor: str
    model: str
    base_url: str | None = None
    api_key_env: str
    temperature: float | None = None


class LLMSettings(BaseModel):
    """四个 Agent 角色各自绑定的模型。"""

    conversation: ModelSpec
    planner: ModelSpec
    executor: ModelSpec
    reviewer: ModelSpec


class Settings(BaseSettings):
    """全局配置；角色与模型的绑定来自 ``config.toml``。"""

    model_config = SettingsConfigDict(toml_file=PROJECT_ROOT / "config.toml")

    llm: LLMSettings

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """优先级：显式传参 > 环境变量 > config.toml。"""
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls))


settings = Settings()
