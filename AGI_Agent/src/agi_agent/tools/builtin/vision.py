"""千问（阿里云百炼 / DashScope）图像理解工具。

**两个工具 = 两个模型档位**，档位藏在注册表与配置里：

===========================  =========================  =====================
工具                          绑定的槽位                    实际模型（config.toml）
===========================  =========================  =====================
``vision.describe_image``    ``Role.VISION_QUICK``       ``qwen3.8-flash``
``vision.analyze_image``     ``Role.VISION_DEEP``        ``qwen3.8-max``
===========================  =========================  =====================

**为什么不做成一个 ``analyze_image(path, question, tier)``**：那样 ``tier`` 会由
planner（一个模型）在计划里填出来，等于"让模型自报该用哪个模型"。这与本项目
ADR 0007「风险等级由注册表赋值、不采信模型自报」是同一个形状的问题——能力的关键
属性不该由模型输出决定。写成两个工具后，档位是"工具名 -> 槽位 -> 模型"两级静态绑定，
审计时不必读计划参数就能知道这次会用哪个模型。完整论证见 ADR 0012。

**为什么风险等级是 ``WRITE`` 而不是 ``READ``**：读图会把**本地图片字节上传到阿里云**。
它不改变任何本地状态，但属于数据外发，必须由人过目一次。定为 ``WRITE`` 后自动汇入
``authorize`` 节点的整批审批中断，不需要在策略层加任何特判。若将来觉得每张图都要点
一次确认太烦，把它改回 ``READ`` 是**注册表里的一行改动**——这个决定刻意放在注册表，
而不是散在代码里的判断。

**错误分类（ADR 0010 / 0011：按"谁来修"分）**：

- 本地文件问题（不存在、是目录、太大、格式不认识、沙箱越界）
  -> ``ToolError`` / ``PermissionError``，确定没做成，planner 可以重规划。
- 环境或配置问题（Key 缺失或被拒、网络不通、配额用尽、模型 ID 写错）
  -> ``PreconditionError``，**停下问人**——重规划修不好一个没配好的密钥。
- **读图永远不产生 ``UncertainOutcomeError``**：它没有副作用，不存在"可能已经改动了
  什么"的情况。这是与写操作最根本的区别，不要照抄 steam.py 那套。
"""

from __future__ import annotations

import base64

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from agi_agent.config import settings
from agi_agent.providers.llm import get_model
from agi_agent.schemas import RiskLevel, Role
from agi_agent.tools.builtin.files import resolve_readable_path
from agi_agent.tools.errors import PreconditionError, ToolError
from agi_agent.tools.registry import ToolSpec, register

#: 图片体积上限。**这是本项目自己的自保上限，不是查证过的厂商限制**——
#: 目的是给 base64 编码后的请求体（约放大 1.33 倍）定一个内存与上传时间的界，
#: 避免一张几十 MB 的图把一次工具调用变成漫长的等待。实测一张 600x240 的 PNG
#: 只占 154 个图像 token，正常截图远达不到这个上限。
_MAX_IMAGE_BYTES = 5_000_000

#: 报错信息里截断供应商响应，避免把整段 HTML/JSON 灌进上下文。
_ERROR_TEXT_LIMIT = 300

_DESCRIBE_INSTRUCTION = (
    "请用中文描述这张图片的内容，客观、具体。"
    "如果图中有文字或数字，请原样抄录出来，不要改写或推测。"
)


def _short(text: object, limit: int = _ERROR_TEXT_LIMIT) -> str:
    """把可能很长的错误信息压到一行以内可读的长度。"""
    rendered = str(text).replace("\n", " ")
    return rendered if len(rendered) <= limit else rendered[:limit] + "…"


def _sniff_mime(data: bytes) -> str | None:
    """按文件头判断图片类型；认不出来返回 None。

    看文件头而不是扩展名：扩展名是随手可以改的，文件头才是事实。
    与 ``files.read_file`` 用 ``\\x00`` 判定二进制是同一个思路——看内容，不看名字。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _check_image_bytes(data: bytes, path: str) -> str:
    """校验图片字节并返回 mime。**只吃 bytes、不碰文件系统。**

    刻意与路径解析分开，是为了能在单测里直接喂构造的字节——
    否则测"太大"和"格式不对"就得造真实图片文件、还要绕过目录沙箱。
    """
    size = len(data)
    if size == 0:
        raise ToolError(f"文件是空的，没有可理解的内容：{path}")
    if size > _MAX_IMAGE_BYTES:
        raise ToolError(
            f"图片太大：{path} 有 {size:,} 字节，上限是 {_MAX_IMAGE_BYTES:,} 字节。"
            "请先缩小或裁剪这张图再试。"
        )
    mime = _sniff_mime(data)
    if mime is None:
        raise ToolError(
            f"这不是受支持的图片：{path}。"
            "文件头既不是 PNG、JPEG、GIF 也不是 WEBP——"
            "它可能根本不是图片，也可能只是被改了后缀名。"
        )
    return mime


def _to_data_url(data: bytes, mime: str) -> str:
    """把字节编码成 data URL（供应商接受的 base64 内联形式）。"""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _content_blocks(instruction: str, data_url: str) -> list[dict]:
    """构造一条多模态消息的 content 块。

    用**显式 OpenAI 形式**（``image_url`` + data URL），而不是 LangChain 更"跨供应商"
    的 ``{"type": "image", "source_type": "base64", ...}`` 数据块：本项目只接 OpenAI
    兼容端点，少一层转换就少一处可能随版本变化的地方。这条路已核对本机
    ``langchain_openai`` 的 ``_format_message_content``——dict 块原样透传。
    """
    return [
        {"type": "text", "text": instruction},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]


def _as_text(content: object) -> str:
    """把模型回复取成纯文本。

    大多数情况下 ``content`` 就是字符串，但多模态供应商有时回一个内容块列表，
    所以这里做一次归一化，而不是假定它是 str。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)
    return str(content)


#: 400 响应里出现这些词，说明是"这个模型/端点不吃图像"，属于配置问题而非计划问题。
#: **注意：这一支尚未实测**——我们实测到的是 401 与 404，400 的分类是保守推断。
#: 所以关键词刻意收窄：不用裸的 "image"（图片过大等计划问题也含这个词）。
_MISCONFIG_MARKERS = ("multimodal", "unsupported", "not support", "does not support")
_NETWORK_MARKERS = ("timeout", "timed out", "connection", "dns", "proxy", "unreachable")


def _classify_provider_error(exc: Exception, model: str) -> ToolError:
    """把供应商异常翻译成"谁来修"（ADR 0011）。

    只按 ``status_code``、异常类名与消息关键词判断，**不 import openai**——
    那会把一个未在 ``pyproject.toml`` 里声明的直接依赖引进来。这一层用鸭子类型就够，
    与 ``steam.py``「网络层什么都可能抛、一律翻译」是同一做法。

    实测过（2026-10-13）：
    - 401 ``invalid_api_key``  -> 鉴权失败
    - 404 ``model_not_found``  -> 模型 ID 写错或无权访问
    """
    status = getattr(exc, "status_code", None)
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    detail = _short(text)

    if status in (401, 403):
        return PreconditionError(
            f"调用 {model} 被拒绝（HTTP {status}，鉴权失败）：{detail}\n"
            "请检查 DASHSCOPE_API_KEY 是否有效——重试和重规划都修不好这个问题。"
        )
    if status == 404:
        return PreconditionError(
            f"模型 {model} 不存在或当前账号无权访问（HTTP 404）：{detail}\n"
            "请核对 config.toml 里的模型 ID——这是配置问题，不是计划问题。"
        )
    if status == 429:
        return PreconditionError(
            f"调用 {model} 被限流或配额用尽（HTTP 429）：{detail}\n"
            "请检查账号配额，稍后再试——重规划解决不了配额。"
        )
    if any(marker in lowered for marker in _NETWORK_MARKERS):
        return PreconditionError(
            f"无法连接 {model} 的服务（网络问题）：{detail}\n"
            "请检查网络与代理设置。"
        )
    if status == 400 and any(marker in lowered for marker in _MISCONFIG_MARKERS):
        return PreconditionError(
            f"{model} 似乎不接受图像输入（HTTP 400）：{detail}\n"
            "请核对 config.toml 里的模型与端点——换一个能看图的模型。"
        )
    # 其余归为"确定失败、可重规划"：宁可让 planner 换个思路，也不要停在无法归因的错上。
    return ToolError(f"调用 {model} 失败（HTTP {status}）：{detail}")


def _ask(role: Role, instruction: str, data_url: str) -> str:
    """取该档位的模型、发一次多模态请求、把回复取成文本。"""
    model_name = getattr(settings.llm, role.value).model
    try:
        model = get_model(role)
    except RuntimeError as exc:
        # get_model 在 API Key 缺失时抛 RuntimeError。必须翻译成 PreconditionError：
        # 否则它会退化成"确定失败"，planner 会一遍遍重规划，而重规划修不好缺密钥——
        # 正是 ADR 0011 要消除的那种空转。
        raise PreconditionError(f"无法初始化图像模型（{model_name}）：{exc}") from exc

    message = HumanMessage(content=_content_blocks(instruction, data_url))
    try:
        reply = model.invoke([message])
    except Exception as exc:
        # 供应商的异常类型很杂（超时、连接、HTTP 错误都可能是不同类），
        # 统一走分类函数，不在调用点做判断。
        raise _classify_provider_error(exc, model_name) from exc

    text = _as_text(reply.content).strip()
    if not text:
        # 空回复不等于失败，但没有可用的答案，对 planner 而言就是这一步没产出。
        raise ToolError(
            f"{model_name} 返回了空内容，没有可用的图像理解结果。"
            "可以重试，或换用另一个档位的工具。"
        )
    return text


def _load_image(path: str) -> str:
    """沙箱校验 -> 读字节 -> 格式与体积校验，返回可直接发出去的 data URL。

    沙箱越界与凭据黑名单由 ``resolve_readable_path`` 抛 ``PermissionError``，
    **这里不重新包装**：与 ``local.read_file`` 保持一致——路径本身不合法属于
    "计划的问题"，落到 executor 的兜底就是 FAILED，planner 换个路径即可。
    """
    target = resolve_readable_path(path)
    if target.is_dir():
        raise ToolError(f"这是一个目录，不是图片文件：{path}")
    if not target.is_file():
        raise ToolError(f"文件不存在：{path}")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise ToolError(f"读取图片失败：{path}：{exc}") from exc
    mime = _check_image_bytes(data, path)
    return _to_data_url(data, mime)


def describe_image(path: str) -> str:
    """看图说话（快档，config 里关掉了思考模式）。"""
    data_url = _load_image(path)
    return f"图像理解结果（{path}）：\n{_ask(Role.VISION_QUICK, _DESCRIBE_INSTRUCTION, data_url)}"


def analyze_image(path: str, question: str) -> str:
    """带着具体问题看图（准档，config 里开着思考模式）。"""
    data_url = _load_image(path)
    instruction = f"请仔细查看这张图片，然后回答问题。\n问题：{question}"
    return f"图像理解结果（{path}）：\n{_ask(Role.VISION_DEEP, instruction, data_url)}"


class DescribeImageArgs(BaseModel):
    path: str = Field(description="图片文件路径；相对路径以项目根为基准")


class AnalyzeImageArgs(BaseModel):
    path: str = Field(description="图片文件路径；相对路径以项目根为基准")
    question: str = Field(description="关于这张图片的具体问题")


register(
    ToolSpec(
        name="vision.describe_image",
        description=(
            "看懂一张本地图片并用中文描述内容，适合「图里是什么」这类简单问题。"
            "快且便宜；会把图片上传到阿里云，需人工确认"
        ),
        risk=RiskLevel.WRITE,
        parameters=DescribeImageArgs,
        func=describe_image,
    )
)

register(
    ToolSpec(
        name="vision.analyze_image",
        description=(
            "带着一个具体问题查看本地图片，适合需要推理的场合（图表、比对、细节判断）。"
            "更准但更贵；会把图片上传到阿里云，需人工确认"
        ),
        risk=RiskLevel.WRITE,
        parameters=AnalyzeImageArgs,
        func=analyze_image,
    )
)
