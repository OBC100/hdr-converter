# ICC Profile 策略与生成器

> 配套实现：`core/icc_policy.py`、`assets/libjxl_pq_icc.py`、`assets/apple_baseline_icc.py`。  
> 历史踩坑见 [CHECKPOINT_PQ.md](CHECKPOINT_PQ.md)、[CHECKPOINT_JPEG_PQ.md](CHECKPOINT_JPEG_PQ.md)。

---

## 1. 为什么需要 ICC

| 元数据 | 作用 | 局限 |
|--------|------|------|
| **cICP / NCLX** | 标明 primaries + transfer（PQ=16、HLG=18…） | 部分 SDR 查看器**忽略** CICP，按 sRGB 解 → 色域/亮度错误 |
| **ICC (iCCP / APP2 / colr·prof)** | 给「只认 ICC」的查看器一条可解码路径 | 错误结构可导致崩溃；HEIF/AVIF + ICC 在 **Windows 照片** 上易全红 |

本项目原则：

1. **HDR 像素语义**以 cICP/NCLX 为准（Chrome / 系统 HDR 路径）。
2. **SDR 回退 / 跨软件打开**依赖匹配的 ICC（色域 primaries + 曲线或 tone-map LUT）。
3. **禁止**只改 ICC 的 `rXYZ/gXYZ/bXYZ` 而不重建 LUT（必过饱和，见 CHECKPOINT_PQ v3）。

---

## 2. 两类生成器（唯一合法来源）

### 2.1 HDR 显示 ICC — `libjxl_pq_icc.py`

移植 libjxl `MaybeCreateProfile`：

```
编码信号（PQ/HLG/Linear）
  → 曲线反变换到线性光
  → Rec.2408 tone-map（约 10000→250 nits，供 SDR 显示器）
  → GamutMapScalar
  → Lab PCS（mft1 3D LUT，按该 profile 的 primaries 逐点生成）
```

| 曲线 | 色域 | 资产文件 | iCCP 名示例 |
|------|------|----------|-------------|
| PQ / HLG / Linear | sRGB / P3 / BT.2020 | `srgb_pq.icc` 等 9 个 | `sRGB PQ` / `DisplayP3 PQ` / `Rec2100PQ` |

重新生成：

```bash
python scripts/generate_pq_icc_assets.py
# 或
python scripts/generate_all_icc_assets.py
```

### 2.2 SDR baseline ICC — `apple_baseline_icc.py`

对齐 Lightroom Ultra HDR 主图：Apple matrix-shaper（`appl` CMM、para TRC、D50 chad + r/g/bXYZ）。

| 色域 | desc | 大小 |
|------|------|------|
| sRGB / Display P3 / Rec. 2020 | 同左 | ~548 B |

**仅用于 SDR 像素**（sRGB γ）。不可拿 HDR ICC 去标 SDR 基础图。

---

## 3. 各格式默认策略（`icc_policy.plan_icc_embed`）

| 格式 | 默认嵌入 | 种类 | Windows 照片 | 备注 |
|------|:--------:|------|:------------:|------|
| **PNG** HDR 曲线 | ✓ | HDR iCCP | ✓ | + cICP + cLLi |
| **PNG** sRGB 曲线 | ✓ | Baseline iCCP | ✓ | **新**：避免仅 cICP 偏色 |
| **JPG** Ultra HDR | ✓ | 主图 baseline + 副图 HDR | ✓（结构敏感） | 见 CHECKPOINT_JPEG |
| **JPG** Direct sRGB | ✓ | Baseline APP2 | ✓ | **新** |
| **JXL** Direct | ✗ | 仅 nclx + 码流 | HDR 非 BT.2020 时**异常**（Windows WIC 解码器 bug，见 §4.6） | `--embed-icc` 才写 prof；ICC 无法规避该 Windows 端 bug |
| **JXL** Gain Map | ✗ | 默认无 `alt_icc` | ✓（推荐） | `--embed-icc` 才写 HDR alt_icc |
| **AVIF / HEIF** | ✗ | 仅 NCLX | ✓（推荐） | `--embed-icc` 才写 prof；**可能全红** |

覆盖默认：

```bash
python -m hdr_converter.cli in.jxr -o out.avif --format avif --embed-icc
python -m hdr_converter.cli in.jxr -o out.png --format png --no-embed-icc
```

API：`ConvertSettings(embed_icc=True|False|None)` / `EncodeOptions.embed_icc`。

---

## 4. 如何生成「正确可用」的 ICC（研究结论）

1. **与像素编码一致**：PQ 图配 PQ ICC；P3 像素配 P3 primaries 的 LUT，不能共用 Rec2100PQ 再改 XYZ。
2. **HDR ICC 必须含 tone-map LUT**：否则 SDR 查看器把 PQ 码值当 γ 显示会严重发灰/发黑；LUT 把 HDR 压到约 250 nits 相对色度。
3. **Baseline 结构要对齐生态**：错误 tag 长度会导致 Windows 照片崩溃（曾踩 reserved 4B、短 TRC）。
4. **容器挂载点要对**：
   - PNG → `iCCP`（zlib）
   - JPEG → `APP2` `ICC_PROFILE`（可分段；Ultra HDR 须注意 MPF 偏移）
   - HEIF/AVIF/JXL → `colr` box：`nclx` 与可选 `prof`（可并存）
5. **HEIF/AVIF/JXL 优先 NCLX**：矩阵系数 identity(0) 在部分解码器会通道错乱，本项目 HEIF/AVIF Direct/Gain Map 基础层用 mc=9；ICC 作为可选增强，不作为默认。
6. **JXL HDR + 窄色域（已确认为 Windows 解码器缺陷，非本工具可修）**：Windows「JPEG XL Image Extension」（WIC 解码器）对 **HDR（float 输出）JPEG XL** 存在已知 bug——不论码流内 primaries 标的是什么，解码时一律按 BT.2020 处理，导致 P3/sRGB 像素被当作更宽色域显示，越窄的原色越明显过饱和/偏红（Microsoft 官方仓库确认，[microsoft/WindowsAppSDK#5390](https://github.com/microsoft/WindowsAppSDK/issues/5390)，状态 closed as external，未修复）。该问题与本项目的 nclx/ICC 写法无关，写不写 ICC、`matrix_coefficients` 取何值都不影响——因为 WIC 的 HDR 解码路径根本没有正确读取/应用这些标签。已知无编码侧规避方法；受影响时建议：
   - 提示用户改用浏览器（Chrome/Edge 原生 libjxl 解码路径不受影响）或其它解码器（djxl、ImageGlass 等）预览 JXL HDR P3/sRGB；
   - 或在 Windows 照片场景下建议用户改选 BT.2020 输出（该 bug 恰好使 BT.2020 标签被"正确"处理）。

---

## 5. 代码入口

| 模块 | 职责 |
|------|------|
| `icc_policy.py` | 按格式决策 + `plan_and_bytes` |
| `assets/libjxl_pq_icc.py` | HDR 生成 |
| `assets/apple_baseline_icc.py` | Baseline 生成 |
| `assets/__init__.py` | `get_hdr_icc` 读预生成资产 |
| `baseline_icc.py` | JPEG APP2 嵌入 / 原位替换 |
| `isobmff_gainmap.attach_icc_*` | AVIF/HEIF `colr`/`prof` |
| `encoders/*` | 按策略调用 |

---

## 6. 验收建议

| 检查 | 期望 |
|------|------|
| PNG PQ：块顺序含 iCCP | Windows 照片 SDR 回退不「假 sRGB」 |
| PNG sRGB 曲线：有 baseline iCCP | 第三方看图软件色域正确 |
| JPG Direct：有 APP2 ICC | 同上 |
| AVIF 默认：无 prof，有 nclx | Windows 照片正常 |
| AVIF `--embed-icc` | 其它查看器可读 ICC；Windows 照片可能异常（已知） |
| 改生成器后跑 `generate_all_icc_assets.py` | 9×HDR 资产刷新；baseline 冒烟 |
