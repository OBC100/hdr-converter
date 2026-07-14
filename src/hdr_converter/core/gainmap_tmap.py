"""ISO 23008-12 tmap 载荷（version 0 + ISO 21496-1 C.2.2 元数据）。"""

from __future__ import annotations

from .gainmap_math import (
    GainmapMetadata,
    GainmapMetadataFrac,
    float_metadata_to_fraction,
    fraction_metadata_to_float,
)


class _BitWriter:
    def __init__(self) -> None:
        self._buf = bytearray()
        self._bitbuf = 0
        self._bitcount = 0

    def write_bits(self, value: int, count: int) -> None:
        for i in range(count - 1, -1, -1):
            self._bitbuf = (self._bitbuf << 1) | ((value >> i) & 1)
            self._bitcount += 1
            if self._bitcount == 8:
                self._buf.append(self._bitbuf)
                self._bitbuf = 0
                self._bitcount = 0

    def flush(self) -> None:
        if self._bitcount:
            self._buf.append(self._bitbuf << (8 - self._bitcount))

    def bytes(self) -> bytes:
        self.flush()
        return bytes(self._buf)


class _BitReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self._bitbuf = 0
        self._bitcount = 0

    def read_bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            if self._bitcount == 0:
                if self._pos >= len(self._data):
                    raise ValueError("tmap bitstream 耗尽")
                self._bitbuf = self._data[self._pos]
                self._pos += 1
                self._bitcount = 8
            self._bitcount -= 1
            value = (value << 1) | ((self._bitbuf >> self._bitcount) & 1)
        return value


def _write_gainmap_metadata_c22(
    w: _BitWriter,
    frac: GainmapMetadataFrac,
    *,
    force_multichannel: bool = False,
) -> None:
    """GainMapMetadata syntax — ISO 21496-1 clause C.2.2（对齐 libavif）。"""
    identical = frac.all_channels_identical()
    channel_count = 3 if (force_multichannel or not identical) else 1
    w.write_bits(0, 16)
    w.write_bits(0, 16)
    w.write_bits(1 if channel_count == 3 else 0, 1)
    w.write_bits(1 if frac.use_base_color_space else 0, 1)
    w.write_bits(0, 6)
    w.write_bits(frac.base_hdr_headroom_n & 0xFFFFFFFF, 32)
    w.write_bits(frac.base_hdr_headroom_d & 0xFFFFFFFF, 32)
    w.write_bits(frac.alternate_hdr_headroom_n & 0xFFFFFFFF, 32)
    w.write_bits(frac.alternate_hdr_headroom_d & 0xFFFFFFFF, 32)
    for c in range(channel_count):
        src = 0 if identical else c
        w.write_bits(frac.gain_map_min_n[src] & 0xFFFFFFFF, 32)
        w.write_bits(frac.gain_map_min_d[src] & 0xFFFFFFFF, 32)
        w.write_bits(frac.gain_map_max_n[src] & 0xFFFFFFFF, 32)
        w.write_bits(frac.gain_map_max_d[src] & 0xFFFFFFFF, 32)
        w.write_bits(frac.gain_map_gamma_n[src] & 0xFFFFFFFF, 32)
        w.write_bits(frac.gain_map_gamma_d[src] & 0xFFFFFFFF, 32)
        w.write_bits(frac.base_offset_n[src] & 0xFFFFFFFF, 32)
        w.write_bits(frac.base_offset_d[src] & 0xFFFFFFFF, 32)
        w.write_bits(frac.alternate_offset_n[src] & 0xFFFFFFFF, 32)
        w.write_bits(frac.alternate_offset_d[src] & 0xFFFFFFFF, 32)


def _read_gainmap_metadata_c22(r: _BitReader) -> GainmapMetadataFrac:
    r.read_bits(16)
    r.read_bits(16)
    is_multi = r.read_bits(1)
    use_base = r.read_bits(1)
    r.read_bits(6)
    channel_count = 3 if is_multi else 1
    frac = GainmapMetadataFrac(use_base_color_space=bool(use_base))
    frac.base_hdr_headroom_n = r.read_bits(32)
    frac.base_hdr_headroom_d = r.read_bits(32)
    frac.alternate_hdr_headroom_n = r.read_bits(32)
    frac.alternate_hdr_headroom_d = r.read_bits(32)

    def _i32(v: int) -> int:
        return v - 0x100000000 if v >= 0x80000000 else v

    mins_n, mins_d, maxs_n, maxs_d = [], [], [], []
    gams_n, gams_d, base_n, base_d, alt_n, alt_d = [], [], [], [], [], []
    for _ in range(channel_count):
        mins_n.append(_i32(r.read_bits(32)))
        mins_d.append(r.read_bits(32))
        maxs_n.append(_i32(r.read_bits(32)))
        maxs_d.append(r.read_bits(32))
        gams_n.append(r.read_bits(32))
        gams_d.append(r.read_bits(32))
        base_n.append(_i32(r.read_bits(32)))
        base_d.append(r.read_bits(32))
        alt_n.append(_i32(r.read_bits(32)))
        alt_d.append(r.read_bits(32))

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


def encode_tmap_payload(
    metadata: GainmapMetadata,
    *,
    force_multichannel: bool = False,
) -> bytes:
    """ToneMapImage 载荷：version=0 + GainMapMetadata（C.2.2）。"""
    frac = float_metadata_to_fraction(metadata)
    w = _BitWriter()
    w.write_bits(0, 8)
    _write_gainmap_metadata_c22(w, frac, force_multichannel=force_multichannel)
    return w.bytes()


def parse_tmap_payload(data: bytes) -> GainmapMetadata:
    """解析 tmap 载荷 → GainmapMetadata。"""
    if not data:
        raise ValueError("空 tmap 载荷")
    r = _BitReader(data)
    version = r.read_bits(8)
    if version != 0:
        raise ValueError(f"不支持的 tmap version: {version}")
    return fraction_metadata_to_float(_read_gainmap_metadata_c22(r))


def encode_tmap_item_bytes(
    metadata: GainmapMetadata,
    *,
    force_multichannel: bool = False,
) -> bytes:
    return encode_tmap_payload(metadata, force_multichannel=force_multichannel)
