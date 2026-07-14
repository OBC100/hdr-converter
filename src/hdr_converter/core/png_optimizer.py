"""PNG 无损压缩：pyoxipng RawImage（level 1–6），失败时回退 legacy + optimize。"""

from __future__ import annotations

import logging
import struct
import zlib
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_OXIPNG_LEVEL = 2


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _make_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = _crc32(chunk_type + data)
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _oxipng_kwargs(level: int = DEFAULT_OXIPNG_LEVEL) -> dict:
    import oxipng

    return {
        "level": level,
        "bit_depth_reduction": False,
        "color_type_reduction": False,
        "palette_reduction": False,
        "grayscale_reduction": False,
        "scale_16": False,
        "strip": oxipng.StripChunks.none(),
    }


def _pixel_bytes(pixels: np.ndarray, bit_depth: int) -> bytes:
    if bit_depth == 16:
        return pixels.astype(np.uint16).byteswap().tobytes()
    return pixels.astype(np.uint8).tobytes()


def _build_legacy_png(
    pixels: np.ndarray,
    *,
    bit_depth: int,
    cicp_bytes: bytes,
    iccp_chunk_data: bytes | None,
    clli_bytes: bytes | None,
) -> bytes:
    h, w = pixels.shape[:2]
    ihdr = struct.pack(">IIBBBBB", w, h, bit_depth, 2, 0, 0, 0)
    chunks: list[bytes] = [b"\x89PNG\r\n\x1a\n", _make_chunk(b"IHDR", ihdr)]
    chunks.append(_make_chunk(b"cICP", cicp_bytes))
    if iccp_chunk_data is not None:
        chunks.append(_make_chunk(b"iCCP", iccp_chunk_data))
    if clli_bytes is not None:
        chunks.append(_make_chunk(b"cLLi", clli_bytes))

    if bit_depth == 16:
        raw_rows = [
            b"\x00" + pixels[y].astype(np.uint16).byteswap().tobytes() for y in range(h)
        ]
    else:
        raw_rows = [b"\x00" + pixels[y].astype(np.uint8).tobytes() for y in range(h)]
    compressed = zlib.compress(b"".join(raw_rows), 9)
    chunks.append(_make_chunk(b"IDAT", compressed))
    chunks.append(_make_chunk(b"IEND", b""))
    return b"".join(chunks)


def _encode_oxipng_raw(
    pixels: np.ndarray,
    *,
    bit_depth: int,
    cicp_bytes: bytes,
    icc_bytes: bytes | None,
    clli_bytes: bytes | None,
    oxipng_level: int = DEFAULT_OXIPNG_LEVEL,
) -> bytes:
    import oxipng

    h, w = pixels.shape[:2]
    raw = oxipng.RawImage(
        _pixel_bytes(pixels, bit_depth),
        w,
        h,
        color_type=oxipng.ColorType.rgb(),
        bit_depth=bit_depth,
    )
    raw.add_png_chunk(b"cICP", cicp_bytes)
    if icc_bytes is not None:
        raw.add_icc_profile(icc_bytes)
    if clli_bytes is not None:
        raw.add_png_chunk(b"cLLi", clli_bytes)
    return raw.create_optimized_png(**_oxipng_kwargs(oxipng_level))


def write_optimized_png(
    output_path: Path,
    pixels: np.ndarray,
    *,
    bit_depth: int,
    cicp_bytes: bytes,
    iccp_chunk_data: bytes | None,
    icc_bytes: bytes | None,
    clli_bytes: bytes | None,
    enabled: bool = True,
    oxipng_level: int = DEFAULT_OXIPNG_LEVEL,
) -> None:
    """写入 PNG。默认 oxipng RawImage；失败则 legacy + optimize_from_memory。"""
    if not enabled:
        output_path.write_bytes(
            _build_legacy_png(
                pixels,
                bit_depth=bit_depth,
                cicp_bytes=cicp_bytes,
                iccp_chunk_data=iccp_chunk_data,
                clli_bytes=clli_bytes,
            )
        )
        return

    try:
        import oxipng  # noqa: F401
    except ImportError:
        logger.warning("pyoxipng 未安装，跳过 PNG 优化")
        output_path.write_bytes(
            _build_legacy_png(
                pixels,
                bit_depth=bit_depth,
                cicp_bytes=cicp_bytes,
                iccp_chunk_data=iccp_chunk_data,
                clli_bytes=clli_bytes,
            )
        )
        return

    try:
        output_path.write_bytes(
            _encode_oxipng_raw(
                pixels,
                bit_depth=bit_depth,
                cicp_bytes=cicp_bytes,
                icc_bytes=icc_bytes,
                clli_bytes=clli_bytes,
                oxipng_level=oxipng_level,
            )
        )
        return
    except Exception as exc:
        logger.warning("oxipng RawImage 失败 (%s)，回退 encode+optimize", exc)

    legacy = _build_legacy_png(
        pixels,
        bit_depth=bit_depth,
        cicp_bytes=cicp_bytes,
        iccp_chunk_data=iccp_chunk_data,
        clli_bytes=clli_bytes,
    )
    try:
        import oxipng

        output_path.write_bytes(
            oxipng.optimize_from_memory(legacy, **_oxipng_kwargs(oxipng_level))
        )
    except Exception as exc:
        logger.warning("oxipng optimize_from_memory 失败 (%s)，使用 legacy PNG", exc)
        output_path.write_bytes(legacy)
