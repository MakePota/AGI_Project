"""图像理解工具的测试。

**真实调用（连阿里云千问）不进自动测试**，与 steam 的取舍一致：它要密钥、要网络、
要花钱，而且结果本身是概率性的，不适合做断言。所以这里只测**确定性的那部分**，
并且刻意把它们抽成了纯函数（见 ``vision.py`` 里 ``_check_image_bytes`` 的说明）。

没被覆盖的是"模型答得好不好"——那只能人工看图比对。
"""

from __future__ import annotations

import pytest

from agi_agent.images import MAX_IMAGE_BYTES, sniff_mime
from agi_agent.policies.risk import assign_risk, requires_approval
from agi_agent.schemas import Plan, PlanStep, RiskLevel
from agi_agent.tools import get as get_tool
from agi_agent.tools.builtin.vision import (
    _as_text,
    _check_image_bytes,
    _classify_provider_error,
    _content_blocks,
    _load_image,
    _to_data_url,
)
from agi_agent.tools.errors import PreconditionError, ToolError

# 各格式的最小合法文件头。用真实文件头而不是编造的字节：
# 这些常量一旦写错，测试就变成"验证我的想象"，而不是验证真实格式。
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
_GIF = b"GIF89a" + b"\x00" * 32
_WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 32


class _FakeProviderError(Exception):
    """伪造供应商异常：只需要有 status_code，与真实的鸭子类型一致。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# --------------------------------------------------------------------------
# 文件头嗅探
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_PNG, "image/png"),
        (_JPEG, "image/jpeg"),
        (_GIF, "image/gif"),
        (_WEBP, "image/webp"),
    ],
)
def test_sniff_mime_recognizes_supported_formats(data: bytes, expected: str) -> None:
    assert sniff_mime(data) == expected


def test_sniff_mime_rejects_unknown_bytes() -> None:
    # 一段普通文本，以及"RIFF 但不是 WEBP"（AVI 也以 RIFF 开头）——后者是最容易误判的。
    assert sniff_mime("这是一个文本文件".encode()) is None
    assert sniff_mime(b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 32) is None


def test_sniff_mime_is_safe_on_short_input() -> None:
    # 短于 12 字节时不能越界抛 IndexError——切片本身安全，这里是把它钉住。
    assert sniff_mime(b"RI") is None
    assert sniff_mime(b"") is None


# --------------------------------------------------------------------------
# 字节校验（体积与格式）
# --------------------------------------------------------------------------


def test_check_image_bytes_returns_mime_for_valid_png() -> None:
    assert _check_image_bytes(_PNG, "a.png") == "image/png"


def test_check_image_bytes_rejects_empty_file() -> None:
    with pytest.raises(ToolError, match="空"):
        _check_image_bytes(b"", "a.png")


def test_check_image_bytes_reports_both_sizes_when_too_large() -> None:
    oversized = _PNG + b"\x00" * MAX_IMAGE_BYTES
    with pytest.raises(ToolError) as excinfo:
        _check_image_bytes(oversized, "big.png")
    message = str(excinfo.value)
    # 报错必须给出实际字节数与上限——否则用户不知道该缩到多小。
    assert f"{len(oversized):,}" in message
    assert f"{MAX_IMAGE_BYTES:,}" in message


def test_check_image_bytes_rejects_non_image_payload() -> None:
    with pytest.raises(ToolError, match="不是受支持的图片"):
        _check_image_bytes(b"#!/bin/sh\necho hi\n", "fake.png")


# --------------------------------------------------------------------------
# 消息构造与编码
# --------------------------------------------------------------------------


def test_to_data_url_has_expected_prefix() -> None:
    url = _to_data_url(b"\x89PNG", "image/png")
    assert url.startswith("data:image/png;base64,")
    # b"\x89PNG" 的 base64 是 iVBORw==
    assert url.endswith("iVBORw==")


def test_content_blocks_pins_the_openai_shape() -> None:
    """把"用哪种多模态写法"钉成回归。

    这是刻意的：换成 LangChain 的 ``source_type="base64"`` 数据块也能跑，
    但那会多一层随版本变化的转换。真要换，先让这条测试红，再决定。
    """
    blocks = _content_blocks("看这张图", "data:image/png;base64,AAAA")
    assert isinstance(blocks, list)
    assert blocks[0] == {"type": "text", "text": "看这张图"}
    assert blocks[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAAA"},
    }


def test_as_text_normalizes_both_shapes() -> None:
    assert _as_text("直接是字符串") == "直接是字符串"
    assert _as_text([{"type": "text", "text": "块"}]) == "块"
    assert _as_text([{"type": "text", "text": "a"}, {"type": "image_url"}]) == "a"


# --------------------------------------------------------------------------
# 错误分类：按"谁来修"分（ADR 0011）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        # 已实测：坏 Key 与错模型 ID 的真实形态
        (_FakeProviderError("Incorrect API key provided.", 401), PreconditionError),
        (_FakeProviderError("no permission", 403), PreconditionError),
        (_FakeProviderError("model does not exist", 404), PreconditionError),
        (_FakeProviderError("rate limited", 429), PreconditionError),
        (_FakeProviderError("multimodal endpoint required", 400), PreconditionError),
        (TimeoutError("timed out"), PreconditionError),
        (_FakeProviderError("Connection refused", None), PreconditionError),
    ],
)
def test_provider_errors_that_humans_must_fix(
    exc: Exception, expected: type[Exception]
) -> None:
    """环境/配置问题一律停下问人——重规划修不好它们。"""
    assert isinstance(_classify_provider_error(exc, "qwen3.8-flash"), expected)


def test_unclassified_provider_error_stays_replannable() -> None:
    """归不了类的错误保守地当作"确定失败、可重规划"，而不是停下来卡住。

    判据：它是 ToolError，但**不是** PreconditionError——只有后者会让图进入 halt。
    """
    result = _classify_provider_error(_FakeProviderError("something odd", 500), "m")
    assert type(result) is ToolError
    assert not isinstance(result, PreconditionError)


def test_error_message_is_truncated() -> None:
    """供应商可能回一大段 HTML，不能整段灌进上下文。"""
    result = _classify_provider_error(_FakeProviderError("x" * 5000, 500), "m")
    assert len(str(result)) < 500
    assert "…" in str(result)


# --------------------------------------------------------------------------
# 沙箱复用：确认真正用的是 files.py 那套边界，而不是自己写了一套
# --------------------------------------------------------------------------


def test_load_image_rejects_directory() -> None:
    with pytest.raises(ToolError, match="目录"):
        _load_image(".")


def test_load_image_rejects_missing_file() -> None:
    with pytest.raises(ToolError, match="不存在"):
        _load_image("这个文件一定不存在.png")


def test_load_image_outside_sandbox_raises_permission_error() -> None:
    """沙箱越界沿用 files.py 的 PermissionError，不在这里重新包装。

    与 ``local.read_file`` 行为一致：路径不合法属于"计划的问题"，
    executor 的兜底会把它记成 FAILED，planner 换个路径即可。
    """
    with pytest.raises(PermissionError):
        _load_image("../../outside.png")


# --------------------------------------------------------------------------
# 注册表与审批：档位与风险都必须由注册表赋值，不看模型怎么写
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["vision.describe_image", "vision.analyze_image"])
def test_vision_tools_are_registered_as_write(name: str) -> None:
    """必须是 WRITE：读图会把图片字节上传到阿里云，属于数据外发。

    改回 READ 是注册表里的一行改动——但那条改动必须是有意识的，
    所以这里用测试钉住当前的取值。
    """
    assert get_tool(name).risk is RiskLevel.WRITE


def test_plan_with_vision_step_requires_approval() -> None:
    plan = Plan(
        task_id="t",
        steps=[
            PlanStep(
                index=0,
                description="看看这张图",
                tool="vision.describe_image",
                arguments={"path": "a.png"},
            )
        ],
    )
    assert requires_approval(assign_risk(plan))


def test_plan_read_only_still_needs_no_approval() -> None:
    """对照组：别为了给 vision 加审批而把只读路径也拖下水。"""
    plan = Plan(
        task_id="t",
        steps=[PlanStep(index=0, description="列目录", tool="local.list_dir")],
    )
    assert not requires_approval(assign_risk(plan))
