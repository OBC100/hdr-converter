"""Stage G 回归：JXR HDR 预览（无校准、BT.2020）与旧 scRGB 透传近似一致。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load_preview_frame():
    """避免经 gui/__init__ 拉起 PyQt6（无 GUI 环境也可跑数值回归）。"""
    path = ROOT / "src/hdr_converter/gui/preview_frame.py"
    spec = importlib.util.spec_from_file_location(
        "hdr_converter.gui.preview_frame", path
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


from hdr_converter.core.canonical import (  # noqa: E402
    SCRGB_REFERENCE_WHITE_NITS,
    to_canonical_bt2020_linear,
)
from hdr_converter.core.cicp import Gamut  # noqa: E402
from hdr_converter.core.decode_cache import load_source_raw  # noqa: E402

pf = _load_preview_frame()
build_hdr_preview_scrgb = pf.build_hdr_preview_scrgb
build_preview_frames = pf.build_preview_frames
scale_preview_rgba = pf.scale_preview_rgba

TOL = 1e-3  # scRGB↔BT.2020 矩阵往返残差约 5e-4（预览可接受；非 Stage A 导出容差）
JXR = Path(r"c:\Users\OBC\source\repos\OBC100\test_output\test_input.jxr")


def main() -> None:
    assert JXR.is_file(), JXR
    scrgb = load_source_raw(JXR)
    small = scale_preview_rgba(scrgb)
    old_hdr = np.clip(small[..., :3], 0.0, None).astype(np.float32)

    canonical = to_canonical_bt2020_linear(small, Gamut.SRGB, SCRGB_REFERENCE_WHITE_NITS)
    new_hdr = build_hdr_preview_scrgb(
        canonical, gamut=Gamut.BT2020
    )
    err = np.abs(new_hdr - old_hdr)
    p999 = float(np.percentile(err, 99.9))
    mx = float(err.max())
    print(f"HDR no-calib BT.2020 vs old passthrough: max={mx:.3e} p99.9={p999:.3e}")
    assert p999 <= TOL, (p999, mx)

    sdr, hdr, meta = build_preview_frames(
        to_canonical_bt2020_linear(scrgb, Gamut.SRGB, SCRGB_REFERENCE_WHITE_NITS),
        gamut=Gamut.BT2020,
        need_sdr=True,
        need_hdr=True,
    )
    assert sdr is not None and hdr is not None
    assert meta.width == scrgb.shape[1] and meta.height == scrgb.shape[0]
    print(f"preview frames OK meta={meta}")
    print("Stage G OK")


if __name__ == "__main__":
    main()
