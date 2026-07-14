"""共享：样本解量化、NCLX/colr 解析、SourceImage 组装。"""

from __future__ import annotations

import struct

import numpy as np

from ..canonical import CANONICAL_PEAK_NITS, SDR_REFERENCE_WHITE_NITS
from ..cicp import CICP, Gamut, TransferCurve, cicp_to_gamut_curve, is_hdr_curve
from ..color_metadata import jpegxl_primaries
from ..named_colourspaces import PrimariesLike
from ..source_image import SourceImage


def samples_to_unit_signal(
    arr: np.ndarray,
    *,
    bit_depth_hint: int | None = None,
) -> np.ndarray:
    """整数样本 → [0,1] 编码信号。

    兼容右对齐（AVIF/JXL imagecodecs）与左对齐（PNG / pillow-heif RGB;16）。
    """
    if arr.dtype == np.uint8 or (arr.dtype.kind == "u" and arr.dtype.itemsize == 1):
        return (arr.astype(np.float64) / 255.0).astype(np.float32)

    u = np.asarray(arr, dtype=np.uint32)
    maxv = int(u.max()) if u.size else 0

    def _right(bits: int) -> np.ndarray:
        return (u / float((1 << bits) - 1)).astype(np.float32)

    def _left(bits: int) -> np.ndarray:
        shift = 16 - bits
        return ((u >> shift) / float((1 << bits) - 1)).astype(np.float32)

    if bit_depth_hint is not None and 1 <= bit_depth_hint <= 16:
        max_code = (1 << bit_depth_hint) - 1
        if maxv <= max_code:
            return _right(bit_depth_hint)
        if bit_depth_hint < 16:
            return _left(bit_depth_hint)
        return (u / 65535.0).astype(np.float32)

    for bits in (10, 12, 14):
        if maxv <= (1 << bits) - 1:
            return _right(bits)
    for bits in (10, 12, 14):
        shift = 16 - bits
        mask = (1 << shift) - 1
        if maxv > 0 and int(np.max(u & mask)) == 0:
            return _left(bits)
    # 未满幅的 16-bit HDR（如 Linear 峰值 ≪ 1.0）不得误判为 14-bit
    return (u / 65535.0).astype(np.float32)


def parse_nclx_colr_payload(payload: bytes) -> CICP | None:
    """解析 ``colr`` box 内 ``nclx`` 载荷（不含 box header）。"""
    if len(payload) < 11 or payload[:4] != b"nclx":
        return None
    cp, tc, mc = struct.unpack(">HHH", payload[4:10])
    full = bool(payload[10] & 0x80)
    return CICP(cp, tc, mc, full_range=full)


def parse_nclx_from_colr_box(colr_box: bytes) -> CICP | None:
    """完整 ``colr`` box（含 size/type）→ CICP。"""
    if len(colr_box) < 16 or colr_box[4:8] != b"colr":
        return None
    return parse_nclx_colr_payload(colr_box[8:])


def cicp_from_nclx_dict(nclx: dict) -> tuple[Gamut, TransferCurve]:
    """pillow-heif ``nclx_profile`` dict → (Gamut, TransferCurve)。"""
    return cicp_to_gamut_curve(
        int(nclx["color_primaries"]),
        int(nclx["transfer_characteristics"]),
        int(nclx.get("matrix_coefficients", 0)),
    )


def gamut_from_jpegxl_primaries(primaries: int) -> Gamut:
    """libjxl / imagecodecs primaries 枚举 → Gamut（P3 为 11）。"""
    if primaries == jpegxl_primaries(Gamut.SRGB):
        return Gamut.SRGB
    if primaries == jpegxl_primaries(Gamut.P3) or primaries == 12:
        return Gamut.P3
    if primaries == jpegxl_primaries(Gamut.BT2020):
        return Gamut.BT2020
    raise ValueError(f"未知 JXL primaries: {primaries}")


def reference_for_transfer(curve: TransferCurve) -> tuple[bool, float]:
    """传输曲线 → ``(is_hdr, reference_white_nits)``。"""
    if curve == TransferCurve.SRGB or not is_hdr_curve(curve):
        return False, float(SDR_REFERENCE_WHITE_NITS)
    return True, float(CANONICAL_PEAK_NITS)


def source_image_from_display_linear(
    linear: np.ndarray,
    primaries: PrimariesLike,
    curve: TransferCurve,
    *,
    alpha: np.ndarray | None = None,
) -> SourceImage:
    """显示线性缓冲 + 传输曲线 → SourceImage。"""
    is_hdr, ref = reference_for_transfer(curve)
    return SourceImage(
        linear=linear,
        primaries=primaries,
        reference_white_nits=ref,
        is_hdr=is_hdr,
        alpha=alpha,
    )
