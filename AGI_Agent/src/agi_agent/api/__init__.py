"""内部服务 API：供 `AGI_Server`（Java）调用的窄接口。

- `contract.py` —— 对外的三个形状（**不要把内部模型加进来**，理由见 ADR 0015）
- `runner.py`   —— 串行任务运行器，中断在这里落成状态
- `app.py`      —— FastAPI 端点与 SSE

**导入顺序有讲究**：`app.py` 会 import `graph.builder`，而图里的 `policies`
又会 import `agi_agent.tools`——工具的注册是**导入副作用**（`tools/__init__.py`
导入 `builtin` 才完成注册）。所以只要走了这条链，工具清单就是完整的；
单独 import `agi_agent.tools.registry` 会拿到一个空注册表。
"""

from agi_agent.api.app import create_app

__all__ = ["create_app"]
