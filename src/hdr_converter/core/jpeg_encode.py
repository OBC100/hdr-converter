"""JPEG 编码选项与 mozjpeg / Pillow 编码。

mozjpeg 参数（Forza 样张 benchmark，quality=90）：
- ``optimize=True`` + ``progressive=True`` + ``notrellis=False``（trellis 量化）
  为同质量下最小体积；关闭 trellis 约快 2×，体积约 +9%。
- ``subsampling`` 保持默认 4:2:0；强制 444 约 +28% 体积。
"""

from __future__ import annotations

import io
from enum import Enum
from functools import lru_cache

import numpy as np
from PIL import Image

from .jpeg_icc import prepend_icc_profile


class JpegSubsampling(str, Enum):
    """色度抽样：4:2:0 / 4:2:2 / 4:4:4。"""

    S420 = "420"
    S422 = "422"
    S444 = "444"


DEFAULT_JPEG_SUBSAMPLING = JpegSubsampling.S420

_MOZJPEG_VALUE: dict[JpegSubsampling, str] = {
    JpegSubsampling.S420: "420",
    JpegSubsampling.S422: "422",
    JpegSubsampling.S444: "444",
}

_PILLOW_VALUE: dict[JpegSubsampling, int] = {
    JpegSubsampling.S420: 2,
    JpegSubsampling.S422: 1,
    JpegSubsampling.S444: 0,
}


def normalize_jpeg_subsampling(value: JpegSubsampling | str) -> JpegSubsampling:
    if isinstance(value, JpegSubsampling):
        return value
    text = str(value).strip().lower().replace(":", "")
    mapping = {
        "420": JpegSubsampling.S420,
        "422": JpegSubsampling.S422,
        "444": JpegSubsampling.S444,
        "4:2:0": JpegSubsampling.S420,
        "4:2:2": JpegSubsampling.S422,
        "4:4:4": JpegSubsampling.S444,
    }
    try:
        return mapping[text]
    except KeyError as exc:
        allowed = ", ".join(sorted(mapping))
        raise ValueError(f"jpeg_subsampling 须为 {allowed}，收到 {value!r}") from exc


def mozjpeg_subsampling_arg(mode: JpegSubsampling) -> str:
    return _MOZJPEG_VALUE[mode]


def pillow_subsampling_arg(mode: JpegSubsampling) -> int:
    return _PILLOW_VALUE[mode]


@lru_cache(maxsize=1)
def mozjpeg_available() -> bool:
    try:
        import imagecodecs

        imagecodecs.mozjpeg_version()
        return True
    except (ImportError, AttributeError, RuntimeError):
        return False


def encode_rgb_jpeg(
    rgb: np.ndarray,
    *,
    quality: int,
    icc: bytes | None = None,
    subsampling: JpegSubsampling = DEFAULT_JPEG_SUBSAMPLING,
) -> bytes:
    """将 RGB uint8（H×W×3）编码为 JPEG 字节。"""
    arr = np.ascontiguousarray(rgb[..., :3], dtype=np.uint8)
    q = max(1, min(100, int(quality)))
    mode = normalize_jpeg_subsampling(subsampling)

    if mozjpeg_available():
        import imagecodecs

        jpeg = bytes(
            imagecodecs.mozjpeg_encode(
                arr,
                level=q,
                optimize=True,
                progressive=True,
                notrellis=False,
                subsampling=mozjpeg_subsampling_arg(mode),
            )
        )
        return prepend_icc_profile(jpeg, icc) if icc else jpeg

    buf = io.BytesIO()
    save_kw: dict = {
        "format": "JPEG",
        "quality": q,
        "subsampling": pillow_subsampling_arg(mode),
    }
    if icc:
        save_kw["icc_profile"] = icc
    Image.fromarray(arr, "RGB").save(buf, **save_kw)
    return buf.getvalue()
