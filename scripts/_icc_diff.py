"""对比 LR baseline ICC 与自生成 ICC。"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.baseline_icc import get_baseline_display_icc
from hdr_converter.core.cicp import Gamut

LR = ROOT / "src/hdr_converter/core/assets/display_p3_baseline_lr.icc"


def dump(data: bytes, name: str) -> None:
    print(f"\n=== {name} ({len(data)} B) ===")
    print("  header device:", data[12:16], "space:", data[16:20], "pcs:", data[20:24])
    print("  rendering intent:", data[67])
    n = struct.unpack_from(">I", data, 128)[0]
    print("  tags:", n)
    for i in range(n):
        e = 132 + i * 12
        sig = data[e : e + 4].decode("ascii", errors="replace")
        off, sz = struct.unpack_from(">II", data, e + 4)
        body = data[off : off + min(sz, 32)]
        print(f"    {sig!r:6} off={off:4} sz={sz:4} head={body[:16].hex()}")


def main() -> None:
    lr = LR.read_bytes()
    ours = get_baseline_display_icc(Gamut.P3)
    dump(lr, "LR")
    dump(ours, "OURS")
    # tag sig sets
    def sigs(d):
        n = struct.unpack_from(">I", d, 128)[0]
        return [d[132 + i * 12 : 132 + i * 12 + 4].decode() for i in range(n)]

    print("\nLR only:", set(sigs(lr)) - set(sigs(ours)))
    print("OURS only:", set(sigs(ours)) - set(sigs(lr)))


if __name__ == "__main__":
    main()
