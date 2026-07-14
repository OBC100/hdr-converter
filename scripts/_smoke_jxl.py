"""JXL encoder smoke test (synthetic pixels, no JXR required)."""
from __future__ import annotations

from pathlib import Path
import tempfile

import imagecodecs
import numpy as np

from hdr_converter.core.cicp import Gamut, TransferCurve, get_cicp
from hdr_converter.core.color_metadata import jpegxl_primaries, jpegxl_transfer
from hdr_converter.core.color_pipeline import PipelineResult
from hdr_converter.core.encoders import get_encoder
from hdr_converter.core.encoders.base import EncodeOptions, OutputFormat
from hdr_converter.core.encoders.jxl_encoder import JXLEncoder


def main() -> None:
    assert get_encoder(OutputFormat.JXL).format == OutputFormat.JXL
    assert jpegxl_primaries(Gamut.P3) == 11
    assert jpegxl_transfer(TransferCurve.PQ) == 16

    h, w = 64, 96
    px = (np.linspace(0, 1, h * w * 3).reshape(h, w, 3) * 65535).astype(np.uint16)
    pipe = PipelineResult(rgb=px, content_light=None, effective_bits=10, is_uint16=True)
    enc = JXLEncoder()
    out = Path(tempfile.gettempdir()) / "jxr_hdr_jxl_smoke.jxl"
    cases = [
        (Gamut.BT2020, TransferCurve.PQ),
        (Gamut.P3, TransferCurve.HLG),
        (Gamut.SRGB, TransferCurve.LINEAR),
        (Gamut.BT2020, TransferCurve.SRGB),
    ]
    for gamut, curve in cases:
        opts = EncodeOptions(
            gamut=gamut,
            curve=curve,
            quality=90,
            base_bits=10,
            output_format=OutputFormat.JXL,
        )
        enc.encode(pipe, out, opts, get_cicp(gamut, curve))
        data = out.read_bytes()
        magic = bytes.fromhex("0000000c4a584c200d0a870a")
        assert data[:12] == magic, data[:12].hex()
        assert imagecodecs.jpegxl_check(data)
        print(f"{gamut.value} {curve.value}: ok ({len(data)} bytes)")
    print("all smoke ok")


if __name__ == "__main__":
    main()
