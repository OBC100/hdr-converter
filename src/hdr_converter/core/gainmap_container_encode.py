"""Gain Map 容器层图像编码（AVIF / HEIF，不经过 libultrahdr）。"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np

from .cicp import CICP
from .color_metadata import (
  avif_encode_kwargs,
  get_baseline_cicp_for_isobmff,
  get_gainmap_item_cicp,
  nclx_save_kwargs,
)
from .isobmff_gainmap import ImageItemPayload, fix_isobmff_pixi, parse_single_image_item, _pixi_box
from .sample_bits import right_align_uint16


def encode_avif_image_bytes(
  pixels: np.ndarray,
  *,
  bit_depth: int,
  quality: int,
  cicp: CICP,
  speed: int | None = None,
  numthreads: int | None = None,
  parallel_jobs: int = 1,
  pixelformat: str | None = None,
) -> bytes:
  """单图 AVIF 编码（Direct / Gain Map 基础层共用）。

  ``pixels``：uint8 或已按 ``bit_depth`` 量化的 uint16（左对齐 16-bit 会在此右移）。
  """
  import imagecodecs

  px = right_align_uint16(pixels, bit_depth)
  if px.dtype != np.uint16:
    px = px.astype(np.uint8)

  kw = avif_encode_kwargs(
    cicp,
    level=quality,
    speed=speed,
    numthreads=numthreads,
    parallel_jobs=parallel_jobs,
  )
  kw["bitspersample"] = bit_depth
  if pixelformat is not None:
    kw["pixelformat"] = pixelformat
  return imagecodecs.avif_encode(px, **kw)


def _is_color_gain(gain: np.ndarray, *, multichannel: bool) -> bool:
  return multichannel and gain.ndim == 3


def _select_gain_channels(gain: np.ndarray, *, multichannel: bool) -> np.ndarray:
  """按 multichannel 选出编码用像素：彩色保留三通道，否则取单通道灰度（AVIF/HEIF 共用）。"""
  if _is_color_gain(gain, multichannel=multichannel):
    return gain
  return gain[..., 0] if gain.ndim == 3 else gain


def encode_avif_rgb(
  sdr_pixels: np.ndarray,
  *,
  bit_depth: int,
  quality: int,
  cicp: CICP,
  speed: int | None = None,
  numthreads: int | None = None,
  parallel_jobs: int = 1,
) -> ImageItemPayload:
  encoded = encode_avif_image_bytes(
    sdr_pixels,
    bit_depth=bit_depth,
    quality=quality,
    cicp=cicp,
    speed=speed,
    numthreads=numthreads,
    parallel_jobs=parallel_jobs,
  )
  return parse_single_image_item(encoded)


def encode_avif_rgb_file(
  sdr_pixels: np.ndarray,
  output_path: Path,
  *,
  bit_depth: int = 8,
  quality: int,
  cicp: CICP | None = None,
  gamut=None,
  speed: int | None = None,
  numthreads: int | None = None,
) -> None:
  """将 SDR 基础图编码为单图 AVIF 文件（无 Gain Map 容器）。"""
  from .cicp import Gamut

  g = gamut or Gamut.BT2020
  cicp = cicp or get_baseline_cicp_for_isobmff(g)
  output_path.write_bytes(
    encode_avif_image_bytes(
      sdr_pixels,
      bit_depth=bit_depth,
      quality=quality,
      cicp=cicp,
      speed=speed,
      numthreads=numthreads,
      parallel_jobs=1,
    )
  )


def encode_avif_gain(
  gain: np.ndarray,
  *,
  bit_depth: int,
  quality: int,
  multichannel: bool,
  speed: int | None = None,
  numthreads: int | None = None,
  parallel_jobs: int = 1,
) -> ImageItemPayload:
  pixfmt = "YUV444" if _is_color_gain(gain, multichannel=multichannel) else "YUV400"
  pixels = _select_gain_channels(gain, multichannel=multichannel)
  encoded = encode_avif_image_bytes(
    pixels,
    bit_depth=bit_depth,
    quality=quality,
    cicp=get_gainmap_item_cicp(),
    speed=speed,
    numthreads=numthreads,
    parallel_jobs=parallel_jobs,
    pixelformat=pixfmt,
  )
  return parse_single_image_item(encoded)


def encode_heif_image_bytes(
  pixels: np.ndarray,
  *,
  bit_depth: int,
  quality: int,
  cicp: CICP,
  icc: bytes | None = None,
) -> bytes:
  """单图 HEIF 编码（Direct / Gain Map 基础层共用）。

  ``pixels``：uint8，或左对齐 uint16（与 ``bit_depth`` 配套，同 PNG/AVIF 量化约定）。
  Direct / Gain Map 基础层均应 ``icc=None``，仅写 NCLX（Windows 照片兼容）。
  """
  from pillow_heif.constants import HeifCompressionFormat
  from pillow_heif.misc import CtxEncode

  if pixels.ndim == 2:
    h, w = pixels.shape
    num_channels = 1
    mode = "L;16" if bit_depth > 8 else "L"
    data = pixels.astype(np.uint16 if bit_depth > 8 else np.uint8).tobytes()
  elif pixels.dtype == np.uint16 or bit_depth > 8:
    px = pixels.astype(np.uint16)
    mode = "RGB;16"
    h, w = px.shape[:2]
    num_channels = 3
    data = px.tobytes()
  else:
    px = pixels.astype(np.uint8)
    mode = "RGB"
    h, w = px.shape[:2]
    num_channels = 3
    data = px.tobytes()

  ctx = CtxEncode(HeifCompressionFormat.HEVC, quality=quality)
  ctx.add_image(
    (w, h),
    mode,
    data,
    primary=True,
    bit_depth=bit_depth,
    icc_profile=icc,
    **nclx_save_kwargs(cicp),
  )
  buf = io.BytesIO()
  ctx.save(buf)
  # pillow-heif 常把 RGB 的 pixi 写成 1 通道，Windows 照片会解成偏色/全红
  return fix_isobmff_pixi(
    buf.getvalue(), bit_depth=bit_depth, num_channels=num_channels
  )


def encode_heif_rgb(
  sdr_pixels: np.ndarray,
  *,
  bit_depth: int,
  quality: int,
  cicp: CICP,
  icc: bytes | None = None,
) -> ImageItemPayload:
  encoded = encode_heif_image_bytes(
    sdr_pixels,
    bit_depth=bit_depth,
    quality=quality,
    cicp=cicp,
    icc=icc,
  )
  item = parse_single_image_item(encoded)
  channels = 1 if sdr_pixels.ndim == 2 else 3
  item.bit_depth = bit_depth
  item.pixi = _pixi_box(bit_depth, channels)
  return item


def encode_heif_gain(
  gain: np.ndarray,
  *,
  bit_depth: int,
  quality: int,
  multichannel: bool,
) -> ImageItemPayload:
  from pillow_heif.constants import HeifCompressionFormat
  from pillow_heif.misc import CtxEncode

  is_color = _is_color_gain(gain, multichannel=multichannel)
  pixels = _select_gain_channels(gain, multichannel=multichannel)
  if pixels.ndim == 3:
    h, w, _ = pixels.shape
  else:
    h, w = pixels.shape
  mode = ("RGB" if is_color else "L") if bit_depth <= 8 else ("RGB;16" if is_color else "L;16")
  dtype = np.uint8 if bit_depth <= 8 else np.uint16
  data = pixels.astype(dtype).tobytes()
  channels = 3 if is_color else 1

  ctx = CtxEncode(HeifCompressionFormat.HEVC, quality=quality)
  ctx.add_image((w, h), mode, data, primary=True, bit_depth=bit_depth)
  buf = io.BytesIO()
  ctx.save(buf)
  encoded = fix_isobmff_pixi(
    buf.getvalue(), bit_depth=bit_depth, num_channels=channels
  )
  item = parse_single_image_item(encoded)
  item.bit_depth = bit_depth
  item.pixi = _pixi_box(bit_depth, channels)
  return item
