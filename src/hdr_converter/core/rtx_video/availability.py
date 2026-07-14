"""探测 RTX Video 原生桥与 GPU。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RtxProbeResult:
    available: bool
    bridge_path: Path | None
    reason: str
    sdk_root: Path | None = None


def _candidate_bridge_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("HDR_RTX_BRIDGE")
    if env:
        paths.append(Path(env))
    here = Path(__file__).resolve()
    # src/hdr_converter/core/rtx_video → 仓库根
    repo = here.parents[4]
    paths.extend(
        [
            repo / "native" / "rtx_bridge" / "out" / "hdr_rtx_bridge.dll",
            repo / "native" / "rtx_bridge" / "build" / "Release" / "hdr_rtx_bridge.dll",
            repo / "native" / "rtx_bridge" / "build" / "hdr_rtx_bridge.dll",
            Path.cwd() / "hdr_rtx_bridge.dll",
        ]
    )
    # 与包同目录（打包 EXE）
    pkg = here.parents[2]
    paths.append(pkg / "hdr_rtx_bridge.dll")
    return paths


def find_bridge_dll() -> Path | None:
    for p in _candidate_bridge_paths():
        if p.is_file():
            return p
    return None


def find_sdk_root() -> Path | None:
    env = os.environ.get("NV_RTX_VIDEO_SDK")
    if not env:
        return None
    root = Path(env)
    if (root / "include").is_dir() or (root / "bin").is_dir():
        return root
    return None


def probe_rtx_video() -> RtxProbeResult:
    """返回桥接 DLL 是否可用（实际 NGX 能力在 DLL 内再校验）。"""
    sdk = find_sdk_root()
    bridge = find_bridge_dll()
    if bridge is None:
        hint = (
            "未找到 hdr_rtx_bridge.dll。请安装 NVIDIA RTX Video SDK，"
            "设置 NV_RTX_VIDEO_SDK，并编译 native/rtx_bridge（见 docs/RTX_VIDEO.md）。"
        )
        if sdk is not None:
            hint = (
                f"已检测到 SDK（{sdk}），但仍缺少编译产物 hdr_rtx_bridge.dll。"
                "请按 docs/RTX_VIDEO.md 编译 native/rtx_bridge。"
            )
        return RtxProbeResult(False, None, hint, sdk)
    return RtxProbeResult(True, bridge, "ok", sdk)
