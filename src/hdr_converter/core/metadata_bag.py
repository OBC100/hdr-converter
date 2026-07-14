"""统一元数据袋（Stage H 骨架）。

原则见 ``docs/EXECUTION_PLAN.md`` §H：默认整块 bytes 透传，不反序列化 MakerNote；
Orientation 仅做原地 SHORT patch；ICC 整块替换或保留。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PrivacyMode(str, Enum):
    """元数据隐私策略。"""

    PRESERVE_ALL = "preserve_all"
    """保留全部可透传块。"""

    ORIENTATION_AND_ICC = "orientation_and_icc"
    """仅方向 + ICC（清除 GPS/人名等）。"""

    STRIP_ALL = "strip_all"
    """清除全部（ICC 是否保留由 ``IccPolicy`` / 写出策略另定）。"""


@dataclass
class MetadataBag:
    """容器无关的元数据袋：各字段为原始字节块，不做深度解析。"""

    exif: bytes | None = None
    """含 TIFF 头的 EXIF blob（JPEG APP1 payload 去掉 ``Exif\\0\\0`` 前缀后的部分，或 PNG eXIf）。"""

    xmp: bytes | None = None
    """完整 XMP 包（可含 xpacket 包装）。"""

    iptc: bytes | None = None
    icc: bytes | None = None
    """嵌入 ICC；写出时也可能被 ``icc_policy`` 替换为生成 profile。"""

    # 格式特有附属（原样透传，Stage H 再接线）
    extras: dict[str, bytes] = field(default_factory=dict)

    # 解析出的只读提示（不写回）
    orientation: int | None = None
    """EXIF Orientation 1–8；None = 未知/不存在。"""


def empty_bag() -> MetadataBag:
    return MetadataBag()


def extract_orientation_from_exif(exif: bytes) -> int | None:
    """从 EXIF TIFF 扫描 IFD0 Orientation (0x0112)；失败返回 None。

    仅读、不改；Stage H 的原地 patch 将复用同一扫描逻辑。
    """
    if len(exif) < 8:
        return None
    # 允许带/不带 "Exif\0\0" 前缀
    data = exif
    if data.startswith(b"Exif\x00\x00"):
        data = data[6:]
    if len(data) < 8:
        return None
    endian = data[:2]
    if endian == b"II":
        endian_fmt = "<"
    elif endian == b"MM":
        endian_fmt = ">"
    else:
        return None
    import struct

    try:
        magic = struct.unpack_from(endian_fmt + "H", data, 2)[0]
        if magic != 42:
            return None
        ifd_off = struct.unpack_from(endian_fmt + "I", data, 4)[0]
        if ifd_off + 2 > len(data):
            return None
        count = struct.unpack_from(endian_fmt + "H", data, ifd_off)[0]
        for i in range(count):
            entry = ifd_off + 2 + i * 12
            if entry + 12 > len(data):
                break
            tag, typ, _cnt, val = struct.unpack_from(endian_fmt + "HHII", data, entry)
            if tag == 0x0112 and typ == 3:  # SHORT
                # 值内联在 entry 后 4 字节的前 2 字节
                orient = struct.unpack_from(endian_fmt + "H", data, entry + 8)[0]
                if 1 <= orient <= 8:
                    return int(orient)
                return None
    except struct.error:
        return None
    return None


def patch_exif_orientation(exif: bytes, orientation: int = 1) -> bytes:
    """原地覆写 Orientation；无该 tag 则原样返回（不强行插入）。"""
    if not (1 <= orientation <= 8):
        raise ValueError(f"invalid orientation: {orientation}")
    prefix = b""
    data = bytearray(exif)
    if data.startswith(b"Exif\x00\x00"):
        prefix = bytes(data[:6])
        data = data[6:]
    if len(data) < 8:
        return exif
    endian = bytes(data[:2])
    if endian == b"II":
        endian_fmt = "<"
    elif endian == b"MM":
        endian_fmt = ">"
    else:
        return exif
    import struct

    try:
        if struct.unpack_from(endian_fmt + "H", data, 2)[0] != 42:
            return exif
        ifd_off = struct.unpack_from(endian_fmt + "I", data, 4)[0]
        count = struct.unpack_from(endian_fmt + "H", data, ifd_off)[0]
        for i in range(count):
            entry = ifd_off + 2 + i * 12
            if entry + 12 > len(data):
                break
            tag, typ, _cnt, _val = struct.unpack_from(endian_fmt + "HHII", data, entry)
            if tag == 0x0112 and typ == 3:
                struct.pack_into(endian_fmt + "H", data, entry + 8, orientation)
                return prefix + bytes(data)
    except struct.error:
        return exif
    return exif


# 占位：各格式 extract/embed 在 Stage H 任务清单中实现
def extract_metadata(path: Any, fmt_hint: str | None = None) -> MetadataBag:  # noqa: ARG001
    """Stage H：按格式整块提取。当前返回空袋。"""
    return empty_bag()


def apply_privacy(bag: MetadataBag, mode: PrivacyMode) -> MetadataBag:
    if mode == PrivacyMode.PRESERVE_ALL:
        return bag
    if mode == PrivacyMode.STRIP_ALL:
        return MetadataBag()
    # ORIENTATION_AND_ICC
    return MetadataBag(
        exif=None,  # 方向信息可在像素转正后丢弃；保留需另存 orientation 字段
        xmp=None,
        iptc=None,
        icc=bag.icc,
        orientation=bag.orientation,
    )
