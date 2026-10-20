"""探针：确认 Qwen-Image-3.0 到底能用哪个端点、请求体长什么样。

## 为什么需要它

官方文档称旧域名 `https://dashscope.aliyuncs.com` 仍可正常使用，但**图片接口的示例
全部用的是业务空间专属域名**（`{WorkspaceId}.cn-beijing.maas.aliyuncs.com`）。
本机 `.env` 里没有 WorkspaceId，所以“旧域名到底支不支持 `/images/generations`”
是一个**只能实测**的问题，不能照着文档猜。

这个脚本把需要确认的事实一次问清楚：

1. 旧域名的 OpenAI 兼容端点是否可用；
2. 如果不可用，DashScope 原生同步端点是否可用；
3. 图生图（I2I）的 `image` 字段是字符串还是数组、是否接受 data URL；
4. 错误响应的形状（状态码、`error.code`），供工具的错误分类参考；
5. 实际耗时与 1K PNG 的字节数，供超时与体积上限取值。

## 怎么用

```bash
# 默认只打印请求，不发网络请求、不花钱
uv run python scripts/probe_image_generation.py

# 真实调用。会产生费用：最多 3 张图（T2I 1 张 + I2I 1 张 + I2I 3 张）
uv run python scripts/probe_image_generation.py --yes
```

**不要把 Key 打印出来。** 图片写到系统临时目录，不写仓库。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

# 导入 config 的唯一目的：触发 .env 的加载，让 DASHSCOPE_API_KEY 可见。
# 探针刻意不读 settings.llm.image_generation——它要在配置落地之前就能跑，
# 否则“先有鸡还是先有蛋”：配置该写哪个 base_url，正是这个脚本要回答的问题。
import agi_agent.config  # noqa: F401

MODEL = os.environ.get("PROBE_IMAGE_MODEL", "qwen-image-3.0-pro")

#: 旧域名（北京）。这是本机当前唯一的可用域名——没有 WorkspaceId。
COMPAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/images/generations"

#: DashScope 原生同步端点。文档示例同样只给了业务空间域名，但旧域名通常也提供
#: `/api/v1/services/...` 这一套，所以作为兼容端点不可用时的备选。
NATIVE_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
    "multimodal-generation/generation"
)

#: 尽量小的合法尺寸：面积 512*512 是文档规定的最小值，费用落在 1K 档。
SIZE = "512x512"

PROMPT = "一张简笔画风格的白猫坐在窗台上，背景是浅蓝色，画面干净，无文字。"


def _api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "DASHSCOPE_API_KEY 未设置。请在 AGI_Agent/.env 或系统环境变量中配置后重试。"
        )
    return key


def _compat_body(prompt: str, image: list[str] | str | None = None) -> dict[str, Any]:
    """OpenAI 兼容模式的请求体：所有参数平铺在顶层。"""
    body: dict[str, Any] = {
        "model": MODEL,
        "prompt": prompt,
        "size": SIZE,
        "n": 1,
        # 关掉提示词改写：探针要验证的是“我们给的 prompt 能不能直接出图”，
        # 而不是改写后的 prompt。工具里也会这么设，理由见 ADR 0018。
        "prompt_extend": False,
        "watermark": False,
    }
    if image is not None:
        body["image"] = image
    return body


def _native_body(prompt: str, image: list[str] | str | None = None) -> dict[str, Any]:
    """DashScope 原生同步模式的请求体：input.messages + parameters。"""
    content: list[dict[str, str]] = []
    if image is not None:
        # 原生协议的 content 是“图片块 + 文本块”的列表。多张图就放多个 image 块。
        images = [image] if isinstance(image, str) else image
        content.extend({"image": url} for url in images)
    content.append({"text": prompt})

    return {
        "model": MODEL,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {
            # 原生协议用星号分隔，兼容协议用字母 x。这是文档明确写出的差异。
            "size": SIZE.replace("x", "*"),
            "n": 1,
            "prompt_extend": False,
            "watermark": False,
        },
    }


def _extract_url(payload: dict[str, Any]) -> str | None:
    """兼容端点与原生端点的成功响应形状不同，这里统一取出第一个图片 URL。"""
    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            url = first.get("url") or first.get("b64_json")
            if isinstance(url, str):
                return url

    output = payload.get("output")
    if isinstance(output, dict):
        choices = output.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("image"), str):
                        return block["image"]
    return None


def _png_size(data: bytes) -> tuple[int, int] | None:
    """从 PNG 的 IHDR 读实际宽高；不是 PNG 就返回 None。"""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return width, height
    return None


def _short(payload: Any, limit: int = 600) -> str:
    text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
    return text if len(text) <= limit else text[:limit] + "…"


def _post(url: str, body: dict[str, Any], key: str) -> tuple[int, dict[str, Any] | str, float]:
    started = time.monotonic()
    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
            # 图片生成是同步长请求。60 秒对 1K 图足够；探针要看到真实耗时。
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
    except Exception as exc:  # noqa: BLE001  探针就是要如实报告任何失败
        return 0, f"{type(exc).__name__}: {exc}", time.monotonic() - started

    elapsed = time.monotonic() - started
    try:
        payload: dict[str, Any] | str = response.json()
    except ValueError:
        payload = response.text[:600]
    return response.status_code, payload, elapsed


def _download(url: str) -> tuple[bytes, str, int]:
    started = time.monotonic()
    response = httpx.get(
        url,
        follow_redirects=True,
        timeout=httpx.Timeout(120.0, connect=10.0),
    )
    response.raise_for_status()
    elapsed = int((time.monotonic() - started) * 1000)
    return response.content, response.headers.get("content-type", ""), elapsed


def _save(data: bytes, label: str) -> Path:
    target = Path(tempfile.gettempdir()) / f"agi-image-probe-{label}-{int(time.time())}.png"
    target.write_bytes(data)
    return target


def _data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _report(label: str, status: int, payload: dict[str, Any] | str, elapsed: float) -> str | None:
    print(f"\n[{label}] HTTP {status}，{elapsed:.1f}s")
    if status != 200:
        print(f"  响应：{_short(payload)}")
        return None
    url = _extract_url(payload) if isinstance(payload, dict) else None
    if not url:
        print(f"  200 但找不到图片 URL：{_short(payload)}")
        return None
    # 响应里可能很长；只打印顶层键与 usage，避免把整段 URL 混进日志。
    keys = list(payload) if isinstance(payload, dict) else "?"
    print(f"  顶层键：{keys}")
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if usage:
        print(f"  usage：{_short(usage, 300)}")
    print(f"  URL 前缀：{url[:80]}…")
    return url


def _probe_errors(transport: str, key: str) -> None:
    """确认错误响应的形状。非法参数不会计费，所以可以放心跑。"""
    print("\n[错误形状]")
    if transport == "compat":
        status, payload, _ = _post(
            COMPAT_URL, _compat_body(PROMPT) | {"size": "999x9999"}, key
        )
        print(f"  非法 size：HTTP {status}，{_short(payload, 400)}")
        status, payload, _ = _post(
            COMPAT_URL, _compat_body(PROMPT) | {"model": "qwen-image-nonexistent"}, key
        )
        print(f"  非法 model：HTTP {status}，{_short(payload, 400)}")
        return

    bad_size = _native_body(PROMPT)
    bad_size["parameters"]["size"] = "999*9999"
    status, payload, _ = _post(NATIVE_URL, bad_size, key)
    print(f"  非法 size：HTTP {status}，{_short(payload, 400)}")

    bad_model = _native_body(PROMPT)
    bad_model["model"] = "qwen-image-nonexistent"
    status, payload, _ = _post(NATIVE_URL, bad_model, key)
    print(f"  非法 model：HTTP {status}，{_short(payload, 400)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="探针：确认 Qwen-Image-3.0 端点与请求形状")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="真正发请求。不加时只打印请求体（不发网络、不花钱）。",
    )
    parser.add_argument(
        "--skip-refs",
        action="store_true",
        help="只做文生图，跳过 I2I（少花两张图的钱）。",
    )
    parser.add_argument(
        "--errors-only",
        action="store_true",
        help="只探错误形状（非法参数不计费），不生成图片。",
    )
    args = parser.parse_args()

    key = _api_key()
    print(f"模型：{MODEL}")
    print(f"兼容端点：{COMPAT_URL}")
    print(f"原生端点：{NATIVE_URL}")
    # 控制台可能是 GBK（Windows 默认），不要在这里打印人民币符号——它会直接抛
    # UnicodeEncodeError，把一个诊断脚本变成“跑不起来”。
    print(f"尺寸：{SIZE}；最多 3 张图，Pro 1K 约 0.25 元/张。")
    if not args.yes:
        print("\n[dry-run] 兼容端点请求体：")
        print(_short(_compat_body(PROMPT), 1200))
        print("\n[dry-run] 原生端点请求体：")
        print(_short(_native_body(PROMPT), 1200))
        print("\n加 --yes 才会真正发请求。")
        return 0

    if args.errors_only:
        _probe_errors("native", key)
        return 0

    # ---------------------------------------------------------------- T2I
    status, payload, elapsed = _post(COMPAT_URL, _compat_body(PROMPT), key)
    url = _report("兼容端点 T2I", status, payload, elapsed)
    transport = "compat"

    if url is None:
        print("\n兼容端点不可用，改试原生同步端点。")
        status, payload, elapsed = _post(NATIVE_URL, _native_body(PROMPT), key)
        url = _report("原生端点 T2I", status, payload, elapsed)
        transport = "native"

    if url is None:
        print("\n两种端点都没能生成图片。先解决端点/WorkspaceId 问题，不要继续实现。")
        return 1

    image, content_type, download_ms = _download(url)
    size = _png_size(image)
    saved = _save(image, "t2i")
    print(
        f"  下载：{len(image):,} 字节，{download_ms}ms，Content-Type={content_type}，"
        f"PNG 尺寸={size}，落盘={saved}"
    )

    if args.skip_refs:
        print("\n已跳过 I2I 探针。")
        return 0

    # ---------------------------------------------------------------- I2I 1 张
    ref = _data_url(image, "image/png")
    prompt = "把这张图里的背景改成温暖的夕阳色，保持主体不变。"
    if transport == "compat":
        body = _compat_body(prompt, image=ref)
        url_2 = _report("兼容端点 I2I（image 传字符串）", *_post(COMPAT_URL, body, key))
        if url_2 is None:
            # 单张也可能要求数组；这个结果直接决定工具里 _build_payload 的形状。
            body = _compat_body(prompt, image=[ref])
            url_2 = _report("兼容端点 I2I（image 传单元素数组）", *_post(COMPAT_URL, body, key))
    else:
        body = _native_body(prompt, image=ref)
        url_2 = _report("原生端点 I2I（1 张）", *_post(NATIVE_URL, body, key))

    if url_2:
        image_2, content_type_2, download_ms_2 = _download(url_2)
        print(
            f"  下载：{len(image_2):,} 字节，{download_ms_2}ms，Content-Type={content_type_2}，"
            f"PNG 尺寸={_png_size(image_2)}，落盘={_save(image_2, 'i2i-1')}"
        )
    else:
        print("  I2I 单张参考图失败。工具里不要把 I2I 当作已支持能力，先记录错误形状。")

    # ---------------------------------------------------------------- I2I 3 张
    # 用同一张图重复三次：探针关心的是“数组形状能否被接受”，不是多图语义。
    refs = [ref, ref, ref]
    prompt_3 = "参考这三张图，生成一张风格统一的简笔画白猫，背景为浅蓝色。"
    if transport == "compat":
        url_3 = _report(
            "兼容端点 I2I（3 张数组）",
            *_post(COMPAT_URL, _compat_body(prompt_3, image=refs), key),
        )
    else:
        url_3 = _report(
            "原生端点 I2I（3 张）",
            *_post(NATIVE_URL, _native_body(prompt_3, image=refs), key),
        )
    if url_3:
        image_3, content_type_3, download_ms_3 = _download(url_3)
        print(
            f"  下载：{len(image_3):,} 字节，{download_ms_3}ms，Content-Type={content_type_3}，"
            f"PNG 尺寸={_png_size(image_3)}，落盘={_save(image_3, 'i2i-3')}"
        )

    _probe_errors(transport, key)

    print("\n探针完成。把上面的端点、I2I 字段形状、错误码写进 ADR 0018。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
