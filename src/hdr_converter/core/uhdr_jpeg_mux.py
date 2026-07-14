"""Ultra HDR JPEG 原生容器拼装（ISO 21496-1 + MPF）。"""

from __future__ import annotations

import struct

from .gainmap_math import GainmapMetadata, encode_iso_gainmap_metadata

_ISO_NS = b"urn:iso:std:iso:ts:21496:-1\x00"

_MPF_SIG = b"MPF\x00"
_MPF_ENDIAN = b"MM\x00\x2a"
_MPF_ATTR_PRIMARY = 0x00030000
_MPF_ATTR_SECONDARY = 0x00000000


def calculate_mpf_payload_size() -> int:
    return 86


def generate_mpf(
    primary_image_size: int,
    primary_image_offset: int,
    secondary_image_size: int,
    secondary_image_offset: int,
) -> bytes:
    """生成 MPF APP2 载荷（对齐 multipictureformat.cpp generateMpf）。"""
    out = bytearray()
    out.extend(_MPF_SIG)
    out.extend(_MPF_ENDIAN)
    out.extend(struct.pack(">I", 8))  # Index IFD offset
    out.extend(struct.pack(">H", 3))

    out.extend(struct.pack(">HHI", 0xB000, 0x0007, 4))
    out.extend(b"0100")
    out.extend(struct.pack(">HHI", 0xB001, 0x0004, 1))
    out.extend(struct.pack(">I", 2))
    out.extend(struct.pack(">HHI", 0xB002, 0x0007, 32))

    mp_entry_offset = len(out) - len(_MPF_SIG) + 4 + 4
    out.extend(struct.pack(">I", mp_entry_offset))
    out.extend(struct.pack(">I", 0))

    out.extend(struct.pack(">III", _MPF_ATTR_PRIMARY, primary_image_size, primary_image_offset))
    out.extend(struct.pack(">HH", 0, 0))
    out.extend(struct.pack(">III", _MPF_ATTR_SECONDARY, secondary_image_size, secondary_image_offset))
    out.extend(struct.pack(">HH", 0, 0))
    return bytes(out)


def _app2_segment(payload: bytes) -> bytes:
    return b"\xff\xe2" + struct.pack(">H", len(payload) + 2) + payload


def mux_ultra_hdr_jpeg(
    base_jpeg: bytes,
    gainmap_jpeg: bytes,
    metadata: GainmapMetadata,
) -> bytes:
    """
    将 baseline JPEG 与增益图 JPEG 合成为 Ultra HDR JPEG/R。

    对齐 libultrahdr ``JpegR::appendGainMap``（ISO 21496-1 模式，无 XMP）。
    """
    if base_jpeg[:2] != b"\xff\xd8" or gainmap_jpeg[:2] != b"\xff\xd8":
        raise ValueError("输入须为完整 JPEG（以 SOI 开头）")

    primary_body = base_jpeg[2:]
    gain_body = gainmap_jpeg[2:]
    iso_secondary = encode_iso_gainmap_metadata(metadata)

    iso_primary_seg = _app2_segment(_ISO_NS + b"\x00\x00\x00\x00")
    mpf_seg_len = 2 + calculate_mpf_payload_size()

    pos = 2 + len(iso_primary_seg)
    iso_secondary_payload = _ISO_NS + iso_secondary
    iso_secondary_seg_total = 4 + len(iso_secondary_payload)
    secondary_image_size = 2 + iso_secondary_seg_total + len(gain_body)

    primary_image_size = pos + mpf_seg_len + len(base_jpeg)
    secondary_image_offset = primary_image_size - pos - 8

    mpf_seg = _app2_segment(
        generate_mpf(primary_image_size, 0, secondary_image_size, secondary_image_offset)
    )

    out = bytearray()
    out.extend(b"\xff\xd8")
    out.extend(iso_primary_seg)
    out.extend(mpf_seg)
    out.extend(primary_body)
    out.extend(b"\xff\xd8")
    out.extend(_app2_segment(iso_secondary_payload))
    out.extend(gain_body)
    return bytes(out)


def demux_ultra_hdr_jpeg(data: bytes) -> tuple[bytes, bytes, GainmapMetadata] | None:
    """拆 Ultra HDR JPEG → (base_jpeg, gain_jpeg, metadata)；非 UHDR 返回 None。"""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None

    # 找第二个 SOI（增益图起点）
    second = data.find(b"\xff\xd8", 2)
    if second < 0:
        return None

    primary = data[:second]
    secondary = data[second:]

    # 从副图 APP2 提取 ISO 21496 元数据
    meta: GainmapMetadata | None = None
    gain_jpeg = bytearray(b"\xff\xd8")
    i = 2
    while i + 4 <= len(secondary):
        if secondary[i] != 0xFF:
            gain_jpeg.extend(secondary[i:])
            break
        marker = secondary[i + 1]
        if marker in (0xD8, 0xD9):
            i += 2
            continue
        if marker == 0xDA:
            gain_jpeg.extend(secondary[i:])
            break
        seglen = struct.unpack_from(">H", secondary, i + 2)[0]
        payload = secondary[i + 4 : i + 2 + seglen]
        if marker == 0xE2 and payload.startswith(_ISO_NS):
            raw = payload[len(_ISO_NS) :]
            if len(raw) >= 4:
                try:
                    from .gainmap_math import decode_iso_gainmap_metadata

                    meta = decode_iso_gainmap_metadata(raw)
                except Exception:
                    meta = None
            # 不把 ISO APP2 放进增益图 JPEG
        else:
            gain_jpeg.extend(secondary[i : i + 2 + seglen])
        i += 2 + seglen

    if meta is None:
        return None

    # 主图去掉 ISO/MPF APP2，还原可独立解码的 baseline
    base_out = bytearray(b"\xff\xd8")
    i = 2
    while i + 4 <= len(primary):
        if primary[i] != 0xFF:
            base_out.extend(primary[i:])
            break
        marker = primary[i + 1]
        if marker == 0xDA:
            base_out.extend(primary[i:])
            break
        if marker in (0xD8, 0xD9):
            i += 2
            continue
        seglen = struct.unpack_from(">H", primary, i + 2)[0]
        payload = primary[i + 4 : i + 2 + seglen]
        skip = marker == 0xE2 and (
            payload.startswith(_ISO_NS) or payload.startswith(_MPF_SIG)
        )
        if not skip:
            base_out.extend(primary[i : i + 2 + seglen])
        i += 2 + seglen

    return bytes(base_out), bytes(gain_jpeg), meta
