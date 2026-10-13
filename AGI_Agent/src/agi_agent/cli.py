"""命令行入口：驱动 Agent 图，并处理人工确认中断。

**过程必须可见。** 一次工具调用可能要跑几十秒（开浏览器、导航、轮询复核）。
旧实现只在最后打印一次回复，中途屏幕上什么都不动——用户会以为是卡死了，
而其实它正在正常干活。所以这里改成流式：节点产出即打印，
工具调用**开始之前**就先打一行。
"""

from __future__ import annotations

import sys

from langgraph.types import Command

from agi_agent.config import CHECKPOINT_DB
from agi_agent.graph.builder import build_graph
from agi_agent.graph.checkpoint import sqlite_checkpointer
from agi_agent.graph.state import initial_state
from agi_agent.schemas import ToolCallStatus, new_id

#: 工具调用结局 -> 进度行前缀。
#: `[?]` 与 `[!]` 是两类会**当场停下任务**的结局，含义见 tools/errors.py：
#: `[?]` 结果不确定（去核实），`[!]` 前提条件不满足（去修复环境）。
#
#: **全部用 ASCII，不要图好看换成 ✓/✗**：Windows 控制台默认编码是 GBK，
#: 编不出 U+2713，一旦输出被重定向到管道（日志、CI、脚本），
#: `print` 会直接抛 UnicodeEncodeError 把整个任务打断——本功能实测时就踩到了。
#: 交互式终端里看着是好的，所以这种坑只在"没人盯着看"的时候炸。
_STATUS_MARK = {
    ToolCallStatus.SUCCEEDED: "[OK]",
    ToolCallStatus.FAILED: "[X]",
    ToolCallStatus.OUTCOME_UNKNOWN: "[?]",
    ToolCallStatus.PRECONDITION_FAILED: "[!]",
}


def _ask(payload: dict) -> dict:
    """人工确认环节。这是守则 §6 要求的那个「必要的人工确认」。"""
    print("\n" + payload["summary"] + "\n")
    if input("批准执行？[y/N] ").strip().lower() in ("y", "yes"):
        return {"approved": True}
    reason = input("已拒绝。补充原因（可留空）：").strip()
    return {"approved": False, "reason": reason or None}


def _report(update: object) -> None:
    """把一次节点产出压成一两行进度。"""
    if not isinstance(update, dict):
        return
    for call in update.get("tool_calls") or []:
        mark = _STATUS_MARK.get(call.status, "[-]")
        print(f"  {mark} {call.tool}")
        detail = call.result if call.status is ToolCallStatus.SUCCEEDED else call.error
        if detail:
            print(f"      {str(detail).splitlines()[0][:140]}")


def _drive(graph, config, graph_input) -> str:
    """跑图直到结束或需要人工确认，返回最终回复。"""
    reply = "（没有产生回复）"
    while True:
        pending: list = []
        # custom 模式让节点能在**长耗时动作开始前**就把进度推出来。
        for mode, chunk in graph.stream(
            graph_input, config, stream_mode=["updates", "custom"]
        ):
            if mode == "custom":
                print(chunk)
                continue
            for node, update in chunk.items():
                if node == "__interrupt__":
                    pending.append(update)
                    continue
                _report(update)
                if isinstance(update, dict) and update.get("reply"):
                    reply = update["reply"]
        if not pending:
            return reply
        graph_input = Command(resume=_ask(pending[0][0].value))


def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        print('用法：uv run agi-agent "你的消息"')
        raise SystemExit(2)

    with sqlite_checkpointer(CHECKPOINT_DB) as saver:
        graph = build_graph().compile(checkpointer=saver)
        config = {"configurable": {"thread_id": new_id()}}
        reply = _drive(graph, config, initial_state(prompt))

    print("\n" + reply)
