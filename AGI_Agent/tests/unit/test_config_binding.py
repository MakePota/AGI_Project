"""配置不变量：模型槽位（``Role``）与配置字段必须严格一一对应。

**为什么要这条测试：** ``LLMSettings`` 的字段是硬编码的，而 pydantic 默认会
**静默忽略**未声明的字段。于是有两种坏法，都不报错、只是不生效：

- 往 ``config.toml`` 加了 ``[llm.xxx]`` 段，却忘了在 ``LLMSettings`` 加字段
  → 那段配置被丢掉，取模型时才发现；
- 在 ``Role`` 加了成员，却忘了在 ``LLMSettings`` 加字段
  → 只有真正用到该槽位的那条路径才会炸，平时一切正常。

这条测试把这两种漂移变成"一提交就失败"。

它**不调用 `get_model`**，因此不需要任何真实密钥，CI 里也能跑。
"""

from __future__ import annotations

import pytest

from agi_agent.config import LLMSettings, settings
from agi_agent.schemas.common import Role


@pytest.mark.parametrize("role", list(Role), ids=[r.value for r in Role])
def test_every_role_has_a_model_binding(role: Role) -> None:
    """每个 `Role` 成员都要能在配置里取到一份完整的 ModelSpec。"""
    spec = getattr(settings.llm, role.value, None)
    assert spec is not None, f"config.toml 里缺少 [llm.{role.value}]"
    assert spec.model, f"[llm.{role.value}] 没写 model"
    assert spec.api_key_env, f"[llm.{role.value}] 没写 api_key_env"


def test_no_orphan_model_slots() -> None:
    """`LLMSettings` 里不能有 `Role` 之外的字段——反过来也要对得上。"""
    role_values = {role.value for role in Role}
    declared = set(LLMSettings.model_fields)
    assert declared == role_values, (
        f"LLMSettings 与 Role 不一致："
        f"只在配置里={sorted(declared - role_values)}，"
        f"只在 Role 里={sorted(role_values - declared)}"
    )
