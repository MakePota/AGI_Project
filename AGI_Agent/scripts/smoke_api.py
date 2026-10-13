"""对外 HTTP 服务的端到端烟测。

用法（先另开一个终端跑 `uv run agi-agent-api`）：

    uv run python scripts/smoke_api.py
    uv run python scripts/smoke_api.py --base-url http://127.0.0.1:8123
    uv run python scripts/smoke_api.py --goal "看一下 probe.png 里写的是几"

它验的是什么（**核心是 ADR 0015 那条命题**）：

1. `POST /tasks` 建任务，返回 202 与 `task_id`；
2. **全程只用 `GET /tasks/{id}` 轮询**，不用任何事件流，直到任务停下来等人批准
   ——这一步证明"等待批准"是**可查询的状态**，不是挂在连接上的东西；
3. 审批载荷里**必须带参数**（审批看的是将要执行的东西，不是模型写的描述）；
4. 提交决定后任务继续跑完；
5. 错误路径：查不存在的任务应 **404**，对已结束的任务重复提交审批应 **409**；
6. 最后拉一次 SSE，核对事件类型序列与结束帧。

**stdout 只输出 ASCII**：Windows 控制台默认 GBK，直接打印中文会乱码、甚至抛
`UnicodeEncodeError`（CLI 那边踩过一次，见 `cli.py` 里 `_STATUS_MARK` 的注释）。
中文一律写进 `--out` 指定的目录，供人用编辑器查看。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

#: 默认任务用假工具 `wechat.send_message`（WRITE 风险 → 会触发审批），
#: 好处是**不碰网络、不需要登录、不产生真实副作用**，却完整走一遍审批链路。
DEFAULT_GOAL = "给张三发一条微信，内容是：明天见"

_TERMINAL = {"completed", "failed", "cancelled", "needs_human"}


def call(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """发一个 HTTP 请求，返回 (状态码, 解析后的 JSON)。错误码也照常返回，不抛。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, (json.loads(raw) if raw.strip() else {})


def poll_until(
    base: str, task_id: str, wanted: set[str], seconds: float = 180.0
) -> dict:
    """轮询任务状态直到落入 `wanted`。顺便打印状态迁移，便于定位卡在哪一步。"""
    deadline = time.monotonic() + seconds
    seen: list[str] = []
    while time.monotonic() < deadline:
        _, task = call(base, "GET", f"/tasks/{task_id}")
        status = str(task.get("status"))
        if not seen or seen[-1] != status:
            seen.append(status)
            print(f"       poll -> {status}")
        if status in wanted:
            print(f"       transitions: {' -> '.join(seen)}")
            return task
        time.sleep(2)
    raise SystemExit(f"FAIL: 超时等待 {wanted}，最后状态 {seen[-1] if seen else '?'}")


def fetch_events(base: str, task_id: str) -> list[dict]:
    """任务结束后拉一次 SSE，把帧解析成事件列表。

    对**已结束**的任务，这个流会立刻把所有缓冲事件发完并给出结束帧，
    因此可以用普通 HTTP 读取，不必做流式解析。
    """
    events: list[dict] = []
    with urllib.request.urlopen(f"{base}/tasks/{task_id}/events", timeout=30) as resp:
        for line in resp.read().decode("utf-8").splitlines():
            # 按 "data:" 判断，而不是 "data: "（带空格）。
            # SSE 规范里冒号后那个空格是**可选**的：FastAPI 会写 "data: {...}"，
            # Spring 的 SseEmitter 写 "data:{...}"。写死带空格的版本，会在换一个
            # 实现时变成"事件一条都收不到"——实测踩过一次，而且现象很像是服务端没发。
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
                if payload and payload != "{}":
                    events.append(json.loads(payload))
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description="对外 HTTP 服务的端到端烟测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8123")
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument(
        "--out",
        # 默认写到系统临时目录，而不是仓库里的相对路径：否则每次烟测都会在
        # 工作区留下一堆未跟踪文件，稍不注意就被 `git add .` 带进提交。
        default=str(Path(tempfile.gettempdir()) / "agi_smoke_out"),
        help="中文输出写到这里（默认在系统临时目录）",
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = args.base_url

    # --- 1. 建任务 --------------------------------------------------------
    code, task = call(base, "POST", "/tasks", {"goal": args.goal})
    if code != 202:
        raise SystemExit(f"FAIL: POST /tasks 期望 202，实际 {code}: {task}")
    task_id = task["task_id"]
    print(f"[1] POST /tasks -> {code} status={task['status']}")
    print(f"    goal 原样送达: {task['goal'] == args.goal}")

    # --- 2. 纯轮询等到"等待批准" ------------------------------------------
    print("[2] 只用 GET /tasks/{id} 轮询（不订阅任何事件流）")
    task = poll_until(base, task_id, _TERMINAL | {"awaiting_approval"})

    if not task.get("approval"):
        print(f"    该任务没有触发审批，终态 {task['status']}（换一个 WRITE 任务再试）")
        return

    approval = task["approval"]
    (out / "approval.txt").write_text(
        approval["summary"] + "\n\nsteps:\n" + json.dumps(
            approval["steps"], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    steps = approval["steps"]
    print(f"[3] 拿到审批请求 steps={len(steps)}（中文见 {out / 'approval.txt'}）")
    print(f"    载荷带参数: {bool(steps and steps[0].get('arguments'))}")

    # --- 4. 提交决定并等它跑完 --------------------------------------------
    code, _ = call(base, "POST", f"/tasks/{task_id}/approval", {"approved": True})
    if code != 200:
        raise SystemExit(f"FAIL: 提交审批期望 200，实际 {code}")
    print(f"[4] POST /approval -> {code}")

    final = poll_until(base, task_id, _TERMINAL)
    (out / "result.txt").write_text(
        f"status={final['status']}\nresult={final.get('result')}\n"
        f"error={final.get('error')}\n",
        encoding="utf-8",
    )
    print(f"[5] 终态 {final['status']}（中文见 {out / 'result.txt'}）")

    # --- 6. 错误路径 ------------------------------------------------------
    code, _ = call(base, "GET", "/tasks/does-not-exist")
    print(f"[6] GET 不存在的任务 -> {code} (期望 404) {'OK' if code == 404 else 'FAIL'}")
    code, _ = call(base, "POST", f"/tasks/{task_id}/approval", {"approved": True})
    print(f"[7] 对已结束任务再审批 -> {code} (期望 409) {'OK' if code == 409 else 'FAIL'}")

    # --- 8. 事件序列 ------------------------------------------------------
    events = fetch_events(base, task_id)
    seqs = [event["seq"] for event in events]
    types = [event["type"] for event in events]
    print(f"[8] SSE 事件 {len(events)} 条，seq 连续: {seqs == list(range(len(seqs)))}")
    print(f"    类型序列: {', '.join(types)}")
    (out / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nPASS：整条链路走通（详细中文输出在 {out.resolve()}）")


if __name__ == "__main__":
    sys.exit(main())
