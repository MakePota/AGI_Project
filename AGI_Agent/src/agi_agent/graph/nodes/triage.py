"""triage 节点：由 planner 角色判定输入是闲聊还是任务。

按已确认的设计，分诊放在第一步、由最强模型承担——判定错误（把任务当闲聊）
的代价远高于多花一次强模型调用。
"""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agi_agent.graph.state import AgentState
from agi_agent.providers.llm import get_model
from agi_agent.providers.llm.structured import invoke_structured
from agi_agent.schemas import Role, TaskKind

_SYSTEM = """你是任务分诊器。判断用户输入属于哪一类：

- chat：闲聊、问候、知识问答、对当前系统本身的提问，不需要操作外部系统。
- task：需要调用工具或操作外部系统才能完成，例如读文件、发消息、打开应用、加购物车。

只做判断，不要回答用户的问题，也不要执行任何动作。"""


class _Triage(BaseModel):
    kind: Literal["chat", "task"]


def triage(state: AgentState) -> dict:
    model = get_model(Role.PLANNER)
    decision = invoke_structured(
        model,
        _Triage,
        [SystemMessage(_SYSTEM), HumanMessage(state["task"].goal)],
    )

    kind = TaskKind(decision.kind)
    return {
        "task": state["task"].model_copy(update={"kind": kind}),
        "messages": [HumanMessage(state["task"].goal)],
    }
