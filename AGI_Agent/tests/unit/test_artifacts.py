"""产物落盘与读取的测试。

这些测试不碰生成 API，只钉住两件事：**文件名由代码生成**、**读回时不能越界**。
路径穿越是本模块唯一的安全边界，值得单独一组用例。
"""

from __future__ import annotations

import base64
import re

import pytest

from agi_agent.artifacts import (
    MAX_ARTIFACT_BYTES,
    artifact_dir,
    resolve_artifact,
    save_artifact,
)
from agi_agent.config import PROJECT_ROOT

#: 一张真实的 1x1 PNG。用它而不是随便几个字节：``save_artifact`` 会嗅探文件头，
#: 假字节会在“格式不支持”那一支就失败，测不到真正要测的东西。
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n0kAAAAASUVORK5CYII="
)

NAME_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}\.png$")


@pytest.fixture(autouse=True)
def _artifact_dir(tmp_path, monkeypatch):
    """把产物目录指到临时目录，避免测试往仓库里写文件。"""
    monkeypatch.setenv("AGI_ARTIFACT_DIR", str(tmp_path))
    return tmp_path


def test_save_artifact_uses_generated_name_and_sniffed_mime(tmp_path) -> None:
    artifact = save_artifact(PNG)

    assert NAME_PATTERN.fullmatch(artifact.name)
    assert artifact.mime == "image/png"
    assert artifact.size == len(PNG)
    assert (tmp_path / artifact.name).read_bytes() == PNG


def test_save_artifact_is_atomic(tmp_path) -> None:
    """不能留下半截临时文件——它看起来像完整产物，排查时很难想到。"""
    save_artifact(PNG)
    assert not list(tmp_path.glob("*.tmp"))


def test_save_artifact_rejects_empty_and_non_image(tmp_path) -> None:
    with pytest.raises(ValueError):
        save_artifact(b"")
    with pytest.raises(ValueError):
        save_artifact(b"not an image")
    assert list(tmp_path.iterdir()) == []


def test_save_artifact_rejects_oversized(monkeypatch, tmp_path) -> None:
    # 不走真实 20 MB 分配：把上限调小，直接验证“超限就拒绝”这条逻辑。
    monkeypatch.setattr("agi_agent.artifacts.MAX_ARTIFACT_BYTES", 64)
    with pytest.raises(ValueError):
        save_artifact(PNG + b"\0" * 100)
    assert list(tmp_path.iterdir()) == []


def test_resolve_artifact_returns_the_file(tmp_path) -> None:
    artifact = save_artifact(PNG)
    assert resolve_artifact(artifact.name) == (tmp_path / artifact.name).resolve()


@pytest.mark.parametrize(
    "name",
    [
        "../escape.png",
        r"..\escape.png",
        "sub/dir.png",
        r"C:\Windows\x.png",
        "/tmp/x.png",
        "20261020-120000-deadbeef.png/../../x.png",
        "%2e%2e%2fescape.png",
        "20261020-120000-deadbeef.exe",
        "not-a-generated-name.png",
    ],
)
def test_resolve_artifact_rejects_traversal(name: str) -> None:
    with pytest.raises((ValueError, FileNotFoundError)):
        resolve_artifact(name)


def test_resolve_artifact_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        resolve_artifact("20261020-120000-deadbeef.png")


def test_default_artifact_dir_is_inside_project(monkeypatch) -> None:
    """默认目录必须在项目根下——它同时是本地文件工具的沙箱边界。"""
    monkeypatch.delenv("AGI_ARTIFACT_DIR", raising=False)
    assert artifact_dir() == PROJECT_ROOT / "artifacts"
    assert PROJECT_ROOT in artifact_dir().parents


def test_max_artifact_bytes_is_larger_than_input_limit() -> None:
    """产物上限不能沿用输入图片的 5 MB：2K PNG 很容易超过它。"""
    assert MAX_ARTIFACT_BYTES > 5_000_000
