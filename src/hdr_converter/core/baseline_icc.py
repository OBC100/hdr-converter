"""Ultra HDR SDR 基础图 ICC（Apple matrix-shaper 生成标准）。"""

from __future__ import annotations

import struct
from functools import lru_cache

import numpy as np

from .assets.apple_baseline_icc import create_apple_baseline_icc_profile
from .cicp import Gamut

_GAMUT_PRIMARIES: dict[Gamut, str] = {
    Gamut.SRGB: "sRGB",
    Gamut.P3: "Display P3",
    Gamut.BT2020: "ITU-R BT.2020",
}


def _xyz_tag_bytes(xyz: np.ndarray) -> bytes:
    """ICC XYZType: s15Fixed16 × 3。"""
    out = bytearray()
    for v in xyz:
        out.extend(struct.pack(">i", int(round(float(v) * 65536.0))))
    return bytes(out)


@lru_cache(maxsize=4)
def get_baseline_display_icc(gamut: Gamut) -> bytes:
    """
    SDR baseline ICC（Apple/Lightroom Ultra HDR 同结构，按色域生成 r/g/bXYZ）。

    见 ``assets.apple_baseline_icc.create_apple_baseline_icc_profile``。
    """
    return create_apple_baseline_icc_profile(gamut)


def _patch_icc_primaries(template: bytes, gamut: Gamut) -> bytes:
    """在 sRGB ICC 模板上替换 r/g/b XYZ 与 chrm 色度（旧方案，仅对比测试）。"""
    import colour

    cs = colour.RGB_COLOURSPACES[_GAMUT_PRIMARIES[gamut]]
    m = cs.matrix_RGB_to_XYZ
    tags = {
        b"rXYZ": m @ np.array([1.0, 0.0, 0.0]),
        b"gXYZ": m @ np.array([0.0, 1.0, 0.0]),
        b"bXYZ": m @ np.array([0.0, 0.0, 1.0]),
    }
    data = bytearray(template)
    tag_count = struct.unpack_from(">I", data, 128)[0]
    for i in range(tag_count):
        off = 132 + i * 12
        sig = bytes(data[off : off + 4])
        if sig not in tags:
            continue
        tag_off = struct.unpack_from(">I", data, off + 4)[0]
        struct.pack_into(">I", data, tag_off, 0)
        struct.pack_into(">I", data, tag_off + 4, 12)
        data[tag_off + 8 : tag_off + 20] = _xyz_tag_bytes(tags[sig])

    primaries = cs.primaries
    for i in range(tag_count):
        off = 132 + i * 12
        if bytes(data[off : off + 4]) != b"chrm":
            continue
        tag_off = struct.unpack_from(">I", data, off + 4)[0]
        for c in range(3):
            base = tag_off + 12 + c * 8
            struct.pack_into(">i", data, base, int(round(float(primaries[c, 0]) * 65536.0)))
            struct.pack_into(">i", data, base + 4, int(round(float(primaries[c, 1]) * 65536.0)))
        break

    return bytes(data)


@lru_cache(maxsize=4)
def get_baseline_display_icc_patched(gamut: Gamut) -> bytes:
    """sRGB 模板 patch 版 baseline ICC（旧方案，仅 A/B 对比）。"""
    from PIL import ImageCms

    template = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    if gamut == Gamut.SRGB:
        return template
    return _patch_icc_primaries(template, gamut)
