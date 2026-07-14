"""HEIF x265 preset 扫描：时间 vs 体积，找性价比档。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from pillow_heif.constants import HeifCompressionFormat
from pillow_heif.misc import CtxEncode

from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.color_metadata import get_direct_cicp_for_isobmff
from hdr_converter.core.color_pipeline import convert_colorspace
from hdr_converter.core.decoders.jxr_decoder import decode_jxr
from hdr_converter.core.color_metadata import nclx_save_kwargs

DEFAULT_JXR = Path(
    r"C:\Users\OBC\Videos\Captures\Horizon Forbidden West™ Complete Edition v1.5.80.0 2026_2_16 18_27_39.jxr"
)

# x265 官方从快到慢（image 静帧常用；placebo 极慢，单独可选）
PRESETS = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",  # x265 / libheif 常见默认
    "slow",
    "slower",
    "veryslow",
]

QUALITY = 90
BIT_DEPTH = 10
RUNS = 2  # 每档重复次数（取中位），首档含 warmup


def _safe(s: str) -> str:
    return s.encode("ascii", "backslashreplace").decode("ascii")


def _encode_once(
    pixels_u16: np.ndarray,
    *,
    quality: int,
    bit_depth: int,
    cicp,
    preset: str | None,
) -> bytes:
    h, w = pixels_u16.shape[:2]
    data = pixels_u16.tobytes()
    kwargs: dict = {"quality": quality}
    if preset is not None:
        kwargs["enc_params"] = {"preset": preset}
    ctx = CtxEncode(HeifCompressionFormat.HEVC, **kwargs)
    ctx.add_image(
        (w, h),
        "RGB;16",
        data,
        primary=True,
        bit_depth=bit_depth,
        icc_profile=None,
        **nclx_save_kwargs(cicp),
    )
    import io

    buf = io.BytesIO()
    ctx.save(buf)
    return buf.getvalue()


def _median(xs: list[float]) -> float:
    ys = sorted(xs)
    n = len(ys)
    if n % 2:
        return ys[n // 2]
    return 0.5 * (ys[n // 2 - 1] + ys[n // 2])


def main() -> None:
    jxr = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JXR
    out_dir = ROOT / "scripts" / "_test_out" / "heif_preset"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not jxr.is_file():
        # 容错：按时间戳/关键字找
        caps = Path(r"C:\Users\OBC\Videos\Captures")
        hits = [
            f
            for f in caps.glob("*.jxr")
            if "18_27_39" in f.name or "Forbidden" in f.name
        ]
        if not hits:
            raise SystemExit(f"文件不存在: {_safe(str(jxr))}")
        jxr = hits[0]
        print(f"fallback -> {_safe(jxr.name)}")

    print(f"Input:   {_safe(jxr.name)}")
    print(f"Quality: {QUALITY}  bit_depth: {BIT_DEPTH}  runs/preset: {RUNS}")
    print("Decode + color pipeline once, then encode-only sweep...\n")

    t0 = time.perf_counter()
    scrgb = decode_jxr(jxr)
    direct = convert_colorspace(
        scrgb,
        Gamut.P3,
        TransferCurve.PQ,
        quantize_bits=BIT_DEPTH,
    )
    px = direct.rgb
    if px.dtype != np.uint16:
        # float [0,1] → 左对齐 10-bit in 16-bit container
        maxv = (1 << BIT_DEPTH) - 1
        px = (np.clip(px[..., :3], 0, 1) * maxv).astype(np.uint16)
        px = (px << (16 - BIT_DEPTH)).astype(np.uint16)
    else:
        px = px[..., :3].astype(np.uint16)
    cicp = get_direct_cicp_for_isobmff(Gamut.P3, TransferCurve.PQ)
    prep_s = time.perf_counter() - t0
    h, w = px.shape[:2]
    print(f"Prep:    {w}x{h} uint16 RGB  ({prep_s:.2f}s)\n")

    # warmup（默认 medium，不计入表）
    _ = _encode_once(px, quality=QUALITY, bit_depth=BIT_DEPTH, cicp=cicp, preset="medium")

    rows: list[dict] = []
    # 先测「不传 preset」= 库默认
    cases: list[tuple[str, str | None]] = [("default", None)] + [
        (p, p) for p in PRESETS
    ]

    for label, preset in cases:
        times: list[float] = []
        size = 0
        blob = b""
        for _ in range(RUNS):
            t1 = time.perf_counter()
            blob = _encode_once(
                px, quality=QUALITY, bit_depth=BIT_DEPTH, cicp=cicp, preset=preset
            )
            times.append(time.perf_counter() - t1)
            size = len(blob)
        med = _median(times)
        out_path = out_dir / f"heif_q{QUALITY}_{label}.heif"
        out_path.write_bytes(blob)
        rows.append(
            {
                "preset": label,
                "sec": med,
                "bytes": size,
                "mib": size / (1024 * 1024),
            }
        )
        print(
            f"{label:12}  {med:7.2f}s  {size / 1024:8.1f} KiB  "
            f"({size / (1024 * 1024):.3f} MiB)"
        )

    # 性价比：相对 medium（或 default）的体积节省 / 额外时间
    base = next((r for r in rows if r["preset"] == "medium"), rows[0])
    print("\n--- vs medium ---")
    print(
        f"{'preset':12}  {'time':>8}  {'size':>10}  "
        f"{'dT':>8}  {'dSize%':>8}  {'KiB/s_saved*':>12}"
    )
    scored: list[tuple[float, str, dict]] = []
    for r in rows:
        d_t = r["sec"] - base["sec"]
        d_size_pct = 100.0 * (r["bytes"] - base["bytes"]) / base["bytes"]
        # 性价比：相对 medium，每多花 1 秒能省多少 KiB（越大越好；负时间=更快且更小更佳）
        saved_kib = (base["bytes"] - r["bytes"]) / 1024.0
        if abs(d_t) < 1e-6:
            efficiency = float("inf") if saved_kib > 0 else 0.0
        else:
            # 更快(d_t<0)且更小(saved>0): 用 saved / max(t,0.01) 再加速度奖励
            if d_t < 0:
                efficiency = saved_kib / r["sec"] + (-d_t) * 100  # 同时更快更小
            else:
                efficiency = saved_kib / d_t  # KiB saved per extra second
        scored.append((efficiency, r["preset"], r))
        print(
            f"{r['preset']:12}  {r['sec']:7.2f}s  {r['mib']:7.3f} MiB  "
            f"{d_t:+7.2f}s  {d_size_pct:+7.2f}%  {efficiency:12.1f}"
        )

    # 推荐：排除 placebo；在「不比 medium 大超过 2%」里找最快；
    # 以及「比 medium 小至少 2%」里找 KiB/s 最高的慢档
    print("\n--- recommendation ---")
    not_bloated = [r for r in rows if r["bytes"] <= base["bytes"] * 1.02]
    fastest_ok = min(not_bloated, key=lambda r: r["sec"]) if not_bloated else base
    print(
        f"Fast sweet spot (size <= medium+2%):  {fastest_ok['preset']}  "
        f"{fastest_ok['sec']:.2f}s  {fastest_ok['mib']:.3f} MiB"
    )

    smaller = [r for r in rows if r["bytes"] < base["bytes"] * 0.98]
    if smaller:
        # 边际收益：相对 medium 的 KiB/s
        best_slow = max(
            smaller,
            key=lambda r: (base["bytes"] - r["bytes"])
            / 1024.0
            / max(r["sec"] - base["sec"], 0.01),
        )
        kib_per_s = (
            (base["bytes"] - best_slow["bytes"])
            / 1024.0
            / max(best_slow["sec"] - base["sec"], 0.01)
        )
        print(
            f"Slow worth it (best KiB saved/s vs medium):  {best_slow['preset']}  "
            f"{best_slow['sec']:.2f}s  {best_slow['mib']:.3f} MiB  "
            f"({kib_per_s:.1f} KiB/s)"
        )
    else:
        print("No preset shrunk >2% vs medium; stick with medium or faster.")

    # 帕累托：体积不增前提下更快，或时间不增前提下更小
    print("\nPareto (non-dominated by both size and time):")
    for r in rows:
        dominated = any(
            o["sec"] <= r["sec"]
            and o["bytes"] <= r["bytes"]
            and (o["sec"] < r["sec"] or o["bytes"] < r["bytes"])
            for o in rows
            if o is not r
        )
        if not dominated:
            print(
                f"  * {r['preset']:12}  {r['sec']:.2f}s  {r['mib']:.3f} MiB"
            )


if __name__ == "__main__":
    main()
