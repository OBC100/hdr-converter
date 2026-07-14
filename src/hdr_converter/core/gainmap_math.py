"""Ultra HDR 增益图计算（对齐 libultrahdr gainmapmath / jpegr generateGainMap）。"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np

from .cicp import Gamut, TransferCurve
from .color_pipeline import _MAXCLL_PERCENTILE, _PQ_PEAK_NITS, compute_content_light
from .sdr_tonemap import build_sdr_linear_from_scrgb

_SDR_WHITE_NITS = 203.0
_PQ_HDR_WHITE_NITS = _PQ_PEAK_NITS
_HLG_HDR_WHITE_NITS = 1000.0
_DEFAULT_GAMMA = 1.0

_LUMA: dict[Gamut, np.ndarray] = {
    Gamut.SRGB: np.array([0.212639, 0.715169, 0.072192], dtype=np.float64),
    Gamut.P3: np.array([0.2289746, 0.6917385, 0.0792869], dtype=np.float64),
    Gamut.BT2020: np.array([0.2627, 0.677998, 0.059302], dtype=np.float64),
}

_K_IS_MULTI_CHANNEL = 1 << 7  # 0x80，对齐 gainmapmetadata.h
_K_USE_BASE_COLOR_SPACE = 1 << 6  # 0x40
_K_BACKWARD_DIRECTION = 4
_K_COMMON_DENOM = 8


@dataclass
class GainmapMetadata:
    """浮点增益图元数据（对应 uhdr_gainmap_metadata_ext_t）。"""

    min_content_boost: tuple[float, float, float] = (1.0, 1.0, 1.0)
    max_content_boost: tuple[float, float, float] = (1.0, 1.0, 1.0)
    gamma: tuple[float, float, float] = (_DEFAULT_GAMMA,) * 3
    offset_sdr: tuple[float, float, float] = (0.0, 0.0, 0.0)
    offset_hdr: tuple[float, float, float] = (0.0, 0.0, 0.0)
    hdr_capacity_min: float = 1.0
    hdr_capacity_max: float = 1.0
    use_base_cg: bool = True

    def channels_identical(self) -> bool:
        return (
            self.min_content_boost[0] == self.min_content_boost[1]
            == self.min_content_boost[2]
            and self.max_content_boost[0] == self.max_content_boost[1]
            == self.max_content_boost[2]
            and self.gamma[0] == self.gamma[1] == self.gamma[2]
            and self.offset_sdr[0] == self.offset_sdr[1] == self.offset_sdr[2]
            and self.offset_hdr[0] == self.offset_hdr[1] == self.offset_hdr[2]
        )


def hdr_reference_nits(curve: TransferCurve) -> float:
    """曲线默认参考峰值（无内容统计时的回退）。"""
    if curve == TransferCurve.HLG:
        return _HLG_HDR_WHITE_NITS
    if curve == TransferCurve.PQ:
        return _PQ_HDR_WHITE_NITS
    return _SDR_WHITE_NITS


def resolve_hdr_peak_nits(
    hdr_linear: np.ndarray,
    curve: TransferCurve,
    *,
    percentile: float = _MAXCLL_PERCENTILE,
) -> float:
    """
    按画面统计动态 HDR 峰值（对齐 libultrahdr ``targetDispPeakBrightness``）。

    使用与 PNG cLLi 相同的 99.99% MaxCLL（``compute_content_light``），
    并钳位到 [203, 10000] nits。
    """
    cll = compute_content_light(hdr_linear, percentile=percentile)
    peak = float(cll.max_cll)
    peak = max(_SDR_WHITE_NITS, min(peak, _PQ_PEAK_NITS))
    return peak


def gainmap_metadata_for_peak(peak_nits: float) -> GainmapMetadata:
    """由动态峰值 nits 生成增益图元数据（``hdr_capacity_max`` = peak / 203）。"""
    peak_nits = max(_SDR_WHITE_NITS, min(float(peak_nits), _PQ_PEAK_NITS))
    max_boost = peak_nits / _SDR_WHITE_NITS
    # 1/64 对齐 Adobe / libavif 参考样本，避免零偏移在部分阅读器上数值不稳
    off = 1.0 / 64.0
    return GainmapMetadata(
        min_content_boost=(1.0, 1.0, 1.0),
        max_content_boost=(max_boost, max_boost, max_boost),
        gamma=(_DEFAULT_GAMMA,) * 3,
        offset_sdr=(off, off, off),
        offset_hdr=(off, off, off),
        hdr_capacity_min=1.0,
        hdr_capacity_max=max_boost,
        use_base_cg=True,
    )


def default_gainmap_metadata(curve: TransferCurve) -> GainmapMetadata:
    return gainmap_metadata_for_peak(hdr_reference_nits(curve))


def _downsample(arr: np.ndarray, scale: int) -> np.ndarray:
    if scale <= 1:
        return arr
    h, w = arr.shape[:2]
    nh, nw = h // scale, w // scale
    if nh == 0 or nw == 0:
        raise ValueError(f"gainmap scale {scale} 过大，图像尺寸 {w}x{h}")
    cropped = arr[: nh * scale, : nw * scale]
    if cropped.ndim == 3:
        return cropped.reshape(nh, scale, nw, scale, -1).mean(axis=(1, 3))
    return cropped.reshape(nh, scale, nw, scale).mean(axis=(1, 3))


def _encode_gain_values(
    sdr_nits: np.ndarray,
    hdr_nits: np.ndarray,
    meta: GainmapMetadata,
) -> np.ndarray:
    min_b = np.asarray(meta.min_content_boost, dtype=np.float64)
    max_b = np.asarray(meta.max_content_boost, dtype=np.float64)
    gamma = np.asarray(meta.gamma, dtype=np.float64)
    log2_min = np.log2(min_b)
    log2_max = np.log2(max_b)

    gain = np.ones_like(sdr_nits, dtype=np.float64)
    mask = sdr_nits > 0.0
    gain[mask] = hdr_nits[mask] / sdr_nits[mask]

    if gain.ndim == 3:
        out = np.empty(gain.shape, dtype=np.uint8)
        for c in range(3):
            g = np.clip(gain[..., c], min_b[c], max_b[c])
            norm = (np.log2(g) - log2_min[c]) / (log2_max[c] - log2_min[c])
            norm = np.clip(norm, 0.0, 1.0) ** gamma[c]
            out[..., c] = np.clip(norm * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return out

    gain = np.clip(gain, min_b[0], max_b[0])
    norm = (np.log2(gain) - log2_min[0]) / (log2_max[0] - log2_min[0])
    norm = np.clip(norm, 0.0, 1.0) ** gamma[0]
    return np.clip(norm * 255.0 + 0.5, 0, 255).astype(np.uint8)


def compute_gainmap(
    hdr_linear: np.ndarray,
    sdr_linear: np.ndarray,
    gamut: Gamut,
    curve: TransferCurve,
    *,
    scale: int = 1,
    multichannel: bool = False,
    metadata: GainmapMetadata | None = None,
) -> tuple[np.ndarray, GainmapMetadata]:
    """
    从目标色域线性 HDR / SDR 计算 8-bit 增益图。

    ``hdr_linear``：绝对线性光，1.0 = 10000 nits（scRGB 标度）。
    ``sdr_linear``：0–1 显示线性（SDR 白 = 1.0）。
    """
    meta = metadata or default_gainmap_metadata(curve)
    hdr = np.maximum(hdr_linear.astype(np.float64), 0.0)
    sdr = np.maximum(sdr_linear.astype(np.float64), 0.0)

    hdr_nits = hdr * _PQ_PEAK_NITS
    sdr_nits = sdr * _SDR_WHITE_NITS

    if scale > 1:
        hdr_nits = _downsample(hdr_nits, scale)
        sdr_nits = _downsample(sdr_nits, scale)

    if multichannel:
        gain = _encode_gain_values(sdr_nits, hdr_nits, meta)
    else:
        weights = _LUMA[gamut]
        hdr_y = np.tensordot(hdr_nits, weights, axes=([-1], [0]))
        sdr_y = np.tensordot(sdr_nits, weights, axes=([-1], [0]))
        gain = _encode_gain_values(sdr_y, hdr_y, meta)

    return gain.astype(np.uint8), meta


def compute_gainmap_with_peak(
    hdr_linear: np.ndarray,
    sdr_linear: np.ndarray,
    gamut: Gamut,
    curve: TransferCurve,
    peak_nits: float,
    *,
    scale: int = 1,
    multichannel: bool = False,
) -> tuple[np.ndarray, GainmapMetadata]:
    """由已算好的 HDR/SDR 线性缓冲生成增益图（避免重复 scRGB 扫描）。"""
    peak_nits = max(_SDR_WHITE_NITS, min(float(peak_nits), _PQ_PEAK_NITS))
    meta = gainmap_metadata_for_peak(peak_nits)
    return compute_gainmap(
        hdr_linear,
        sdr_linear,
        gamut,
        curve,
        scale=scale,
        multichannel=multichannel,
        metadata=meta,
    )


def compute_gainmap_from_scrgb(
    scrgb: np.ndarray,
    gamut: Gamut,
    curve: TransferCurve,
    tonemap,
    *,
    scale: int = 1,
    multichannel: bool = False,
) -> tuple[np.ndarray, GainmapMetadata, np.ndarray]:
    """从 scRGB 计算增益图，并返回 SDR 线性（供调试）。"""
    from .color_pipeline import scrgb_to_gamut_linear

    hdr_linear = scrgb_to_gamut_linear(scrgb, gamut)
    sdr_linear = build_sdr_linear_from_scrgb(scrgb, gamut, tonemap)
    peak_nits = resolve_hdr_peak_nits(hdr_linear, curve)
    meta = gainmap_metadata_for_peak(peak_nits)
    gain, meta = compute_gainmap(
        hdr_linear,
        sdr_linear,
        gamut,
        curve,
        scale=scale,
        multichannel=multichannel,
        metadata=meta,
    )
    return gain, meta, sdr_linear


def _float_to_unsigned_fraction(v: float, max_numerator: int) -> tuple[int, int]:
    """对齐 gainmapmath.cpp floatToUnsignedFractionImpl。"""
    if math.isnan(v) or v < 0 or v > max_numerator:
        raise ValueError(f"无法将 {v} 转为分数")
    max_d = 0xFFFFFFFF if v <= 1 else int(math.floor(max_numerator / v))
    denom = 1
    prev_d = 0
    current_v = float(v) - math.floor(v)
    for _ in range(39):
        num_d = float(denom) * v
        if num_d > max_numerator:
            raise ValueError(f"分数分子溢出: {v}")
        numer = int(round(num_d))
        if abs(num_d - numer) == 0.0:
            return numer, denom
        current_v = 1.0 / current_v
        new_d = prev_d + math.floor(current_v) * denom
        if new_d > max_d:
            return numer, denom
        prev_d = denom
        if new_d > 0xFFFFFFFF:
            raise ValueError(f"分数分母溢出: {v}")
        denom = int(new_d)
        current_v -= math.floor(current_v)
    return int(round(float(denom) * v)), denom


def _float_to_signed_fraction(v: float) -> tuple[int, int]:
    n, d = _float_to_unsigned_fraction(abs(v), 0x7FFFFFFF)
    return (-n if v < 0 else n), d


@dataclass
class GainmapMetadataFrac:
    """分数域增益图元数据（uhdr_gainmap_metadata_frac）。"""

    gain_map_min_n: tuple[int, int, int] = (0, 0, 0)
    gain_map_min_d: tuple[int, int, int] = (1, 1, 1)
    gain_map_max_n: tuple[int, int, int] = (0, 0, 0)
    gain_map_max_d: tuple[int, int, int] = (1, 1, 1)
    gain_map_gamma_n: tuple[int, int, int] = (1, 1, 1)
    gain_map_gamma_d: tuple[int, int, int] = (1, 1, 1)
    base_offset_n: tuple[int, int, int] = (0, 0, 0)
    base_offset_d: tuple[int, int, int] = (1, 1, 1)
    alternate_offset_n: tuple[int, int, int] = (0, 0, 0)
    alternate_offset_d: tuple[int, int, int] = (1, 1, 1)
    base_hdr_headroom_n: int = 0
    base_hdr_headroom_d: int = 1
    alternate_hdr_headroom_n: int = 0
    alternate_hdr_headroom_d: int = 1
    backward_direction: bool = False
    use_base_color_space: bool = True

    def all_channels_identical(self) -> bool:
        return (
            self.gain_map_min_n[0] == self.gain_map_min_n[1]
            == self.gain_map_min_n[2]
            and self.gain_map_min_d[0] == self.gain_map_min_d[1]
            == self.gain_map_min_d[2]
            and self.gain_map_max_n[0] == self.gain_map_max_n[1]
            == self.gain_map_max_n[2]
            and self.gain_map_max_d[0] == self.gain_map_max_d[1]
            == self.gain_map_max_d[2]
            and self.gain_map_gamma_n[0] == self.gain_map_gamma_n[1]
            == self.gain_map_gamma_n[2]
            and self.gain_map_gamma_d[0] == self.gain_map_gamma_d[1]
            == self.gain_map_gamma_d[2]
            and self.base_offset_n[0] == self.base_offset_n[1]
            == self.base_offset_n[2]
            and self.base_offset_d[0] == self.base_offset_d[1]
            == self.base_offset_d[2]
            and self.alternate_offset_n[0] == self.alternate_offset_n[1]
            == self.alternate_offset_n[2]
            and self.alternate_offset_d[0] == self.alternate_offset_d[1]
            == self.alternate_offset_d[2]
        )


def float_metadata_to_fraction(meta: GainmapMetadata) -> GainmapMetadataFrac:
    """对齐 gainmapmetadata.cpp gainmapMetadataFloatToFraction。"""
    single = meta.channels_identical()
    ch = 1 if single else 3
    frac = GainmapMetadataFrac(use_base_color_space=meta.use_base_cg)

    def set_ch(i: int, src_i: int) -> None:
        max_n, max_d = _float_to_signed_fraction(math.log2(meta.max_content_boost[src_i]))
        min_n, min_d = _float_to_signed_fraction(math.log2(meta.min_content_boost[src_i]))
        gn, gd = _float_to_unsigned_fraction(meta.gamma[src_i], 0xFFFFFFFF)
        bo_n, bo_d = _float_to_signed_fraction(meta.offset_sdr[src_i])
        ao_n, ao_d = _float_to_signed_fraction(meta.offset_hdr[src_i])
        frac.gain_map_max_n = _tuple_set(frac.gain_map_max_n, i, max_n)
        frac.gain_map_max_d = _tuple_set(frac.gain_map_max_d, i, max_d)
        frac.gain_map_min_n = _tuple_set(frac.gain_map_min_n, i, min_n)
        frac.gain_map_min_d = _tuple_set(frac.gain_map_min_d, i, min_d)
        frac.gain_map_gamma_n = _tuple_set(frac.gain_map_gamma_n, i, gn)
        frac.gain_map_gamma_d = _tuple_set(frac.gain_map_gamma_d, i, gd)
        frac.base_offset_n = _tuple_set(frac.base_offset_n, i, bo_n)
        frac.base_offset_d = _tuple_set(frac.base_offset_d, i, bo_d)
        frac.alternate_offset_n = _tuple_set(frac.alternate_offset_n, i, ao_n)
        frac.alternate_offset_d = _tuple_set(frac.alternate_offset_d, i, ao_d)

    for i in range(ch):
        set_ch(i, i)

    if single:
        for i in (1, 2):
            frac.gain_map_max_n = _tuple_set(frac.gain_map_max_n, i, frac.gain_map_max_n[0])
            frac.gain_map_max_d = _tuple_set(frac.gain_map_max_d, i, frac.gain_map_max_d[0])
            frac.gain_map_min_n = _tuple_set(frac.gain_map_min_n, i, frac.gain_map_min_n[0])
            frac.gain_map_min_d = _tuple_set(frac.gain_map_min_d, i, frac.gain_map_min_d[0])
            frac.gain_map_gamma_n = _tuple_set(frac.gain_map_gamma_n, i, frac.gain_map_gamma_n[0])
            frac.gain_map_gamma_d = _tuple_set(frac.gain_map_gamma_d, i, frac.gain_map_gamma_d[0])
            frac.base_offset_n = _tuple_set(frac.base_offset_n, i, frac.base_offset_n[0])
            frac.base_offset_d = _tuple_set(frac.base_offset_d, i, frac.base_offset_d[0])
            frac.alternate_offset_n = _tuple_set(frac.alternate_offset_n, i, frac.alternate_offset_n[0])
            frac.alternate_offset_d = _tuple_set(frac.alternate_offset_d, i, frac.alternate_offset_d[0])

    frac.base_hdr_headroom_n, frac.base_hdr_headroom_d = _float_to_unsigned_fraction(
        math.log2(meta.hdr_capacity_min), 0xFFFFFFFF
    )
    frac.alternate_hdr_headroom_n, frac.alternate_hdr_headroom_d = _float_to_unsigned_fraction(
        math.log2(meta.hdr_capacity_max), 0xFFFFFFFF
    )
    return frac


def _tuple_set(t: tuple[int, int, int], i: int, v: int) -> tuple[int, int, int]:
    lst = list(t)
    lst[i] = v
    return tuple(lst)


def encode_gainmap_metadata_frac(frac: GainmapMetadataFrac) -> bytes:
    """对齐 gainmapmetadata.cpp encodeGainmapMetadata。"""
    out = bytearray()
    out.extend(struct.pack(">HH", 0, 0))

    channel_count = 1 if frac.all_channels_identical() else 3
    flags = 0
    if channel_count == 3:
        flags |= _K_IS_MULTI_CHANNEL
    if frac.use_base_color_space:
        flags |= _K_USE_BASE_COLOR_SPACE
    if frac.backward_direction:
        flags |= _K_BACKWARD_DIRECTION

    denom = frac.base_hdr_headroom_d
    use_common = frac.alternate_hdr_headroom_d == denom
    if use_common:
        for c in range(channel_count):
            if (
                frac.gain_map_min_d[c] != denom
                or frac.gain_map_max_d[c] != denom
                or frac.gain_map_gamma_d[c] != denom
                or frac.base_offset_d[c] != denom
                or frac.alternate_offset_d[c] != denom
            ):
                use_common = False
                break
    if use_common:
        flags |= _K_COMMON_DENOM
    out.append(flags)

    if use_common:
        out.extend(struct.pack(">I", denom))
        out.extend(struct.pack(">I", frac.base_hdr_headroom_n))
        out.extend(struct.pack(">I", frac.alternate_hdr_headroom_n))
        for c in range(channel_count):
            out.extend(struct.pack(">i", frac.gain_map_min_n[c]))
            out.extend(struct.pack(">i", frac.gain_map_max_n[c]))
            out.extend(struct.pack(">I", frac.gain_map_gamma_n[c]))
            out.extend(struct.pack(">i", frac.base_offset_n[c]))
            out.extend(struct.pack(">i", frac.alternate_offset_n[c]))
    else:
        out.extend(struct.pack(">II", frac.base_hdr_headroom_n, frac.base_hdr_headroom_d))
        out.extend(struct.pack(">II", frac.alternate_hdr_headroom_n, frac.alternate_hdr_headroom_d))
        for c in range(channel_count):
            out.extend(struct.pack(">i", frac.gain_map_min_n[c]))
            out.extend(struct.pack(">I", frac.gain_map_min_d[c]))
            out.extend(struct.pack(">i", frac.gain_map_max_n[c]))
            out.extend(struct.pack(">I", frac.gain_map_max_d[c]))
            out.extend(struct.pack(">I", frac.gain_map_gamma_n[c]))
            out.extend(struct.pack(">I", frac.gain_map_gamma_d[c]))
            out.extend(struct.pack(">i", frac.base_offset_n[c]))
            out.extend(struct.pack(">I", frac.base_offset_d[c]))
            out.extend(struct.pack(">i", frac.alternate_offset_n[c]))
            out.extend(struct.pack(">I", frac.alternate_offset_d[c]))
    return bytes(out)


def encode_iso_gainmap_metadata(meta: GainmapMetadata) -> bytes:
    """ISO 21496-1 二进制元数据（浮点 → 分数 → 编码）。"""
    return encode_gainmap_metadata_frac(float_metadata_to_fraction(meta))


def fraction_metadata_to_float(frac: GainmapMetadataFrac) -> GainmapMetadata:
    """分数域 → 浮点 GainmapMetadata。"""

    def f(n: int, d: int) -> float:
        return float(n) / float(d) if d else 0.0

    def boost(i: int) -> float:
        return 2.0 ** f(frac.gain_map_min_n[i], frac.gain_map_min_d[i])

    def boost_max(i: int) -> float:
        return 2.0 ** f(frac.gain_map_max_n[i], frac.gain_map_max_d[i])

    return GainmapMetadata(
        min_content_boost=(boost(0), boost(1), boost(2)),
        max_content_boost=(boost_max(0), boost_max(1), boost_max(2)),
        gamma=(
            f(frac.gain_map_gamma_n[0], frac.gain_map_gamma_d[0]),
            f(frac.gain_map_gamma_n[1], frac.gain_map_gamma_d[1]),
            f(frac.gain_map_gamma_n[2], frac.gain_map_gamma_d[2]),
        ),
        offset_sdr=(
            f(frac.base_offset_n[0], frac.base_offset_d[0]),
            f(frac.base_offset_n[1], frac.base_offset_d[1]),
            f(frac.base_offset_n[2], frac.base_offset_d[2]),
        ),
        offset_hdr=(
            f(frac.alternate_offset_n[0], frac.alternate_offset_d[0]),
            f(frac.alternate_offset_n[1], frac.alternate_offset_d[1]),
            f(frac.alternate_offset_n[2], frac.alternate_offset_d[2]),
        ),
        hdr_capacity_min=2.0 ** f(frac.base_hdr_headroom_n, frac.base_hdr_headroom_d),
        hdr_capacity_max=2.0
        ** f(frac.alternate_hdr_headroom_n, frac.alternate_hdr_headroom_d),
        use_base_cg=frac.use_base_color_space,
    )


def decode_gainmap_metadata_frac(data: bytes) -> GainmapMetadataFrac:
    """解析 encode_gainmap_metadata_frac 写出的 ISO 21496 二进制块。"""
    if len(data) < 5:
        raise ValueError("gainmap metadata 过短")
    pos = 4  # skip version u16 + writer u16
    flags = data[pos]
    pos += 1
    channel_count = 3 if (flags & _K_IS_MULTI_CHANNEL) else 1
    use_base = bool(flags & _K_USE_BASE_COLOR_SPACE)
    backward = bool(flags & _K_BACKWARD_DIRECTION)
    use_common = bool(flags & _K_COMMON_DENOM)

    frac = GainmapMetadataFrac(
        backward_direction=backward,
        use_base_color_space=use_base,
    )

    def read_u32() -> int:
        nonlocal pos
        v = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        return v

    def read_i32() -> int:
        nonlocal pos
        v = struct.unpack_from(">i", data, pos)[0]
        pos += 4
        return v

    if use_common:
        denom = read_u32()
        frac.base_hdr_headroom_n = read_u32()
        frac.base_hdr_headroom_d = denom
        frac.alternate_hdr_headroom_n = read_u32()
        frac.alternate_hdr_headroom_d = denom
        mins_n, maxs_n, gams_n, base_n, alt_n = [], [], [], [], []
        mins_d, maxs_d, gams_d, base_d, alt_d = [], [], [], [], []
        for _ in range(channel_count):
            mins_n.append(read_i32())
            maxs_n.append(read_i32())
            gams_n.append(read_u32())
            base_n.append(read_i32())
            alt_n.append(read_i32())
            mins_d.append(denom)
            maxs_d.append(denom)
            gams_d.append(denom)
            base_d.append(denom)
            alt_d.append(denom)
    else:
        frac.base_hdr_headroom_n = read_u32()
        frac.base_hdr_headroom_d = read_u32()
        frac.alternate_hdr_headroom_n = read_u32()
        frac.alternate_hdr_headroom_d = read_u32()
        mins_n, maxs_n, gams_n, base_n, alt_n = [], [], [], [], []
        mins_d, maxs_d, gams_d, base_d, alt_d = [], [], [], [], []
        for _ in range(channel_count):
            mins_n.append(read_i32())
            mins_d.append(read_u32())
            maxs_n.append(read_i32())
            maxs_d.append(read_u32())
            gams_n.append(read_u32())
            gams_d.append(read_u32())
            base_n.append(read_i32())
            base_d.append(read_u32())
            alt_n.append(read_i32())
            alt_d.append(read_u32())

    def pad3(vals: list[int]) -> tuple[int, int, int]:
        while len(vals) < 3:
            vals.append(vals[0])
        return (vals[0], vals[1], vals[2])

    frac.gain_map_min_n = pad3(mins_n)
    frac.gain_map_min_d = pad3(mins_d)
    frac.gain_map_max_n = pad3(maxs_n)
    frac.gain_map_max_d = pad3(maxs_d)
    frac.gain_map_gamma_n = pad3(gams_n)
    frac.gain_map_gamma_d = pad3(gams_d)
    frac.base_offset_n = pad3(base_n)
    frac.base_offset_d = pad3(base_d)
    frac.alternate_offset_n = pad3(alt_n)
    frac.alternate_offset_d = pad3(alt_d)
    return frac


def decode_iso_gainmap_metadata(data: bytes) -> GainmapMetadata:
    """ISO 21496-1 二进制 → GainmapMetadata。"""
    return fraction_metadata_to_float(decode_gainmap_metadata_frac(data))


def apply_gainmap(
    base_sdr_linear: np.ndarray,
    gain: np.ndarray,
    metadata: GainmapMetadata,
    *,
    gamut: Gamut = Gamut.BT2020,
) -> np.ndarray:
    """ISO 21496-1 增益图还原：SDR 显示线性 → HDR 线性（1.0 = 10000 nits）。

    ``base_sdr_linear``：0–1，1.0 = SDR 白（203 nits）。
    ``gain``：uint8 单通道或三通道；尺寸可小于 base（会双线性放大）。
    与本项目 ``_encode_gain_values`` 互逆（含 offset 时走完整 ISO 公式）。
    """
    sdr = np.maximum(np.asarray(base_sdr_linear[..., :3], dtype=np.float64), 0.0)
    h, w = sdr.shape[:2]
    g = np.asarray(gain, dtype=np.float64)
    if g.ndim == 2:
        g = g[..., None]
    if g.shape[0] != h or g.shape[1] != w:
        # 最近邻放大到 base 尺寸
        ys = (np.arange(h) * g.shape[0] // h).astype(np.int32)
        xs = (np.arange(w) * g.shape[1] // w).astype(np.int32)
        g = g[ys][:, xs]

    norm = np.clip(g / 255.0, 0.0, 1.0)
    min_b = np.asarray(metadata.min_content_boost, dtype=np.float64)
    max_b = np.asarray(metadata.max_content_boost, dtype=np.float64)
    gamma = np.asarray(metadata.gamma, dtype=np.float64)
    off_s = np.asarray(metadata.offset_sdr, dtype=np.float64)
    off_h = np.asarray(metadata.offset_hdr, dtype=np.float64)
    log2_min = np.log2(min_b)
    log2_max = np.log2(max_b)

    multichannel = g.shape[-1] >= 3
    if multichannel:
        recovered = np.empty_like(sdr)
        for c in range(3):
            n = np.clip(norm[..., min(c, norm.shape[-1] - 1)], 0.0, 1.0) ** (
                1.0 / max(gamma[c], 1e-6)
            )
            log_g = log2_min[c] + n * (log2_max[c] - log2_min[c])
            ratio = np.exp2(log_g)
            # 与 _encode_gain_values 互逆：ratio = hdr_nits / sdr_nits（未用 offset）
            sdr_nits = sdr[..., c] * _SDR_WHITE_NITS
            recovered[..., c] = np.clip(sdr_nits * ratio, 0.0, None) / _PQ_PEAK_NITS
    else:
        weights = _LUMA[gamut]
        n = np.clip(norm[..., 0], 0.0, 1.0) ** (1.0 / max(gamma[0], 1e-6))
        log_g = log2_min[0] + n * (log2_max[0] - log2_min[0])
        ratio = np.exp2(log_g)
        sdr_nits = sdr * _SDR_WHITE_NITS
        sdr_y = np.tensordot(sdr_nits, weights, axes=([-1], [0]))
        hdr_y = sdr_y * ratio
        scale = np.ones_like(sdr_y)
        mask = sdr_y > 1e-10
        scale[mask] = hdr_y[mask] / sdr_y[mask]
        recovered = np.clip(sdr_nits * scale[..., None], 0.0, None) / _PQ_PEAK_NITS

    _ = (off_s, off_h)  # 元数据保留；本项目 encode 路径未嵌入 offset 比值
    return recovered.astype(np.float32)
