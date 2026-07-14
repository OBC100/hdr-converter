"""PNG 编码 (cICP, iCCP, cLLi) — HDR/SDR 均嵌入匹配 ICC。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..assets import make_iccp_chunk_data
from ..cicp import CICP
from ..color_metadata import should_write_clli
from ..color_pipeline import PipelineResult
from ..hdr_options import HdrDeliveryMode
from ..icc_policy import plan_and_bytes, resolve_icc_name
from ..png_optimizer import write_optimized_png
from .base import BaseEncoder, EncodeOptions, OutputFormat, unpack_direct_pixels


class PNGEncoder(BaseEncoder):
    format = OutputFormat.PNG

    def encode(
        self,
        rgb: np.ndarray | PipelineResult,
        output_path: Path,
        options: EncodeOptions,
        cicp: CICP,
    ) -> None:
        direct = unpack_direct_pixels(rgb, options)
        pixels, is_uint16, bit_depth, content_light = (
            direct.pixels,
            direct.is_uint16,
            direct.bit_depth,
            direct.content_light,
        )

        if is_uint16:
            rgb_out = pixels.astype(np.uint16)
        elif bit_depth == 16:
            rgb_out = (np.clip(pixels, 0, 1) * 65535).astype(np.uint16)
        else:
            rgb_out = (np.clip(pixels, 0, 1) * 255).astype(np.uint8)

        plan, icc_bytes = plan_and_bytes(
            OutputFormat.PNG,
            options.gamut,
            options.curve,
            options.hdr_delivery or HdrDeliveryMode.DIRECT,
            embed_icc=options.embed_icc,
        )
        iccp_chunk_data = None
        if icc_bytes is not None:
            name = plan.profile_name or resolve_icc_name(
                plan.kind, options.gamut, options.curve
            )
            iccp_chunk_data = make_iccp_chunk_data(name or "ICC", icc_bytes)

        clli_bytes = None
        if content_light and should_write_clli(options.curve):
            clli_bytes = content_light.to_png_bytes()

        write_optimized_png(
            output_path,
            rgb_out,
            bit_depth=bit_depth,
            cicp_bytes=cicp.to_bytes(),
            iccp_chunk_data=iccp_chunk_data,
            icc_bytes=icc_bytes,
            clli_bytes=clli_bytes,
            enabled=options.png_optimize,
            oxipng_level=options.oxipng_level,
        )
