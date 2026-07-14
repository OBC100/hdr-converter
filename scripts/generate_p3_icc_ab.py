"""P3 同像素、不同 ICC：LR 成品 vs 我们 patch 版（并排对比）。"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.baseline_icc import (
    get_baseline_display_icc,
    get_baseline_display_icc_patched,
)
from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.converter import ConvertSettings, convert_file
from hdr_converter.core.encoders.base import OutputFormat
from hdr_converter.core.hdr_options import HdrDeliveryMode, SdrToneMap
from hdr_converter.core.jpeg_icc import embed_baseline_icc_in_jpeg
from hdr_converter.core.decoders.jxr_decoder import decode_jxr
from hdr_converter.core.sdr_tonemap import build_sdr_base_from_scrgb

DEFAULT_JXR = Path(
    r"C:\Users\OBC\Videos\Captures\Horizon Forbidden West™ Complete Edition v1.5.80.0 "
    r"2026_2_16 18_27_39.png @ 66.7%(RGB_32_) _ 2026_6_7 4_58_59.jxr"
)
OUT_DIR = ROOT / "scripts" / "_test_out" / "p3_compare"


def _icc_rxyz(icc: bytes) -> list[float] | None:
    n = struct.unpack_from(">I", icc, 128)[0]
    for i in range(n):
        off = 132 + i * 12
        if icc[off : off + 4] != b"rXYZ":
            continue
        tag_off = struct.unpack_from(">I", icc, off + 4)[0]
        return [
            round(struct.unpack_from(">i", icc, tag_off + 8 + j)[0] / 65536.0, 4)
            for j in (0, 4, 8)
        ]
    return None


def _extract_icc(path: Path) -> bytes:
    data = path.read_bytes()
    chunks: list[bytes] = []
    i = 2
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
    return b"".join(chunks)


def main() -> None:
    jxr = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JXR
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    lr_icc_path = OUT_DIR / "horizon_pq_p3_hable_max_lr_icc.jpg"
    patched_icc_path = OUT_DIR / "horizon_pq_p3_hable_max_patched_icc.jpg"
    lr_sdr_path = OUT_DIR / "horizon_pq_p3_hable_max_sdr_base_lr_icc.jpg"
    patched_sdr_path = OUT_DIR / "horizon_pq_p3_hable_max_sdr_base_patched_icc.jpg"

    print("Encoding P3 Ultra HDR (pixels once, ICC swapped for A/B) ...")
    convert_file(
        jxr,
        lr_icc_path,
        ConvertSettings(
            output_format=OutputFormat.JPG,
            gamut=Gamut.P3,
            curve=TransferCurve.PQ,
            hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
            sdr_tonemap=SdrToneMap.HABLE_MAX,
            gainmap_scale=2,
            encode_level=90,
        ),
    )

    lr_icc = get_baseline_display_icc(Gamut.P3)
    patched_icc = get_baseline_display_icc_patched(Gamut.P3)
    patched_icc_path.write_bytes(
        embed_baseline_icc_in_jpeg(lr_icc_path.read_bytes(), patched_icc),
    )

    scrgb = decode_jxr(jxr)
    sdr = build_sdr_base_from_scrgb(scrgb, Gamut.P3, SdrToneMap.HABLE_MAX)
    Image.fromarray(sdr, "RGB").save(lr_sdr_path, quality=95, icc_profile=lr_icc)
    Image.fromarray(sdr, "RGB").save(patched_sdr_path, quality=95, icc_profile=patched_icc)

    lr_bytes = lr_icc_path.read_bytes()
    patched_bytes = patched_icc_path.read_bytes()
    report = {
        "input_jxr": str(jxr),
        "note": "像素相同，仅 ICC 不同",
        "lr_icc": {
            "ultrahdr": str(lr_icc_path),
            "sdr_base": str(lr_sdr_path),
            "icc_len": len(_extract_icc(lr_icc_path)),
            "rXYZ": _icc_rxyz(_extract_icc(lr_icc_path)),
            "display_p3": b"Display P3" in _extract_icc(lr_icc_path),
        },
        "patched_icc": {
            "ultrahdr": str(patched_icc_path),
            "sdr_base": str(patched_sdr_path),
            "icc_len": len(_extract_icc(patched_icc_path)),
            "rXYZ": _icc_rxyz(_extract_icc(patched_icc_path)),
            "display_p3": b"Display P3" in _extract_icc(patched_icc_path),
        },
        "pixel_payload_same": lr_bytes == patched_bytes,
    }

    report_path = OUT_DIR / "p3_icc_ab_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"LR ICC UHDR:      {lr_icc_path}")
    print(f"Patched ICC UHDR: {patched_icc_path}")
    print(f"LR ICC SDR:       {lr_sdr_path}")
    print(f"Patched ICC SDR:  {patched_sdr_path}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
