"""PNG 轻量 chunk 遍历与 HDR/SDR 解码 → SourceImage。"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..canonical import SDR_REFERENCE_WHITE_NITS
from ..cicp import CICP, ContentLightLevel, Gamut, TransferCurve
from ..named_colourspaces import (
    ColourSpaceDescriptor,
    cicp_to_primaries_like,
    parse_icc_to_descriptor,
)
from ..source_image import SourceImage
from ..transfer_decode import (
    encoded_to_display_linear,
    encoded_to_linear_via_colourspace,
)
from ._common import reference_for_transfer, samples_to_unit_signal

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PNGDecodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class PngChunk:
    type: bytes
    data: bytes
    offset: int


def is_png_supported() -> bool:
    try:
        import imagecodecs  # noqa: F401

        return True
    except ImportError:
        pass
    try:
        from PIL import Image  # noqa: F401

        return True
    except ImportError:
        return False


def iter_png_chunks(data: bytes) -> list[PngChunk]:
    """遍历 PNG chunk（校验 CRC）；定位 cICP / iCCP / cLLi / IHDR / IDAT 等。"""
    if not data.startswith(PNG_SIGNATURE):
        raise PNGDecodeError("不是有效的 PNG 文件（签名不匹配）")
    chunks: list[PngChunk] = []
    i = len(PNG_SIGNATURE)
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i : i + 4])[0]
        ctype = data[i + 4 : i + 8]
        start = i + 8
        end = start + length
        if end + 4 > len(data):
            raise PNGDecodeError(f"chunk {ctype!r} 越界")
        payload = data[start:end]
        crc_file = struct.unpack(">I", data[end : end + 4])[0]
        crc_calc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
        if crc_file != crc_calc:
            raise PNGDecodeError(
                f"chunk {ctype!r} CRC 不匹配: file={crc_file:#x} calc={crc_calc:#x}"
            )
        chunks.append(PngChunk(type=ctype, data=payload, offset=i))
        i = end + 4
        if ctype == b"IEND":
            break
    return chunks


def _find_chunk(chunks: list[PngChunk], name: bytes) -> PngChunk | None:
    for c in chunks:
        if c.type == name:
            return c
    return None


def _parse_ihdr(data: bytes) -> tuple[int, int, int, int]:
    if len(data) < 13:
        raise PNGDecodeError("IHDR 过短")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", data[:10])
    return width, height, bit_depth, color_type


def _dequant_to_unit(arr: np.ndarray, bit_depth: int) -> np.ndarray:
    """整数像素 → [0,1] 信号。

    IHDR≤8：按 8-bit；IHDR 为 16 时可能是左对齐 10/12/14，交给
    ``samples_to_unit_signal`` 启发式（与 AVIF/HEIF/JXL 同一套）。
    """
    if bit_depth <= 8:
        return samples_to_unit_signal(arr, bit_depth_hint=8)
    return samples_to_unit_signal(arr, bit_depth_hint=None)


def _load_rgb_array(path: Path) -> tuple[np.ndarray, int]:
    """解码像素；优先 imagecodecs 保留 16-bit，回退 Pillow。"""
    data = path.read_bytes()
    try:
        import imagecodecs

        arr = np.asarray(imagecodecs.png_decode(data))
        if arr.ndim == 2:
            arr = np.stack([arr, arr, arr], axis=-1)
        if arr.dtype == np.uint16:
            return arr, 16
        if arr.dtype == np.uint8:
            return arr, 8
        # float / 其它：缩放到 uint16 语义由调用方处理前先归一
        if np.issubdtype(arr.dtype, np.floating):
            u16 = np.clip(np.round(arr * 65535.0), 0, 65535).astype(np.uint16)
            return u16, 16
    except Exception:
        pass

    if not is_png_supported():
        raise PNGDecodeError(
            "PNG 解码不可用。请安装 imagecodecs 或 Pillow。"
        )
    from PIL import Image

    with Image.open(path) as im:
        # 16-bit PNG：Pillow 默认可能降为 8-bit，尽量用原始模式
        im.load()
        arr = np.asarray(im)
        if arr.dtype == np.uint16:
            return arr, 16
        if im.mode not in ("RGB", "RGBA"):
            arr = np.asarray(im.convert("RGBA" if "A" in im.mode else "RGB"))
        return arr.astype(np.uint8, copy=False), 8


def _extract_iccp(iccp_chunk: PngChunk | None) -> bytes | None:
    if iccp_chunk is None:
        return None
    raw = iccp_chunk.data
    nul = raw.find(b"\x00")
    if nul >= 0 and len(raw) > nul + 2 and raw[nul + 1] == 0:
        try:
            return zlib.decompress(raw[nul + 2 :])
        except zlib.error:
            return None
    return None


def _decode_primaries_signal(
    signal: np.ndarray,
    primaries,
    curve: TransferCurve | None,
) -> tuple[np.ndarray, float, bool]:
    """编码信号 → 显示线性 + reference_white_nits + is_hdr。"""
    if curve is None:
        linear = encoded_to_linear_via_colourspace(signal, primaries)
        return linear, SDR_REFERENCE_WHITE_NITS, False

    linear = encoded_to_display_linear(signal, curve)
    is_hdr, ref_white = reference_for_transfer(curve)
    return linear, ref_white, is_hdr


def decode_png_to_source_image(path: str | Path) -> SourceImage:
    """解码 PNG → SourceImage。

    优先级：cICP → iCCP 命名空间 → 默认 sRGB。
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    data = path.read_bytes()
    chunks = iter_png_chunks(data)
    ihdr = _find_chunk(chunks, b"IHDR")
    if ihdr is None:
        raise PNGDecodeError("缺少 IHDR")
    _w, _h, ihdr_bits, _ctype = _parse_ihdr(ihdr.data)

    cicp_chunk = _find_chunk(chunks, b"cICP")
    iccp_chunk = _find_chunk(chunks, b"iCCP")
    clli_chunk = _find_chunk(chunks, b"cLLi")

    pixels, pil_bits = _load_rgb_array(path)
    bit_depth = max(ihdr_bits, pil_bits)
    signal = _dequant_to_unit(pixels[..., :3], bit_depth)

    icc_profile = _extract_iccp(iccp_chunk)

    if cicp_chunk is not None:
        cicp = CICP.from_bytes(cicp_chunk.data)
        primaries, curve = cicp_to_primaries_like(
            cicp.color_primaries,
            cicp.transfer_characteristics,
            cicp.matrix_coefficients,
        )
        linear, ref_white, is_hdr = _decode_primaries_signal(signal, primaries, curve)
    elif icc_profile is not None:
        desc = parse_icc_to_descriptor(icc_profile)
        if desc is not None:
            # 内建 Gamut 可用快速路径 + sRGB EOTF；否则用 colourspace TRC
            builtin = desc.as_builtin_gamut()
            if builtin is not None and desc.colour_name in (
                "sRGB",
                "Display P3",
                "ITU-R BT.2020",
            ):
                # Display P3 / BT.2020 ICC 通常仍是 γ≈sRGB 的 SDR 文件
                primaries: Gamut | ColourSpaceDescriptor = builtin
                linear, ref_white, is_hdr = _decode_primaries_signal(
                    signal, primaries, TransferCurve.SRGB
                )
            else:
                primaries = desc
                linear, ref_white, is_hdr = _decode_primaries_signal(
                    signal, primaries, None
                )
        else:
            primaries = Gamut.SRGB
            linear, ref_white, is_hdr = _decode_primaries_signal(
                signal, primaries, TransferCurve.SRGB
            )
    else:
        primaries = Gamut.SRGB
        linear, ref_white, is_hdr = _decode_primaries_signal(
            signal, primaries, TransferCurve.SRGB
        )

    alpha = None
    if pixels.shape[-1] >= 4:
        alpha = _dequant_to_unit(pixels[..., 3:4], bit_depth)[..., 0]

    # cLLi 暂存到 metadata 占位（Stage H 再结构化）；此处仅验证可解析
    if clli_chunk is not None:
        try:
            ContentLightLevel.from_png_bytes(clli_chunk.data)
        except ValueError:
            pass

    return SourceImage(
        linear=linear.astype(np.float32, copy=False),
        primaries=primaries,
        reference_white_nits=float(ref_white),
        is_hdr=is_hdr,
        alpha=alpha,
        icc_profile=icc_profile,
    )
