"""AVIF HDR Direct 编码。

默认仅 NCLX（Windows 照片兼容）。``embed_icc=True`` 时追加 ``colr``/``prof``。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..cicp import CICP
from ..color_pipeline import PipelineResult
from ..gainmap_container_encode import encode_avif_image_bytes
from ..isobmff_gainmap import attach_clli_to_avif, attach_icc_to_avif
from ._isobmff_direct import (
    maybe_attach_clli,
    prepare_isobmff_direct_pixels,
    write_bytes,
)
from .base import BaseEncoder, EncodeOptions, OutputFormat


class AVIFEncoder(BaseEncoder):
    format = OutputFormat.AVIF

    def encode(
        self,
        rgb: np.ndarray | PipelineResult,
        output_path: Path,
        options: EncodeOptions,
        cicp: CICP,
    ) -> None:
        _ = cicp
        px, bit_depth, direct, nclx, icc = prepare_isobmff_direct_pixels(rgb, options)
        encoded = encode_avif_image_bytes(
            px,
            bit_depth=bit_depth,
            quality=options.quality,
            cicp=nclx,
            speed=options.avif_speed,
            numthreads=options.avif_numthreads,
            parallel_jobs=1,
        )
        encoded = maybe_attach_clli(encoded, direct, options, attach_clli_to_avif)
        if icc:
            encoded = attach_icc_to_avif(encoded, icc)
        write_bytes(output_path, encoded)
