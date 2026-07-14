"""JXR (JPEG XR) 解码。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..canonical import SCRGB_REFERENCE_WHITE_NITS
from ..cicp import Gamut
from ..source_image import SourceImage


class JXRDecodeError(RuntimeError):
    pass


def is_jxr_supported() -> bool:
    """运行时检测 JPEG XR 编解码器是否可用（兼容 PyInstaller 冻结环境）。"""
    try:
        from imagecodecs import JPEGXR

        return bool(JPEGXR.available)
    except ImportError:
        return False


def _require_jpegxr():
    if not is_jxr_supported():
        raise JXRDecodeError(
            "JPEG XR 解码不可用。请安装 imagecodecs: pip install imagecodecs"
        )
    from imagecodecs import imread, jpegxr_decode

    return imread, jpegxr_decode


def decode_jxr(path: str | Path) -> np.ndarray:
    """
    解码 JXR 文件为 float RGBA 数组。

    Windows HDR 截图通常为 float16 RGBA，scRGB 色彩空间。
    """
    imread, jpegxr_decode = _require_jpegxr()

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    try:
        image = imread(str(path))
    except Exception as exc:
        raise JXRDecodeError(f"无法解码 JXR: {path.name}") from exc

    if image is None:
        data = path.read_bytes()
        image = jpegxr_decode(data)

    arr = np.asarray(image)
    if arr.ndim != 3 or arr.shape[-1] < 3:
        raise JXRDecodeError(f"意外的 JXR 数据形状: {arr.shape}")

    return arr.astype(np.float32, copy=False)


def decode_jxr_bytes(data: bytes) -> np.ndarray:
    _, jpegxr_decode = _require_jpegxr()
    arr = np.asarray(jpegxr_decode(data), dtype=np.float32)
    return arr


def decode_jxr_to_source_image(path: str | Path) -> SourceImage:
    """解码 JXR → ``SourceImage``（原生 scRGB，1.0 ≈ 80 nits）。"""
    raw = decode_jxr(path)
    alpha = raw[..., 3] if raw.shape[-1] >= 4 else None
    return SourceImage(
        linear=raw,
        primaries=Gamut.SRGB,
        reference_white_nits=SCRGB_REFERENCE_WHITE_NITS,
        is_hdr=True,
        alpha=alpha,
    )
