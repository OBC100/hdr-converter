# 检查点：JPEG Ultra HDR — PQ（2026-06）

本文档记录 **Ultra HDR JPEG（Gain Map + PQ）** 原生管线的定稿状态。PNG 侧见 [CHECKPOINT_PNG.md](CHECKPOINT_PNG.md)；ICC 生成历程见 [CHECKPOINT_PQ.md](CHECKPOINT_PQ.md)。HLG 后续见 [CHECKPOINT_JPEG_HLG.md](CHECKPOINT_JPEG_HLG.md)。

---

## 1. 状态

| 模块 | 状态 |
|------|------|
| 增益图计算（`gainmap_math.py`） | **完成**（PQ 峰值 10000 nits） |
| ISO 21496-1 元数据编码 | **完成**（标志位对齐 libultrahdr 1.4+） |
| MPF + 容器拼装（`uhdr_jpeg_mux.py`） | **完成** |
| 原生 JPEG 编码（`encode_gainmap_native_jpeg`） | **完成**（默认 JPG 路径） |
| Baseline ICC（Apple matrix-shaper 生成器） | **完成**（三色域 548B） |
| Gain map 副图 HDR ICC | **完成**（per-gamut PQ，`libjxl_pq_icc`） |
| `imagecodecs.ultrahdr_check` | **通过**（sRGB / P3 / BT.2020） |
| Windows 照片（baseline / 完整 UHDR） | **不崩溃**（2026-06 **初步验收**） |
| mozjpeg 编码 + 色度抽样 | **完成**（默认 4:2:0） |
| GUI（JPG 选项对齐） | **完成** |

**SDR 色调映射**：固定 `HABLE_MAX`（见 [STATUS.md §3](STATUS.md#3-sdr-色调映射)）。

---

## 2. 管线总览

```
JXR → scRGB
  → 目标色域 linear HDR（1.0 = 10000 nits）
  → SdrToneMap → SDR 基础图（8-bit，sRGB OETF，目标基色）
  → 增益图 = f(HDR_nits, SDR_nits)，log2 编码
  → baseline JPEG + gainmap JPEG
  → mux（ISO primary/secondary APP2 + MPF + 副图拼接）
```

与 libultrahdr 回退路径的区别：**JPG 默认走原生拼装**，不依赖 `imagecodecs.ultrahdr_encode` 写容器（仅用于校验与 A/B）。

### 2.1 默认编码参数

| 参数 | 值 |
|------|-----|
| `curve` | PQ |
| `hdr_delivery` | `gainmap_mono` |
| `sdr_tonemap` | **`HABLE_MAX`**（唯一） |
| `gainmap_scale` | 2 |
| `encode_level` / quality | 90 |
| `jpeg_subsampling` | 420（4:2:0） |
| `base_bits` | 8（JPEG baseline 锁定） |

### 2.2 色彩语义

| 图像 | 像素含义 | ICC |
|------|----------|-----|
| **Baseline（主图）** | 非线性 SDR，目标基色 + sRGB γ | Apple baseline（`create_apple_baseline_icc_profile`） |
| **Gain map（副图）** | 8-bit 增益编码（mono 时 RGB 三通道相同） | per-gamut PQ HDR ICC（`get_hdr_icc(gamut, PQ)`） |

Baseline ICC **不随 PQ/HLG 变化**（传递函数均为 sRGB 型）；HDR 曲线差异体现在增益图元数据 headroom 与副图 ICC。

---

## 3. Baseline ICC 生成标准

LR Ultra HDR 参考结构，**非整文件复制**：

| 固定（三色域相同） | 随 `gamut` 变化 |
|-------------------|-----------------|
| 头 byte 4–127（CMM=`appl`） | `desc`：`sRGB` / `Display P3` / `Rec. 2020` |
| `wtpt`、`chad`、共享 `rTRC/gTRC/bTRC` | `rXYZ` / `gXYZ` / `bXYZ`（D50 适应矩阵列） |
| `cprt` text | — |

实现：`assets/apple_baseline_icc.py` → `get_baseline_display_icc()`。

曾踩坑：tag table 每条多写 4B reserved → 偏移错位 → Windows 照片崩溃；自研 jxl 短 TRC / 短 desc 亦会崩溃。

---

## 4. 容器结构（原生路径）

主图 APP2 顺序（典型）：

1. `urn:iso:std:iso:ts:21496:-1` — primary（version only，4B payload）
2. `MPF` — 双图目录（primary size/offset、secondary size/offset）
3. `ICC_PROFILE` — baseline（单段 548B）
4. … JPEG 常规段 …
5. 第一个 `FF D9` 后：secondary ISO APP2 + gainmap JPEG body

ISO secondary 载荷含完整 `GainmapMetadata` 二进制（分数域，对齐 `gainmapmetadata.cpp`）。

关键标志位（`gainmap_math.py`）：

- bit 7 `0x80`：多通道增益图
- bit 6 `0x40`：`use_base_color_space`（使用 baseline 色域合成）

---

## 5. 增益图元数据（PQ / HLG）

| 字段 | 值 |
|------|-----|
| 动态峰值 | `compute_content_light` 99.99% 分位 MaxCLL（nits） |
| 钳位 | [203, 10000] nits |
| `max_content_boost` | `peak_nits / 203` |
| `hdr_capacity_max` | 同上 |
| HDR 像素 → nits | `hdr_linear × 10000`（绝对标度，与曲线无关） |

固定 10000/1000 仅作 `default_gainmap_metadata` 无画面时的回退。

---

## 6. 关键文件

```
src/hdr_converter/core/
├── gainmap_math.py           # 增益计算 + ISO 21496 编码
├── uhdr_jpeg_mux.py          # MPF + 主/副图拼接
├── gainmap_pipeline.py       # encode_gainmap_native_jpeg
├── jpeg_encode.py            # mozjpeg（回退 Pillow）
├── jpeg_options.py           # 色度抽样
├── baseline_icc.py           # JPEG ICC 嵌入 / 原位替换
├── assets/
│   ├── apple_baseline_icc.py # SDR baseline ICC 生成器
│   ├── libjxl_pq_icc.py     # HDR ICC（PQ/HLG/Linear）
│   ├── display_p3_baseline_lr.icc  # 结构标定样本
│   └── *_pq.icc              # 副图 PQ ICC 资产
scripts/
├── _gen_baseline_only.py     # 仅 baseline 对照图
├── _gen_uhdr_all_gamuts.py   # 三色域 PQ/HLG
├── _compare_mozjpeg.py       # Pillow vs mozjpeg
├── _sweep_mozjpeg.py         # mozjpeg 参数扫描
├── _compare_uhdr.py          # 原生 vs libultrahdr 结构
```

---

## 7. 测试样本（`scripts/_test_out/`）

| 文件 | 说明 |
|------|------|
| `Forza_Horizon_6_baseline_{srgb,p3,bt2020}_icc.jpg` | 仅 baseline + ICC |
| `Forza_Horizon_6_uhdr_{srgb,p3,bt2020}.jpg` | 完整 Ultra HDR PQ |
| `Forza_Horizon_6_uhdr.jpg` | P3 别名 |

样张：`Forza Horizon 6` JXR（3840×2160，peak ~10000 nits）。

验收项：

- [x] Windows 照片打开 baseline / UHDR 不崩溃（**初步验收**）
- [x] `ultrahdr_check` 通过
- [x] baseline ICC 548B，三色域结构一致
- [x] mozjpeg 编码；色度抽样 420/422/444 可选
- [x] SDR tone map 定稿为 `HABLE_MAX`
- [ ] Chrome / Android HDR 显示目视
- [ ] 与 Lightroom 导出 UHDR 数值对比（可选）

---

## 8. 与 PNG PQ 的关系

| 项目 | PNG Direct | JPEG Ultra HDR |
|------|------------|----------------|
| HDR 像素 | PQ OETF 编码 | 不直接存 PQ 像素；存 linear + gain map |
| HDR ICC | A2B0 LUT tone-map | 副图 ICC + ISO 元数据描述合成 |
| Baseline ICC | 无（整图为 HDR） | **必选**，Apple matrix-shaper |
| SDR 回退 | ICC LUT 解码 | baseline 图 + 可选忽略 gain map |

PNG 侧「per-gamut ICC + 匹配 LUT」经验仍适用于 **gain map 副图 HDR ICC**；baseline ICC 为 JPEG 专有层。

---

## 9. 已知限制

- JPG 仅支持 Gain Map 交付（PQ/HLG）；**GUI 隐藏 Linear 曲线**
- sRGB 曲线输出普通 8-bit JPEG（非 Ultra HDR）
- `gainmap_bits` 实际由 JPEG 8-bit 锁定；`base_bits` 对 JPG 恒为 8
- libultrahdr 回退路径仍可用于 A/B，但生产默认原生 mux
- MPF 原位替换 ICC 时须保持 APP2 段长，见 `embed_baseline_icc_in_jpeg()`
- 预览不含 curve 编码与 tone map / gainmap 设置（见 [PROJECT.md §10](PROJECT.md#10-gui-预览)）

---

## 10. 下一步

- **SDR 色调映射**：已固定 `HABLE_MAX` — [STATUS.md §3](STATUS.md#3-sdr-色调映射)
- HLG 目视验收 — [CHECKPOINT_JPEG_HLG.md](CHECKPOINT_JPEG_HLG.md)
- Chrome / Android HDR 显示
