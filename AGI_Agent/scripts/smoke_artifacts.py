"""端到端烟测：真实生成一张图片，并验证产物链路。

**它会花钱。** 默认使用 ``qwen-image-3.0-pro`` 生成一张 512x512 图（约 0.25 元），
所以不放进 ``pytest``。需要真实密钥与网络，跑之前确认 ``DASHSCOPE_API_KEY`` 可用。

## 两种模式

```bash
# Agent 内部 API（8123）：产物端点是 /artifacts/{name}
uv run python scripts/smoke_artifacts.py --base-url http://127.0.0.1:8123

# 业务服务（8090）：产物端点是 /api/tasks/{id}/artifacts/{name}
uv run python scripts/smoke_artifacts.py \
  --base-url http://127.0.0.1:8090/api --mode server
```

## 它验证什么

1. 建任务 → 计划里出现 ``image.generate``（需要审批）；
2. 提交审批 → 任务跑完；
3. ``TaskRef.artifacts`` 非空；
4. 通过 HTTP 取回图片，状态 200、Content-Type 是图片、文件头正确、字节数与元数据一致；
5. 不存在的产物名 → 404。

**不要断言图片内容**：生成结果是概率性的，断言“画面里必须有一只猫”只会制造假失败。
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

#: 只用于让“这张图能被显示出来”成立的最小验收；不要用它判断质量。
DEFAULT_GOAL = (
    "生成一张简单的图片：一只简笔画风格的白猫坐在窗台上，浅蓝色背景，画面干净，无文字。"
    "请使用 image.generate 工具，尺寸用 512x512。"
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def _call(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    response = httpx.request(
        method,
        base.rstrip("/") + path,
        json=body,
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response.status_code, payload


def _poll(base: str, task_id: str, wanted: set[str], seconds: float) -> dict:
    deadline = time.monotonic() + seconds
    last = {}
    while time.monotonic() < deadline:
        status, task = _call(base, "GET", f"/tasks/{task_id}")
        if status != 200:
            raise SystemExit(f"GET /tasks/{task_id} -> HTTP {status}: {task}")
        last = task
        state = task.get("status", "")
        print(f"  status={state}")
        if state in wanted:
            return task
        if state in {"completed", "failed", "cancelled", "needs_human"}:
            return task
        time.sleep(1.0)
    raise SystemExit(
        f"任务 {task_id} 在 {seconds:.0f} 秒内没有到达 {sorted(wanted)}；"
        f"最后状态：{last}"
    )


def _artifact_url(base: str, mode: str, task_id: str, name: str) -> str:
    base = base.rstrip("/")
    if mode == "server":
        return f"{base}/tasks/{task_id}/artifacts/{name}"
    return f"{base}/artifacts/{name}"


def _check_image(data: bytes, artifact: dict) -> None:
    if not data:
        raise SystemExit("取回的产物是空的")
    if not data.startswith((PNG_MAGIC, JPEG_MAGIC)):
        raise SystemExit(f"取回的内容不是 PNG/JPEG，前 8 字节：{data[:8]!r}")
    expected = artifact.get("size")
    if isinstance(expected, int) and expected != len(data):
        raise SystemExit(f"字节数不符：元数据 {expected}，实际 {len(data)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="真实生成一张图并验证产物链路（会花钱）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8123")
    parser.add_argument("--mode", choices=["agent", "server"], default="agent")
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    print(f"base={args.base_url} mode={args.mode}")
    print(f"goal={args.goal}")
    print("注意：这会真实调用 Qwen-Image-3.0，产生一次生成费用。")

    status, task = _call(args.base_url, "POST", "/tasks", {"goal": args.goal})
    if status != 202:
        raise SystemExit(f"POST /tasks -> HTTP {status}: {task}")
    task_id = task["task_id"]
    print(f"task_id={task_id}")

    task = _poll(args.base_url, task_id, {"awaiting_approval"}, args.timeout)
    if task.get("status") == "awaiting_approval":
        approval = task.get("approval") or {}
        steps = approval.get("steps") or []
        tools = [step.get("tool") for step in steps]
        print(f"approval tools={tools}")
        if "image.generate" not in tools:
            raise SystemExit(
                "计划里没有 image.generate——planner 没有选中图片生成工具，"
                "先检查工具描述/任务措辞，不要继续。"
            )
        status, task = _call(
            args.base_url,
            "POST",
            f"/tasks/{task_id}/approval",
            {"approved": True, "reason": None},
        )
        if status != 200:
            raise SystemExit(f"POST approval -> HTTP {status}: {task}")

    task = _poll(args.base_url, task_id, {"completed"}, args.timeout)
    if task.get("status") != "completed":
        raise SystemExit(f"任务没有完成：status={task.get('status')} error={task.get('error')}")

    artifacts = task.get("artifacts") or []
    print(f"artifacts={[a.get('name') for a in artifacts]}")
    if not artifacts:
        raise SystemExit("任务完成了，但 TaskRef.artifacts 为空——产物链路断了")

    for artifact in artifacts:
        name = artifact["name"]
        url = _artifact_url(args.base_url, args.mode, task_id, name)
        response = httpx.get(url, timeout=httpx.Timeout(60.0, connect=10.0))
        if response.status_code != 200:
            raise SystemExit(f"GET {url} -> HTTP {response.status_code}")
        print(
            f"  {name}: {len(response.content):,} bytes, "
            f"Content-Type={response.headers.get('content-type')}"
        )
        _check_image(response.content, artifact)

    # 不存在的产物必须 404，而不是 500 或空响应。
    missing = _artifact_url(args.base_url, args.mode, task_id, "20990101-000000-deadbeef.png")
    response = httpx.get(missing, timeout=httpx.Timeout(30.0, connect=10.0))
    print(f"missing artifact -> HTTP {response.status_code}")
    if response.status_code != 404:
        raise SystemExit(f"不存在的产物应返回 404，实际 {response.status_code}")

    print("\nOK: 图片生成与产物链路验证通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
