"""编码器注册表。"""

from __future__ import annotations

from .avif_encoder import AVIFEncoder
from .base import BaseEncoder, OutputFormat
from .heif_encoder import HEIFEncoder
from .jpg_encoder import JPGEncoder
from .jxl_encoder import JXLEncoder
from .png_encoder import PNGEncoder

_ENCODERS: dict[OutputFormat, BaseEncoder] = {
    OutputFormat.PNG: PNGEncoder(),
    OutputFormat.HEIF: HEIFEncoder(),
    OutputFormat.AVIF: AVIFEncoder(),
    OutputFormat.JPG: JPGEncoder(),
    OutputFormat.JXL: JXLEncoder(),
}


def get_encoder(fmt: OutputFormat) -> BaseEncoder:
    return _ENCODERS[fmt]
