"""Gain Map 统一核心：格式无关的中间缓冲与准备逻辑。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cicp import ContentLightLevel, Gamut, TransferCurve
from .color_pipeline import (
    _PQ_PEAK_NITS,
    _SCRGB_TO_HDR_LINEAR_SCALE,
    compute_content_light,
    scrgb_to_gamut_linear,
)
from .encoders.base import EncodeOptions
from .gainmap_math import GainmapMetadata, compute_gainmap_with_peak
from .hdr_options import (
    multichannel_gainmap,
    resolve_gainmap_tonemap,
)
from .parallel import run_parallel_pair
from .scrgb_colour import scrgb_to_gamut_linear_abs
from .sdr_tonemap import (
    apply_sdr_tonemap_linear,
    compute_tonemap_peak_nits,
    gamut_map_sdr_linear,
    sdr_linear_to_base_pixels,
)

_UHDR_NITS_PER_UNIT = 203.0


@dataclass
class GainMapBuffers:
    """Gain Map L1b 中间表示（JPG / HEIF / AVIF / JXL 共用）。"""

    hdr_linear: np.ndarray
    sdr_linear: np.ndarray
    sdr_pixels: np.ndarray
    gain: np.ndarray
    metadata: GainmapMetadata
    content_light: ContentLightLevel
    multichannel: bool


def prepare_gainmap_linear(
    scrgb: np.ndarray,
    gamut: Gamut,
    curve: TransferCurve,
    tonemap,
) -> tuple[np.ndarray, np.ndarray, ContentLightLevel]:
    """
    单次（或共享）色域转换 + 单次 tone map。

    scrgb → gamut linear abs **一次**
          → 自适应 tone map 峰值（99.9% + 超亮面积 cap）
          → hdr_linear（×1/125）与 SDR tone map 共用
    """
    linear_abs = scrgb_to_gamut_linear_abs(scrgb, gamut)
    peak_nits = compute_tonemap_peak_nits(linear_abs)
    hdr_linear = np.clip(
        linear_abs.astype(np.float64) * _SCRGB_TO_HDR_LINEAR_SCALE,
        0.0,
        None,
    ).astype(np.float32)

    def _build_sdr_linear() -> np.ndarray:
        return gamut_map_sdr_linear(
            apply_sdr_tonemap_linear(linear_abs, tonemap, peak_nits=peak_nits)
        )

    cll, sdr_linear = run_parallel_pair(
        lambda: compute_content_light(hdr_linear),
        _build_sdr_linear,
    )
    return hdr_linear, sdr_linear, cll


def prepare_gainmap_buffers(
    scrgb: np.ndarray,
    options: EncodeOptions,
) -> GainMapBuffers:
    """从 scRGB 生成完整 GainMapBuffers（tone map + gain + 基础图像素）。"""
    tonemap = resolve_gainmap_tonemap(options.sdr_tonemap, options.curve)
    multichannel = multichannel_gainmap(options.hdr_delivery)

    # JPG：基础图锁定 8-bit；HEIF/AVIF/JXL 用 base_bits；增益图固定 8-bit
    base_bits = 8 if options.output_format.value == "jpg" else options.base_bits

    hdr_linear, sdr_linear, cll = prepare_gainmap_linear(
        scrgb,
        options.gamut,
        options.curve,
        tonemap,
    )
    peak = float(cll.max_cll)

    def _compute_gain() -> tuple[np.ndarray, GainmapMetadata]:
        gain, meta = compute_gainmap_with_peak(
            hdr_linear,
            sdr_linear,
            options.gamut,
            options.curve,
            peak,
            scale=options.gainmap_scale,
            multichannel=multichannel,
        )
        return gain, meta

    (gain, metadata), sdr_pixels = run_parallel_pair(
        _compute_gain,
        lambda: sdr_linear_to_base_pixels(sdr_linear, base_bits=base_bits),
    )

    return GainMapBuffers(
        hdr_linear=hdr_linear,
        sdr_linear=sdr_linear,
        sdr_pixels=sdr_pixels,
        gain=gain,
        metadata=metadata,
        content_light=cll,
        multichannel=multichannel,
    )


def scrgb_to_hdr_linear(
    scrgb: np.ndarray,
    gamut: Gamut,
) -> tuple[np.ndarray, ContentLightLevel]:
    """scRGB → 目标色域 HDR 线性 + MaxCLL（脚本/诊断用）。"""
    hdr_linear = scrgb_to_gamut_linear(scrgb, gamut)
    cll = compute_content_light(hdr_linear)
    return hdr_linear, cll


def linear_to_ultrahdr_buffer(linear_rgb: np.ndarray) -> np.ndarray:
    """项目 linear（1.0=10000 nits）→ libultrahdr 线性缓冲刻度。"""
    scale = _PQ_PEAK_NITS / _UHDR_NITS_PER_UNIT
    return np.clip(linear_rgb.astype(np.float64), 0.0, None) * scale


def pack_hdr_rgba16f(hdr_linear: np.ndarray) -> np.ndarray:
    """HDR 意图 float16 RGBA（libultrahdr transfer=LINEAR）。"""
    rgb = linear_to_ultrahdr_buffer(hdr_linear).astype(np.float32)
    alpha = np.ones((*rgb.shape[:2], 1), dtype=np.float32)
    return np.concatenate([rgb, alpha], axis=-1).astype(np.float16)


def gain_pixels_for_jpeg(gain: np.ndarray, *, multichannel: bool) -> np.ndarray:
    """mono 增益图扩展为 JPEG 编码用的 RGB 数组。"""
    if multichannel:
        return gain
    return np.dstack([gain, gain, gain])
