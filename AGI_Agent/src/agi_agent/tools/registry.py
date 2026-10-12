"""工具注册表：名称 -> 实现、参数签名与风险等级。

两个关键约束：

1. **风险等级在这里定义，不由模型决定。** 模型不能自己声明某个动作是安全的，
   否则一个被误导或被注入的计划只要自报 risk=read，就能绕过人工确认。
   计划里的风险值一律由 ``policies`` 依据本表重新赋值。
2. **参数签名必须对 planner 可见。** 不发布签名，模型只能猜参数名，
   会在重规划里反复猜测并最终失败。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from agi_agent.schemas import RiskLevel


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的元数据与实现。"""

    name: str
    description: str
    risk: RiskLevel
    parameters: type[BaseModel]
    func: Callable[..., Any]

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """按声明校验参数。

        校验失败时抛出带具体字段名的 ``ValueError``——这条信息会进入执行记录，
        让 reviewer 和 planner 知道该怎么改，而不是收到一个无意义的 TypeError。
        """
        try:
            return self.parameters(**arguments).model_dump()
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in exc.errors()
            )
            raise ValueError(
                f"参数不符合 {self.name} 的签名（{details}）。"
                f"该工具接受的参数是：{', '.join(self.parameters.model_fields)}"
            ) from exc

    def render_signature(self) -> str:
        """渲染成给 planner 看的参数说明。"""
        schema = self.parameters.model_json_schema()
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        if not props:
            return "无参数"
        parts = []
        for name, spec in props.items():
            kind = spec.get("type", "any")
            desc = spec.get("description")
            mark = "" if name in required else "（可选）"
            parts.append(f"{name}: {kind}{mark}" + (f" — {desc}" if desc else ""))
        return "；".join(parts)


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> None:
    """注册一个工具；重名视为编程错误。"""
    if spec.name in _REGISTRY:
        raise ValueError(f"工具重复注册：{spec.name}")
    _REGISTRY[spec.name] = spec


def get(name: str) -> ToolSpec:
    """按名称取回工具。"""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"未注册的工具：{name}（已注册：{sorted(_REGISTRY)}）") from None


def all_specs() -> list[ToolSpec]:
    """按名称排序返回全部工具。"""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def catalog_for_prompt() -> str:
    """渲染给 planner 看的工具清单，含完整参数签名。"""
    return "\n".join(
        f"- {s.name}（风险：{s.risk.value}）：{s.description}\n"
        f"    参数：{s.render_signature()}"
        for s in all_specs()
    )
