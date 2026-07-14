"""HEIF / AVIF / JPG / JXL HDR 交付选项与校验。"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from .cicp import TransferCurve

if TYPE_CHECKING:
    from .encoders.base import OutputFormat

CONTAINER_BITS_CHOICES = (8, 10, 12)
# JPEG XL（ISO/IEC 18181）整数样本常用 8–16 bit；含 14 以对齐管线 QUANTIZE_BITS
JXL_BITS_CHOICES = (8, 10, 12, 14, 16)
DEFAULT_BASE_BITS = 8
DEFAULT_GAINMAP_BITS = 8
DEFAULT_GAINMAP_SCALE = 2
# libavif speed 0=最慢/最小，10=最快；未指定时 imagecodecs 默认极慢（4K 可 >6 分钟）
DEFAULT_AVIF_SPEED = 6
# libjxl effort 1–9（越高越慢/略小）；4 兼顾导出速度与体积
DEFAULT_JXL_EFFORT = 4


class HdrDeliveryMode(str, Enum):
    DIRECT = "direct"
    GAINMAP_MONO = "gainmap_mono"
    GAINMAP_COLOR = "gainmap_color"


class SdrToneMap(str, Enum):
    """Gain Map SDR 色调映射。"""

    HABLE_MAX = "hable_max"
    CHROME = "chrome"  # Chromium 有理函数 max-RGB
    SAFARI = "safari"  # BT.2408 Annex 5 max-RGB（Safari 风格）


GAINMAP_LINEAR_TONEMAPS: frozenset[SdrToneMap] = frozenset(
    {
        SdrToneMap.HABLE_MAX,
        SdrToneMap.CHROME,
        SdrToneMap.SAFARI,
    }
)


class GainMapScale(int, Enum):
    FULL = 1
    HALF = 2
    QUARTER = 4
    EIGHTH = 8


def gainmap_allowed(curve: TransferCurve) -> bool:
    return curve in (TransferCurve.PQ, TransferCurve.HLG)


def default_sdr_tonemap(curve: TransferCurve) -> SdrToneMap:
    _ = curve
    return SdrToneMap.HABLE_MAX


def resolve_gainmap_tonemap(
    requested: SdrToneMap | None,
    curve: TransferCurve = TransferCurve.PQ,
) -> SdrToneMap:
    op = requested or default_sdr_tonemap(curve)
    if op not in GAINMAP_LINEAR_TONEMAPS:
        return default_sdr_tonemap(curve)
    return op


def supports_hdr_delivery(fmt: OutputFormat) -> bool:
    from .encoders.base import OutputFormat as Fmt

    return fmt in (Fmt.HEIF, Fmt.AVIF, Fmt.JPG, Fmt.JXL)


def resolve_hdr_delivery(
    fmt: OutputFormat,
    curve: TransferCurve,
    delivery: HdrDeliveryMode,
) -> HdrDeliveryMode:
    from .encoders.base import OutputFormat as Fmt

    if not supports_hdr_delivery(fmt):
        return HdrDeliveryMode.DIRECT
    if fmt == Fmt.JPG:
        if curve in (TransferCurve.PQ, TransferCurve.HLG):
            if delivery == HdrDeliveryMode.DIRECT:
                return HdrDeliveryMode.GAINMAP_MONO
            return delivery
        return HdrDeliveryMode.DIRECT
    if not gainmap_allowed(curve):
        return HdrDeliveryMode.DIRECT
    return delivery


def uses_gainmap(delivery: HdrDeliveryMode) -> bool:
    return delivery in (HdrDeliveryMode.GAINMAP_MONO, HdrDeliveryMode.GAINMAP_COLOR)


def multichannel_gainmap(delivery: HdrDeliveryMode) -> bool:
    return delivery == HdrDeliveryMode.GAINMAP_COLOR


def normalize_container_bits(bits: int) -> int:
    if bits not in CONTAINER_BITS_CHOICES:
        allowed = ", ".join(str(b) for b in CONTAINER_BITS_CHOICES)
        raise ValueError(f"位深须为 {allowed}，收到 {bits}")
    return bits


def normalize_jxl_bits(bits: int) -> int:
    if bits not in JXL_BITS_CHOICES:
        allowed = ", ".join(str(b) for b in JXL_BITS_CHOICES)
        raise ValueError(f"JXL 位深须为 {allowed}，收到 {bits}")
    return bits


def normalize_gainmap_scale(scale: int) -> int:
    try:
        return GainMapScale(scale).value
    except ValueError as exc:
        allowed = ", ".join(str(s.value) for s in GainMapScale)
        raise ValueError(f"gainmap_scale 须为 {allowed}，收到 {scale}") from exc
