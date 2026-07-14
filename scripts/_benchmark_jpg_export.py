"""JPG Ultra HDR 导出测速（优化后管线）。"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import imagecodecs

from hdr_converter.core.baseline_icc import get_baseline_display_icc
from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.converter import ConvertSettings, convert_file
from hdr_converter.core.decode_cache import DecodeCache, load_source_raw
from hdr_converter.core.encoders.base import EncodeOptions, OutputFormat
from hdr_converter.core.gainmap_core import prepare_gainmap_linear
from hdr_converter.core.gainmap_pipeline import (
    encode_gainmap_native_jpeg,
)
from hdr_converter.core.gainmap_math import compute_gainmap_with_peak
from hdr_converter.core.hdr_options import HdrDeliveryMode, SdrToneMap
from hdr_converter.core.jpeg_encode import encode_rgb_jpeg
from hdr_converter.core.decoders.jxr_decoder import decode_jxr
from hdr_converter.core.assets import get_hdr_icc
from hdr_converter.core.sdr_tonemap import sdr_linear_to_base_pixels
from hdr_converter.core.uhdr_jpeg_mux import mux_ultra_hdr_jpeg

JXR = Path(r"C:\Users\OBC\Videos\Captures\Forza Horizon 6 2026_6_18 3_31_01.jxr")
OUT = ROOT / "scripts" / "_test_out" / "_bench_uhdr.jpg"
RUNS = 3

# 优化前典型耗时（同机 Forza 3840×2160，2026-06 剖析）
BASELINE_MS = {
    "decode": 370,
    "encode_e2e": 8000,
    "encode_only": 8000,
    "mozjpeg_total": 540,
}
# 合并管线后、3×3 前（2026-06）
PIPELINE_OPT_MS = {
    "encode_only": 3830,
    "prepare_buffers": 2040,
}
# 3×3 后、CPU 并行前（2026-06）
MATRIX3_MS = {
    "encode_only": 2836,
    "convert_file": 3228,
}


def _ms(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000.0


def bench_stage(scrgb, runs: int = RUNS) -> dict[str, float]:
    opts = EncodeOptions(
        output_format=OutputFormat.JPG,
        gamut=Gamut.P3,
        curve=TransferCurve.PQ,
        quality=90,
        hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
        gainmap_scale=2,
        sdr_tonemap=SdrToneMap.HABLE_MAX,
    )
    tonemap = SdrToneMap.HABLE_MAX
    icc_base = get_baseline_display_icc(Gamut.P3)
    icc_gain = get_hdr_icc(Gamut.P3, TransferCurve.PQ)

    def once_prepare():
        prepare_gainmap_linear(
            scrgb, Gamut.P3, TransferCurve.PQ, tonemap
        )

    def once_gain(hdr, sdr, peak):
        compute_gainmap_with_peak(
            hdr, sdr, Gamut.P3, TransferCurve.PQ, peak, scale=2, multichannel=False
        )

    def once_full():
        encode_gainmap_native_jpeg(scrgb, OUT, opts)

    # warmup
    once_full()

    prep = [_ms(once_prepare) for _ in range(runs)]
    hdr, sdr, cll = prepare_gainmap_linear(
        scrgb, Gamut.P3, TransferCurve.PQ, tonemap
    )
    peak = float(cll.max_cll)
    gain_t = [_ms(lambda: once_gain(hdr, sdr, peak)) for _ in range(runs)]
    sdr_rgb = sdr_linear_to_base_pixels(sdr, base_bits=8)
    gain_u8, meta = compute_gainmap_with_peak(
        hdr, sdr, Gamut.P3, TransferCurve.PQ, peak, scale=2, multichannel=False
    )
    import numpy as np

    gain_rgb = np.dstack([gain_u8, gain_u8, gain_u8])
    moz_base = [_ms(lambda: encode_rgb_jpeg(sdr_rgb, quality=90, icc=icc_base)) for _ in range(runs)]
    moz_gain = [_ms(lambda: encode_rgb_jpeg(gain_rgb, quality=90, icc=icc_gain)) for _ in range(runs)]
    base_j = encode_rgb_jpeg(sdr_rgb, quality=90, icc=icc_base)
    gain_j = encode_rgb_jpeg(gain_rgb, quality=90, icc=icc_gain)
    mux_t = [_ms(lambda: mux_ultra_hdr_jpeg(base_j, gain_j, meta)) for _ in range(runs)]
    enc = [_ms(once_full) for _ in range(runs)]

    return {
        "prepare_buffers": statistics.mean(prep),
        "gainmap": statistics.mean(gain_t),
        "mozjpeg_base": statistics.mean(moz_base),
        "mozjpeg_gain": statistics.mean(moz_gain),
        "mux": statistics.mean(mux_t),
        "encode_native": statistics.mean(enc),
    }


def bench_e2e(runs: int = RUNS) -> dict[str, float]:
    settings = ConvertSettings(
        output_format=OutputFormat.JPG,
        gamut=Gamut.P3,
        curve=TransferCurve.PQ,
        encode_level=90,
        hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
        gainmap_scale=2,
        sdr_tonemap=SdrToneMap.HABLE_MAX,
    )

    def once():
        convert_file(JXR, OUT, settings)

    once()  # warmup
    times = [_ms(once) for _ in range(runs)]
    return {"convert_file": statistics.mean(times)}


def bench_e2e_cached(cache: DecodeCache, runs: int = RUNS) -> dict[str, float]:
    """模拟 GUI：预览已 decode 入 DecodeCache 后点击转换。"""
    settings = ConvertSettings(
        output_format=OutputFormat.JPG,
        gamut=Gamut.P3,
        curve=TransferCurve.PQ,
        encode_level=90,
        hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
        gainmap_scale=2,
        sdr_tonemap=SdrToneMap.HABLE_MAX,
    )

    def once():
        convert_file(JXR, OUT, settings, decode_cache=cache)

    once()
    times = [_ms(once) for _ in range(runs)]
    return {"convert_file": statistics.mean(times)}


def main() -> None:
    if not JXR.is_file():
        print(f"样张不存在: {JXR}")
        return

    decode_runs = [_ms(lambda: decode_jxr(JXR)) for _ in range(RUNS)]
    decode_ms = statistics.mean(decode_runs)
    cache = DecodeCache()
    scrgb = load_source_raw(JXR, cache=cache)
    h, w = scrgb.shape[:2]

    print(f"样张: {JXR.name}")
    print(f"分辨率: {w}x{h} ({w*h/1e6:.2f} Mpx)")
    print(f"轮次: {RUNS} 次取平均\n")

    stages = bench_stage(scrgb)
    e2e = bench_e2e()
    e2e_cached = bench_e2e_cached(cache)

    encode_only = stages["encode_native"]
    moz_total = stages["mozjpeg_base"] + stages["mozjpeg_gain"]
  # cpu colour ~= encode - moz - mux
    colour_est = encode_only - moz_total - stages["mux"]

    print("=== 分阶段（优化后，encode_gainmap_native_jpeg）===")
    print(f"{'阶段':<28} {'ms':>9} {'占比':>7}")
    print("-" * 48)
    rows = [
        ("prepare_gainmap_linear", stages["prepare_buffers"]),
        ("compute_gainmap_with_peak", stages["gainmap"]),
        ("mozjpeg 主图", stages["mozjpeg_base"]),
        ("mozjpeg 增益图", stages["mozjpeg_gain"]),
        ("mux_ultra_hdr_jpeg", stages["mux"]),
    ]
    for name, ms in rows:
        pct = 100.0 * ms / encode_only if encode_only else 0
        print(f"{name:<28} {ms:9.1f} {pct:6.1f}%")
    print("-" * 48)
    print(f"{'编码合计':<28} {encode_only:9.1f}")
    print(f"{'  其中 colour+tone map 估计':<28} {colour_est:9.1f}")

    print("\n=== 端到端 ===")
    print(f"JXR 解码（冷）:    {decode_ms:8.1f} ms")
    print(f"convert_file:      {e2e['convert_file']:8.1f} ms  (含解码)")
    print(f"convert_file 缓存:  {e2e_cached['convert_file']:8.1f} ms  (DecodeCache 命中，模拟 GUI)")
    print(f"编码估算:          {e2e['convert_file'] - decode_ms:8.1f} ms  (e2e - 单次解码)")
    saved = e2e["convert_file"] - e2e_cached["convert_file"]
    if saved > 1:
        print(f"缓存节省:          {saved:8.1f} ms  (~{100 * saved / e2e['convert_file']:.0f}%)")

    out_b = OUT.read_bytes()
    print(f"\n输出: {OUT.name}  {len(out_b)/1024:.1f} KB")
    print(f"ultrahdr_check: {imagecodecs.ultrahdr_check(out_b)}")

    print("\n=== 对比优化前（同机历史剖析）===")
    print(f"{'指标':<22} {'原始':>9} {'管线':>9} {'3×3':>9} {'当前':>9}")
    print("-" * 62)
    for label, orig, pipe, mat3, cur in [
        ("JXR 解码", BASELINE_MS["decode"], BASELINE_MS["decode"], decode_ms, decode_ms),
        ("UHDR 编码", BASELINE_MS["encode_only"], PIPELINE_OPT_MS["encode_only"], MATRIX3_MS["encode_only"], encode_only),
        ("prepare_buffers", 2040, PIPELINE_OPT_MS["prepare_buffers"], 1067, stages["prepare_buffers"]),
        ("convert_file", 8370, 4126, MATRIX3_MS["convert_file"], e2e["convert_file"]),
        ("convert_file+缓存", 8370, 4126, MATRIX3_MS["convert_file"], e2e_cached["convert_file"]),
        ("mozjpeg 合计", BASELINE_MS["mozjpeg_total"], 542, 524, moz_total),
    ]:
        print(f"{label:<22} {orig:8.0f} ms {pipe:8.0f} ms {mat3:8.1f} ms {cur:8.1f} ms")


if __name__ == "__main__":
    main()
