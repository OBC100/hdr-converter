"""编码器基类与注册。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np

from ..cicp import CICP, ContentLightLevel, Gamut, TransferCurve, is_hdr_curve
from ..color_pipeline import PipelineResult
from ..hdr_options import (
    DEFAULT_AVIF_SPEED,
    DEFAULT_BASE_BITS,
    DEFAULT_GAINMAP_BITS,
    DEFAULT_GAINMAP_SCALE,
    DEFAULT_JXL_EFFORT,
    HdrDeliveryMode,
    SdrToneMap,
    default_sdr_tonemap,
)
from ..jpeg_encode import DEFAULT_JPEG_SUBSAMPLING, JpegSubsampling


def pixels_from_pipeline(data: np.ndarray | PipelineResult) -> tuple[np.ndarray, bool]:
    if isinstance(data, PipelineResult):
        return data.rgb[..., :3], data.is_uint16
    return data[..., :3], False


@dataclass
class DirectPixels:
    """Direct 编码路径（PNG/HEIF/AVIF/JXL）共用的解包结果。

    只统一"从 PipelineResult/裸数组解包 + 判定容器位深"这一步；ICC/CICP/cLLi 的
    具体字节组装仍由各编码器按容器格式实现（PNG 走 raw chunk，HEIF/AVIF 走
    Pillow 或 imagecodecs，JXL 走 jpegxl_encode primaries/transfer），因为各容器的
    元数据写入 API 形态本身不同，勉强合并收益有限且会引入额外抽象层，见
    docs/UNIFIED_PIPELINE.md §4。
    """

    pixels: np.ndarray
    is_uint16: bool
    bit_depth: int
    content_light: ContentLightLevel | None


def unpack_direct_pixels(
    data: np.ndarray | PipelineResult,
    options: "EncodeOptions",
) -> DirectPixels:
    """PNG/HEIF/AVIF/JXL Direct 共用规则：uint16（已量化）或 HDR 曲线（PQ/HLG/Linear）
    时用 16-bit，否则 8-bit；content_light 优先取 PipelineResult 自带值，否则回退
    ``options.content_light``（由 converter.convert_file 预先写入）。
    """
    if isinstance(data, PipelineResult):
        pixels = data.rgb[..., :3]
        is_uint16 = data.is_uint16
        content_light = data.content_light or options.content_light
    else:
        pixels = data[..., :3]
        is_uint16 = False
        content_light = options.content_light
    bit_depth = 16 if is_uint16 or is_hdr_curve(options.curve) else 8
    return DirectPixels(
        pixels=pixels,
        is_uint16=is_uint16,
        bit_depth=bit_depth,
        content_light=content_light,
    )


class OutputFormat(str, Enum):
    PNG = "png"
    HEIF = "heif"
    AVIF = "avif"
    JPG = "jpg"
    JXL = "jxl"


@dataclass
class EncodeOptions:
    gamut: Gamut = Gamut.BT2020
    curve: TransferCurve = TransferCurve.PQ
    bit_depth: int = 16
    quality: int = 90
    content_light: ContentLightLevel | None = None
    png_optimize: bool = True
    oxipng_level: int = 2
    output_format: OutputFormat = OutputFormat.PNG
    hdr_delivery: HdrDeliveryMode = HdrDeliveryMode.DIRECT
    base_bits: int = DEFAULT_BASE_BITS
    gainmap_bits: int = DEFAULT_GAINMAP_BITS
    gainmap_scale: int = DEFAULT_GAINMAP_SCALE
    sdr_tonemap: SdrToneMap | None = None
    jpeg_subsampling: JpegSubsampling = DEFAULT_JPEG_SUBSAMPLING
    avif_speed: int = DEFAULT_AVIF_SPEED
    avif_numthreads: int | None = None
    jxl_effort: int = DEFAULT_JXL_EFFORT
    # None = 按 icc_policy.default_embed_icc；True/False 强制开关（HEIF/AVIF 默认 False）
    embed_icc: bool | None = None

    def resolved_sdr_tonemap(self) -> SdrToneMap:
        return self.sdr_tonemap or default_sdr_tonemap(self.curve)


DEFAULT_OXIPNG_LEVEL = 2
DEFAULT_LOSSY_QUALITY = 90
OXIPNG_LEVEL_MIN = 1
OXIPNG_LEVEL_MAX = 6


def default_encode_level(output_format: OutputFormat) -> int:
    if output_format == OutputFormat.PNG:
        return DEFAULT_OXIPNG_LEVEL
    return DEFAULT_LOSSY_QUALITY


def resolve_encode_level(output_format: OutputFormat, encode_level: int | None) -> int:
    level = encode_level if encode_level is not None else default_encode_level(output_format)
    if output_format == OutputFormat.PNG:
        return max(0, min(OXIPNG_LEVEL_MAX, level))
    return max(1, min(100, level))


def apply_encode_level(
    options: EncodeOptions,
    output_format: OutputFormat,
    encode_level: int | None,
) -> None:
    """按输出格式解析 ``encode_level``，写入 ``EncodeOptions``。"""
    level = resolve_encode_level(output_format, encode_level)
    if output_format == OutputFormat.PNG:
        options.png_optimize = level > 0
        options.oxipng_level = level if level > 0 else DEFAULT_OXIPNG_LEVEL
        return
    options.quality = level
    options.png_optimize = False


class BaseEncoder(ABC):
    format: OutputFormat

    @abstractmethod
    def encode(
        self,
        rgb: np.ndarray | PipelineResult,
        output_path: Path,
        options: EncodeOptions,
        cicp: CICP,
    ) -> None:
        ...
