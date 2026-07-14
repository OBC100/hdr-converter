"""JPG 编码：PQ/HLG 走 Gain Map（converter）；sRGB 走标准 JPEG + baseline ICC。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..cicp import CICP, TransferCurve
from ..color_pipeline import PipelineResult
from ..hdr_options import HdrDeliveryMode, uses_gainmap
from ..icc_policy import plan_and_bytes
from .base import BaseEncoder, EncodeOptions, OutputFormat, pixels_from_pipeline


class JPGEncoder(BaseEncoder):
    format = OutputFormat.JPG

    def encode(
        self,
        rgb: np.ndarray | PipelineResult,
        output_path: Path,
        options: EncodeOptions,
        cicp: CICP,
    ) -> None:
        if uses_gainmap(options.hdr_delivery):
            raise RuntimeError("Ultra HDR JPG 应由 converter.encode_gainmap 处理")

        if options.curve not in (TransferCurve.SRGB,):
            raise ValueError("JPG Direct 仅支持 sRGB 曲线；PQ/HLG 请使用 Gain Map")

        from ..jpeg_encode import encode_rgb_jpeg

        _ = cicp
        pixels, _ = pixels_from_pipeline(rgb)
        sdr = (np.clip(pixels[..., :3], 0, 1) * 255).astype(np.uint8)
        _plan, icc = plan_and_bytes(
            OutputFormat.JPG,
            options.gamut,
            options.curve,
            HdrDeliveryMode.DIRECT,
            embed_icc=options.embed_icc,
        )
        output_path.write_bytes(
            encode_rgb_jpeg(
                sdr,
                quality=options.quality,
                icc=icc,
                subsampling=options.jpeg_subsampling,
            )
        )
