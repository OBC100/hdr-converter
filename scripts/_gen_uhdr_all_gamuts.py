"""生成三色域 Ultra HDR JPEG（PQ / HLG）。"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.assets import get_hdr_icc
from hdr_converter.core.baseline_icc import get_baseline_display_icc
from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.converter import ConvertSettings, convert_file
from hdr_converter.core.encoders.base import OutputFormat
from hdr_converter.core.gainmap_math import (
    hdr_reference_nits,
    resolve_hdr_peak_nits,
)
from hdr_converter.core.hdr_options import HdrDeliveryMode, SdrToneMap

JXR = Path(r"C:\Users\OBC\Videos\Captures\Forza Horizon 6 2026_6_18 3_31_01.jxr")
OUT = ROOT / "scripts" / "_test_out"

_CURVE_MAP = {
    "pq": TransferCurve.PQ,
    "hlg": TransferCurve.HLG,
}


def extract_primary_icc(jpeg: bytes) -> bytes:
    chunks: list[bytes] = []
    i = 2
    while i < len(jpeg) - 1:
        if jpeg[i] != 0xFF:
            break
        m = jpeg[i + 1]
        if m == 0xDA:
            break
        if m in (0xD8, 0xD9):
            i += 2
            continue
        ln = struct.unpack(">H", jpeg[i + 2 : i + 4])[0]
        pl = jpeg[i + 4 : i + 2 + ln]
        if m == 0xE2 and pl.startswith(b"ICC_PROFILE"):
            chunks.append(pl[14:])
        i += 2 + ln
    return b"".join(chunks)


def extract_gainmap_icc(jpeg: bytes) -> bytes:
    eoi = jpeg.find(b"\xff\xd9")
    if eoi < 0:
        return b""
    tail = jpeg[eoi + 2 :]
    if not tail.startswith(b"\xff\xd8"):
        return b""
    chunks: list[bytes] = []
    i = 2
    while i < len(tail) - 1:
        if tail[i] != 0xFF:
            break
        m = tail[i + 1]
        if m == 0xDA:
            break
        if m in (0xD8, 0xD9):
            i += 2
            continue
        ln = struct.unpack(">H", tail[i + 2 : i + 4])[0]
        pl = tail[i + 4 : i + 2 + ln]
        if m == 0xE2 and pl.startswith(b"ICC_PROFILE"):
            chunks.append(pl[14:])
        i += 2 + ln
    return b"".join(chunks)


def cicp_transfer(icc: bytes) -> tuple[int, int] | None:
    n = struct.unpack_from(">I", icc, 128)[0]
    for i in range(n):
        off = 132 + i * 12
        if icc[off : off + 4] != b"cicp":
            continue
        tag_off = struct.unpack_from(">I", icc, off + 4)[0]
        body = tag_off + 8
        return icc[body], icc[body + 1]
    return None


def verify_uhdr(path: Path) -> str:
    try:
        import imagecodecs

        if imagecodecs.ultrahdr_check(path.read_bytes()):
            return "ultrahdr_check OK"
        return "ultrahdr_check FAIL"
    except Exception as exc:
        return f"skip ({exc})"


def generate(curve: TransferCurve, tonemap: SdrToneMap) -> None:
    from hdr_converter.core.decoders.jxr_decoder import decode_jxr
    from hdr_converter.core.color_pipeline import scrgb_to_gamut_linear

    curve_tag = curve.value
    scrgb = decode_jxr(JXR)
    linear = scrgb_to_gamut_linear(scrgb, Gamut.P3)
    dynamic_peak = resolve_hdr_peak_nits(linear, curve)
    static_peak = hdr_reference_nits(curve)
    print(
        f"\n=== {curve_tag.upper()} "
        f"(static_ref={static_peak:.0f} nits, "
        f"dynamic_MaxCLL={dynamic_peak:.0f} nits, "
        f"max_boost={dynamic_peak / 203:.3f}) ==="
    )

    settings = ConvertSettings(
        output_format=OutputFormat.JPG,
        curve=curve,
        hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
        sdr_tonemap=tonemap,
        gainmap_scale=2,
        encode_level=90,
    )

    for gamut in Gamut:
        out_path = OUT / f"Forza_Horizon_6_uhdr_{curve_tag}_{gamut.value}.jpg"
        result = convert_file(
            JXR,
            out_path,
            ConvertSettings(
                output_format=settings.output_format,
                gamut=gamut,
                curve=settings.curve,
                hdr_delivery=settings.hdr_delivery,
                sdr_tonemap=settings.sdr_tonemap,
                gainmap_scale=settings.gainmap_scale,
                encode_level=settings.encode_level,
            ),
        )
        data = out_path.read_bytes()

        base_icc = extract_primary_icc(data)
        gain_icc = extract_gainmap_icc(data)
        base_ok = base_icc == get_baseline_display_icc(gamut)
        gain_ok = gain_icc == get_hdr_icc(gamut, curve)
        cicp = cicp_transfer(gain_icc)
        expected_tf = 16 if curve == TransferCurve.PQ else 18
        tf_ok = cicp is not None and cicp[1] == expected_tf

        print(
            f"  {out_path.name}: {len(data) // 1024} KB, {result.width}x{result.height}, "
            f"baseline_icc={'OK' if base_ok else 'MISMATCH'}, "
            f"gain_icc={'OK' if gain_ok else 'MISMATCH'}, "
            f"cicp={cicp} tf={'OK' if tf_ok else 'BAD'}, "
            f"{verify_uhdr(out_path)}"
        )

    if curve == TransferCurve.PQ:
        alias = OUT / "Forza_Horizon_6_uhdr.jpg"
        alias.write_bytes((OUT / "Forza_Horizon_6_uhdr_pq_p3.jpg").read_bytes())
        print(f"  别名: {alias.name} <- Forza_Horizon_6_uhdr_pq_p3.jpg")
    elif curve == TransferCurve.HLG:
        alias = OUT / "Forza_Horizon_6_uhdr_hlg.jpg"
        alias.write_bytes((OUT / "Forza_Horizon_6_uhdr_hlg_p3.jpg").read_bytes())
        print(f"  别名: {alias.name} <- Forza_Horizon_6_uhdr_hlg_p3.jpg")


def main() -> None:
    parser = argparse.ArgumentParser(description="生成三色域 Ultra HDR JPEG")
    parser.add_argument(
        "--curve",
        choices=("pq", "hlg", "all"),
        default="all",
        help="传输曲线（默认 all）",
    )
    parser.add_argument(
        "--tonemap",
        default="hable_max",
        choices=[SdrToneMap.HABLE_MAX.value],
    )
    args = parser.parse_args()

    if not JXR.exists():
        print(f"样张不存在: {JXR}")
        sys.exit(1)

    OUT.mkdir(parents=True, exist_ok=True)
    get_baseline_display_icc.cache_clear()

    tonemap = SdrToneMap(args.tonemap)
    curves = list(_CURVE_MAP.values()) if args.curve == "all" else [_CURVE_MAP[args.curve]]

    print(f"输入: {JXR}")
    print(f"输出: {OUT}")
    print(f"tonemap: {tonemap.value}")

    for curve in curves:
        generate(curve, tonemap)

    print("\n完成。HDR 峰值 = 99.99% MaxCLL，钳位 [203, 10000] nits。")


if __name__ == "__main__":
    main()
