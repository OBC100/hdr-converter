"""CICP (Coding Independent Code Points) 元数据定义与映射。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Gamut(str, Enum):
    SRGB = "srgb"
    P3 = "p3"
    BT2020 = "bt2020"


class TransferCurve(str, Enum):
    SRGB = "srgb"
    LINEAR = "linear"
    PQ = "pq"
    HLG = "hlg"


@dataclass(frozen=True)
class CICP:
    """编码无关代码点 (ISO/IEC 23091-4 / ITU-T H.273)。"""

    color_primaries: int
    transfer_characteristics: int
    matrix_coefficients: int
    full_range: bool = True

    def to_bytes(self) -> bytes:
        """PNG cICP 块 payload (4 bytes)，与 jxr_to_png 一致。"""
        return bytes(
            [
                self.color_primaries,
                self.transfer_characteristics,
                self.matrix_coefficients,
                1 if self.full_range else 0,
            ]
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> CICP:
        if len(data) < 4:
            raise ValueError(f"cICP payload 过短: {len(data)} bytes")
        return cls(
            color_primaries=data[0],
            transfer_characteristics=data[1],
            matrix_coefficients=data[2],
            full_range=bool(data[3]),
        )


# jxr_to_png 对 BT.2020+PQ 使用 matrix=0, full_range=1
_CICP_TABLE: dict[tuple[Gamut, TransferCurve], CICP] = {
    (Gamut.SRGB, TransferCurve.SRGB): CICP(1, 13, 0),
    (Gamut.SRGB, TransferCurve.PQ): CICP(1, 16, 0),
    (Gamut.SRGB, TransferCurve.HLG): CICP(1, 18, 0),
    (Gamut.P3, TransferCurve.SRGB): CICP(12, 13, 0),
    (Gamut.P3, TransferCurve.PQ): CICP(12, 16, 0),
    (Gamut.P3, TransferCurve.HLG): CICP(12, 18, 0),
    (Gamut.BT2020, TransferCurve.SRGB): CICP(9, 13, 0),
    (Gamut.BT2020, TransferCurve.PQ): CICP(9, 16, 0),
    (Gamut.BT2020, TransferCurve.HLG): CICP(9, 18, 0),
    (Gamut.SRGB, TransferCurve.LINEAR): CICP(1, 8, 0),
    (Gamut.P3, TransferCurve.LINEAR): CICP(12, 8, 0),
    (Gamut.BT2020, TransferCurve.LINEAR): CICP(9, 8, 9),
}

# 反查：(primaries, transfer, matrix) → (Gamut, TransferCurve)
CICP_TO_GAMUT_CURVE: dict[tuple[int, int, int], tuple[Gamut, TransferCurve]] = {
    (cicp.color_primaries, cicp.transfer_characteristics, cicp.matrix_coefficients): (
        gamut,
        curve,
    )
    for (gamut, curve), cicp in _CICP_TABLE.items()
}

_CICP_TO_GAMUT_CURVE_2: dict[tuple[int, int], tuple[Gamut, TransferCurve]] = {
    (cicp.color_primaries, cicp.transfer_characteristics): (gamut, curve)
    for (gamut, curve), cicp in _CICP_TABLE.items()
}


def get_cicp(gamut: Gamut, curve: TransferCurve) -> CICP:
    try:
        return _CICP_TABLE[(gamut, curve)]
    except KeyError as exc:
        raise ValueError(f"不支持的组合: gamut={gamut}, curve={curve}") from exc


def cicp_to_gamut_curve(
    color_primaries: int,
    transfer_characteristics: int,
    matrix_coefficients: int | None = None,
) -> tuple[Gamut, TransferCurve]:
    """CICP 代码点 → (Gamut, TransferCurve)。"""
    if matrix_coefficients is not None:
        key3 = (color_primaries, transfer_characteristics, matrix_coefficients)
        if key3 in CICP_TO_GAMUT_CURVE:
            return CICP_TO_GAMUT_CURVE[key3]
    key2 = (color_primaries, transfer_characteristics)
    if key2 in _CICP_TO_GAMUT_CURVE_2:
        return _CICP_TO_GAMUT_CURVE_2[key2]
    raise ValueError(
        f"无法识别的 CICP: primaries={color_primaries}, "
        f"transfer={transfer_characteristics}, matrix={matrix_coefficients}"
    )


def is_hdr_curve(curve: TransferCurve) -> bool:
    return curve in (TransferCurve.PQ, TransferCurve.HLG, TransferCurve.LINEAR)


@dataclass(frozen=True)
class ContentLightLevel:
    """内容亮度信息 (cLLi chunk / AVIF clli box)，单位为 nits。"""

    max_cll: int
    max_fall: int

    def to_png_bytes(self) -> bytes:
        """PNG cLLi: 两个 uint32，单位 0.0001 cd/m²（= nits × 10000）。"""
        import struct

        return struct.pack(
            ">II",
            self.max_cll * 10_000,
            self.max_fall * 10_000,
        )

    @classmethod
    def from_png_bytes(cls, data: bytes) -> ContentLightLevel:
        import struct

        if len(data) < 8:
            raise ValueError(f"cLLi payload 过短: {len(data)} bytes")
        max_cll_raw, max_fall_raw = struct.unpack(">II", data[:8])
        return cls(max_cll=max_cll_raw // 10_000, max_fall=max_fall_raw // 10_000)
