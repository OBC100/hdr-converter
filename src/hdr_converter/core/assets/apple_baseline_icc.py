"""
Apple matrix-shaper baseline ICC 生成器（对齐 Lightroom Ultra HDR / Windows 照片）。

Lightroom 导出 Ultra HDR 时 baseline 内嵌的是 Apple ``Display P3`` 类 monitor profile：
- 头部 CMM/creator = ``appl``
- ``desc`` type 11，固定 101 字节
- ``cprt`` = ``text`` 类型
- ``rTRC/gTRC/bTRC`` 共享 32 字节 para type 3（5 个 s15Fixed16 参数，非 jxl 自研短格式）
- ``chad`` + D50 适应后的 ``r/g/bXYZ``

色域差异仅体现在 ``desc`` 文案与 ``r/g/bXYZ``；传递函数与白点适应与 Apple 保持一致。
"""

from __future__ import annotations

import struct
from functools import lru_cache
from importlib import resources

from ..cicp import Gamut
from .libjxl_pq_icc import (
    _D65,
    _PRIMARIES,
    _append_tag,
    _append_u32,
    _create_xyz_tag,
    _primaries_to_xyz_d50,
    _write_u32,
    gamut_to_primaries,
)

# Lightroom 参考图提取的固定 tag 载荷（与色域无关部分）
_APPLE_WTPT_TAG = bytes.fromhex(
    "58595a20000000000000f35100010000000116cc"
)
_APPLE_TRC_TAG = bytes.fromhex(
    "706172610000000000030000000266660000f2b000000d50000013b6000009fc"
)
_APPLE_CHAD_TAG = bytes.fromhex(
    "736633320000000000010c42000005defffff326000007930000fd90fffffba2fffffda3000003dc0000c06e"
)
_APPLE_CPRT_TAG = bytes.fromhex(
    "7465787400000000436f70797269676874204170706c6520496e632e2c203230313500"
)
_APPLE_P3_REFERENCE = "display_p3_baseline_lr.icc"


@lru_cache(maxsize=1)
def _load_lr_reference_icc() -> bytes:
    """Lightroom Ultra HDR 参考 baseline ICC（用于提取 Apple 固定字段）。"""
    return (
        resources.files("hdr_converter.core.assets")
        .joinpath(_APPLE_P3_REFERENCE)
        .read_bytes()
    )


@lru_cache(maxsize=1)
def _apple_icc_header_rest() -> bytes:
    ref = _load_lr_reference_icc()
    return ref[4:128]

_BASELINE_DESC: dict[Gamut, str] = {
    Gamut.SRGB: "sRGB",
    Gamut.P3: "Display P3",
    Gamut.BT2020: "Rec. 2020",
}

_DESC_TAG_SIZE = 101


def _create_apple_desc_tag(label: str) -> bytes:
    """Apple/LR ``desc`` type 11：固定 101B，ASCII 名称 + 零填充。"""
    if len(label) > _DESC_TAG_SIZE - 13:
        raise ValueError(f"baseline desc 过长: {label!r}")
    body = bytearray(_DESC_TAG_SIZE)
    struct.pack_into(">4sI", body, 0, b"desc", 0)
    struct.pack_into(">I", body, 8, 11)
    text = label.encode("ascii", errors="replace") + b"\x00"
    body[12 : 12 + len(text)] = text
    return bytes(body)


def _create_apple_header() -> bytearray:
    rest = _apple_icc_header_rest()
    header = bytearray(128)
    header[4:128] = rest
    return header


def _append_tag_table_entry(table: bytearray, sig: str, offset: int, size: int) -> None:
    _append_tag(table, sig)
    _append_u32(table, offset)
    _append_u32(table, size)


@lru_cache(maxsize=4)
def create_apple_baseline_icc_profile(gamut: Gamut) -> bytes:
    """
    生成与 Lightroom Ultra HDR baseline 同结构的 ICC。

    仅 ``desc`` 与 ``r/g/bXYZ`` 随 ``gamut`` 变化；TRC/chad/wtpt/cprt/头信息与 Apple 参考一致。
    """
    prim = gamut_to_primaries(gamut)
    (rx, ry), (gx, gy), (bx, by) = _PRIMARIES[prim]
    wx, wy = _D65
    rgb_matrix = _primaries_to_xyz_d50(rx, ry, gx, gy, bx, by, wx, wy)

    header = _create_apple_header()
    tag_table = bytearray()
    tag_data = bytearray()
    tag_base = len(header) + 4 + 10 * 12

    def place(blob: bytes) -> tuple[int, int]:
        offset = tag_base + len(tag_data)
        tag_data.extend(blob)
        rem = len(blob) % 4
        if rem:
            tag_data.extend(b"\x00" * (4 - rem))
        return offset, len(blob)

    desc_off, desc_sz = place(_create_apple_desc_tag(_BASELINE_DESC[gamut]))
    cprt_off, cprt_sz = place(_APPLE_CPRT_TAG)
    wtpt_off, wtpt_sz = place(_APPLE_WTPT_TAG)

    r_off, r_sz = place(
        _create_xyz_tag(float(rgb_matrix[0, 0]), float(rgb_matrix[1, 0]), float(rgb_matrix[2, 0]))
    )
    g_off, g_sz = place(
        _create_xyz_tag(float(rgb_matrix[0, 1]), float(rgb_matrix[1, 1]), float(rgb_matrix[2, 1]))
    )
    b_off, b_sz = place(
        _create_xyz_tag(float(rgb_matrix[0, 2]), float(rgb_matrix[1, 2]), float(rgb_matrix[2, 2]))
    )
    trc_off, trc_sz = place(_APPLE_TRC_TAG)
    chad_off, chad_sz = place(_APPLE_CHAD_TAG)

    _append_u32(tag_table, 10)
    _append_tag_table_entry(tag_table, "desc", desc_off, desc_sz)
    _append_tag_table_entry(tag_table, "cprt", cprt_off, cprt_sz)
    _append_tag_table_entry(tag_table, "wtpt", wtpt_off, wtpt_sz)
    _append_tag_table_entry(tag_table, "rXYZ", r_off, r_sz)
    _append_tag_table_entry(tag_table, "gXYZ", g_off, g_sz)
    _append_tag_table_entry(tag_table, "bXYZ", b_off, b_sz)
    _append_tag_table_entry(tag_table, "rTRC", trc_off, trc_sz)
    _append_tag_table_entry(tag_table, "chad", chad_off, chad_sz)
    _append_tag_table_entry(tag_table, "bTRC", trc_off, trc_sz)
    _append_tag_table_entry(tag_table, "gTRC", trc_off, trc_sz)

    icc = bytearray(header) + tag_table + tag_data
    _write_u32(icc, 0, len(icc))
    return bytes(icc)
