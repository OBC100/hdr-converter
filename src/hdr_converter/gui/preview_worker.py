"""后台解码并生成预览帧。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from ..core.canonical import (
    SCRGB_REFERENCE_WHITE_NITS,
    to_canonical_bt2020_linear,
)
from ..core.cicp import Gamut
from ..core.decode_cache import DecodeCache, decode_path_to_source_image, load_source_raw, source_image_to_scrgb_rgba
from ..core.format_detect import InputFormat, detect_format
from .preview_frame import build_preview_frames


class PreviewWorker(QThread):
    ready = pyqtSignal(object, object, object, str)  # sdr_scrgb, hdr_scrgb, metadata, error
    failed = pyqtSignal(str)

    def __init__(
        self,
        path: Path,
        *,
        gamut: Gamut,
        decode_cache: DecodeCache | None = None,
        need_sdr: bool = True,
        need_hdr: bool = True,
    ) -> None:
        super().__init__()
        self._path = path
        self._gamut = gamut
        self._decode_cache = decode_cache
        self._need_sdr = need_sdr
        self._need_hdr = need_hdr
        self._generation = 0

    def cancel(self) -> None:
        """丢弃进行中的结果（新预览或文件切换时调用）。"""
        self._generation += 1

    def run(self) -> None:
        gen = self._generation
        try:
            fmt = detect_format(self._path)
            if fmt == InputFormat.JXR:
                # JXR：L0 仍缓存原生 scRGB（转换零回归）；预览走 canonical
                scrgb = load_source_raw(self._path, cache=self._decode_cache)
                is_hdr = True
                if self._decode_cache is not None:
                    entry = self._decode_cache.get_entry(self._path)
                    if entry is not None:
                        is_hdr = entry.is_hdr
                canonical = to_canonical_bt2020_linear(
                    scrgb, Gamut.SRGB, SCRGB_REFERENCE_WHITE_NITS
                )
                source_primaries = Gamut.SRGB
            else:
                src = decode_path_to_source_image(self._path)
                scrgb = source_image_to_scrgb_rgba(src)
                is_hdr = src.is_hdr
                if self._decode_cache is not None:
                    self._decode_cache.put(self._path, scrgb, is_hdr=is_hdr)
                canonical = to_canonical_bt2020_linear(
                    src.linear, src.primaries, src.reference_white_nits
                )
                source_primaries = src.primaries
            if gen != self._generation:
                return
            need_hdr = self._need_hdr and is_hdr
            sdr_scrgb, hdr_scrgb, metadata = build_preview_frames(
                canonical,
                gamut=self._gamut,
                need_sdr=self._need_sdr,
                need_hdr=need_hdr,
                source_primaries=source_primaries,
            )
            if gen == self._generation:
                self.ready.emit(sdr_scrgb, hdr_scrgb, metadata, "")
        except Exception as exc:
            if gen == self._generation:
                self.failed.emit(str(exc))
