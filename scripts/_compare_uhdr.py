"""对比原生 vs libultrahdr Ultra HDR JPEG 结构。"""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import imagecodecs
import numpy as np
from PIL import Image

from hdr_converter.core.baseline_icc import get_baseline_display_icc
from hdr_converter.core.cicp import Gamut, TransferCurve
from hdr_converter.core.color_metadata import ultrahdr_kwargs
from hdr_converter.core.gainmap_core import pack_hdr_rgba16f
from hdr_converter.core.gainmap_pipeline import (
    encode_gainmap_native_jpeg,
    scrgb_to_hdr_linear,
)
from hdr_converter.core.gainmap_math import encode_iso_gainmap_metadata, default_gainmap_metadata
from hdr_converter.core.hdr_options import HdrDeliveryMode, SdrToneMap
from hdr_converter.core.jpeg_icc import embed_baseline_icc_in_jpeg
from hdr_converter.core.decoders.jxr_decoder import decode_jxr
from hdr_converter.core.sdr_tonemap import build_sdr_base_from_scrgb
from hdr_converter.core.encoders.base import EncodeOptions, OutputFormat
from hdr_converter.core.uhdr_jpeg_mux import mux_ultra_hdr_jpeg, generate_mpf

JXR = Path(
    r"C:\Users\OBC\Videos\Captures\Horizon Forbidden West™ Complete Edition v1.5.80.0 "
    r"2026_2_16 18_27_39.png @ 66.7%(RGB_32_) _ 2026_6_7 4_58_59.jxr"
)
LR = Path(r"C:\Users\OBC\Documents\Forza Horizon 6 2026_6_18 3_31_01 (1).jpg")
OUT = ROOT / "scripts" / "_test_out"


def parse_mpf(payload: bytes) -> dict:
    tiff = payload[4:]
    ifd = struct.unpack(">I", tiff[4:8])[0]
    pos = ifd + 2
    mp_off = None
    for _ in range(3):
        tag, typ, cnt, val = struct.unpack(">HHII", tiff[pos : pos + 12])
        pos += 12
        if tag == 0xB002:
            mp_off = val
    entries = []
    for i in range(2):
        o = mp_off + i * 16
        a, sz, off, d1, d2 = struct.unpack(">IIIHH", tiff[o : o + 16])
        entries.append({"attr": a, "size": sz, "offset": off, "dep": (d1, d2)})
    return {"mp_off": mp_off, "entries": entries, "hex": payload.hex()}


def analyze(path: Path, label: str) -> None:
    data = path.read_bytes()
    print(f"\n{'='*70}\n{label}: {path.name} ({len(data)} bytes)")
    i = 2
    segs = []
    while i < len(data) - 1:
        if data[i] != 0xFF:
            print(f"  BAD @ {i}: 0x{data[i]:02X}")
            break
        m = data[i + 1]
        if m == 0xDA:
            print(f"  SOS @ {i}, tail={len(data)-i}")
            break
        ln = struct.unpack(">H", data[i + 2 : i + 4])[0]
        pl = data[i + 4 : i + 2 + ln]
        name = {0xE0: "APP0", 0xE1: "APP1", 0xE2: "APP2"}.get(m, f"0x{m:02X}")
        extra = ""
        if m == 0xE2:
            if pl.startswith(b"ICC_PROFILE"):
                extra = f" ICC {pl[12]}/{pl[13]} icc={len(pl)-14}"
            elif pl.startswith(b"MPF"):
                extra = " MPF"
                mpf = parse_mpf(pl)
                for j, e in enumerate(mpf["entries"]):
                    end = e["offset"] + e["size"] if e["offset"] else e["size"]
                    bad = end > len(data) or (e["offset"] and e["offset"] >= len(data))
                    print(f"    MPF img{j}: attr=0x{e['attr']:08X} sz={e['size']} off={e['offset']} end={end} {'BAD' if bad else 'OK'}")
            elif pl.startswith(b"urn:"):
                extra = f" ISO payload={len(pl)-28}B hex={pl[28:min(28+20,len(pl))].hex()}"
        elif m == 0xE1 and pl.startswith(b"Exif"):
            extra = " Exif"
        print(f"  @{i:6d} {name}{extra} seg={ln}")
        segs.append((i, m, ln))
        i += 2 + ln
    # EOI positions
    eois = []
    j = 0
    while True:
        k = data.find(b"\xff\xd9", j)
        if k < 0:
            break
        eois.append(k)
        j = k + 2
    print(f"  EOIs: {eois}")


def encode_libultrahdr(path: Path) -> None:
    scrgb = decode_jxr(JXR)
    linear, cll = scrgb_to_hdr_linear(scrgb, Gamut.P3)
    alpha = np.full(scrgb.shape[:2], 255, dtype=np.uint8)
    sdr = np.dstack([build_sdr_base_from_scrgb(scrgb, Gamut.P3, SdrToneMap.HABLE_MAX), alpha])
    hdr = pack_hdr_rgba16f(linear)
    kw = ultrahdr_kwargs(TransferCurve.PQ, cll, gamut=Gamut.P3)
    kw["codec"] = "JPEG"
    kw["transfer"] = "LINEAR"
    raw = imagecodecs.ultrahdr_encode(hdr, level=90, sdr=sdr, scale=2, **kw)
    path.write_bytes(embed_baseline_icc_in_jpeg(raw, get_baseline_display_icc(Gamut.P3)))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    native = OUT / "native_uhdr.jpg"
    lib = OUT / "libuhdr_ref.jpg"

    if JXR.exists():
        scrgb = decode_jxr(JXR)
        opts = EncodeOptions(
            output_format=OutputFormat.JPG,
            gamut=Gamut.P3,
            curve=TransferCurve.PQ,
            hdr_delivery=HdrDeliveryMode.GAINMAP_MONO,
            sdr_tonemap=SdrToneMap.HABLE_MAX,
            gainmap_scale=2,
            quality=90,
        )
        encode_gainmap_native_jpeg(scrgb, native, opts)
        encode_libultrahdr(lib)

    for p, lb in [(native, "NATIVE"), (lib, "LIBUHDR"), (LR, "LR")]:
        if p.exists():
            analyze(p, lb)

    # ISO secondary payload compare
    if native.exists() and lib.exists():
        def iso_sec(d):
            i = 2
            while i < len(d) - 1:
                if d[i] != 0xFF:
                    break
                m = d[i + 1]
                if m == 0xDA:
                    break
                ln = struct.unpack(">H", d[i + 2 : i + 4])[0]
                pl = d[i + 4 : i + 2 + ln]
                if m == 0xE2 and pl.startswith(b"urn:") and len(pl) > 28:
                    return pl[28:]
                i += 2 + ln
            return b""
        ns = iso_sec(native.read_bytes())
        ls = iso_sec(lib.read_bytes())
        print(f"\nISO secondary: native={len(ns)} lib={len(ls)} equal={ns==ls}")
        if ns != ls:
            print(" native hex:", ns.hex())
            print(" lib    hex:", ls.hex())


if __name__ == "__main__":
    main()
