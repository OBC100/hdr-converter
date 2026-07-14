"""P3 vs BT.2020 Ultra HDR 对比样张（对齐 LR Display P3 baseline 参考）。"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import colour
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
LR_REF = Path(r"C:\Users\OBC\Documents\Forza Horizon 6 2026_6_18 3_31_01 (1).jpg")

SPOTS = {
    "blue": "#1473E6",
    "skin": "#F7E9E1",
    "gray": "#323232",
}


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


def _extract_icc_from_jpeg(path: Path) -> bytes:
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


def _stats(path: Path, label: str) -> dict:
    img = Image.open(path)
    arr = np.array(img)
    br = arr[..., 2].astype(np.int32) - arr[..., 0].astype(np.int32)
    icc = img.info.get("icc_profile") or _extract_icc_from_jpeg(path)
    return {
        "label": label,
        "path": str(path),
        "size": list(img.size),
        "mean_rgb": [round(float(x), 1) for x in arr.reshape(-1, 3).mean(0)],
        "mean_br": round(float(br.mean()), 2),
        "icc_len": len(icc),
        "icc_rxyz": _icc_rxyz(icc) if icc else None,
        "display_p3_icc": b"Display P3" in icc if icc else False,
    }


def _find_spot(scrgb: np.ndarray, target_hex: str) -> tuple[int, int]:
    tgt = np.array([int(target_hex[i : i + 2], 16) for i in (1, 3, 5)], float)
    preview = np.clip(scrgb[..., :3], 0, 1)
    u8 = (colour.cctf_encoding(preview, function="sRGB") * 255).astype(np.uint8)
    dist = np.sum((u8.astype(float) - tgt) ** 2, axis=-1)
    idx = np.unravel_index(int(np.argmin(dist)), scrgb.shape[:2])
    return int(idx[0]), int(idx[1])


def main() -> None:
    jxr = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JXR
    out_dir = ROOT / "scripts" / "_test_out" / "p3_compare"
    out_dir.mkdir(parents=True, exist_ok=True)

    for gamut, name in ((Gamut.P3, "p3"), (Gamut.BT2020, "bt2020")):
        uhdr_path = out_dir / f"horizon_pq_{name}_hable_max.jpg"
        sdr_path = out_dir / f"horizon_pq_{name}_hable_max_sdr_base.jpg"
        print(f"Encoding {name} ...")
        convert_file(
            jxr,
            uhdr_path,
            ConvertSettings(
                output_format=OutputFormat.JPG,
                gamut=gamut,
                curve=TransferCurve.PQ,
                hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
                sdr_tonemap=SdrToneMap.HABLE_MAX,
                gainmap_scale=2,
                encode_level=90,
            ),
        )
        scrgb = decode_jxr(jxr)
        sdr = build_sdr_base_from_scrgb(scrgb, gamut, SdrToneMap.HABLE_MAX)
        Image.fromarray(sdr, "RGB").save(
            sdr_path,
            quality=95,
            icc_profile=get_baseline_display_icc(gamut),
        )

    scrgb = decode_jxr(jxr)
    spots: dict = {}
    for spot, hx in SPOTS.items():
        row, col = _find_spot(scrgb, hx)
        colors: dict[str, str] = {"jxr_ref": hx}
        for gamut, name in ((Gamut.P3, "p3"), (Gamut.BT2020, "bt2020")):
            sdr = build_sdr_base_from_scrgb(scrgb, gamut, SdrToneMap.HABLE_MAX)
            px = sdr[row, col]
            colors[name] = f"#{px[0]:02X}{px[1]:02X}{px[2]:02X}"
        spots[spot] = {"rc": [row, col], "colors": colors}

    lr_icc = Image.open(LR_REF).info.get("icc_profile", b"")
    our_p3_icc = get_baseline_display_icc(Gamut.P3)

    report = {
        "input_jxr": str(jxr),
        "lr_reference": _stats(LR_REF, "LR Forza") if LR_REF.exists() else None,
        "ultrahdr": [
            _stats(out_dir / f"horizon_pq_{n}_hable_max.jpg", n) for n in ("p3", "bt2020")
        ],
        "sdr_base": [
            _stats(out_dir / f"horizon_pq_{n}_hable_max_sdr_base.jpg", f"{n} sdr")
            for n in ("p3", "bt2020")
        ],
        "icc_compare": {
            "lr": {"len": len(lr_icc), "rXYZ": _icc_rxyz(lr_icc)},
            "ours_p3": {"len": len(our_p3_icc), "rXYZ": _icc_rxyz(our_p3_icc)},
            "ours_bt2020": {
                "len": len(get_baseline_display_icc(Gamut.BT2020)),
                "rXYZ": _icc_rxyz(get_baseline_display_icc(Gamut.BT2020)),
            },
            "lr_matches_our_p3_bytes": lr_icc == our_p3_icc,
        },
        "spots": spots,
    }

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"P3 UHDR: {out_dir / 'horizon_pq_p3_hable_max.jpg'}")
    print(f"BT2020 UHDR: {out_dir / 'horizon_pq_bt2020_hable_max.jpg'}")


if __name__ == "__main__":
    main()
