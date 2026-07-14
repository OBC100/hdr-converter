"""解码器注册表（格式 → SourceImage）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .avif_decoder import decode_avif_to_source_image, is_avif_supported
from .heif_decoder import decode_heif_to_source_image, is_heif_supported
from .jpeg_decoder import decode_jpeg_to_source_image, is_jpeg_supported
from .jxl_decoder import decode_jxl_to_source_image, is_jxl_supported
from .jxr_decoder import decode_jxr_to_source_image, is_jxr_supported
from .png_decoder import decode_png_to_source_image, is_png_supported
from ..source_image import SourceImage

DecoderFn = Callable[[str | Path], SourceImage]


DECODER_REGISTRY: dict[str, DecoderFn] = {
    "jxr": decode_jxr_to_source_image,
    "png": decode_png_to_source_image,
    "jpg": decode_jpeg_to_source_image,
    "jpeg": decode_jpeg_to_source_image,
    "avif": decode_avif_to_source_image,
    "heif": decode_heif_to_source_image,
    "heic": decode_heif_to_source_image,
    "jxl": decode_jxl_to_source_image,
}


def is_format_supported(fmt: str) -> bool:
    key = fmt.lower().lstrip(".")
    checkers = {
        "jxr": is_jxr_supported,
        "png": is_png_supported,
        "jpg": is_jpeg_supported,
        "jpeg": is_jpeg_supported,
        "avif": is_avif_supported,
        "heif": is_heif_supported,
        "heic": is_heif_supported,
        "jxl": is_jxl_supported,
    }
    fn = checkers.get(key)
    return bool(fn and fn())


def get_decoder(fmt: str) -> DecoderFn:
    key = fmt.lower().lstrip(".")
    try:
        return DECODER_REGISTRY[key]
    except KeyError as exc:
        raise ValueError(f"无解码器: {fmt}") from exc


def decode_to_source_image(path: str | Path, fmt: str | None = None) -> SourceImage:
    """按扩展名（或显式 fmt）分发到已注册解码器。"""
    path = Path(path)
    key = (fmt or path.suffix).lower().lstrip(".")
    if not is_format_supported(key):
        raise RuntimeError(f"格式不可用或未注册: {key}")
    return get_decoder(key)(path)
