"""输入格式魔数检测（优先于扩展名）。"""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class InputFormat(str, Enum):
    JXR = "jxr"
    PNG = "png"
    JPEG = "jpeg"
    AVIF = "avif"
    HEIF = "heif"
    JXL = "jxl"
    UNKNOWN = "unknown"


_EXT_MAP: dict[str, InputFormat] = {
    ".jxr": InputFormat.JXR,
    ".wdp": InputFormat.JXR,
    ".hdp": InputFormat.JXR,
    ".png": InputFormat.PNG,
    ".jpg": InputFormat.JPEG,
    ".jpeg": InputFormat.JPEG,
    ".avif": InputFormat.AVIF,
    ".heif": InputFormat.HEIF,
    ".heic": InputFormat.HEIF,
    ".jxl": InputFormat.JXL,
}


def detect_format(path: str | Path, *, peek: bytes | None = None) -> InputFormat:
    """魔数优先，扩展名兜底。"""
    path = Path(path)
    data = peek
    if data is None:
        try:
            with path.open("rb") as f:
                data = f.read(32)
        except OSError:
            data = b""

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return InputFormat.PNG
    if len(data) >= 3 and data[0] == 0xFF and data[1] == 0xD8:
        return InputFormat.JPEG
    # JXL ISOBMFF signature
    if data.startswith(bytes.fromhex("0000000c4a584c200d0a870a")):
        return InputFormat.JXL
    # raw JXL codestream
    if data.startswith(b"\xff\x0a"):
        return InputFormat.JXL
    # ISOBMFF ftyp
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brands = data[8:32]
        if b"avif" in brands or b"avis" in brands:
            return InputFormat.AVIF
        if b"heic" in brands or b"heix" in brands or b"mif1" in brands or b"msf1" in brands:
            return InputFormat.HEIF
        if b"jxl " in brands:
            return InputFormat.JXL
    # JPEG XR TIFF-like II*\0 or MM\0*
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):
        # could be TIFF; JXR often starts with II*\x00 + JPEG XR GUID later
        # also check for JPEG XR magic at start used by some writers
        if b"WMPHOTO" in data[:64] or path.suffix.lower() in (".jxr", ".wdp", ".hdp"):
            return InputFormat.JXR
    # imagecodecs JXR sometimes has different header — fall back to ext
    return _EXT_MAP.get(path.suffix.lower(), InputFormat.UNKNOWN)


def format_to_decoder_key(fmt: InputFormat) -> str | None:
    if fmt == InputFormat.UNKNOWN:
        return None
    if fmt == InputFormat.JPEG:
        return "jpg"
    return fmt.value
