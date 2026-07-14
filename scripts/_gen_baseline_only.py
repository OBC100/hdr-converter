"""各色域 SDR 基础图 JPEG（仅 baseline，无 Ultra HDR 容器）。"""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.baseline_icc import get_baseline_display_icc
from hdr_converter.core.cicp import Gamut
from hdr_converter.core.hdr_options import SdrToneMap
from hdr_converter.core.decoders.jxr_decoder import decode_jxr
from hdr_converter.core.sdr_tonemap import build_sdr_base_from_scrgb

JXR = Path(r"C:\Users\OBC\Videos\Captures\Forza Horizon 6 2026_6_18 3_31_01.jxr")
OUT = ROOT / "scripts" / "_test_out"
LR_ICC = ROOT / "src/hdr_converter/core/assets/display_p3_baseline_lr.icc"


def save_jpeg(path: Path, image: Image.Image, *, icc: bytes | None, quality: int = 90) -> None:
    buf = io.BytesIO()
    kw: dict = {"format": "JPEG", "quality": quality}
    if icc is not None:
        kw["icc_profile"] = icc
    image.save(buf, **kw)
    data = buf.getvalue()
    path.write_bytes(data)
    icc_len = len(icc) if icc else 0
    print(f"  {path.name}: {len(data) // 1024} KB, icc={icc_len} B, {image.size[0]}x{image.size[1]}")


def verify_icc_embedded(jpeg: bytes, expected_icc: bytes) -> bool:
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
    return b"".join(chunks) == expected_icc


def main() -> None:
    if not JXR.exists():
        print(f"样张不存在: {JXR}")
        sys.exit(1)

    OUT.mkdir(parents=True, exist_ok=True)
    get_baseline_display_icc.cache_clear()

    print(f"输入: {JXR.name}")
    scrgb = decode_jxr(JXR)
    tonemap = SdrToneMap.HABLE_MAX

    print(f"输出目录: {OUT}\n")

    for gamut in Gamut:
        icc = get_baseline_display_icc(gamut)
        sdr = build_sdr_base_from_scrgb(scrgb, gamut, tonemap, base_bits=8)
        image = Image.fromarray(sdr, "RGB")
        out_path = OUT / f"Forza_Horizon_6_baseline_{gamut.value}_icc.jpg"
        save_jpeg(out_path, image, icc=icc)
        ok = verify_icc_embedded(out_path.read_bytes(), icc)
        print(f"    ICC 嵌入校验: {'OK' if ok else 'FAIL'}")

    # 对照：无 ICC
    sdr_p3 = build_sdr_base_from_scrgb(scrgb, Gamut.P3, tonemap, base_bits=8)
    save_jpeg(OUT / "Forza_Horizon_6_baseline_no_icc.jpg", Image.fromarray(sdr_p3, "RGB"), icc=None)

    # 对照：LR 原始 P3 ICC（与生成器 A/B）
    if LR_ICC.exists():
        save_jpeg(
            OUT / "Forza_Horizon_6_baseline_p3_lr_icc.jpg",
            Image.fromarray(sdr_p3, "RGB"),
            icc=LR_ICC.read_bytes(),
        )

    print("\n请用 Windows 照片依次打开:")
    for gamut in Gamut:
        print(f"  - Forza_Horizon_6_baseline_{gamut.value}_icc.jpg")
    print("  - Forza_Horizon_6_baseline_no_icc.jpg (无 ICC)")
    print("  - Forza_Horizon_6_baseline_p3_lr_icc.jpg (LR 参考 ICC，仅 P3 像素)")


if __name__ == "__main__":
    main()
