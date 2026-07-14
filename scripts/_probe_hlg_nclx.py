"""Probe HLG Direct AVIF/HEIF/PNG CICP/NCLX."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.color_metadata import (
    avif_imagecodecs_kwargs,
    get_direct_cicp_for_isobmff,
    nclx_save_kwargs,
)
from hdr_converter.core.converter import ConvertSettings, convert_file
from hdr_converter.core.encoders.base import OutputFormat
from hdr_converter.core.hdr_options import HdrDeliveryMode


def _safe(s: str) -> str:
    return s.encode("ascii", "backslashreplace").decode("ascii")


def dump_nclx(data: bytes) -> None:
    i = 0
    found = 0
    while True:
        j = data.find(b"nclx", i)
        if j < 0:
            break
        if data[j - 4 : j] == b"colr":
            payload = data[j + 4 : j + 4 + 7]
            cp, tc, mc = struct.unpack(">HHH", payload[:6])
            fr = payload[6]
            print(f"  nclx @{j}: cp={cp} tc={tc} mc={mc} full={fr}")
            found += 1
        i = j + 4
    if not found:
        print("  (no nclx)")


def dump_cicp_png(data: bytes) -> None:
    k = data.find(b"cICP")
    if k < 0:
        print("  (no cICP)")
        return
    ln = struct.unpack(">I", data[k - 4 : k])[0]
    payload = data[k + 4 : k + 4 + ln]
    print(f"  cICP: {list(payload)}")


def main() -> None:
    caps = Path(r"C:\Users\OBC\Videos\Captures")
    hits = list(caps.glob("*18_27_39*.jxr"))
    if not hits:
        hits = [f for f in caps.glob("*.jxr") if "Forbidden" in f.name]
    jxr = hits[0]
    print("input:", _safe(jxr.name))

    out = ROOT / "scripts" / "_test_out" / "hlg_probe"
    out.mkdir(parents=True, exist_ok=True)

    cicp = get_direct_cicp_for_isobmff(Gamut.P3, TransferCurve.HLG)
    print("expected direct CICP:", cicp)
    print("avif kwargs:", avif_imagecodecs_kwargs(cicp))
    print("nclx kwargs:", nclx_save_kwargs(cicp))
    print()

    for fmt, ext in (
        (OutputFormat.AVIF, "avif"),
        (OutputFormat.HEIF, "heif"),
        (OutputFormat.PNG, "png"),
    ):
        dst = out / f"hlg_p3_direct.{ext}"
        print(f"encoding {ext}...")
        convert_file(
            jxr,
            dst,
            ConvertSettings(
                output_format=fmt,
                gamut=Gamut.P3,
                curve=TransferCurve.HLG,
                quantize_bits=10,
                encode_level=90 if fmt != OutputFormat.PNG else 2,
                hdr_delivery=HdrDeliveryMode.DIRECT,
            ),
        )
        data = dst.read_bytes()
        print(f"  size={len(data)}")
        if ext == "png":
            dump_cicp_png(data)
        else:
            dump_nclx(data)
        print()


if __name__ == "__main__":
    main()
