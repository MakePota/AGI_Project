"""conversation 节点：闲聊分支的直接回复，使用普通模型。"""

from __future__ import annotations

from langchain_core.messages import SystemMessage

from agi_agent.graph.state import AgentState
from agi_agent.providers.llm import get_model
from agi_agent.schemas import Role

_SYSTEM = "你是一个可靠的助手。直接用自然语言回答用户，不要提及任何内部流程或角色分工。"


def conversation(state: AgentState) -> dict:
    model = get_model(Role.CONVERSATION)
    reply = model.invoke([SystemMessage(_SYSTEM), *state["messages"]])
    return {"reply": reply.content, "messages": [reply]}
