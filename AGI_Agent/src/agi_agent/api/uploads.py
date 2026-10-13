"""图片上传：把前端送来的图片落到本机，再交给读图工具去用。

**为什么不走对象存储（OSS）**：读图工具读的是**本机文件路径**，图片完全不需要
离开这台机器。引入 OSS 意味着多一个外部服务、一套凭据、一笔费用，
换来的只是"绕一圈再回来"。

**它什么时候才会变得必要**：当 `AGI_Agent` 与 `AGI_Server` 部署到**不同机器**时——
那时两者之间没有共享文件系统。那是一个有明确触发条件的未来，不是现在。

**上传目录必须落在沙箱内**（`config.ALLOWED_ROOTS`）：读图工具用
`resolve_readable_path` 校验路径，落在沙箱外的话会出现"上传成功、读图被拒"
这种自相矛盾的现象，而且很难排查。默认放在项目根下的 `uploads/`，天然满足。
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from agi_agent.config import PROJECT_ROOT
from agi_agent.images import MAX_IMAGE_BYTES, extension_for, sniff_mime


class UploadRejected(ValueError):
    """上传被拒：空文件、太大、或者根本不是图片。

    单独立一个异常类型而不是抛 ValueError，是为了让 HTTP 层能干净地翻成 **400**：
    这是调用方的问题，不是服务端故障。混成 500 会把排查方向完全带偏。
    """


def upload_dir() -> Path:
    """上传目录。可用 `AGI_UPLOAD_DIR` 覆盖。

    每次调用都读环境变量（而不是模块导入时读一次），是为了让测试能把它指到临时目录——
    否则单测会往仓库里写文件，留下"跑一次测试多一堆图片"的麻烦。
    """
    override = os.environ.get("AGI_UPLOAD_DIR")
    return Path(override).expanduser() if override else PROJECT_ROOT / "uploads"


def save_image(data: bytes) -> dict:
    """保存一张图片，返回它的位置与基本信息。

    返回的 `path` 用**正斜杠**：它会出现在任务目标文本里，由模型抄进工具参数。
    反斜杠在 JSON/文本里更容易被转义或吃掉，正斜杠在 Windows 上一样能用。
    """
    if not data:
        raise UploadRejected("上传的文件是空的")

    size = len(data)
    if size > MAX_IMAGE_BYTES:
        raise UploadRejected(
            f"图片太大：收到 {size:,} 字节，上限是 {MAX_IMAGE_BYTES:,} 字节。"
            "请先缩小或裁剪后再上传。"
        )

    mime = sniff_mime(data)
    if mime is None:
        raise UploadRejected(
            "这不是受支持的图片。文件头既不是 PNG、JPEG、GIF 也不是 WEBP——"
            "请确认选的是图片文件（改过后缀名的文件也会被这里拦住）。"
        )

    target_dir = upload_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 文件名由我们生成，不用上传方给的名字：那里面可能带路径分隔符、控制字符，
    # 或者干脆是 `..\\..\\某个重要文件`——让它参与路径拼装就是自找麻烦。
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}{extension_for(mime)}"
    target = target_dir / name
    target.write_bytes(data)

    return {"path": target.as_posix(), "mime": mime, "size": size, "name": name}
