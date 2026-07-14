import struct
from pathlib import Path

def iso_segments(path: Path):
    d = path.read_bytes()
    eois = [i for i in range(len(d) - 1) if d[i : i + 2] == b"\xff\xd9"]
    print(path.name, "size", len(d), "EOIs", eois)
    i = 2
    while i < len(d) - 1:
        if d[i] != 0xFF:
            i += 1
            continue
        m = d[i + 1]
        if m == 0xDA:
            ln = struct.unpack(">H", d[i + 2 : i + 4])[0]
            i += 2 + ln
            continue
        if m == 0xD9:
            i += 2
            continue
        if m == 0x00 or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        ln = struct.unpack(">H", d[i + 2 : i + 4])[0]
        pl = d[i + 4 : i + 2 + ln]
        if m == 0xE2 and pl.startswith(b"urn:"):
            where = "primary" if i < eois[0] else "secondary"
            meta = pl[28:]
            print(f"  @{i} {where} seg={ln} meta_len={len(meta)} meta={meta.hex()}")
        i += 2 + ln

out = Path(r"c:\Users\OBC\source\repos\OBC100\new project\scripts\_test_out")
for n in ("native_uhdr.jpg", "libuhdr_ref.jpg"):
    p = out / n
    if p.exists():
        iso_segments(p)
lr = Path(r"C:\Users\OBC\Documents\Forza Horizon 6 2026_6_18 3_31_01 (1).jpg")
if lr.exists():
    iso_segments(lr)
