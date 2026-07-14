"""完整解析 LR baseline ICC 结构。"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LR_ICC = ROOT / "src/hdr_converter/core/assets/display_p3_baseline_lr.icc"
LR_JPG = Path(r"C:\Users\OBC\Documents\Forza Horizon 6 2026_6_18 3_31_01 (1).jpg")


def extract_icc_from_jpeg(data: bytes) -> bytes:
    chunks: list[bytes] = []
    i = 2
    while i < len(data) - 1:
        if data[i] != 0xFF:
            break
        m = data[i + 1]
        if m == 0xDA:
            break
        if m in (0xD8, 0xD9):
            i += 2
            continue
        ln = struct.unpack(">H", data[i + 2 : i + 4])[0]
        pl = data[i + 4 : i + 2 + ln]
        if m == 0xE2 and pl.startswith(b"ICC_PROFILE"):
            chunks.append(pl[14:])
        i += 2 + ln
    return b"".join(chunks)


def s15(data: bytes, off: int) -> float:
    return struct.unpack_from(">i", data, off)[0] / 65536.0


def parse_icc(data: bytes, name: str) -> None:
    print(f"\n{'='*60}\n{name} ({len(data)} bytes)")
    print("profile size field:", struct.unpack_from(">I", data, 0)[0])
    print("CMM:", data[4:8])
    print("version:", data[8:12].hex())
    print("class:", data[12:16], "colorspace:", data[16:20], "pcs:", data[20:24])
    print("date:", data[24:36])
    print("signature:", data[36:40])
    print("platform:", data[40:44])
    print("flags:", data[44:48].hex())
    print("manufacturer:", data[48:52], "model:", data[52:56])
    print("attributes:", data[64:68].hex())
    print("intent:", data[67])
    print("illuminant XYZ:", [round(s15(data, 68 + j), 5) for j in (0, 4, 8)])
    print("creator:", data[80:84])

    n = struct.unpack_from(">I", data, 128)[0]
    print("tag count:", n)
    tags: dict[str, tuple[int, int]] = {}
    for i in range(n):
        e = 132 + i * 12
        sig = data[e : e + 4].decode("ascii", errors="replace")
        off, sz = struct.unpack_from(">II", data, e + 4)
        tags[sig] = (off, sz)
        print(f"  {sig:4} off={off:4} sz={sz:4}")

    for sig, (off, sz) in tags.items():
        body = data[off : off + sz]
        print(f"\n--- {sig} ---")
        if sig in ("rXYZ", "gXYZ", "bXYZ", "wtpt"):
            if body[:4] == b"XYZ ":
                xyz = [round(s15(body, 8 + j), 5) for j in (0, 4, 8)]
                print("  XYZ:", xyz)
        elif sig in ("rTRC", "gTRC", "bTRC"):
            if body[:4] == b"para":
                ft = struct.unpack_from(">H", body, 8)[0]
                print("  para type:", ft)
                if ft == 3:
                    print("  gamma:", round(s15(body, 12), 6))
                elif ft == 4:
                    for j in range(5):
                        print(f"  p{j}:", round(s15(body, 12 + j * 4), 6))
            elif body[:4] == b"curv":
                npts = struct.unpack_from(">I", body, 8)[0]
                print("  curv points:", npts)
        elif sig == "chad":
            if body[:4] == b"sf32":
                print("  matrix:")
                for row in range(3):
                    vals = [round(s15(body, 8 + row * 12 + col * 4), 6) for col in range(3)]
                    print("   ", vals)
        elif sig == "desc":
            if body[:4] == b"desc":
                typ = struct.unpack_from(">I", body, 8)[0]
                print("  desc type:", typ)
                if typ == 0:
                    nchars = struct.unpack_from(">I", body, 12)[0]
                    print("  text:", body[16 : 16 + nchars - 1].decode("ascii", errors="replace"))
        elif sig == "cprt":
            if body[:4] == b"text":
                print("  text:", body[8:].split(b"\x00")[0].decode("ascii", errors="replace"))


def main() -> None:
    if LR_ICC.exists():
        parse_icc(LR_ICC.read_bytes(), "bundled LR ICC")
    if LR_JPG.exists():
        jpg_icc = extract_icc_from_jpeg(LR_JPG.read_bytes())
        parse_icc(jpg_icc, "LR JPEG extracted ICC")
        if LR_ICC.exists() and jpg_icc != LR_ICC.read_bytes():
            print("\n*** bundled vs JPEG extracted DIFFER ***")
        else:
            print("\n*** bundled matches JPEG extracted ***")

    from hdr_converter.core.baseline_icc import get_baseline_display_icc
    from hdr_converter.core.cicp import Gamut

    get_baseline_display_icc.cache_clear()
    parse_icc(get_baseline_display_icc(Gamut.P3), "ours generated")


if __name__ == "__main__":
    main()
