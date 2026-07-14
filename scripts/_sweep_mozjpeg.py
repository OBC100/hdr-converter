"""扫描 mozjpeg_encode 参数：体积 / 耗时 / ultrahdr_check。"""
from __future__ import annotations

import io
import sys
import time
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import imagecodecs
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.baseline_icc import get_baseline_display_icc
from hdr_converter.core.jpeg_icc import prepend_icc_profile
from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.encoders.base import EncodeOptions, OutputFormat
from hdr_converter.core.gainmap_pipeline import encode_gainmap_native_jpeg
from hdr_converter.core.hdr_options import HdrDeliveryMode, SdrToneMap
from hdr_converter.core.decoders.jxr_decoder import decode_jxr
from hdr_converter.core.uhdr_jpeg_mux import mux_ultra_hdr_jpeg

JXR = Path(r"C:\Users\OBC\Videos\Captures\Forza Horizon 6 2026_6_18 3_31_01.jxr")
OUT = ROOT / "scripts" / "_test_out"
QUALITY = 90


@dataclass(frozen=True)
class MozCfg:
    name: str
    level: int = QUALITY
    optimize: bool | None = True
    progressive: bool | None = True
    notrellis: bool | None = None
    subsampling: str | tuple[int, int] | None = None
    quanttable: int | None = None


def encode_jpeg(arr: np.ndarray, cfg: MozCfg, *, icc: bytes | None = None) -> bytes:
    kw: dict = {
        "level": cfg.level,
        "optimize": cfg.optimize,
        "progressive": cfg.progressive,
    }
    if cfg.notrellis is not None:
        kw["notrellis"] = cfg.notrellis
    if cfg.subsampling is not None:
        kw["subsampling"] = cfg.subsampling
    if cfg.quanttable is not None:
        kw["quanttable"] = cfg.quanttable
    jpeg = bytes(imagecodecs.mozjpeg_encode(arr, **kw))
    return prepend_icc_profile(jpeg, icc) if icc else jpeg


def pillow_jpeg(arr: np.ndarray, *, quality: int, icc: bytes | None) -> bytes:
    buf = io.BytesIO()
    kw: dict = {"format": "JPEG", "quality": quality}
    if icc:
        kw["icc_profile"] = icc
    Image.fromarray(arr, "RGB").save(buf, **kw)
    return buf.getvalue()


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    diff = a.astype(np.float64) - b.astype(np.float64)
    mse = np.mean(diff * diff)
    if mse <= 0:
        return 99.0
    return float(10.0 * np.log10(255.0 * 255.0 / mse))


def bench_single_image(arr: np.ndarray, cfg: MozCfg, *, icc: bytes | None, ref: np.ndarray) -> dict:
    t0 = time.perf_counter()
    jpeg = encode_jpeg(arr, cfg, icc=icc)
    ms = (time.perf_counter() - t0) * 1000.0
    dec = imagecodecs.mozjpeg_decode(jpeg)
    return {
        "bytes": len(jpeg),
        "ms": ms,
        "psnr": psnr(ref, dec),
    }


def encode_uhdr(scrgb: np.ndarray, cfg: MozCfg) -> tuple[int, float, bool]:
    from hdr_converter.core import gainmap_pipeline
    from hdr_converter.core.gainmap_math import compute_gainmap_from_scrgb
    from hdr_converter.core.sdr_tonemap import build_sdr_base_from_scrgb
    from hdr_converter.core.assets import get_hdr_icc
    from hdr_converter.core.hdr_options import resolve_gainmap_tonemap

    tonemap = resolve_gainmap_tonemap(SdrToneMap.HABLE_MAX, TransferCurve.PQ)
    icc_base = get_baseline_display_icc(Gamut.P3)
    icc_gain = get_hdr_icc(Gamut.P3, TransferCurve.PQ)

    gain_u8, metadata, _ = compute_gainmap_from_scrgb(
        scrgb,
        Gamut.P3,
        TransferCurve.PQ,
        tonemap,
        scale=2,
        multichannel=False,
    )
    sdr_rgb = build_sdr_base_from_scrgb(scrgb, Gamut.P3, tonemap, base_bits=8)
    gain_rgb = np.dstack([gain_u8, gain_u8, gain_u8])

    t0 = time.perf_counter()
    base_jpeg = encode_jpeg(sdr_rgb, cfg, icc=icc_base)
    gain_jpeg = encode_jpeg(gain_rgb, cfg, icc=icc_gain)
    uhdr = mux_ultra_hdr_jpeg(base_jpeg, gain_jpeg, metadata)
    ms = (time.perf_counter() - t0) * 1000.0
    ok = bool(imagecodecs.ultrahdr_check(uhdr))
    return len(uhdr), ms, ok


def main() -> None:
    if not JXR.is_file():
        print(f"样张不存在: {JXR}")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    scrgb = decode_jxr(JXR)
    print(f"mozjpeg {imagecodecs.mozjpeg_version()}")
    print(f"样张: {JXR.name}\n")

    # --- 单图 SDR baseline 扫描（代表主图）---
    from hdr_converter.core.sdr_tonemap import build_sdr_base_from_scrgb
    from hdr_converter.core.hdr_options import resolve_gainmap_tonemap

    tonemap = resolve_gainmap_tonemap(SdrToneMap.HABLE_MAX, TransferCurve.PQ)
    sdr = build_sdr_base_from_scrgb(scrgb, Gamut.P3, tonemap, base_bits=8)
    icc = get_baseline_display_icc(Gamut.P3)
    ref_pillow = np.asarray(
        Image.open(io.BytesIO(pillow_jpeg(sdr, quality=QUALITY, icc=icc)))
    )

    configs: list[MozCfg] = [
        MozCfg("pillow_ref", level=QUALITY),  # handled separately
        MozCfg("current", optimize=True, progressive=True, notrellis=None),
        MozCfg("fast_noopt", optimize=False, progressive=False, notrellis=True),
        MozCfg("trellis", optimize=True, progressive=True, notrellis=False),
        MozCfg("no_trellis", optimize=True, progressive=True, notrellis=True),
        MozCfg("no_progressive", optimize=True, progressive=False, notrellis=False),
        MozCfg("prog_noopt", optimize=False, progressive=True, notrellis=False),
        MozCfg("420_trellis", optimize=True, progressive=True, notrellis=False, subsampling="420"),
        MozCfg("444_trellis", optimize=True, progressive=True, notrellis=False, subsampling="444"),
        MozCfg("qt0_trellis", optimize=True, progressive=True, notrellis=False, quanttable=0),
        MozCfg("qt1_trellis", optimize=True, progressive=True, notrellis=False, quanttable=1),
        MozCfg("qt2_trellis", optimize=True, progressive=True, notrellis=False, quanttable=2),
        MozCfg("qt3_trellis", optimize=True, progressive=True, notrellis=False, quanttable=3),
    ]

    print("=== SDR 主图单帧 (quality=90) ===")
    print(f"{'配置':<18} {'KB':>8} {'ms':>8} {'PSNR':>7} {'vs pillow':>10}")
    pillow_b = len(pillow_jpeg(sdr, quality=QUALITY, icc=icc))
    print(f"{'pillow':<18} {pillow_b/1024:8.1f} {'—':>8} {'—':>7} {'0.0%':>10}")

    rows: list[tuple[str, int, float, float, float]] = []
    for cfg in configs:
        if cfg.name == "pillow_ref":
            continue
        try:
            r = bench_single_image(sdr, cfg, icc=icc, ref=ref_pillow)
        except Exception as exc:
            print(f"{cfg.name:<18} ERROR {exc}")
            continue
        vs = 100.0 * (r["bytes"] - pillow_b) / pillow_b
        print(
            f"{cfg.name:<18} {r['bytes']/1024:8.1f} {r['ms']:8.0f} "
            f"{r['psnr']:7.2f} {vs:+9.1f}%"
        )
        rows.append((cfg.name, r["bytes"], r["ms"], r["psnr"], vs))

    # --- 完整 Ultra HDR 容器 ---
    print("\n=== Ultra HDR 全文件 (P3/PQ/mono, scale=2, q=90) ===")
    print(f"{'配置':<18} {'KB':>8} {'ms':>8} {'uhdr_ok':>8}")
    uhdr_rows: list[tuple[str, int, float, bool]] = []
    for cfg in configs:
        if cfg.name == "pillow_ref":
            continue
        try:
            size, ms, ok = encode_uhdr(scrgb, cfg)
        except Exception as exc:
            print(f"{cfg.name:<18} ERROR {exc}")
            continue
        print(f"{cfg.name:<18} {size/1024:8.1f} {ms:8.0f} {str(ok):>8}")
        uhdr_rows.append((cfg.name, size, ms, ok))

    # quality sweep with best compression preset
    print("\n=== quality 扫描 (trellis+optimize+progressive) ===")
    print(f"{'Q':>4} {'SDR KB':>8} {'UHDR KB':>9} {'SDR ms':>8} {'UHDR ms':>9}")
    for q in (82, 85, 88, 90, 92, 95):
        cfg = MozCfg(
            f"q{q}",
            level=q,
            optimize=True,
            progressive=True,
            notrellis=False,
        )
        try:
            sdr_r = bench_single_image(sdr, cfg, icc=icc, ref=ref_pillow)
            uhdr_b, uhdr_ms, _ = encode_uhdr(scrgb, cfg)
            print(
                f"{q:4d} {sdr_r['bytes']/1024:8.1f} {uhdr_b/1024:9.1f} "
                f"{sdr_r['ms']:8.0f} {uhdr_ms:9.0f}"
            )
        except Exception as exc:
            print(f"{q:4d} ERROR {exc}")

    # score: bytes per ms (lower bytes better, lower ms better) - efficiency ratio
    if uhdr_rows:
        base_size = next((s for n, s, _, _ in uhdr_rows if n == "current"), uhdr_rows[0][1])
        print("\n=== 性价比摘要 (相对 current) ===")
        cur = next((r for r in uhdr_rows if r[0] == "current"), None)
        if cur:
            _, cur_b, cur_ms, _ = cur
            scored = []
            for name, size, ms, ok in uhdr_rows:
                if not ok or name == "current":
                    continue
                save_pct = 100.0 * (cur_b - size) / cur_b
                time_ratio = ms / cur_ms if cur_ms > 0 else 1.0
                # 每多 1% 体积节省，时间增加多少倍
                eff = save_pct / max(time_ratio, 0.01) if save_pct > 0 else -time_ratio
                scored.append((name, size, ms, save_pct, time_ratio, eff))
            scored.sort(key=lambda x: (-x[3] / max(x[4], 0.1)))
            print(f"{'配置':<18} {'节省%':>7} {'耗时比':>7} {'得分':>8}")
            for name, _, ms, save_pct, tr, eff in scored[:8]:
                print(f"{name:<18} {save_pct:+6.1f}% {tr:6.2f}x {eff:7.2f}")


if __name__ == "__main__":
    main()
