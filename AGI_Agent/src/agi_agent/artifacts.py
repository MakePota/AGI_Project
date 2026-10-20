"""产物文件的落盘与读取。

与 ``images.py`` 的分工：``images`` 只回答“这段字节是不是受支持的图片”，
本模块负责“把已经确认过的图片存成什么名字、之后怎么安全地找回来”。

**为什么产物目录要落在项目根下**：本地文件工具的沙箱边界是 ``ALLOWED_ROOTS``
（默认项目根）。把产物放到项目根之外，会出现“界面能看、但 Agent 自己读不了”
这种自相矛盾的现象——而“看看刚生成的图”正是最容易出现的下一个请求。

**文件名的形状是安全边界的一部分**：``resolve_artifact`` 只接受本模块生成的那种
名字（时间戳-随机后缀.扩展名）。即使调用方传入 ``../../.env`` 或
``C:\\Windows\\...``，正则直接就拒绝了；后面还有一次 ``resolve()`` 后的父目录比较，
两道都要过。
"""

from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path

from agi_agent.config import PROJECT_ROOT
from agi_agent.images import extension_for, sniff_mime
from agi_agent.schemas import Artifact

#: 产物体积上限。**不能沿用 ``MAX_IMAGE_BYTES``（5 MB）**：那是“输入图片”的上限，
#: 而 2048x2048 的 PNG 很容易超过 5 MB。20 MB 给 2K 输出留足余量，同时仍然是一个
#: 有界的值——下载和 Server 侧的内存缓冲都按它来设。
MAX_ARTIFACT_BYTES = 20_000_000

#: 只接受本模块自己生成的名字：8 位日期-6 位时间-8 位十六进制.扩展名。
_NAME_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}\.(?:png|jpg|gif|webp)$")


def artifact_dir() -> Path:
    """产物目录。可用 ``AGI_ARTIFACT_DIR`` 覆盖。

    每次调用都读环境变量（而不是模块导入时读一次），理由与 ``uploads.upload_dir``
    相同：单测要能把它指到临时目录，否则测试会往仓库里写文件。
    """
    override = os.environ.get("AGI_ARTIFACT_DIR")
    return Path(override).expanduser() if override else PROJECT_ROOT / "artifacts"


def save_artifact(data: bytes) -> Artifact:
    """把一段已经确认是图片的字节落盘，返回产物元数据。

    **MIME 从文件头嗅探，不采信调用方或供应商给的 Content-Type。**
    扩展名也跟着嗅探结果走——否则会出现“内容是 PNG、文件名却是 .jpg”的欺骗。

    **原子写**：先写同目录临时文件，再 ``os.replace``。直接写目标文件时，
    进程中途崩溃会留下半截图片；而它看起来像一个完整产物，界面会显示破图，
    排查时很难想到是“写了一半”。
    """
    if not data:
        raise ValueError("产物是空的，没有可保存的内容")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError(
            f"产物太大：{len(data):,} 字节，上限是 {MAX_ARTIFACT_BYTES:,} 字节"
        )

    mime = sniff_mime(data)
    if mime is None:
        raise ValueError("产物不是受支持的图片（文件头既不是 PNG/JPEG/GIF/WEBP）")

    target_dir = artifact_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 同一秒内生成多张图时，随机后缀负责避免碰撞；循环只是防御极端情况。
    # 文件名完全由代码生成，绝不使用外部输入——那里面可能带路径分隔符或控制字符。
    for _ in range(10):
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}{extension_for(mime)}"
        target = target_dir / name
        if not target.exists():
            break
    else:  # pragma: no cover  10 次都撞名在实际中不可能发生
        raise RuntimeError("无法生成不重复的产物文件名")

    temp = target.with_name(target.name + ".tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, target)
    finally:
        # 失败时清掉临时文件；成功时 os.replace 已经把它移走了，unlink 会抛
        # FileNotFoundError——所以只在它还在的时候删。
        if temp.exists():
            temp.unlink(missing_ok=True)

    return Artifact(name=name, mime=mime, size=len(data))


def resolve_artifact(name: str) -> Path:
    """把产物名解析成本机路径，并做路径穿越校验。

    非法名字抛 ``ValueError``，文件不存在抛 ``FileNotFoundError``。
    API 层把两者都翻成 **404**：对外不区分“名字非法”和“文件不存在”，
    否则这个端点就成了探测目录结构的工具。
    """
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"不是合法的产物名：{name!r}")

    root = artifact_dir().resolve()
    target = (root / name).resolve()

    # 正则已经排除了分隔符与 ``..``；这一行是第二道防线。``resolve()`` 会展开
    # 符号链接与 ``..``，所以必须比较**解析后**的父目录，而不是字符串前缀。
    if target.parent != root:
        raise ValueError(f"产物路径越界：{name!r}")

    if not target.is_file():
        raise FileNotFoundError(f"没有这个产物：{name}")

    return target
