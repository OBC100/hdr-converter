"""普通 JPEG / Ultra HDR JPEG 解码 → SourceImage。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..canonical import SDR_REFERENCE_WHITE_NITS
from ..cicp import Gamut, TransferCurve
from ..named_colourspaces import parse_icc_to_descriptor
from ..source_image import SourceImage
from ..transfer_decode import (
    encoded_to_display_linear,
    encoded_to_linear_via_colourspace,
    srgb_eotf,
)


class JPEGDecodeError(RuntimeError):
    pass


def is_jpeg_supported() -> bool:
    try:
        from PIL import Image  # noqa: F401

        return True
    except ImportError:
        return False


def _looks_like_jpeg(data: bytes) -> bool:
    return len(data) >= 3 and data[0] == 0xFF and data[1] == 0xD8


def _extract_jpeg_icc(im) -> bytes | None:
    """从 Pillow Image 取出 ICC（若有）。"""
    icc = im.info.get("icc_profile")
    if isinstance(icc, (bytes, bytearray)) and len(icc) > 128:
        return bytes(icc)
    return None


def decode_jpeg_to_source_image(path: str | Path) -> SourceImage:
    """解码 JPEG：有 ISO 21496 Gain Map 则还原 HDR；否则按 ICC / sRGB。"""
    if not is_jpeg_supported():
        raise JPEGDecodeError("JPEG 解码不可用。请安装 Pillow: pip install Pillow")

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    data = path.read_bytes()
    if not _looks_like_jpeg(data):
        raise JPEGDecodeError(f"不是有效的 JPEG 文件: {path.name}")

    # Stage D：Ultra HDR demux
    try:
        from ..gainmap_demux import demux_uhdr_jpeg_to_hdr, result_to_source_image

        gm = demux_uhdr_jpeg_to_hdr(data, gamut=Gamut.SRGB)
        if gm is not None:
            return result_to_source_image(gm)
    except Exception:
        pass

    from PIL import Image
    import io

    with Image.open(io.BytesIO(data)) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
        icc_profile = _extract_jpeg_icc(im)

    if icc_profile is not None:
        desc = parse_icc_to_descriptor(icc_profile)
        if desc is not None:
            builtin = desc.as_builtin_gamut()
            if builtin is not None:
                linear = encoded_to_display_linear(rgb, TransferCurve.SRGB)
                return SourceImage(
                    linear=linear,
                    primaries=builtin,
                    reference_white_nits=SDR_REFERENCE_WHITE_NITS,
                    is_hdr=False,
                    alpha=None,
                    icc_profile=icc_profile,
                )
            linear = encoded_to_linear_via_colourspace(rgb, desc)
            return SourceImage(
                linear=linear,
                primaries=desc,
                reference_white_nits=SDR_REFERENCE_WHITE_NITS,
                is_hdr=False,
                alpha=None,
                icc_profile=icc_profile,
            )

    linear = srgb_eotf(rgb)
    return SourceImage(
        linear=linear,
        primaries=Gamut.SRGB,
        reference_white_nits=SDR_REFERENCE_WHITE_NITS,
        is_hdr=False,
        alpha=None,
        icc_profile=icc_profile,
    )
