"""对比 apple_baseline_icc 生成的各色域 ICC 差异。"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.assets.apple_baseline_icc import create_apple_baseline_icc_profile
from hdr_converter.core.assets.libjxl_pq_icc import _D65, _PRIMARIES, gamut_to_primaries
from hdr_converter.core.cicp import Gamut

LR_ICC = ROOT / "src/hdr_converter/core/assets/display_p3_baseline_lr.icc"


def s15(data: bytes, off: int) -> float:
    return struct.unpack_from(">i", data, off)[0] / 65536.0


def parse_xyz_tag(blob: bytes) -> list[float]:
    return [round(s15(blob, 8 + j), 6) for j in (0, 4, 8)]


def parse_desc(data: bytes, off: int, sz: int) -> str:
    body = data[off : off + sz]
    end = body.find(b"\x00", 12)
    return body[12:end].decode("ascii")


def parse_tags(data: bytes) -> dict[str, tuple[int, int]]:
    n = struct.unpack_from(">I", data, 128)[0]
    tags: dict[str, tuple[int, int]] = {}
    for i in range(n):
        e = 132 + i * 12
        sig = data[e : e + 4].decode("ascii")
        off, sz = struct.unpack_from(">II", data, e + 4)
        tags[sig] = (off, sz)
    return tags


def diff_regions(a: bytes, b: bytes) -> list[tuple[int, int]]:
    regions: list[tuple[int, int]] = []
    for i, (x, y) in enumerate(zip(a, b)):
        if x == y:
            continue
        if not regions or i > regions[-1][1] + 1:
            regions.append((i, i))
        else:
            s, _ = regions[-1]
            regions[-1] = (s, i)
    return regions


def main() -> None:
    create_apple_baseline_icc_profile.cache_clear()
    profiles = {g: create_apple_baseline_icc_profile(g) for g in Gamut}
    lr = LR_ICC.read_bytes() if LR_ICC.exists() else None

    print("=== 文件大小 ===")
    for g in Gamut:
        print(f"  {g.value}: {len(profiles[g])} B")
    if lr:
        print(f"  LR reference (P3): {len(lr)} B")

    print("\n=== 输入原色 xy (白点 D65) ===")
    for g in Gamut:
        prim = gamut_to_primaries(g)
        p = _PRIMARIES[prim]
        print(f"  {g.value}:")
        print(f"    R {p[0]}  G {p[1]}  B {p[2]}")

    print("\n=== 完全相同（三色域共享）===")
    items = [
        "128B 头（byte 4–127 来自 LR 模板）：CMM=appl, class=mntr, space=RGB, PCS=XYZ",
        "wtpt：PCS D50 白点 (0.9642, 1.0, 0.8249)",
        "rTRC/gTRC/bTRC：共享 32B，para type 3，sRGB 五参数曲线",
        "chad：44B，D65→D50 Bradford 适应矩阵",
        "cprt：text，Copyright Apple Inc., 2015",
        "tag 表：10 项 × 12B + count，总文件 548B",
        "tag 顺序：desc, cprt, wtpt, r/g/bXYZ, rTRC, chad, bTRC, gTRC",
    ]
    for line in items:
        print(f"  - {line}")

    tags_srgb = parse_tags(profiles[Gamut.SRGB])
    for sig in ("cprt", "wtpt", "rTRC", "chad"):
        blobs = []
        for g in Gamut:
            off, sz = parse_tags(profiles[g])[sig]
            blobs.append(profiles[g][off : off + sz])
        same = blobs[0] == blobs[1] == blobs[2]
        print(f"  校验 {sig} 三色域一致: {same}")

    print("\n=== 随色域变化 ===")
    hdr = f"{'Gamut':<8} {'desc':<12} {'rXYZ':<30} {'gXYZ':<30} {'bXYZ':<30}"
    print(hdr)
    for g in Gamut:
        data = profiles[g]
        tags = parse_tags(data)
        desc = parse_desc(data, *tags["desc"])
        r = parse_xyz_tag(data[tags["rXYZ"][0] : tags["rXYZ"][0] + tags["rXYZ"][1]])
        gr = parse_xyz_tag(data[tags["gXYZ"][0] : tags["gXYZ"][0] + tags["gXYZ"][1]])
        b = parse_xyz_tag(data[tags["bXYZ"][0] : tags["bXYZ"][0] + tags["bXYZ"][1]])
        print(f"{g.value:<8} {desc:<12} {str(r):<30} {str(gr):<30} {str(b):<30}")

    print("\n=== 色域两两字节差异 ===")
    for ga, gb in [(Gamut.SRGB, Gamut.P3), (Gamut.SRGB, Gamut.BT2020), (Gamut.P3, Gamut.BT2020)]:
        da, db = profiles[ga], profiles[gb]
        nd = sum(1 for x, y in zip(da, db) if x != y)
        regions = diff_regions(da, db)
        print(f"  {ga.value} vs {gb.value}: {nd} bytes, {len(regions)} region(s)")
        for s, e in regions:
            print(f"    offset {s}–{e} ({e - s + 1} B)")

    if lr:
        print("\n=== 生成 P3 vs LR 参考 ===")
        p3 = profiles[Gamut.P3]
        nd = sum(1 for x, y in zip(lr, p3) if x != y)
        print(f"  差异字节: {nd} / {len(lr)}")
        for s, e in diff_regions(lr, p3):
            print(f"    offset {s}–{e} ({e - s + 1} B)")
            if s >= 252:
                # likely rXYZ region
                for sig in ("desc", "rXYZ", "gXYZ", "bXYZ"):
                    off, sz = parse_tags(p3)[sig]
                    if off <= s < off + sz:
                        print(f"      → 落在 tag {sig}")


if __name__ == "__main__":
    main()
