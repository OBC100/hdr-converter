"""Stage C 回归：HEIF/AVIF/JXL Direct encode→decode 往返。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.canonical import to_canonical_bt2020_linear  # noqa: E402
from hdr_converter.core.cicp import Gamut, TransferCurve  # noqa: E402
from hdr_converter.core.color_pipeline import (  # noqa: E402
    hlg_encode_bt2100,
    pq_oetf,
    scrgb_to_gamut_linear,
)
from hdr_converter.core.converter import ConvertSettings, convert_file  # noqa: E402
from hdr_converter.core.decoders import decode_to_source_image, is_format_supported  # noqa: E402
from hdr_converter.core.encoders.base import OutputFormat  # noqa: E402
from hdr_converter.core.hdr_options import HdrDeliveryMode  # noqa: E402
from hdr_converter.core.decoders.jxr_decoder import decode_jxr_to_source_image  # noqa: E402
from hdr_converter.core.transfer_decode import hlg_decode_bt2100, pq_eotf  # noqa: E402

TOL = 1e-2  # 有损 Direct（AV1/HEVC/JXL）按格式压缩量级；见 MULTI_FORMAT_PLAN §6.4


def _ref_linear(expected: np.ndarray, curve: TransferCurve, gamut: Gamut) -> np.ndarray:
    if curve == TransferCurve.HLG:
        return hlg_decode_bt2100(hlg_encode_bt2100(expected, gamut=gamut))
    if curve == TransferCurve.PQ:
        return pq_eotf(pq_oetf(expected))
    return expected


def _roundtrip(
    jxr: Path,
    fmt: OutputFormat,
    gamut: Gamut,
    curve: TransferCurve,
    *,
    bits: int = 10,
) -> float:
    ext = {OutputFormat.AVIF: "avif", OutputFormat.HEIF: "heif", OutputFormat.JXL: "jxl"}[fmt]
    if not is_format_supported(ext):
        print(f"  [SKIP] {ext} not supported")
        return 0.0

    src = decode_jxr_to_source_image(jxr)
    expected = scrgb_to_gamut_linear(src.linear, gamut)
    ref_lin = _ref_linear(expected, curve, gamut)

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / f"rt_{gamut.value}_{curve.value}.{ext}"
        convert_file(
            jxr,
            out,
            ConvertSettings(
                output_format=fmt,
                gamut=gamut,
                curve=curve,
                hdr_delivery=HdrDeliveryMode.DIRECT,
                encode_level=90,
                base_bits=bits,
                quantize_bits=bits,
            ),
        )
        decoded = decode_to_source_image(out)
        assert decoded.primaries == gamut, (decoded.primaries, gamut, fmt)
        got = to_canonical_bt2020_linear(
            decoded.linear, decoded.primaries, decoded.reference_white_nits
        )
        ref = (
            ref_lin
            if gamut == Gamut.BT2020
            else to_canonical_bt2020_linear(ref_lin, gamut, 10000.0)
        )
        abs_err = np.abs(got.astype(np.float64) - ref.astype(np.float64))
        p999 = float(np.percentile(abs_err, 99.9))
        mx = float(abs_err.max())
        # 有损编解码：用 99.9% 分位，避免个别块效应像素主导
        status = "PASS" if p999 <= TOL else "FAIL"
        print(
            f"  [{status}] {ext:4} {gamut.value}/{curve.value}: "
            f"p99.9={p999:.3e} max={mx:.3e}"
        )
        return p999


def main(argv: list[str]) -> int:
    jxr = Path(argv[1]) if len(argv) > 1 else ROOT.parent / "test_output" / "test_input.jxr"
    if not jxr.is_file():
        print(f"[SKIP] no JXR: {jxr}")
        return 0

    print(f"Stage C Direct round-trip ({jxr.name})")
    worst = 0.0
    for fmt in (OutputFormat.AVIF, OutputFormat.HEIF, OutputFormat.JXL):
        for curve in (TransferCurve.PQ, TransferCurve.HLG, TransferCurve.LINEAR):
            for gamut in (Gamut.BT2020, Gamut.P3):
                bits = 10 if curve != TransferCurve.LINEAR else (
                    12 if fmt != OutputFormat.JXL else 16
                )
                # HEIF/AVIF 容器位深上限 12
                if fmt in (OutputFormat.HEIF, OutputFormat.AVIF) and bits > 12:
                    bits = 12
                worst = max(
                    worst,
                    _roundtrip(jxr, fmt, gamut, curve, bits=bits),
                )

    print(f"\nworst p99.9|d| = {worst:.3e}  (tol={TOL:.1e})")
    if worst > TOL:
        print("FAILED")
        return 1
    print("PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
