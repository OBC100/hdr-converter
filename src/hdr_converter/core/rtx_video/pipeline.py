"""对 scRGB 缓冲应用 RTX Video 增强。"""

from __future__ import annotations

import numpy as np

from ..canonical import SCRGB_REFERENCE_WHITE_NITS
from ..hdr_options import (
    RtxEnhanceMode,
    RtxVsrQuality,
    RTX_VSR_QUALITY_ORDER,
    normalize_rtx_vsr_scale,
    rtx_uses_thdr,
    rtx_uses_vsr,
)
from .availability import probe_rtx_video
from .bridge import RtxBridgeError, process_rgba_fp32

_MODE_CODE = {
    RtxEnhanceMode.THDR: 1,
    RtxEnhanceMode.VSR: 2,
    RtxEnhanceMode.VSR_THDR: 3,
}

_QUALITY_CODE = {q: i for i, q in enumerate(RTX_VSR_QUALITY_ORDER)}


def _scrgb_to_sdr_display01(scrgb: np.ndarray) -> np.ndarray:
    """scRGB → 近似 SDR 显示域 [0,1]（供 TrueHDR / VSR 输入）。"""
    rgb = np.asarray(scrgb[..., :3], dtype=np.float32)
    # 将约 100 nits 图形白压到 1.0：100/80 = 1.25 scRGB → 归一
    scale = 100.0 / SCRGB_REFERENCE_WHITE_NITS
    disp = np.clip(rgb / scale, 0.0, 1.0)
    out = np.empty(scrgb.shape, dtype=np.float32)
    out[..., :3] = disp
    if scrgb.shape[-1] >= 4:
        out[..., 3] = scrgb[..., 3]
    else:
        out = np.concatenate([out[..., :3], np.ones((*out.shape[:2], 1), np.float32)], axis=-1)
    return out


def apply_rtx_enhance(
    scrgb_rgba: np.ndarray,
    mode: RtxEnhanceMode,
    *,
    contrast: int = 125,
    saturation: int = 100,
    middle_gray: int = 25,
    max_luminance: int = 1000,
    vsr_quality: RtxVsrQuality = RtxVsrQuality.HIGH,
    vsr_scale: int = 2,
) -> np.ndarray:
    """输入/输出 float32 HxWx4 scRGB（1.0≈80 nits）。

    TrueHDR 开启时输出为扩展 scRGB（可 >1）；仅 VSR 时输出仍近似 SDR scRGB。
    """
    if mode == RtxEnhanceMode.OFF:
        return scrgb_rgba

    probe = probe_rtx_video()
    if not probe.available:
        raise RtxBridgeError(probe.reason)

    vsr_scale = normalize_rtx_vsr_scale(vsr_scale) if rtx_uses_vsr(mode) else 1
    sdr = _scrgb_to_sdr_display01(scrgb_rgba)
    out = process_rgba_fp32(
        sdr,
        mode=_MODE_CODE[mode],
        vsr_quality=_QUALITY_CODE.get(vsr_quality, 3),
        contrast=int(contrast),
        saturation=int(saturation),
        middle_gray=int(middle_gray),
        max_luminance=int(max_luminance),
        vsr_scale=vsr_scale,
    )
    # TrueHDR：桥接已输出 scRGB。仅 VSR：输出仍是 [0,1] 显示域 → 转回 scRGB
    if rtx_uses_thdr(mode):
        return out.astype(np.float32, copy=False)

    scale = 100.0 / SCRGB_REFERENCE_WHITE_NITS
    rgb = np.clip(out[..., :3], 0.0, 1.0) * scale
    result = np.empty_like(out, dtype=np.float32)
    result[..., :3] = rgb
    result[..., 3] = out[..., 3] if out.shape[-1] >= 4 else 1.0
    return result
