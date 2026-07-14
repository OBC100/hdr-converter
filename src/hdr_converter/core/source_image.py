"""格式无关的源图像中间表示（L(-1) 解码器产出）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .named_colourspaces import PrimariesLike


@dataclass
class SourceImage:
    """解码器产出的原生色彩缓冲（尚未归一化到 canonical BT.2020）。"""

    linear: np.ndarray
    """float32 HxWx(3|4)，原生色域下显示线性，未裁剪。"""

    primaries: PrimariesLike
    """原生色域：内建 ``Gamut`` 或 ``ColourSpaceDescriptor``（ICC/CICP）。"""

    reference_white_nits: float
    """``linear == 1.0`` 对应的绝对亮度（nits）。"""

    is_hdr: bool
    alpha: np.ndarray | None = None
    embedded_gainmap: Any | None = None
    icc_profile: bytes | None = None
    orientation_exif: bytes | None = None
    metadata: Any | None = None
