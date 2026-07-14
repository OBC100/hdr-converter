"""预览帧：canonical BT.2020 线性下采样后生成 SDR/HDR 显示缓冲。

入参语义（Stage G）：``canonical`` 为 BT.2020 显示线性，绝对刻度 1.0 = 10000 nits。
呈现层仍输出 Windows scRGB（D3D / ImageLabel 硬限制）。
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from ..core.canonical import (
    CANONICAL_PEAK_NITS,
    SCRGB_REFERENCE_WHITE_NITS,
    to_canonical_bt2020_linear,
)
from ..core.cicp import Gamut
from ..core.color_pipeline import compute_content_light
from ..core.named_colourspaces import PrimariesLike, describe_primaries
from ..core.scrgb_colour import gamut_linear_to_gamut_linear
from ..core.sdr_tonemap import hable_curve

# 预览 L2 短边上限。速度优先：最近邻整数步进下采样（非双线性）。
_PREVIEW_SHORT_EDGE = 1080


class PreviewMetadata(NamedTuple):
    width: int
    height: int
    max_cll: int
    max_fall: int
    color_space: str = ""
    """输入图像的原始色彩空间显示名（非导出目标色域），见 ``describe_primaries``。"""


def resize_rgba_bilinear(arr: np.ndarray, nw: int, nh: int) -> np.ndarray:
    """双线性缩放 RGBA / RGB 数组到 (nh, nw)，全通道向量化。"""
    h, w = arr.shape[:2]
    if nw == w and nh == h:
        return arr
    if nw < 1 or nh < 1:
        return arr

    y = np.linspace(0, h - 1, nh, dtype=np.float32)
    x = np.linspace(0, w - 1, nw, dtype=np.float32)
    y0 = np.floor(y).astype(np.int32)
    x0 = np.floor(x).astype(np.int32)
    y1 = np.minimum(y0 + 1, h - 1)
    x1 = np.minimum(x0 + 1, w - 1)
    wy = y - y0
    wx = x - x0

    yi = y0[:, None]
    xi = x0[None, :]
    y1i = y1[:, None]
    x1i = x1[None, :]

    src = arr.astype(np.float32, copy=False)
    c00 = src[yi, xi]
    c01 = src[yi, x1i]
    c10 = src[y1i, xi]
    c11 = src[y1i, x1i]

    wx_b = wx[None, :, None]
    wy_b = wy[:, None, None]
    top = c00 * (1.0 - wx_b) + c01 * wx_b
    bottom = c10 * (1.0 - wx_b) + c11 * wx_b
    blended = top * (1.0 - wy_b) + bottom * wy_b
    if arr.dtype == np.float32:
        return blended
    if arr.dtype == np.uint8:
        return np.clip(blended, 0, 255).astype(np.uint8)
    return blended.astype(arr.dtype, copy=False)


def scale_preview_rgba(
    arr: np.ndarray,
    short_edge: int = _PREVIEW_SHORT_EDGE,
) -> np.ndarray:
    """预览下采样：最短边压到 ≤ short_edge，最近邻整数步进（速度优先）。"""
    h, w = arr.shape[:2]
    short = min(h, w)
    target = max(1, int(short_edge))
    if short <= target:
        return arr
    step = max(1, short // target)
    return np.ascontiguousarray(arr[::step, ::step])


def fit_size_preserve_aspect(
    img_w: int,
    img_h: int,
    avail_w: int,
    avail_h: int,
    *,
    max_scale: float = 1.0,
) -> tuple[int, int]:
    """在可用区域内等比适配显示尺寸；默认不放大（max_scale=1）。"""
    if img_w < 1 or img_h < 1 or avail_w < 1 or avail_h < 1:
        return max(1, avail_w), max(1, avail_h)
    scale = min(avail_w / img_w, avail_h / img_h, max_scale)
    return max(1, int(round(img_w * scale))), max(1, int(round(img_h * scale)))


_PQ_PEAK_NITS = float(CANONICAL_PEAK_NITS)
_SCRGB_WHITE_NITS = float(SCRGB_REFERENCE_WHITE_NITS)
_SCRGB_LINEAR_SCALE = _PQ_PEAK_NITS / _SCRGB_WHITE_NITS
_SDR_PREVIEW_TARGET_NITS = 100.0
_SDR_PREVIEW_TARGET_SCRGB = _SDR_PREVIEW_TARGET_NITS / _SCRGB_WHITE_NITS


def scrgb_to_canonical_preview(scrgb: np.ndarray) -> np.ndarray:
    """L0 scRGB（含桥接后缓冲）→ canonical BT.2020，供仍持有 scRGB 的调用方。"""
    return to_canonical_bt2020_linear(scrgb, Gamut.SRGB, SCRGB_REFERENCE_WHITE_NITS)


def canonical_to_target_linear(
    canonical: np.ndarray,
    gamut: Gamut,
) -> np.ndarray:
    """canonical BT.2020 → 目标色域显示线性（预览不额外裁到 1.0）。"""
    rgb = np.clip(np.asarray(canonical[..., :3], dtype=np.float64), 0.0, None)
    if gamut != Gamut.BT2020:
        rgb = gamut_linear_to_gamut_linear(rgb, Gamut.BT2020, gamut).astype(np.float64)
    return np.clip(rgb, 0.0, None).astype(np.float32)


def linear_to_preview_scrgb(
    linear: np.ndarray, *, from_gamut: Gamut
) -> np.ndarray:
    """目标色域线性 (1.0 = 10000 nits) → Windows scRGB sRGB 线性 (1.0 ≈ 80 nits)。

    与 ``to_canonical_bt2020_linear`` 共用 ``gamut_linear_to_gamut_linear`` 矩阵，
    保证 JXR BT.2020 预览与旧 scRGB 透传数值接近。
    """
    rgb = np.clip(linear[..., :3], 0.0, None).astype(np.float64)
    if from_gamut != Gamut.SRGB:
        rgb = gamut_linear_to_gamut_linear(rgb, from_gamut, Gamut.SRGB).astype(np.float64)
    scrgb = rgb * _SCRGB_LINEAR_SCALE
    return np.clip(scrgb, 0.0, None).astype(np.float32)


def _hable_tone_map_scrgb(scrgb: np.ndarray, white_scrgb: float) -> np.ndarray:
    """Hable 色调映射，white_scrgb 对应输出 1.0。"""
    white = float(hable_curve(np.array(white_scrgb, dtype=np.float64)))
    if white <= 0.0:
        return np.clip(scrgb, 0.0, 1.0).astype(np.float32)
    mapped = hable_curve(scrgb) / white
    return np.clip(mapped, 0.0, 1.0).astype(np.float32)


def linear_to_sdr_scrgb(
    linear: np.ndarray,
    *,
    from_gamut: Gamut,
    max_fall_nits: int | float | None = None,
) -> np.ndarray:
    """目标色域线性 → scRGB 曝光（MaxFALL → 100 nits）+ Hable。"""
    scrgb = linear_to_preview_scrgb(linear, from_gamut=from_gamut)
    if max_fall_nits is None or max_fall_nits <= 0:
        max_comp = np.max(linear[..., :3], axis=-1)
        fall_nits = float(np.mean(max_comp)) * _PQ_PEAK_NITS
    else:
        fall_nits = float(max_fall_nits)
    fall_scrgb = fall_nits / _SCRGB_WHITE_NITS
    exposure = _SDR_PREVIEW_TARGET_SCRGB / max(fall_scrgb, 1e-6)
    scaled = scrgb * exposure
    return _hable_tone_map_scrgb(scaled, _SDR_PREVIEW_TARGET_SCRGB)


def build_hdr_preview_scrgb(
    canonical: np.ndarray,
    *,
    gamut: Gamut,
    linear: np.ndarray | None = None,
) -> np.ndarray:
    """D3D HDR 预览：canonical → 目标色域线性 → scRGB（统一路径，无 JXR 透传捷径）。"""
    target_linear = linear
    if target_linear is None:
        target_linear = canonical_to_target_linear(canonical, gamut)
    return linear_to_preview_scrgb(target_linear, from_gamut=gamut)


def build_sdr_preview_scrgb(
    canonical: np.ndarray,
    *,
    gamut: Gamut,
    linear: np.ndarray | None = None,
    max_fall_nits: int | None = None,
) -> np.ndarray:
    """SDR 预览 scRGB：Hable 色调映射，MaxFALL 对齐 100 nits。"""
    target_linear = linear
    if target_linear is None:
        target_linear = canonical_to_target_linear(canonical, gamut)
    return linear_to_sdr_scrgb(
        target_linear,
        from_gamut=gamut,
        max_fall_nits=max_fall_nits,
    )


def scrgb_to_display_uint8(scrgb: np.ndarray) -> np.ndarray:
    """scRGB 0–1 → 8-bit sRGB（供无 D3D 回退）。"""
    import colour

    srgb = colour.cctf_encoding(np.clip(scrgb[..., :3], 0.0, 1.0), function="sRGB")
    return (np.clip(srgb, 0.0, 1.0) * 255.0).astype(np.uint8)


def build_preview_frames(
    canonical: np.ndarray,
    *,
    gamut: Gamut,
    need_sdr: bool = True,
    need_hdr: bool = True,
    source_primaries: PrimariesLike | None = None,
) -> tuple[np.ndarray | None, np.ndarray | None, PreviewMetadata]:
    """从 canonical BT.2020 生成预览帧。

    返回 (sdr_scrgb | None, hdr_scrgb | None, metadata)。
    ``need_sdr=False`` 时跳过 Hable。``source_primaries``：输入图像的原生色域
    （信息栏展示用，与 ``gamut``——预览渲染的目标色域——无关）。
    """
    orig_h, orig_w = canonical.shape[:2]
    small = scale_preview_rgba(canonical)
    linear_full = canonical_to_target_linear(small, gamut)

    cll = compute_content_light(linear_full)
    color_space = describe_primaries(source_primaries) if source_primaries is not None else ""
    metadata = PreviewMetadata(orig_w, orig_h, cll.max_cll, cll.max_fall, color_space)

    sdr_scrgb: np.ndarray | None = None
    if need_sdr:
        sdr_scrgb = build_sdr_preview_scrgb(
            small,
            gamut=gamut,
            linear=linear_full,
            max_fall_nits=cll.max_fall,
        )

    hdr: np.ndarray | None = None
    if need_hdr:
        hdr = build_hdr_preview_scrgb(
            small,
            gamut=gamut,
            linear=linear_full,
        )

    return sdr_scrgb, hdr, metadata


def build_preview_frames_from_scrgb(
    scrgb: np.ndarray,
    *,
    gamut: Gamut,
    need_sdr: bool = True,
    need_hdr: bool = True,
) -> tuple[np.ndarray | None, np.ndarray | None, PreviewMetadata]:
    """兼容入口：L0 scRGB → canonical → 预览。"""
    return build_preview_frames(
        scrgb_to_canonical_preview(scrgb),
        gamut=gamut,
        need_sdr=need_sdr,
        need_hdr=need_hdr,
    )
