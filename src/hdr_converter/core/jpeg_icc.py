"""JPEG ICC_PROFILE APP2 段插入 / 替换 / MPF 偏移修正。"""

from __future__ import annotations

import struct


def _strip_icc_profile_segments(jpeg: bytes) -> bytes:
    """移除 JPEG 中全部 ICC_PROFILE APP2 段（含 libultrahdr 自带 profile）。"""
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("不是 JPEG 文件")
    parts: list[bytes] = [jpeg[:2]]
    i = 2
    while i < len(jpeg) - 1:
        if jpeg[i] != 0xFF:
            parts.append(jpeg[i:])
            break
        marker = jpeg[i + 1]
        if marker == 0xDA:
            parts.append(jpeg[i:])
            break
        if marker in (0xD8, 0xD9):
            i += 2
            continue
        seg_len = struct.unpack(">H", jpeg[i + 2 : i + 4])[0]
        segment = jpeg[i : i + 2 + seg_len]
        payload = jpeg[i + 4 : i + 2 + seg_len]
        if marker == 0xE2 and payload.startswith(b"ICC_PROFILE"):
            i += 2 + seg_len
            continue
        parts.append(segment)
        i += 2 + seg_len
    return b"".join(parts)


_ICC_PROFILE_SIG = b"ICC_PROFILE\x00"
_ICC_CHUNK_MAX = 65519 - len(_ICC_PROFILE_SIG) - 2


def _iter_jpeg_segments(jpeg: bytes) -> list[tuple[int, int, int, bytes]]:
    """返回 (marker, start, end, payload)，不含 SOS 之后。"""
    segs: list[tuple[int, int, int, bytes]] = []
    i = 2
    while i < len(jpeg) - 1:
        if jpeg[i] != 0xFF:
            break
        marker = jpeg[i + 1]
        if marker == 0xDA:
            break
        if marker in (0xD8, 0xD9):
            i += 2
            continue
        seg_len = struct.unpack_from(">H", jpeg, i + 2)[0]
        end = i + 2 + seg_len
        segs.append((marker, i, end, jpeg[i + 4 : end]))
        i = end
    return segs


def _icc_profile_segments(
    segs: list[tuple[int, int, int, bytes]],
) -> list[tuple[int, int, int]]:
    """ICC APP2 段：(start, end, icc_data_capacity)。"""
    out: list[tuple[int, int, int]] = []
    for marker, start, end, payload in segs:
        if marker != 0xE2 or not payload.startswith(_ICC_PROFILE_SIG):
            continue
        if len(payload) < 14:
            continue
        out.append((start, end, len(payload) - 14))
    return out


def _build_icc_app2_segment(icc_chunk: bytes, index: int, total: int) -> bytes:
    payload = _ICC_PROFILE_SIG + bytes([index, total]) + icc_chunk
    return b"\xff\xe2" + struct.pack(">H", len(payload) + 2) + payload


def prepend_icc_profile(jpeg: bytes, icc: bytes) -> bytes:
    """在 JPEG SOI 之后插入 ICC_PROFILE APP2 段（mozjpeg 等无 ICC 编码器用）。"""
    if not icc:
        return jpeg
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("不是 JPEG 文件")
    chunks: list[bytes] = []
    total = max(1, (len(icc) + _ICC_CHUNK_MAX - 1) // _ICC_CHUNK_MAX)
    for i in range(total):
        chunk = icc[i * _ICC_CHUNK_MAX : (i + 1) * _ICC_CHUNK_MAX]
        chunks.append(_build_icc_app2_segment(chunk, i + 1, total))
    return jpeg[:2] + b"".join(chunks) + jpeg[2:]


def _patch_mpf_entries(jpeg: bytearray, byte_offset: int, delta: int) -> None:
    """在 byte_offset 处增删 delta 字节后，修正 MPF MPEntry 的 size/offset。"""
    if delta == 0:
        return
    segs = _iter_jpeg_segments(jpeg)
    for marker, start, end, payload in segs:
        if marker != 0xE2 or not payload.startswith(b"MPF\x00"):
            continue
        tiff_start = start + 4 + 4
        tiff = bytearray(payload[4:])
        endian = ">" if tiff[:2] == b"MM" else "<"
        ifd_off = struct.unpack(endian + "I", tiff[4:8])[0]
        pos = ifd_off
        n = struct.unpack(endian + "H", tiff[pos : pos + 2])[0]
        pos += 2
        mp_off = None
        for _ in range(n):
            tag, _typ, _cnt, val = struct.unpack(endian + "HHII", tiff[pos : pos + 12])
            pos += 12
            if tag == 0xB002:
                mp_off = val
        if mp_off is None:
            return
        pack = struct.Struct(endian + "IIIHH")
        for idx in range(2):
            epos = mp_off + idx * 16
            attr, size, off, dep1, dep2 = pack.unpack_from(tiff, epos)
            if idx == 0 and byte_offset <= 0 and size > 0:
                size = max(0, size + delta)
            if idx == 1 and off > 0 and off >= byte_offset:
                off = max(0, off + delta)
            pack.pack_into(tiff, epos, attr, size, off, dep1, dep2)
        new_payload = payload[:4] + bytes(tiff)
        new_seg = b"\xff\xe2" + struct.pack(">H", len(new_payload) + 2) + new_payload
        jpeg[start:end] = new_seg
        return


def _splice_segment(jpeg: bytearray, start: int, end: int, replacement: bytes) -> int:
    """用 replacement 替换 [start,end)，返回文件长度变化量。"""
    delta = len(replacement) - (end - start)
    jpeg[start:end] = replacement
    if delta:
        _patch_mpf_entries(jpeg, start, delta)
    return delta


def embed_baseline_icc_in_jpeg(jpeg: bytes, icc: bytes) -> bytes:
    """
    替换 Ultra HDR JPEG 的 baseline ICC。

    优先在原有 ICC_PROFILE APP2 段原位替换并保持段长不变，避免破坏 MPF 增益图偏移。
    """
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("不是 JPEG 文件")
    if not icc:
        return _strip_icc_profile_segments(jpeg)

    segs = _iter_jpeg_segments(jpeg)
    icc_segs = _icc_profile_segments(segs)
    if icc_segs:
        total_cap = sum(cap for _, _, cap in icc_segs)
        if len(icc) <= total_cap:
            out = bytearray(jpeg)
            padded = icc + b"\x00" * (total_cap - len(icc))
            pos = 0
            keep = icc_segs[0]
            for idx, (start, end, cap) in enumerate(icc_segs):
                chunk = padded[pos : pos + cap]
                pos += cap
                new_seg = _build_icc_app2_segment(chunk, idx + 1, len(icc_segs))
                if len(new_seg) != end - start:
                    raise ValueError("ICC APP2 段长度不匹配，无法原位替换")
                out[start:end] = new_seg
            # 合并为单段时去掉多余 ICC APP2
            for start, end, _cap in reversed(icc_segs[1:]):
                _splice_segment(out, start, end, b"")
            return bytes(out)

    # 无现有 ICC：在 SOI 后插入并修正 MPF
    out = bytearray(jpeg)
    chunks: list[bytes] = []
    total = (len(icc) + _ICC_CHUNK_MAX - 1) // _ICC_CHUNK_MAX
    for i in range(total):
        chunk = icc[i * _ICC_CHUNK_MAX : (i + 1) * _ICC_CHUNK_MAX]
        chunks.append(_build_icc_app2_segment(chunk, i + 1, total))
    insert = b"".join(chunks)
    _splice_segment(out, 2, 2, insert + out[2:])
    return bytes(out)
