"""任务运行器：把「图 + checkpointer」包成一个可以被 HTTP 层驱动的服务。

三件事是它存在的全部理由，每一件都对应一个已知约束或一条已定的决策：

1. **串行执行。** SQLite checkpointer 没有并发控制——`graph/checkpoint.py` 的注释写明
   「仅适用于单机单进程」，`config.py` 也提醒「不支持部署多副本共享」。所以这里
   **只有一个工作线程**，任务排队跑完一个再跑下一个。这是显式的取舍，不是忘了加并发：
   要真并发，得先按 ADR 0004 把 checkpointer 换成 Redis。

2. **中断不占用线程。** 「等待批准」是一个**状态**，不是挂着的调用（ADR 0015）。
   图停下来后工作线程**就返回了**，等到 `POST /approval` 再排一个新的恢复任务。
   所以断线、刷新页面、换客户端都不会丢——状态本来就存在 checkpointer 里。

3. **事件有界缓冲。** 每个任务保留最近若干条事件，供 SSE 与增量拉取（`since=seq`）使用。
   有界是刻意的：Java 侧也不存事件，只存任务状态与最终结果；如果这里无限增长，
   就等于把"不存事件"的决定从侧面推翻了。

**线程模型**：只有这个工作线程会碰 checkpointer。HTTP 线程只读运行器里的内存状态，
绝不直接调用图——这一点是上面"串行"能成立的前提。
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from langgraph.types import Command

from agi_agent.api.contract import (
    ApprovalRequest,
    ApprovalStep,
    TaskEvent,
    TaskRef,
)
from agi_agent.graph.state import initial_state
from agi_agent.schemas import EventType, TaskStatus, new_id, utc_now

#: 每个任务保留的事件条数上限。超出后丢最旧的——SSE 的增量拉取语义不受影响
#: （`since` 落在被丢掉的区间时，客户端会拿到"当前最早的可读事件"，见 `events_since`）。
_EVENT_BUFFER = 500

#: 事件 payload 里放的结果/错误预览长度。放全文会把事件流撑爆，
#: 完整结果在 `TaskRef.result` 里，需要全文的客户端去读它。
_PREVIEW_CHARS = 400


class GraphLike(Protocol):
    """运行器真正用到的那两个方法。

    用 Protocol 而不是直接写死 LangGraph 的编译产物类型，是为了让单测能塞进一个
    假图——**真实调用要花钱且不确定，不适合做断言**（与项目其它部分的取舍一致）。
    """

    def stream(self, input: Any, config: Any, stream_mode: Any) -> Any: ...

    def get_state(self, config: Any) -> Any: ...


@dataclass
class _Job:
    task_id: str
    #: "start" 开跑新任务；"resume" 带着用户的审批决定继续。
    kind: str
    resume: dict[str, Any] | None = None


@dataclass
class _Record:
    task_id: str
    goal: str
    thread_id: str
    created_at: datetime
    updated_at: datetime
    status: TaskStatus = TaskStatus.RECEIVED
    result: str | None = None
    error: str | None = None
    approval: ApprovalRequest | None = None
    events: deque[TaskEvent] = field(default_factory=lambda: deque(maxlen=_EVENT_BUFFER))
    next_seq: int = 0
    finished: bool = False

    def to_ref(self) -> TaskRef:
        return TaskRef(
            task_id=self.task_id,
            status=self.status,
            goal=self.goal,
            created_at=self.created_at,
            updated_at=self.updated_at,
            result=self.result,
            error=self.error,
            approval=self.approval,
        )


def _preview(text: Any) -> str:
    rendered = str(text).replace("\n", " ")
    return rendered if len(rendered) <= _PREVIEW_CHARS else rendered[:_PREVIEW_CHARS] + "…"


class TaskRunner:
    """串行执行任务，并把执行过程暴露成可查询的状态 + 有界事件流。"""

    def __init__(self, graph: GraphLike) -> None:
        self._graph = graph
        self._records: dict[str, _Record] = {}
        self._lock = threading.RLock()
        self._queue: queue.Queue[_Job | None] = queue.Queue()
        self._worker = threading.Thread(
            target=self._work, name="agi-task-runner", daemon=True
        )
        self._worker.start()

    # ------------------------------------------------------------------ 对外

    def submit(self, goal: str) -> TaskRef:
        """建一个任务并排队开跑。**立刻返回**，不等它跑完。"""
        record = _Record(
            task_id=new_id(),
            goal=goal,
            # thread_id 与 task_id 用同一个值：checkpointer 按 thread_id 存状态，
            # 而外部只认 task_id。两者共用可以避免"哪张表对应哪个 id"的困惑。
            thread_id=new_id(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        with self._lock:
            self._records[record.task_id] = record
            self._emit(record, EventType.TASK_RECEIVED, {"goal": goal})
        self._queue.put(_Job(task_id=record.task_id, kind="start"))
        return record.to_ref()

    def approve(self, task_id: str, approved: bool, reason: str | None = None) -> TaskRef:
        """提交审批决定并排队恢复执行。

        找不到任务、或任务不在等待批准时抛 `KeyError` / `ValueError`——
        HTTP 层据此返回 404 / 409，而不是假装成功。
        """
        with self._lock:
            record = self._require(task_id)
            if record.approval is None:
                raise ValueError(f"任务 {task_id} 当前不在等待批准（status={record.status}）")
            self._emit(
                record,
                EventType.APPROVAL_DECIDED,
                {"approved": approved, "reason": reason},
            )
            record.approval = None
            record.status = TaskStatus.PLANNING
            record.updated_at = utc_now()
            ref = record.to_ref()
        self._queue.put(
            _Job(
                task_id=task_id,
                kind="resume",
                resume={"approved": approved, "reason": reason},
            )
        )
        return ref

    def get(self, task_id: str) -> TaskRef:
        with self._lock:
            return self._require(task_id).to_ref()

    def events_since(self, task_id: str, since: int = 0) -> list[TaskEvent]:
        """取 `seq >= since` 的事件。

        若 `since` 已经落在被环形缓冲丢掉的范围里，就返回当前缓冲里最早的那几条——
        **不能假装无事发生**：客户端据此知道自己漏了一段，可以重新拉一次完整状态。
        """
        with self._lock:
            record = self._require(task_id)
            return [event for event in record.events if event.seq >= since]

    def is_finished(self, task_id: str) -> bool:
        with self._lock:
            return self._require(task_id).finished

    def shutdown(self) -> None:
        """停掉工作线程；在服务退出时调用，避免留下挂着的线程。"""
        self._queue.put(None)
        self._worker.join(timeout=5)

    # ------------------------------------------------------------------ 内部

    def _require(self, task_id: str) -> _Record:
        record = self._records.get(task_id)
        if record is None:
            raise KeyError(f"没有这个任务：{task_id}")
        return record

    def _emit(self, record: _Record, etype: EventType, payload: dict[str, Any]) -> None:
        """追加一条事件。调用方必须已持有 `self._lock`。"""
        record.events.append(
            TaskEvent(seq=record.next_seq, type=etype, at=utc_now(), payload=payload)
        )
        record.next_seq += 1
        record.updated_at = utc_now()

    def _work(self) -> None:
        """工作线程主循环。

        **绝不让异常逃出去**：这个线程一死，整个服务就再也跑不了任何任务，
        而症状只是"提交了但没反应"，极难排查。所以这里兜底捕获所有异常。
        """
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                self._execute(job)
            except Exception as exc:  # noqa: BLE001  见上面的说明：线程不能死
                self._crash(job.task_id, exc)

    def _crash(self, task_id: str, exc: Exception) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.status = TaskStatus.FAILED
            record.error = f"{type(exc).__name__}: {exc}"
            record.finished = True
            self._emit(record, EventType.TASK_FAILED, {"error": _preview(exc)})

    def _execute(self, job: _Job) -> None:
        with self._lock:
            record = self._require(job.task_id)
            thread_id = record.thread_id
            # 中间状态只给一个粗粒度标记（"正在跑"）。每个节点的**权威**状态在
            # checkpointer 里，由 `_settle` 在结束时一次性读出。
            # 中间过程刻意不去猜：猜错的代价是客户端看到一个并不存在的状态迁移，
            # 而收益几乎为零——它只是想知道"还在跑吗"。
            record.status = TaskStatus.EXECUTING
            record.updated_at = utc_now()

        config = {"configurable": {"thread_id": thread_id}}
        graph_input = (
            initial_state(record.goal)
            if job.kind == "start"
            else Command(resume=job.resume)
        )

        # 图的节点是**同步**的（Steam 工具用的是 Playwright 的 sync API），
        # 所以整段跑在这个工作线程里，不碰事件循环。
        for mode, chunk in self._graph.stream(
            graph_input, config, stream_mode=["updates", "custom"]
        ):
            if mode == "custom":
                # 节点在长耗时动作**开始前**推的进度文本（executor 的 _progress）。
                self._emit(record, EventType.TOOL_STARTED, {"text": _preview(chunk)})
                continue
            for node, update in chunk.items():
                self._on_update(record, node, update)

        # 流结束不等于跑完：可能是**停下来等人批准**，也可能是正常结束。
        # 一律以 checkpointer 里的权威状态为准，而不是从事件流里猜。
        self._settle(record, config)

    def _on_update(self, record: _Record, node: str, update: Any) -> None:
        """把一次节点产出翻译成对外事件。"""
        if not isinstance(update, dict):
            return
        with self._lock:
            if node == "triage":
                task = update.get("task")
                self._emit(
                    record,
                    EventType.ROUTED,
                    {"kind": str(getattr(task, "kind", "") or "")},
                )
            elif node == "planner":
                plan = update.get("plan")
                steps = list(getattr(plan, "steps", []) or [])
                # 首次规划与重规划要能分辨。`Plan.revision` 是权威信号，但要注意
                # **它从 1 开始**（planner 里是 `state["revision"] + 1`），
                # 所以"首次"是 `== 1` 而不是 `> 0`——写成 `> 0` 会把首次规划
                # 误报成"修订"，实测踩到过。
                # 也不要用 record.status 去猜：重规划时它并不是 PLANNING。
                revision = int(getattr(plan, "revision", 0) or 0)
                revised = revision > 1
                self._emit(
                    record,
                    EventType.PLAN_REVISED if revised else EventType.PLAN_CREATED,
                    {
                        "steps": [step.description for step in steps],
                        "tools": [step.tool for step in steps if step.tool],
                        "rationale": getattr(plan, "rationale", None),
                    },
                )
            elif node == "executor":
                self._emit_tool_calls(record, update)
            elif node == "reviewer":
                review = update.get("review")
                passed = bool(getattr(review, "passed", False))
                self._emit(
                    record,
                    EventType.REVIEW_PASSED if passed else EventType.REVIEW_REJECTED,
                    {"reason": getattr(review, "reason", None)},
                )
            # conversation / authorize / respond 不单独发事件：
            # 前者的结果就是最终回复，后两者要么由中断承载、要么在结束时统一结算。

    def _emit_tool_calls(self, record: _Record, update: dict[str, Any]) -> None:
        for call in update.get("tool_calls") or []:
            payload: dict[str, Any] = {
                "tool": call.tool,
                "status": str(call.status),
                "arguments": call.arguments,
            }
            if call.result is not None:
                payload["result"] = _preview(call.result)
            if call.error:
                payload["error"] = _preview(call.error)
            self._emit(record, EventType.TOOL_FINISHED, payload)

    def _settle(self, record: _Record, config: dict[str, Any]) -> None:
        """流结束后，从 checkpointer 读权威状态并结算。"""
        snapshot = self._graph.get_state(config)
        interrupts = tuple(getattr(snapshot, "interrupts", ()) or ())

        if interrupts:
            self._await_approval(record, interrupts[0])
            return

        values = dict(getattr(snapshot, "values", {}) or {})
        task = values.get("task")
        status = getattr(task, "status", TaskStatus.FAILED)
        result = values.get("reply") or getattr(task, "result", None)
        error = getattr(task, "error", None)

        with self._lock:
            record.status = status
            record.result = result
            record.error = error
            record.finished = True
            if status is TaskStatus.COMPLETED:
                self._emit(record, EventType.TASK_COMPLETED, {"reply": _preview(result)})
            elif status is TaskStatus.NEEDS_HUMAN:
                # "停下等人"不是失败（ADR 0010/0011）：可能是要核实结果，
                # 也可能是要修环境。用专门的事件类型，不要并进 TASK_FAILED。
                self._emit(
                    record,
                    EventType.TASK_NEEDS_HUMAN,
                    {"status": status.value, "detail": _preview(result or error)},
                )
            else:
                self._emit(
                    record,
                    EventType.TASK_FAILED,
                    {"status": status.value, "error": _preview(error or result)},
                )

    def _await_approval(self, record: _Record, interrupt: Any) -> None:
        """把中断载荷转成 `ApprovalRequest`，并让任务进入"等待批准"。"""
        value = getattr(interrupt, "value", None) or {}
        steps = [
            ApprovalStep(
                index=int(step.get("index", 0)),
                description=str(step.get("description", "")),
                tool=step.get("tool"),
                arguments=dict(step.get("arguments") or {}),
                risk=step.get("risk"),
            )
            for step in (value.get("steps") or [])
        ]
        request = ApprovalRequest(summary=str(value.get("summary", "")), steps=steps)

        with self._lock:
            record.approval = request
            record.status = TaskStatus.AWAITING_APPROVAL
            record.updated_at = utc_now()
            # 工作线程到此为止就**结束**了，不在这里等待——等用户提交决定时
            # 会排一个新的恢复任务（ADR 0015：中断是状态，不是挂着的调用）。
            self._emit(
                record,
                EventType.APPROVAL_REQUESTED,
                {"summary": request.summary, "steps": [s.model_dump(mode="json") for s in steps]},
            )
