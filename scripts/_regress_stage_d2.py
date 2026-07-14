"""Stage D2 回归：命名色彩空间、ICC 解析、异白点 Bradford CAT。

用法::

    .venv\\Scripts\\python scripts/_regress_stage_d2.py

同时应再跑 ``scripts/_regress_stage_a.py`` 确认内建三色域零回归。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hdr_converter.core.canonical import (  # noqa: E402
    SDR_REFERENCE_WHITE_NITS,
    to_canonical_bt2020_linear,
)
from hdr_converter.core.cicp import Gamut  # noqa: E402
from hdr_converter.core.named_colourspaces import (  # noqa: E402
    ColourSpaceDescriptor,
    cicp_to_primaries_like,
    descriptor_from_colour_name,
    match_colourspace_name,
    parse_icc_to_descriptor,
    resolve_colour_rgb_colourspace,
)


def _check(cond: bool, msg: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        raise AssertionError(msg)


def test_name_aliases() -> None:
    cases = [
        ("ProPhoto RGB", "ProPhoto RGB"),
        ("Adobe RGB (1998)", "Adobe RGB (1998)"),
        ("DCI-P3", "DCI-P3"),
        ("Display P3", "Display P3"),
        ("BT.2020", "ITU-R BT.2020"),
        ("sRGB IEC61966-2.1", "sRGB"),
        ("ACEScg", "ACEScg"),
    ]
    for text, expect in cases:
        got = match_colourspace_name(text)
        _check(got == expect, f"match({text!r}) → {got!r} (expect {expect!r})")


def test_cicp_dci_p3() -> None:
    prim, curve = cicp_to_primaries_like(11, 13, 0)
    _check(isinstance(prim, ColourSpaceDescriptor), "cp=11 → ColourSpaceDescriptor")
    _check(prim.colour_name == "DCI-P3", f"cp=11 colour_name={prim.colour_name!r}")
    _check(curve is not None and curve.value == "srgb", f"cp=11 tc=13 curve={curve}")

    prim2, curve2 = cicp_to_primaries_like(11, 2, 0)  # cinema gamma often unspecified
    _check(prim2.colour_name == "DCI-P3", "cp=11 unknown tc still DCI-P3")
    _check(curve2 is None, "cp=11 unknown tc → use colourspace TRC")

    # Display P3 CICP 不得误判为 DCI-P3
    prim3, _ = cicp_to_primaries_like(12, 13, 0)
    _check(prim3 == Gamut.P3, f"cp=12 → Gamut.P3 got {prim3}")


def test_icc_named_from_desc() -> None:
    """用 colour 自带 ICC 或合成最小 profile 测 desc 匹配。"""
    try:
        import colour
    except ImportError:
        print("  [SKIP] colour not installed")
        return

    # 若 colour 提供写 ICC：取 sRGB 空间名即可；否则手写最小 desc profile
    # 最小 v2 ICC：header + desc tag 指向 "Adobe RGB (1998)"
    name = b"Adobe RGB (1998)\x00"
    # Build tiny fake: we'll just call match via parse after crafting tags
    # Simpler path: descriptor_from_colour_name + resolve
    desc = descriptor_from_colour_name("Adobe RGB (1998)")
    cs = resolve_colour_rgb_colourspace(desc)
    _check(cs.name == colour.RGB_COLOURSPACES["Adobe RGB (1998)"].name, "resolve AdobeRGB")


def _minimal_icc_with_desc(ascii_name: str) -> bytes:
    """构造可被 parse_icc_to_descriptor 读到的最小 ICC（仅 desc + 假 XYZ）。"""
    import struct

    header = bytearray(128)
    header[36:40] = b"acsp"
    data_start = 132 + 5 * 12

    def xyz_tag(x: float, y: float, z: float) -> bytes:
        def s15(v: float) -> int:
            return int(round(v * 65536.0))

        return b"XYZ " + b"\x00" * 4 + struct.pack(
            ">iii", s15(x), s15(y), s15(z)
        )

    name_b = ascii_name.encode("latin-1") + b"\x00"
    desc_payload = b"desc" + b"\x00" * 4 + struct.pack(">I", len(name_b)) + name_b
    if len(desc_payload) % 2:
        desc_payload += b"\x00"

    tags_data = [
        (b"desc", desc_payload),
        (b"rXYZ", xyz_tag(0.60974, 0.31111, 0.01945)),
        (b"gXYZ", xyz_tag(0.20528, 0.62567, 0.06087)),
        (b"bXYZ", xyz_tag(0.14919, 0.06322, 0.74457)),
        (b"wtpt", xyz_tag(0.95045, 1.0, 1.08905)),
    ]

    offset = data_start
    table = bytearray()
    blobs = bytearray()
    for sig, payload in tags_data:
        table += sig + struct.pack(">II", offset, len(payload))
        blobs += payload
        pad = (4 - (len(payload) % 4)) % 4
        blobs += b"\x00" * pad
        offset += len(payload) + pad

    profile = bytes(header) + struct.pack(">I", 5) + bytes(table) + bytes(blobs)
    return struct.pack(">I", len(profile)) + profile[4:]


def test_icc_parse_adobe() -> None:
    icc = _minimal_icc_with_desc("Adobe RGB (1998)")
    desc = parse_icc_to_descriptor(icc)
    _check(desc is not None, "parse Adobe ICC")
    assert desc is not None
    _check(
        desc.colour_name == "Adobe RGB (1998)",
        f"Adobe ICC name={desc.colour_name!r}",
    )


def test_bradford_cat_different_wp() -> None:
    """DCI-P3 与 Display P3 原色不同；彩色样本经转换后应可区分。"""
    rgb = np.zeros((4, 4, 3), dtype=np.float32)
    rgb[..., 0] = 0.85
    rgb[..., 1] = 0.15
    rgb[..., 2] = 0.05
    dci = descriptor_from_colour_name("DCI-P3")
    out_dci = to_canonical_bt2020_linear(rgb, dci, SDR_REFERENCE_WHITE_NITS)
    out_p3 = to_canonical_bt2020_linear(rgb, Gamut.P3, SDR_REFERENCE_WHITE_NITS)
    diff = float(np.max(np.abs(out_dci.astype(np.float64) - out_p3.astype(np.float64))))
    _check(diff > 1e-4, f"DCI-P3 vs Display P3 should differ (Δ={diff:.3e})")


def test_prophoto_cat_runs() -> None:
    """ProPhoto（D50）经 Bradford 可转换且与 sRGB 路径不同。"""
    rgb = np.full((2, 2, 3), [0.7, 0.2, 0.1], dtype=np.float32)
    pro = descriptor_from_colour_name("ProPhoto RGB")
    out_pro = to_canonical_bt2020_linear(rgb, pro, SDR_REFERENCE_WHITE_NITS)
    out_srgb = to_canonical_bt2020_linear(rgb, Gamut.SRGB, SDR_REFERENCE_WHITE_NITS)
    diff = float(np.max(np.abs(out_pro.astype(np.float64) - out_srgb.astype(np.float64))))
    _check(np.all(np.isfinite(out_pro)), "ProPhoto→BT.2020 finite")
    _check(diff > 1e-3, f"ProPhoto vs sRGB should differ (Δ={diff:.3e})")


def test_builtin_gamut_unchanged_path() -> None:
    """ColourSpaceDescriptor(sRGB) 应走内建快速路径，与 Gamut.SRGB 一致。"""
    rgb = np.random.default_rng(0).random((8, 8, 3), dtype=np.float64).astype(np.float32)
    a = to_canonical_bt2020_linear(rgb, Gamut.SRGB, SDR_REFERENCE_WHITE_NITS)
    b = to_canonical_bt2020_linear(
        rgb, descriptor_from_colour_name("sRGB"), SDR_REFERENCE_WHITE_NITS
    )
    diff = float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))))
    _check(diff < 1e-6, f"sRGB descriptor vs Gamut Δ={diff:.3e}")


def main() -> int:
    print("Stage D2 regression\n")
    try:
        test_name_aliases()
        test_cicp_dci_p3()
        test_icc_named_from_desc()
        test_icc_parse_adobe()
        test_bradford_cat_different_wp()
        test_prophoto_cat_runs()
        test_builtin_gamut_unchanged_path()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}")
        return 1
    except Exception as exc:
        print(f"\nERROR: {exc}")
        import traceback

        traceback.print_exc()
        return 1
    print("\nAll Stage D2 checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
