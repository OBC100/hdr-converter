"""Gain Map 用 SDR 色调映射（Hable / Chrome / Safari）。"""

from __future__ import annotations

import numpy as np

from .cicp import Gamut
from .hdr_options import SdrToneMap
from .scrgb_colour import (
    gamut_linear_to_gamut_linear,
    scrgb_to_gamut_linear_abs,
    srgb_linear_to_gamut_linear_abs,
)
from .transfer_constants import (
    HABLE_A,
    HABLE_B,
    HABLE_C,
    HABLE_D,
    HABLE_E,
    HABLE_F,
    HABLE_WHITE,
)

# Hable（Uncharted 2 / jxr2uhdr）
_HABLE_A = HABLE_A
_HABLE_B = HABLE_B
_HABLE_C = HABLE_C
_HABLE_D = HABLE_D
_HABLE_E = HABLE_E
_HABLE_F = HABLE_F
_HABLE_WHITE = HABLE_WHITE

# scRGB 参考白 ≈ 80 nits；浏览器 SDR 白 = 203 nits（BT.2408）
_SCRGB_REF_NITS = 80.0
_SDR_WHITE_NITS = 203.0
# 所有 tone map 算子共用的内容峰值：像素 max-RGB 的 99.9% 分位（nits）
_TONEMAP_PEAK_PERCENTILE = 0.999
# 自适应 cap：按「超过阈值的像素占比」在 1000 / 2000 / 4000 间切换
_TONEMAP_BRIGHT_THRESHOLD_NITS = 1000.0
_TONEMAP_CAP_SPIKE_NITS = 1000.0  # 尖峰噪声级（frac < 0.1%）
_TONEMAP_CAP_LOCAL_NITS = 2000.0  # 局部高光（0.1% ≤ frac < 2%）
_TONEMAP_CAP_WIDE_NITS = 4000.0  # 大面积高光（frac ≥ 2%）
_TONEMAP_FRAC_SPIKE = 0.001
_TONEMAP_FRAC_WIDE = 0.02
# 大面积高光时，用超亮区 99% 分位抬峰，避免 p99.9 仍偏保守
_TONEMAP_HIGHLIGHT_PERCENTILE = 0.99
# 无有效峰值时的回退（对齐 Chromium 默认 mastering max）
_FALLBACK_PEAK_NITS = _TONEMAP_CAP_SPIKE_NITS


def _linear_rgb(x: np.ndarray) -> np.ndarray:
    """线性 RGB（允许 scRGB 负值）。"""
    return np.asarray(x, dtype=np.float64)


def _clip_sdr(x: np.ndarray) -> np.ndarray:
    return np.clip(x, 0.0, 1.0).astype(np.float32)


def compute_tonemap_peak_nits(
    linear_abs: np.ndarray,
    *,
    percentile: float = _TONEMAP_PEAK_PERCENTILE,
    cap_nits: float | None = None,
) -> float:
    """
    从目标色域 scRGB 绝对线性（1.0 ≈ 80 nits）估计 tone map 峰值。

    默认：每像素 max(R,G,B) 的 ``percentile`` 分位（99.9%），再按超亮面积自适应 cap：

    - 超过 1000 nits 的像素占比 ``frac < 0.1%`` → cap 1000（防尖峰）
    - ``0.1% ≤ frac < 2%`` → cap 2000（局部高光）
    - ``frac ≥ 2%`` → cap 4000，并用超亮区 99% 分位抬峰（大面积高光）

    传入 ``cap_nits`` 时改为固定上限（跳过自适应）。结果钳位到 [203, cap]。
    """
    max_c = np.max(np.maximum(linear_abs[..., :3], 0.0), axis=-1)
    if max_c.size == 0:
        return _FALLBACK_PEAK_NITS
    nits = max_c.astype(np.float64, copy=False) * _SCRGB_REF_NITS
    peak_nits = float(np.quantile(nits, percentile))
    if not np.isfinite(peak_nits) or peak_nits <= 0.0:
        return _FALLBACK_PEAK_NITS

    if cap_nits is not None:
        cap = max(_SDR_WHITE_NITS, float(cap_nits))
        return max(_SDR_WHITE_NITS, min(peak_nits, cap))

    bright = nits > _TONEMAP_BRIGHT_THRESHOLD_NITS
    frac = float(np.mean(bright))

    if frac < _TONEMAP_FRAC_SPIKE:
        cap = _TONEMAP_CAP_SPIKE_NITS
    elif frac < _TONEMAP_FRAC_WIDE:
        cap = _TONEMAP_CAP_LOCAL_NITS
    else:
        cap = _TONEMAP_CAP_WIDE_NITS

    peak = max(_SDR_WHITE_NITS, min(peak_nits, cap))
    if frac >= _TONEMAP_FRAC_WIDE:
        bright_vals = nits[bright]
        if bright_vals.size > 0:
            p_hi = float(np.quantile(bright_vals, _TONEMAP_HIGHLIGHT_PERCENTILE))
            if np.isfinite(p_hi) and p_hi > 0.0:
                peak = max(peak, min(p_hi, cap))
    return peak


def hable_curve(x: np.ndarray) -> np.ndarray:
    """John Hable / Uncharted 2 filmic 曲线（未归一化）。"""
    x = np.clip(np.asarray(x, dtype=np.float64), 0.0, None)
    return (
        (x * (_HABLE_A * x + _HABLE_C * _HABLE_B) + _HABLE_D * _HABLE_E)
        / (x * (_HABLE_A * x + _HABLE_B) + _HABLE_D * _HABLE_F)
        - _HABLE_E / _HABLE_F
    )


def _hable_filmic_normalized(x: np.ndarray) -> np.ndarray:
    white = hable_curve(np.array(_HABLE_WHITE))
    return np.clip(hable_curve(x) / white, 0.0, 1.0)


def _max_rgb_preserve_hue(x: np.ndarray, mapped_fn) -> np.ndarray:
    max_rgb = np.max(x, axis=-1)
    mapped_max = mapped_fn(max_rgb)
    scale = np.divide(
        mapped_max,
        max_rgb,
        out=np.zeros_like(mapped_max),
        where=max_rgb > 0.0,
    )
    return (x * scale[..., np.newaxis]).astype(np.float32)


def hable_max_rgb_scene(
    x: np.ndarray,
    *,
    peak_nits: float = _FALLBACK_PEAK_NITS,
) -> np.ndarray:
    """
    Uncharted 2 Hable，按 max-RGB 缩放以保色相。

    将 ``peak_nits``（默认应由自适应峰值传入）对齐到 Hable 白点 11.2（scRGB 刻度），
    使内容峰值映射到曲线归一化白点。
    """
    rgb = _linear_rgb(x)
    peak_scrgb = float(peak_nits) / _SCRGB_REF_NITS
    if peak_scrgb > 1e-6:
        rgb = rgb * (_HABLE_WHITE / peak_scrgb)
    return _max_rgb_preserve_hue(rgb, _hable_filmic_normalized)


def _pq_encoded_from_nits(nits: np.ndarray) -> np.ndarray:
    """绝对 nits → PQ 码值 [0,1]（委托 ``pq_oetf``，刻度 1.0=10000 nits）。"""
    from .color_pipeline import pq_oetf

    return pq_oetf(np.asarray(nits, dtype=np.float64) / 10000.0).astype(np.float64)


def _pq_nits_from_encoded(e: np.ndarray) -> np.ndarray:
    """PQ 码值 → 绝对 nits（委托 ``pq_eotf``）。"""
    from .transfer_decode import pq_eotf

    return pq_eotf(e).astype(np.float64) * 10000.0


def chrome_rational_max_rgb_scene(
    x: np.ndarray,
    *,
    peak_nits: float = _FALLBACK_PEAK_NITS,
) -> np.ndarray:
    """
    Chromium ``ColorTransformToneMapInRec2020Linear`` 有理函数（max-RGB）。

    输入：scRGB 绝对线性（1.0 ≈ 80 nits）。
    先转到 SDR-relative（1.0 = 203 nits），再：
      a = maxOut/(maxIn²), b = 1/maxOut
      RGB' = RGB × (1+a·m)/(1+b·m), m=max(R,G,B)
    SDR 显示：maxOut=1；maxIn = peak_nits/203（peak 应为自适应内容峰值）。
    """
    sdr_rel = _linear_rgb(x) * (_SCRGB_REF_NITS / _SDR_WHITE_NITS)
    max_in = float(peak_nits) / _SDR_WHITE_NITS
    max_out = 1.0
    if max_in <= max_out:
        return _clip_sdr(sdr_rel)
    a = max_out / (max_in * max_in)
    b = 1.0 / max_out
    maximum = np.max(sdr_rel, axis=-1)
    scale = np.ones_like(maximum)
    pos = maximum > 0.0
    scale[pos] = (1.0 + a * maximum[pos]) / (1.0 + b * maximum[pos])
    return _clip_sdr(sdr_rel * scale[..., np.newaxis])


def safari_bt2408_max_rgb_scene(
    x: np.ndarray,
    *,
    source_peak_nits: float = _FALLBACK_PEAK_NITS,
    target_peak_nits: float = _SDR_WHITE_NITS,
) -> np.ndarray:
    """
    Safari 风格：ITU-R BT.2408 Annex 5 EETF，max-RGB（option 5）。

    将 ``source_peak_nits``（应为自适应内容峰值）压到 203 nits SDR 白。
    输入：scRGB 绝对线性（1.0 ≈ 80 nits）；输出：SDR 显示线性（1.0 = target_peak）。
    """
    rgb_nits = np.maximum(_linear_rgb(x) * _SCRGB_REF_NITS, 0.0)
    source_hi = float(source_peak_nits)
    target_hi = float(target_peak_nits)

    pq_lo = float(_pq_encoded_from_nits(np.array(0.0)))
    pq_hi = float(_pq_encoded_from_nits(np.array(source_hi)))
    pq_range = max(pq_hi - pq_lo, 1e-12)
    inv_pq_range = 1.0 / pq_range

    max_lum = (
        float(_pq_encoded_from_nits(np.array(target_hi))) - pq_lo
    ) * inv_pq_range
    min_lum = 0.0
    ks = 1.5 * max_lum - 0.5
    inv_one_minus_ks = 1.0 / max(1.0 - ks, 1e-6)

    max_nits = np.max(rgb_nits, axis=-1)
    e1 = np.clip(
        (_pq_encoded_from_nits(max_nits) - pq_lo) * inv_pq_range,
        0.0,
        1.0,
    )

    t = (e1 - ks) * inv_one_minus_ks
    t2 = t * t
    t3 = t2 * t
    p = (
        (2.0 * t3 - 3.0 * t2 + 1.0) * ks
        + (t3 - 2.0 * t2 + t) * (1.0 - ks)
        + (-2.0 * t3 + 3.0 * t2) * max_lum
    )
    e2 = np.where(e1 < ks, e1, p)
    one_m = 1.0 - e2
    e3 = min_lum * (one_m * one_m * one_m * one_m) + e2
    e4 = e3 * pq_range + pq_lo
    new_max_nits = np.clip(_pq_nits_from_encoded(e4), 0.0, target_hi)

    scale = np.divide(
        new_max_nits,
        max_nits,
        out=np.zeros_like(new_max_nits),
        where=max_nits > 1e-6,
    )
    # 极暗像素：直接落到目标峰值归一化后的灰阶，避免除零放大噪声
    dark = max_nits <= 1e-6
    mapped = rgb_nits * scale[..., np.newaxis]
    if np.any(dark):
        cap = (new_max_nits / target_hi)[..., np.newaxis]
        mapped = np.where(dark[..., np.newaxis], cap, mapped)

    return _clip_sdr(mapped / target_hi)


def apply_sdr_tonemap_linear(
    rgb: np.ndarray,
    operator: SdrToneMap,
    *,
    peak_nits: float | None = None,
) -> np.ndarray:
    """
    目标色域线性 tone map → SDR 显示线性（0–1）。

    ``peak_nits`` 缺省时从 ``rgb`` 按自适应峰值估计；所有算子共用该峰值。
    """
    x = _linear_rgb(np.asarray(rgb[..., :3], dtype=np.float64))
    peak = float(peak_nits) if peak_nits is not None else compute_tonemap_peak_nits(x)
    if operator == SdrToneMap.HABLE_MAX:
        return hable_max_rgb_scene(x, peak_nits=peak)
    if operator == SdrToneMap.CHROME:
        return chrome_rational_max_rgb_scene(x, peak_nits=peak)
    if operator == SdrToneMap.SAFARI:
        return safari_bt2408_max_rgb_scene(x, source_peak_nits=peak)
    raise ValueError(f"不支持的 linear tone map: {operator}")


def convert_sdr_srgb_to_gamut_linear(
    sdr_srgb: np.ndarray,
    gamut: Gamut,
) -> np.ndarray:
    """② 色彩空间转换：sRGB 基色 SDR 线性 → 目标色域（经 XYZ）。"""
    return srgb_linear_to_gamut_linear_abs(sdr_srgb, gamut)


def gamut_map_sdr_linear(linear: np.ndarray) -> np.ndarray:
    """③ Gamut mapping：非负 + 按比例缩放到 [0,1]，保色相。"""
    rgb = np.maximum(linear.astype(np.float64), 0.0)
    peak = np.max(rgb, axis=-1, keepdims=True)
    scale = np.where(peak > 1.0, 1.0 / np.maximum(peak, 1e-12), 1.0)
    return (rgb * scale).astype(np.float32)


def apply_srgb_oetf(
    sdr_linear: np.ndarray,
    *,
    base_bits: int,
) -> np.ndarray:
    """④ sRGB OETF → 基础图像素（uint8 或左对齐 uint16）。"""
    import colour

    from .sample_bits import quantize_unit_to_left_aligned_uint16

    gamma = colour.cctf_encoding(np.clip(sdr_linear, 0.0, 1.0), function="sRGB")
    if base_bits <= 8:
        return quantize_unit_to_left_aligned_uint16(gamma, 8, container_bits=8)
    return quantize_unit_to_left_aligned_uint16(gamma, base_bits, container_bits=16)


def build_sdr_base_from_scrgb(
    scrgb: np.ndarray,
    gamut: Gamut,
    tonemap: SdrToneMap,
    *,
    base_bits: int = 8,
    baseline_gamut: Gamut | None = None,
    peak_nits: float | None = None,
) -> np.ndarray:
    """
    SDR 基础图管线：
    ① scRGB（含负值）→ XYZ → 目标色域线性
    ② tone mapping（目标色域内；峰值默认自适应）
    ③ gamut mapping → ④ 可选 baseline 色域 → sRGB OETF

    ``baseline_gamut`` 默认与 ``gamut`` 相同（如 BT.2020 baseline：像素色度属目标基色，
    仅传递函数为 sRGB γ）。仅当需要非色度管理的预览输出时，才显式设为 ``Gamut.SRGB``。

    ①-③ 步与 ``build_sdr_linear_from_scrgb`` 完全一致（供 Gain Map 增益计算复用同一
    路径），此函数只是在其结果上再套一层 ``apply_srgb_oetf``。
    """
    sdr_gamut = build_sdr_linear_from_scrgb(
        scrgb, gamut, tonemap, baseline_gamut=baseline_gamut, peak_nits=peak_nits
    )
    return apply_srgb_oetf(sdr_gamut, base_bits=base_bits)


def build_sdr_linear_from_scrgb(
    scrgb: np.ndarray,
    gamut: Gamut,
    tonemap: SdrToneMap,
    *,
    baseline_gamut: Gamut | None = None,
    peak_nits: float | None = None,
) -> np.ndarray:
    """SDR 基础图显示线性（OETF 之前），与 ``build_sdr_base_from_scrgb`` 同色域语义。"""
    out_gamut = baseline_gamut if baseline_gamut is not None else gamut
    linear_target = scrgb_to_gamut_linear_abs(scrgb, gamut)
    peak = peak_nits if peak_nits is not None else compute_tonemap_peak_nits(linear_target)
    sdr_gamut = apply_sdr_tonemap_linear(linear_target, tonemap, peak_nits=peak)
    sdr_gamut = gamut_map_sdr_linear(sdr_gamut)
    if out_gamut != gamut:
        sdr_gamut = gamut_map_sdr_linear(gamut_linear_to_gamut_linear(sdr_gamut, gamut, out_gamut))
    return sdr_gamut


def sdr_scrgb_linear_to_gamut_linear(
    sdr_scrgb: np.ndarray,
    gamut: Gamut,
) -> np.ndarray:
    """②+③：色彩空间转换 + gamut mapping（测试/诊断用）。"""
    return gamut_map_sdr_linear(convert_sdr_srgb_to_gamut_linear(sdr_scrgb, gamut))


def sdr_linear_to_base_pixels(
    sdr_linear: np.ndarray,
    *,
    base_bits: int,
) -> np.ndarray:
    """④ sRGB OETF（`apply_srgb_oetf` 别名）。"""
    return apply_srgb_oetf(sdr_linear, base_bits=base_bits)
