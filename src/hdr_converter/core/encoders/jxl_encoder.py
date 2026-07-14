"""JPEG XL (ISO/IEC 18181) HDR Direct 编码。

默认写 nclx + 码流 ColourEncoding（不嵌 ICC）。``embed_icc=True`` 时追加
``colr``/``prof``。Gain Map 由 ``jxl_gainmap`` 处理。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..cicp import CICP
from ..color_pipeline import PipelineResult
from ..hdr_options import uses_gainmap
from ..icc_policy import plan_and_bytes
from ..isobmff_gainmap import _prof_colr_box
from ..jxl_gainmap import _inject_box_after_ftyp, encode_jxl_image_bytes
from ..sample_bits import direct_pixels_for_container
from .base import BaseEncoder, EncodeOptions, OutputFormat, unpack_direct_pixels


class JXLEncoder(BaseEncoder):
    format = OutputFormat.JXL

    def encode(
        self,
        rgb: np.ndarray | PipelineResult,
        output_path: Path,
        options: EncodeOptions,
        cicp: CICP,
    ) -> None:
        if uses_gainmap(options.hdr_delivery):
            raise RuntimeError("Gain Map JXL 应由 converter.encode_gainmap 处理")

        _ = cicp
        direct = unpack_direct_pixels(rgb, options)
        px, bit_depth = direct_pixels_for_container(direct, options.base_bits)

        encoded = encode_jxl_image_bytes(
            px,
            bit_depth=bit_depth,
            quality=options.quality,
            gamut=options.gamut,
            curve=options.curve,
            effort=options.jxl_effort,
            usecontainer=True,
        )
        _plan, icc = plan_and_bytes(
            OutputFormat.JXL,
            options.gamut,
            options.curve,
            options.hdr_delivery,
            embed_icc=options.embed_icc,
        )
        if icc:
            # nclx 已由 encode_jxl_image_bytes 注入；再追加 prof（同 type 的 inject 会跳过，
            # 故用 raw 拼接：在已有 colr 后再插一条 prof）
            encoded = _inject_prof_after_nclx(encoded, icc)
        output_path.write_bytes(encoded)


def _inject_prof_after_nclx(data: bytes, icc: bytes) -> bytes:
    """在已有 ``colr``/nclx 之后插入 ``colr``/prof；已有 prof 则跳过。"""
    import struct

    i = 0
    while i + 8 <= len(data):
        size = struct.unpack(">I", data[i : i + 4])[0]
        if size < 8 or i + size > len(data):
            break
        typ = data[i + 4 : i + 8]
        if typ == b"colr" and size >= 12 and data[i + 8 : i + 12] == b"prof":
            return data
        i += size
    # 找第一个 colr（nclx）之后插入
    i = 0
    while i + 8 <= len(data):
        size = struct.unpack(">I", data[i : i + 4])[0]
        if size < 8 or i + size > len(data):
            break
        typ = data[i + 4 : i + 8]
        if typ == b"colr":
            prof = _prof_colr_box(icc)
            return data[: i + size] + prof + data[i + size :]
        i += size
    return _inject_box_after_ftyp(data, _prof_colr_box(icc))
