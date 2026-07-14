"""HDR ICC Profile — libjxl MaybeCreateProfile 逐色域/曲线生成。"""

from __future__ import annotations

import zlib
from functools import lru_cache
from importlib import resources

from ..cicp import Gamut, TransferCurve, is_hdr_curve
from .libjxl_pq_icc import iccp_profile_name

_GAMUT_ICC: dict[tuple[Gamut, TransferCurve], tuple[str, str]] = {
    (Gamut.BT2020, TransferCurve.PQ): ("Rec2100PQ", "rec2100_pq.icc"),
    (Gamut.SRGB, TransferCurve.PQ): ("sRGB PQ", "srgb_pq.icc"),
    (Gamut.P3, TransferCurve.PQ): ("DisplayP3 PQ", "display_p3_pq.icc"),
    (Gamut.BT2020, TransferCurve.HLG): ("Rec2100HLG", "rec2100_hlg.icc"),
    (Gamut.SRGB, TransferCurve.HLG): ("sRGB HLG", "srgb_hlg.icc"),
    (Gamut.P3, TransferCurve.HLG): ("DisplayP3 HLG", "display_p3_hlg.icc"),
    (Gamut.BT2020, TransferCurve.LINEAR): ("Rec2100Linear", "rec2100_linear.icc"),
    (Gamut.SRGB, TransferCurve.LINEAR): ("sRGB Linear", "srgb_linear.icc"),
    (Gamut.P3, TransferCurve.LINEAR): ("DisplayP3 Linear", "display_p3_linear.icc"),
}


@lru_cache(maxsize=16)
def get_hdr_icc(gamut: Gamut, curve: TransferCurve) -> bytes:
    """返回指定色域 + 曲线的 ICC；LUT 按 primaries 与 transfer 重建。"""
    _name, filename = _GAMUT_ICC[(gamut, curve)]
    return resources.files(__package__).joinpath(filename).read_bytes()


def get_hdr_icc_name(gamut: Gamut, curve: TransferCurve) -> str:
    transfer = {"pq": "pq", "hlg": "hlg", "linear": "linear"}[curve.value]
    return iccp_profile_name(gamut, transfer)  # type: ignore[arg-type]


def make_iccp_chunk_data(profile_name: str, icc_bytes: bytes) -> bytes:
    """构建 iCCP chunk payload：name\\0 + compression(0) + zlib(profile)。"""
    name_bytes = profile_name.encode("latin-1") + b"\x00"
    compressed = zlib.compress(icc_bytes, 9)
    return name_bytes + b"\x00" + compressed


def should_embed_hdr_icc(gamut: Gamut, curve: TransferCurve) -> bool:
    return is_hdr_curve(curve) and gamut in (Gamut.SRGB, Gamut.P3, Gamut.BT2020)
