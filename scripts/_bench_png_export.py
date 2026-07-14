"""单次 PNG 导出测速（decode / colorspace / 全流程 / 缓存命中）。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.color_pipeline import convert_colorspace
from hdr_converter.core.converter import ConvertSettings, convert_file
from hdr_converter.core.decode_cache import DecodeCache, load_source_raw
from hdr_converter.core.encoders.base import OutputFormat
from hdr_converter.core.decoders.jxr_decoder import decode_jxr

DEFAULT_JXR = Path(
    r"C:\Users\OBC\Videos\Captures\Horizon Forbidden West™ Complete Edition v1.5.80.0 2026_2_16 18_27_39.jxr"
)


def bench(label: str, fn, runs: int = 3) -> float:
    times: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    avg = sum(times) / len(times)
    print(
        f"{label:28}  avg {avg * 1000:7.1f} ms  "
        f"(min {min(times) * 1000:.1f} / max {max(times) * 1000:.1f})"
    )
    return avg


def _safe_text(s: str) -> str:
    return s.encode("ascii", "backslashreplace").decode("ascii")


def main() -> None:
    jxr = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JXR
    out = ROOT / "scripts" / "_test_out" / "bench_horizon.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    if not jxr.is_file():
        raise SystemExit(f"文件不存在: {jxr}")

    _ = decode_jxr(jxr)
    h, w = _.shape[:2]
    mb = _.nbytes / (1024 * 1024)

    print(f"Input:     {_safe_text(jxr.name)}")
    print(f"Resolution:{w} x {h}")
    print(f"L0 buffer: {mb:.1f} MiB (float32 RGBA)")
    print(f"Settings:  PNG / P3 / PQ / oxipng L2 / 10-bit (GUI 默认近似)")
    print()

    settings = ConvertSettings(
        output_format=OutputFormat.PNG,
        gamut=Gamut.P3,
        curve=TransferCurve.PQ,
        quantize_bits=10,
        encode_level=2,
    )

    t_decode = bench("1. decode_jxr", lambda: decode_jxr(jxr))
    t_color = bench(
        "2. convert_colorspace",
        lambda: convert_colorspace(
            load_source_raw(jxr),
            Gamut.P3,
            TransferCurve.PQ,
            quantize_bits=10,
        ),
    )
    t_cold = bench("3. PNG export (cold)", lambda: convert_file(jxr, out, settings))

    cache = DecodeCache()
    load_source_raw(jxr, cache=cache)
    t_warm = bench(
        "4. PNG export (cache hit)",
        lambda: convert_file(jxr, out, settings, decode_cache=cache),
    )

    print()
    print("Oxipng level sweep (cached raw):")
    for level in (0, 1, 2):
        s = ConvertSettings(
            output_format=OutputFormat.PNG,
            gamut=Gamut.P3,
            curve=TransferCurve.PQ,
            quantize_bits=10,
            encode_level=level,
        )
        p = out.parent / f"bench_horizon_l{level}.png"
        bench(
            f"   level {level}",
            lambda lev=level, path=p, cfg=s: convert_file(
                jxr, path, cfg, decode_cache=cache
            ),
            runs=2,
        )
        if p.is_file():
            print(f"      size {p.stat().st_size / 1024 / 1024:.2f} MiB")

    print()
    print(f"Output:    {out}")
    if out.is_file():
        print(f"File size: {out.stat().st_size / 1024 / 1024:.2f} MiB")
    print()
    print("Stage share (cold export):")
    encode_est = max(0.0, t_cold - t_decode - t_color)
    total = t_cold or 1.0
    for name, sec in (
        ("decode", t_decode),
        ("colorspace+quantize", t_color),
        ("encode+oxipng (est.)", encode_est),
    ):
        print(f"  {name:22} {sec * 1000:7.1f} ms  ({sec / total * 100:4.1f}%)")


if __name__ == "__main__":
    main()
