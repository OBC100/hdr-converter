"""Stage B 回归：CICP 反查互逆 + PNG encode→decode→canonical 往返。

用法::

    .venv\\Scripts\\python scripts/_regress_stage_b.py [path.jxr]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.canonical import to_canonical_bt2020_linear  # noqa: E402
from hdr_converter.core.cicp import (  # noqa: E402
    Gamut,
    TransferCurve,
    cicp_to_gamut_curve,
    get_cicp,
)
from hdr_converter.core.color_pipeline import (  # noqa: E402
    convert_colorspace,
    scrgb_to_gamut_linear,
)
from hdr_converter.core.converter import ConvertSettings, convert_file  # noqa: E402
from hdr_converter.core.decoders import decode_to_source_image  # noqa: E402
from hdr_converter.core.decoders.png_decoder import decode_png_to_source_image  # noqa: E402
from hdr_converter.core.encoders.base import OutputFormat  # noqa: E402
from hdr_converter.core.hdr_options import HdrDeliveryMode  # noqa: E402
from hdr_converter.core.decoders.jxr_decoder import decode_jxr_to_source_image  # noqa: E402

TOLERANCE = 1.5e-5
# 量化往返容差：10-bit PQ/HLG 量化步长量级，远大于 Stage A 浮点容差
QUANT_TOLERANCE = 2.0 / 1023.0  # ≈ 2 LSB in normalized PQ domain → linear worse; use relative on linear
LINEAR_ROUNDTRIP_TOL = 5e-3  # display-linear max abs after 10-bit quantize round-trip


def _test_cicp_inverse() -> None:
    print("CICP forward/reverse:")
    for gamut in Gamut:
        for curve in TransferCurve:
            try:
                cicp = get_cicp(gamut, curve)
            except ValueError:
                continue
            back = cicp_to_gamut_curve(
                cicp.color_primaries,
                cicp.transfer_characteristics,
                cicp.matrix_coefficients,
            )
            assert back == (gamut, curve), f"{gamut}/{curve} -> {back}"
            # 2-tuple 反查也应命中
            back2 = cicp_to_gamut_curve(
                cicp.color_primaries, cicp.transfer_characteristics
            )
            assert back2 == (gamut, curve), f"2-key {gamut}/{curve} -> {back2}"
            print(f"  [PASS] {gamut.value:7} {curve.value:6} <-> {cicp.to_bytes().hex()}")
    print()


def _png_roundtrip(jxr: Path, gamut: Gamut, curve: TransferCurve) -> float:
    from hdr_converter.core.color_pipeline import hlg_encode_bt2100
    from hdr_converter.core.transfer_decode import (
        encoded_to_display_linear,
        hlg_decode_bt2100,
        pq_eotf,
    )
    from hdr_converter.core.color_pipeline import pq_oetf

    src = decode_jxr_to_source_image(jxr)
    expected = scrgb_to_gamut_linear(src.linear, gamut)

    # 与编码器相同的曲线往返（含 HLG 在 L_W 处的裁剪、PQ 量化前的 OETF）
    if curve == TransferCurve.HLG:
        ref_linear = hlg_decode_bt2100(hlg_encode_bt2100(expected, gamut=gamut))
    elif curve == TransferCurve.PQ:
        ref_linear = pq_eotf(pq_oetf(expected))
    else:
        ref_linear = expected

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / f"rt_{gamut.value}_{curve.value}.png"
        convert_file(
            jxr,
            out,
            ConvertSettings(
                output_format=OutputFormat.PNG,
                gamut=gamut,
                curve=curve,
                hdr_delivery=HdrDeliveryMode.DIRECT,
                encode_level=0,
            ),
        )
        decoded = decode_png_to_source_image(out)
        assert decoded.primaries == gamut, (decoded.primaries, gamut)
        got = to_canonical_bt2020_linear(
            decoded.linear, decoded.primaries, decoded.reference_white_nits
        )
        if gamut == Gamut.BT2020:
            ref = ref_linear
        else:
            ref = to_canonical_bt2020_linear(ref_linear, gamut, 10000.0)

        diff = float(np.max(np.abs(got.astype(np.float64) - ref.astype(np.float64))))
        status = "PASS" if diff <= LINEAR_ROUNDTRIP_TOL else "FAIL"
        print(
            f"  [{status}] PNG {gamut.value}/{curve.value}: "
            f"max|d|={diff:.3e}  hdr={decoded.is_hdr}"
        )
        return diff


def _jpeg_sdr_smoke() -> None:
    """无真实样张时：用 Pillow 写一张普通 sRGB JPEG 再解码。"""
    from PIL import Image

    print("JPEG baseline SDR:")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "sdr.jpg"
        arr = np.zeros((32, 48, 3), dtype=np.uint8)
        arr[..., 0] = 200
        arr[..., 1] = 100
        arr[..., 2] = 50
        Image.fromarray(arr, "RGB").save(path, quality=95)
        src = decode_to_source_image(path)
        assert src.primaries == Gamut.SRGB
        assert src.is_hdr is False
        assert abs(src.reference_white_nits - 100.0) < 1e-6
        print(f"  [PASS] jpeg decode shape={src.linear.shape} max={src.linear.max():.4f}")
    print()


def main(argv: list[str]) -> int:
    _test_cicp_inverse()
    _jpeg_sdr_smoke()

    jxr = Path(argv[1]) if len(argv) > 1 else ROOT.parent / "test_output" / "test_input.jxr"
    if not jxr.is_file():
        print(f"[SKIP] no JXR sample: {jxr}")
        print("PASSED (partial)")
        return 0

    print(f"PNG round-trip vs JXR linear ({jxr.name}):")
    worst = 0.0
    for curve in (TransferCurve.PQ, TransferCurve.HLG, TransferCurve.LINEAR):
        for gamut in (Gamut.BT2020, Gamut.P3, Gamut.SRGB):
            worst = max(worst, _png_roundtrip(jxr, gamut, curve))

    # 无 cICP 的普通 PNG（sRGB 假设）
    from PIL import Image

    print("\nPNG without cICP (assume sRGB):")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "nocicp.png"
        Image.fromarray(np.full((16, 16, 3), 128, dtype=np.uint8), "RGB").save(path)
        src = decode_png_to_source_image(path)
        assert src.primaries == Gamut.SRGB and not src.is_hdr
        print(f"  [PASS] default sRGB linear max={src.linear.max():.4f}")

    print(f"\nworst PNG round-trip max|Δ| = {worst:.3e}  (tol={LINEAR_ROUNDTRIP_TOL:.1e})")
    if worst > LINEAR_ROUNDTRIP_TOL:
        print("FAILED")
        return 1
    print("PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
