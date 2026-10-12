"""运行时配置。

模型角色与具体供应商在此解耦：

- 角色到模型的绑定写在 ``config.toml``（可提交，不含密钥）；
- 密钥放在 ``.env`` 或系统环境变量中，本文件只记录环境变量的"名字"。

替换供应商或模型只需修改 ``config.toml``，不需要改动代码。
"""

from __future__ import annotations

import os
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

# 必须先载入 .env：本文件下面若干常量会在导入时读取 os.environ，
# 若 load_dotenv 放在它们之后，.env 里的 AGI_* 配置将全部静默失效。
# load_dotenv 默认不覆盖已存在的环境变量，因此系统环境变量优先级高于 .env。
load_dotenv(PROJECT_ROOT / ".env")

#: LangGraph checkpointer 的 SQLite 文件位置；不支持部署多副本共享。
CHECKPOINT_DB = PROJECT_ROOT / "checkpoints.db"


def _allowed_roots() -> list[Path]:
    """本地文件工具可访问的根目录；默认仅项目根。

    可用环境变量 ``AGI_ALLOWED_ROOTS`` 覆盖，多个路径用系统分隔符（Windows 为 ``;``）。
    """
    raw = os.environ.get("AGI_ALLOWED_ROOTS")
    if raw:
        roots = [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]
        if roots:
            return roots
    return [PROJECT_ROOT]


#: 本地文件工具的沙箱边界。
ALLOWED_ROOTS = _allowed_roots()

#: 浏览器登录会话状态的位置。**等同于凭据**：不得提交、不得被本地文件工具读到。
#: 由 scripts/steam_login.py 人工登录一次后生成。
SECRETS_DIR = PROJECT_ROOT / ".secrets"
STEAM_STATE = SECRETS_DIR / "steam_state.json"

#: 浏览器访问外网所需的代理，例如 ``http://127.0.0.1:7897``。
#: 不设置则由 Chromium 沿用系统默认。国内直连 Steam 不可达，通常必须设置。
BROWSER_PROXY = os.environ.get("AGI_BROWSER_PROXY") or None


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
