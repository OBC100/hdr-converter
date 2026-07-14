"""JPEG XL Gain Map：ISO/IEC 18181-2 ``jhgm`` 盒 + ISO 21496-1 元数据。

布局对齐 libjxl ``JxlGainMapWriteBundle``（见 ``jxl/gain_map.h``）：

```
jhgm_version          u8
gain_map_metadata_size u16 BE
gain_map_metadata     ISO 21496-1（与 JPG APP2 载荷同形，无 URN）
color_encoding_size   u8（0 = 无）
[color_encoding…]
alt_icc_size          u32 BE（0 = 无）
[alt_icc…]
gain_map              JPEG XL 裸码流（``FF 0A``…）
```

主图为含 ``jxlc`` 的 ISOBMFF 容器（SDR 基础图）；``jhgm`` 追加在文件末尾。
"""

from __future__ import annotations

import struct

import numpy as np

from .cicp import Gamut, TransferCurve
from .color_metadata import get_direct_cicp_for_jxl, jpegxl_encode_kwargs
from .gainmap_math import GainmapMetadata, encode_iso_gainmap_metadata
from .hdr_options import DEFAULT_JXL_EFFORT
from .sample_bits import right_align_uint16

_JXL_SIGNATURE = bytes.fromhex("0000000c4a584c200d0a870a")
_JHGM_VERSION = 0


def _box(box_type: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    if size > 0xFFFFFFFF:
        raise ValueError(f"box {box_type!r} 过大: {size}")
    return struct.pack(">I", size) + box_type + payload


def _require_jpegxl():
    import imagecodecs

    if not getattr(imagecodecs.JPEGXL, "available", False):
        raise RuntimeError("imagecodecs.JPEGXL 不可用（未链接 libjxl）")
    return imagecodecs


def encode_jxl_image_bytes(
    pixels: np.ndarray,
    *,
    bit_depth: int,
    quality: int,
    gamut: Gamut,
    curve: TransferCurve,
    effort: int | None = None,
    usecontainer: bool = True,
) -> bytes:
    """单图 JPEG XL 编码（Direct / Gain Map 基础层共用）。

    容器模式下在 ``ftyp`` 后写入 ``colr``/``nclx``，供解码器读回 CICP
    （码流内 ColourEncoding 位打包，imagecodecs 未暴露读接口）。
    """
    imagecodecs = _require_jpegxl()
    if pixels.dtype == np.uint16 or bit_depth > 8:
        px = right_align_uint16(pixels.astype(np.uint16), bit_depth)
        bps = bit_depth
    else:
        px = pixels.astype(np.uint8)
        bps = 8

    kwargs = jpegxl_encode_kwargs(
        gamut,
        curve,
        level=quality,
        bitspersample=bps,
        effort=effort,
        usecontainer=usecontainer,
    )
    encoded = imagecodecs.jpegxl_encode(px, **kwargs)
    if usecontainer:
        from .isobmff_gainmap import _nclx_colr_box

        # JXL P3 对外仍用 H.273 cp=12 写 colr，便于通用反查；matrix 经
        # get_direct_cicp_for_jxl 修正（identity(0) 会导致 Windows 照片等
        # 阅读器把宽色域 RGB 误读偏色，与 HEIF/AVIF 同一坑，见该函数注释）
        cicp = get_direct_cicp_for_jxl(gamut, curve)
        encoded = _inject_box_after_ftyp(encoded, _nclx_colr_box(cicp))
    return encoded


def _inject_box_after_ftyp(data: bytes, box: bytes) -> bytes:
    """在 ISOBMFF ``ftyp`` 之后插入一个顶层 box（已存在同 type 则跳过）。"""
    i = 0
    parts: list[bytes] = []
    inserted = False
    box_type = box[4:8]
    while i + 8 <= len(data):
        size = struct.unpack(">I", data[i : i + 4])[0]
        if size < 8 or i + size > len(data):
            break
        typ = data[i + 4 : i + 8]
        if typ == box_type:
            return data  # 已有
        parts.append(data[i : i + size])
        i += size
        if typ == b"ftyp" and not inserted:
            parts.append(box)
            inserted = True
    if not inserted:
        return data
    if i < len(data):
        parts.append(data[i:])
    return b"".join(parts)


def encode_jxl_base_bytes(
    sdr_pixels: np.ndarray,
    *,
    bit_depth: int,
    quality: int,
    gamut: Gamut,
    effort: int | None = None,
) -> bytes:
    """Gain Map SDR 基础图：目标基色 + sRGB 传递，ISOBMFF 容器。"""
    return encode_jxl_image_bytes(
        sdr_pixels,
        bit_depth=bit_depth,
        quality=quality,
        gamut=gamut,
        curve=TransferCurve.SRGB,
        effort=effort,
        usecontainer=True,
    )


def encode_jxl_gain_bytes(
    gain: np.ndarray,
    *,
    bit_depth: int,
    quality: int,
    multichannel: bool,
    effort: int | None = None,
) -> bytes:
    """增益图像素 → 裸码流（mono=灰度，color=RGB）。"""
    imagecodecs = _require_jpegxl()
    if multichannel and gain.ndim == 3 and gain.shape[2] >= 3:
        px = gain[..., :3]
        photometric = "rgb"
    else:
        px = gain[..., 0] if gain.ndim == 3 else gain
        photometric = "gray"

    if px.dtype == np.uint16 or bit_depth > 8:
        px = right_align_uint16(px.astype(np.uint16), bit_depth)
        bps = bit_depth
    elif np.issubdtype(px.dtype, np.floating):
        px = (np.clip(px.astype(np.float32), 0.0, 1.0) * 255.0).astype(np.uint8)
        bps = 8
    else:
        px = px.astype(np.uint8)
        bps = 8

    return imagecodecs.jpegxl_encode(
        px,
        level=quality,
        effort=DEFAULT_JXL_EFFORT if effort is None else effort,
        photometric=photometric,
        bitspersample=bps,
        usecontainer=False,
    )


def jxl_naked_codestream(data: bytes) -> bytes:
    """从容器或裸码流提取 ``jxlc`` 载荷（增益图须为裸码流）。"""
    if len(data) >= 2 and data[0] == 0xFF and data[1] == 0x0A:
        return data
    if not data.startswith(_JXL_SIGNATURE):
        raise ValueError("不是 JPEG XL 容器或裸码流")
    offset = 0
    while offset + 8 <= len(data):
        size = struct.unpack_from(">I", data, offset)[0]
        typ = data[offset + 4 : offset + 8]
        hdr = 8
        if size == 1:
            if offset + 16 > len(data):
                break
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            hdr = 16
        elif size == 0:
            size = len(data) - offset
        if size < hdr or offset + size > len(data):
            break
        if typ == b"jxlc":
            return data[offset + hdr : offset + size]
        if typ == b"jxlp":
            body = data[offset + hdr : offset + size]
            if len(body) >= 4:
                return body[4:]
            return body
        offset += size
    raise ValueError("JPEG XL 容器中未找到 jxlc/jxlp 码流")


def build_jhgm_bundle(
    metadata: GainmapMetadata,
    gain_codestream: bytes,
    *,
    alt_icc: bytes = b"",
) -> bytes:
    """序列化 ``jhgm`` 盒载荷（不含 box 头）。"""
    meta = encode_iso_gainmap_metadata(metadata)
    if len(meta) > 0xFFFF:
        raise ValueError(f"gain map metadata 过长: {len(meta)}")
    naked = jxl_naked_codestream(gain_codestream)
    out = bytearray()
    out.append(_JHGM_VERSION)
    out.extend(struct.pack(">H", len(meta)))
    out.extend(meta)
    out.append(0)  # color_encoding_size = 0（无 Bundle 色彩编码）
    out.extend(struct.pack(">I", len(alt_icc)))
    if alt_icc:
        out.extend(alt_icc)
    out.extend(naked)
    return bytes(out)


def mux_jxl_gainmap(
    base_container: bytes,
    gain_codestream: bytes,
    metadata: GainmapMetadata,
    *,
    alt_icc: bytes = b"",
) -> bytes:
    """将 ``jhgm`` 盒追加到 SDR 基础图 JXL 容器。"""
    if not base_container.startswith(_JXL_SIGNATURE):
        raise ValueError("基础图须为 JPEG XL ISOBMFF 容器（usecontainer=True）")
    bundle = build_jhgm_bundle(metadata, gain_codestream, alt_icc=alt_icc)
    return base_container + _box(b"jhgm", bundle)


def parse_jhgm_bundle(bundle: bytes) -> tuple[bytes, bytes]:
    """解析 ``jhgm`` 载荷 → (ISO 21496 metadata, naked gain codestream)。"""
    if len(bundle) < 1 + 2 + 1 + 4:
        raise ValueError("jhgm bundle 过短")
    offset = 0
    version = bundle[offset]
    offset += 1
    if version != _JHGM_VERSION:
        raise ValueError(f"不支持的 jhgm_version: {version}")
    meta_size = struct.unpack_from(">H", bundle, offset)[0]
    offset += 2
    meta = bundle[offset : offset + meta_size]
    offset += meta_size
    color_size = bundle[offset]
    offset += 1
    offset += color_size
    icc_size = struct.unpack_from(">I", bundle, offset)[0]
    offset += 4
    offset += icc_size
    gain = bundle[offset:]
    if not gain.startswith(b"\xff\n"):
        raise ValueError("jhgm 内增益图不是裸 JPEG XL 码流")
    return meta, gain
