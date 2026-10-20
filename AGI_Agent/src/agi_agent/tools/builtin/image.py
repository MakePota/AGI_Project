"""千问 Qwen-Image-3.0 图片生成工具。

## 为什么走 DashScope 原生同步端点，而不是 OpenAI 兼容端点

官方文档说旧域名 ``https://dashscope.aliyuncs.com`` 仍可用，但**实测（2026-10-20）
它的 ``/compatible-mode/v1/images/generations`` 直接返回 404**；图片接口的兼容示例
全部用的是业务空间专属域名（``{WorkspaceId}.cn-beijing.maas.aliyuncs.com``），
而本机没有 WorkspaceId。真正可用的是原生同步端点：

    POST {base_url}/api/v1/services/aigc/multimodal-generation/generation

请求体是 ``input.messages`` + ``parameters`` 的嵌套结构，响应里图片在
``output.choices[].message.content[].image``。这个结论是探针实测出来的，
不是照文档猜的，见 ADR 0018。

## 为什么不做成两个工具（文生图 / 图生图）

两者的差别只是**有没有参考图**，不是两个模型档位。拆成两个工具会让 planner
多一次无意义的选择，而且它可能选文生图、把用户给的参考图丢掉。模型档位则由
``[llm.image_generation]`` 静态绑定，**绝不让模型在计划参数里自报**（ADR 0012）。

## 为什么不用 ``get_model``

``providers/llm.get_model`` 返回的是 ``ChatOpenAI``，它只会说 ``/chat/completions``。
图片生成是另一个端点、另一套请求体。误调它不会报“用错了”，只会得到一个
看起来像网络问题的 404。所以这里直接读 ``settings.llm.image_generation`` 并走 HTTP。

## 失败语义：请求发出前 vs 发出后

这是本工具最重要的一条规则：

- **发出前**（参数、参考图、缺 Key、连接失败）→ ``ToolError`` / ``PreconditionError``。
  确定没有生成、没有计费，可以安全重规划或停下修环境。
- **发出后**（读超时、5xx、200 但无 URL、下载失败、落盘失败）→
  ``UncertainOutcomeError``，**绝不自动重新生成**。一次生成已经可能计费，
  重试会在账单上叠加一次；而且 DashScope 返回的临时 URL 只有 24 小时有效，
  正确的做法是停下来让人尽快取回，而不是再买一张。

429 是少数“发出后被明确拒绝”的情况：请求没有被接受、不计费，但重试也不该
由工具静默等待（Pro 限流 1 RPM，等 60 秒会把整个任务卡住）。所以归为
``PreconditionError``，让用户自己决定什么时候再发。
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from agi_agent.artifacts import MAX_ARTIFACT_BYTES, save_artifact
from agi_agent.config import ModelSpec, settings
from agi_agent.images import MAX_IMAGE_BYTES, sniff_mime
from agi_agent.schemas import RiskLevel
from agi_agent.tools.builtin.files import resolve_readable_path
from agi_agent.tools.errors import PreconditionError, ToolError, UncertainOutcomeError
from agi_agent.tools.registry import ToolSpec, register
from agi_agent.tools.result import ToolResult

#: 原生同步端点的路径。``base_url`` 只负责地域/域名，路径固定。
_GENERATION_PATH = "/api/v1/services/aigc/multimodal-generation/generation"

#: 生成请求的超时。实测 512x512 约 7-9 秒；2K 会明显更久。读超时给 180 秒，
#: 连接超时 10 秒——连接阶段失败可以确定“请求没发出去”，与读超时的分类不同。
_REQUEST_TIMEOUT = httpx.Timeout(180.0, connect=10.0)

#: 下载临时图片的超时。同城 OSS 实测几百毫秒；给 60 秒足够。
_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

#: 下载失败时的重试次数。**只重试下载（GET），绝不重试生成（POST）。**
_DOWNLOAD_ATTEMPTS = 3

#: 参考图最多 3 张（qwen-image-3.0 系列的限制）。单张沿用上传的 5 MB 上限；
#: 总字节数另设上限——base64 会让请求体放大 1.33 倍，三张满额图约 20 MB，
#: 供应商是否接受未经实测，先按 15 MB 原始字节保守限制。
_MAX_REFERENCE_IMAGES = 3
_MAX_REFERENCE_TOTAL_BYTES = 15_000_000

#: 输出尺寸的合法范围（文档：面积 512*512..2048*2048，宽高比 1:8..8:1）。
#: 实测的报错文案写的是 2560*2560，但文档与计费档位都按 2048 讲；取保守值，
#: 2K 以上需求真出现时再单独放开。
_MIN_AREA = 512 * 512
_MAX_AREA = 2048 * 2048
_MAX_ASPECT = 8.0

_ERROR_TEXT_LIMIT = 300

class GenerateImageArgs(BaseModel):
    """``image.generate`` 的参数签名。

    **没有 ``model`` / ``tier`` / ``n``。** 模型由配置决定；``n`` 固定为 1，
    一次调用一张图——这样审批、计费、产物数量、失败语义都只有一种形状。
    需要多张就让 planner 调多次，每次都有独立的审计记录。
    """

    prompt: str = Field(
        min_length=1,
        max_length=2000,
        description="图片内容描述；越具体越好，支持中英文",
    )
    reference_images: list[str] = Field(
        default_factory=list,
        max_length=_MAX_REFERENCE_IMAGES,
        description=(
            "本机参考图路径；0 张是文生图，1-3 张是图生图/编辑。"
            "相对路径以项目根为基准"
        ),
    )
    size: str = Field(
        default="1024x1024",
        description="输出尺寸，如 1024x1024；也接受 auto（由模型决定）",
    )
    negative_prompt: str | None = Field(
        default=None,
        max_length=1000,
        description="不希望出现在画面里的内容；可选",
    )
    seed: int | None = Field(
        default=None,
        ge=0,
        le=2_147_483_647,
        description="随机种子；固定它可以让结果相对稳定。可选",
    )


def _short(text: object, limit: int = _ERROR_TEXT_LIMIT) -> str:
    rendered = str(text).replace("\n", " ")
    return rendered if len(rendered) <= limit else rendered[:limit] + "…"


def _endpoint(spec: ModelSpec) -> str:
    base = (spec.base_url or "").strip().rstrip("/")
    if not base:
        raise PreconditionError(
            "图片生成缺少 base_url。请在 config.toml 的 [llm.image_generation] 里配置，"
            "例如 https://dashscope.aliyuncs.com"
        )
    return base + _GENERATION_PATH


def _api_key(spec: ModelSpec) -> str:
    key = os.environ.get(spec.api_key_env, "").strip()
    if not key:
        raise PreconditionError(
            f"环境变量 {spec.api_key_env} 未设置；请在 .env 或系统环境变量中配置。"
            "重试和重规划都修不好一个没配好的密钥。"
        )
    return key


def _normalize_size(size: str) -> str:
    """把尺寸统一成 ``WxH`` 或 ``auto``。

    **兼容 ``1024*1024`` 的写法**：planner 很容易照抄 DashScope 文档里的星号，
    而星号在这个工具的公开签名里不是合法分隔符。为此让整步失败没有意义——
    归一化它，并在发请求时再换成原生协议要求的星号。
    """
    text = size.strip().lower()
    if text == "auto":
        return "auto"

    normalized = text.replace("*", "x").replace(" ", "")
    parts = normalized.split("x")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ToolError(
            f"无法理解的尺寸：{size!r}。请用 1024x1024 这样的格式，或传 auto。"
        )

    width, height = int(parts[0]), int(parts[1])
    area = width * height
    if area < _MIN_AREA or area > _MAX_AREA:
        raise ToolError(
            f"尺寸 {width}x{height} 的像素面积是 {area:,}，超出允许范围 "
            f"{_MIN_AREA:,}..{_MAX_AREA:,}（512x512 到 2048x2048）。"
        )
    if min(width, height) <= 0 or max(width, height) / min(width, height) > _MAX_ASPECT:
        raise ToolError(
            f"尺寸 {width}x{height} 的宽高比超出限制（长边:短边不能超过 8:1）。"
        )
    return f"{width}x{height}"


def _read_reference(path: str) -> tuple[bytes, str]:
    """读取一张本机参考图：沙箱 → 体积 → 文件头。

    与 ``vision._load_image`` 的分工一致：**沙箱与凭据黑名单只由
    ``resolve_readable_path`` 判断**，这里不重新实现一遍。路径本身不合法时
    让它抛 ``PermissionError``，与本地文件工具保持同一语义。
    """
    target = resolve_readable_path(path)
    if target.is_dir():
        raise ToolError(f"参考图是一个目录，不是文件：{path}")
    if not target.is_file():
        raise ToolError(f"参考图不存在：{path}")

    try:
        data = target.read_bytes()
    except OSError as exc:
        raise ToolError(f"读取参考图失败：{path}：{exc}") from exc

    if not data:
        raise ToolError(f"参考图是空的：{path}")
    if len(data) > MAX_IMAGE_BYTES:
        raise ToolError(
            f"参考图太大：{path} 有 {len(data):,} 字节，上限是 {MAX_IMAGE_BYTES:,} 字节。"
        )

    mime = sniff_mime(data)
    if mime is None:
        raise ToolError(
            f"参考图不是受支持的图片：{path}。"
            "文件头既不是 PNG、JPEG、GIF 也不是 WEBP。"
        )
    return data, mime


def _reference_data_urls(paths: list[str]) -> list[str]:
    if len(paths) > _MAX_REFERENCE_IMAGES:
        raise ToolError(
            f"参考图最多 {_MAX_REFERENCE_IMAGES} 张，收到 {len(paths)} 张。"
        )

    urls: list[str] = []
    total = 0
    for path in paths:
        data, mime = _read_reference(path)
        total += len(data)
        if total > _MAX_REFERENCE_TOTAL_BYTES:
            raise ToolError(
                f"参考图总大小超过上限 {_MAX_REFERENCE_TOTAL_BYTES:,} 字节。"
                "请减少张数或先缩小图片。"
            )
        urls.append(f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}")
    return urls


def _build_payload(
    model: str,
    prompt: str,
    size: str,
    negative_prompt: str | None,
    seed: int | None,
    reference_urls: list[str],
) -> dict[str, Any]:
    """构造原生同步端点的请求体。

    **纯函数**，不读配置、不发网络——这样单测可以直接断言形状，不需要 mock 整个
    客户端。形状的依据是 2026-10-20 的实测（见 ADR 0018）：

    - 参考图是 ``content`` 里的 ``{"image": ...}`` 块，按顺序放在文本块之前；
    - ``size`` 用星号分隔；``auto`` 时**整个字段省略**，让供应商自己推荐；
    - ``prompt_extend`` / ``watermark`` 显式关闭，理由见 ADR。
    """
    content: list[dict[str, str]] = [{"image": url} for url in reference_urls]
    content.append({"text": prompt})

    parameters: dict[str, Any] = {
        "n": 1,
        # 关闭提示词改写：审批时用户看到的是原始 prompt，出图也应该按原始 prompt。
        # 改写会让人无法判断“这张图为什么和我说的话不一样”，而且它是额外一次模型调用。
        "prompt_extend": False,
        "watermark": False,
    }
    if size != "auto":
        # 原生协议用星号；这是文档明确写出的、与 OpenAI 兼容协议的唯一格式差异。
        parameters["size"] = size.replace("x", "*")
    if negative_prompt:
        parameters["negative_prompt"] = negative_prompt
    if seed is not None:
        parameters["seed"] = seed

    return {
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }


def _error_fields(body: Any) -> tuple[str, str]:
    """从两种错误形状里取出 (code, message)。

    原生端点是顶层 ``{"code", "message", "request_id"}``；
    OpenAI 兼容端点是 ``{"error": {"code", "message"}}``。这个工具只走原生，
    但分类函数保留两种形状——将来若切回兼容端点，不必重写分类。
    """
    if not isinstance(body, dict):
        return "", str(body)
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or ""), str(error.get("message") or "")
    return str(body.get("code") or ""), str(body.get("message") or "")


#: 400 里出现这些词，说明是**提示词/参考图内容**触发了审核。这类失败会消耗一次
#: 请求但通常不计费；改一改措辞可以重试，所以归 ToolError 而不是 PreconditionError。
_CONTENT_MARKERS = (
    "data_inspection",
    "inappropriate",
    "sensitive",
    "content policy",
    "审核",
    "违规",
    "敏感",
)

#: 400 里出现这些词，说明模型或端点配置错了——重规划修不好。
_CONFIG_MARKERS = ("model not exist", "model not found", "invalid model", "unsupported model")


def _classify_http_error(status: int, body: Any, model: str) -> ToolError:
    """把 HTTP 错误翻译成“谁来修”（ADR 0010 / 0011）。

    实测（2026-10-20）：
    - 400 ``InvalidParameter`` + ``Image area must be...`` -> 计划参数问题
    - 400 ``InvalidParameter`` + ``Model not exist.``   -> 配置问题
    - 兼容端点的 ``/images/generations`` 在旧域名上返回 404 -> 配置/端点问题
    """
    code, message = _error_fields(body)
    detail = _short(f"{code}: {message}" if code else message or body)
    lowered = detail.lower()

    if status in (401, 403):
        return PreconditionError(
            f"调用 {model} 被拒绝（HTTP {status}，鉴权失败）：{detail}\n"
            "请检查 DASHSCOPE_API_KEY 是否有效——重试和重规划都修不好这个问题。"
        )
    if status == 404:
        return PreconditionError(
            f"图片生成端点不存在（HTTP 404）：{detail}\n"
            "请核对 config.toml 的 [llm.image_generation].base_url。"
            "实测旧域名的 OpenAI 兼容 /images/generations 不可用，应使用原生同步端点。"
        )
    if status == 429:
        return PreconditionError(
            f"图片生成被限流或配额用尽（HTTP 429）：{detail}\n"
            "qwen-image-3.0-pro 的官方限流是 1 RPM。请等待约 60 秒后再重新发起任务——"
            "工具不会在这里静默等待，因为那会把整个任务卡住。"
        )
    if status == 400:
        if any(marker in lowered for marker in _CONFIG_MARKERS):
            return PreconditionError(
                f"模型 {model} 不存在或当前账号无权访问（HTTP 400）：{detail}\n"
                "请核对 config.toml 里的模型 ID——这是配置问题，不是计划问题。"
            )
        if any(marker in lowered for marker in _CONTENT_MARKERS):
            return ToolError(
                f"提示词或参考图触发了内容审核（HTTP 400）：{detail}\n"
                "请改写提示词、换一张参考图，或调整编辑指令后再试。"
            )
        return ToolError(
            f"生成参数不被接受（HTTP 400）：{detail}\n"
            "请核对 size 的像素面积/宽高比、参考图数量（最多 3 张）与提示词长度。"
        )
    if status >= 500:
        # 5xx 无法证明“没有生成”。保守地按结果不确定处理：宁可停下来问人，
        # 也不要自动重试一次可能已经计费的生成。
        return UncertainOutcomeError(
            f"调用 {model} 时供应商返回 HTTP {status}：{detail}\n"
            "无法确认图片是否已经生成。请先人工核实，不要直接重试。"
        )
    return ToolError(f"调用 {model} 失败（HTTP {status}）：{detail}")


def _post_generation(
    url: str, payload: dict[str, Any], api_key: str, model: str
) -> dict[str, Any]:
    """发一次生成请求。**所有“请求是否已经发出”的判断都在这里。**"""
    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=_REQUEST_TIMEOUT,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as exc:
        # 连接阶段失败：请求没有到达供应商，确定没有生成。
        raise PreconditionError(
            f"无法连接图片生成服务（{url}）：{type(exc).__name__}: {exc}\n"
            "请检查网络与代理设置。"
        ) from exc
    except httpx.TimeoutException as exc:
        # 连上之后的超时：请求可能已经被处理，甚至已经计费。
        raise UncertainOutcomeError(
            f"图片生成请求超时（{url}）：{type(exc).__name__}: {exc}\n"
            "请求可能已经生成并计费，**不要自动重试**。请先人工核实。"
        ) from exc
    except httpx.TransportError as exc:
        # 其余传输层错误无法区分“发出前/后”，按不确定处理。
        raise UncertainOutcomeError(
            f"图片生成请求的传输中断（{url}）：{type(exc).__name__}: {exc}\n"
            "请求可能已经到达供应商，**不要自动重试**。请先人工核实。"
        ) from exc

    try:
        body = response.json()
    except ValueError:
        body = {"message": response.text[:300]}

    if response.status_code != 200:
        raise _classify_http_error(response.status_code, body, model)

    if not isinstance(body, dict):
        raise UncertainOutcomeError(
            "图片生成返回了 200，但响应不是对象，无法判断是否成功。请人工核实。"
        )
    return body


def _extract_image_url(body: dict[str, Any]) -> str | None:
    """从原生/兼容两种成功响应里取第一张图片的 URL。"""
    data = body.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                return item["url"]

    output = body.get("output")
    if isinstance(output, dict):
        choices = output.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message") or {}
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and isinstance(block.get("image"), str):
                            return block["image"]
    return None


def _reported_size(body: dict[str, Any]) -> tuple[int, int] | None:
    usage = body.get("usage")
    if isinstance(usage, dict):
        width, height = usage.get("output_width"), usage.get("output_height")
        if isinstance(width, int) and isinstance(height, int):
            return width, height
    return None


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None


def _allowed_download_host(host: str) -> bool:
    """只允许从阿里云域名下载。响应内容不能把我们引到任意地址去。"""
    host = host.lower()
    return host == "aliyuncs.com" or host.endswith(".aliyuncs.com")


def _download_image(url: str) -> bytes:
    """下载生成结果。

    **这是整个工具里最不能“重试生成”的地方。** 到达这里时，生成请求已经成功、
    很可能已经计费；下载失败只能重试 GET，最终失败就停下来让人在 24 小时内取回。
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or not _allowed_download_host(parsed.hostname or ""):
        raise UncertainOutcomeError(
            f"生成结果给了一个不在允许范围内的下载地址（{parsed.scheme}://{parsed.hostname}）。"
            "图片可能已经生成，请先人工核实。"
        )

    last_error = "未知错误"
    for attempt in range(_DOWNLOAD_ATTEMPTS):
        try:
            with httpx.stream(
                "GET",
                url,
                follow_redirects=True,
                timeout=_DOWNLOAD_TIMEOUT,
            ) as response:
                if response.status_code == 200:
                    # 重定向后的最终域名也要检查：否则一次 302 就能把下载引到别处。
                    # 注意这里是 httpx.URL 的 **host**，不是 urllib 的 hostname——
                    # 用错会抛 AttributeError，而它发生在生成成功之后，非常危险。
                    final_host = response.url.host or ""
                    if not _allowed_download_host(final_host):
                        raise UncertainOutcomeError(
                            f"下载被重定向到了不允许的域名（{final_host}）。"
                            "图片可能已经生成，请先人工核实。"
                        )

                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_ARTIFACT_BYTES:
                            raise UncertainOutcomeError(
                                f"生成的图片超过 {MAX_ARTIFACT_BYTES:,} 字节上限。"
                                "图片已经生成并可能计费，不要重新生成；请人工取回。"
                            )
                    raw = bytes(data)
                    if not raw:
                        raise UncertainOutcomeError(
                            "生成的图片是空的。图片可能已经生成并计费，请先人工核实。"
                        )
                    if sniff_mime(raw) is None:
                        raise UncertainOutcomeError(
                            "生成结果不是受支持的图片格式。"
                            "图片可能已经生成并计费，请先人工核实。"
                        )
                    return raw

                if 400 <= response.status_code < 500:
                    # 4xx 重试也不会变；直接结束循环。
                    last_error = f"HTTP {response.status_code}"
                    break
                last_error = f"HTTP {response.status_code}"
        except UncertainOutcomeError:
            # 已经确定是“生成了但不可用”，重试没有意义。
            raise
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt + 1 < _DOWNLOAD_ATTEMPTS:
            # 1s、2s 退避。下载是幂等 GET，重试安全。
            time.sleep(2**attempt)

    raise UncertainOutcomeError(
        f"图片已经生成，但下载失败（{last_error}）。\n"
        f"临时 URL（24 小时内有效，请尽快人工取回）：{url}\n"
        "**不要重新生成**——那会再计费一次，而且不一定得到同一张图。"
    )


def generate_image(
    prompt: str,
    reference_images: list[str] | None = None,
    size: str = "1024x1024",
    negative_prompt: str | None = None,
    seed: int | None = None,
) -> ToolResult:
    """生成一张图片；有参考图时走图生图/编辑。"""
    spec = settings.llm.image_generation
    normalized_size = _normalize_size(size)
    reference_urls = _reference_data_urls(reference_images or [])

    # 以上全部是本地校验：任何失败都发生在请求发出之前，没有副作用。
    payload = _build_payload(
        model=spec.model,
        prompt=prompt,
        size=normalized_size,
        negative_prompt=negative_prompt,
        seed=seed,
        reference_urls=reference_urls,
    )
    url = _endpoint(spec)
    api_key = _api_key(spec)

    try:
        body = _post_generation(url, payload, api_key, spec.model)
    except ToolError:
        # 已经分好类（请求前 vs 请求后），原样上抛，不要在这里重新包装。
        raise

    # **从这一行开始，生成请求已经成功返回。** 后面的任何异常都不能退化成
    # “普通失败”——executor 会把普通失败交给 planner 重规划，而重规划可能
    # 再次调用 image.generate，在账单上叠加一次。2026-10-20 的端到端实测就踩到过：
    # 下载阶段一个 AttributeError 被当成普通失败，planner 立即准备再生成一次。
    image_url: str | None = None
    try:
        image_url = _extract_image_url(body)
        if image_url is None:
            raise UncertainOutcomeError(
                "图片生成返回了成功状态，但响应里没有图片 URL。\n"
                "请求可能已经生成并计费，**不要自动重试**。请先人工核实。"
            )

        data = _download_image(image_url)
        artifact = save_artifact(data)
    except UncertainOutcomeError:
        raise
    except (OSError, ValueError) as exc:
        raise UncertainOutcomeError(
            f"图片已经生成，但保存到本机失败：{exc}\n"
            f"临时 URL（24 小时内有效）：{image_url}\n"
            "**不要重新生成**——那会再计费一次。"
        ) from exc
    except Exception as exc:
        # 请求已发出，任何未知错误都不能当普通失败——那会触发重规划与重复计费。
        raise UncertainOutcomeError(
            f"图片生成请求已经成功，但后续处理出现未预期错误："
            f"{type(exc).__name__}: {exc}\n"
            f"临时 URL（如果已拿到）：{image_url}\n"
            "图片可能已经生成并计费，**不要自动重试**。请先人工核实。"
        ) from exc

    width, height = _reported_size(body) or _png_size(data) or (None, None)
    size_text = f"{width}x{height}" if width and height else normalized_size
    return ToolResult(
        text=(
            f"图片已生成：{artifact.name}（{size_text}，{artifact.mime}，"
            f"{artifact.size:,} 字节）"
        ),
        artifacts=(artifact,),
    )


register(
    ToolSpec(
        name="image.generate",
        description=(
            "根据文字描述生成一张图片；给出 1-3 张本机参考图时改为图生图/编辑。"
            "会调用阿里云千问、产生费用（约 0.25 元/张），参考图会上传到阿里云，"
            "因此需要人工确认"
        ),
        risk=RiskLevel.WRITE,
        parameters=GenerateImageArgs,
        func=generate_image,
    )
)
