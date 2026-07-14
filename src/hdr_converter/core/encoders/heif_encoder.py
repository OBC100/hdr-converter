"""HEIF HDR Direct 编码。

默认仅 NCLX（Windows 照片兼容）。``embed_icc=True`` 时经 pillow-heif 写入 ICC，
并可用 ``attach_icc_to_heif`` 补 ``colr``/``prof``。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..cicp import CICP
from ..color_pipeline import PipelineResult
from ..gainmap_container_encode import encode_heif_image_bytes
from ..isobmff_gainmap import attach_clli_to_heif, attach_icc_to_heif
from ._isobmff_direct import (
    maybe_attach_clli,
    prepare_isobmff_direct_pixels,
    write_bytes,
)
from .base import BaseEncoder, EncodeOptions, OutputFormat


class HEIFEncoder(BaseEncoder):
    format = OutputFormat.HEIF

    def encode(
        self,
        rgb: np.ndarray | PipelineResult,
        output_path: Path,
        options: EncodeOptions,
        cicp: CICP,
    ) -> None:
        _ = cicp
        px, bit_depth, direct, nclx, icc = prepare_isobmff_direct_pixels(rgb, options)
        encoded = encode_heif_image_bytes(
            px,
            bit_depth=bit_depth,
            quality=options.quality,
            cicp=nclx,
            icc=icc,
        )
        # pillow-heif 可能未写 colr/prof；再补一层 ISOBMFF 属性
        if icc:
            encoded = attach_icc_to_heif(encoded, icc)
        encoded = maybe_attach_clli(encoded, direct, options, attach_clli_to_heif)
        write_bytes(output_path, encoded)
