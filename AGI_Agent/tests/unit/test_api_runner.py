"""任务运行器的测试。

**用假图，不碰真实模型。** 真实调用要密钥、要网络、要花钱，而且结果是概率性的，
不适合做断言——这与项目其它测试的取舍一致（见 `test_uncertain_outcome.py` 的说明）。

这里要钉住的是**运行器自己的契约**，每一条都对应一个已知约束或一篇 ADR：

- 任务串行执行（SQLite checkpointer 不支持并发）
- 中断落成**可查询状态**，且不占用工作线程（ADR 0015）
- 「停下等人」不被报成失败（ADR 0010 / 0011）
- 工作线程不会因为某个任务出错而死掉（死了以后症状极难排查）
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from agi_agent.api.contract import TaskRef
from agi_agent.api.runner import TaskRunner
from agi_agent.schemas import (
    EventType,
    Plan,
    PlanStep,
    RiskLevel,
    Task,
    TaskKind,
    TaskStatus,
    ToolCall,
    ToolCallStatus,
)

# --------------------------------------------------------------------------
# 假图：够用的剧本播放器
# --------------------------------------------------------------------------


class _FakeInterrupt:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value


class _FakeSnapshot:
    def __init__(self, values: dict[str, Any], interrupts: tuple = ()) -> None:
        self.values = values
        self.interrupts = interrupts


class _Round:
    """一轮 stream：产出的事件 + 这轮结束时 checkpointer 里的权威状态。"""

    def __init__(
        self,
        chunks: list[tuple[str, Any]],
        values: dict[str, Any],
        interrupts: tuple = (),
    ) -> None:
        self.chunks = chunks
        self.values = values
        self.interrupts = interrupts


class _FakeGraph:
    def __init__(self, rounds: list[_Round]) -> None:
        self._rounds = list(rounds)
        self.inputs: list[Any] = []
        self._active = 0
        self.max_active = 0
        self._guard = threading.Lock()
        self._current = _Round([], {})

    # 形参用不到但要保留名字：这是 GraphLike 协议的签名，运行器按关键字传 stream_mode。
    def stream(self, graph_input: Any, config: Any, stream_mode: Any = None):  # noqa: ARG002
        self.inputs.append(graph_input)
        with self._guard:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            self._current = self._rounds.pop(0)
            yield from self._current.chunks
            time.sleep(0.02)  # 让"并发"有机会暴露：真串行的话这里谁也插不进来
        finally:
            with self._guard:
                self._active -= 1

    def get_state(self, config: Any) -> _FakeSnapshot:  # noqa: ARG002  同上
        return _FakeSnapshot(self._current.values, self._current.interrupts)


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def runner_factory():
    created: list[TaskRunner] = []

    def make(graph: _FakeGraph) -> TaskRunner:
        runner = TaskRunner(graph)
        created.append(runner)
        return runner

    yield make
    for runner in created:
        runner.shutdown()


# --------------------------------------------------------------------------
# 剧本素材
# --------------------------------------------------------------------------

_APPROVAL_VALUE = {
    "type": "approval_requested",
    "summary": (
        "即将执行以下计划：\n"
        "  1. [需确认] 把游戏加入购物车（steam.add_to_cart）\n"
        "      参数：game=Elden Ring"
    ),
    "steps": [
        {
            "index": 0,
            "description": "把游戏加入购物车",
            "tool": "steam.add_to_cart",
            "arguments": {"game": "Elden Ring"},
            "risk": "write",
            "depends_on": [],
        }
    ],
}


def _plan_update(revision: int = 1) -> dict[str, Any]:
    """一次规划产出，**按 LangGraph 的真实形状包装**：`{节点名: 节点返回的 dict}`。

    `revision` 默认给 1 而不是 0——planner 实际就是从 1 开始的
    （`state["revision"] + 1`）。测试里自己编一个 0 就测不出真实行为。

    注意这层包装不是装饰：早先的假图直接给 `{"plan": ...}`，于是节点名变成了
    `"plan"`，`_on_update` 里所有按节点名分派的逻辑**一个分支都没被走到**，
    测试却全绿。这个教训值得写在这里——**假图必须模仿真形状，否则测试只是在测自己**。
    """
    plan = Plan(
        task_id="t",
        revision=revision,
        steps=[
            PlanStep(
                index=0,
                description="把游戏加入购物车",
                tool="steam.add_to_cart",
                arguments={"game": "Elden Ring"},
            )
        ],
        rationale="用户要求加购",
    )
    return {"planner": {"plan": plan}}


def _triage_update(kind: str = "task") -> dict[str, Any]:
    return {"triage": {"task": Task(goal="买游戏", kind=TaskKind(kind))}}


def _executor_update() -> dict[str, Any]:
    call = ToolCall(
        task_id="t",
        step_index=0,
        tool="steam.add_to_cart",
        arguments={"game": "Elden Ring"},
        risk=RiskLevel.WRITE,
        status=ToolCallStatus.SUCCEEDED,
        result="已把「Elden Ring」加入购物车。",
    )
    return {"executor": {"tool_calls": [call]}}


def _done_values(status: TaskStatus, reply: str | None = None) -> dict[str, Any]:
    return {
        "task": Task(goal="买游戏", status=status, result=reply),
        "reply": reply,
    }


# --------------------------------------------------------------------------
# 终止结算：三类结局必须分开
# --------------------------------------------------------------------------


def test_completed_task_records_result_and_event(runner_factory) -> None:
    """一次完整任务要产出可读的事件序列——**每个节点都被翻译对**。

    这条同时是对 `_on_update` 分派的覆盖：它要求 triage / planner / executor
    三个节点的产出都真的变成了对应事件，而不是因为节点名对不上被静默跳过。
    """
    graph = _FakeGraph(
        [
            _Round(
                [
                    ("updates", _triage_update()),
                    ("updates", _plan_update()),
                    ("custom", "  → 正在调用 steam.add_to_cart …"),
                    ("updates", _executor_update()),
                ],
                _done_values(TaskStatus.COMPLETED, "已加入购物车"),
            )
        ]
    )
    runner = runner_factory(graph)
    ref = runner.submit("把 Elden Ring 加入购物车")

    assert _wait(lambda: runner.is_finished(ref.task_id))
    final = runner.get(ref.task_id)
    assert final.status is TaskStatus.COMPLETED
    assert final.result == "已加入购物车"

    types = [event.type for event in runner.events_since(ref.task_id)]
    assert types == [
        EventType.TASK_RECEIVED,
        EventType.ROUTED,
        EventType.PLAN_CREATED,
        EventType.TOOL_STARTED,  # 来自节点的 custom 进度推送
        EventType.TOOL_FINISHED,
        EventType.TASK_COMPLETED,
    ]
    # 工具事件必须带上参数与结果——列表页要显示"调了什么、结果如何"。
    finished = runner.events_since(ref.task_id)[-2]
    assert finished.payload["tool"] == "steam.add_to_cart"
    assert finished.payload["arguments"] == {"game": "Elden Ring"}
    assert "Elden Ring" in finished.payload["result"]


def test_needs_human_is_not_reported_as_failure(runner_factory) -> None:
    """「停下等人」有自己的事件类型，不能被并进 TASK_FAILED。

    这是 ADR 0010 / 0011 的直接后果：那两篇 ADR 花了很多笔墨区分
    「确定失败」「结果不确定」「前提条件不满足」，如果对外事件又把后两者
    说成"失败"，那些区分就在最外层被抹平了。
    """
    graph = _FakeGraph(
        [_Round([], _done_values(TaskStatus.NEEDS_HUMAN, "登录过期，请重新登录后再试"))]
    )
    runner = runner_factory(graph)
    ref = runner.submit("加购")

    assert _wait(lambda: runner.is_finished(ref.task_id))
    types = [event.type for event in runner.events_since(ref.task_id)]
    assert EventType.TASK_NEEDS_HUMAN in types
    assert EventType.TASK_FAILED not in types
    assert runner.get(ref.task_id).status is TaskStatus.NEEDS_HUMAN


def test_failed_task_records_error(runner_factory) -> None:
    graph = _FakeGraph([_Round([], _done_values(TaskStatus.FAILED, None))])
    runner = runner_factory(graph)
    ref = runner.submit("干活")

    assert _wait(lambda: runner.is_finished(ref.task_id))
    assert runner.get(ref.task_id).status is TaskStatus.FAILED
    assert runner.events_since(ref.task_id)[-1].type is EventType.TASK_FAILED


# --------------------------------------------------------------------------
# 中断：这是整套设计的核心
# --------------------------------------------------------------------------


def test_interrupt_becomes_queryable_state_not_a_blocked_call(runner_factory) -> None:
    """图停下来后，任务进入「等待批准」，并且**工作线程立刻释放**。

    这条测试同时钉住两件事：
    1. `GET /tasks/{id}` 能直接读到审批载荷（不必依赖任何事件流）；
    2. 之后还能再排一个任务并跑完——说明工作线程没有被那次中断占住。
    """
    graph = _FakeGraph(
        [
            _Round(
                [("updates", _plan_update())],
                {},
                interrupts=(_FakeInterrupt(_APPROVAL_VALUE),),
            ),
            _Round([], _done_values(TaskStatus.COMPLETED, "已加入购物车")),
        ]
    )
    runner = runner_factory(graph)
    ref = runner.submit("把 Elden Ring 加入购物车")

    assert _wait(lambda: runner.get(ref.task_id).approval is not None)
    waiting = runner.get(ref.task_id)
    assert waiting.status is TaskStatus.AWAITING_APPROVAL
    assert not runner.is_finished(ref.task_id)

    # 审批载荷必须带上参数——审批看的应该是"真正会被执行的东西"。
    assert waiting.approval is not None
    assert "steam.add_to_cart" in waiting.approval.summary
    assert waiting.approval.steps[0].arguments == {"game": "Elden Ring"}
    assert waiting.approval.steps[0].risk is RiskLevel.WRITE


def test_approve_resumes_and_finishes(runner_factory) -> None:
    graph = _FakeGraph(
        [
            _Round([], {}, interrupts=(_FakeInterrupt(_APPROVAL_VALUE),)),
            _Round([("updates", _executor_update())], _done_values(TaskStatus.COMPLETED, "已加入")),
        ]
    )
    runner = runner_factory(graph)
    ref = runner.submit("加购")
    assert _wait(lambda: runner.get(ref.task_id).approval is not None)

    runner.approve(ref.task_id, approved=True)
    assert _wait(lambda: runner.is_finished(ref.task_id))

    assert runner.get(ref.task_id).status is TaskStatus.COMPLETED
    assert runner.get(ref.task_id).approval is None
    types = [event.type for event in runner.events_since(ref.task_id)]
    assert EventType.APPROVAL_REQUESTED in types
    assert EventType.APPROVAL_DECIDED in types


def test_approve_uses_command_resume_with_the_expected_shape(runner_factory) -> None:
    """恢复必须用 `Command(resume={...})`，且形状要与 `authorize` 节点读的一致。

    形状写错的后果是**审批决定被静默忽略**——节点读不到 `approved`，当成没批准，
    任务莫名其妙被取消。所以这里把形状钉死。
    """
    graph = _FakeGraph(
        [
            _Round([], {}, interrupts=(_FakeInterrupt(_APPROVAL_VALUE),)),
            _Round([], _done_values(TaskStatus.COMPLETED, "ok")),
        ]
    )
    runner = runner_factory(graph)
    ref = runner.submit("加购")
    assert _wait(lambda: runner.get(ref.task_id).approval is not None)

    runner.approve(ref.task_id, approved=False, reason="不想买")
    assert _wait(lambda: runner.is_finished(ref.task_id))

    resumed = graph.inputs[-1]
    assert resumed.resume == {"approved": False, "reason": "不想买"}


def test_approve_when_not_waiting_raises_value_error(runner_factory) -> None:
    """不在等待批准时提交审批要报错，不能装作成功（HTTP 层据此返回 409）。"""
    graph = _FakeGraph([_Round([], _done_values(TaskStatus.COMPLETED, "ok"))])
    runner = runner_factory(graph)
    ref = runner.submit("闲聊")
    assert _wait(lambda: runner.is_finished(ref.task_id))

    with pytest.raises(ValueError, match="不在等待批准"):
        runner.approve(ref.task_id, approved=True)


def test_unknown_task_raises_key_error(runner_factory) -> None:
    """查不到的任务抛 KeyError，HTTP 层据此返回 404。"""
    runner = runner_factory(_FakeGraph([]))
    with pytest.raises(KeyError):
        runner.get("does-not-exist")


# --------------------------------------------------------------------------
# 事件流：序号、增量、有界
# --------------------------------------------------------------------------


def test_events_have_monotonic_seq_and_support_incremental_read(runner_factory) -> None:
    graph = _FakeGraph(
        [
            _Round(
                [("custom", "第一步"), ("custom", "第二步")],
                _done_values(TaskStatus.COMPLETED, "done"),
            )
        ]
    )
    runner = runner_factory(graph)
    ref = runner.submit("干活")
    assert _wait(lambda: runner.is_finished(ref.task_id))

    all_events = runner.events_since(ref.task_id)
    assert [event.seq for event in all_events] == list(range(len(all_events)))

    tail = runner.events_since(ref.task_id, since=2)
    assert all(event.seq >= 2 for event in tail)
    assert len(tail) == len(all_events) - 2


def test_first_plan_is_created_while_a_redo_is_revised(runner_factory) -> None:
    """首次规划不能被报成「修订」。

    踩过的坑：`Plan.revision` **从 1 开始**（planner 里是 `state["revision"] + 1`），
    我最初用 `revision > 0` 判断"是否修订"，结果第一次规划也被说成修订——
    真实的事件序列里确实这样出现过。对外事件说错话，前端就会显示错的东西。
    """
    graph = _FakeGraph(
        [
            _Round(
                [
                    ("updates", _plan_update(revision=1)),  # 首次
                    ("updates", _plan_update(revision=2)),  # 被驳回后重来
                ],
                _done_values(TaskStatus.COMPLETED, "done"),
            )
        ]
    )
    runner = runner_factory(graph)
    ref = runner.submit("干活")
    assert _wait(lambda: runner.is_finished(ref.task_id))

    types = [event.type for event in runner.events_since(ref.task_id)]
    assert types.count(EventType.PLAN_CREATED) == 1
    assert types.count(EventType.PLAN_REVISED) == 1
    assert types.index(EventType.PLAN_CREATED) < types.index(EventType.PLAN_REVISED)


def test_event_buffer_is_bounded(runner_factory) -> None:
    """事件缓冲必须有界——否则等于偷偷推翻了「Server 不存事件」的决定。"""
    from agi_agent.api import runner as runner_module

    chunks = [("custom", f"进度 {i}") for i in range(runner_module._EVENT_BUFFER + 50)]
    graph = _FakeGraph([_Round(chunks, _done_values(TaskStatus.COMPLETED, "done"))])
    runner = runner_factory(graph)
    ref = runner.submit("干很多活")
    assert _wait(lambda: runner.is_finished(ref.task_id))

    kept = runner.events_since(ref.task_id)
    assert len(kept) <= runner_module._EVENT_BUFFER


# --------------------------------------------------------------------------
# 串行与健壮性
# --------------------------------------------------------------------------


def test_tasks_run_one_at_a_time(runner_factory) -> None:
    """任务必须串行：SQLite checkpointer 不支持并发写入。

    这是本项目的硬约束（见 `graph/checkpoint.py` 的注释），不是性能取舍。
    真并发跑两个任务会争用同一个 db 文件，症状是随机的锁错误。
    """
    graph = _FakeGraph(
        [
            _Round([], _done_values(TaskStatus.COMPLETED, "1")),
            _Round([], _done_values(TaskStatus.COMPLETED, "2")),
            _Round([], _done_values(TaskStatus.COMPLETED, "3")),
        ]
    )
    runner = runner_factory(graph)
    refs = [runner.submit(f"任务 {i}").task_id for i in range(3)]

    assert _wait(lambda: all(runner.is_finished(t) for t in refs), timeout=10.0)
    assert graph.max_active == 1, "同一时刻只允许一个任务在跑"


def test_worker_survives_a_crashing_task(runner_factory) -> None:
    """一个任务把工作线程搞死，整个服务就再也跑不了任何任务——而症状只是
    "提交了没反应"。所以异常必须被兜住，且后续任务仍能正常执行。"""

    class _ExplodingGraph(_FakeGraph):
        def stream(self, graph_input, config, stream_mode=None):
            if len(self.inputs) == 0:
                self.inputs.append(graph_input)
                raise RuntimeError("图炸了")
            yield from super().stream(graph_input, config, stream_mode)

    graph = _ExplodingGraph([_Round([], _done_values(TaskStatus.COMPLETED, "第二次成功"))])
    runner = runner_factory(graph)

    first = runner.submit("会炸的任务").task_id
    assert _wait(lambda: runner.is_finished(first))
    assert runner.get(first).status is TaskStatus.FAILED
    assert "图炸了" in (runner.get(first).error or "")

    second = runner.submit("正常任务").task_id
    assert _wait(lambda: runner.is_finished(second))
    assert runner.get(second).status is TaskStatus.COMPLETED


# --------------------------------------------------------------------------
# 契约：对外形状不夹带内部字段
# --------------------------------------------------------------------------


def test_task_ref_exposes_only_the_agreed_shape(runner_factory) -> None:
    """对外形状是**窄**的。内部模型（Plan/ToolCall/Review 等）不得出现在这里——
    那会把这个契约变成内部实现的镜像，以后每加一个字段都要考虑 Java 侧兼容。
    """
    runner = runner_factory(_FakeGraph([]))
    ref = runner.submit("干活")
    assert isinstance(ref, TaskRef)
    assert set(ref.model_dump()) == {
        "task_id",
        "status",
        "goal",
        "created_at",
        "updated_at",
        "result",
        "error",
        "approval",
    }
