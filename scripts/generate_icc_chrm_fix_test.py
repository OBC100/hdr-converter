"""ICC chrm 修复效果对比样张（与 unified_pipeline 同参数）。"""

from __future__ import annotations

import json
import struct
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
from hdr_converter.core.hdr_options import HdrDeliveryMode, SdrToneMap
from hdr_converter.core.decoders.jxr_decoder import decode_jxr
from hdr_converter.core.sdr_tonemap import build_sdr_base_from_scrgb

DEFAULT_JXR = Path(
    r"C:\Users\OBC\Videos\Captures\Horizon Forbidden West™ Complete Edition v1.5.80.0 "
    r"2026_2_16 18_27_39.png @ 66.7%(RGB_32_) _ 2026_6_7 4_58_59.jxr"
)


def _read_chrm(icc: bytes) -> list[tuple[float, float]] | None:
    n = struct.unpack_from(">I", icc, 128)[0]
    for i in range(n):
        off = 132 + i * 12
        if icc[off : off + 4] != b"chrm":
            continue
        tag_off = struct.unpack_from(">I", icc, off + 4)[0]
        return [
            (
                struct.unpack_from(">i", icc, tag_off + 12 + c * 8)[0] / 65536.0,
                struct.unpack_from(">i", icc, tag_off + 12 + c * 8 + 4)[0] / 65536.0,
            )
            for c in range(3)
        ]
    return None


def _extract_icc_from_jpeg(data: bytes) -> bytes | None:
    i = 2
    chunks: list[bytes] = []
    while i < len(data) - 1:
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xDA:
            break
        if marker in (0xD8, 0xD9):
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        payload = data[i + 4 : i + 2 + seg_len]
        if marker == 0xE2 and payload.startswith(b"ICC_PROFILE"):
            chunks.append(payload[14:])
        i += 2 + seg_len
    return b"".join(chunks) if chunks else None


def main() -> None:
    jxr = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JXR
    out_dir = ROOT / "scripts" / "_test_out" / "jpg_tonemap_compare"
    out_dir.mkdir(parents=True, exist_ok=True)

    uhdr_path = out_dir / "horizon_pq_bt2020_icc_chrm_fix.jpg"
    sdr_path = out_dir / "horizon_pq_bt2020_icc_chrm_fix_sdr_base.jpg"
    old_path = out_dir / "horizon_pq_bt2020_unified_pipeline.jpg"

    print("Encoding Ultra HDR JPG (hable_max, BT.2020, PQ)...")
    convert_file(
        jxr,
        uhdr_path,
        ConvertSettings(
            output_format=OutputFormat.JPG,
            gamut=Gamut.BT2020,
            curve=TransferCurve.PQ,
            hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
            sdr_tonemap=SdrToneMap.HABLE_MAX,
            gainmap_scale=2,
            encode_level=90,
        ),
    )

    print("Exporting SDR baseline with fixed ICC...")
    scrgb = decode_jxr(jxr)
    sdr = build_sdr_base_from_scrgb(scrgb, Gamut.BT2020, SdrToneMap.HABLE_MAX, base_bits=8)
    Image.fromarray(sdr, "RGB").save(
        sdr_path,
        quality=95,
        icc_profile=get_baseline_display_icc(Gamut.BT2020),
    )

    new_icc = _extract_icc_from_jpeg(uhdr_path.read_bytes())
    old_icc = _extract_icc_from_jpeg(old_path.read_bytes()) if old_path.exists() else None

    report = {
        "input": str(jxr),
        "tonemap": "hable_max",
        "gamut": "BT2020",
        "fix": "chrm tag synced with r/g/b XYZ primaries",
        "ultrahdr_jpg": str(uhdr_path),
        "sdr_base_jpg": str(sdr_path),
        "old_unified_pipeline": str(old_path) if old_path.exists() else None,
        "chrm_new": _read_chrm(new_icc) if new_icc else None,
        "chrm_old": _read_chrm(old_icc) if old_icc else None,
        "jpg_bytes": uhdr_path.stat().st_size,
    }
    report_path = out_dir / "icc_chrm_fix_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("chrm old:", report["chrm_old"])
    print("chrm new:", report["chrm_new"])
    print(f"UHDR: {uhdr_path}")
    print(f"SDR base: {sdr_path}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
