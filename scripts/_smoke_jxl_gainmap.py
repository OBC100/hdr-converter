"""JXL Gain Map (jhgm) smoke test."""
from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import imagecodecs
import numpy as np

from hdr_converter.core.cicp import Gamut, TransferCurve, get_cicp
from hdr_converter.core.encoders.base import EncodeOptions, OutputFormat
from hdr_converter.core.gainmap_pipeline import encode_gainmap
from hdr_converter.core.hdr_options import HdrDeliveryMode
from hdr_converter.core.jxl_gainmap import parse_jhgm_bundle


def _find_box(data: bytes, box_type: bytes) -> bytes | None:
    offset = 0
    while offset + 8 <= len(data):
        size = struct.unpack_from(">I", data, offset)[0]
        typ = data[offset + 4 : offset + 8]
        hdr = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            hdr = 16
        elif size == 0:
            size = len(data) - offset
        if size < hdr or offset + size > len(data):
            break
        if typ == box_type:
            return data[offset + hdr : offset + size]
        offset += size
    return None


def main() -> None:
    h, w = 48, 64
    rgb = np.zeros((h, w, 4), dtype=np.float32)
    rgb[..., :3] = 0.02
    rgb[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4, :3] = 0.25
    rgb[..., 3] = 1.0

    out = Path(tempfile.gettempdir()) / "jxr_hdr_jxl_gainmap_smoke.jxl"
    cicp = get_cicp(Gamut.BT2020, TransferCurve.PQ)
    for delivery in (HdrDeliveryMode.GAINMAP_MONO, HdrDeliveryMode.GAINMAP_COLOR):
        opts = EncodeOptions(
            gamut=Gamut.BT2020,
            curve=TransferCurve.PQ,
            quality=85,
            base_bits=10,
            gainmap_bits=8,
            gainmap_scale=2,
            output_format=OutputFormat.JXL,
            hdr_delivery=delivery,
        )
        encode_gainmap(rgb, out, opts, cicp)
        data = out.read_bytes()
        assert data.startswith(bytes.fromhex("0000000c4a584c200d0a870a"))
        assert imagecodecs.jpegxl_check(data)
        jhgm = _find_box(data, b"jhgm")
        assert jhgm is not None, "missing jhgm box"
        meta, gain_cs = parse_jhgm_bundle(jhgm)
        assert len(meta) >= 5
        assert meta[:4] == b"\x00\x00\x00\x00"  # ISO 21496 min/writer version
        assert gain_cs.startswith(b"\xff\n")
        decoded = imagecodecs.jpegxl_decode(data)
        assert decoded.ndim >= 2
        print(
            f"{delivery.value}: ok size={len(data)} "
            f"jhgm={len(jhgm)} meta={len(meta)} gain_cs={len(gain_cs)} "
            f"decoded={decoded.shape}"
        )
    print("jxl gainmap smoke ok")


if __name__ == "__main__":
    main()
