"""Pillow vs mozjpeg 体积对比（quality=90，Ultra HDR JPEG）。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.baseline_icc import get_baseline_display_icc
from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.converter import ConvertSettings, convert_file
from hdr_converter.core.encoders.base import OutputFormat
from hdr_converter.core.gainmap_pipeline import encode_gainmap_native_jpeg
from hdr_converter.core.hdr_options import HdrDeliveryMode, SdrToneMap
from hdr_converter.core.jpeg_encode import encode_rgb_jpeg, mozjpeg_available
from hdr_converter.core.decoders.jxr_decoder import decode_jxr

JXR = Path(r"C:\Users\OBC\Videos\Captures\Forza Horizon 6 2026_6_18 3_31_01.jxr")
OUT = ROOT / "scripts" / "_test_out"
QUALITY = 90


def _pillow_jpeg_bytes(rgb: np.ndarray, *, quality: int, icc: bytes | None) -> bytes:
    buf = io.BytesIO()
    kw: dict = {"format": "JPEG", "quality": quality}
    if icc:
        kw["icc_profile"] = icc
    Image.fromarray(rgb, "RGB").save(buf, **kw)
    return buf.getvalue()


def _encode_uhdr_with_jpeg_fn(
    scrgb: np.ndarray,
    out_path: Path,
    *,
    jpeg_fn,
) -> int:
    from hdr_converter.core import gainmap_pipeline

    original = gainmap_pipeline._encode_jpeg_bytes
    gainmap_pipeline._encode_jpeg_bytes = (
        lambda image, *, quality, icc=None: jpeg_fn(
            np.asarray(image), quality=quality, icc=icc
        )
    )
    try:
        from hdr_converter.core.encoders.base import EncodeOptions

        opts = EncodeOptions(
            output_format=OutputFormat.JPG,
            gamut=Gamut.P3,
            curve=TransferCurve.PQ,
            quality=QUALITY,
            hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
            sdr_tonemap=SdrToneMap.HABLE_MAX,
            gainmap_scale=2,
        )
        encode_gainmap_native_jpeg(scrgb, out_path, opts)
    finally:
        gainmap_pipeline._encode_jpeg_bytes = original
    return out_path.stat().st_size


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"mozjpeg 可用: {mozjpeg_available()}")
    if mozjpeg_available():
        import imagecodecs

        print(f"mozjpeg 版本: {imagecodecs.mozjpeg_version()}")

    if not JXR.is_file():
        print(f"样张不存在: {JXR}")
        print("跳过 Ultra HDR 全文件对比。")
        return

    scrgb = decode_jxr(JXR)
    moz_path = OUT / "_compare_mozjpeg_uhdr.jpg"
    pil_path = OUT / "_compare_pillow_uhdr.jpg"

    moz_size = _encode_uhdr_with_jpeg_fn(
        scrgb,
        moz_path,
        jpeg_fn=encode_rgb_jpeg,
    )
    pil_size = _encode_uhdr_with_jpeg_fn(
        scrgb,
        pil_path,
        jpeg_fn=_pillow_jpeg_bytes,
    )

    print(f"\nUltra HDR JPEG (P3/PQ/mono, scale=2, quality={QUALITY})")
    print(f"  Pillow 主+副图容器: {pil_size:,} B  ({pil_path.name})")
    print(f"  mozjpeg 主+副图容器: {moz_size:,} B  ({moz_path.name})")
    saved = pil_size - moz_size
    pct = 100.0 * saved / pil_size if pil_size else 0.0
    print(f"  节省: {saved:,} B ({pct:.1f}%)")

    # 生产路径（convert_file 已默认 mozjpeg）
    prod = OUT / "_compare_prod_uhdr.jpg"
    convert_file(
        JXR,
        prod,
        ConvertSettings(
            output_format=OutputFormat.JPG,
            gamut=Gamut.P3,
            curve=TransferCurve.PQ,
            encode_level=QUALITY,
            hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
            gainmap_scale=2,
            sdr_tonemap=SdrToneMap.HABLE_MAX,
        ),
    )
    print(f"  convert_file 输出: {prod.stat().st_size:,} B")


if __name__ == "__main__":
    main()
