"""ctypes 加载 hdr_rtx_bridge.dll。"""

from __future__ import annotations

import ctypes
from ctypes import c_char_p, c_float, c_int, c_uint32, c_void_p
from pathlib import Path
from threading import Lock

import numpy as np

from .availability import find_bridge_dll

_lock = Lock()
_lib: ctypes.CDLL | None = None
_lib_path: Path | None = None


class RtxBridgeError(RuntimeError):
    pass


def _load_lib() -> ctypes.CDLL:
    global _lib, _lib_path
    with _lock:
        if _lib is not None:
            return _lib
        path = find_bridge_dll()
        if path is None:
            raise RtxBridgeError(
                "未找到 hdr_rtx_bridge.dll。请编译 native/rtx_bridge（docs/RTX_VIDEO.md）。"
            )
        lib = ctypes.CDLL(str(path))
        lib.rtx_bridge_version.restype = c_int
        lib.rtx_bridge_probe.restype = c_int
        lib.rtx_bridge_probe.argtypes = [ctypes.POINTER(c_char_p)]
        lib.rtx_bridge_process.restype = c_int
        lib.rtx_bridge_process.argtypes = [
            ctypes.POINTER(c_float),  # in RGBA
            c_uint32,  # in_w
            c_uint32,  # in_h
            ctypes.POINTER(c_float),  # out buffer (caller alloc max)
            ctypes.POINTER(c_uint32),  # out_w
            ctypes.POINTER(c_uint32),  # out_h
            c_int,  # mode: 1=thdr 2=vsr 3=both
            c_int,  # vsr_quality 0..4
            c_uint32,  # contrast
            c_uint32,  # saturation
            c_uint32,  # middle_gray
            c_uint32,  # max_luminance
            c_uint32,  # vsr_scale
            ctypes.POINTER(c_char_p),  # err
        ]
        lib.rtx_bridge_free_string.argtypes = [c_char_p]
        lib.rtx_bridge_free_string.restype = None
        _lib = lib
        _lib_path = path
        return lib


def bridge_available() -> bool:
    try:
        lib = _load_lib()
    except RtxBridgeError:
        return False
    err = c_char_p()
    ok = lib.rtx_bridge_probe(ctypes.byref(err))
    if err:
        lib.rtx_bridge_free_string(err)
    return ok == 1


def process_rgba_fp32(
    rgba: np.ndarray,
    *,
    mode: int,
    vsr_quality: int,
    contrast: int,
    saturation: int,
    middle_gray: int,
    max_luminance: int,
    vsr_scale: int,
) -> np.ndarray:
    """输入/输出 float32 HxWx4；输入为显示域 SDR [0,1]（TrueHDR）或同格式。

    输出为 scRGB 线性扩展范围（1.0≈80 nits）。
    """
    if rgba.ndim != 3 or rgba.shape[2] < 3:
        raise ValueError("期望 HxWx3|4 float 数组")
    h, w = rgba.shape[:2]
    src = np.ascontiguousarray(rgba[..., :4] if rgba.shape[2] >= 4 else
                               np.concatenate(
                                   [rgba[..., :3], np.ones((h, w, 1), dtype=np.float32)],
                                   axis=-1,
                               ),
                               dtype=np.float32)
    scale = max(1, int(vsr_scale))
    out_h, out_w = h * scale, w * scale
    # 预分配最大可能输出
    dst = np.empty((out_h, out_w, 4), dtype=np.float32)

    lib = _load_lib()
    err = c_char_p()
    ow = c_uint32(out_w)
    oh = c_uint32(out_h)
    rc = lib.rtx_bridge_process(
        src.ctypes.data_as(ctypes.POINTER(c_float)),
        c_uint32(w),
        c_uint32(h),
        dst.ctypes.data_as(ctypes.POINTER(c_float)),
        ctypes.byref(ow),
        ctypes.byref(oh),
        c_int(mode),
        c_int(vsr_quality),
        c_uint32(contrast),
        c_uint32(saturation),
        c_uint32(middle_gray),
        c_uint32(max_luminance),
        c_uint32(scale),
        ctypes.byref(err),
    )
    msg = ""
    if err and err.value:
        msg = err.value.decode("utf-8", errors="replace")
        lib.rtx_bridge_free_string(err)
    if rc != 0:
        raise RtxBridgeError(msg or f"rtx_bridge_process 失败 code={rc}")
    return np.ascontiguousarray(dst[: int(oh.value), : int(ow.value), :])
