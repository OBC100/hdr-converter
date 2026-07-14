"""HEIF Direct 解码 → SourceImage（NCLX + pillow-heif）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..cicp import Gamut, TransferCurve
from ..transfer_decode import encoded_to_display_linear
from ._common import cicp_from_nclx_dict, samples_to_unit_signal, source_image_from_display_linear


class HEIFDecodeError(RuntimeError):
    pass


def is_heif_supported() -> bool:
    try:
        import pillow_heif  # noqa: F401

        return True
    except ImportError:
        return False


def decode_heif_to_source_image(path: str | Path) -> SourceImage:
    if not is_heif_supported():
        raise HEIFDecodeError("HEIF 解码不可用。请安装 pillow-heif")

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    import pillow_heif

    try:
        heif = pillow_heif.open_heif(path, convert_hdr_to_8bit=False)
    except Exception as exc:
        raise HEIFDecodeError(f"无法打开 HEIF: {path.name}") from exc

    # Stage D：Gain Map（在 Direct 路径前）
    try:
        from ..gainmap_demux import demux_isobmff_gainmap, result_to_source_image

        gm = demux_isobmff_gainmap(path.read_bytes(), is_avif=False, gamut=Gamut.BT2020)
        if gm is not None:
            return result_to_source_image(gm)
    except Exception:
        pass

    info = heif.info or {}
    gamut = Gamut.SRGB
    curve = TransferCurve.SRGB
    nclx = info.get("nclx_profile")
    if nclx:
        try:
            gamut, curve = cicp_from_nclx_dict(nclx)
        except ValueError:
            pass

    bit_depth_hint = info.get("bit_depth")
    try:
        pixels = np.asarray(heif)
    except Exception as exc:
        raise HEIFDecodeError(f"无法解码 HEIF 像素: {path.name}") from exc

    if pixels.ndim != 3 or pixels.shape[-1] < 3:
        raise HEIFDecodeError(f"意外的 HEIF 形状: {pixels.shape}")

    signal = samples_to_unit_signal(
        pixels[..., :3],
        bit_depth_hint=int(bit_depth_hint) if bit_depth_hint else None,
    )
    linear = encoded_to_display_linear(signal, curve)
    alpha = None
    if pixels.shape[-1] >= 4:
        alpha = samples_to_unit_signal(
            pixels[..., 3:4],
            bit_depth_hint=int(bit_depth_hint) if bit_depth_hint else None,
        )[..., 0]

    return source_image_from_display_linear(linear, gamut, curve, alpha=alpha)
