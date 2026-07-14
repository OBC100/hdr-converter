#!/usr/bin/env python3
"""刷新全部 ICC 资产并冒烟验证两类生成器。

1. 调用 ``generate_pq_icc_assets`` 逻辑写 9 个 HDR ``*.icc``
2. 运行时生成三色域 Apple baseline，检查长度与 desc
3. 打印 ``icc_policy`` 默认决策表
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.assets.apple_baseline_icc import (  # noqa: E402
    create_apple_baseline_icc_profile,
)
from hdr_converter.core.assets.libjxl_pq_icc import (  # noqa: E402
    create_hlg_icc_for_gamut,
    create_linear_icc_for_gamut,
    create_pq_icc_for_gamut,
)
from hdr_converter.core.cicp import Gamut, TransferCurve  # noqa: E402
from hdr_converter.core.encoders.base import OutputFormat  # noqa: E402
from hdr_converter.core.hdr_options import HdrDeliveryMode  # noqa: E402
from hdr_converter.core.icc_policy import plan_icc_embed  # noqa: E402

_ASSET_DIR = ROOT / "src" / "hdr_converter" / "core" / "assets"

_HDR_FILES: list[tuple[Gamut, str, str]] = [
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


def _write_hdr_assets() -> None:
    _ASSET_DIR.mkdir(parents=True, exist_ok=True)
    for gamut, filename, transfer in _HDR_FILES:
        if transfer == "pq":
            icc = create_pq_icc_for_gamut(gamut)
        elif transfer == "hlg":
            icc = create_hlg_icc_for_gamut(gamut)
        else:
            icc = create_linear_icc_for_gamut(gamut)
        path = _ASSET_DIR / filename
        path.write_bytes(icc)
        print(f"  HDR  {gamut.value:8s} {transfer:6s} -> {path.name} ({len(icc)} bytes)")


def _smoke_baseline() -> None:
    for gamut in (Gamut.SRGB, Gamut.P3, Gamut.BT2020):
        icc = create_apple_baseline_icc_profile(gamut)
        assert len(icc) >= 500, (gamut, len(icc))
        assert icc[36:40] == b"acsp", "ICC signature"
        print(f"  BASE {gamut.value:8s} -> {len(icc)} bytes OK")


def _print_policy() -> None:
    print("\nDefault embed plans:")
    rows = [
        (OutputFormat.PNG, TransferCurve.PQ, HdrDeliveryMode.DIRECT),
        (OutputFormat.PNG, TransferCurve.SRGB, HdrDeliveryMode.DIRECT),
        (OutputFormat.JPG, TransferCurve.SRGB, HdrDeliveryMode.DIRECT),
        (OutputFormat.JPG, TransferCurve.PQ, HdrDeliveryMode.GAINMAP_MONO),
        (OutputFormat.JXL, TransferCurve.PQ, HdrDeliveryMode.DIRECT),
        (OutputFormat.AVIF, TransferCurve.PQ, HdrDeliveryMode.DIRECT),
        (OutputFormat.HEIF, TransferCurve.PQ, HdrDeliveryMode.DIRECT),
    ]
    for fmt, curve, delivery in rows:
        plan = plan_icc_embed(fmt, Gamut.BT2020, curve, delivery)
        print(
            f"  {fmt.value:4s} {curve.value:6s} {delivery.value:14s} "
            f"embed={plan.embed!s:5} kind={plan.kind.value:8s} "
            f"safe={plan.windows_photos_safe}"
        )


def main() -> None:
    print("=== Write HDR ICC assets ===")
    _write_hdr_assets()
    print("=== Smoke baseline generators ===")
    _smoke_baseline()
    _print_policy()
    print("OK — see docs/ICC_PROFILES.md")


if __name__ == "__main__":
    main()
