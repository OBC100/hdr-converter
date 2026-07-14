"""JPG 导出耗时剖析（分阶段计时）。"""
from __future__ import annotations

import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np

from hdr_converter.core.baseline_icc import get_baseline_display_icc
from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.color_pipeline import compute_content_light, scrgb_to_gamut_linear
from hdr_converter.core.encoders.base import EncodeOptions, OutputFormat
from hdr_converter.core.gainmap_math import compute_gainmap_from_scrgb
from hdr_converter.core.gainmap_pipeline import encode_gainmap_native_jpeg, scrgb_to_hdr_linear
from hdr_converter.core.hdr_options import HdrDeliveryMode, SdrToneMap
from hdr_converter.core.jpeg_encode import encode_rgb_jpeg
from hdr_converter.core.decoders.jxr_decoder import decode_jxr
from hdr_converter.core.sdr_tonemap import build_sdr_base_from_scrgb, build_sdr_linear_from_scrgb
from hdr_converter.core.uhdr_jpeg_mux import mux_ultra_hdr_jpeg
from hdr_converter.core.assets import get_hdr_icc

JXR = Path(r"C:\Users\OBC\Videos\Captures\Forza Horizon 6 2026_6_18 3_31_01.jxr")
OUT = ROOT / "scripts" / "_test_out" / "_profile_uhdr.jpg"


class Timer:
    def __init__(self) -> None:
        self.rows: list[tuple[str, float]] = []

    def run(self, name: str, fn) -> object:
        t0 = time.perf_counter()
        out = fn()
        ms = (time.perf_counter() - t0) * 1000.0
        self.rows.append((name, ms))
        return out

    def report(self, total_ms: float | None = None) -> None:
        rows = sorted(self.rows, key=lambda x: -x[1])
        if total_ms is None:
            total_ms = sum(ms for _, ms in self.rows)
        print(f"\n{'阶段':<42} {'ms':>9} {'占比':>7}")
        print("-" * 62)
        for name, ms in rows:
            pct = 100.0 * ms / total_ms if total_ms else 0
            print(f"{name:<42} {ms:9.1f} {pct:6.1f}%")
        print("-" * 62)
        print(f"{'合计':<42} {total_ms:9.1f}")


def profile_stages(scrgb: np.ndarray) -> None:
    h, w = scrgb.shape[:2]
    print(f"分辨率: {w}x{h} = {w*h/1e6:.2f} Mpx")
    tonemap = SdrToneMap.HABLE_MAX
    gamut = Gamut.P3
    curve = TransferCurve.PQ
    scale = 2
    quality = 90

    t = Timer()

    linear1 = t.run("scrgb_to_hdr_linear (整体)", lambda: scrgb_to_hdr_linear(scrgb, gamut))
    hdr_linear, cll = linear1

    t.run("scrgb_to_gamut_linear [重复#1]", lambda: scrgb_to_gamut_linear(scrgb, gamut))
    t.run("compute_content_light [重复#1]", lambda: compute_content_light(hdr_linear))

    gain_pack = t.run(
        "compute_gainmap_from_scrgb (整体)",
        lambda: compute_gainmap_from_scrgb(scrgb, gamut, curve, tonemap, scale=scale, multichannel=False),
    )
    gain_u8, metadata, sdr_linear = gain_pack

    t.run("scrgb_to_gamut_linear [重复#2 在 gainmap 内]", lambda: scrgb_to_gamut_linear(scrgb, gamut))
    t.run("build_sdr_linear_from_scrgb", lambda: build_sdr_linear_from_scrgb(scrgb, gamut, tonemap))
    t.run("compute_gainmap 数值部分", lambda: compute_gainmap_from_scrgb(scrgb, gamut, curve, tonemap, scale=scale, multichannel=False))

    sdr_rgb = t.run("build_sdr_base_from_scrgb", lambda: build_sdr_base_from_scrgb(scrgb, gamut, tonemap, base_bits=8))

    icc_base = get_baseline_display_icc(gamut)
    icc_gain = get_hdr_icc(gamut, curve)

    base_jpeg = t.run(
        "mozjpeg 主图 (全分辨率)",
        lambda: encode_rgb_jpeg(sdr_rgb, quality=quality, icc=icc_base),
    )
    gain_rgb = np.dstack([gain_u8, gain_u8, gain_u8])
    gain_jpeg = t.run(
        "mozjpeg 增益图 (scale=1/2)",
        lambda: encode_rgb_jpeg(gain_rgb, quality=quality, icc=icc_gain),
    )
    t.run("mux_ultra_hdr_jpeg", lambda: mux_ultra_hdr_jpeg(base_jpeg, gain_jpeg, metadata))

    t.report()


def profile_end_to_end() -> None:
    if not JXR.is_file():
        print(f"样张不存在: {JXR}")
        return

    print("=== 端到端 convert_file ===")
    from hdr_converter.core.converter import ConvertSettings, convert_file

    t0 = time.perf_counter()
    t_decode = time.perf_counter()
    scrgb = decode_jxr(JXR)
    decode_ms = (time.perf_counter() - t_decode) * 1000

    t_enc = time.perf_counter()
    convert_file(
        JXR,
        OUT,
        ConvertSettings(
            output_format=OutputFormat.JPG,
            gamut=Gamut.P3,
            curve=TransferCurve.PQ,
            encode_level=90,
            hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
            gainmap_scale=2,
            sdr_tonemap=SdrToneMap.HABLE_MAX,
        ),
    )
    encode_ms = (time.perf_counter() - t_enc) * 1000
    total_ms = (time.perf_counter() - t0) * 1000

    print(f"  JXR 解码:     {decode_ms:8.1f} ms")
    print(f"  编码+写盘:   {encode_ms:8.1f} ms  (含重复解码若 convert 内再读)")
    print(f"  脚本总计时:  {total_ms:8.1f} ms")

    # convert_file 内部会再 decode — 单独测 encode only
    print("\n=== 仅 encode_gainmap_native_jpeg（已解码缓冲）===")
    opts = EncodeOptions(
        output_format=OutputFormat.JPG,
        gamut=Gamut.P3,
        curve=TransferCurve.PQ,
        quality=90,
        hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
        gainmap_scale=2,
        sdr_tonemap=SdrToneMap.HABLE_MAX,
    )
    t0 = time.perf_counter()
    decode_ms = 0.0
    scrgb = decode_jxr(JXR)
    decode_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    encode_gainmap_native_jpeg(scrgb, OUT, opts)
    enc_ms = (time.perf_counter() - t0) * 1000
    print(f"  JXR 解码:     {decode_ms:8.1f} ms")
    print(f"  UHDR 编码:   {enc_ms:8.1f} ms")
    print(f"  合计:        {decode_ms + enc_ms:8.1f} ms")

    print("\n=== 分阶段（单次解码后）===")
    profile_stages(scrgb)


def profile_cprofile() -> None:
    if not JXR.is_file():
        return
    scrgb = decode_jxr(JXR)
    opts = EncodeOptions(
        output_format=OutputFormat.JPG,
        gamut=Gamut.P3,
        curve=TransferCurve.PQ,
        quality=90,
        hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
        gainmap_scale=2,
        sdr_tonemap=SdrToneMap.HABLE_MAX,
    )

    pr = cProfile.Profile()
    pr.enable()
    encode_gainmap_native_jpeg(scrgb, OUT, opts)
    pr.disable()

    print("\n=== cProfile Top 25（累计时间）===")
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(25)
    print(s.getvalue())


if __name__ == "__main__":
    profile_end_to_end()
    profile_cprofile()
