"""`AGI_Agent` 的内部服务 API——供 `AGI_Server`（Java）调用。

**没有认证、没有界面**。这是 ADR 0014 重新划定的边界：Agent 提供**内部服务 API**，
但仍然不承担用户认证、不提供客户端页面。

端点：

=========================  ============================================
`POST /tasks`              建任务并开跑，立刻返回
`GET  /tasks/{id}`         当前状态（`approval` 非空就是"正在等你批准"）
`GET  /tasks/{id}/events`  SSE 事件流（可选，只是免轮询的优化）
`POST /tasks/{id}/approval` 提交审批决定并恢复执行
`POST /uploads`            收下一张图片并存到本机，返回路径
`GET  /artifacts/{name}`   取一个已经生成的产物（图片）
=========================  ============================================

**为什么 SSE 与状态查询是两条独立的路**：审批语义不挂在连接上（ADR 0015）。
SSE 断掉、或者客户端压根不用它，靠 `GET /tasks/{id}` 也能完整走完全流程。
正因为如此，SSE 的实现可以很朴素——它坏掉不影响正确性。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from agi_agent.api.contract import TaskRef, UploadResult
from agi_agent.api.runner import TaskRunner
from agi_agent.api.uploads import UploadRejected, save_image
from agi_agent.artifacts import resolve_artifact
from agi_agent.config import CHECKPOINT_DB
from agi_agent.graph.builder import build_graph
from agi_agent.graph.checkpoint import sqlite_checkpointer
from agi_agent.images import MAX_IMAGE_BYTES, sniff_mime

#: SSE 轮询间隔。事件本来就产生于秒级动作，0.25 秒的粒度在体感上就是实时的，
#: 换来的是不必做跨线程唤醒（工作线程往队列里放数据、asyncio 侧负责取，
#: 中间如何唤醒是这类代码最容易出错的地方——而轮询根本没有这个问题）。
_POLL_S = 0.25

#: 心跳间隔。注释行 `: keep-alive` 不进入客户端事件处理，只为让中间层看到连接还活着。
#: 不发心跳的话，代理或负载均衡会把空闲连接当成死连接掐掉（这是 SSE 最常见的"莫名其妙断开"）。
_HEARTBEAT_S = 15.0


class CreateTaskRequest(BaseModel):
    """建任务。**只接受自然语言目标**——参数校验不足的内容交给 planner 处理。"""

    goal: str = Field(min_length=1, description="要完成的事情，自然语言")


class ApprovalDecision(BaseModel):
    """人工审批决定。形状必须与 `authorize` 节点里 `Command(resume=…)` 期望的一致。"""

    approved: bool
    reason: str | None = None


def create_app() -> FastAPI:
    """构造应用。测试与 `main()` 都走这里，避免两处各自拼装。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # checkpointer 在**进程生命周期内只开一次**：它是单文件 SQLite，
        # 反复开关没有意义。这里在主线程打开（setup 建表），随后只由工作线程使用——
        # 串行执行保证了同一时刻只有一个使用方，这正是 runner 存在的理由之一。
        with sqlite_checkpointer(CHECKPOINT_DB) as saver:
            app.state.runner = TaskRunner(build_graph().compile(checkpointer=saver))
            yield
            app.state.runner.shutdown()

    app = FastAPI(title="AGI Agent 内部服务 API", version="0.1.0", lifespan=lifespan)

    def runner(request: Request) -> TaskRunner:
        return request.app.state.runner

    @app.post("/tasks", response_model=TaskRef, status_code=202)
    def create_task(payload: CreateTaskRequest, request: Request) -> TaskRef:
        """建任务并排队开跑。

        返回 **202** 而不是 200：任务只是被受理了，还在跑。客户端拿 `task_id`
        去查状态或订阅事件。
        """
        return runner(request).submit(payload.goal)

    @app.get("/tasks/{task_id}", response_model=TaskRef)
    def get_task(task_id: str, request: Request) -> TaskRef:
        try:
            return runner(request).get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/approval", response_model=TaskRef)
    def approve(task_id: str, payload: ApprovalDecision, request: Request) -> TaskRef:
        """提交审批决定。

        任务不在等待批准时返回 **409**（而不是装作成功）：那说明客户端的状态已经
        过期了（比如另一个客户端已经批过了），它应该重新拉一次状态再决定。
        """
        try:
            return runner(request).approve(task_id, payload.approved, payload.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/tasks/{task_id}/events")
    async def task_events(
        task_id: str,
        request: Request,
        since: int = Query(0, ge=0, description="只返回 seq >= since 的事件，用于断线续传"),
    ) -> StreamingResponse:
        """SSE 事件流。任务进入终态且事件发完后自动结束。"""
        run = runner(request)
        try:
            run.get(task_id)  # 提前探测不存在的任务，好在建流之前就返回 404
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return StreamingResponse(
            _stream(run, task_id, since),
            media_type="text/event-stream",
            # 这两个头是 SSE 的常规要求，缺了会出现"本地正常、过一层代理就卡住不推送"：
            # no-cache 防止中间层缓存事件，X-Accel-Buffering 明确让 nginx 别缓冲。
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/uploads", response_model=UploadResult)
    async def upload_image(file: UploadFile = File(...)) -> dict:
        """收下一张图片并存到本机，返回它的路径。

        路径随后会写进任务目标文本，由 planner 交给图像工具去读——
        图片**不经过任何外部存储**（见 `uploads.py` 里关于 OSS 的说明）。

        **分块读、超限立刻中止**，而不是 `await file.read()` 一次读完：
        后者会把整个请求体灌进内存，一个超大的上传就能把进程拖垮；
        而且等它读完再判断大小，伤害已经造成了（Starlette 没有内置的请求体上限）。
        """
        data = bytearray()
        while chunk := await file.read(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"图片太大：超过 {MAX_IMAGE_BYTES:,} 字节的上限，已中止接收。",
                )
        try:
            return save_image(bytes(data))
        except UploadRejected as exc:
            # 400 而不是 500：这是调用方给的东西不对，不是服务端故障。
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/artifacts/{name}")
    def get_artifact(name: str) -> FileResponse:
        """取一个已经生成的产物文件。

        **名字不合法与文件不存在都回 404**：区分两者会让这个端点变成目录探测工具。

        MIME 仍然按文件头嗅探，不采信存盘时的记录——存盘记录在数据库/checkpoint
        里，和磁盘上的实际内容可能因手工改动而不一致；文件头是唯一的事实。
        """
        try:
            path = resolve_artifact(name)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="没有这个产物") from exc

        try:
            with path.open("rb") as handle:
                mime = sniff_mime(handle.read(16))
        except OSError as exc:
            raise HTTPException(status_code=404, detail="没有这个产物") from exc
        if mime is None:
            raise HTTPException(status_code=404, detail="没有这个产物")

        return FileResponse(
            path,
            media_type=mime,
            filename=name,
            content_disposition_type="inline",
            # 产物名唯一且内容不可变，可以缓存一天。必须是 private：这是一个
            # 没有认证的本机服务，不要让中间代理把它当成公共资源。
            headers={"Cache-Control": "private, max-age=86400"},
        )

    return app


async def _stream(run: TaskRunner, task_id: str, since: int) -> AsyncIterator[str]:
    """产生 SSE 帧。

    事件用 `seq` 做游标，因此**重复请求同一段不会重复消费**——这正是 ADR 0015 里
    "事件带单调序号"那条决定的用处。
    """
    cursor = since
    last_beat = time.monotonic()

    while True:
        events = run.events_since(task_id, cursor)
        for event in events:
            cursor = event.seq + 1
            yield f"data: {event.model_dump_json()}\n\n"

        if run.is_finished(task_id) and not events:
            # 明确发一个结束帧：客户端据此正常关闭，而不是等连接超时。
            yield "event: end\ndata: {}\n\n"
            return

        now = time.monotonic()
        if now - last_beat >= _HEARTBEAT_S:
            last_beat = now
            yield ": keep-alive\n\n"

        await asyncio.sleep(_POLL_S)


def main() -> None:
    """启动服务。

    **默认只绑 127.0.0.1**：这是一个没有认证的服务，绑到 0.0.0.0 等于把"任何能连上
    这台机器的人都能让 Agent 操作你的 Steam 账号、读你的文件"开放出去。要做局域网访问，
    必须先加认证——本期没做（见 ADR 0016 的"本期明确不做"）。
    """
    import uvicorn

    host = os.environ.get("AGI_API_HOST", "127.0.0.1")
    # 默认端口是 8123 而不是 8000：8000 是各类 demo 与工具的默认端口，被占用的
    # 概率很高——本机实测就有一个别的项目的容器占了它。默认值必须能开箱即用，
    # 否则每次启动都要先排查"端口被谁占了"。要改就用 AGI_API_PORT。
    port = int(os.environ.get("AGI_API_PORT", "8123"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
