# 检查点：PQ 实现历程（2026-06）

本文档记录 PQ 曲线从零到可用的完整路径，供 HLG 等后续曲线借鉴。

---

## 1. 目标

Windows HDR JXR（scRGB 线性，1.0 = 10000 nits）→ PNG/HEIF/AVIF/JPG，支持 **BT.2020 / Display P3 / sRGB + PQ**，嵌入 **cICP + iCCP + cLLi**，对齐 [jxr_to_png](https://github.com/ledoge/jxr_to_png)。

---

## 2. 历程与踩坑

| 阶段 | 做法 | 结果 |
|------|------|------|
| v1 | 仅 cICP + cLLi，无 iCCP | 部分查看器兼容性差 |
| v2 | 统一嵌入 libjxl `Rec2100PQ` | BT.2020 SDR 回退正常 |
| v3 | patch r/g/bXYZ → sRGB/P3 专用 ICC | **SDR 过饱和**（LUT 仍为 BT.2020 tone-map） |
| v4 | 自写简化 tone-map Python ICC | **比统一 Rec2100PQ 更差** |
| v5 | 回退统一 `rec2100_pq.icc` + 不同 cICP | HDR 正确，SDR 仍依赖错误 LUT |
| **定稿 v6** | **完整移植 libjxl `MaybeCreateProfile`** | per-gamut primaries + 匹配 mft1 LUT，目测 OK |

### 核心结论

`rec2100_pq.icc` 的 **mft1 3D LUT** 由 `ToneMapPixel()` 按**该 profile 的 primaries** 逐点生成：

```
PQ 解码 → Rec.2408 tone-map (10000→250 nits) → GamutMapScalar → Lab PCS
```

**只改 r/g/bXYZ 而不重建 LUT = primaries 与 LUT 不匹配 → SDR 过饱和。**

---

## 3. 定稿架构

### 3.1 像素管线（`color_pipeline.py`）

```
scRGB × (1/125) → colour 直转目标色域 → PQ OETF → uint16(10-bit 左对齐)
```

- **量化**：10-bit 有效深度，左对齐到 16-bit（与 jxr_to_png 一致）
- 历史可选亮度校准已移除（见 [PROJECT.md](PROJECT.md) §7）

### 3.2 ICC（`assets/libjxl_pq_icc.py`）

- 移植：`TF_PQ_Base`、`Rec2408ToneMapperBase`、`GamutMapScalar`、`CreateICCLutAtoBTagForHDR`、`MaybeCreateProfileImpl`
- 输出：`rec2100_pq.icc` / `srgb_pq.icc` / `display_p3_pq.icc`
- **验证**：生成的 `rec2100_pq.icc` 与 jxr_to_png 参考 **除 MD5 profile ID（16 字节）外完全一致**

### 3.3 元数据

| 色域 | cICP | iCCP 名称 |
|------|------|-----------|
| BT.2020 | `{9,16,0,1}` | Rec2100PQ |
| P3 | `{12,16,0,1}` | DisplayP3 PQ |
| sRGB | `{1,16,0,1}` | sRGB PQ |

PNG 块顺序：`IHDR → cICP → iCCP → cLLi → IDAT → IEND`（无 gAMA/cHRM）

---

## 4. 关键文件

```
src/hdr_converter/core/
├── color_pipeline.py          # direct 直转 + OETF
├── color_metadata.py          # ICC 按 gamut 选择
├── assets/
│   ├── libjxl_pq_icc.py       # ICC 生成器
│   ├── __init__.py
│   ├── rec2100_pq.icc
│   ├── srgb_pq.icc
│   └── display_p3_pq.icc
└── encoders/png_encoder.py
scripts/generate_pq_icc_assets.py
```

---

## 5. 给 HLG 的借鉴清单

1. **ICC 必须与像素曲线 + 色域 primaries 一致**：per-gamut 生成，禁止 patch XYZ。
2. **优先移植 libjxl**，不要自写简化 tone-map。
3. **像素管线与 ICC LUT 使用同一套解码/映射公式**（libjxl `ToneMapPixel` 分支）。
4. ~~亮度校准可复用~~：**已废弃并移除**（不再建议设备特定暗部增益）。
5. **cICP transfer = 18（HLG）**，iCCP 描述：`Rec2100HLG` / `RGB_D65_*_Rel_HLG`。
6. **生成后做字节级对比**（若有参考 profile）或 metadata 校验。

---

## 6. 测试样本

- Forza Horizon 6 JXR（peak ~10000 nits）→ `test_output/forza_*_pq_v2.png`
- 验证项：cICP / iCCP 名称 / ICC 内 cicp 标签 / Chrome HDR + Windows 照片 SDR

---

## 7. 状态

**PQ：PNG / JPG Ultra HDR / HEIF / AVIF / JXL 已完成正式验收**（见 [STATUS.md](STATUS.md)）。

HLG / Linear：代码路径存在，**现视为实验性、未纳入正式验收**（早期 HLG 笔记见 [CHECKPOINT_JPEG_HLG.md](CHECKPOINT_JPEG_HLG.md)）。SDR tone map 定稿为 `hable_max`。baseline ICC 生成见 `apple_baseline_icc.py`。
