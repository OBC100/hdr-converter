"""scRGB 扩展色域转换（IEC 61966-2-2：保留负值与超值）。"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from .cicp import Gamut

_GAMUT_COLOUR_NAMES: dict[Gamut, str] = {
    Gamut.BT2020: "ITU-R BT.2020",
    Gamut.SRGB: "sRGB",
    Gamut.P3: "Display P3",
}

# 公开别名（供 color_pipeline / 其它模块共用）
GAMUT_COLOUR_NAMES = _GAMUT_COLOUR_NAMES


@lru_cache(maxsize=8)
def _matrix_scrgb_to_gamut(gamut: Gamut) -> np.ndarray:
    """scRGB 线性 → 目标色域线性（合并 XYZ 两步，矩阵来自 colour-science）。"""
    import colour

    cs_s = colour.RGB_COLOURSPACES["sRGB"]
    cs_t = colour.RGB_COLOURSPACES[_GAMUT_COLOUR_NAMES[gamut]]
    return np.asarray(cs_t.matrix_XYZ_to_RGB @ cs_s.matrix_RGB_to_XYZ, dtype=np.float64)


@lru_cache(maxsize=16)
def _matrix_gamut_to_gamut(src: Gamut, dst: Gamut) -> np.ndarray:
    """源色域线性 → 目标色域线性（合并矩阵）。"""
    import colour

    cs_s = colour.RGB_COLOURSPACES[_GAMUT_COLOUR_NAMES[src]]
    cs_t = colour.RGB_COLOURSPACES[_GAMUT_COLOUR_NAMES[dst]]
    return np.asarray(cs_t.matrix_XYZ_to_RGB @ cs_s.matrix_RGB_to_XYZ, dtype=np.float64)


def _matmul_linear_rgb(rgb: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """(H,W,3) @ Mᵀ；float64 计算，float32 输出，不裁负值。"""
    arr = np.asarray(rgb[..., :3], dtype=np.float64)
    h, w = arr.shape[:2]
    out = arr.reshape(h * w, 3) @ matrix.T
    return out.reshape(h, w, 3).astype(np.float32)


def scrgb_to_gamut_linear_abs(scrgb: np.ndarray, gamut: Gamut) -> np.ndarray:
    """
    scRGB → 目标色域显示线性（scRGB 绝对刻度，1.0 ≈ 80 nits）。

    经 XYZ 中转，保留 scRGB 负值所表达的扩展色域。
    """
    return _matmul_linear_rgb(scrgb, _matrix_scrgb_to_gamut(gamut))


def srgb_linear_to_gamut_linear_abs(
    srgb_linear: np.ndarray,
    gamut: Gamut,
) -> np.ndarray:
    """sRGB 基色显示线性 → 目标色域线性（与 scrgb 同基色时等价于 abs 转换）。"""
    return scrgb_to_gamut_linear_abs(srgb_linear, gamut)


def gamut_linear_to_gamut_linear(
    linear: np.ndarray,
    src: Gamut,
    dst: Gamut,
) -> np.ndarray:
    """显示线性 RGB：源色域 → 目标色域（经 XYZ，D65）。"""
    if src == dst:
        return np.asarray(linear, dtype=np.float32)
    return _matmul_linear_rgb(linear, _matrix_gamut_to_gamut(src, dst))
