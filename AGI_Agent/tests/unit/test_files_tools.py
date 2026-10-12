"""本地文件工具的三道边界。

第 2 条最重要：项目根目录下放着含真实 API Key 的 .env，
只要沙箱允许读它，Agent 就可能把密钥读进上下文、写进执行记录。
"""

from __future__ import annotations

import pytest

from agi_agent.config import ALLOWED_ROOTS, PROJECT_ROOT
from agi_agent.tools.builtin.files import list_dir, read_file


def test_reads_a_file_inside_the_sandbox():
    content = read_file("README.md")
    assert "AGI Agent" in content


def test_resolves_relative_paths_against_the_allowed_root():
    assert read_file("pyproject.toml") == (PROJECT_ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_reads_absolute_path_inside_the_sandbox():
    assert "AGI Agent" in read_file(str(PROJECT_ROOT / "README.md"))


def test_env_file_is_refused():
    """守则 §7：密钥不得进入对话、代码或日志。"""
    with pytest.raises(PermissionError, match="凭据"):
        read_file(".env")


@pytest.mark.parametrize("name", ["secret.key", "server.pem", "id_rsa", ".netrc"])
def test_credential_like_names_are_refused(name):
    with pytest.raises(PermissionError):
        read_file(name)


def test_parent_traversal_is_refused():
    with pytest.raises(PermissionError, match="允许范围"):
        read_file("../Memory.md")


def test_absolute_path_outside_the_sandbox_is_refused():
    with pytest.raises(PermissionError, match="允许范围"):
        read_file(str(PROJECT_ROOT.parent / "Memory.md"))


def test_git_directory_is_refused():
    with pytest.raises(PermissionError):
        read_file(".git/config")


def test_secrets_directory_is_refused():
    """登录会话状态等同于凭据——Agent 绝不能读到自己的登录态。"""
    with pytest.raises(PermissionError, match="受保护"):
        read_file(".secrets/steam_state.json")


def test_secrets_directory_is_not_listable():
    with pytest.raises(PermissionError, match="受保护"):
        list_dir(".secrets")


def test_directory_is_reported_as_such():
    """报"文件不存在"会把重规划引向错误方向——它是目录，不是缺失。"""
    with pytest.raises(IsADirectoryError, match="list_dir"):
        read_file("src")


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        read_file("no-such-file-anywhere.txt")


def test_list_dir_lists_project_root():
    listing = list_dir(".")
    assert "pyproject.toml" in listing
    assert "src" in listing


def test_list_dir_hides_credential_files():
    """清单里也不能暴露凭据文件的存在。"""
    assert ".env" not in list_dir(".")


def test_list_dir_rejects_traversal():
    with pytest.raises(PermissionError):
        list_dir("..")


def test_allowed_roots_defaults_to_project_root():
    assert ALLOWED_ROOTS == [PROJECT_ROOT]
