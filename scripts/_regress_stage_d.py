"""Stage D 回归：apply_gainmap 数学逆 + 四容器 Gain Map encode→demux。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.canonical import to_canonical_bt2020_linear  # noqa: E402
from hdr_converter.core.cicp import Gamut, TransferCurve  # noqa: E402
from hdr_converter.core.converter import ConvertSettings, convert_file  # noqa: E402
from hdr_converter.core.decoders import decode_to_source_image, is_format_supported  # noqa: E402
from hdr_converter.core.encoders.base import OutputFormat  # noqa: E402
from hdr_converter.core.gainmap_demux import (  # noqa: E402
    demux_isobmff_gainmap,
    demux_jxl_gainmap,
    demux_uhdr_jpeg_to_hdr,
)
from hdr_converter.core.gainmap_math import apply_gainmap, compute_gainmap_with_peak  # noqa: E402
from hdr_converter.core.hdr_options import HdrDeliveryMode  # noqa: E402
from hdr_converter.core.decoders.jxr_decoder import decode_jxr_to_source_image  # noqa: E402
from hdr_converter.core.color_pipeline import scrgb_to_gamut_linear  # noqa: E402

MATH_TOL = 1.5e-5
# Gain Map 有损容器往返：允许更大误差（tone map + 压缩）
GM_P99_TOL = 5e-2


def _test_apply_gainmap_math() -> None:
    rng = np.random.default_rng(42)
    h, w = 32, 48
    # HDR ≈ 50–350 nits，peak 元数据固定 800，ratio≈2 < max_boost≈3.94，SDR 不钳位
    hdr = rng.uniform(0.005, 0.035, size=(h, w, 3)).astype(np.float64)
    peak = 800.0
    max_boost = peak / 203.0
    factor = (10000.0 / 203.0) / (max_boost * 0.5)
    sdr = hdr * factor
    assert float(sdr.max()) <= 1.0 + 1e-6, (sdr.max(), factor)
    for multi in (False, True):
        gain, meta = compute_gainmap_with_peak(
            hdr, sdr, Gamut.BT2020, TransferCurve.PQ, peak, multichannel=multi
        )
        recovered = apply_gainmap(sdr, gain, meta, gamut=Gamut.BT2020)
        err = np.abs(recovered - hdr)
        p999 = float(np.percentile(err, 99.9))
        mx = float(err.max())
        print(f"  apply_gainmap multi={multi}: max={mx:.3e} p99.9={p999:.3e}")
        assert p999 <= 2e-3, (multi, p999, mx)


def _gm_roundtrip(jxr: Path, fmt: OutputFormat, gamut: Gamut = Gamut.BT2020) -> float:
    ext = {
        OutputFormat.JPG: "jpg",
        OutputFormat.AVIF: "avif",
        OutputFormat.HEIF: "heif",
        OutputFormat.JXL: "jxl",
    }[fmt]
    key = "jpg" if ext == "jpg" else ext
    if not is_format_supported(key):
        print(f"  [SKIP] {ext} not supported")
        return 0.0

    src = decode_jxr_to_source_image(jxr)
    expected = scrgb_to_gamut_linear(src.linear, gamut)
    ref = to_canonical_bt2020_linear(expected, gamut, 10000.0)

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / f"gm.{ext}"
        convert_file(
            jxr,
            out,
            ConvertSettings(
                output_format=fmt,
                gamut=gamut,
                curve=TransferCurve.PQ,
                hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
                encode_level=90,
                base_bits=10,
            ),
        )
        data = out.read_bytes()
        # demux_* 的 gamut= 仅为"未标 nclx 时"的回退默认值；这里故意传入与真实
        # 编码色域不同的 BT2020，验证 demux 是否真的从文件自身的 colr/nclx
        # 反查色域（而不是沿用调用方给的默认值——那正是本次红移 bug 的根因）。
        if fmt == OutputFormat.JPG:
            gm = demux_uhdr_jpeg_to_hdr(data, gamut=Gamut.BT2020)
        elif fmt == OutputFormat.JXL:
            gm = demux_jxl_gainmap(data, gamut=Gamut.BT2020)
        else:
            gm = demux_isobmff_gainmap(
                data, is_avif=(fmt == OutputFormat.AVIF), gamut=Gamut.BT2020
            )
        assert gm is not None, f"demux failed for {ext}"
        if fmt != OutputFormat.JPG:
            assert gm.primaries == gamut, (ext, gamut, gm.primaries)

        # 也走统一解码器
        decoded = decode_to_source_image(out)
        assert decoded.is_hdr
        got = to_canonical_bt2020_linear(
            decoded.linear, decoded.primaries, decoded.reference_white_nits
        )
        # 对齐尺寸（gainmap scale 可能降采样后重建）
        if got.shape != ref.shape:
            # 最近邻缩到 ref
            ys = (np.arange(ref.shape[0]) * got.shape[0] // ref.shape[0]).astype(np.int32)
            xs = (np.arange(ref.shape[1]) * got.shape[1] // ref.shape[1]).astype(np.int32)
            got = got[ys][:, xs]
        err = np.abs(got - ref)
        p999 = float(np.percentile(err, 99.9))
        print(f"  {ext} [{gamut.value}] gainmap demux p99.9={p999:.3e} max={float(err.max()):.3e}")
        assert p999 <= GM_P99_TOL, (ext, gamut, p999)
        return p999


def main() -> None:
    print("=== Stage D: apply_gainmap math ===")
    _test_apply_gainmap_math()

    jxr = Path(r"c:\Users\OBC\source\repos\OBC100\test_output\test_input.jxr")
    if not jxr.is_file():
        print(f"[SKIP] missing {jxr}")
        return
    print("=== Stage D: Gain Map encode→demux ===")
    for fmt in (OutputFormat.JPG, OutputFormat.AVIF, OutputFormat.HEIF, OutputFormat.JXL):
        _gm_roundtrip(jxr, fmt)
    print("=== Stage D: 非 BT2020 基础层色域反查（回归 sRGB/P3 误当 BT2020 的红移 bug）===")
    for fmt in (OutputFormat.AVIF, OutputFormat.HEIF, OutputFormat.JXL):
        for gamut in (Gamut.P3, Gamut.SRGB):
            _gm_roundtrip(jxr, fmt, gamut)
    print("Stage D OK")


if __name__ == "__main__":
    main()
