"""本地只读文件工具。

三道边界，缺一不可：

1. **目录沙箱**：只允许访问 ``config.ALLOWED_ROOTS`` 内的路径（默认仅项目根）。
2. **凭据黑名单**：拒绝读取 ``.env``、私钥、``.git``、``.ssh`` 等。
   本项目根目录下就放着含真实 API Key 的 ``.env``——只要允许读任意文件，
   Agent 就可能把密钥读进上下文、写进执行记录，直接违反守则 §7。
3. **容量上限**：截断超大文件与超长目录，避免灌爆模型上下文。

路径一律先 ``resolve()`` 再校验，因此 ``..`` 与指向外部的符号链接都会被拦住。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from agi_agent.config import ALLOWED_ROOTS
from agi_agent.schemas import RiskLevel
from agi_agent.tools.registry import ToolSpec, register

_MAX_READ_CHARS = 20_000
_MAX_LIST_ENTRIES = 200

#: 任何一段路径命中即拒绝。
#: `.secrets` 存放登录会话状态，等同于凭据——Agent 绝不能读到自己的登录态。
_DENIED_DIR_PARTS = {
    ".git",
    ".ssh",
    ".venv",
    ".idea",
    ".secrets",
    "node_modules",
    "__pycache__",
}

#: 文件名命中即拒绝。
_DENIED_NAMES = {".env", ".netrc", "credentials", "id_rsa", "id_ed25519", "id_ecdsa"}
_DENIED_PREFIXES = (".env.",)
_DENIED_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".jks", ".keystore")


def _denied(path: Path) -> str | None:
    """返回拒绝原因；允许访问时返回 None。"""
    if any(part in _DENIED_DIR_PARTS for part in path.parts):
        return f"路径中包含受保护的目录名（{path}）"
    name = path.name.lower()
    if name in _DENIED_NAMES:
        return f"{path.name} 属于凭据文件，禁止读取"
    if name.startswith(_DENIED_PREFIXES):
        return f"{path.name} 属于凭据文件，禁止读取"
    if name.endswith(_DENIED_SUFFIXES):
        return f"{path.name} 属于密钥文件，禁止读取"
    return None


def resolve_readable_path(path: str) -> Path:
    """把入参解析成受信路径；越界或命中黑名单则抛错。

    **这是本模块对外的沙箱入口，其他只读工具必须复用它**（图像工具就是这么做的），
    不要各自实现一遍：沙箱规则一旦有两份实现，就必然有一份先过期，
    而这类漏洞不会报错、只会安静地放行本不该读的东西。

    注意它只管"能不能读"，不管"读出来是什么"——字节级的上限与格式判断
    由各工具自己负责（文本与图片的合理上限完全不同）。
    """
    raw = Path(path).expanduser()
    # 相对路径以第一个允许根为基准。
    candidate = (raw if raw.is_absolute() else ALLOWED_ROOTS[0] / raw).resolve()

    inside = any(candidate == root or root in candidate.parents for root in ALLOWED_ROOTS)
    if not inside:
        raise PermissionError(
            f"路径不在允许范围内：{candidate}。允许的根目录："
            f"{'、'.join(str(r) for r in ALLOWED_ROOTS)}"
        )

    reason = _denied(candidate)
    if reason:
        raise PermissionError(reason)

    return candidate


class ListDirArgs(BaseModel):
    path: str = Field(default=".", description="目录路径；相对路径以项目根为基准")


class ReadFileArgs(BaseModel):
    path: str = Field(description="文件路径；相对路径以项目根为基准")


def list_dir(path: str = ".") -> str:
    target = resolve_readable_path(path)
    if not target.is_dir():
        raise NotADirectoryError(f"不是目录：{path}")

    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines = []
    for entry in entries[:_MAX_LIST_ENTRIES]:
        if _denied(entry.resolve()):
            continue  # 目录清单里也不暴露凭据文件的名字
        lines.append(("[目录] " if entry.is_dir() else "       ") + entry.name)
    if len(entries) > _MAX_LIST_ENTRIES:
        lines.append(f"…（共 {len(entries)} 项，已截断）")
    return "\n".join(lines) if lines else "（空目录）"


def read_file(path: str) -> str:
    target = resolve_readable_path(path)
    if target.is_dir():
        raise IsADirectoryError(f"这是一个目录，不是文件：{path}。可改用 local.list_dir")
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")

    data = target.read_bytes()
    if b"\x00" in data[:8192]:
        # 报错要可操作：光说"这是二进制"没有下一步。图片是最常见的撞墙场景，
        # 所以直接点出该用哪个工具，避免 planner 一脸茫然地重试同一个调用。
        raise ValueError(
            f"看起来是二进制文件，拒绝按文本读取：{path}。"
            "如果它是图片，请改用 vision.describe_image（简单描述）"
            "或 vision.analyze_image（需要推理的问题）。"
        )

    text = data.decode("utf-8", errors="replace")
    if len(text) > _MAX_READ_CHARS:
        text = text[:_MAX_READ_CHARS] + f"\n…（已截断，全文共 {len(text)} 字符）"
    return text


register(
    ToolSpec(
        name="local.list_dir",
        description="列出指定目录下的文件与子目录",
        risk=RiskLevel.READ,
        parameters=ListDirArgs,
        func=list_dir,
    )
)

register(
    ToolSpec(
        name="local.read_file",
        description="读取指定文本文件的内容，供总结或检索",
        risk=RiskLevel.READ,
        parameters=ReadFileArgs,
        func=read_file,
    )
)
