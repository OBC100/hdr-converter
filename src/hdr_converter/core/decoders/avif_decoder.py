"""AVIF Direct 解码 → SourceImage（NCLX + imagecodecs）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..cicp import Gamut, TransferCurve, cicp_to_gamut_curve
from ..isobmff_gainmap import parse_single_image_item
from ..transfer_decode import encoded_to_display_linear
from ._common import parse_nclx_from_colr_box, samples_to_unit_signal, source_image_from_display_linear


class AVIFDecodeError(RuntimeError):
    pass


def is_avif_supported() -> bool:
    try:
        import imagecodecs

        return bool(getattr(imagecodecs.AVIF, "available", False))
    except ImportError:
        return False


def decode_avif_to_source_image(path: str | Path) -> SourceImage:
    if not is_avif_supported():
        raise AVIFDecodeError("AVIF 解码不可用（imagecodecs.AVIF 未链接）")

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    data = path.read_bytes()

    # Stage D：Gain Map
    try:
        from ..gainmap_demux import demux_isobmff_gainmap, result_to_source_image

        gm = demux_isobmff_gainmap(data, is_avif=True, gamut=Gamut.BT2020)
        if gm is not None:
            return result_to_source_image(gm)
    except Exception:
        pass

    gamut = Gamut.SRGB
    curve = TransferCurve.SRGB
    bit_depth_hint: int | None = None

    try:
        item = parse_single_image_item(data)
        bit_depth_hint = item.bit_depth
        if item.colr is not None:
            cicp = parse_nclx_from_colr_box(item.colr)
            if cicp is not None:
                gamut, curve = cicp_to_gamut_curve(
                    cicp.color_primaries,
                    cicp.transfer_characteristics,
                    cicp.matrix_coefficients,
                )
    except ValueError:
        pass

    import imagecodecs

    try:
        pixels = np.asarray(imagecodecs.avif_decode(data))
    except Exception as exc:
        raise AVIFDecodeError(f"无法解码 AVIF: {path.name}") from exc

    if pixels.ndim != 3 or pixels.shape[-1] < 3:
        raise AVIFDecodeError(f"意外的 AVIF 形状: {pixels.shape}")

    signal = samples_to_unit_signal(pixels[..., :3], bit_depth_hint=bit_depth_hint)
    linear = encoded_to_display_linear(signal, curve)
    return source_image_from_display_linear(linear, gamut, curve)
