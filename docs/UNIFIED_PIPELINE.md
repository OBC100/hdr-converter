# 统一导出管线

> 状态：**输出侧已验收**（2026-07）— Gain Map 统一核心 + 手动容器封装已落地；全格式导出验收通过。输入侧扩展见 [MULTI_FORMAT_PLAN.md](MULTI_FORMAT_PLAN.md)。

本文档描述 JXR → PNG / HEIF / AVIF / JPG / JXL 的**格式无关**色彩管线与**容器适配器**分层，对齐 ISO 21496-1（HDR Gain Map）、ISOBMFF（HEIF/AVIF）与 ISO/IEC 18181-2（JXL ``jhgm``）。

相关文档：[PROJECT.md](PROJECT.md) §4.5 / §6.4、[STATUS.md](STATUS.md)。

---

## 1. 三层架构

```
┌─────────────────────────────────────────────────────────────────┐
│ L0 源缓冲（格式无关）                                              │
│   load_jxr_raw() → float32 scRGB RGBA (H×W×4)                   │
└────────────────────────────┬────────────────────────────────────┘
                             │ 同一份 raw
         ┌───────────────────┴───────────────────┐
         ▼                                       ▼
┌─────────────────────┐               ┌─────────────────────┐
│ L1a Direct          │               │ L1b Gain Map        │
│ convert_colorspace  │               │ prepare_gainmap_    │
│ → PipelineResult    │               │   buffers()         │
│                     │               │ → GainMapBuffers    │
└─────────┬───────────┘               └─────────┬───────────┘
          │                                     │
          ▼                                     ▼
┌─────────────────────┐               ┌─────────────────────┐
│ L2 Direct 适配器     │               │ L2 Gain Map 适配器   │
│ PNG / HEIF / AVIF   │               │ JPG mux / ISOBMFF   │
│ （色彩元数据同 PNG）  │               │ tmap + dimg + altr  │
└─────────────────────┘               └─────────────────────┘
```

**不依赖 libultrahdr**：JPG / HEIF / AVIF Gain Map 均在 Python 侧完成数学与容器拼装。

---

## 2. Direct 交付（HEIF / AVIF / JXL 对齐 PNG）

```
raw → convert_colorspace → PipelineResult → PNGEncoder | HeifEncoder | AvifEncoder | JxlEncoder
```

待办：抽出 `build_direct_payload`，PNG / HEIF / AVIF / JXL 共用元数据组装（阶段 2）。

**JXL Direct（ISO/IEC 18181）**：`imagecodecs.jpegxl_encode`，RGB + CICP `primaries`/`transfer`（非 XYB）；ISOBMFF 容器；位深 8–16。

**JXL Gain Map**：L1b 同 HEIF/AVIF；L2 为 SDR 基础图容器 + 增益图裸码流，经 `jxl_gainmap.mux_jxl_gainmap` 写入 ``jhgm`` 盒（ISO 21496 元数据与 JPG APP2 同形，无 URN）。

---

## 3. Gain Map 交付

### 3.1 统一中间表示 `GainMapBuffers`

定义于 `core/gainmap_core.py`：

| 字段 | 语义 |
|------|------|
| `hdr_linear` | 目标色域 HDR 线性，1.0 = 10000 nits |
| `sdr_linear` | SDR 显示线性 |
| `sdr_pixels` | 基础图（`base_bits` + sRGB OETF） |
| `gain` | 降采样增益图 uint8 |
| `metadata` | `GainmapMetadata` |
| `content_light` | MaxCLL / MaxFALL |

### 3.2 核心算法（只写一次）

```
scrgb → prepare_gainmap_linear() → compute_gainmap_with_peak()
      → sdr_linear_to_base_pixels(base_bits) → GainMapBuffers
```

### 3.3 容器适配器（全部手动）

| 格式 | 模块 | 基础图编码 | 增益图编码 | 元数据 / 纽带 |
|------|------|------------|------------|---------------|
| **JPG** | `uhdr_jpeg_mux` | mozjpeg 8-bit | mozjpeg 8-bit | APP2 ISO 21496 + MPF |
| **AVIF** | `isobmff_gainmap` | `imagecodecs.avif_encode` | `avif_encode` **YUV400（mono）/ YUV444（color）** | `tmap` + `iref` dimg + `grpl` altr |
| **HEIF** | `isobmff_gainmap` | `pillow-heif` HEVC | HEVC 灰度/RGB | 同上 |
| **JXL** | `jxl_gainmap` | `jpegxl_encode` 容器 | `jpegxl_encode` 裸码流 | ``jhgm`` 盒（ISO 21496，无 URN） |

HEIF/AVIF 结构对齐 **libavif** 实验性 Gain Map 写法：

- **item 1**：SDR 基础图（`pitm`）
- **item 2**：`tmap` 元数据（ISO 21496-1 C.2.2，见 `gainmap_tmap.py`）
- **item 3**：增益图（hidden）
- **iref `dimg`**：tmap → [base, gain]
- **grpl `altr`**：[tmap, base]

JPG 规范锁定 8-bit；HEIF/AVIF **基础图**使用 UI 的 `base_bits`（8/10/12）；**增益图固定 8-bit**（与 JPG 一致）。

**AVIF / HEIF Direct**：与各自 Gain Map 基础层共用编码器（无 tone map；像素经 PQ/HLG/Linear OETF）。NCLX 用 Direct CICP（identity matrix→9）；**不写 HDR ICC**（Windows 照片兼容）；可选附加 `clli`。

---

## 4. 实施里程碑

| 阶段 | 内容 | 状态 |
|------|------|------|
| **1** | `GainMapBuffers` + 统一 L1b | ✅ |
| **3b** | HEIF/AVIF 手动 ISOBMFF mux（移除 libultrahdr） | ✅ |
| **2** | Direct `build_direct_payload` | 待办（非阻塞；各编码器已各自可用） |
| **4** | 全格式输出验收 | ✅（2026-07） |

---

## 5. 代码索引

| 模块 | 职责 |
|------|------|
| `gainmap_core.py` | L1b 中间表示 |
| `gainmap_tmap.py` | `tmap` 载荷（ISO 21496 C.2.2） |
| `gainmap_container_encode.py` | 单图 AVIF/HEIF 编码 |
| `isobmff_gainmap.py` | 解析单图 + Gain Map mux |
| `gainmap_pipeline.py` | 编排与格式分发 |
| `uhdr_jpeg_mux.py` | Ultra HDR JPEG |
| `gainmap_math.py` | 增益图数学 + JPEG 用 ISO 元数据 |
