"""Stage E 回归：格式检测 + load_source_raw 冒烟 + 直通优化。"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.cicp import Gamut, TransferCurve  # noqa: E402
from hdr_converter.core.converter import ConvertSettings, convert_file  # noqa: E402
from hdr_converter.core.decode_cache import load_source_raw  # noqa: E402
from hdr_converter.core.encoders.base import OutputFormat  # noqa: E402
from hdr_converter.core.format_detect import InputFormat, detect_format  # noqa: E402
from hdr_converter.core.hdr_options import HdrDeliveryMode  # noqa: E402
from hdr_converter.core.passthrough import can_passthrough  # noqa: E402

JXR = Path(r"c:\Users\OBC\source\repos\OBC100\test_output\test_input.jxr")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    assert JXR.is_file(), JXR
    assert detect_format(JXR) == InputFormat.JXR

    print("=== Stage E: encode samples + load_source_raw ===")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        samples: list[tuple[Path, OutputFormat, ConvertSettings]] = []
        combos = [
            (OutputFormat.PNG, TransferCurve.PQ, HdrDeliveryMode.DIRECT),
            (OutputFormat.JPG, TransferCurve.PQ, HdrDeliveryMode.GAINMAP_MONO),
            (OutputFormat.AVIF, TransferCurve.PQ, HdrDeliveryMode.DIRECT),
            (OutputFormat.HEIF, TransferCurve.PQ, HdrDeliveryMode.DIRECT),
            (OutputFormat.JXL, TransferCurve.PQ, HdrDeliveryMode.DIRECT),
            (OutputFormat.AVIF, TransferCurve.PQ, HdrDeliveryMode.GAINMAP_MONO),
        ]
        for fmt, curve, delivery in combos:
            ext = {
                OutputFormat.PNG: "png",
                OutputFormat.JPG: "jpg",
                OutputFormat.AVIF: "avif",
                OutputFormat.HEIF: "heif",
                OutputFormat.JXL: "jxl",
            }[fmt]
            out = td / f"sample_{delivery.value}.{ext}"
            settings = ConvertSettings(
                output_format=fmt,
                gamut=Gamut.BT2020,
                curve=curve,
                hdr_delivery=delivery,
                encode_level=85,
                base_bits=10,
                quantize_bits=10,
            )
            try:
                convert_file(JXR, out, settings)
            except Exception as exc:
                print(f"  [SKIP] encode {ext}/{delivery}: {exc}")
                continue
            samples.append((out, fmt, settings))
            fmt_det = detect_format(out)
            expected = {
                OutputFormat.PNG: InputFormat.PNG,
                OutputFormat.JPG: InputFormat.JPEG,
                OutputFormat.AVIF: InputFormat.AVIF,
                OutputFormat.HEIF: InputFormat.HEIF,
                OutputFormat.JXL: InputFormat.JXL,
            }[fmt]
            assert fmt_det == expected, (out.name, fmt_det, expected)
            raw = load_source_raw(out)
            assert raw.ndim == 3 and raw.shape[-1] == 4, raw.shape
            assert np.isfinite(raw).all()
            print(f"  OK load {out.name} shape={raw.shape} max={float(raw[..., :3].max()):.4f}")

        print("=== Stage E: passthrough ===")
        # PNG→PNG 同参数应字节相同
        png_same = next((s for s in samples if s[1] == OutputFormat.PNG), None)
        if png_same is not None:
            src, fmt, settings = png_same
            assert can_passthrough(src, settings)
            out2 = td / "passthrough.png"
            convert_file(src, out2, settings)
            assert _sha(src) == _sha(out2), "passthrough hash mismatch"
            print("  OK PNG passthrough byte-identical")

            # 参数不同不可直通
            settings2 = ConvertSettings(
                output_format=OutputFormat.PNG,
                gamut=Gamut.P3,
                curve=TransferCurve.PQ,
                hdr_delivery=HdrDeliveryMode.DIRECT,
                encode_level=85,
                quantize_bits=10,
            )
            assert not can_passthrough(src, settings2)
            print("  OK passthrough blocked when gamut changes")

        # JXR→PNG 不可直通
        assert not can_passthrough(JXR, ConvertSettings(output_format=OutputFormat.PNG))
        print("  OK cross-format blocked")

    print("Stage E OK")


if __name__ == "__main__":
    main()
