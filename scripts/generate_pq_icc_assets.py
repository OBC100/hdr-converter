#!/usr/bin/env python3
"""生成 libjxl 兼容的 per-gamut PQ / HLG ICC 资源文件。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.assets.libjxl_pq_icc import (
    create_hlg_icc_for_gamut,
    create_linear_icc_for_gamut,
    create_pq_icc_for_gamut,
)
from hdr_converter.core.cicp import Gamut

_ASSET_DIR = ROOT / "src" / "hdr_converter" / "core" / "assets"
_FILES: list[tuple[Gamut, str, str]] = [
    (Gamut.BT2020, "rec2100_pq.icc", "pq"),
    (Gamut.SRGB, "srgb_pq.icc", "pq"),
    (Gamut.P3, "display_p3_pq.icc", "pq"),
    (Gamut.BT2020, "rec2100_hlg.icc", "hlg"),
    (Gamut.SRGB, "srgb_hlg.icc", "hlg"),
    (Gamut.P3, "display_p3_hlg.icc", "hlg"),
    (Gamut.BT2020, "rec2100_linear.icc", "linear"),
    (Gamut.SRGB, "srgb_linear.icc", "linear"),
    (Gamut.P3, "display_p3_linear.icc", "linear"),
]


def main() -> None:
    _ASSET_DIR.mkdir(parents=True, exist_ok=True)
    for gamut, filename, transfer in _FILES:
        if transfer == "pq":
            icc = create_pq_icc_for_gamut(gamut)
        elif transfer == "hlg":
            icc = create_hlg_icc_for_gamut(gamut)
        else:
            icc = create_linear_icc_for_gamut(gamut)
        path = _ASSET_DIR / filename
        path.write_bytes(icc)
        print(f"{gamut.value:8s} {transfer:3s} -> {path.name} ({len(icc)} bytes)")


if __name__ == "__main__":
    main()
