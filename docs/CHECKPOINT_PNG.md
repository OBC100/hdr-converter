# 检查点：PNG HDR 实现（2026-06）

本文档记录 **PNG 输出**定稿状态。**正式验收以 PQ 为准**；HLG / Linear 为实验性。PQ 历程详见 [CHECKPOINT_PQ.md](CHECKPOINT_PQ.md)。

---

## 1. 状态

| 模块 | 状态 |
|------|------|
| 色彩管线 PQ | **完成（正式验收）** |
| 色彩管线 HLG / Linear | **实验性**（未同等验收） |
| PNG 元数据（cICP + iCCP + cLLi） | **完成** |
| oxipng 压缩（level 2） | **完成** |
| JPEG Ultra HDR（PQ） | **完成** — [CHECKPOINT_JPEG_PQ.md](CHECKPOINT_JPEG_PQ.md) |
| JPEG Ultra HDR（HLG） | **实验性** — [CHECKPOINT_JPEG_HLG.md](CHECKPOINT_JPEG_HLG.md) |
| HEIF / AVIF / JXL（PQ） | **完成**（2026-07）— 见 [STATUS.md](STATUS.md) |

---

## 2. 输出管线

```
scRGB × (1/125) → colour 直转 → 曲线编码 → 量化
    → pyoxipng.RawImage + metadata chunks → PNG
```

| 曲线 | cLLi | 默认量化 |
|------|------|----------|
| PQ | 写 | 10-bit |
| HLG | 不写 | 10-bit |
| Linear | 写 | 16-bit |

HLG 须走 `hlg_encode_bt2100()`（显示光 ÷ L_W → OETF）。静帧阅读器通常不做 BT.2100 OOTF，编码侧也不做 OOTF⁻¹。

---

## 3. PNG 块

`IHDR → cICP → iCCP → [cLLi] → IDAT → IEND`

per-gamut ICC：`assets/*.icc`（PQ/HLG/Linear × 3 色域），生成见 `scripts/generate_pq_icc_assets.py`。

---

## 4. oxipng

- **依赖**：`pyoxipng>=9.1.1`
- **等级**：`encode_level` **1–6**（默认 **2**）；`0` = 关闭
- **回退**：RawImage 失败 → legacy + `optimize_from_memory` → 仅 legacy
- **CLI / GUI**：统一 `--level` / 「压缩等级」控件

---

## 5. 关键文件

```
src/hdr_converter/core/
├── color_pipeline.py
├── png_optimizer.py
├── encoders/png_encoder.py
└── assets/*.icc
```

---

## 6. 验收

- Chrome HDR（cICP）
- Windows 照片 SDR（ICC LUT）
- 样本：Horizon / Forza JXR → `test_output/`

---

## 7. 后续

- JPEG Ultra HDR — [CHECKPOINT_JPEG_PQ.md](CHECKPOINT_JPEG_PQ.md) / [CHECKPOINT_JPEG_HLG.md](CHECKPOINT_JPEG_HLG.md)
- Gain Map SDR tone map 已固定为 `hable_max` — [STATUS.md §3](STATUS.md#3-sdr-色调映射)
- 输出侧 HEIF / AVIF / JXL 已验收 — [STATUS.md](STATUS.md)
- 下一阶段：多格式输入 — [MULTI_FORMAT_PLAN.md](MULTI_FORMAT_PLAN.md)
