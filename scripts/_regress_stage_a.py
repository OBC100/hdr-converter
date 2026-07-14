"""Stage A 回归：to_canonical_bt2020_linear vs scrgb_to_gamut_linear。

用法::

    .venv\\Scripts\\python scripts/_regress_stage_a.py [path.jxr ...]

容差沿用 PROJECT.md §8（Horizon 样张 max diff ≈ 1.5e-5）。
默认样张：仓库旁 ``../test_output/test_input.jxr``。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.canonical import (  # noqa: E402
    SCRGB_REFERENCE_WHITE_NITS,
    to_canonical_bt2020_linear,
)
from hdr_converter.core.cicp import Gamut  # noqa: E402
from hdr_converter.core.color_pipeline import scrgb_to_gamut_linear  # noqa: E402
from hdr_converter.core.decoders.jxr_decoder import (  # noqa: E402
    decode_jxr_to_source_image,
    is_jxr_supported,
)

TOLERANCE = 1.5e-5
DEFAULT_SAMPLES = [
    ROOT.parent / "test_output" / "test_input.jxr",
]


def _synthetic_scrgb(h: int = 64, w: int = 64) -> np.ndarray:
    """无可选样张时用的合成 scRGB（含 >1.0 与轻微负值扩展色）。"""
    yy, xx = np.mgrid[0:h, 0:w]
    r = (xx / max(w - 1, 1)).astype(np.float32) * 2.0
    g = (yy / max(h - 1, 1)).astype(np.float32) * 1.5
    b = np.full((h, w), 0.25, dtype=np.float32)
    # 左上角注入少量负值，模拟 scRGB 扩展色域
    r[:8, :8] = -0.05
    a = np.ones((h, w), dtype=np.float32)
    return np.stack([r, g, b, a], axis=-1)


def _compare(name: str, scrgb: np.ndarray) -> float:
    old = scrgb_to_gamut_linear(scrgb, Gamut.BT2020)
    new = to_canonical_bt2020_linear(
        scrgb,
        Gamut.SRGB,
        SCRGB_REFERENCE_WHITE_NITS,
    )
    diff = float(np.max(np.abs(old.astype(np.float64) - new.astype(np.float64))))
    status = "PASS" if diff <= TOLERANCE else "FAIL"
    print(f"  [{status}] {name}: max|Δ|={diff:.3e}  (tol={TOLERANCE:.1e})")
    return diff


def main(argv: list[str]) -> int:
    if not is_jxr_supported():
        print("JPEG XR 不可用（imagecodecs/JPEGXR），跳过真实样张，仅跑合成。")

    paths = [Path(p) for p in argv[1:]] if len(argv) > 1 else []
    if not paths:
        paths = [p for p in DEFAULT_SAMPLES if p.is_file()]

    print("Stage A regression: canonical vs scrgb_to_gamut_linear(BT2020)")
    print(f"tolerance = {TOLERANCE:.1e}\n")

    worst = 0.0
    t0 = time.perf_counter()

    worst = max(worst, _compare("synthetic_scrgb_64x64", _synthetic_scrgb()))

    if not paths and is_jxr_supported():
        print("  (no JXR sample found; synthetic only)")

    for path in paths:
        if not path.is_file():
            print(f"  [SKIP] missing: {path}")
            continue
        src = decode_jxr_to_source_image(path)
        assert src.primaries == Gamut.SRGB
        assert src.reference_white_nits == SCRGB_REFERENCE_WHITE_NITS
        assert src.is_hdr is True
        worst = max(worst, _compare(path.name, src.linear))

    elapsed = time.perf_counter() - t0
    print(f"\nworst max|Δ| = {worst:.3e}  ({elapsed:.2f}s)")
    if worst > TOLERANCE:
        print("FAILED")
        return 1
    print("PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
