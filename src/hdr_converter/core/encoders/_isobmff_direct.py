"""AVIF / HEIF Direct 编码共用步骤。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..cicp import CICP
from ..color_metadata import get_direct_cicp_for_isobmff, should_write_clli
from ..color_pipeline import PipelineResult
from ..hdr_options import uses_gainmap
from ..icc_policy import plan_and_bytes
from ..sample_bits import direct_pixels_for_container
from .base import EncodeOptions, OutputFormat, unpack_direct_pixels


def prepare_isobmff_direct_pixels(
    rgb: np.ndarray | PipelineResult,
    options: EncodeOptions,
) -> tuple[np.ndarray, int, object, CICP, bytes | None]:
    """返回 (px, bit_depth, direct, nclx_cicp, icc_or_none)。"""
    if uses_gainmap(options.hdr_delivery):
        raise RuntimeError("Gain Map 应由 converter.encode_gainmap 处理")

    direct = unpack_direct_pixels(rgb, options)
    px, bit_depth = direct_pixels_for_container(direct, options.base_bits)

    nclx = get_direct_cicp_for_isobmff(options.gamut, options.curve)
    _plan, icc = plan_and_bytes(
        options.output_format,
        options.gamut,
        options.curve,
        options.hdr_delivery,
        embed_icc=options.embed_icc,
    )
    return px, bit_depth, direct, nclx, icc


def maybe_attach_clli(
    encoded: bytes,
    direct,
    options: EncodeOptions,
    attach_fn,
) -> bytes:
    if (
        direct.content_light is not None
        and should_write_clli(options.curve)
        and (direct.content_light.max_cll or direct.content_light.max_fall)
    ):
        return attach_fn(encoded, direct.content_light)
    return encoded


def write_bytes(path: Path, data: bytes) -> None:
    path.write_bytes(data)
