"""Gain Map demux：四容器 → HDR 显示线性 SourceImage 组件。"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .canonical import CANONICAL_PEAK_NITS
from .cicp import CICP, Gamut, TransferCurve, cicp_to_gamut_curve
from .gainmap_math import GainmapMetadata, apply_gainmap, decode_iso_gainmap_metadata
from .gainmap_tmap import parse_tmap_payload
from .source_image import SourceImage
from .transfer_decode import srgb_eotf

logger = logging.getLogger(__name__)


def _gamut_from_cicp(cicp: CICP | None, fallback: Gamut) -> Gamut:
    if cicp is None:
        return fallback
    try:
        gamut, _curve = cicp_to_gamut_curve(
            cicp.color_primaries, cicp.transfer_characteristics, cicp.matrix_coefficients
        )
        return gamut
    except ValueError:
        return fallback


def _base_gamut_from_item(item, fallback: Gamut) -> Gamut:
    """从基础层条目的 ``colr``（可能多条）反查真实色域；缺失/无法识别则回退。

    Gain Map demux 此前对 HEIF/AVIF/JXL 统一硬编码 ``gamut=BT2020``，忽略了
    基础层自身的 nclx 标签——当真实基础层是 sRGB/P3 时，还原出的 HDR 线性
    会被误当作已经是 BT.2020，色相因此被错误"拉宽"而偏红（与 canonical 阶段
    跳过应有的色域矩阵转换等价）。
    """
    from .decoders._common import parse_nclx_from_colr_box

    boxes = item.colr_boxes if getattr(item, "colr_boxes", None) else (
        [item.colr] if getattr(item, "colr", None) else []
    )
    for box in boxes:
        cicp = parse_nclx_from_colr_box(box)
        if cicp is None:
            continue
        try:
            gamut, _curve = cicp_to_gamut_curve(
                cicp.color_primaries, cicp.transfer_characteristics, cicp.matrix_coefficients
            )
            return gamut
        except ValueError:
            continue
    return fallback


def _base_gamut_from_jxl_container(base_data: bytes, fallback: Gamut) -> Gamut:
    """从 JXL 基础层容器顶层 ``colr``/nclx 反查真实色域（见 ``_base_gamut_from_item``）。"""
    from .decoders._common import parse_nclx_from_colr_box
    from .isobmff_gainmap import _find_box

    found = _find_box(base_data, b"colr")
    if found is None:
        return fallback
    off, size = found
    cicp = parse_nclx_from_colr_box(base_data[off : off + size])
    return _gamut_from_cicp(cicp, fallback)


@dataclass
class GainMapDemuxResult:
    """demux 中间结果。"""

    hdr_linear: np.ndarray  # 目标/基色域显示线性，1.0=10000 nits
    primaries: Gamut
    metadata: GainmapMetadata
    multichannel: bool


def _sdr_jpeg_to_linear(jpeg: bytes) -> np.ndarray:
    from PIL import Image
    import io

    with Image.open(io.BytesIO(jpeg)) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    return srgb_eotf(rgb)


def demux_uhdr_jpeg_to_hdr(data: bytes, *, gamut: Gamut = Gamut.SRGB) -> GainMapDemuxResult | None:
    from .uhdr_jpeg_mux import demux_ultra_hdr_jpeg

    parts = demux_ultra_hdr_jpeg(data)
    if parts is None:
        return None
    base_jpeg, gain_jpeg, meta = parts
    sdr = _sdr_jpeg_to_linear(base_jpeg)
    from PIL import Image
    import io

    with Image.open(io.BytesIO(gain_jpeg)) as im:
        gain = np.asarray(im.convert("RGB" if im.mode != "L" else "L"))
    multi = gain.ndim == 3 and gain.shape[-1] >= 3
    if gain.ndim == 3 and gain.shape[-1] >= 3 and not multi:
        gain = gain[..., 0]
    hdr = apply_gainmap(sdr, gain, meta, gamut=gamut)
    return GainMapDemuxResult(hdr_linear=hdr, primaries=gamut, metadata=meta, multichannel=multi)


def _parse_all_infe(data: bytes) -> dict[int, bytes]:
    from .isobmff_gainmap import _find_box

    iinf = _find_box(data, b"iinf")
    if iinf is None:
        raise ValueError("缺少 iinf")
    body = data[iinf[0] + 8 : iinf[0] + iinf[1]]
    entry_count = struct.unpack(">H", body[4:6])[0]
    pos = 6
    out: dict[int, bytes] = {}
    for _ in range(entry_count):
        size = struct.unpack(">I", body[pos : pos + 4])[0]
        raw = body[pos : pos + size]
        version = raw[8]
        if version >= 2:
            item_id = struct.unpack(">H", raw[12:14])[0]
            item_type = raw[16:20]
        else:
            item_id = struct.unpack(">H", raw[12:14])[0]
            item_type = raw[14:18]
        out[item_id] = item_type
        pos += size
    return out


def _parse_iref_dimg(data: bytes) -> dict[int, list[int]]:
    """from_id → [to_ids] for dimg refs."""
    from .isobmff_gainmap import _find_box, _child_boxes

    iref = _find_box(data, b"iref")
    if iref is None:
        return {}
    # iref is FullBox; children start after version/flags
    start = iref[0] + 12
    end = iref[0] + iref[1]
    mapping: dict[int, list[int]] = {}
    off = start
    while off + 8 <= end:
        size = struct.unpack(">I", data[off : off + 4])[0]
        typ = data[off + 4 : off + 8]
        if size < 8:
            break
        if typ == b"dimg" and size >= 12:
            body = data[off + 8 : off + size]
            from_id = struct.unpack(">H", body[0:2])[0]
            count = struct.unpack(">H", body[2:4])[0]
            tos = [
                struct.unpack(">H", body[4 + 2 * i : 6 + 2 * i])[0]
                for i in range(count)
            ]
            mapping[from_id] = tos
        off += size
    return mapping


def demux_isobmff_gainmap(
    data: bytes,
    *,
    is_avif: bool,
    gamut: Gamut = Gamut.BT2020,
) -> GainMapDemuxResult | None:
    """HEIF/AVIF Gain Map demux。"""
    from .isobmff_gainmap import build_single_image_isobmff, extract_gainmap_items
    from .decoders._common import samples_to_unit_signal

    extracted = extract_gainmap_items(data)
    if extracted is None:
        return None
    base_item, gain_item, tmap_payload = extracted
    try:
        meta = parse_tmap_payload(tmap_payload)
    except Exception as exc:
        logger.debug("tmap parse failed: %s", exc)
        return None

    brands = [b"avif", b"mif1"] if is_avif else [b"heic", b"mif1"]
    base_file = build_single_image_isobmff(base_item, brands=brands)
    gain_file = build_single_image_isobmff(gain_item, brands=brands)

    try:
        if is_avif:
            import imagecodecs

            base_px = np.asarray(imagecodecs.avif_decode(base_file))
            gain_px = np.asarray(imagecodecs.avif_decode(gain_file))
        else:
            import pillow_heif

            bh = pillow_heif.open_heif(base_file, convert_hdr_to_8bit=False)
            gh = pillow_heif.open_heif(gain_file, convert_hdr_to_8bit=False)
            base_px = np.asarray(bh)
            gain_px = np.asarray(gh)
    except Exception as exc:
        logger.debug("isobmff gainmap pixel decode failed: %s", exc)
        return None

    from .decoders._common import samples_to_unit_signal

    if base_px.dtype == np.uint8:
        sdr_rgb = base_px[..., :3].astype(np.float32) / 255.0
    else:
        sdr_rgb = samples_to_unit_signal(
            base_px[..., :3], bit_depth_hint=base_item.bit_depth
        )
    sdr = srgb_eotf(np.clip(sdr_rgb, 0, 1))

    if gain_px.dtype != np.uint8:
        # 右对齐 8-bit gain 常见
        gmax = int(gain_px.max()) if gain_px.size else 0
        if gmax <= 255:
            gain_u8 = gain_px.astype(np.uint8)
        else:
            gain_u8 = (samples_to_unit_signal(gain_px) * 255.0).astype(np.uint8)
    else:
        gain_u8 = gain_px

    multi = gain_u8.ndim == 3 and gain_u8.shape[-1] >= 3
    if not multi and gain_u8.ndim == 3:
        gain_u8 = gain_u8[..., 0]
    actual_gamut = _base_gamut_from_item(base_item, gamut)
    hdr = apply_gainmap(sdr, gain_u8, meta, gamut=actual_gamut)
    return GainMapDemuxResult(
        hdr_linear=hdr, primaries=actual_gamut, metadata=meta, multichannel=multi
    )


def demux_jxl_gainmap(data: bytes, *, gamut: Gamut = Gamut.BT2020) -> GainMapDemuxResult | None:
    from .jxl_gainmap import parse_jhgm_bundle, _JXL_SIGNATURE
    import imagecodecs

    if not data.startswith(_JXL_SIGNATURE):
        return None
    # find jhgm box
    i = 0
    jhgm = None
    while i + 8 <= len(data):
        size = struct.unpack(">I", data[i : i + 4])[0]
        typ = data[i + 4 : i + 8]
        if size < 8 or i + size > len(data):
            break
        if typ == b"jhgm":
            jhgm = data[i + 8 : i + size]
            break
        i += size
    if jhgm is None:
        return None
    try:
        meta_bytes, gain_cs = parse_jhgm_bundle(jhgm)
        meta = decode_iso_gainmap_metadata(meta_bytes)
    except Exception as exc:
        logger.debug("jhgm parse failed: %s", exc)
        return None

    # base = container without jhgm
    base_data = data[:i] if jhgm is not None else data
    try:
        base = np.asarray(imagecodecs.jpegxl_decode(base_data))
        gain = np.asarray(imagecodecs.jpegxl_decode(gain_cs))
    except Exception as exc:
        logger.debug("jxl gainmap pixel decode failed: %s", exc)
        return None

    if base.dtype == np.uint8:
        sdr_rgb = base[..., :3].astype(np.float32) / 255.0
    else:
        sdr_rgb = base[..., :3].astype(np.float32) / max(float(base.max()), 1.0)
    sdr = srgb_eotf(np.clip(sdr_rgb, 0, 1))
    multi = gain.ndim == 3 and gain.shape[-1] >= 3
    gain_u8 = gain.astype(np.uint8)
    if not multi and gain.ndim == 3:
        gain_u8 = gain_u8[..., 0]
    actual_gamut = _base_gamut_from_jxl_container(base_data, gamut)
    hdr = apply_gainmap(sdr, gain_u8, meta, gamut=actual_gamut)
    return GainMapDemuxResult(
        hdr_linear=hdr, primaries=actual_gamut, metadata=meta, multichannel=multi
    )


def result_to_source_image(result: GainMapDemuxResult) -> SourceImage:
    return SourceImage(
        linear=result.hdr_linear.astype(np.float32, copy=False),
        primaries=result.primaries,
        reference_white_nits=CANONICAL_PEAK_NITS,
        is_hdr=True,
        embedded_gainmap=result.metadata,
    )
