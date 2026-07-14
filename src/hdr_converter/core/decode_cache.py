"""多格式解码结果缓存（L0 源像素缓冲）。

经格式检测 + 解码器 → SourceImage → scRGB 桥接，供现有
convert_colorspace / encode_gainmap / 预览消费。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import numpy as np

from .canonical import to_canonical_bt2020_linear
from .color_pipeline import bt2020_linear_to_scrgb
from .format_detect import InputFormat, detect_format, format_to_decoder_key
from .decoders.jxr_decoder import decode_jxr_to_source_image
from .source_image import SourceImage


@dataclass
class _CacheEntry:
    raw: np.ndarray
    mtime_ns: int
    is_hdr: bool = True


class DecodeCache:
    """按绝对路径 + mtime 缓存 scRGB float32 RGBA（桥接后）。"""

    def __init__(self, *, max_entries: int = 8) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: dict[str, _CacheEntry] = {}
        self._lock = Lock()

    @staticmethod
    def _key(path: Path) -> str:
        return str(path.resolve())

    @staticmethod
    def _mtime_ns(path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    def get(self, path: Path) -> np.ndarray | None:
        key = self._key(path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            mtime_ns = self._mtime_ns(path)
            if mtime_ns is None or mtime_ns != entry.mtime_ns:
                self._entries.pop(key, None)
                return None
            return entry.raw

    def get_entry(self, path: Path) -> _CacheEntry | None:
        key = self._key(path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            mtime_ns = self._mtime_ns(path)
            if mtime_ns is None or mtime_ns != entry.mtime_ns:
                self._entries.pop(key, None)
                return None
            return entry

    def put(self, path: Path, raw: np.ndarray, *, is_hdr: bool = True) -> None:
        mtime_ns = self._mtime_ns(path)
        if mtime_ns is None:
            return
        key = self._key(path)
        with self._lock:
            self._entries[key] = _CacheEntry(raw=raw, mtime_ns=mtime_ns, is_hdr=is_hdr)
            while len(self._entries) > self._max_entries:
                self._entries.pop(next(iter(self._entries)))

    def remove(self, path: Path) -> None:
        with self._lock:
            self._entries.pop(self._key(path), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def drop_missing(self, paths: list[Path]) -> None:
        keep = {self._key(p) for p in paths}
        with self._lock:
            for key in list(self._entries):
                if key not in keep:
                    del self._entries[key]


def source_image_to_scrgb_rgba(src: SourceImage) -> np.ndarray:
    """SourceImage → float32 HxWx4 scRGB（1.0≈80 nits），供现有管线消费。"""
    canonical = to_canonical_bt2020_linear(
        src.linear, src.primaries, src.reference_white_nits
    )
    scrgb = bt2020_linear_to_scrgb(canonical)
    h, w = scrgb.shape[:2]
    if src.alpha is not None:
        a = np.asarray(src.alpha, dtype=np.float32)
        if a.shape[:2] != (h, w):
            a = np.ones((h, w), dtype=np.float32)
    else:
        a = np.ones((h, w), dtype=np.float32)
    return np.concatenate([scrgb, a[..., None]], axis=-1).astype(np.float32, copy=False)


def decode_path_to_source_image(path: Path) -> SourceImage:
    """按魔数/扩展名分发解码器。"""
    fmt = detect_format(path)
    if fmt == InputFormat.JXR:
        return decode_jxr_to_source_image(path)
    key = format_to_decoder_key(fmt)
    if key is None:
        raise ValueError(f"无法识别的输入格式: {path.name}")
    from .decoders import decode_to_source_image, is_format_supported

    if not is_format_supported(key):
        raise RuntimeError(f"格式解码器不可用: {key}")
    return decode_to_source_image(path, key)


def load_source_raw(
    input_path: Path,
    *,
    cache: DecodeCache | None = None,
    raw: np.ndarray | None = None,
) -> np.ndarray:
    """加载 scRGB 桥接像素：优先 raw，其次缓存，最后多格式解码。

    JXR 保留原生 scRGB（零回归）；其它格式经 canonical→scRGB 桥接。
    """
    if raw is not None:
        return raw
    if cache is not None:
        cached = cache.get(input_path)
        if cached is not None:
            return cached
    fmt = detect_format(input_path)
    if fmt == InputFormat.JXR:
        src = decode_jxr_to_source_image(input_path)
        decoded = np.asarray(src.linear, dtype=np.float32)
        if decoded.ndim == 2:
            decoded = decoded[..., None]
        if decoded.shape[-1] == 3:
            a = np.ones((*decoded.shape[:2], 1), dtype=np.float32)
            decoded = np.concatenate([decoded, a], axis=-1)
    else:
        src = decode_path_to_source_image(input_path)
        decoded = source_image_to_scrgb_rgba(src)
    if cache is not None:
        cache.put(input_path, decoded, is_hdr=src.is_hdr)
    return decoded
