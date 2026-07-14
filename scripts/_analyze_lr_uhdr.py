"""分析 Lightroom Ultra HDR 参考图元数据。"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from PIL import Image

LR = Path(r"C:\Users\OBC\Documents\Forza Horizon 6 2026_6_18 3_31_01 (1).jpg")


def extract_xmp(data: bytes) -> str | None:
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
        if marker == 0xE1 and b"xpacket" in payload[:64]:
            return payload.decode("utf-8", errors="replace")
        i += 2 + seg_len
    return None


def main() -> None:
    data = LR.read_bytes()
    xmp = extract_xmp(data)
    assert xmp is not None

    print("=== XMP namespaces ===")
    for ns in sorted(set(re.findall(r"xmlns:([A-Za-z0-9]+)=", xmp))):
        print(" ", ns)

    print("\n=== crs: HDR-related ===")
    for key, val in re.findall(r"crs:([A-Za-z0-9]+)=\"([^\"]*)\"", xmp):
        if "hdr" in key.lower():
            print(f"  crs:{key} = {val}")

    print("\n=== hdrgm tags ===")
    for key, val in re.findall(r"hdrgm:([A-Za-z0-9]+)=\"([^\"]*)\"", xmp):
        print(f"  hdrgm:{key} = {val}")
    for key in re.findall(r"hdrgm:([A-Za-z0-9]+)/>", xmp):
        print(f"  hdrgm:{key} (empty)")

    print("\n=== binary markers ===")
    for kw in (
        b"GainMap",
        b"Container",
        b"Semantic",
        b"Colorimetry",
        b"cicp",
        b"urn:iso:std:iso:ts:21496",
    ):
        print(f"  {kw.decode()}: {'yes' if kw in data else 'no'}")

    img = Image.open(LR)
    print("\n=== PIL info ===")
    for k in sorted(img.info):
        v = img.info[k]
        if isinstance(v, bytes):
            print(f"  {k}: bytes[{len(v)}]")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
