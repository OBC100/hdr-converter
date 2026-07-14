"""HDR 预览表面：Qt 原生子 HWND + D3D11 FP16 scRGB。"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPaintEvent
from PyQt6.QtWidgets import QWidget

from .hdr_d3d11 import D3D11HdrRenderer

if sys.platform == "win32":
    _GWL_EXSTYLE = -20
    _WS_EX_NOREDIRECTIONBITMAP = 0x00200000
    _user32 = ctypes.windll.user32
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    _user32.SetWindowLongW.restype = ctypes.c_long
    _user32.RedrawWindow.argtypes = [
        wintypes.HWND,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    _user32.RedrawWindow.restype = wintypes.BOOL
    _RDW_INVALIDATE = 0x0001
    _RDW_UPDATENOW = 0x0100
else:
    _user32 = None


class HdrPreviewSurface(QWidget):
    """嵌入 Fluent 的 D3D11 HDR 预览；使用 WA_NativeWindow 子 HWND。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setMinimumSize(320, 180)
        self.setUpdatesEnabled(False)
        self._renderer: D3D11HdrRenderer | None = None
        self._pending: np.ndarray | None = None
        self._init_error = ""
        self._hwnd_prepared = False
        self._bound_hwnd = 0
        self._last_scrgb: np.ndarray | None = None

    def paintEngine(self):
        return None

    def paintEvent(self, event: QPaintEvent) -> None:
        del event

    @property
    def init_error(self) -> str:
        return self._init_error

    def _prepare_native_hwnd(self, hwnd: int) -> None:
        if self._hwnd_prepared or _user32 is None:
            return
        style = _user32.GetWindowLongW(wintypes.HWND(hwnd), _GWL_EXSTYLE)
        _user32.SetWindowLongW(
            wintypes.HWND(hwnd),
            _GWL_EXSTYLE,
            style | _WS_EX_NOREDIRECTIONBITMAP,
        )
        self._hwnd_prepared = True

    def _physical_size(self, logical_w: int, logical_h: int) -> tuple[int, int]:
        """逻辑像素 → 物理像素；HiDPI 下交换链必须按物理像素分配才不会被拉伸糊化。"""
        dpr = self.devicePixelRatioF() or 1.0
        return max(1, round(logical_w * dpr)), max(1, round(logical_h * dpr))

    def _ensure_renderer(self) -> bool:
        hwnd = int(self.winId())
        if self._renderer is not None and self._bound_hwnd != hwnd:
            self.close_renderer()
        if self._renderer is not None:
            return True
        if not self.isVisible() or self.width() < 8 or self.height() < 8:
            return False
        try:
            self._prepare_native_hwnd(hwnd)
            self._init_error = ""
            self._bound_hwnd = hwnd
            self._renderer = D3D11HdrRenderer(hwnd)
            phys_w, phys_h = self._physical_size(self.width(), self.height())
            self._renderer.resize(phys_w, phys_h)
            if not self._renderer.uses_hdr_swap_chain:
                self._init_error = (
                    "HDR swap chain unavailable; using legacy D3D path "
                    f"(flip={self._renderer._flip_model})"
                )
            return True
        except Exception as exc:
            self._init_error = str(exc)
            self._renderer = None
            return False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._on_visible)

    def _on_visible(self) -> None:
        if self._pending is not None:
            self._flush_pending()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 交换链尺寸跟随帧缓冲（present 时设定），不随 HWND 重配；
        # 控件缩放由 DWM 拉伸，避免每次 resize 重建纹理。
        if self._last_scrgb is not None:
            QTimer.singleShot(0, self.represent)
        elif self._pending is not None:
            QTimer.singleShot(0, self._flush_pending)

    def _flush_pending(self) -> None:
        if self._pending is None:
            return
        if not self._ensure_renderer() or self._renderer is None:
            return
        try:
            self._renderer.present(self._pending)
            self._pending = None
            if _user32 is not None:
                hwnd = int(self.winId())
                _user32.RedrawWindow(
                    wintypes.HWND(hwnd),
                    None,
                    None,
                    _RDW_INVALIDATE | _RDW_UPDATENOW,
                )
        except Exception as exc:
            self._init_error = str(exc)
            self.close_renderer()

    def _represent_last(self) -> None:
        if self._last_scrgb is not None:
            self.set_frame(self._last_scrgb)

    def set_frame(self, scrgb: np.ndarray, *, copy: bool = True) -> None:
        """呈现一帧 scRGB 缓冲（保持缓冲分辨率，不在此做 CPU 缩放）。

        交换链按帧形状分配；控件逻辑尺寸可与帧不同，由 DWM 拉伸到 HWND。
        ``copy=False`` 时调用方须保证缓冲在 present 完成前不被原地修改。
        """
        frame = np.ascontiguousarray(scrgb, dtype=np.float32) if copy else scrgb
        self._last_scrgb = frame
        self._init_error = ""
        self._pending = frame
        self._flush_pending()

    def represent(self) -> None:
        """复用上一帧重新 Present，用于窗口激活 / 叠层清理。"""
        if self._last_scrgb is None:
            return
        self._pending = self._last_scrgb
        self._flush_pending()

    def close_renderer(self) -> None:
        self._pending = None
        self._last_scrgb = None
        self._bound_hwnd = 0
        self._hwnd_prepared = False
        if self._renderer is not None:
            try:
                self._renderer.close()
            except Exception:
                pass
            self._renderer = None


# 兼容旧名
HdrPreviewWindow = HdrPreviewSurface if sys.platform == "win32" else object
