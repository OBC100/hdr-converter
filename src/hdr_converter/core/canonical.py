"""格式无关的规范线性空间（L0 canonical）。

绝对刻度：1.0 = 10000 nits，色域：BT.2020 显示线性。
"""

from __future__ import annotations

import numpy as np

from .cicp import Gamut
from .named_colourspaces import (
    ColourSpaceDescriptor,
    PrimariesLike,
    resolve_colour_rgb_colourspace,
)
from .scrgb_colour import gamut_linear_to_gamut_linear

# Windows scRGB / JXR：linear==1.0 ≈ 80 nits（IEC 61966-2-2）
SCRGB_REFERENCE_WHITE_NITS = 80.0
# 无 HDR 元数据的 SDR 输入：贴近 BT.2408 图形白
SDR_REFERENCE_WHITE_NITS = 100.0
# L0 canonical 峰值刻度
CANONICAL_PEAK_NITS = 10_000.0


def to_canonical_bt2020_linear(
    src_linear: np.ndarray,
    primaries: PrimariesLike,
    reference_white_nits: float,
) -> np.ndarray:
    """任意原生显示线性 → BT.2020 线性，绝对刻度 1.0 = 10000 nits。

    - 内建 ``Gamut``（同为 D65）：沿用快速矩阵路径（与 Stage A 零回归）。
    - ``ColourSpaceDescriptor`` / 异白点：``colour.RGB_to_RGB`` + Bradford CAT。
    """
    scale = float(reference_white_nits) / CANONICAL_PEAK_NITS
    rgb = np.asarray(src_linear[..., :3], dtype=np.float64) * scale

    if isinstance(primaries, Gamut):
        bt2020 = gamut_linear_to_gamut_linear(rgb, primaries, Gamut.BT2020)
        return np.clip(bt2020, 0.0, None).astype(np.float32)

    if isinstance(primaries, ColourSpaceDescriptor):
        builtin = primaries.as_builtin_gamut()
        if builtin is not None:
            bt2020 = gamut_linear_to_gamut_linear(rgb, builtin, Gamut.BT2020)
            return np.clip(bt2020, 0.0, None).astype(np.float32)

    import colour

    src_cs = resolve_colour_rgb_colourspace(primaries)
    dst_cs = colour.RGB_COLOURSPACES["ITU-R BT.2020"]
    converted = colour.RGB_to_RGB(
        rgb,
        src_cs,
        dst_cs,
        chromatic_adaptation_transform="Bradford",
    )
    return np.clip(converted, 0.0, None).astype(np.float32)
