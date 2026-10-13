"""图片格式与体积的公共判断。

**为什么单独一个模块**：同一条"什么算可接受的图片"的规则，现在有**两处**要用——
读图工具（处理本机已有的文件）与上传接口（处理刚收到的字节）。
两边各写一套的话，迟早会出现"上传时接受了、调用时却拒绝"这类自相矛盾的行为，
而且这种不一致不会报错，只会让人困惑。

**看文件头，不看扩展名**：扩展名是随手可以改的，文件头才是事实。
这和 `files.read_file` 用 ``\\x00`` 判定二进制是同一个思路。
"""

from __future__ import annotations

#: 图片体积上限。**这是本项目自保用的值，不是查证过的厂商限制**——
#: 目的是给 base64 编码后的请求体（约放大 1.33 倍）定一个内存与上传时间的界。
#: 实测一张 600x240 的 PNG 只占 154 个图像 token，正常截图远达不到它。
MAX_IMAGE_BYTES = 5_000_000

#: 文件头 → MIME。只收这四种：都是浏览器与千问都认的常见格式。
#: 用 tuple 而不是 dict，是因为要按顺序做 startswith 比较（部分前缀有重叠）。
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

#: MIME → 存盘用的扩展名。上传的文件必须落成正确的扩展名，
#: 否则以后人（或别的工具）看目录时会被文件名误导。
_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def sniff_mime(data: bytes) -> str | None:
    """按文件头判断图片类型；认不出来返回 None。"""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    # WEBP 的头是分段的（RIFF....WEBP），不能简单 startswith。
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def extension_for(mime: str) -> str:
    """给定 MIME 返回扩展名；未知类型返回空串（调用方不该走到这里）。"""
    return _EXTENSIONS.get(mime, "")
