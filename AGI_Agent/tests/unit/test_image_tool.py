"""图片生成工具的测试。

**不碰真实网络、不花钱。** 真实调用要密钥、要等 7-9 秒、结果是概率性的，
不适合做断言（与 vision/steam 的取舍一致）。这里钉住的是：

- 请求体的形状（探针实测的原生协议）；
- 尺寸归一化与本地校验；
- 错误分类——尤其“请求发出前”和“发出后”必须分开；
- 下载只重试 GET、绝不重试 POST；
- 注册表里的风险等级与参数签名。
"""

from __future__ import annotations

import base64
import re
from types import SimpleNamespace

import httpx
import pytest

from agi_agent.artifacts import artifact_dir
from agi_agent.images import MAX_IMAGE_BYTES
from agi_agent.schemas import RiskLevel, ToolCall, ToolCallStatus
from agi_agent.tools import get
from agi_agent.tools.builtin import image as image_tool
from agi_agent.tools.errors import PreconditionError, ToolError, UncertainOutcomeError
from agi_agent.tools.result import ToolResult

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n0kAAAAASUVORK5CYII="
)
ARTIFACT_NAME = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}\.png$")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGI_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    return tmp_path


# --------------------------------------------------------------------------
# 纯函数：尺寸与请求体
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1024x1024", "1024x1024"),
        ("1024*1024", "1024x1024"),
        (" 1024 X 1024 ", "1024x1024"),
        ("auto", "auto"),
        ("512x2048", "512x2048"),
    ],
)
def test_normalize_size_accepts(raw: str, expected: str) -> None:
    assert image_tool._normalize_size(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["1024", "1024x", "abc", "256x512", "4096x4096", "100x10000", "0x0"],
)
def test_normalize_size_rejects(raw: str) -> None:
    with pytest.raises(ToolError):
        image_tool._normalize_size(raw)


def test_build_payload_t2i_shape() -> None:
    payload = image_tool._build_payload(
        model="qwen-image-3.0-pro",
        prompt="一只猫",
        size="1024x1024",
        negative_prompt=None,
        seed=None,
        reference_urls=[],
    )

    assert payload["model"] == "qwen-image-3.0-pro"
    content = payload["input"]["messages"][0]["content"]
    assert content == [{"text": "一只猫"}]

    parameters = payload["parameters"]
    assert parameters["n"] == 1
    assert parameters["prompt_extend"] is False
    assert parameters["watermark"] is False
    # 原生协议用星号，不是字母 x。
    assert parameters["size"] == "1024*1024"
    assert "negative_prompt" not in parameters
    assert "seed" not in parameters


def test_build_payload_i2i_puts_images_before_text() -> None:
    payload = image_tool._build_payload(
        model="m",
        prompt="改成蓝色",
        size="512x512",
        negative_prompt="文字",
        seed=42,
        reference_urls=["data:image/png;base64,AAA", "data:image/png;base64,BBB"],
    )

    content = payload["input"]["messages"][0]["content"]
    assert [block.get("image") for block in content[:2]] == [
        "data:image/png;base64,AAA",
        "data:image/png;base64,BBB",
    ]
    assert content[-1] == {"text": "改成蓝色"}
    assert payload["parameters"]["negative_prompt"] == "文字"
    assert payload["parameters"]["seed"] == 42


def test_build_payload_auto_omits_size() -> None:
    payload = image_tool._build_payload("m", "猫", "auto", None, None, [])
    assert "size" not in payload["parameters"]


# --------------------------------------------------------------------------
# 参考图：沙箱、体积、格式
# --------------------------------------------------------------------------


@pytest.fixture
def allow_tmp(tmp_path, monkeypatch):
    """把文件沙箱指到临时目录——参考图测试不该依赖项目根。"""
    monkeypatch.setattr("agi_agent.tools.builtin.files.ALLOWED_ROOTS", [tmp_path])
    return tmp_path


def test_read_reference_accepts_png(allow_tmp) -> None:
    path = allow_tmp / "ref.png"
    path.write_bytes(PNG)

    data, mime = image_tool._read_reference(str(path))
    assert data == PNG
    assert mime == "image/png"


def test_reference_data_url_is_base64(allow_tmp) -> None:
    path = allow_tmp / "ref.png"
    path.write_bytes(PNG)

    urls = image_tool._reference_data_urls([str(path)])
    assert urls[0].startswith("data:image/png;base64,")


def test_read_reference_rejects_missing_and_directory(allow_tmp) -> None:
    with pytest.raises(ToolError):
        image_tool._read_reference(str(allow_tmp / "missing.png"))
    with pytest.raises(ToolError):
        image_tool._read_reference(str(allow_tmp))


def test_read_reference_rejects_non_image(allow_tmp) -> None:
    path = allow_tmp / "not.png"
    path.write_bytes(b"hello")
    with pytest.raises(ToolError):
        image_tool._read_reference(str(path))


def test_read_reference_rejects_oversized(allow_tmp) -> None:
    path = allow_tmp / "big.png"
    path.write_bytes(PNG + b"\0" * MAX_IMAGE_BYTES)
    with pytest.raises(ToolError):
        image_tool._read_reference(str(path))


def test_reference_count_is_capped() -> None:
    with pytest.raises(ToolError):
        image_tool._reference_data_urls(["a.png", "b.png", "c.png", "d.png"])


# --------------------------------------------------------------------------
# 错误分类
# --------------------------------------------------------------------------


def test_connect_error_is_precondition(monkeypatch) -> None:
    def explode(*args, **kwargs):  # noqa: ARG001
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(image_tool.httpx, "post", explode)
    with pytest.raises(PreconditionError):
        image_tool._post_generation("https://x", {}, "k", "m")


def test_read_timeout_is_uncertain(monkeypatch) -> None:
    def explode(*args, **kwargs):  # noqa: ARG001
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(image_tool.httpx, "post", explode)
    with pytest.raises(UncertainOutcomeError):
        image_tool._post_generation("https://x", {}, "k", "m")


def test_http_400_parameter_error_is_tool_error() -> None:
    body = {
        "code": "InvalidParameter",
        "message": "Image area must be between 262144 (512x512) and 6553600 (2560x2560) pixels",
    }
    error = image_tool._classify_http_error(400, body, "qwen-image-3.0-pro")
    assert type(error) is ToolError


def test_http_400_bad_model_is_precondition() -> None:
    body = {"code": "InvalidParameter", "message": "Model not exist."}
    error = image_tool._classify_http_error(400, body, "qwen-image-3.0-pro")
    assert isinstance(error, PreconditionError)


def test_http_400_content_moderation_is_tool_error() -> None:
    body = {"code": "DataInspectionFailed", "message": "内容审核未通过"}
    error = image_tool._classify_http_error(400, body, "qwen-image-3.0-pro")
    assert type(error) is ToolError


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_auth_endpoint_and_quota_are_precondition(status: int) -> None:
    error = image_tool._classify_http_error(status, {}, "m")
    assert isinstance(error, PreconditionError)


def test_5xx_is_uncertain() -> None:
    error = image_tool._classify_http_error(500, {}, "m")
    assert isinstance(error, UncertainOutcomeError)


# --------------------------------------------------------------------------
# 下载：只重试 GET，且失败后绝不重新生成
# --------------------------------------------------------------------------


class _FakeStreamResponse:
    def __init__(self, status_code: int, content: bytes = b"", host: str = "") -> None:
        self.status_code = status_code
        self._content = content
        # httpx.URL 的属性叫 **host**，不是 urllib 的 hostname。
        # 测试替身必须模仿真实类型，否则测试全绿、线上 AttributeError——
        # 2026-10-20 端到端实测就是这么发现的。
        self.url = SimpleNamespace(host=host or "dashscope-a717.oss-accelerate.aliyuncs.com")

    def iter_bytes(self):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_stream(monkeypatch, responses: list[_FakeStreamResponse]) -> None:
    queue = list(responses)

    def fake_stream(*args, **kwargs):  # noqa: ARG001
        return queue.pop(0)

    monkeypatch.setattr(image_tool.httpx, "stream", fake_stream)
    monkeypatch.setattr(image_tool.time, "sleep", lambda _: None)


def test_download_image_succeeds(monkeypatch) -> None:
    _patch_stream(monkeypatch, [_FakeStreamResponse(200, PNG)])
    assert image_tool._download_image(
        "https://dashscope-a717.oss-accelerate.aliyuncs.com/x.png"
    ) == PNG


def test_download_image_retries_get(monkeypatch) -> None:
    _patch_stream(
        monkeypatch,
        [_FakeStreamResponse(500), _FakeStreamResponse(200, PNG)],
    )
    assert image_tool._download_image(
        "https://dashscope-a717.oss-accelerate.aliyuncs.com/x.png"
    ) == PNG


def test_download_image_does_not_retry_4xx(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_stream(*args, **kwargs):  # noqa: ARG001
        calls["n"] += 1
        return _FakeStreamResponse(404)

    monkeypatch.setattr(image_tool.httpx, "stream", fake_stream)
    monkeypatch.setattr(image_tool.time, "sleep", lambda _: None)

    with pytest.raises(UncertainOutcomeError):
        image_tool._download_image(
            "https://dashscope-a717.oss-accelerate.aliyuncs.com/x.png"
        )
    assert calls["n"] == 1


def test_download_image_rejects_foreign_host() -> None:
    with pytest.raises(UncertainOutcomeError):
        image_tool._download_image("https://example.com/x.png")


def test_download_image_rejects_non_image(monkeypatch) -> None:
    _patch_stream(monkeypatch, [_FakeStreamResponse(200, b"<html>")])
    with pytest.raises(UncertainOutcomeError):
        image_tool._download_image(
            "https://dashscope-a717.oss-accelerate.aliyuncs.com/x.png"
        )


def test_download_image_rejects_oversized(monkeypatch) -> None:
    monkeypatch.setattr(image_tool, "MAX_ARTIFACT_BYTES", 64)
    _patch_stream(monkeypatch, [_FakeStreamResponse(200, PNG + b"\0" * 100)])
    with pytest.raises(UncertainOutcomeError):
        image_tool._download_image(
            "https://dashscope-a717.oss-accelerate.aliyuncs.com/x.png"
        )


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def test_generate_image_saves_artifact(monkeypatch) -> None:
    body = {
        "output": {
            "choices": [
                {"message": {"content": [{"image": "https://x.aliyuncs.com/a.png"}]}}
            ]
        },
        "usage": {"output_width": 512, "output_height": 512},
    }
    monkeypatch.setattr(image_tool, "_post_generation", lambda *a, **k: body)  # noqa: ARG005
    monkeypatch.setattr(image_tool, "_download_image", lambda url: PNG)  # noqa: ARG005

    result = image_tool.generate_image("一只猫", size="512x512")

    assert isinstance(result, ToolResult)
    assert ARTIFACT_NAME.fullmatch(result.artifacts[0].name)
    assert result.artifacts[0].mime == "image/png"
    assert "512x512" in result.text
    assert (artifact_dir() / result.artifacts[0].name).read_bytes() == PNG


def test_unexpected_error_after_generation_is_uncertain(monkeypatch) -> None:
    """请求已经成功返回后，任何未预期异常都不能退化成“普通失败”。

    这是 2026-10-20 端到端实测踩到的真实缺陷：下载阶段一个 AttributeError
    被 executor 当成普通失败，planner 立刻准备再次生成——会在账单上叠加一次。
    """
    body = {
        "output": {
            "choices": [
                {"message": {"content": [{"image": "https://x.aliyuncs.com/a.png"}]}}
            ]
        }
    }
    monkeypatch.setattr(image_tool, "_post_generation", lambda *a, **k: body)  # noqa: ARG005

    def explode(_url: str) -> bytes:
        raise AttributeError("'URL' object has no attribute 'hostname'")

    monkeypatch.setattr(image_tool, "_download_image", explode)

    with pytest.raises(UncertainOutcomeError):
        image_tool.generate_image("一只猫")


def test_generate_image_without_url_is_uncertain(monkeypatch) -> None:
    monkeypatch.setattr(
        image_tool, "_post_generation", lambda *a, **k: {"output": {}}  # noqa: ARG005
    )
    with pytest.raises(UncertainOutcomeError):
        image_tool.generate_image("一只猫")


def test_generate_image_missing_key_is_precondition(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    with pytest.raises(PreconditionError):
        image_tool.generate_image("一只猫")


def test_reviewer_sees_tool_arguments() -> None:
    """reviewer 必须看到参数，否则它无法判断“到底有没有用参考图”。

    2026-10-20 实测：图生图成功、reference_images 也确实传了，但 reviewer
    只看结果文本，于是不敢确认、把任务驳回。参数才是真正被执行的事实。
    """
    from agi_agent.graph.nodes.reviewer import _render

    call = ToolCall(
        task_id="t",
        step_index=0,
        tool="image.generate",
        arguments={"reference_images": ["E:/pics/ref.png"], "size": "512x512"},
        status=ToolCallStatus.SUCCEEDED,
        result="图片已生成：a.png",
    )

    rendered = _render([call])
    assert "reference_images" in rendered
    assert "E:/pics/ref.png" in rendered
    assert "size=512x512" in rendered


def test_tool_is_registered_as_write_with_narrow_signature() -> None:
    spec = get("image.generate")
    assert spec.risk is RiskLevel.WRITE
    assert set(spec.parameters.model_fields) == {
        "prompt",
        "reference_images",
        "size",
        "negative_prompt",
        "seed",
    }
