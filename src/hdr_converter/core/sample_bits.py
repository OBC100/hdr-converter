"""编码样本位深对齐（左对齐容器 vs 右对齐码值）。"""

from __future__ import annotations

import numpy as np


def right_align_uint16(px: np.ndarray, bit_depth: int) -> np.ndarray:
    """左对齐 uint16（高位有效）→ ``[0, 2^bit_depth-1]`` 右对齐。

    ``bit_depth == 16`` 或非 uint16 时原样返回。
    """
    if px.dtype == np.uint16 and bit_depth < 16:
        shift = 16 - bit_depth
        if shift > 0:
            return (px >> shift).astype(np.uint16)
    return px


def left_align_codes(
    codes: np.ndarray,
    bit_depth: int,
    *,
    container_bits: int = 16,
) -> np.ndarray:
    """右对齐码值 ``[0, 2^bit_depth-1]`` → 左对齐装入 ``container_bits`` 容器。"""
    shift = container_bits - bit_depth
    out = np.asarray(codes, dtype=np.uint32)
    if shift > 0:
        out = out << shift
    if container_bits <= 8:
        return out.astype(np.uint8)
    return out.astype(np.uint16)


def quantize_unit_to_left_aligned_uint16(
    signal: np.ndarray,
    bit_depth: int,
    *,
    container_bits: int = 16,
) -> np.ndarray:
    """``[0,1]`` 信号 → 左对齐 uint16（或 container_bits≤8 时 uint8）。"""
    max_code = (1 << bit_depth) - 1
    codes = np.round(np.clip(signal, 0.0, 1.0) * max_code).astype(np.uint32)
    codes = np.clip(codes, 0, max_code)
    return left_align_codes(codes, bit_depth, container_bits=container_bits)


def direct_pixels_for_container(
    direct,
    base_bits: int,
) -> tuple[np.ndarray, int]:
    """Direct 路径：已量化 uint16 用 ``base_bits``；否则压成 8-bit。

    返回 ``(px, bit_depth)``，供 AVIF/HEIF/JXL 等容器编码器使用。
    """
    if direct.is_uint16 or direct.bit_depth == 16:
        return direct.pixels.astype(np.uint16), base_bits
    px = (np.clip(direct.pixels, 0, 1) * 255).astype(np.uint8)
    return px, 8
