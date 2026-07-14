"""输出格式相关 UI：曲线 / 位深 / HDR 交付 / JPG 抽样。"""

from __future__ import annotations

from ..core.cicp import TransferCurve
from ..core.encoders.base import OutputFormat
from ..core.hdr_options import (
    GAINMAP_LINEAR_TONEMAPS,
    JXL_BITS_CHOICES,
    GainMapScale,
    HdrDeliveryMode,
    SdrToneMap,
    gainmap_allowed,
    uses_gainmap,
)
from ..core.jpeg_encode import JpegSubsampling

# ---- 曲线 ----

CURVE_KEYS: dict[TransferCurve, str] = {
    TransferCurve.SRGB: "curve.srgb",
    TransferCurve.LINEAR: "curve.linear",
    TransferCurve.PQ: "curve.pq",
    TransferCurve.HLG: "curve.hlg",
}


def supported_curves(fmt: OutputFormat) -> tuple[TransferCurve, ...]:
    """JPG 不支持 Linear（无 Gain Map 且 Direct 仅 sRGB）。"""
    if fmt == OutputFormat.JPG:
        return (TransferCurve.SRGB, TransferCurve.PQ, TransferCurve.HLG)
    return (
        TransferCurve.SRGB,
        TransferCurve.LINEAR,
        TransferCurve.PQ,
        TransferCurve.HLG,
    )


# ---- 有效位深 ----

QUANT_KEYS: dict[int, str] = {
    8: "quant.8",
    10: "quant.10",
    12: "quant.12",
    14: "quant.14",
    16: "quant.16",
}

DEFAULT_QUANT_BITS = 10

# AVIF/HEIF：AV1 与 HEVC Main 原生支持 8/10/12-bit（见 AVIF v1.2 / ISO 23008-12）
_LOSSY_CONTAINER_BITS = (8, 10, 12)


def supported_quant_bits(
    output_format: OutputFormat,
    curve: TransferCurve,
) -> tuple[int, ...]:
    if output_format == OutputFormat.PNG:
        return (8, 10, 12, 14, 16)
    if output_format == OutputFormat.JPG:
        return (8,)
    if output_format == OutputFormat.JXL:
        return JXL_BITS_CHOICES
    if output_format in (OutputFormat.HEIF, OutputFormat.AVIF):
        return _LOSSY_CONTAINER_BITS
    return _LOSSY_CONTAINER_BITS


def quant_card_visible(
    output_format: OutputFormat,
    delivery: HdrDeliveryMode = HdrDeliveryMode.DIRECT,
) -> bool:
    """PNG / HEIF / AVIF / JXL 显示位深卡；JPG 锁定 8-bit 不显示。

    HEIF/AVIF 在 Direct 与 Gain Map 下均显示：控件语义为基础图位深
    （Gain Map 增益图固定 8-bit，与 JPG 一致）。
    """
    _ = delivery
    return output_format != OutputFormat.JPG


def clamp_quant_bits(
    bits: int,
    output_format: OutputFormat,
    curve: TransferCurve,
) -> int:
    allowed = supported_quant_bits(output_format, curve)
    if bits in allowed:
        return bits
    if DEFAULT_QUANT_BITS in allowed:
        return DEFAULT_QUANT_BITS
    return allowed[0]


# ---- HDR 交付 / Gain Map ----

DELIVERY_KEYS: dict[HdrDeliveryMode, str] = {
    HdrDeliveryMode.DIRECT: "hdr.direct",
    HdrDeliveryMode.GAINMAP_MONO: "hdr.gainmap_mono",
    HdrDeliveryMode.GAINMAP_COLOR: "hdr.gainmap_color",
}

TONEMAP_KEYS: dict[SdrToneMap, str] = {
    SdrToneMap.HABLE_MAX: "tonemap.hable_max",
    SdrToneMap.CHROME: "tonemap.chrome",
    SdrToneMap.SAFARI: "tonemap.safari",
}

GAINMAP_TONEMAP_ORDER: list[SdrToneMap] = [
    t for t in TONEMAP_KEYS if t in GAINMAP_LINEAR_TONEMAPS
]

SCALE_KEYS: dict[GainMapScale, str] = {
    GainMapScale.FULL: "gainmap_scale.full",
    GainMapScale.HALF: "gainmap_scale.half",
    GainMapScale.QUARTER: "gainmap_scale.quarter",
    GainMapScale.EIGHTH: "gainmap_scale.eighth",
}


def delivery_card_visible(fmt: OutputFormat) -> bool:
    return fmt in (OutputFormat.HEIF, OutputFormat.AVIF, OutputFormat.JPG, OutputFormat.JXL)


def supported_delivery_modes(
    fmt: OutputFormat,
    curve: TransferCurve,
) -> tuple[HdrDeliveryMode, ...]:
    if fmt == OutputFormat.JPG:
        if gainmap_allowed(curve):
            return (HdrDeliveryMode.GAINMAP_MONO, HdrDeliveryMode.GAINMAP_COLOR)
        return (HdrDeliveryMode.DIRECT,)
    if fmt in (OutputFormat.HEIF, OutputFormat.AVIF, OutputFormat.JXL):
        if gainmap_allowed(curve):
            return (
                HdrDeliveryMode.DIRECT,
                HdrDeliveryMode.GAINMAP_MONO,
                HdrDeliveryMode.GAINMAP_COLOR,
            )
        return (HdrDeliveryMode.DIRECT,)
    return (HdrDeliveryMode.DIRECT,)


def gainmap_options_visible(fmt: OutputFormat, delivery: HdrDeliveryMode) -> bool:
    return fmt in (
        OutputFormat.HEIF,
        OutputFormat.AVIF,
        OutputFormat.JPG,
        OutputFormat.JXL,
    ) and uses_gainmap(delivery)


# ---- JPG 色度抽样 ----

SUBSAMPLING_KEYS: dict[JpegSubsampling, str] = {
    JpegSubsampling.S420: "jpeg.subsampling.420",
    JpegSubsampling.S422: "jpeg.subsampling.422",
    JpegSubsampling.S444: "jpeg.subsampling.444",
}

JPEG_SUBSAMPLING_ORDER: tuple[JpegSubsampling, ...] = (
    JpegSubsampling.S420,
    JpegSubsampling.S422,
    JpegSubsampling.S444,
)


def jpeg_subsampling_card_visible(fmt: OutputFormat) -> bool:
    return fmt == OutputFormat.JPG
