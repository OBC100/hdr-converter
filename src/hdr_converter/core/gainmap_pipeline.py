"""Ultra HDR Gain Map 编码管线（统一核心 + 手动容器适配器）。"""

from __future__ import annotations

from pathlib import Path

from .baseline_icc import get_baseline_display_icc
from .assets import get_hdr_icc
from .cicp import CICP, ContentLightLevel
from .color_metadata import (
  get_baseline_cicp_for_isobmff,
  get_direct_cicp_for_isobmff,
  should_write_clli,
)
from .encoders.base import EncodeOptions, OutputFormat
from .gainmap_container_encode import (
  encode_avif_gain,
  encode_avif_rgb,
  encode_heif_gain,
  encode_heif_rgb,
)
from .gainmap_core import (
  GainMapBuffers,
  gain_pixels_for_jpeg,
  prepare_gainmap_buffers,
  scrgb_to_hdr_linear,
)
from .isobmff_gainmap import (
  attach_icc_to_avif,
  attach_icc_to_heif,
  mux_gainmap_avif,
  mux_gainmap_heif,
)
from .jpeg_encode import encode_rgb_jpeg
from .jxl_gainmap import encode_jxl_base_bytes, encode_jxl_gain_bytes, mux_jxl_gainmap
from .hdr_options import uses_gainmap
from .parallel import run_parallel_pair
from .uhdr_jpeg_mux import mux_ultra_hdr_jpeg


def _tmap_hdr_cicp(options: EncodeOptions) -> CICP:
  """tmap 关联的 HDR NCLX：与 Direct 相同（identity matrix→9）。"""
  return get_direct_cicp_for_isobmff(options.gamut, options.curve)


def _tmap_content_light(options: EncodeOptions, buffers: GainMapBuffers) -> ContentLightLevel | None:
  if should_write_clli(options.curve):
    return buffers.content_light
  return None


def encode_gainmap_jpeg_from_buffers(
  buffers: GainMapBuffers,
  output_path: Path,
  options: EncodeOptions,
) -> ContentLightLevel:
  """JPG 容器适配器：mozjpeg × 2 + ISO/MPF 拼装。"""
  icc_base = get_baseline_display_icc(options.gamut)
  gain_icc = get_hdr_icc(options.gamut, options.curve)
  gain_arr = gain_pixels_for_jpeg(buffers.gain, multichannel=buffers.multichannel)

  base_jpeg, gain_jpeg = run_parallel_pair(
    lambda: encode_rgb_jpeg(
      buffers.sdr_pixels,
      quality=options.quality,
      icc=icc_base,
      subsampling=options.jpeg_subsampling,
    ),
    lambda: encode_rgb_jpeg(
      gain_arr,
      quality=options.quality,
      icc=gain_icc,
      subsampling=options.jpeg_subsampling,
    ),
  )

  output_path.write_bytes(
    mux_ultra_hdr_jpeg(base_jpeg, gain_jpeg, buffers.metadata)
  )
  return buffers.content_light


def _encode_isobmff_gainmap_from_buffers(
  buffers: GainMapBuffers,
  output_path: Path,
  options: EncodeOptions,
  *,
  encode_base,
  encode_gain,
  mux_fn,
  attach_icc_fn,
) -> ContentLightLevel:
  """AVIF / HEIF 共用：并行编码 → mux → 可选 baseline ICC。"""
  base_item, gain_item = run_parallel_pair(encode_base, encode_gain)
  data = mux_fn(
    base_item,
    gain_item,
    buffers.metadata,
    hdr_cicp=_tmap_hdr_cicp(options),
    content_light=_tmap_content_light(options, buffers),
    multichannel=buffers.multichannel,
  )
  if options.embed_icc:
    data = attach_icc_fn(data, get_baseline_display_icc(options.gamut))
  output_path.write_bytes(data)
  return buffers.content_light


def encode_gainmap_avif_from_buffers(
  buffers: GainMapBuffers,
  output_path: Path,
  options: EncodeOptions,
) -> ContentLightLevel:
  """AVIF 容器适配器：imagecodecs 编码 + 手动 ISOBMFF mux。

  mono → 增益图 YUV400 + tmap ``is_multichannel=0``；
  color → 增益图 YUV444 + tmap ``is_multichannel=1``（与 HEIF 对齐）。
  """
  baseline_cicp = get_baseline_cicp_for_isobmff(options.gamut)
  multichannel = buffers.multichannel

  return _encode_isobmff_gainmap_from_buffers(
    buffers,
    output_path,
    options,
    encode_base=lambda: encode_avif_rgb(
      buffers.sdr_pixels,
      bit_depth=options.base_bits,
      quality=options.quality,
      cicp=baseline_cicp,
      speed=options.avif_speed,
      numthreads=options.avif_numthreads,
      parallel_jobs=2,
    ),
    encode_gain=lambda: encode_avif_gain(
      buffers.gain,
      bit_depth=options.gainmap_bits,
      quality=options.quality,
      multichannel=multichannel,
      speed=options.avif_speed,
      numthreads=options.avif_numthreads,
      parallel_jobs=2,
    ),
    mux_fn=mux_gainmap_avif,
    attach_icc_fn=attach_icc_to_avif,
  )


def encode_gainmap_heif_from_buffers(
  buffers: GainMapBuffers,
  output_path: Path,
  options: EncodeOptions,
) -> ContentLightLevel:
  """HEIF 容器适配器：pillow-heif 编码 + 手动 ISOBMFF mux。

  基础层对齐 AVIF：NCLX mc=9、不写 ICC（Windows 照片 + ICC/mc=0 易全红）。
  """
  baseline_cicp = get_baseline_cicp_for_isobmff(options.gamut)

  return _encode_isobmff_gainmap_from_buffers(
    buffers,
    output_path,
    options,
    encode_base=lambda: encode_heif_rgb(
      buffers.sdr_pixels,
      bit_depth=options.base_bits,
      quality=options.quality,
      cicp=baseline_cicp,
      icc=None,
    ),
    encode_gain=lambda: encode_heif_gain(
      buffers.gain,
      bit_depth=options.gainmap_bits,
      quality=options.quality,
      multichannel=buffers.multichannel,
    ),
    mux_fn=mux_gainmap_heif,
    attach_icc_fn=attach_icc_to_heif,
  )


def encode_gainmap_jxl_from_buffers(
  buffers: GainMapBuffers,
  output_path: Path,
  options: EncodeOptions,
) -> ContentLightLevel:
  """JXL 容器适配器：基础图容器 + 增益图裸码流 → ``jhgm`` 盒（ISO 18181-2）。"""
  multichannel = buffers.multichannel

  def _encode_base():
    return encode_jxl_base_bytes(
      buffers.sdr_pixels,
      bit_depth=options.base_bits,
      quality=options.quality,
      gamut=options.gamut,
      effort=options.jxl_effort,
    )

  def _encode_gain():
    return encode_jxl_gain_bytes(
      buffers.gain,
      bit_depth=options.gainmap_bits,
      quality=options.quality,
      multichannel=multichannel,
      effort=options.jxl_effort,
    )

  base_jxl, gain_jxl = run_parallel_pair(_encode_base, _encode_gain)
  alt_icc = b""
  from .icc_policy import plan_and_bytes

  _plan, icc = plan_and_bytes(
    OutputFormat.JXL,
    options.gamut,
    options.curve,
    options.hdr_delivery,
    embed_icc=options.embed_icc,
  )
  if icc:
    alt_icc = icc
  output_path.write_bytes(
    mux_jxl_gainmap(base_jxl, gain_jxl, buffers.metadata, alt_icc=alt_icc)
  )
  return buffers.content_light


def encode_gainmap_native_jpeg(
  scrgb,
  output_path: Path,
  options: EncodeOptions,
) -> ContentLightLevel | None:
  """原生 Ultra HDR JPEG（统一核心 + JPG 适配器）。"""
  buffers = prepare_gainmap_buffers(scrgb, options)
  return encode_gainmap_jpeg_from_buffers(buffers, output_path, options)


def encode_gainmap(
  scrgb,
  output_path: Path,
  options: EncodeOptions,
  cicp: CICP,
) -> ContentLightLevel | None:
  if not uses_gainmap(options.hdr_delivery):
    raise ValueError("encode_gainmap 需要 Gain Map 交付模式")

  buffers = prepare_gainmap_buffers(scrgb, options)

  if options.output_format == OutputFormat.JPG:
    return encode_gainmap_jpeg_from_buffers(buffers, output_path, options)
  if options.output_format == OutputFormat.AVIF:
    return encode_gainmap_avif_from_buffers(buffers, output_path, options)
  if options.output_format == OutputFormat.HEIF:
    return encode_gainmap_heif_from_buffers(buffers, output_path, options)
  if options.output_format == OutputFormat.JXL:
    return encode_gainmap_jxl_from_buffers(buffers, output_path, options)

  raise ValueError(f"不支持的 Gain Map 格式: {options.output_format}")
