"""传输曲线解码：编码信号 → 显示线性（1.0 = 10000 nits，PQ/HLG/Linear）。"""

from __future__ import annotations

import numpy as np

from .canonical import CANONICAL_PEAK_NITS
from .cicp import TransferCurve
from .transfer_constants import (
    HLG_REF_DISPLAY_NITS,
    PQ_C1,
    PQ_C2,
    PQ_C3,
    PQ_M1,
    PQ_M2,
)

# 与 color_pipeline.pq_oetf / hlg_encode_bt2100 保持同一套常量
_HLG_REF_DISPLAY_NITS = HLG_REF_DISPLAY_NITS
_PQ_M1 = PQ_M1
_PQ_M2 = PQ_M2
_PQ_C1 = PQ_C1
_PQ_C2 = PQ_C2
_PQ_C3 = PQ_C3


def pq_eotf(signal: np.ndarray) -> np.ndarray:
    """ST 2084 PQ EOTF；输出显示线性 1.0 = 10000 nits（与 ``pq_oetf`` 互逆）。"""
    n = np.clip(signal, 0.0, None).astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        n_p = np.power(n, 1.0 / _PQ_M2)
        num = np.maximum(n_p - _PQ_C1, 0.0)
        den = _PQ_C2 - _PQ_C3 * n_p
        y = np.power(np.where(den > 0, num / den, 0.0), 1.0 / _PQ_M1)
    return np.clip(y, 0.0, None).astype(np.float32)


def hlg_decode_bt2100(
    signal: np.ndarray,
    *,
    L_W: float = _HLG_REF_DISPLAY_NITS,
) -> np.ndarray:
    """HLG 信号 → 显示线性（1.0 = 10000 nits）。

    与 ``hlg_encode_bt2100`` 对称：不做 BT.2100 OOTF，仅 OETF⁻¹ 后按 ``L_W`` 还原。
    """
    from colour.models.rgb.transfer_functions.itur_bt_2100 import oetf_inverse_BT2100_HLG

    enc = np.clip(signal, 0.0, 1.0).astype(np.float64)
    with np.errstate(invalid="ignore"):
        scene = oetf_inverse_BT2100_HLG(enc)
    display = scene * (float(L_W) / CANONICAL_PEAK_NITS)
    return np.clip(display, 0.0, None).astype(np.float32)


def srgb_eotf(signal: np.ndarray) -> np.ndarray:
    """sRGB 分段 EOTF → 显示线性（相对，1.0 = 参考白）。"""
    x = np.clip(signal, 0.0, None).astype(np.float64)
    return np.where(
        x <= 0.04045,
        x / 12.92,
        np.power((x + 0.055) / 1.055, 2.4),
    ).astype(np.float32)


def encoded_to_display_linear(
    signal: np.ndarray,
    curve: TransferCurve,
) -> np.ndarray:
    """按曲线把编码信号解码为显示线性。

    - PQ / HLG / Linear：1.0 = 10000 nits
    - sRGB：1.0 = 参考白（由调用方设 ``reference_white_nits``）
    """
    rgb = np.asarray(signal[..., :3], dtype=np.float32)
    if curve == TransferCurve.PQ:
        return pq_eotf(rgb)
    if curve == TransferCurve.HLG:
        return hlg_decode_bt2100(rgb)
    if curve == TransferCurve.LINEAR:
        return np.clip(rgb, 0.0, None).astype(np.float32)
    if curve == TransferCurve.SRGB:
        return srgb_eotf(rgb)
    raise ValueError(f"不支持的传输曲线: {curve}")


def encoded_to_linear_via_colourspace(
    signal: np.ndarray,
    primaries,
) -> np.ndarray:
    """用 colourspace 自带 TRC 解码（AdobeRGB γ2.2、ProPhoto、DCI-P3 γ2.6 等）。"""
    from .named_colourspaces import resolve_colour_rgb_colourspace

    cs = resolve_colour_rgb_colourspace(primaries)
    x = np.clip(np.asarray(signal[..., :3], dtype=np.float64), 0.0, None)
    if cs.cctf_decoding is None:
        return x.astype(np.float32)
    return np.clip(cs.cctf_decoding(x), 0.0, None).astype(np.float32)
