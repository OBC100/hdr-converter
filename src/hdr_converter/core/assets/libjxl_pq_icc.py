"""
libjxl MaybeCreateProfile 的 HDR ICC 生成器（PQ / HLG，含 mft1 3D tone-map LUT）。

算法来源：lib/jxl/cms/jxl_cms_internal.h（ToneMapPixel、CreateICCLutAtoBTagForHDR、
MaybeCreateProfileImpl），tone_mapping.h，transfer_functions.h。
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Literal

import numpy as np

from ..cicp import Gamut

Matrix3 = np.ndarray  # shape (3, 3) float64

TransferKind = Literal["pq", "hlg", "linear"]

K_BRADFORD = np.array(
    [[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]],
    dtype=np.float64,
)
K_BRADFORD_INV = np.array(
    [[0.9869929, -0.1470543, 0.1599627], [0.4323053, 0.5183603, 0.0492912], [-0.0085287, 0.0400428, 0.9684867]],
    dtype=np.float64,
)
W50 = np.array([0.96422, 1.0, 0.82521], dtype=np.float64)

PQ_M1 = 2610.0 / 16384
PQ_M2 = (2523.0 / 4096) * 128
PQ_C1 = 3424.0 / 4096
PQ_C2 = (2413.0 / 4096) * 32
PQ_C3 = (2392.0 / 4096) * 32

K_XN, K_YN, K_ZN = 0.964212, 1.0, 0.825188
K_LAB_DELTA = 6.0 / 29.0

PrimariesKind = Literal["srgb", "p3", "bt2020"]

_PRIMARIES: dict[PrimariesKind, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = {
    "srgb": ((0.639998686, 0.330010138), (0.300003784, 0.600003357), (0.150002046, 0.059997204)),
    "p3": ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)),
    "bt2020": ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)),
}
_D65 = (0.3127, 0.3290)

_CICP_PRIMARIES: dict[PrimariesKind, int] = {"srgb": 1, "bt2020": 9, "p3": 12}
_CICP_TRANSFER: dict[TransferKind, int] = {"pq": 16, "hlg": 18, "linear": 8}
_DESC_PQ: dict[PrimariesKind, str] = {
    "srgb": "RGB_D65_SRG_Rel_PeQ",
    "p3": "RGB_D65_DCI_Rel_PeQ",
    "bt2020": "Rec2100PQ",
}
_DESC_HLG: dict[PrimariesKind, str] = {
    "srgb": "RGB_D65_SRG_Rel_HLG",
    "p3": "RGB_D65_DCI_Rel_HLG",
    "bt2020": "Rec2100HLG",
}
_DESC_LINEAR: dict[PrimariesKind, str] = {
    "srgb": "RGB_D65_SRG_Rel_Lin",
    "p3": "RGB_D65_DCI_Rel_Lin",
    "bt2020": "Rec2100Linear",
}
_ICCP_NAME_PQ: dict[Gamut, str] = {
    Gamut.SRGB: "sRGB PQ",
    Gamut.P3: "DisplayP3 PQ",
    Gamut.BT2020: "Rec2100PQ",
}
_ICCP_NAME_HLG: dict[Gamut, str] = {
    Gamut.SRGB: "sRGB HLG",
    Gamut.P3: "DisplayP3 HLG",
    Gamut.BT2020: "Rec2100HLG",
}
_ICCP_NAME_LINEAR: dict[Gamut, str] = {
    Gamut.SRGB: "sRGB Linear",
    Gamut.P3: "DisplayP3 Linear",
    Gamut.BT2020: "Rec2100Linear",
}
_ICCP_NAMES: dict[TransferKind, dict[Gamut, str]] = {
    "pq": _ICCP_NAME_PQ,
    "hlg": _ICCP_NAME_HLG,
    "linear": _ICCP_NAME_LINEAR,
}

HLG_A = 0.17883277
HLG_RA = 1.0 / HLG_A
HLG_B = 1.0 - 4.0 * HLG_A
HLG_C = 0.5599107295
HLG_INV12 = 1.0 / 12.0


@dataclass(frozen=True)
class HdrColorEncoding:
    primaries: PrimariesKind
    transfer: TransferKind = "pq"
    rendering_intent: int = 1  # Relative


def gamut_to_primaries(gamut: Gamut) -> PrimariesKind:
    return {"srgb": "srgb", "p3": "p3", "bt2020": "bt2020"}[gamut.value]


def iccp_profile_name(gamut: Gamut, transfer: TransferKind = "pq") -> str:
    return _ICCP_NAMES[transfer][gamut]


def _clamp1(val: float, lo: float, hi: float) -> float:
    return lo if val < lo else hi if val > hi else val


def _mul3x3_vector(m: Matrix3, v: np.ndarray) -> np.ndarray:
    return m @ v


def _inv3x3(m: Matrix3) -> Matrix3:
    return np.linalg.inv(m)


def _adapt_to_xyz_d50(wx: float, wy: float) -> Matrix3:
    w = np.array([wx / wy, 1.0, (1.0 - wx - wy) / wy], dtype=np.float64)
    lms = K_BRADFORD @ w
    lms50 = K_BRADFORD @ W50
    a = np.diag(lms50 / lms)
    return K_BRADFORD_INV @ a @ K_BRADFORD


def _primaries_to_xyz(
    rx: float, ry: float, gx: float, gy: float, bx: float, by: float, wx: float, wy: float
) -> Matrix3:
    primaries = np.array(
        [[rx, gx, bx], [ry, gy, by], [1.0 - rx - ry, 1.0 - gx - gy, 1.0 - bx - by]],
        dtype=np.float64,
    )
    primaries_inv = _inv3x3(primaries)
    w = np.array([wx / wy, 1.0, (1.0 - wx - wy) / wy], dtype=np.float64)
    xyz = primaries_inv @ w
    return primaries @ np.diag(xyz)


def _primaries_to_xyz_d50(
    rx: float, ry: float, gx: float, gy: float, bx: float, by: float, wx: float, wy: float
) -> Matrix3:
    return _adapt_to_xyz_d50(wx, wy) @ _primaries_to_xyz(rx, ry, gx, gy, bx, by, wx, wy)


def _pq_display_from_encoded(display_intensity_target: float, e: float) -> float:
    if e == 0.0:
        return 0.0
    sign = -1.0 if e < 0 else 1.0
    e = abs(e)
    xp = e ** (1.0 / PQ_M2)
    num = max(xp - PQ_C1, 0.0)
    den = PQ_C2 - PQ_C3 * xp
    d = (num / den) ** (1.0 / PQ_M1)
    return sign * d * (10000.0 / display_intensity_target)


def _pq_encoded_from_display(display_intensity_target: float, d: float) -> float:
    if d == 0.0:
        return 0.0
    sign = -1.0 if d < 0 else 1.0
    d = abs(d)
    xp = (d * (display_intensity_target / 10000.0)) ** PQ_M1
    num = PQ_C1 + xp * PQ_C2
    den = 1.0 + xp * PQ_C3
    e = (num / den) ** PQ_M2
    return sign * e


def _hlg_display_from_encoded(e: float) -> float:
    """TF_HLG_Base::DisplayFromEncoded — libjxl OOTF 在 ICC 路径为恒等。"""
    if e == 0.0:
        return 0.0
    sign = -1.0 if e < 0 else 1.0
    e = abs(e)
    if e <= 0.5:
        return sign * e * e * (1.0 / 3.0)
    s = (math.exp((e - HLG_C) * HLG_RA) + HLG_B) * HLG_INV12
    return sign * s


class _HlgOOTF:
    """HlgOOTF_Base(source=300, target=80) — libjxl ICC tone-map 分支。"""

    def __init__(self, luminances: np.ndarray, source_luminance: float = 300.0, target_luminance: float = 80.0):
        gamma = 1.111 ** math.log2(target_luminance / source_luminance)
        self.exponent = gamma - 1.0
        self.apply_ootf = abs(self.exponent) > 0.01
        self.red_y, self.green_y, self.blue_y = luminances

    def apply(self, rgb: np.ndarray) -> None:
        if not self.apply_ootf:
            return
        luminance = self.red_y * rgb[0] + self.green_y * rgb[1] + self.blue_y * rgb[2]
        lum = max(float(luminance), 1e-10)
        ratio = min(lum**self.exponent, 1e9)
        rgb *= ratio


class _Rec2408ToneMapper:
    def __init__(self, source_range: tuple[float, float], target_range: tuple[float, float], luminances: np.ndarray):
        self.source_hi = source_range[1]
        self.target_hi = target_range[1]
        self.red_y, self.green_y, self.blue_y = luminances
        self.pq_mastering_min = _pq_encoded_from_display(1.0, source_range[0])
        self.pq_mastering_max = _pq_encoded_from_display(1.0, source_range[1])
        self.pq_mastering_range = self.pq_mastering_max - self.pq_mastering_min
        self.inv_pq_mastering_range = 1.0 / self.pq_mastering_range
        self.min_lum = ( _pq_encoded_from_display(1.0, target_range[0]) - self.pq_mastering_min) * self.inv_pq_mastering_range
        self.max_lum = (_pq_encoded_from_display(1.0, target_range[1]) - self.pq_mastering_min) * self.inv_pq_mastering_range
        self.ks = 1.5 * self.max_lum - 0.5
        self.inv_one_minus_ks = 1.0 / max(1e-6, 1.0 - self.ks)
        self.normalizer = source_range[1] / target_range[1]
        self.inv_target_peak = 1.0 / target_range[1]

    def _t(self, b: float) -> float:
        return (b - self.ks) * self.inv_one_minus_ks

    def _p(self, b: float) -> float:
        t_b = self._t(b)
        return (
            (2 * t_b**3 - 3 * t_b**2 + 1) * self.ks
            + (t_b**3 - 2 * t_b**2 + t_b) * (1 - self.ks)
            + (-2 * t_b**3 + 3 * t_b**2) * self.max_lum
        )

    def tone_map(self, rgb: np.ndarray) -> None:
        luminance = self.source_hi * (self.red_y * rgb[0] + self.green_y * rgb[1] + self.blue_y * rgb[2])
        normalized_pq = min(
            1.0,
            (_pq_encoded_from_display(1.0, luminance) - self.pq_mastering_min) * self.inv_pq_mastering_range,
        )
        e2 = normalized_pq if normalized_pq < self.ks else self._p(normalized_pq)
        one_minus_e2_4 = (1 - e2) ** 4
        e3 = self.min_lum * one_minus_e2_4 + e2
        e4 = e3 * self.pq_mastering_range + self.pq_mastering_min
        d4 = _pq_display_from_encoded(1.0, e4)
        new_luminance = _clamp1(d4, 0.0, self.target_hi)
        min_luminance = 1e-6
        use_cap = luminance <= min_luminance
        ratio = new_luminance / max(luminance, min_luminance)
        cap = new_luminance * self.inv_target_peak
        multiplier = ratio * self.normalizer
        for i in range(3):
            rgb[i] = cap if use_cap else rgb[i] * multiplier


def _gamut_map_scalar(rgb: np.ndarray, primaries_luminances: np.ndarray, preserve_saturation: float = 0.3) -> None:
    luminance = float(primaries_luminances @ rgb)
    gray_mix_saturation = 0.0
    gray_mix_luminance = 0.0
    for val in rgb:
        val_minus_gray = val - luminance
        inv = 1.0 / (val_minus_gray if val_minus_gray != 0.0 else 1.0)
        val_over = val * inv
        if val_minus_gray < 0.0:
            gray_mix_saturation = max(gray_mix_saturation, val_over)
        gray_mix_luminance = max(
            gray_mix_luminance,
            gray_mix_saturation if val_minus_gray <= 0.0 else (val_over - inv),
        )
    gray_mix = _clamp1(preserve_saturation * (gray_mix_saturation - gray_mix_luminance) + gray_mix_luminance, 0.0, 1.0)
    for i in range(3):
        rgb[i] = gray_mix * (luminance - rgb[i]) + rgb[i]
    max_clr = max(1.0, float(rgb.max()))
    rgb /= max_clr


def _lab_f(x: float) -> float:
    d3 = K_LAB_DELTA**3
    return x * (1 / (3 * K_LAB_DELTA**2)) + 4.0 / 29 if x <= d3 else x ** (1.0 / 3.0)


def _tone_map_pixel(encoding: HdrColorEncoding, rgb_in: tuple[float, float, float]) -> tuple[int, int, int]:
    (rx, ry), (gx, gy), (bx, by) = _PRIMARIES[encoding.primaries]
    wx, wy = _D65
    primaries_xyz = _primaries_to_xyz(rx, ry, gx, gy, bx, by, wx, wy)
    luminances = primaries_xyz[1].copy()
    if encoding.transfer == "pq":
        linear = np.array([_pq_display_from_encoded(10000.0, c) for c in rgb_in], dtype=np.float64)
        tone_mapper = _Rec2408ToneMapper((0.0, 10000.0), (0.0, 250.0), luminances)
        tone_mapper.tone_map(linear)
    elif encoding.transfer == "hlg":
        linear = np.array([_hlg_display_from_encoded(c) for c in rgb_in], dtype=np.float64)
        _HlgOOTF(luminances).apply(linear)
    else:
        linear = np.array(rgb_in, dtype=np.float64) * 10000.0
        tone_mapper = _Rec2408ToneMapper((0.0, 10000.0), (0.0, 250.0), luminances)
        tone_mapper.tone_map(linear)
    _gamut_map_scalar(linear, luminances, 0.3)
    chad = _adapt_to_xyz_d50(wx, wy)
    to_xyzd50 = chad @ primaries_xyz
    xyz = to_xyzd50 @ linear
    f_x, f_y, f_z = _lab_f(xyz[0] / K_XN), _lab_f(xyz[1] / K_YN), _lab_f(xyz[2] / K_ZN)
    l_val = round(255.0 * _clamp1(1.16 * f_y - 0.16, 0.0, 1.0))
    a_val = round(128.0 + _clamp1(500.0 * (f_x - f_y), -128.0, 127.0))
    b_val = round(128.0 + _clamp1(200.0 * (f_y - f_z), -128.0, 127.0))
    return int(l_val), int(a_val), int(b_val)


# --- ICC binary writers (big-endian) ---

def _write_u32(buf: bytearray, pos: int, value: int) -> None:
    struct.pack_into(">I", buf, pos, value & 0xFFFFFFFF)


def _write_u16(buf: bytearray, pos: int, value: int) -> None:
    struct.pack_into(">H", buf, pos, value & 0xFFFF)


def _write_u8(buf: bytearray, pos: int, value: int) -> None:
    buf[pos] = value & 0xFF


def _write_tag(buf: bytearray, pos: int, tag: bytes) -> None:
    buf[pos : pos + 4] = tag[:4]


def _write_s15_fixed16(buf: bytearray, pos: int, value: float) -> None:
    i = int(round(value * 65536.0))
    struct.pack_into(">i", buf, pos, i)


def _append_u32(data: bytearray, value: int) -> None:
    data.extend(struct.pack(">I", value & 0xFFFFFFFF))


def _append_u16(data: bytearray, value: int) -> None:
    data.extend(struct.pack(">H", value & 0xFFFF))


def _append_u8(data: bytearray, value: int) -> None:
    data.append(value & 0xFF)


def _append_tag(data: bytearray, tag: str) -> None:
    data.extend(tag.encode("ascii")[:4].ljust(4, b"\x00")[:4])


def _append_s15_fixed16(data: bytearray, value: float) -> None:
    data.extend(struct.pack(">i", int(round(value * 65536.0))))


def _create_mluc_tag(text: str) -> bytes:
    data = bytearray()
    _append_tag(data, "mluc")
    _append_u32(data, 0)
    _append_u32(data, 1)
    _append_u32(data, 12)
    _append_tag(data, "enUS")
    _append_u32(data, len(text) * 2)
    _append_u32(data, 28)
    for c in text:
        data.extend(b"\x00" + bytes([ord(c)]))
    return bytes(data)


def _create_xyz_tag(x: float, y: float, z: float) -> bytes:
    data = bytearray()
    _append_tag(data, "XYZ ")
    _append_u32(data, 0)
    _append_s15_fixed16(data, x)
    _append_s15_fixed16(data, y)
    _append_s15_fixed16(data, z)
    return bytes(data)


def _create_chad_tag(chad: Matrix3) -> bytes:
    data = bytearray()
    _append_tag(data, "sf32")
    _append_u32(data, 0)
    for j in range(3):
        for i in range(3):
            _append_s15_fixed16(data, float(chad[j, i]))
    return bytes(data)


def _create_cicp_tag(primaries: int, tf: int = 16) -> bytes:
    data = bytearray()
    _append_tag(data, "cicp")
    _append_u32(data, 0)
    _append_u8(data, primaries)
    _append_u8(data, tf)
    _append_u8(data, 0)  # matrix
    _append_u8(data, 1)  # full range
    return bytes(data)


def _create_lut_atob_hdr(encoding: HdrColorEncoding) -> bytes:
    dim = 9
    data = bytearray()
    _append_tag(data, "mft1")
    _append_u32(data, 0)
    _append_u8(data, 3)
    _append_u8(data, 3)
    _append_u8(data, dim)
    _append_u8(data, 0)
    for i in range(3):
        for j in range(3):
            _append_s15_fixed16(data, 1.0 if i == j else 0.0)
    for _c in range(3):
        for i in range(256):
            _append_u8(data, i)
    for ix in range(dim):
        for iy in range(dim):
            for ib in range(dim):
                f = (ix / (dim - 1), iy / (dim - 1), ib / (dim - 1))
                lab = _tone_map_pixel(encoding, f)
                for val in lab:
                    _append_u8(data, val)
    for _c in range(3):
        for i in range(256):
            _append_u8(data, i)
    return bytes(data)


def _create_noop_btoa_tag() -> bytes:
    data = bytearray()
    _append_tag(data, "mBA ")
    _append_u32(data, 0)
    _append_u8(data, 3)
    _append_u8(data, 3)
    _append_u16(data, 0)
    _append_u32(data, 32)
    _append_u32(data, 0)
    _append_u32(data, 0)
    _append_u32(data, 0)
    _append_u32(data, 0)
    for _ in range(3):
        _append_tag(data, "para")
        _append_u32(data, 0)
        _append_u16(data, 0)
        _append_u16(data, 0)
        _append_s15_fixed16(data, 1.0)
    return bytes(data)


def _icc_compute_md5(data: bytes) -> bytes:
    """libjxl ICCComputeMD5（非标准 RFC1321 padding）。"""
    sineparts = [
        0xD76AA478, 0xE8C7B756, 0x242070DB, 0xC1BDCEEE, 0xF57C0FAF, 0x4787C62A, 0xA8304613, 0xFD469501,
        0x698098D8, 0x8B44F7AF, 0xFFFF5BB1, 0x895CD7BE, 0x6B901122, 0xFD987193, 0xA679438E, 0x49B40821,
        0xF61E2562, 0xC040B340, 0x265E5A51, 0xE9B6C7AA, 0xD62F105D, 0x02441453, 0xD8A1E681, 0xE7D3FBC8,
        0x21E1CDE6, 0xC33707D6, 0xF4D50D87, 0x455A14ED, 0xA9E3E905, 0xFCEFA3F8, 0x676F02D9, 0x8D2A4C8A,
        0xFFFA3942, 0x8771F681, 0x6D9D6122, 0xFDE5380C, 0xA4BEEA44, 0x4BDECFA9, 0xF6BB4B60, 0xBEBFBC70,
        0x289B7EC6, 0xEAA127FA, 0xD4EF3085, 0x04881D05, 0xD9D4D039, 0xE6DB99E5, 0x1FA27CF8, 0xC4AC5665,
        0xF4292244, 0x432AFF97, 0xAB9423A7, 0xFC93A039, 0x655B59C3, 0x8F0CCC92, 0xFFEFD47D, 0x85845DD1,
        0x6FA87E4F, 0xFE2CE6E0, 0xA3014314, 0x4E0811A1, 0xF7537E82, 0xBD3AF235, 0x2AD7D2BB, 0xEB86D391,
    ]
    shift = [
        7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
        5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
        4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
        6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
    ]
    data64 = bytearray(data)
    data64.append(128)
    extra = (64 - ((len(data64) + 8) & 63)) & 63
    data64.extend(b"\x00" * extra)
    bit_len = len(data) << 3
    for i in range(0, 64, 8):
        data64.append((bit_len >> i) & 0xFF)
    a0, b0, c0, d0 = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    for i in range(0, len(data64), 64):
        a, b, c, d = a0, b0, c0, d0
        for j in range(64):
            if j < 16:
                f = (b & c) | ((~b & 0xFFFFFFFF) & d)
                g = j
            elif j < 32:
                f = (d & b) | ((~d & 0xFFFFFFFF) & c)
                g = (5 * j + 1) & 0xF
            elif j < 48:
                f = (b ^ c ^ d) & 0xFFFFFFFF
                g = (3 * j + 5) & 0xF
            else:
                f = (c ^ (b | (~d & 0xFFFFFFFF))) & 0xFFFFFFFF
                g = (7 * j) & 0xF
            base = i + g * 4
            u = data64[base] | (data64[base + 1] << 8) | (data64[base + 2] << 16) | (data64[base + 3] << 24)
            f = (f + a + sineparts[j] + u) & 0xFFFFFFFF
            s = shift[j]
            a, d, c, b = d, c, b, (b + ((f << s) | (f >> (32 - s)))) & 0xFFFFFFFF
        a0 = (a0 + a) & 0xFFFFFFFF
        b0 = (b0 + b) & 0xFFFFFFFF
        c0 = (c0 + c) & 0xFFFFFFFF
        d0 = (d0 + d) & 0xFFFFFFFF
    out = bytearray(16)
    for idx, val in enumerate((a0, b0, c0, d0)):
        out[idx * 4 : idx * 4 + 4] = val.to_bytes(4, "little")
    return bytes(out)


def create_hdr_icc_profile(encoding: HdrColorEncoding) -> bytes:
    """生成与 libjxl MaybeCreateProfile 等价的 PQ/HLG HDR ICC。"""
    prim = encoding.primaries
    (rx, ry), (gx, gy), (bx, by) = _PRIMARIES[prim]
    wx, wy = _D65
    desc = {"pq": _DESC_PQ, "hlg": _DESC_HLG, "linear": _DESC_LINEAR}[encoding.transfer][prim]
    cicp_tf = _CICP_TRANSFER[encoding.transfer]

    header = bytearray(128)
    _write_u32(header, 0, 0)
    _write_tag(header, 4, b"jxl ")
    _write_u32(header, 8, 0x04400000)
    _write_tag(header, 12, b"mntr")
    _write_tag(header, 16, b"RGB ")
    _write_tag(header, 20, b"Lab ")
    _write_u16(header, 24, 2019)
    _write_u16(header, 26, 12)
    _write_u16(header, 28, 1)
    for pos, val in ((30, 0), (32, 0), (34, 0)):
        _write_u16(header, pos, val)
    _write_tag(header, 36, b"acsp")
    _write_tag(header, 40, b"APPL")
    _write_u32(header, 44, 0)
    _write_u32(header, 48, 0)
    _write_u32(header, 52, 0)
    _write_u32(header, 56, 0)
    _write_u32(header, 60, 0)
    _write_u32(header, 64, encoding.rendering_intent)
    _write_u32(header, 68, 0x0000F6D6)
    _write_u32(header, 72, 0x00010000)
    _write_u32(header, 76, 0x0000D32D)
    _write_tag(header, 80, b"jxl ")

    tagtable = bytearray()
    _append_u32(tagtable, 0)
    tags = bytearray()
    offsets: list[int] = []
    tag_offset = 0
    tag_size = 0

    def _add_tag(sig: str) -> None:
        nonlocal tag_offset, tag_size
        while len(tags) % 4:
            tags.append(0)
        tag_offset += tag_size
        tag_size = len(tags) - tag_offset
        _append_tag(tagtable, sig)
        _append_u32(tagtable, 0)
        offsets.append(tag_offset)
        _append_u32(tagtable, tag_size)

    tags.extend(_create_mluc_tag(desc))
    _add_tag("desc")
    tags.extend(_create_mluc_tag("CC0"))
    _add_tag("cprt")
    tags.extend(_create_xyz_tag(0.964203, 1.0, 0.824905))
    _add_tag("wtpt")

    chad = _adapt_to_xyz_d50(wx, wy)
    tags.extend(_create_chad_tag(chad))
    _add_tag("chad")

    tags.extend(_create_cicp_tag(_CICP_PRIMARIES[prim], cicp_tf))
    _add_tag("cicp")

    rgb_matrix = _primaries_to_xyz_d50(rx, ry, gx, gy, bx, by, wx, wy)
    for col in range(3):
        tags.extend(_create_xyz_tag(float(rgb_matrix[0, col]), float(rgb_matrix[1, col]), float(rgb_matrix[2, col])))
        _add_tag(("rXYZ", "gXYZ", "bXYZ")[col])

    enc = HdrColorEncoding(primaries=prim, transfer=encoding.transfer, rendering_intent=encoding.rendering_intent)
    tags.extend(_create_lut_atob_hdr(enc))
    _add_tag("A2B0")
    tags.extend(_create_noop_btoa_tag())
    _add_tag("B2A0")

    _write_u32(tagtable, 0, len(offsets))
    for i, off in enumerate(offsets):
        _write_u32(tagtable, 4 + 12 * i + 4, off + len(header) + len(tagtable))

    icc = bytes(header) + bytes(tagtable) + bytes(tags)
    icc_mut = bytearray(icc)
    total_size = len(icc_mut)
    _write_u32(icc_mut, 0, total_size)
    icc = bytes(icc_mut)

    icc_sum = bytearray(icc)
    icc_sum[44:48] = b"\x00\x00\x00\x00"
    icc_sum[64:68] = b"\x00\x00\x00\x00"
    checksum = _icc_compute_md5(bytes(icc_sum))
    icc_final = bytearray(icc)
    icc_final[84:100] = checksum
    return bytes(icc_final)


def create_pq_icc_for_gamut(gamut: Gamut) -> bytes:
    return create_hdr_icc_profile(HdrColorEncoding(primaries=gamut_to_primaries(gamut), transfer="pq"))


def create_hlg_icc_for_gamut(gamut: Gamut) -> bytes:
    return create_hdr_icc_profile(HdrColorEncoding(primaries=gamut_to_primaries(gamut), transfer="hlg"))


def create_linear_icc_for_gamut(gamut: Gamut) -> bytes:
    return create_hdr_icc_profile(HdrColorEncoding(primaries=gamut_to_primaries(gamut), transfer="linear"))


_BASELINE_DESC: dict[PrimariesKind, str] = {
    "srgb": "sRGB",
    "p3": "Display P3",
    "bt2020": "BT.2020",
}

# IEC 61966-2-1 sRGB 参数曲线（ICC parametric type 4）
_SRGB_PARA_PARAMS: tuple[float, float, float, float, float] = (
    2.4,
    1.0 / 1.055,
    0.055 / 1.055,
    1.0 / 12.92,
    0.04045,
)

# Apple / ICC 常用 D65 媒体白点（绝对 XYZ）
_D65_MEDIA_XYZ = (0.95045471, 1.0, 1.08905029)


def _create_ascii_desc_tag(text: str) -> bytes:
    data = bytearray()
    _append_tag(data, "desc")
    _append_u32(data, 0)
    ascii_bytes = text.encode("ascii", errors="replace") + b"\x00"
    _append_u32(data, len(ascii_bytes))
    data.extend(ascii_bytes)
    while len(data) % 4:
        data.append(0)
    return bytes(data)


def _create_para_trc_tag(function_type: int, params: tuple[float, ...]) -> bytes:
    data = bytearray()
    _append_tag(data, "para")
    _append_u32(data, 0)
    _append_u16(data, function_type)
    _append_u16(data, 0)
    for value in params:
        _append_s15_fixed16(data, value)
    while len(data) % 4:
        data.append(0)
    return bytes(data)


def _create_baseline_trc_tag(gamma: float = 2.4) -> bytes:
    """para type 3（单 gamma），与 LR / Apple Display P3 baseline ICC 一致。

    Windows 照片对 para type 4（sRGB 分段）会崩溃，勿用于 baseline。
    """
    return _create_para_trc_tag(3, (gamma,))
