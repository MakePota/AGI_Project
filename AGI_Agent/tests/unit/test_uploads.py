"""图片上传。

**不碰网络、不启服务**：这里测的是"收下来的字节该怎么处理"——校验规则、
落盘、以及返回的路径长什么样。真实的 multipart 收发交给端到端（`ui_smoke.py`）。

这里最该钉住的一条是：**上传的校验规则必须和读图工具是同一套**。
两边各写一套的话，迟早出现"上传时接受了、调用时被拒"这种自相矛盾的行为。
"""

from __future__ import annotations

import pytest

from agi_agent.api.uploads import UploadRejected, save_image, upload_dir
from agi_agent.images import MAX_IMAGE_BYTES, extension_for, sniff_mime

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture
def temp_upload_dir(monkeypatch, tmp_path):
    """把上传目录指到临时目录。

    没有这个，测试会往仓库里的 `uploads/` 写文件——跑几次测试就多出一堆图片，
    而且它们还会被本地文件工具（沙箱内）读到。`upload_dir()` 每次调用读环境变量，
    所以这里 setenv 就够，不需要重载模块。
    """
    monkeypatch.setenv("AGI_UPLOAD_DIR", str(tmp_path))
    return tmp_path


def test_saves_a_png_and_returns_a_usable_path(temp_upload_dir) -> None:
    result = save_image(_PNG)

    assert result["mime"] == "image/png"
    assert result["size"] == len(_PNG)
    assert result["path"].endswith(".png"), "扩展名要按真实格式给，不能沿用调用方的名字"
    assert "\\" not in result["path"], "必须用正斜杠：这个路径会被模型抄进工具参数"

    saved = temp_upload_dir / result["name"]
    assert saved.read_bytes() == _PNG, "存下来的字节要和收到的一模一样"


@pytest.mark.usefixtures("temp_upload_dir")
def test_extension_matches_the_real_format_not_the_filename() -> None:
    """扩展名按**文件头**决定，不看上传方给的原始文件名。

    否则一个叫 `report.png` 的 JPEG 会以 .png 落盘，之后每个看到它的人
    （包括我们自己）都得重新判断一次它到底是什么。
    """
    result = save_image(_JPEG)
    assert result["path"].endswith(".jpg")


@pytest.mark.usefixtures("temp_upload_dir")
def test_generated_names_do_not_collide() -> None:
    names = {save_image(_PNG)["name"] for _ in range(20)}
    assert len(names) == 20, "同一秒内连传多张也不能互相覆盖"


@pytest.mark.usefixtures("temp_upload_dir")
def test_rejects_empty_upload() -> None:
    with pytest.raises(UploadRejected, match="空"):
        save_image(b"")


@pytest.mark.usefixtures("temp_upload_dir")
def test_rejects_oversized_image() -> None:
    with pytest.raises(UploadRejected) as excinfo:
        save_image(_PNG + b"\x00" * MAX_IMAGE_BYTES)
    # 报错要给出实际大小与上限，否则用户不知道该缩到多小
    assert f"{MAX_IMAGE_BYTES:,}" in str(excinfo.value)


@pytest.mark.usefixtures("temp_upload_dir")
def test_rejects_non_image_bytes() -> None:
    with pytest.raises(UploadRejected, match="不是受支持的图片"):
        save_image("#!/bin/sh\necho 这不是图片\n".encode())


def test_nothing_is_written_when_rejected(temp_upload_dir) -> None:
    """被拒的上传不能留下痕迹——否则目录里会堆满失败品，没人知道哪些是有效的。"""
    with pytest.raises(UploadRejected):
        save_image(b"not an image")
    assert list(temp_upload_dir.iterdir()) == []


def test_upload_dir_is_overridable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGI_UPLOAD_DIR", str(tmp_path / "custom"))
    assert upload_dir() == tmp_path / "custom"


def test_default_upload_dir_sits_inside_the_sandbox(monkeypatch) -> None:
    """默认目录必须在项目根下——读图工具的沙箱边界就是它。

    落在沙箱外的话会出现"上传成功、读图被拒"，而且那种不一致很难查。
    """
    from agi_agent.config import ALLOWED_ROOTS, PROJECT_ROOT

    monkeypatch.delenv("AGI_UPLOAD_DIR", raising=False)
    assert upload_dir() == PROJECT_ROOT / "uploads"
    assert PROJECT_ROOT in ALLOWED_ROOTS


def test_sniffing_and_extension_agree() -> None:
    """两个函数必须对同一份 MIME 有一致的认识——否则会存出没有扩展名的图片。"""
    for data in (_PNG, _JPEG):
        mime = sniff_mime(data)
        assert mime is not None
        assert extension_for(mime), f"{mime} 没有对应的扩展名"
