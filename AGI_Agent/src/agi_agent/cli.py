"""命令行入口：驱动 Agent 图，并处理人工确认中断。"""

from __future__ import annotations

import sys

from langgraph.types import Command

from agi_agent.config import CHECKPOINT_DB
from agi_agent.graph.builder import build_graph
from agi_agent.graph.checkpoint import sqlite_checkpointer
from agi_agent.graph.state import initial_state
from agi_agent.schemas import new_id


def _ask(payload: dict) -> dict:
    """人工确认环节。这是守则 §6 要求的那个"必要的人工确认"。"""
    print("\n" + payload["summary"] + "\n")
    if input("批准执行？[y/N] ").strip().lower() in ("y", "yes"):
        return {"approved": True}
    reason = input("已拒绝。补充原因（可留空）：").strip()
    return {"approved": False, "reason": reason or None}


def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        print('用法：uv run agi-agent "你的消息"')
        raise SystemExit(2)

    with sqlite_checkpointer(CHECKPOINT_DB) as saver:
        graph = build_graph().compile(checkpointer=saver)
        config = {"configurable": {"thread_id": new_id()}}

        result = graph.invoke(initial_state(prompt), config)

        while "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            result = graph.invoke(Command(resume=_ask(payload)), config)

        print(result["reply"])
