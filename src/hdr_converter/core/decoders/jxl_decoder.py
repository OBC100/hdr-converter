"""JXL Direct 解码 → SourceImage（容器 colr/nclx + imagecodecs）。"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from ..cicp import Gamut, TransferCurve, cicp_to_gamut_curve
from ..transfer_decode import encoded_to_display_linear
from ._common import parse_nclx_from_colr_box, samples_to_unit_signal, source_image_from_display_linear


class JXLDecodeError(RuntimeError):
    pass


def is_jxl_supported() -> bool:
    try:
        import imagecodecs

        return bool(getattr(imagecodecs.JPEGXL, "available", False))
    except ImportError:
        return False


def _iter_top_boxes(data: bytes) -> list[tuple[bytes, bytes]]:
    boxes: list[tuple[bytes, bytes]] = []
    i = 0
    while i + 8 <= len(data):
        size = struct.unpack(">I", data[i : i + 4])[0]
        if size < 8 or i + size > len(data):
            break
        typ = data[i + 4 : i + 8]
        boxes.append((typ, data[i : i + size]))
        i += size
    return boxes


def _read_cicp_from_jxl_container(data: bytes) -> tuple[Gamut, TransferCurve] | None:
    for typ, box in _iter_top_boxes(data):
        if typ != b"colr":
            continue
        cicp = parse_nclx_from_colr_box(box)
        if cicp is None:
            continue
        try:
            return cicp_to_gamut_curve(
                cicp.color_primaries,
                cicp.transfer_characteristics,
                cicp.matrix_coefficients,
            )
        except ValueError:
            continue
    return None


def decode_jxl_to_source_image(path: str | Path) -> SourceImage:
    if not is_jxl_supported():
        raise JXLDecodeError("JXL 解码不可用（imagecodecs.JPEGXL 未链接）")

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    data = path.read_bytes()
    # Stage D：jhgm Gain Map
    try:
        from ..gainmap_demux import demux_jxl_gainmap, result_to_source_image

        gm = demux_jxl_gainmap(data, gamut=Gamut.BT2020)
        if gm is not None:
            return result_to_source_image(gm)
    except Exception:
        pass

    gamut = Gamut.SRGB
    curve = TransferCurve.SRGB
    parsed = _read_cicp_from_jxl_container(data)
    if parsed is not None:
        gamut, curve = parsed

    import imagecodecs

    try:
        pixels = np.asarray(imagecodecs.jpegxl_decode(data))
    except Exception as exc:
        raise JXLDecodeError(f"无法解码 JXL: {path.name}") from exc

    if pixels.ndim != 3 or pixels.shape[-1] < 3:
        raise JXLDecodeError(f"意外的 JXL 形状: {pixels.shape}")

    signal = samples_to_unit_signal(pixels[..., :3])
    linear = encoded_to_display_linear(signal, curve)
    return source_image_from_display_linear(linear, gamut, curve)
