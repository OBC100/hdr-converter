"""直通优化：输入输出格式与色彩参数一致时字节拷贝。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .cicp import Gamut, TransferCurve, cicp_to_gamut_curve
from .encoders.base import OutputFormat
from .format_detect import InputFormat, detect_format
from .hdr_options import HdrDeliveryMode, resolve_hdr_delivery, uses_gainmap

if TYPE_CHECKING:
    from .converter import ConvertSettings


_FMT_MAP: dict[InputFormat, OutputFormat] = {
    InputFormat.PNG: OutputFormat.PNG,
    InputFormat.JPEG: OutputFormat.JPG,
    InputFormat.AVIF: OutputFormat.AVIF,
    InputFormat.HEIF: OutputFormat.HEIF,
    InputFormat.JXL: OutputFormat.JXL,
}


def _read_png_cicp_bits(path: Path) -> tuple[Gamut, TransferCurve, int] | None:
    from .cicp import CICP
    from .decoders.png_decoder import _find_chunk, _parse_ihdr, iter_png_chunks

    try:
        data = path.read_bytes()
        chunks = iter_png_chunks(data)
        ihdr = _find_chunk(chunks, b"IHDR")
        if ihdr is None:
            return None
        _, _, bit_depth, _ = _parse_ihdr(ihdr.data)
        cicp_chunk = _find_chunk(chunks, b"cICP")
        if cicp_chunk is None or len(cicp_chunk.data) < 4:
            return Gamut.SRGB, TransferCurve.SRGB, bit_depth
        cicp = CICP.from_bytes(cicp_chunk.data[:4])
        mapped = cicp_to_gamut_curve(
            cicp.color_primaries,
            cicp.transfer_characteristics,
            cicp.matrix_coefficients,
        )
        if mapped is None:
            return None
        gamut, curve = mapped
        return gamut, curve, bit_depth
    except Exception:
        return None


def _read_isobmff_nclx(path: Path) -> tuple[Gamut, TransferCurve, int] | None:
    from .decoders._common import parse_nclx_colr_payload, parse_nclx_from_colr_box
    from .isobmff_gainmap import parse_single_image_item

    try:
        item = parse_single_image_item(path.read_bytes())
        if item.colr is None:
            return None
        raw = item.colr
        if len(raw) >= 8 and raw[4:8] == b"colr":
            cicp = parse_nclx_from_colr_box(raw)
        else:
            cicp = parse_nclx_colr_payload(raw)
        if cicp is None:
            return None
        mapped = cicp_to_gamut_curve(
            cicp.color_primaries,
            cicp.transfer_characteristics,
            cicp.matrix_coefficients,
        )
        if mapped is None:
            return None
        return mapped[0], mapped[1], item.bit_depth
    except Exception:
        return None


def _probe_source_params(
    path: Path, fmt: InputFormat
) -> tuple[Gamut, TransferCurve, int] | None:
    if fmt == InputFormat.PNG:
        return _read_png_cicp_bits(path)
    if fmt in (InputFormat.AVIF, InputFormat.HEIF, InputFormat.JXL):
        return _read_isobmff_nclx(path)
    # JPEG / JXR：不做直通（JPEG Gain Map 参数难对齐；JXR 无对称输出）
    return None


def can_passthrough(input_path: str | Path, settings: ConvertSettings) -> bool:
    """保守判断：仅 Direct 且格式/色域/曲线/位深一致时允许字节拷贝。"""
    input_path = Path(input_path)

    fmt = detect_format(input_path)
    out_fmt = _FMT_MAP.get(fmt)
    if out_fmt is None or out_fmt != settings.output_format:
        return False

    delivery = resolve_hdr_delivery(
        settings.output_format, settings.curve, settings.hdr_delivery
    )
    # Gain Map / 有损重编码路径：不直通（参数表面相同也不保证字节可复用）
    if uses_gainmap(delivery):
        return False
    if fmt == InputFormat.JPEG:
        return False

    probed = _probe_source_params(input_path, fmt)
    if probed is None:
        return False
    gamut, curve, bits = probed
    if gamut != settings.gamut or curve != settings.curve:
        return False

    want_bits = settings.quantize_bits
    if want_bits is None and fmt != InputFormat.PNG:
        want_bits = settings.base_bits
    if want_bits is not None and int(want_bits) != int(bits):
        # PNG 常把 10/12/14-bit 信号左对齐存进 16-bit IHDR；位深以语义参数为准时允许直通
        if not (fmt == InputFormat.PNG and int(bits) == 16 and int(want_bits) in (8, 10, 12, 14, 16)):
            return False

    return True


def try_passthrough(
    input_path: Path, output_path: Path, settings: ConvertSettings
) -> bool:
    """若可直通则拷贝并返回 True。"""
    if not can_passthrough(input_path, settings):
        return False
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if input_path.resolve() == output_path.resolve():
        return True
    output_path.write_bytes(input_path.read_bytes())
    return True
