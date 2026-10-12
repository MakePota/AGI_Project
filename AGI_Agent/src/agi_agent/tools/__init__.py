"""内置工具、MCP 工具适配和工具注册表。

导入本包即完成内置工具注册，因此对外的入口统一是 ``agi_agent.tools``，
而不是 ``agi_agent.tools.registry``。
"""

from agi_agent.tools import builtin  # noqa: F401  导入即注册内置工具
from agi_agent.tools.registry import ToolSpec, all_specs, catalog_for_prompt, get, register

__all__ = ["ToolSpec", "all_specs", "catalog_for_prompt", "get", "register"]
