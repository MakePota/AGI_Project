"""命令行入口：把一条用户消息交给对话角色并打印回复。"""

from __future__ import annotations

import sys

from agi_agent.providers.llm import get_model
from agi_agent.schemas import Role


def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        print('用法：uv run agi-agent "你的消息"')
        raise SystemExit(2)

    model = get_model(Role.CONVERSATION)
    reply = model.invoke(prompt)
    print(reply.content)
