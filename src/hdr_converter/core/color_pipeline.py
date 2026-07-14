"""色彩空间与传输曲线转换管线。

主线：scRGB × (1/125) → colour.RGB_to_RGB(sRGB → 目标色域) → OETF。
历史废弃方案见 docs/PROJECT.md §7。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cicp import ContentLightLevel, Gamut, TransferCurve
from .scrgb_colour import GAMUT_COLOUR_NAMES, scrgb_to_gamut_linear_abs
from .transfer_constants import (
    HLG_REF_DISPLAY_NITS,
    PQ_C1,
    PQ_C2,
    PQ_C3,
    PQ_M1,
    PQ_M2,
    PQ_PEAK_NITS,
)

_PQ_PEAK_NITS = PQ_PEAK_NITS
_MAXCLL_PERCENTILE = 0.9999
_SCRGB_TO_HDR_LINEAR_SCALE = 1.0 / 125.0
QUANTIZE_BITS_CHOICES = (8, 10, 12, 14, 16)
_DEFAULT_QUANTIZE_BITS: dict[TransferCurve, int] = {
    TransferCurve.PQ: 10,
    TransferCurve.HLG: 10,
    TransferCurve.LINEAR: 16,
}

_HLG_REF_DISPLAY_NITS = HLG_REF_DISPLAY_NITS
_PQ_M1 = PQ_M1
_PQ_M2 = PQ_M2
_PQ_C1 = PQ_C1
_PQ_C2 = PQ_C2
_PQ_C3 = PQ_C3


@dataclass
class PipelineResult:
    """色彩管线输出。"""

    rgb: np.ndarray
    content_light: ContentLightLevel | None = None
    effective_bits: int = 16
    is_uint16: bool = False


def pq_oetf(linear_normalized: np.ndarray) -> np.ndarray:
    """SMPTE ST 2084 PQ OETF；输入线性 0–1（1.0 = 10000 nits）。"""
    y = np.clip(linear_normalized, 0.0, None).astype(np.float64)
    pow1 = np.power(y, _PQ_M1)
    return np.power((_PQ_C1 + _PQ_C2 * pow1) / (1.0 + _PQ_C3 * pow1), _PQ_M2).astype(
        np.float32
    )


def hlg_encode_bt2100(
    display_linear_normalized: np.ndarray,
    *,
    L_W: float = _HLG_REF_DISPLAY_NITS,
    gamut: Gamut = Gamut.BT2020,
) -> np.ndarray:
    """显示线性（1.0=10000 nits）→ 按 ``L_W`` 归一化 → HLG OETF。

    静帧链路（PNG ICC / AVIF·HEIF NCLX）对 transfer=18 普遍只做 ARIB STD-B67
    逆变换，**不**再施加 BT.2100 OOTF。若编码侧先做 OOTF⁻¹，中灰会系统性偏亮
   （203 nit 参考白约亮到 265 nit）。因此这里用显示光归一化直连 OETF，与
    libjxl ICC（OOTF 恒等）及常见阅读器一致。

    ``gamut`` 保留兼容调用方；本路径不依赖 luma 权重。
    """
    _ = gamut
    from colour.models.rgb.transfer_functions.itur_bt_2100 import oetf_BT2100_HLG

    rgb = np.asarray(display_linear_normalized, dtype=np.float64)
    # 1.0 显示线性 = 10000 nits；HLG 场景光 1.0 ↔ 参考峰 L_W
    scene = np.clip(rgb, 0.0, None) * (_PQ_PEAK_NITS / float(L_W))
    with np.errstate(invalid="ignore"):
        encoded = oetf_BT2100_HLG(scene)
    return np.clip(encoded, 0.0, 1.0).astype(np.float32)


def scrgb_to_gamut_linear(scrgb: np.ndarray, gamut: Gamut) -> np.ndarray:
    """scRGB → 目标色域 HDR 线性（÷125；保留负值至 XYZ 后再等色相去饱和）。

    源色域覆盖不到的色度在目标色域坐标下会出现负通道；逐通道裁零会扭曲色相
    （对高饱和暖色在窄色域下常见地表现为偏红），因此改为三通道统一减去
    最负通道的值，把颜色沿去饱和方向拉回非负——色相不变，仅损失饱和度。
    """
    linear_abs = scrgb_to_gamut_linear_abs(scrgb, gamut).astype(np.float64)
    min_c = np.min(linear_abs, axis=-1, keepdims=True)
    shift = np.minimum(min_c, 0.0)
    desaturated = linear_abs - shift
    hdr = desaturated * _SCRGB_TO_HDR_LINEAR_SCALE
    return np.clip(hdr, 0.0, None).astype(np.float32)


def bt2020_linear_to_scrgb(bt2020_linear: np.ndarray) -> np.ndarray:
    """BT.2020 显示线性（1.0=10000 nits）→ scRGB 线性（与 scrgb_to_gamut_linear 互逆）。"""
    import colour

    srgb = colour.RGB_to_RGB(
        np.clip(bt2020_linear.astype(np.float64), 0.0, None),
        GAMUT_COLOUR_NAMES[Gamut.BT2020],
        "sRGB",
        chromatic_adaptation_transform=None,
    )
    scale = 1.0 / _SCRGB_TO_HDR_LINEAR_SCALE
    return np.clip(srgb * scale, 0.0, None).astype(np.float32)


def compute_content_light(
    linear_rgb: np.ndarray,
    *,
    percentile: float = _MAXCLL_PERCENTILE,
) -> ContentLightLevel:
    """从像素计算 MaxCLL / MaxFALL。"""
    max_comp = np.max(linear_rgb, axis=-1)
    nits_per_pixel = max_comp * _PQ_PEAK_NITS
    max_fall_nits = int(round(float(np.mean(nits_per_pixel))))
    max_cll_nits = int(round(float(np.quantile(nits_per_pixel, percentile))))
    return ContentLightLevel(max_cll=max_cll_nits, max_fall=max_fall_nits)


def resolve_quantize_bits(
    curve: TransferCurve,
    quantize_bits: int | None = None,
) -> int:
    bits = quantize_bits if quantize_bits is not None else _DEFAULT_QUANTIZE_BITS.get(curve, 16)
    if bits not in QUANTIZE_BITS_CHOICES:
        allowed = ", ".join(str(b) for b in QUANTIZE_BITS_CHOICES)
        raise ValueError(f"quantize_bits 须为 {allowed}，收到 {bits}")
    return bits


def quantize_hdr_to_uint16(
    signal: np.ndarray,
    *,
    target_bits: int = 10,
    container_bits: int = 16,
) -> np.ndarray:
    from .sample_bits import quantize_unit_to_left_aligned_uint16

    bits = resolve_quantize_bits(TransferCurve.PQ, target_bits)
    return quantize_unit_to_left_aligned_uint16(
        signal, bits, container_bits=container_bits
    )


def quantize_to_uint16(
    signal: np.ndarray,
    *,
    quantize_bits: int,
    container_bits: int = 16,
) -> np.ndarray:
    return quantize_hdr_to_uint16(
        signal, target_bits=quantize_bits, container_bits=container_bits
    )


def convert_scrgb_to_hlg(
    scrgb: np.ndarray,
    gamut: Gamut,
    *,
    quantize_bits: int | None = None,
) -> PipelineResult:
    return _convert_scrgb_encoded(
        scrgb,
        gamut,
        TransferCurve.HLG,
        encode_fn=lambda linear: hlg_encode_bt2100(linear, gamut=gamut),
        with_cll=False,
        quantize_bits=quantize_bits,
    )


def convert_scrgb_to_linear(
    scrgb: np.ndarray,
    gamut: Gamut,
    *,
    quantize_bits: int | None = None,
) -> PipelineResult:
    return _convert_scrgb_encoded(
        scrgb,
        gamut,
        TransferCurve.LINEAR,
        encode_fn=None,
        with_cll=True,
        quantize_bits=quantize_bits,
    )


def convert_scrgb_to_pq(
    scrgb: np.ndarray,
    gamut: Gamut,
    *,
    quantize_bits: int | None = None,
) -> PipelineResult:
    return _convert_scrgb_encoded(
        scrgb,
        gamut,
        TransferCurve.PQ,
        encode_fn=pq_oetf,
        with_cll=True,
        quantize_bits=quantize_bits,
    )


def _convert_scrgb_encoded(
    scrgb: np.ndarray,
    gamut: Gamut,
    curve: TransferCurve,
    *,
    encode_fn,
    with_cll: bool,
    quantize_bits: int | None,
) -> PipelineResult:
    bits = resolve_quantize_bits(curve, quantize_bits)
    linear = scrgb_to_gamut_linear(scrgb, gamut)
    cll = compute_content_light(linear) if with_cll else None
    signal = encode_fn(linear) if encode_fn is not None else linear
    uint16 = quantize_to_uint16(signal, quantize_bits=bits)
    return PipelineResult(
        rgb=uint16,
        content_light=cll,
        effective_bits=bits,
        is_uint16=True,
    )


def convert_colorspace(
    rgb: np.ndarray,
    gamut: Gamut,
    curve: TransferCurve,
    *,
    quantize_bits: int | None = None,
) -> PipelineResult:
    if curve == TransferCurve.PQ and gamut in (Gamut.BT2020, Gamut.SRGB, Gamut.P3):
        return convert_scrgb_to_pq(rgb, gamut, quantize_bits=quantize_bits)
    if curve == TransferCurve.HLG and gamut in (Gamut.BT2020, Gamut.SRGB, Gamut.P3):
        return convert_scrgb_to_hlg(rgb, gamut, quantize_bits=quantize_bits)
    if curve == TransferCurve.LINEAR and gamut in (Gamut.BT2020, Gamut.SRGB, Gamut.P3):
        return convert_scrgb_to_linear(rgb, gamut, quantize_bits=quantize_bits)

    import colour

    rgb_f = np.asarray(rgb, dtype=np.float32)
    alpha = rgb_f[..., 3:4] if rgb_f.shape[-1] >= 4 else None
    linear = (
        scrgb_to_gamut_linear(rgb_f, gamut)
        if gamut in (Gamut.BT2020, Gamut.SRGB, Gamut.P3)
        else rgb_f[..., :3]
    )

    if curve == TransferCurve.PQ:
        encoded = pq_oetf(linear)
    elif curve == TransferCurve.HLG:
        encoded = hlg_encode_bt2100(linear, gamut=gamut)
    elif curve == TransferCurve.SRGB:
        mapped = np.clip(linear * _PQ_PEAK_NITS / 100.0, 0, 1)
        encoded = colour.cctf_encoding(mapped, function="sRGB")
    elif curve == TransferCurve.LINEAR:
        encoded = linear
    else:
        raise ValueError(f"未知曲线: {curve}")

    if alpha is not None:
        encoded = np.concatenate([encoded, alpha], axis=-1)

    cll = (
        compute_content_light(linear)
        if curve in (TransferCurve.PQ, TransferCurve.LINEAR)
        else None
    )
    return PipelineResult(rgb=encoded, content_light=cll, is_uint16=False)

