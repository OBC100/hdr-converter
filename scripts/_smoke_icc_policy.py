"""ICC 策略冒烟：plan + PNG/JPG 编码后文件含 profile。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.cicp import Gamut, TransferCurve, get_cicp  # noqa: E402
from hdr_converter.core.color_pipeline import PipelineResult  # noqa: E402
from hdr_converter.core.encoders.base import EncodeOptions, OutputFormat  # noqa: E402
from hdr_converter.core.encoders.jpg_encoder import JPGEncoder  # noqa: E402
from hdr_converter.core.encoders.png_encoder import PNGEncoder  # noqa: E402
from hdr_converter.core.hdr_options import HdrDeliveryMode  # noqa: E402
from hdr_converter.core.icc_policy import plan_icc_embed  # noqa: E402


def _has_png_iccp(data: bytes) -> bool:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    i = 8
    while i + 8 <= len(data):
        length = int.from_bytes(data[i : i + 4], "big")
        ctype = data[i + 4 : i + 8]
        if ctype == b"iCCP":
            return True
        if ctype == b"IEND":
            break
        i += 12 + length
    return False


def _has_jpeg_icc(data: bytes) -> bool:
    return b"ICC_PROFILE\x00" in data


def main() -> None:
    # AVIF 默认不嵌
    p = plan_icc_embed(
        OutputFormat.AVIF, Gamut.BT2020, TransferCurve.PQ, HdrDeliveryMode.DIRECT
    )
    assert not p.embed and p.windows_photos_safe

    p = plan_icc_embed(
        OutputFormat.AVIF,
        Gamut.BT2020,
        TransferCurve.PQ,
        HdrDeliveryMode.DIRECT,
        embed_icc=True,
    )
    assert p.embed and not p.windows_photos_safe

    # 合成 8×8 SDR / HDR 缓冲
    sdr = np.full((8, 8, 3), 0.5, dtype=np.float32)
    hdr_u16 = np.full((8, 8, 3), 32768, dtype=np.uint16)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # PNG SDR → baseline iCCP
        out = td / "sdr.png"
        PNGEncoder().encode(
            PipelineResult(rgb=sdr, content_light=None, effective_bits=8, is_uint16=False),
            out,
            EncodeOptions(
                gamut=Gamut.P3,
                curve=TransferCurve.SRGB,
                bit_depth=8,
                png_optimize=False,
            ),
            get_cicp(Gamut.P3, TransferCurve.SRGB),
        )
        assert _has_png_iccp(out.read_bytes()), "PNG SDR missing iCCP"
        print("  PNG SDR iCCP OK")

        # PNG PQ → HDR iCCP
        out2 = td / "hdr.png"
        PNGEncoder().encode(
            PipelineResult(
                rgb=hdr_u16, content_light=None, effective_bits=10, is_uint16=True
            ),
            out2,
            EncodeOptions(
                gamut=Gamut.BT2020,
                curve=TransferCurve.PQ,
                bit_depth=16,
                png_optimize=False,
            ),
            get_cicp(Gamut.BT2020, TransferCurve.PQ),
        )
        assert _has_png_iccp(out2.read_bytes()), "PNG PQ missing iCCP"
        print("  PNG PQ iCCP OK")

        # JPG Direct → APP2 ICC
        out3 = td / "sdr.jpg"
        JPGEncoder().encode(
            PipelineResult(rgb=sdr, content_light=None, effective_bits=8, is_uint16=False),
            out3,
            EncodeOptions(gamut=Gamut.SRGB, curve=TransferCurve.SRGB, quality=90),
            get_cicp(Gamut.SRGB, TransferCurve.SRGB),
        )
        assert _has_jpeg_icc(out3.read_bytes()), "JPG missing ICC_PROFILE"
        print("  JPG Direct ICC OK")

    print("ICC policy smoke OK")


if __name__ == "__main__":
    main()
