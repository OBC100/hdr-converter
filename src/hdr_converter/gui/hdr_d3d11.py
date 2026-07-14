"""Win11 D3D11 FP16 scRGB 交换链，供嵌入 QWidget 的 HDR 预览。"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

import numpy as np

if sys.platform != "win32":
    D3D11HdrRenderer = None  # type: ignore[misc, assignment]

    def is_system_hdr_enabled() -> bool:
        return False
else:
    HRESULT = ctypes.c_long
    LPVOID = ctypes.c_void_p

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_byte * 8),
        ]

        @classmethod
        def from_uuid(cls, s: str) -> GUID:
            import uuid

            u = uuid.UUID(s)
            g = cls()
            g.Data1 = u.time_low
            g.Data2 = u.time_mid
            g.Data3 = u.time_hi_version
            for i in range(8):
                g.Data4[i] = u.bytes[8 + i]
            return g

    class DXGI_SAMPLE_DESC(ctypes.Structure):
        _fields_ = [("Count", wintypes.UINT), ("Quality", wintypes.UINT)]

    class DXGI_MODE_DESC(ctypes.Structure):
        _fields_ = [
            ("Width", wintypes.UINT),
            ("Height", wintypes.UINT),
            ("RefreshRate", wintypes.UINT * 2),
            ("Format", wintypes.UINT),
            ("ScanlineOrdering", wintypes.UINT),
            ("Scaling", wintypes.UINT),
        ]

    class DXGI_SWAP_CHAIN_DESC(ctypes.Structure):
        _fields_ = [
            ("BufferDesc", DXGI_MODE_DESC),
            ("SampleDesc", DXGI_SAMPLE_DESC),
            ("BufferUsage", wintypes.UINT),
            ("BufferCount", wintypes.UINT),
            ("OutputWindow", wintypes.HWND),
            ("Windowed", wintypes.BOOL),
            ("SwapEffect", wintypes.UINT),
            ("Flags", wintypes.UINT),
        ]

    class DXGI_SWAP_CHAIN_DESC1(ctypes.Structure):
        _fields_ = [
            ("Width", wintypes.UINT),
            ("Height", wintypes.UINT),
            ("Format", wintypes.UINT),
            ("Stereo", wintypes.BOOL),
            ("SampleDesc", DXGI_SAMPLE_DESC),
            ("BufferUsage", wintypes.UINT),
            ("BufferCount", wintypes.UINT),
            ("Scaling", wintypes.UINT),
            ("SwapEffect", wintypes.UINT),
            ("AlphaMode", wintypes.UINT),
            ("Flags", wintypes.UINT),
        ]

    class D3D11_TEXTURE2D_DESC(ctypes.Structure):
        _fields_ = [
            ("Width", wintypes.UINT),
            ("Height", wintypes.UINT),
            ("MipLevels", wintypes.UINT),
            ("ArraySize", wintypes.UINT),
            ("Format", wintypes.UINT),
            ("SampleDesc", DXGI_SAMPLE_DESC),
            ("Usage", wintypes.UINT),
            ("BindFlags", wintypes.UINT),
            ("CPUAccessFlags", wintypes.UINT),
            ("MiscFlags", wintypes.UINT),
        ]

    class D3D11_MAPPED_SUBRESOURCE(ctypes.Structure):
        _fields_ = [
            ("pData", LPVOID),
            ("RowPitch", wintypes.UINT),
            ("DepthPitch", wintypes.UINT),
        ]

    DXGI_FORMAT_R16G16B16A16_FLOAT = 10
    DXGI_SWAP_EFFECT_DISCARD = 0
    DXGI_SWAP_EFFECT_FLIP_DISCARD = 4
    DXGI_USAGE_RENDER_TARGET_OUTPUT = 0x20
    DXGI_SCALING_STRETCH = 0
    DXGI_ALPHA_MODE_IGNORE = 0
    D3D11_USAGE_DEFAULT = 0
    D3D11_USAGE_STAGING = 3
    D3D11_CPU_ACCESS_READ = 0x20000
    D3D11_CPU_ACCESS_WRITE = 0x10000
    D3D11_MAP_READ = 1
    D3D11_MAP_WRITE = 2
    D3D11_BIND_SHADER_RESOURCE = 0x8
    D3D11_BIND_RENDER_TARGET = 0x20
    D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
    D3D_DRIVER_TYPE_HARDWARE = 1
    D3D11_SDK_VERSION = 7
    DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709 = 9

    # IDXGISwapChain vtable slots (Win11 x64)
    _VTBL_PRESENT = 8
    _VTBL_GET_BUFFER = 9
    _VTBL_RESIZE_BUFFERS = 13
    _VTBL_CREATE_SWAP_CHAIN_FOR_HWND = 15
    _VTBL_SET_COLOR_SPACE1 = 23
    _VTBL_COPY_SUBRESOURCE_REGION = 46
    _VTBL_MAP = 14
    _VTBL_UNMAP = 15

    IID_IDXGIFactory1 = GUID.from_uuid("770AAE78-F26F-4DBA-A829-253C83D1B387")
    IID_IDXGIFactory2 = GUID.from_uuid("50C832A4-927B-431D-8A8E-CEF70DEE3EA9")
    IID_ID3D11Texture2D = GUID.from_uuid("6F15AAF2-D208-4E89-9AB4-489535D34F9C")

    _VTBL_QUERY_INTERFACE = 0

    d3d11 = ctypes.windll.d3d11
    dxgi = ctypes.windll.dxgi
    d3d11.D3D11CreateDevice.argtypes = [
        LPVOID,
        wintypes.UINT,
        LPVOID,
        wintypes.UINT,
        LPVOID,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(LPVOID),
        ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(LPVOID),
    ]
    d3d11.D3D11CreateDevice.restype = wintypes.UINT
    dxgi.CreateDXGIFactory1.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(LPVOID)]
    dxgi.CreateDXGIFactory1.restype = wintypes.UINT
    if hasattr(dxgi, "CreateDXGIFactory2"):
        dxgi.CreateDXGIFactory2.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(GUID),
            ctypes.POINTER(LPVOID),
        ]
        dxgi.CreateDXGIFactory2.restype = wintypes.UINT

    def _check(hr: int, msg: str) -> None:
        if int(hr) < 0:
            raise OSError(f"{msg} (HRESULT=0x{int(hr) & 0xFFFFFFFF:08X})")

    def _com_fn(com_ptr: int | LPVOID, slot: int, restype, *argtypes):
        ptr_val = com_ptr if isinstance(com_ptr, int) else com_ptr.value
        if not ptr_val:
            raise OSError(f"null COM pointer (slot {slot})")
        this = LPVOID(ptr_val)

        def call(*args):
            vtbl = ctypes.cast(this, ctypes.POINTER(LPVOID)).contents.value
            fn = ctypes.CFUNCTYPE(restype, LPVOID, *argtypes)(
                ctypes.cast(vtbl, ctypes.POINTER(LPVOID))[slot]
            )
            return fn(this, *args)

        return call

    def _release(com_ptr: LPVOID) -> None:
        if com_ptr and com_ptr.value:
            _com_fn(com_ptr.value, 2, wintypes.ULONG)()

    class D3D11HdrRenderer:
        """将 scRGB float32 (H,W,3) 呈现到 HWND 交换链。"""

        def __init__(self, hwnd: int) -> None:
            self._hwnd = hwnd
            self._device = LPVOID()
            self._context = LPVOID()
            self._swap = LPVOID()
            self._upload = LPVOID()
            self._read_staging = LPVOID()
            self._write_staging = LPVOID()
            self._factory = LPVOID()
            self._factory2 = LPVOID()
            self._width = 0
            self._height = 0
            self._flip_model = False
            self._uses_for_hwnd = False
            self._last_upload: np.ndarray | None = None
            self._present_rgba: np.ndarray | None = None
            self._init_device()
            self.resize(640, 360)

        @staticmethod
        def is_supported() -> bool:
            if sys.platform != "win32":
                return False
            try:
                return sys.getwindowsversion().build >= 19041
            except Exception:
                return False

        @property
        def uses_hdr_swap_chain(self) -> bool:
            return self._uses_for_hwnd and self._flip_model

        def _set_scrgb_color_space(self) -> None:
            if not self._flip_model or not self._swap.value:
                return
            try:
                hr = _com_fn(
                    self._swap.value,
                    _VTBL_SET_COLOR_SPACE1,
                    HRESULT,
                    wintypes.UINT,
                )(DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709)
                if int(hr) < 0:
                    raise OSError(
                        f"SetColorSpace1 failed (HRESULT=0x{int(hr) & 0xFFFFFFFF:08X})"
                    )
            except OSError:
                raise
            except Exception as exc:
                raise OSError(f"SetColorSpace1 failed: {exc}") from exc

        def _create_dxgi_factory(self) -> None:
            self._factory = LPVOID()
            if hasattr(dxgi, "CreateDXGIFactory2"):
                hr = int(
                    dxgi.CreateDXGIFactory2(
                        0,
                        ctypes.byref(IID_IDXGIFactory2),
                        ctypes.byref(self._factory),
                    )
                )
                if hr >= 0 and self._factory.value:
                    return
            self._factory = LPVOID()
            _check(
                dxgi.CreateDXGIFactory1(
                    ctypes.byref(IID_IDXGIFactory1), ctypes.byref(self._factory)
                ),
                "CreateDXGIFactory1",
            )
            if not self._factory.value:
                raise OSError("CreateDXGIFactory1 returned null factory")
            factory2 = LPVOID()
            hr = int(
                _com_fn(
                    self._factory.value,
                    _VTBL_QUERY_INTERFACE,
                    HRESULT,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(LPVOID),
                )(ctypes.byref(IID_IDXGIFactory2), ctypes.byref(factory2))
            )
            if hr >= 0 and factory2.value:
                self._factory2 = factory2

        def _factory_for_hwnd(self) -> int:
            if self._factory2.value:
                return self._factory2.value
            return self._factory.value

        def _create_swap_chain(self, width: int, height: int) -> None:
            swap_out = LPVOID()
            last_err = ""
            factory_ptr = self._factory_for_hwnd()

            desc1 = DXGI_SWAP_CHAIN_DESC1()
            desc1.Width = width
            desc1.Height = height
            desc1.Format = DXGI_FORMAT_R16G16B16A16_FLOAT
            desc1.SampleDesc.Count = 1
            desc1.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT
            desc1.BufferCount = 2
            desc1.Scaling = DXGI_SCALING_STRETCH
            desc1.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD
            desc1.AlphaMode = DXGI_ALPHA_MODE_IGNORE
            hr = int(
                _com_fn(
                    factory_ptr,
                    _VTBL_CREATE_SWAP_CHAIN_FOR_HWND,
                    HRESULT,
                    LPVOID,
                    wintypes.HWND,
                    ctypes.POINTER(DXGI_SWAP_CHAIN_DESC1),
                    LPVOID,
                    LPVOID,
                    ctypes.POINTER(LPVOID),
                )(
                    self._device.value,
                    wintypes.HWND(self._hwnd),
                    ctypes.byref(desc1),
                    None,
                    None,
                    ctypes.byref(swap_out),
                )
            )
            if hr >= 0 and swap_out.value:
                self._swap = swap_out
                self._flip_model = True
                self._uses_for_hwnd = True
                self._set_scrgb_color_space()
                return
            last_err = f"CreateSwapChainForHwnd=0x{hr & 0xFFFFFFFF:08X}"

            if not self._factory.value:
                raise OSError(f"CreateSwapChain ({last_err})")

            desc = DXGI_SWAP_CHAIN_DESC()
            desc.BufferDesc.Width = width
            desc.BufferDesc.Height = height
            desc.BufferDesc.Format = DXGI_FORMAT_R16G16B16A16_FLOAT
            desc.BufferDesc.RefreshRate[0] = 60
            desc.BufferDesc.RefreshRate[1] = 1
            desc.SampleDesc.Count = 1
            desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT
            desc.OutputWindow = wintypes.HWND(self._hwnd)
            desc.Windowed = True

            for buffer_count, swap_effect, flip in (
                (2, DXGI_SWAP_EFFECT_FLIP_DISCARD, True),
                (1, DXGI_SWAP_EFFECT_DISCARD, False),
            ):
                desc.BufferCount = buffer_count
                desc.SwapEffect = swap_effect
                swap_out = LPVOID()
                hr = _com_fn(
                    self._factory.value,
                    10,
                    HRESULT,
                    LPVOID,
                    ctypes.POINTER(DXGI_SWAP_CHAIN_DESC),
                    ctypes.POINTER(LPVOID),
                )(self._device.value, ctypes.byref(desc), ctypes.byref(swap_out))
                if int(hr) >= 0:
                    self._swap = swap_out
                    self._flip_model = flip
                    self._uses_for_hwnd = False
                    self._set_scrgb_color_space()
                    return
                last_err = f"CreateSwapChain=0x{int(hr) & 0xFFFFFFFF:08X}"

            raise OSError(f"CreateSwapChain ({last_err})")

        def _init_device(self) -> None:
            _check(
                d3d11.D3D11CreateDevice(
                    None,
                    D3D_DRIVER_TYPE_HARDWARE,
                    None,
                    D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                    None,
                    0,
                    D3D11_SDK_VERSION,
                    ctypes.byref(self._device),
                    None,
                    ctypes.byref(self._context),
                ),
                "D3D11CreateDevice",
            )
            self._create_dxgi_factory()
            self._create_swap_chain(640, 360)

        def _release_read_staging(self) -> None:
            if self._read_staging.value:
                _release(self._read_staging)
                self._read_staging = LPVOID()

        def _release_write_staging(self) -> None:
            if self._write_staging.value:
                _release(self._write_staging)
                self._write_staging = LPVOID()

        def _ensure_read_staging(self) -> None:
            if self._read_staging.value:
                return
            tex_desc = D3D11_TEXTURE2D_DESC()
            tex_desc.Width = self._width
            tex_desc.Height = self._height
            tex_desc.MipLevels = 1
            tex_desc.ArraySize = 1
            tex_desc.Format = DXGI_FORMAT_R16G16B16A16_FLOAT
            tex_desc.SampleDesc.Count = 1
            tex_desc.Usage = D3D11_USAGE_STAGING
            tex_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ
            tex_out = LPVOID()
            _check(
                _com_fn(
                    self._device.value,
                    5,
                    HRESULT,
                    ctypes.POINTER(D3D11_TEXTURE2D_DESC),
                    LPVOID,
                    ctypes.POINTER(LPVOID),
                )(ctypes.byref(tex_desc), None, ctypes.byref(tex_out)),
                "CreateReadStagingTexture2D",
            )
            self._read_staging = tex_out

        def _ensure_write_staging(self) -> None:
            if self._write_staging.value:
                return
            tex_desc = D3D11_TEXTURE2D_DESC()
            tex_desc.Width = self._width
            tex_desc.Height = self._height
            tex_desc.MipLevels = 1
            tex_desc.ArraySize = 1
            tex_desc.Format = DXGI_FORMAT_R16G16B16A16_FLOAT
            tex_desc.SampleDesc.Count = 1
            tex_desc.Usage = D3D11_USAGE_STAGING
            tex_desc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE
            tex_out = LPVOID()
            _check(
                _com_fn(
                    self._device.value,
                    5,
                    HRESULT,
                    ctypes.POINTER(D3D11_TEXTURE2D_DESC),
                    LPVOID,
                    ctypes.POINTER(LPVOID),
                )(ctypes.byref(tex_desc), None, ctypes.byref(tex_out)),
                "CreateWriteStagingTexture2D",
            )
            self._write_staging = tex_out

        def _upload_rgba16f(self, rgba_f16: np.ndarray) -> None:
            """通过 staging Map 写入 GPU 纹理（UpdateSubresource vtable 不可靠）。"""
            h, w = rgba_f16.shape[:2]
            self._ensure_write_staging()
            mapped = D3D11_MAPPED_SUBRESOURCE()
            _check(
                _com_fn(
                    self._context.value,
                    _VTBL_MAP,
                    HRESULT,
                    LPVOID,
                    wintypes.UINT,
                    wintypes.UINT,
                    wintypes.UINT,
                    ctypes.POINTER(D3D11_MAPPED_SUBRESOURCE),
                )(self._write_staging, 0, D3D11_MAP_WRITE, 0, ctypes.byref(mapped)),
                "Map write staging",
            )
            row_bytes = w * 8
            for y in range(h):
                ctypes.memmove(
                    mapped.pData + y * mapped.RowPitch,
                    rgba_f16[y].ctypes.data,
                    row_bytes,
                )
            _com_fn(
                self._context.value,
                _VTBL_UNMAP,
                None,
                LPVOID,
                wintypes.UINT,
            )(self._write_staging, 0)
            _com_fn(
                self._context.value,
                _VTBL_COPY_SUBRESOURCE_REGION,
                None,
                LPVOID,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
                LPVOID,
                wintypes.UINT,
                LPVOID,
            )(self._upload, 0, 0, 0, 0, self._write_staging, 0, LPVOID())

        def resize(self, width: int, height: int) -> None:
            width = max(1, int(width))
            height = max(1, int(height))
            if width == self._width and height == self._height and self._upload.value:
                return
            self._width = width
            self._height = height
            if self._upload.value:
                _release(self._upload)
                self._upload = LPVOID()
            self._release_read_staging()
            self._release_write_staging()

            if self._uses_for_hwnd:
                if self._swap.value:
                    _release(self._swap)
                    self._swap = LPVOID()
                self._create_swap_chain(width, height)
            else:
                _check(
                    _com_fn(
                        self._swap.value,
                        _VTBL_RESIZE_BUFFERS,
                        HRESULT,
                        wintypes.UINT,
                        wintypes.UINT,
                        wintypes.UINT,
                        wintypes.UINT,
                    )(0, width, height, DXGI_FORMAT_R16G16B16A16_FLOAT, 0),
                    "ResizeBuffers",
                )
                self._set_scrgb_color_space()

            tex_desc = D3D11_TEXTURE2D_DESC()
            tex_desc.Width = width
            tex_desc.Height = height
            tex_desc.MipLevels = 1
            tex_desc.ArraySize = 1
            tex_desc.Format = DXGI_FORMAT_R16G16B16A16_FLOAT
            tex_desc.SampleDesc.Count = 1
            tex_desc.Usage = D3D11_USAGE_DEFAULT
            tex_desc.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET

            tex_out = LPVOID()
            _check(
                _com_fn(
                    self._device.value,
                    5,
                    HRESULT,
                    ctypes.POINTER(D3D11_TEXTURE2D_DESC),
                    LPVOID,
                    ctypes.POINTER(LPVOID),
                )(ctypes.byref(tex_desc), None, ctypes.byref(tex_out)),
                "CreateTexture2D",
            )
            self._upload = tex_out

        def present(self, scrgb: np.ndarray) -> None:
            h, w = scrgb.shape[:2]
            if w != self._width or h != self._height:
                self.resize(w, h)

            rgba = np.empty((h, w, 4), dtype=np.float32)
            rgba[..., :3] = np.clip(scrgb[..., :3], 0.0, 65504.0)
            rgba[..., 3] = 1.0
            # 保留整缓冲引用，供调试读回；避免每帧额外 .copy()
            self._present_rgba = rgba
            self._last_upload = rgba[..., :3]
            rgba_f16 = np.ascontiguousarray(rgba, dtype=np.float16)
            self._upload_rgba16f(rgba_f16)

            back = LPVOID()
            _check(
                _com_fn(
                    self._swap.value,
                    _VTBL_GET_BUFFER,
                    HRESULT,
                    wintypes.UINT,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(LPVOID),
                )(
                    0,
                    ctypes.byref(IID_ID3D11Texture2D),
                    ctypes.byref(back),
                ),
                "GetBuffer",
            )

            _com_fn(
                self._context.value,
                _VTBL_COPY_SUBRESOURCE_REGION,
                None,
                LPVOID,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
                LPVOID,
                wintypes.UINT,
                LPVOID,
            )(back, 0, 0, 0, 0, self._upload, 0, LPVOID())
            _release(back)

            _check(
                _com_fn(
                    self._swap.value,
                    _VTBL_PRESENT,
                    HRESULT,
                    wintypes.UINT,
                    wintypes.UINT,
                )(0, 0),
                "Present",
            )

        def readback_upload_scrgb(self) -> np.ndarray | None:
            """读回最近一次上传的 scRGB（供调试分析）。"""
            if self._last_upload is not None:
                return self._last_upload.copy()
            return None

        def close(self) -> None:
            self._last_upload = None
            for attr in (
                "_read_staging",
                "_write_staging",
                "_upload",
                "_swap",
                "_factory2",
                "_factory",
                "_context",
                "_device",
            ):
                ptr = getattr(self, attr)
                if ptr.value:
                    _release(ptr)
                    setattr(self, attr, LPVOID())

    def is_system_hdr_enabled() -> bool:
        """读取 Windows「使用 HDR」开关（注册表）。"""
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\VideoSettings",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AdvancedColorEnabled")
                return int(val) != 0
        except OSError:
            return False
