"""命名色彩空间登记表 + ICC 识别（Stage D2）。

色彩空间 = 原色 + 白点 + TRC。内建 ``Gamut`` 三选一只覆盖 D65 常见三项；
外部输入（ProPhoto / AdobeRGB / DCI-P3 等）经本模块识别后交给
``to_canonical_bt2020_linear``（Bradford CAT）。
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .cicp import Gamut, TransferCurve

# colour-science 登记名 → 项目内建 Gamut（可走快速矩阵路径）
_BUILTIN_COLOUR_TO_GAMUT: dict[str, Gamut] = {
    "sRGB": Gamut.SRGB,
    "ITU-R BT.709": Gamut.SRGB,
    "Display P3": Gamut.P3,
    "ITU-R BT.2020": Gamut.BT2020,
}

# ICC desc / 常见别名 → colour.RGB_COLOURSPACES 键
_NAME_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"pro\s*photo|romm\s*rgb|prophoto", re.I), "ProPhoto RGB"),
    (re.compile(r"adobe\s*rgb|adobe\s*rgb\s*\(1998\)", re.I), "Adobe RGB (1998)"),
    (re.compile(r"dci[\s\-]?p3(?!\s*d65)|p3[\s\-]?dci", re.I), "DCI-P3"),
    (re.compile(r"display\s*p3|p3\s*d65|dci[\s\-]?p3\s*d65", re.I), "Display P3"),
    (re.compile(r"rec\.?\s*2020|bt\.?\s*2020|itu[\s\-]?r\s*bt\.?\s*2020", re.I), "ITU-R BT.2020"),
    (re.compile(r"\bsrgb\b|iec\s*61966|bt\.?\s*709", re.I), "sRGB"),
    (re.compile(r"adobe\s*wide|wide\s*gamut\s*rgb", re.I), "Adobe Wide Gamut RGB"),
    (re.compile(r"eci\s*rgb", re.I), "eciRGB v2"),
    (re.compile(r"acescg|ap1", re.I), "ACEScg"),
    (re.compile(r"aces2065|ap0|aces\b", re.I), "ACES2065-1"),
]


@dataclass(frozen=True)
class ColourSpaceDescriptor:
    """原色 + 白点 + TRC 三元组（可映射到 colour.RGB_Colourspace）。"""

    name: str
    colour_name: str | None
    """``colour.RGB_COLOURSPACES`` 键；匿名空间为 None。"""

    # 匿名空间：xy 原色与白点（CIE xy）
    primaries_xy: tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None = None
    whitepoint_xy: tuple[float, float] | None = None
    gamma: float | None = None
    """纯 gamma；None 表示使用 colour 空间自带 TRC。"""

    def as_builtin_gamut(self) -> Gamut | None:
        if self.colour_name and self.colour_name in _BUILTIN_COLOUR_TO_GAMUT:
            return _BUILTIN_COLOUR_TO_GAMUT[self.colour_name]
        return None


PrimariesLike = Gamut | ColourSpaceDescriptor


_DISPLAY_NAME_OVERRIDES: dict[str, str] = {
    "sRGB": "sRGB",
    "ITU-R BT.709": "sRGB",
    "Display P3": "Display P3",
    "DCI-P3": "DCI-P3",
    "ITU-R BT.2020": "BT.2020",
    "Adobe RGB (1998)": "Adobe RGB",
    "ProPhoto RGB": "ProPhoto RGB",
    "Adobe Wide Gamut RGB": "Adobe Wide Gamut RGB",
    "eciRGB v2": "eciRGB v2",
    "ACEScg": "ACEScg",
    "ACES2065-1": "ACES2065-1",
}


def describe_primaries(primaries: PrimariesLike) -> str:
    """``PrimariesLike`` → 供 UI 展示的色彩空间名（输入原生色域，非导出目标）。"""
    if isinstance(primaries, Gamut):
        return _DISPLAY_NAME_OVERRIDES.get(
            gamut_to_descriptor(primaries).colour_name or "", primaries.value
        )
    if primaries.colour_name:
        return _DISPLAY_NAME_OVERRIDES.get(primaries.colour_name, primaries.colour_name)
    return primaries.name


def gamut_to_descriptor(gamut: Gamut) -> ColourSpaceDescriptor:
    names = {
        Gamut.SRGB: ("sRGB", "sRGB"),
        Gamut.P3: ("Display P3", "Display P3"),
        Gamut.BT2020: ("BT.2020", "ITU-R BT.2020"),
    }
    label, key = names[gamut]
    return ColourSpaceDescriptor(name=label, colour_name=key)


def match_colourspace_name(text: str) -> str | None:
    """模糊匹配 ICC desc / 名称 → colour 登记名。"""
    s = text.strip()
    if not s:
        return None
    for pat, key in _NAME_ALIASES:
        if pat.search(s):
            return key
    # 精确命中 colour 键
    try:
        import colour

        if s in colour.RGB_COLOURSPACES:
            return s
    except Exception:
        pass
    return None


def descriptor_from_colour_name(key: str) -> ColourSpaceDescriptor:
    return ColourSpaceDescriptor(name=key, colour_name=key)


# ---------------------------------------------------------------------------
# ICC 二进制解析（最小：desc / rXYZ / gXYZ / bXYZ / wtpt）
# ---------------------------------------------------------------------------


def _s15f16(v: int) -> float:
    return v / 65536.0


def _parse_icc_tags(icc: bytes) -> dict[bytes, bytes]:
    if len(icc) < 132 or icc[36:40] != b"acsp":
        raise ValueError("不是有效的 ICC profile")
    count = struct.unpack_from(">I", icc, 128)[0]
    tags: dict[bytes, bytes] = {}
    for i in range(count):
        off = 132 + i * 12
        if off + 12 > len(icc):
            break
        sig = icc[off : off + 4]
        tag_off = struct.unpack_from(">I", icc, off + 4)[0]
        tag_size = struct.unpack_from(">I", icc, off + 8)[0]
        if tag_off + tag_size > len(icc):
            continue
        tags[sig] = icc[tag_off : tag_off + tag_size]
    return tags


def _decode_desc_tag(raw: bytes) -> str:
    if len(raw) < 12:
        return ""
    typ = raw[:4]
    if typ == b"desc":
        # ICC v2 desc: ASCII count at +8
        if len(raw) >= 12:
            n = struct.unpack_from(">I", raw, 8)[0]
            end = 12 + max(0, n - 1)
            return raw[12:end].decode("latin-1", errors="replace").strip("\x00")
    if typ == b"mluc":
        # multilanguage Unicode：取第一个记录
        if len(raw) < 28:
            return ""
        rec_count = struct.unpack_from(">I", raw, 8)[0]
        if rec_count < 1:
            return ""
        # record: lang(2) country(2) len(4) offset(4) — first at 16
        length = struct.unpack_from(">I", raw, 20)[0]
        offset = struct.unpack_from(">I", raw, 24)[0]
        chunk = raw[offset : offset + length]
        try:
            return chunk.decode("utf-16-be", errors="replace").strip("\x00")
        except Exception:
            return ""
    return ""


def _xyz_from_tag(raw: bytes) -> tuple[float, float, float] | None:
    if len(raw) < 20 or raw[:4] != b"XYZ ":
        return None
    x = _s15f16(struct.unpack_from(">i", raw, 8)[0])
    y = _s15f16(struct.unpack_from(">i", raw, 12)[0])
    z = _s15f16(struct.unpack_from(">i", raw, 16)[0])
    return (x, y, z)


def _xy_from_xyz(xyz: tuple[float, float, float]) -> tuple[float, float]:
    x, y, z = xyz
    s = x + y + z
    if s <= 1e-12:
        return (0.3127, 0.3290)
    return (x / s, y / s)


def _approx_match_named_by_xy(
    r_xy: tuple[float, float],
    g_xy: tuple[float, float],
    b_xy: tuple[float, float],
    wp_xy: tuple[float, float],
    *,
    tol: float = 0.008,
) -> str | None:
    """用原色/白点近似匹配已知 colour 空间。"""
    import colour

    candidates = [
        "sRGB",
        "Display P3",
        "DCI-P3",
        "ITU-R BT.2020",
        "Adobe RGB (1998)",
        "ProPhoto RGB",
        "ACEScg",
        "ACES2065-1",
    ]
    best: str | None = None
    best_err = tol
    for key in candidates:
        if key not in colour.RGB_COLOURSPACES:
            continue
        cs = colour.RGB_COLOURSPACES[key]
        prim = np.asarray(cs.primaries, dtype=np.float64)
        wp = np.asarray(cs.whitepoint, dtype=np.float64)
        err = (
            float(np.linalg.norm(prim[0] - r_xy))
            + float(np.linalg.norm(prim[1] - g_xy))
            + float(np.linalg.norm(prim[2] - b_xy))
            + float(np.linalg.norm(wp - wp_xy))
        )
        if err < best_err:
            best_err = err
            best = key
    return best


def parse_icc_to_descriptor(icc: bytes) -> ColourSpaceDescriptor | None:
    """从 ICC 字节识别色彩空间；失败返回 None。"""
    try:
        tags = _parse_icc_tags(icc)
    except ValueError:
        return None

    desc_text = ""
    if b"desc" in tags:
        desc_text = _decode_desc_tag(tags[b"desc"])
    matched = match_colourspace_name(desc_text) if desc_text else None
    if matched:
        return descriptor_from_colour_name(matched)

    r = _xyz_from_tag(tags[b"rXYZ"]) if b"rXYZ" in tags else None
    g = _xyz_from_tag(tags[b"gXYZ"]) if b"gXYZ" in tags else None
    b = _xyz_from_tag(tags[b"bXYZ"]) if b"bXYZ" in tags else None
    w = _xyz_from_tag(tags[b"wtpt"]) if b"wtpt" in tags else None
    if not (r and g and b and w):
        return None

    r_xy, g_xy, b_xy, wp_xy = _xy_from_xyz(r), _xy_from_xyz(g), _xy_from_xyz(b), _xy_from_xyz(w)
    named = _approx_match_named_by_xy(r_xy, g_xy, b_xy, wp_xy)
    if named:
        return descriptor_from_colour_name(named)

    # 匿名空间
    gamma = None
    for trc_sig in (b"rTRC", b"gTRC", b"bTRC"):
        if trc_sig not in tags:
            continue
        raw = tags[trc_sig]
        if len(raw) >= 12 and raw[:4] == b"curv":
            count = struct.unpack_from(">I", raw, 8)[0]
            if count == 1 and len(raw) >= 14:
                # gamma = u8Fixed8
                gamma = struct.unpack_from(">H", raw, 12)[0] / 256.0
            break
        if len(raw) >= 12 and raw[:4] == b"para":
            # type 3 / function type — 粗略：若像 sRGB 则留给 colour；否则记 2.2
            ftype = struct.unpack_from(">H", raw, 8)[0]
            if ftype == 3:
                gamma = 2.2
            break

    label = desc_text or "Anonymous RGB"
    return ColourSpaceDescriptor(
        name=label,
        colour_name=None,
        primaries_xy=(r_xy, g_xy, b_xy),
        whitepoint_xy=wp_xy,
        gamma=gamma or 2.2,
    )


@lru_cache(maxsize=32)
def _colourspace_object(desc_key: str):
    import colour

    return colour.RGB_COLOURSPACES[desc_key]


def resolve_colour_rgb_colourspace(primaries: PrimariesLike):
    """``Gamut | ColourSpaceDescriptor`` → ``colour.RGB_Colourspace``。"""
    import colour

    if isinstance(primaries, Gamut):
        key = gamut_to_descriptor(primaries).colour_name or "sRGB"
        return _colourspace_object(key)

    if primaries.colour_name:
        return _colourspace_object(primaries.colour_name)

    if primaries.primaries_xy is None or primaries.whitepoint_xy is None:
        return colour.RGB_COLOURSPACES["sRGB"]

    prim = np.asarray(primaries.primaries_xy, dtype=np.float64)
    wp = np.asarray(primaries.whitepoint_xy, dtype=np.float64)
    gamma = float(primaries.gamma or 2.2)

    def _enc(x, g=gamma):
        return colour.models.exponent_function_basic(x, g, "encoding")

    def _dec(x, g=gamma):
        return colour.models.exponent_function_basic(x, g, "decoding")

    return colour.RGB_Colourspace(
        primaries.name,
        prim,
        wp,
        None,
        use_derived_matrix_RGB_to_XYZ=True,
        use_derived_matrix_XYZ_to_RGB=True,
        cctf_encoding=_enc,
        cctf_decoding=_dec,
    )


def cicp_to_primaries_like(
    color_primaries: int,
    transfer_characteristics: int,
    matrix_coefficients: int | None = None,
) -> tuple[PrimariesLike, TransferCurve | None]:
    """CICP → (primaries, curve)。

    ``curve`` 为 None 时表示应用 colourspace 自带 TRC（如 DCI-P3 γ2.6）。
    """
    from .cicp import cicp_to_gamut_curve

    # H.273：cp=11 → DCI-P3（白点≠D65，不可映射为项目内建 Display P3）
    if color_primaries == 11:
        desc = descriptor_from_colour_name("DCI-P3")
        if transfer_characteristics in (16,):
            return desc, TransferCurve.PQ
        if transfer_characteristics in (18,):
            return desc, TransferCurve.HLG
        if transfer_characteristics in (8,):
            return desc, TransferCurve.LINEAR
        if transfer_characteristics in (13,):
            return desc, TransferCurve.SRGB
        return desc, None

    try:
        gamut, curve = cicp_to_gamut_curve(
            color_primaries, transfer_characteristics, matrix_coefficients
        )
        return gamut, curve
    except ValueError:
        # 未知 CICP：退回 sRGB 假设
        return Gamut.SRGB, TransferCurve.SRGB
