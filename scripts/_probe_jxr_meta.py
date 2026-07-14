"""Probe what metadata / properties can be extracted from a Windows HDR JXR."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

OUT = Path(__file__).resolve().parents[1] / "scripts" / "_probe_jxr_meta_out.txt"


def log(msg: str = "") -> None:
    print(msg)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def find_jxr(arg: str) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    # fallback: glob by suffix timestamp if ™ causes issues
    caps = Path(r"C:\Users\OBC\Videos\Captures")
    hits = list(caps.glob("*13_21_29.jxr"))
    if hits:
        return hits[0]
    raise FileNotFoundError(arg)


def dump_tiff_ifds(data: bytes) -> None:
    """JPEG XR / HD Photo often uses a TIFF-like container (II*\\0)."""
    if len(data) < 8:
        log("file too small")
        return
    endian = data[:2]
    if endian == b"II":
        bo = "<"
        log("endian: little (II)")
    elif endian == b"MM":
        bo = ">"
        log("endian: big (MM)")
    else:
        log(f"not TIFF-like magic: {data[:16].hex()} {data[:16]!r}")
        return

    magic = struct.unpack(bo + "H", data[2:4])[0]
    log(f"TIFF magic number: {magic} (expect 42)")
    ifd_off = struct.unpack(bo + "I", data[4:8])[0]
    log(f"first IFD offset: {ifd_off}")

    # Known TIFF / HD Photo tag names (partial)
    TAGS = {
        254: "NewSubfileType",
        255: "SubfileType",
        256: "ImageWidth",
        257: "ImageLength",
        258: "BitsPerSample",
        259: "Compression",
        262: "PhotometricInterpretation",
        270: "ImageDescription",
        271: "Make",
        272: "Model",
        273: "StripOffsets",
        274: "Orientation",
        277: "SamplesPerPixel",
        278: "RowsPerStrip",
        279: "StripByteCounts",
        282: "XResolution",
        283: "YResolution",
        284: "PlanarConfiguration",
        296: "ResolutionUnit",
        305: "Software",
        306: "DateTime",
        315: "Artist",
        320: "ColorMap",
        338: "ExtraSamples",
        339: "SampleFormat",
        340: "SMinSampleValue",
        341: "SMaxSampleValue",
        34665: "ExifIFD",
        34853: "GPSIFD",
        700: "XMP",
        33723: "IPTC",
        34377: "Photoshop",
        34675: "ICCProfile",
        # HD Photo / WMP specific (common)
        48129: "PixelFormat",
        48130: "Transformation",
        48131: "ImageType",
        48132: "ImageWidth_WMP",
        48133: "ImageHeight_WMP",
        48134: "WidthResolution",
        48135: "HeightResolution",
        48136: "ImageOffset",
        48137: "ImageByteCount",
        48138: "AlphaOffset",
        48139: "AlphaByteCount",
        48140: "ImageDataDiscard",
        48141: "AlphaDataDiscard",
        48256: "Padding",
    }

    TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

    visited = set()
    queue = [ifd_off]
    while queue:
        off = queue.pop(0)
        if off in visited or off <= 0 or off + 2 > len(data):
            continue
        visited.add(off)
        n = struct.unpack(bo + "H", data[off : off + 2])[0]
        log(f"\n--- IFD @ {off} entries={n} ---")
        p = off + 2
        for i in range(n):
            if p + 12 > len(data):
                break
            tag, typ, count = struct.unpack(bo + "HHI", data[p : p + 8])
            val_or_off = data[p + 8 : p + 12]
            name = TAGS.get(tag, f"Tag_{tag}")
            unit = TYPE_SIZE.get(typ, 1)
            nbytes = count * unit
            if nbytes <= 4:
                raw = val_or_off[:nbytes]
            else:
                (voff,) = struct.unpack(bo + "I", val_or_off)
                raw = data[voff : voff + nbytes] if voff + nbytes <= len(data) else b""

            preview = _format_value(bo, typ, count, raw)
            log(f"  {tag:5d} {name:28s} type={typ} count={count} -> {preview}")

            if tag == 34665 and typ == 4 and count == 1 and len(raw) >= 4:
                queue.append(struct.unpack(bo + "I", raw[:4])[0])
            if tag == 34853 and typ == 4 and count == 1 and len(raw) >= 4:
                queue.append(struct.unpack(bo + "I", raw[:4])[0])
            p += 12
        if p + 4 <= len(data):
            (next_ifd,) = struct.unpack(bo + "I", data[p : p + 4])
            log(f"  next IFD: {next_ifd}")
            if next_ifd:
                queue.append(next_ifd)


def _format_value(bo: str, typ: int, count: int, raw: bytes) -> str:
    if not raw:
        return "<empty/oob>"
    try:
        if typ == 2:  # ASCII
            return repr(raw.split(b"\x00", 1)[0].decode("ascii", errors="replace"))
        if typ == 1 and count <= 16:
            return raw.hex()
        if typ == 3 and count <= 8:
            vals = struct.unpack(bo + f"{count}H", raw[: count * 2])
            return str(vals if count > 1 else vals[0])
        if typ == 4 and count <= 8:
            vals = struct.unpack(bo + f"{count}I", raw[: count * 4])
            return str(vals if count > 1 else vals[0])
        if typ == 5 and count <= 4:
            out = []
            for i in range(count):
                n, d = struct.unpack(bo + "II", raw[i * 8 : i * 8 + 8])
                out.append(f"{n}/{d}" + (f" ({n / d:.4g})" if d else ""))
            return ", ".join(out)
        if typ == 11 and count <= 8:
            vals = struct.unpack(bo + f"{count}f", raw[: count * 4])
            return str(vals if count > 1 else vals[0])
        if typ == 12 and count <= 4:
            vals = struct.unpack(bo + f"{count}d", raw[: count * 8])
            return str(vals if count > 1 else vals[0])
        if typ == 7:  # UNDEFINED
            if count <= 32:
                return raw.hex()
            return f"<{count} bytes> head={raw[:16].hex()}"
        return f"<{len(raw)} bytes> head={raw[:16].hex()}"
    except Exception as exc:
        return f"<parse err {exc}> {raw[:16].hex()}"


def pixel_stats(path: Path) -> None:
    from imagecodecs import jpegxr_decode

    from hdr_converter.core.color_pipeline import compute_content_light_level
    from hdr_converter.core.decoders.jxr_decoder import decode_jxr

    log("\n=== decode via project decode_jxr ===")
    arr = decode_jxr(path)
    log(f"shape={arr.shape} dtype={arr.dtype}")
    rgb = arr[..., :3]
    log(f"min={float(rgb.min()):.6g} max={float(rgb.max()):.6g}")
    for q in (0.1, 1, 50, 99, 99.9, 99.99):
        log(f"p{q}={float(np.percentile(rgb, q)):.6g}")
    log(f"frac>1={float((rgb > 1).mean()):.6g} frac>2={float((rgb > 2).mean()):.6g} frac>10={float((rgb > 10).mean()):.6g}")
    if arr.shape[-1] >= 4:
        a = arr[..., 3]
        log(f"alpha min={float(a.min())} max={float(a.max())} mean={float(a.mean()):.6g}")

    # Project MaxCLL/MaxFALL (scRGB linear, 1.0 = 10000 nits per PROJECT.md note variance)
    try:
        cll = compute_content_light_level(arr)
        log(f"project MaxCLL={cll.max_cll} MaxFALL={cll.max_fall}")
    except Exception as exc:
        log(f"CLL compute failed: {exc}")

    # Also try raw jpegxr_decode identity
    raw = jpegxr_decode(path.read_bytes())
    r = np.asarray(raw)
    log(f"jpegxr_decode shape={r.shape} dtype={r.dtype}")


def filesystem_meta(path: Path) -> None:
    st = path.stat()
    log("=== filesystem ===")
    log(f"name={path.name}")
    log(f"size={st.st_size}")
    log(f"ctime={st.st_ctime}")
    log(f"mtime={st.st_mtime}")


def main() -> None:
    if OUT.exists():
        OUT.unlink()
    arg = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\OBC\Videos\Captures\Horizon Forbidden West™ Complete Edition v1.5.80.0 2026_6_29 13_21_29.jxr"
    path = find_jxr(arg)
    filesystem_meta(path)
    data = path.read_bytes()
    log(f"\n=== container header ===")
    log(f"head16={data[:16].hex()} {data[:16]!r}")
    dump_tiff_ifds(data)
    # search for ascii markers
    log("\n=== ascii / xml markers ===")
    for needle in (b"<?xpacket", b"<x:xmpmeta", b"Exif", b"ICC_PROFILE", b"http://", b"Adobe", b"Microsoft"):
        idx = data.find(needle)
        log(f"find {needle!r}: {idx}")
    pixel_stats(path)
    log(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
