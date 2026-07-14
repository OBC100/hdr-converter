"""ISOBMFF Gain Map 手动封装（HEIF / AVIF，对齐 libavif tmap + dimg + altr）。"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .cicp import CICP, ContentLightLevel
from .color_metadata import get_gainmap_item_cicp
from .gainmap_math import GainmapMetadata
from .gainmap_tmap import encode_tmap_payload


@dataclass
class ImageItemPayload:
  """从单图 AVIF/HEIF 解析出的图像条目。"""

  width: int
  height: int
  bit_depth: int
  item_type: bytes  # b'av01' / b'hvc1' / ...
  codec_config: bytes  # av1C / hvcC 完整 box（含头）
  colr: bytes | None  # 完整 colr box（单条 nclx 时）
  bitstream: bytes
  pixi: bytes | None = None
  colr_boxes: list[bytes] | None = None  # prof + nclx 等多条 colr


def _u32(n: int) -> bytes:
  return struct.pack(">I", n)


def _u16(n: int) -> bytes:
  return struct.pack(">H", n)


def _box(box_type: bytes, payload: bytes) -> bytes:
  size = 8 + len(payload)
  return _u32(size) + box_type + payload


def _full_box(box_type: bytes, version: int, flags: int, payload: bytes) -> bytes:
  return _box(box_type, bytes([version]) + _u32(flags)[1:] + payload)


def _find_box(data: bytes, box_type: bytes, start: int = 0) -> tuple[int, int] | None:
  """在 data[start:] 中递归查找 box，返回 (绝对 offset, size)。"""
  off = start
  end = len(data)
  while off + 8 <= end:
    size = struct.unpack(">I", data[off : off + 4])[0]
    typ = data[off + 4 : off + 8]
    hdr = 8
    if size == 0:
      size = end - off
    elif size == 1:
      if off + 16 > end:
        return None
      size = struct.unpack(">Q", data[off + 8 : off + 16])[0]
      hdr = 16
    elif size < 8:
      return None
    if typ == box_type:
      return off, size
    containers = {
      b"meta",
      b"iprp",
      b"ipco",
      b"iinf",
      b"iref",
      b"grpl",
      b"moov",
      b"trak",
      b"minf",
      b"stbl",
    }
    if typ in containers:
      child_start = off + hdr
      if typ == b"meta":
        child_start += 4
      found = _find_box(data, box_type, child_start)
      if found is not None:
        return found
    off += size
  return None


def _child_boxes(data: bytes, start: int, end: int) -> list[tuple[bytes, bytes]]:
  """解析子 box，返回 (type, full_box_bytes)。"""
  out: list[tuple[bytes, bytes]] = []
  off = start
  while off + 8 <= end:
    size = struct.unpack(">I", data[off : off + 4])[0]
    typ = data[off + 4 : off + 8]
    hdr = 8
    if size == 0:
      size = end - off
    elif size == 1:
      size = struct.unpack(">Q", data[off + 8 : off + 16])[0]
      hdr = 16
    elif size < 8:
      break
    out.append((typ, data[off : off + size]))
    off += size
  return out


def _parse_iloc(data: bytes) -> list[tuple[int, int, int]]:
  """返回 [(item_id, offset, length), ...]。"""
  loc = _find_box(data, b"iloc")
  if loc is None:
    raise ValueError("缺少 iloc")
  off, size = loc
  body = data[off + 8 : off + size]
  version = body[0]
  if version not in (0, 1, 2):
    raise ValueError(f"不支持的 iloc version {version}")
  offset_size = body[4] >> 4
  length_size = body[4] & 0x0F
  base_offset_size = body[5] >> 4
  if version in (1, 2):
    index_size = body[5] & 0x0F
  else:
    index_size = 0
  pos = 6
  item_count = struct.unpack(">H", body[pos : pos + 2])[0]
  pos += 2
  items: list[tuple[int, int, int]] = []
  for _ in range(item_count):
    item_id = struct.unpack(">H", body[pos : pos + 2])[0]
    pos += 2
    if version in (1, 2):
      construction_method = body[pos] & 0x0F
      pos += 2
    else:
      construction_method = 0
    if construction_method != 0:
      raise ValueError(
        f"不支持的 iloc construction_method={construction_method}"
        "（仅支持 0=文件偏移；本模块只解析 imagecodecs/pillow-heif 产出的单图文件）"
      )
    pos += 2  # data_reference_index
    base_offset = 0
    for _b in range(base_offset_size):
      base_offset = (base_offset << 8) | body[pos]
      pos += 1
    extent_count = struct.unpack(">H", body[pos : pos + 2])[0]
    pos += 2
    for _ in range(extent_count):
      if version in (1, 2) and index_size:
        pos += index_size
      extent_offset = 0
      for _b in range(offset_size):
        extent_offset = (extent_offset << 8) | body[pos]
        pos += 1
      extent_length = 0
      for _b in range(length_size):
        extent_length = (extent_length << 8) | body[pos]
        pos += 1
      items.append((item_id, base_offset + extent_offset, extent_length))
  return items


def _parse_infe(data: bytes) -> tuple[int, bytes]:
  """返回 (item_id, item_type)。"""
  iinf = _find_box(data, b"iinf")
  if iinf is None:
    raise ValueError("缺少 iinf")
  iinf_off, iinf_size = iinf
  body = data[iinf_off + 8 : iinf_off + iinf_size]
  # iinf fullbox: version(1) + flags(3) + entry_count(2) + infe...
  entry_count = struct.unpack(">H", body[4:6])[0]
  pos = 6
  for _ in range(entry_count):
    if pos + 8 > len(body):
      break
    size = struct.unpack(">I", body[pos : pos + 4])[0]
    typ = body[pos + 4 : pos + 8]
    if typ != b"infe":
      pos += size
      continue
    raw = body[pos : pos + size]
    version = raw[8]
    if version >= 2:
      item_id = struct.unpack(">H", raw[12:14])[0]
      item_type = raw[16:20]
    else:
      item_id = struct.unpack(">H", raw[12:14])[0]
      item_type = raw[14:18]
    return item_id, item_type
  raise ValueError("缺少 infe")


def _parse_ipco_properties(data: bytes) -> dict[str, bytes]:
  iprp = _find_box(data, b"iprp")
  if iprp is None:
    raise ValueError("缺少 iprp")
  iprp_off, iprp_size = iprp
  ipco = _find_box(data, b"ipco", iprp_off + 8)
  if ipco is None:
    raise ValueError("缺少 ipco")
  ipco_off, ipco_size = ipco
  base = ipco_off + 8
  props: dict[str, bytes] = {}
  for typ, raw in _child_boxes(data, base, ipco_off + ipco_size):
    props[typ.decode("latin1")] = raw
  return props


def parse_single_image_item(data: bytes) -> ImageItemPayload:
  """解析 imagecodecs / pillow-heif 产出的单图 AVIF/HEIF。"""
  mdat = _find_box(data, b"mdat")
  if mdat is None:
    raise ValueError("缺少 mdat")
  mdat_off, mdat_size = mdat
  mdat_payload_off = mdat_off + 8

  iloc_items = _parse_iloc(data)
  if len(iloc_items) != 1:
    raise ValueError(f"期望单条目 iloc，收到 {len(iloc_items)}")
  _, extent_off, extent_len = iloc_items[0]
  bitstream = data[extent_off : extent_off + extent_len]

  _, item_type = _parse_infe(data)
  props = _parse_ipco_properties(data)

  ispe = props.get("ispe")
  if ispe is None:
    raise ValueError("缺少 ispe")
  width = struct.unpack(">I", ispe[12:16])[0]
  height = struct.unpack(">I", ispe[16:20])[0]

  pixi = props.get("pixi")
  bit_depth = 8
  if pixi is not None and len(pixi) >= 14:
    # FullBox: size(4)+type(4)+ver/flags(4)+num_channels(1)+bits...
    num_ch = pixi[12]
    if num_ch >= 1 and len(pixi) >= 13 + num_ch:
      bit_depth = pixi[13]
    elif len(pixi) > 13:
      bit_depth = pixi[13]

  codec_config = props.get("av1C") or props.get("hvcC")
  if codec_config is None:
    raise ValueError("缺少 av1C/hvcC")

  return ImageItemPayload(
    width=width,
    height=height,
    bit_depth=bit_depth,
    item_type=item_type,
    codec_config=codec_config,
    colr=props.get("colr"),
    bitstream=bitstream,
    pixi=pixi,
  )


def _ispe_box(width: int, height: int) -> bytes:
  return _full_box(b"ispe", 0, 0, _u32(width) + _u32(height))


def _pixi_box(bit_depth: int, num_channels: int = 3) -> bytes:
  return _full_box(b"pixi", 0, 0, bytes([num_channels]) + bytes([bit_depth] * num_channels))


def _nclx_colr_box(cicp: CICP) -> bytes:
  payload = (
    b"nclx"
    + _u16(cicp.color_primaries)
    + _u16(cicp.transfer_characteristics)
    + _u16(cicp.matrix_coefficients)
    + bytes([0x80 if cicp.full_range else 0x00])
  )
  return _box(b"colr", payload)


def _clli_box(cll: ContentLightLevel) -> bytes:
  return _box(b"clli", _u16(cll.max_cll) + _u16(cll.max_fall))


def _infe_box(
  item_id: int,
  item_type: bytes,
  *,
  hidden: bool = False,
  item_name: str = "",
) -> bytes:
  flags = 0x000001 if hidden else 0
  # version 2: item_ID + protection_index + item_type + item_name (null-terminated)
  payload = _u16(item_id) + b"\x00\x00" + item_type + item_name.encode("utf-8") + b"\x00"
  return _full_box(b"infe", 2, flags, payload)


def _iloc_box(items: list[tuple[int, int, int]]) -> bytes:
  """items: (item_id, offset, length) — offset 为文件绝对偏移。

  data_reference_index=0 表示数据在本文件内（无需 dinf/dref），对齐 libavif。
  """
  body = bytearray()
  body.append(0x44)  # offset_size=4, length_size=4
  body.append(0x00)  # base_offset_size=0, index_size=0
  body.extend(_u16(len(items)))
  for item_id, offset, length in items:
    body.extend(_u16(item_id))
    body.extend(_u16(0))  # data_reference_index（0 = 本文件）
    body.extend(_u16(1))  # extent_count
    body.extend(_u32(offset))
    body.extend(_u32(length))
  return _full_box(b"iloc", 0, 0, bytes(body))


def _rebuild_isobmff_with_iprp(
  data: bytes,
  *,
  new_iprp: bytes,
) -> bytes:
  """用新的 iprp 重建单图 ISOBMFF，并按最终 meta 长度重写 iloc（两遍，避免盒子变长导致偏移错误）。"""
  ftyp = _find_box(data, b"ftyp")
  meta = _find_box(data, b"meta")
  mdat = _find_box(data, b"mdat")
  if ftyp is None or meta is None or mdat is None:
    return data

  ftyp_off, ftyp_size = ftyp
  meta_off, meta_size = meta
  mdat_off, mdat_size = mdat
  ftyp_bytes = data[ftyp_off : ftyp_off + ftyp_size]
  mdat_box = data[mdat_off : mdat_off + mdat_size]
  meta_children = _child_boxes(data, meta_off + 12, meta_off + meta_size)

  iloc_items = _parse_iloc(data)
  orig_mdat_payload = mdat_off + 8
  lengths = [(iid, length) for iid, _off, length in iloc_items]
  rel_offs = [off - orig_mdat_payload for _iid, off, _length in iloc_items]

  def _build_meta(iloc_box: bytes) -> bytes:
    rebuilt = bytearray()
    for typ, raw in meta_children:
      if typ == b"iprp":
        rebuilt.extend(new_iprp)
      elif typ == b"iloc":
        rebuilt.extend(iloc_box)
      else:
        rebuilt.extend(raw)
    return _full_box(b"meta", 0, 0, bytes(rebuilt))

  # 第一遍：占位 iloc，确定 meta 长度
  placeholder = _iloc_box([(iid, 0, length) for iid, length in lengths])
  meta1 = _build_meta(placeholder)
  mdat_start = len(ftyp_bytes) + len(meta1)
  items = [
    (iid, mdat_start + 8 + rel, length)
    for (iid, length), rel in zip(lengths, rel_offs)
  ]
  meta2 = _build_meta(_iloc_box(items))
  if len(meta2) != len(meta1):
    mdat_start = len(ftyp_bytes) + len(meta2)
    items = [
      (iid, mdat_start + 8 + rel, length)
      for (iid, length), rel in zip(lengths, rel_offs)
    ]
    meta2 = _build_meta(_iloc_box(items))
  return ftyp_bytes + meta2 + mdat_box


def _iinf_box(infe_boxes: list[bytes]) -> bytes:
  payload = _u16(len(infe_boxes))
  for infe in infe_boxes:
    payload += infe
  return _full_box(b"iinf", 0, 0, payload)


def _ipma_box(associations: list[tuple[int, list[int]]]) -> bytes:
  """(item_id, [property_index...])，property_index 从 1 起；≥0x80 表示 essential。"""
  body = bytearray()
  body.extend(_u32(len(associations)))
  for item_id, props in associations:
    body.extend(_u16(item_id))
    body.append(len(props))
    for p in props:
      body.append(p)
  return _full_box(b"ipma", 0, 0, bytes(body))


def _iref_dimg(from_item_id: int, to_item_ids: list[int]) -> bytes:
  """iref FullBox 内含 dimg 子 box（对齐 libavif）。"""
  dimg_body = _u16(from_item_id) + _u16(len(to_item_ids))
  for tid in to_item_ids:
    dimg_body += _u16(tid)
  return _full_box(b"iref", 0, 0, _box(b"dimg", dimg_body))


def _grpl_altr(group_id: int, item_ids: list[int]) -> bytes:
  """grpl 内含 altr FullBox（entity_id 为 uint32）。"""
  altr_body = _u32(group_id) + _u32(len(item_ids))
  for iid in item_ids:
    altr_body += _u32(iid)
  return _box(b"grpl", _full_box(b"altr", 0, 0, altr_body))


def mux_gainmap_isobmff(
  base: ImageItemPayload,
  gain: ImageItemPayload,
  metadata: GainmapMetadata,
  *,
  major_brand: bytes,
  compatible_brands: list[bytes],
  hdr_cicp: CICP | None = None,
  content_light: ContentLightLevel | None = None,
  force_multichannel_tmap: bool = False,
) -> bytes:
  """
  将基础图 + 增益图 + tmap 元数据合成为 Gain Map 容器。

  结构（对齐 libavif）：
  - item 1: 基础 SDR 图（pitm）
  - item 2: tmap 元数据
  - item 3: 增益图（hidden）
  - iref dimg: tmap → [base, gain]
  - grpl altr: [tmap, base]
  """
  tmap_payload = encode_tmap_payload(
    metadata, force_multichannel=force_multichannel_tmap
  )

  base_id, tmap_id, gain_id = 1, 2, 3

  # --- 组装 ipco 属性 ---
  ipco_children: list[bytes] = []
  ipco_children.append(_ispe_box(base.width, base.height))
  if base.pixi is not None:
    ipco_children.append(base.pixi)
  else:
    ipco_children.append(_pixi_box(base.bit_depth, 3))
  ipco_children.append(base.codec_config)
  base_props = [1, 2, 3 | 0x80]
  if base.colr_boxes:
    for colr_box in base.colr_boxes:
      ipco_children.append(colr_box)
      base_props.append(len(ipco_children))
  elif base.colr is not None:
    ipco_children.append(base.colr)
    base_props.append(len(ipco_children))

  gain_ispe_idx = len(ipco_children) + 1
  ipco_children.append(_ispe_box(gain.width, gain.height))
  gain_pixi_idx = len(ipco_children) + 1
  if gain.pixi is not None:
    ipco_children.append(gain.pixi)
  else:
    ipco_children.append(_pixi_box(gain.bit_depth, 1))
  gain_codec_idx = len(ipco_children) + 1
  ipco_children.append(gain.codec_config)
  gain_colr_idx = len(ipco_children) + 1
  if gain.colr is not None:
    ipco_children.append(gain.colr)
  else:
    ipco_children.append(_nclx_colr_box(get_gainmap_item_cicp()))

  # tmap：对齐 libavif，关联 ispe + pixi（与基础层同尺寸/位深）+ HDR colr/clli
  tmap_ispe_idx = len(ipco_children) + 1
  ipco_children.append(_ispe_box(base.width, base.height))
  tmap_pixi_idx = len(ipco_children) + 1
  if base.pixi is not None:
    ipco_children.append(base.pixi)
  else:
    ipco_children.append(_pixi_box(base.bit_depth, 3))
  tmap_props = [tmap_ispe_idx, tmap_pixi_idx]
  if hdr_cicp is not None:
    ipco_children.append(_nclx_colr_box(hdr_cicp))
    tmap_props.append(len(ipco_children))
  if content_light is not None and (content_light.max_cll or content_light.max_fall):
    ipco_children.append(_clli_box(content_light))
    tmap_props.append(len(ipco_children))

  ipco = _box(b"ipco", b"".join(ipco_children))
  gain_props = [gain_ispe_idx, gain_pixi_idx, gain_codec_idx | 0x80, gain_colr_idx]

  ipma = _ipma_box(
    [
      (base_id, base_props),
      (tmap_id, tmap_props),
      (gain_id, gain_props),
    ]
  )
  iprp = _box(b"iprp", ipco + ipma)

  # --- mdat 布局：base | gain | tmap ---
  ftyp_payload = major_brand + _u32(0) + major_brand
  for b in compatible_brands:
    if b != major_brand:
      ftyp_payload += b

  # 先占位，稍后回填 iloc offset
  header_estimate = 512
  base_off = header_estimate
  gain_off = base_off + len(base.bitstream)
  tmap_off = gain_off + len(gain.bitstream)
  mdat_payload = base.bitstream + gain.bitstream + tmap_payload

  infe_boxes = [
    _infe_box(base_id, base.item_type, item_name="Color"),
    _infe_box(tmap_id, b"tmap", item_name="GMap"),
    _infe_box(gain_id, gain.item_type, hidden=True, item_name="GMap"),
  ]
  iinf = _iinf_box(infe_boxes)
  iloc = _iloc_box(
    [
      (base_id, base_off, len(base.bitstream)),
      (tmap_id, tmap_off, len(tmap_payload)),
      (gain_id, gain_off, len(gain.bitstream)),
    ]
  )
  pitm = _full_box(b"pitm", 0, 0, _u16(base_id))
  # HandlerBox: pre_defined(4) + handler_type('pict') + reserved(12) + name
  hdlr = _full_box(
    b"hdlr",
    0,
    0,
    b"\x00" * 4 + b"pict" + b"\x00" * 12 + b"pict\x00",
  )
  iref = _iref_dimg(tmap_id, [base_id, gain_id])
  grpl = _grpl_altr(gain_id + 1, [tmap_id, base_id])

  meta_inner = hdlr + pitm + iloc + iinf + iref + iprp + grpl
  meta = _full_box(b"meta", 0, 0, meta_inner)
  ftyp = _box(b"ftyp", ftyp_payload)
  mdat = _box(b"mdat", mdat_payload)

  # 计算真实偏移并重建 iloc
  prefix = ftyp + meta
  base_off = len(prefix) + 8
  gain_off = base_off + len(base.bitstream)
  tmap_off = gain_off + len(gain.bitstream)
  iloc = _iloc_box(
    [
      (base_id, base_off, len(base.bitstream)),
      (tmap_id, tmap_off, len(tmap_payload)),
      (gain_id, gain_off, len(gain.bitstream)),
    ]
  )
  meta_inner = hdlr + pitm + iloc + iinf + iref + iprp + grpl
  meta = _full_box(b"meta", 0, 0, meta_inner)
  return ftyp + meta + mdat


def mux_gainmap_avif(
  base: ImageItemPayload,
  gain: ImageItemPayload,
  metadata: GainmapMetadata,
  *,
  hdr_cicp: CICP,
  content_light: ContentLightLevel,
  multichannel: bool = False,
) -> bytes:
  """AVIF Gain Map mux。

  ``multichannel=True``（color）时 tmap 写 ``is_multichannel=1``；
  ``False``（mono）时写单通道元数据，增益图像素为 YUV400。
  """
  # 对齐 libavif：MA1A（基础层 YUV444 剖面）+ tmap
  brands = [b"avif", b"mif1", b"miaf", b"MA1A", b"tmap"]
  return mux_gainmap_isobmff(
    base,
    gain,
    metadata,
    major_brand=b"avif",
    compatible_brands=brands,
    hdr_cicp=hdr_cicp,
    content_light=content_light,
    force_multichannel_tmap=multichannel,
  )


def mux_gainmap_heif(
  base: ImageItemPayload,
  gain: ImageItemPayload,
  metadata: GainmapMetadata,
  *,
  hdr_cicp: CICP | None = None,
  content_light: ContentLightLevel | None = None,
  multichannel: bool = False,
) -> bytes:
  brands = [b"heic", b"mif1", b"miaf", b"tmap"]
  return mux_gainmap_isobmff(
    base,
    gain,
    metadata,
    major_brand=b"heic",
    compatible_brands=brands,
    hdr_cicp=hdr_cicp,
    content_light=content_light,
    force_multichannel_tmap=multichannel,
  )


def _prof_colr_box(icc: bytes) -> bytes:
  return _box(b"colr", b"prof" + icc)


def _attach_primary_ipco_boxes(data: bytes, new_boxes: list[bytes]) -> bytes:
  """向单图 AVIF/HEIF 主条目追加 ipco 属性，并修正 iloc 偏移。"""
  if not new_boxes:
    return data

  ftyp = _find_box(data, b"ftyp")
  meta = _find_box(data, b"meta")
  mdat = _find_box(data, b"mdat")
  if ftyp is None or meta is None or mdat is None:
    return data

  meta_off, meta_size = meta
  mdat_off, _ = mdat
  meta_body_start = meta_off + 12
  meta_end = meta_off + meta_size

  iprp = _find_box(data, b"iprp", meta_body_start)
  if iprp is None:
    return data
  iprp_off, iprp_size = iprp
  ipco = _find_box(data, b"ipco", iprp_off + 8)
  ipma = _find_box(data, b"ipma", iprp_off + 8)
  if ipco is None or ipma is None:
    return data
  ipco_off, ipco_size = ipco
  ipma_off, ipma_size = ipma

  existing = _child_boxes(data, ipco_off + 8, ipco_off + ipco_size)
  n_props = len(existing)
  new_indices = list(range(n_props + 1, n_props + 1 + len(new_boxes)))

  ipma_raw = data[ipma_off : ipma_off + ipma_size]
  ipma_body = ipma_raw[8:]
  version = ipma_body[0]
  flags = ipma_body[1:4]
  pos = 4
  if version < 1:
    entry_count_u16 = struct.unpack(">H", ipma_body[pos : pos + 2])[0]
    if entry_count_u16 == 0 and len(ipma_body) >= pos + 6:
      entry_count = struct.unpack(">I", ipma_body[pos : pos + 4])[0]
      pos += 4
      entry_count_is_u32 = True
    else:
      entry_count = entry_count_u16
      pos += 2
      entry_count_is_u32 = False
  else:
    entry_count = struct.unpack(">I", ipma_body[pos : pos + 4])[0]
    pos += 4
    entry_count_is_u32 = True

  associations: list[tuple[int, list[int]]] = []
  for _ in range(entry_count):
    item_id = struct.unpack(">H", ipma_body[pos : pos + 2])[0]
    pos += 2
    assoc_count = ipma_body[pos]
    pos += 1
    props = list(ipma_body[pos : pos + assoc_count])
    pos += assoc_count
    associations.append((item_id, props))

  if not associations:
    return data

  primary_id, primary_props = associations[0]
  have = {p & 0x7F for p in primary_props}
  for idx in new_indices:
    if idx not in have:
      primary_props.append(idx)
  associations[0] = (primary_id, primary_props)

  new_ipma_payload = bytearray()
  if entry_count_is_u32:
    new_ipma_payload.extend(_u32(len(associations)))
  else:
    new_ipma_payload.extend(_u16(len(associations)))
  for item_id, props in associations:
    new_ipma_payload.extend(_u16(item_id))
    new_ipma_payload.append(len(props))
    new_ipma_payload.extend(bytes(props))
  new_ipma = _full_box(
    b"ipma", version, int.from_bytes(flags, "big"), bytes(new_ipma_payload)
  )

  new_ipco = _box(
    b"ipco", data[ipco_off + 8 : ipco_off + ipco_size] + b"".join(new_boxes)
  )
  iprp_children = _child_boxes(data, iprp_off + 8, iprp_off + iprp_size)
  rebuilt_iprp = bytearray()
  for typ, raw in iprp_children:
    if typ == b"ipco":
      rebuilt_iprp.extend(new_ipco)
    elif typ == b"ipma":
      rebuilt_iprp.extend(new_ipma)
    else:
      rebuilt_iprp.extend(raw)
  return _rebuild_isobmff_with_iprp(data, new_iprp=_box(b"iprp", bytes(rebuilt_iprp)))


def attach_clli_to_avif(data: bytes, content_light: ContentLightLevel) -> bytes:
  """向单图 AVIF/HEIF 主条目追加 ``clli``（对齐 PNG cLLi）。已存在则跳过。"""
  if not (content_light.max_cll or content_light.max_fall):
    return data
  ipco = _find_box(data, b"ipco")
  if ipco is not None:
    for typ, _ in _child_boxes(data, ipco[0] + 8, ipco[0] + ipco[1]):
      if typ == b"clli":
        return data
  return _attach_primary_ipco_boxes(data, [_clli_box(content_light)])


attach_clli_to_heif = attach_clli_to_avif


def fix_isobmff_pixi(
  data: bytes,
  *,
  bit_depth: int,
  num_channels: int,
) -> bytes:
  """修正 pillow-heif 常把 RGB 的 pixi 写成 1 通道的问题，并同步 iloc。"""
  new_pixi = _pixi_box(bit_depth, num_channels)

  meta = _find_box(data, b"meta")
  if meta is None:
    return data
  meta_off, meta_size = meta
  meta_body_start = meta_off + 12

  iprp = _find_box(data, b"iprp", meta_body_start)
  if iprp is None:
    return data
  iprp_off, iprp_size = iprp
  ipco = _find_box(data, b"ipco", iprp_off + 8)
  if ipco is None:
    return data
  ipco_off, ipco_size = ipco

  children = _child_boxes(data, ipco_off + 8, ipco_off + ipco_size)
  rebuilt = bytearray()
  replaced = False
  for typ, raw in children:
    if typ == b"pixi":
      if (
        len(raw) >= 14
        and raw[12] == num_channels
        and len(raw) >= 13 + num_channels
        and all(raw[13 + i] == bit_depth for i in range(num_channels))
      ):
        return data
      rebuilt.extend(new_pixi)
      replaced = True
    else:
      rebuilt.extend(raw)
  if not replaced:
    rebuilt.extend(new_pixi)

  new_ipco = _box(b"ipco", bytes(rebuilt))
  iprp_children = _child_boxes(data, iprp_off + 8, iprp_off + iprp_size)
  rebuilt_iprp = bytearray()
  for typ, raw in iprp_children:
    if typ == b"ipco":
      rebuilt_iprp.extend(new_ipco)
    else:
      rebuilt_iprp.extend(raw)
  return _rebuild_isobmff_with_iprp(data, new_iprp=_box(b"iprp", bytes(rebuilt_iprp)))


def attach_icc_to_avif(data: bytes, icc: bytes) -> bytes:
  """向单图 AVIF 主条目追加 ``colr``/``prof`` ICC。已有 prof 则跳过。"""
  if not icc:
    return data
  ipco = _find_box(data, b"ipco")
  if ipco is not None:
    for typ, raw in _child_boxes(data, ipco[0] + 8, ipco[0] + ipco[1]):
      if typ == b"colr" and len(raw) >= 12 and raw[8:12] == b"prof":
        return data
  return _attach_primary_ipco_boxes(data, [_prof_colr_box(icc)])


def attach_icc_to_heif(data: bytes, icc: bytes) -> bytes:
  """向单图 HEIF 主条目追加 ``colr``/``prof`` ICC（与 AVIF 同结构）。"""
  return attach_icc_to_avif(data, icc)


def build_single_image_isobmff(
  item: ImageItemPayload,
  *,
  brands: list[bytes],
) -> bytes:
  """由 ImageItemPayload 重建可解码的单图 ISOBMFF（供 Gain Map demux）。"""
  item_id = 1
  compatible = b"".join(brands[1:]) if len(brands) > 1 else b""
  ftyp = _box(b"ftyp", brands[0] + _u32(0) + compatible)
  hdlr = _full_box(b"hdlr", 0, 0, b"\x00" * 4 + b"pict" + b"\x00" * 12 + b"\x00")
  pitm = _full_box(b"pitm", 0, 0, _u16(item_id))
  infe = _infe_box(item_id, item.item_type)
  iinf = _iinf_box([infe])

  ipco_children: list[bytes] = [
    _ispe_box(item.width, item.height),
    item.pixi or _pixi_box(item.bit_depth),
    item.codec_config,
  ]
  if item.colr is not None:
    ipco_children.append(item.colr)
  ipco = _box(b"ipco", b"".join(ipco_children))
  props = list(range(1, len(ipco_children) + 1))
  props[2] = props[2] | 0x80
  ipma = _ipma_box([(item_id, props)])
  iprp = _box(b"iprp", ipco + ipma)

  iloc0 = _iloc_box([(item_id, 0, len(item.bitstream))])
  meta0 = _full_box(b"meta", 0, 0, hdlr + pitm + iloc0 + iinf + iprp)
  data_off = len(ftyp) + len(meta0) + 8
  iloc = _iloc_box([(item_id, data_off, len(item.bitstream))])
  meta = _full_box(b"meta", 0, 0, hdlr + pitm + iloc + iinf + iprp)
  data_off = len(ftyp) + len(meta) + 8
  iloc = _iloc_box([(item_id, data_off, len(item.bitstream))])
  meta = _full_box(b"meta", 0, 0, hdlr + pitm + iloc + iinf + iprp)
  return ftyp + meta + _box(b"mdat", item.bitstream)


def extract_gainmap_items(data: bytes) -> tuple[ImageItemPayload, ImageItemPayload, bytes] | None:
  """从 Gain Map HEIF/AVIF 提取 (base, gain, tmap_payload)。"""
  items: dict[int, bytes] = {}
  iinf = _find_box(data, b"iinf")
  if iinf is None:
    return None
  body = data[iinf[0] + 8 : iinf[0] + iinf[1]]
  entry_count = struct.unpack(">H", body[4:6])[0]
  pos = 6
  for _ in range(entry_count):
    size = struct.unpack(">I", body[pos : pos + 4])[0]
    raw = body[pos : pos + size]
    version = raw[8]
    if version >= 2:
      item_id = struct.unpack(">H", raw[12:14])[0]
      item_type = raw[16:20]
    else:
      item_id = struct.unpack(">H", raw[12:14])[0]
      item_type = raw[14:18]
    items[item_id] = item_type
    pos += size

  dimg: dict[int, list[int]] = {}
  iref = _find_box(data, b"iref")
  if iref is None:
    return None
  off = iref[0] + 12
  end = iref[0] + iref[1]
  while off + 8 <= end:
    size = struct.unpack(">I", data[off : off + 4])[0]
    typ = data[off + 4 : off + 8]
    if typ == b"dimg" and size >= 12:
      b = data[off + 8 : off + size]
      from_id = struct.unpack(">H", b[0:2])[0]
      count = struct.unpack(">H", b[2:4])[0]
      dimg[from_id] = [
        struct.unpack(">H", b[4 + 2 * i : 6 + 2 * i])[0] for i in range(count)
      ]
    off += size

  tmap_id = next((i for i, t in items.items() if t == b"tmap"), None)
  if tmap_id is None or tmap_id not in dimg or len(dimg[tmap_id]) < 2:
    return None
  base_id, gain_id = dimg[tmap_id][0], dimg[tmap_id][1]
  iloc = {iid: (o, ln) for iid, o, ln in _parse_iloc(data)}
  if tmap_id not in iloc or base_id not in iloc or gain_id not in iloc:
    return None

  iprp = _find_box(data, b"iprp")
  if iprp is None:
    return None
  ipco = _find_box(data, b"ipco", iprp[0] + 8)
  if ipco is None:
    return None
  prop_list = [raw for _, raw in _child_boxes(data, ipco[0] + 8, ipco[0] + ipco[1])]

  ipma = _find_box(data, b"ipma", iprp[0] + 8)
  if ipma is None:
    return None
  ipma_body = data[ipma[0] + 8 : ipma[0] + ipma[1]]
  version = ipma_body[0]
  pos = 4
  # 本项目 _ipma_box 在 version=0 时仍写 u32 entry_count（对齐 libavif 常见写法）
  if version < 1:
    entry_count_u16 = struct.unpack(">H", ipma_body[pos : pos + 2])[0]
    if entry_count_u16 == 0 and len(ipma_body) >= pos + 6:
      entry_count = struct.unpack(">I", ipma_body[pos : pos + 4])[0]
      pos += 4
    else:
      entry_count = entry_count_u16
      pos += 2
  else:
    entry_count = struct.unpack(">I", ipma_body[pos : pos + 4])[0]
    pos += 4
  assoc: dict[int, list[int]] = {}
  for _ in range(entry_count):
    if version < 1:
      item_id = struct.unpack(">H", ipma_body[pos : pos + 2])[0]
      pos += 2
    else:
      item_id = struct.unpack(">I", ipma_body[pos : pos + 4])[0]
      pos += 4
    assoc_count = ipma_body[pos]
    pos += 1
    idxs = []
    for _a in range(assoc_count):
      idxs.append(ipma_body[pos] & 0x7F)
      pos += 1
    assoc[item_id] = idxs

  def _item_payload(item_id: int) -> ImageItemPayload:
    o, ln = iloc[item_id]
    bitstream = data[o : o + ln]
    idxs = assoc.get(item_id, [])
    props = {}
    for idx in idxs:
      if 1 <= idx <= len(prop_list):
        raw = prop_list[idx - 1]
        typ = raw[4:8].decode("latin1")
        props[typ] = raw
    ispe = props.get("ispe")
    if ispe is None:
      raise ValueError("缺少 ispe")
    width = struct.unpack(">I", ispe[12:16])[0]
    height = struct.unpack(">I", ispe[16:20])[0]
    pixi = props.get("pixi")
    bit_depth = 8
    if pixi is not None and len(pixi) >= 14:
      bit_depth = pixi[13]
    codec = props.get("av1C") or props.get("hvcC")
    if codec is None:
      raise ValueError("缺少 codec config")
    return ImageItemPayload(
      width=width,
      height=height,
      bit_depth=bit_depth,
      item_type=items[item_id],
      codec_config=codec,
      colr=props.get("colr"),
      bitstream=bitstream,
      pixi=pixi,
    )

  t_off, t_len = iloc[tmap_id]
  return _item_payload(base_id), _item_payload(gain_id), data[t_off : t_off + t_len]

