# 执行计划（供子代理执行）

> 本文档是 [MULTI_FORMAT_PLAN.md](MULTI_FORMAT_PLAN.md) 的可执行拆解——那份文档讲"为什么这么设计"，本文档讲"具体按什么顺序、改哪些文件、怎么验收"。执行者不需要重新决策架构，只需要按阶段推进并满足每阶段的验收标准；如果执行中发现某个决策跟 `MULTI_FORMAT_PLAN.md` 冲突，**以那份文档为准，先停下来汇报，不要自行改架构**。

---

## 0. 给执行者的通用规则（不可违反）

**0.1 阶段必须顺序执行，不能跳过**——后面阶段依赖前面阶段的产物（`SourceImage`、`to_canonical_bt2020_linear` 等）。**元数据阶段（H / H2）必须放在最后**：色彩管线的 bug 大多"肉眼可见"（颜色不对），元数据的 bug 往往"沉默直到某个挑剔的查看器/设备打不开文件"，两类风险叠加会互相掩盖，排查成本更高；等色彩管线稳定后再单独投入精力做元数据的结构校验。

**0.2 每阶段收尾前必须跑 JXR 回归基线**：用重构前后的输出做像素级对比，容差沿用 `PROJECT.md` §8 记录的标准（Horizon 样张 max diff ≈ 1.5×10⁻⁵ 量级）。任何阶段如果让 JXR 现有行为发生超出这个容差的偏移，视为失败，必须先修复才能进入下一阶段。

**0.3 新格式的验证必须用真实世界样本，不能只用本工具自己生成的文件自测**——用自己写的编码器产出的文件去验证自己写的解码器，只能证明"两段代码互相认识对方的假设"，不能证明"这些假设是对的"。每新增一种格式的读支持，至少准备 1-2 个来自其它主流工具（系统相机、Photoshop/Lightroom、ffmpeg、libavif/libheif 官方 CLI、Android/iOS 相机等）产出的真实文件用于测试。

**0.4 二进制容器里的长度/计数/校验字段永远用代码算出来，不手写字面值**——JPEG 段长度、PNG chunk CRC32、ISOBMFF box size、iloc/iinf 计数，任何一处手写错的字面值都是"看起来能跑、遇到严格解码器就炸"的经典 bug 来源。

**0.5 元数据处理默认"最小侵入"**：能整体不解析就不解析（纯字节透传）；必须改的字段用**原地 patch**（只覆写那几个字节），不要把整块 blob 反序列化成对象再重新序列化回去——反序列化/重序列化的风险是会丢掉工具没预料到的厂商私有结构（MakerNote、非标准 IFD 等），这一点在 §8（元数据）会展开，这里先立规则。

**0.6 每阶段的"完成的定义"**：任务清单全部完成 + 验收标准全部通过 + 补充/更新了 `docs/MULTI_FORMAT_PLAN.md` 里对应里程碑的状态标记（草案里目前都是"待办"，完成后改成"✅"，参照 `UNIFIED_PIPELINE.md` 的记法）。

---

## Stage A — `SourceImage` + 唯一归一化函数 + JXR 路径重构

**目标**：搭好骨架，验证 JXR 现状零回归，后续阶段都在这个骨架上加解码器。

**涉及文件**：
- 新增 `core/source_image.py`：`SourceImage` dataclass（字段见 `MULTI_FORMAT_PLAN.md` §3.1，先只填 JXR 需要的字段：`linear`/`primaries`/`reference_white_nits`/`is_hdr`/`alpha`，`embedded_gainmap`/`icc_profile`/`metadata` 先占位为 `None`，Stage D/D2/H 再填）
- `core/scrgb_colour.py`：确认 `gamut_linear_to_gamut_linear`（已存在）可以直接被新函数复用，不需要改动
- 新增 `to_canonical_bt2020_linear()`（放在 `core/color_pipeline.py` 或新建 `core/canonical.py`，建议新建文件避免 `color_pipeline.py` 越来越臃肿）
- `core/jxr_decoder.py`：新增 `decode_jxr_to_source_image(path) -> SourceImage`，内部调用现有 `decode_jxr()`，包一层 `SourceImage(linear=raw, primaries=Gamut.SRGB, reference_white_nits=80.0, is_hdr=True)`
- `core/decode_cache.py`：**Stage A 仍缓存原生 scRGB**（`SourceImage.linear`），保持现有 `JxrDecodeCache` 接口与下游消费者零回归。`load_jxr_raw` 内部改成经 `decode_jxr_to_source_image` 取 `.linear`。**缓存改为 canonical BT.2020 的泛化留到 Stage E**，与本阶段骨架搭建解耦。

**任务清单**：
- [x] 写 `SourceImage` dataclass
- [x] 写 `to_canonical_bt2020_linear(src_linear, primaries, reference_white_nits) -> np.ndarray`，内部调用 `gamut_linear_to_gamut_linear(src_linear * (reference_white_nits/10000), primaries, Gamut.BT2020)` 后 `np.clip(..., 0.0, None)`（**注意**：缩放是 `ref/10000` 而非 `10000/ref`，与 `_SCRGB_TO_HDR_LINEAR_SCALE=1/125` 对齐）
- [x] `jxr_decoder.py` 加 `decode_jxr_to_source_image`
- [x] `decode_cache.py::load_jxr_raw` 改接线：经 `decode_jxr_to_source_image` 取原生 `linear`（Stage A **仍缓存 scRGB 原生缓冲**，保持下游 `convert_colorspace` / Gain Map / 预览零回归；canonical 转换由回归脚本与后续 Stage E 消费）
- [x] 删除/废弃 `color_pipeline.py` 里被替代的 JXR 专属函数前，**先跑通验收标准**，确认新路径数值等价后才能删旧函数（Stage A：旧函数保留，回归 max|Δ|≈7e-9 ≤ 1.5e-5）

**验收标准**：
- [x] `scripts/_regress_stage_a.py`：合成 + `test_input.jxr`，max|Δ| ≤ 1.5×10⁻⁵
- [x] `convert_file` 冒烟：PNG/JPG/HEIF/AVIF/JXL 各一次（用户可见行为不变）
- [x] Stage A 不改变任何用户可见行为（缓存仍为 scRGB）

---

## Stage B — PNG / 普通 JPEG 的 SDR + HDR(cICP) 解码器

**目标**：让本工具自己产出的 HDR PNG、以及普通 SDR PNG/JPEG 能作为输入读回来。

**涉及文件**：
- `core/cicp.py`：新增反查表 `CICP_TO_GAMUT_CURVE: dict[tuple[int,int,int], tuple[Gamut, TransferCurve]]`（`_CICP_TABLE` 的反向映射，注意 `to_bytes()`/十进制值一一对应，写个单测保证正反表互逆）
- 新增 `core/decoders/png_decoder.py`：读 `IHDR`/`cICP`/`iCCP`/`cLLi` chunk（可复用 Pillow 或 `imagecodecs` 的 PNG 解码拿像素，chunk 解析需要自己写一个轻量 PNG chunk 遍历器，不依赖 Pillow 是否暴露原始 chunk）→ 有 `cICP` 时查反查表得到 `(gamut, curve)`，按 §4 曲线公式解码回线性；没有 `cICP` 时按 sRGB gamma 处理（primaries 暂时先固定 sRGB，Stage D2 再接入 ICC 解析泛化）
- 新增 `core/decoders/jpeg_decoder.py`：先只处理**不带 Gain Map 的 baseline JPEG**（Gain Map 检测和 demux 留给 Stage D），按 sRGB gamma 解码
- 新增 `core/decoders/__init__.py`：格式 → 解码函数的注册表，为 Stage E 的自动分发做准备（哪怕 Stage E 还没做，先把注册表建起来）

**任务清单**：
- [x] `cicp.py` 反查表 + 单测（正反表互逆）— `scripts/_regress_stage_b.py`
- [x] PNG chunk 遍历器（`iter_png_chunks`，含 CRC 校验）
- [x] `png_decoder.py`：产出 `SourceImage`（优先 imagecodecs 保留 16-bit）
- [x] `jpeg_decoder.py`（baseline 分支）：产出 `SourceImage`
- [x] 解码器注册表（`core/decoders/__init__.py`）

**验收标准**：
- [x] 本工具 HDR PNG（PQ/HLG/Linear × 三色域）encode→decode 往返，max|Δ|≈4.5e-4（10-bit 量化量级）
- [x] 无 cICP 的普通 PNG / baseline JPEG 按 sRGB 假设解码不报错
- [x] CICP 正反表互逆

---

## Stage C — HEIF / AVIF / JXL 的 Direct（NCLX/CICP）解码器

**目标**：读取这三种格式的 Direct（非 Gain Map）HDR/SDR 内容。

**涉及文件**：
- 新增 `core/decoders/heif_decoder.py` / `avif_decoder.py` / `jxl_decoder.py`
- 复用 `isobmff_gainmap.py` 已有的 `_find_box`/`_child_boxes`/`parse_single_image_item` 读 `colr` box（NCLX，`nclx` 类型；也可能是 `prof`/`rICC` 类型的嵌入 ICC，先只处理 `nclx`，ICC 类型留给 Stage D2）
- JXL：容器模式下编码器在 `ftyp` 后写入 `colr`/nclx（码流 ColourEncoding 位打包 imagecodecs 未暴露）；解码器读该 box

**任务清单**：
- [x] `heif_decoder.py`：像素解码（pillow-heif）+ NCLX 读取 + 反查表映射
- [x] `avif_decoder.py`：像素解码（`imagecodecs.avif_decode`）+ NCLX 读取
- [x] `jxl_decoder.py`：像素解码（`imagecodecs.jpegxl_decode`）+ 容器 `colr` 读取
- [x] 三者都接入 Stage B 建的解码器注册表
- [x] 运行时可用性检测：`is_avif_supported` / `is_heif_supported` / `is_jxl_supported`

**验收标准**：
- [x] `scripts/_regress_stage_c.py`：本工具导出的 HEIF/AVIF/JXL Direct 往返，有损格式按 p99.9 ≤ 1e-2
- [ ] 真实世界 HDR HEIF/AVIF 样本（无设备时待补测）
- [x] 依赖缺失时报错清晰（`AVIFDecodeError` / `HEIFDecodeError` / `JXLDecodeError`）

---

## Stage D — 四种格式的 Gain Map demux

**目标**：让带 Gain Map 的 HEIF/AVIF/JPG(UltraHDR)/JXL 输入能正确还原出完整 HDR 线性。

**涉及文件**：
- `core/gainmap_tmap.py`：补反向解析函数（读 `tmap` payload → `GainmapMetadata`）
- `core/gainmap_math.py`：新增 `apply_gainmap(base_sdr_linear, gain, metadata) -> hdr_linear`（ISO 21496-1 公式的逆运算，四种容器共用，**只写一次**）
- `core/isobmff_gainmap.py`：新增读取 `iref dimg` / `grpl altr` 找 base item + gain item 的函数（`_find_box`/`_child_boxes`/`_parse_iloc`/`_parse_infe` 已有，缺的是"顺藤摸瓜找到 tmap→[base,gain] 这组关系"的组装逻辑）
- `core/uhdr_jpeg_mux.py`：新增读取 MPF + APP2（ISO 21496 二进制）的函数，拆出 base JPEG + gain JPEG 两路裸流
- `core/jxl_gainmap.py`：已有 `parse_jhgm_bundle`，只需要接上 `apply_gainmap`
- Stage B/C 的四个解码器都要接一个"先检测是否带 Gain Map，有则走 demux，无则走 Direct"的分支

**任务清单**：
- [x] `apply_gainmap` 通用函数 + 单测（构造一组已知 hdr_linear/sdr_linear，先用现有 `compute_gainmap_with_peak` 生成 gain，再用 `apply_gainmap` 还原，比较还原结果和原始 hdr_linear 的误差）
- [x] `gainmap_tmap.py` 反向解析
- [x] `isobmff_gainmap.py` 的 HEIF/AVIF Gain Map 读取组装函数
- [x] `uhdr_jpeg_mux.py` 的 JPEG Gain Map 读取（MPF + APP2 demux）
- [x] `jxl_gainmap.py` 接上 `apply_gainmap`
- [x] 四个解码器接入"检测 Gain Map → 走 demux"分支

**验收标准**：
- [x] `apply_gainmap` 单测：`scripts/_regress_stage_d.py`，p99.9 ≤ 2e-3
- [x] 本工具导出的 JPG/AVIF/JXL Gain Map encode→demux 往返（HEIF 依赖缺失时跳过），p99.9 ≤ 5e-2
- [ ] 真实世界 UltraHDR JPEG（XMP `hdrgm:` 路径留给 Stage H2；无 APP2 时退化 Direct SDR）

---

## Stage D2 — 命名色彩空间登记表 + ICC 数值解析 + CAT

**目标**：`SourceImage.primaries` 从"只能是内建 `Gamut` 三选一"泛化成"任意原色+白点+TRC"，覆盖 ProPhoto/AdobeRGB/DCI-P3 等。

**涉及文件**：
- `core/source_image.py`：`primaries` 字段类型从 `Gamut` 泛化为 `Gamut | ColourSpaceDescriptor`（新类型，包一个 `colour.RGB_Colourspace` 或等价的原色矩阵+白点）
- 新增 `core/named_colourspaces.py`：CICP 代码点/ICC 描述字符串 → `colour.RGB_COLOURSPACES` 具名色彩空间的登记表（内容见 `MULTI_FORMAT_PLAN.md` §3.5 的对照表）
- 新增 ICC 数值标签解析（`rXYZ`/`gXYZ`/`bXYZ`/`wtpt`/TRC tag），登记表匹配不到名字时的兜底路径
- `to_canonical_bt2020_linear`：`chromatic_adaptation_transform=None` 改成 `"Bradford"`

**任务清单**：
- [x] 常见色彩空间登记表（sRGB/Display P3/DCI-P3/BT.2020/AdobeRGB/ProPhoto/AdobeWideGamut/eciRGB/ACEScg/ACES2065-1，至少覆盖前 6 个，后面几个可以后续补）
- [x] ICC `desc` 字符串模糊匹配
- [x] ICC 数值标签兜底解析（构造匿名 `colour.RGB_Colourspace`）
- [x] `to_canonical_bt2020_linear` 接入 CAT（内建 Gamut 仍走快速矩阵；descriptor 用 Bradford）
- [x] PNG/JPEG 无 cICP 时读 iCCP / `icc_profile` → descriptor（不再一律当 sRGB）
- [x] CICP `color_primaries=11` → DCI-P3（与 Display P3 `cp=12` 区分）
- [x] **回归确认**：`scripts/_regress_stage_a.py` + `scripts/_regress_stage_d2.py`

**验收标准**：
- [x] 合成 AdobeRGB ICC / 名称别名 / DCI-P3 CICP / ProPhoto Bradford 路径（`_regress_stage_d2.py`）
- [ ] 真实 ProPhoto/AdobeRGB 样张与 Photoshop 导出视觉比对（人工，有样本时补）
- [x] Stage A 回归脚本仍然通过（内建三色域零偏差）

---

## Stage E — `convert_file` 接入格式自动识别 + 解码器分发 + 直通优化

**目标**：把前面几个阶段的解码器真正接入主转换流程，加上直通优化。

**涉及文件**：
- `core/decode_cache.py`：泛化成通用缓存（存 canonical 数组，key 不变，但 `load_jxr_raw` 改名/包装成 `load_source_raw(path, cache, decoder_registry)`，按魔数分发到 Stage B/C/D 的解码器）
- 新增格式检测函数（魔数优先，扩展名兜底）：`core/format_detect.py`
- `core/converter.py`：`convert_file` 顶部加直通优化判断（输入输出格式相同 + 参数未变 → 字节拷贝，跳过整条 decode/encode）
- `gui/preview_worker.py`：同步改成调用泛化后的加载函数（为 Stage G 铺路，但这里只做接口对接，不改预览呈现逻辑）

**任务清单**：
- [x] 魔数格式检测（PNG `\x89PNG`、JPEG `\xFF\xD8`、AVIF/HEIF/JXL 的 ISOBMFF `ftyp` box 里的 brand 判断、JXR 的 JPEG XR 签名）
- [x] `load_source_raw` 泛化 + 解码器分发（JXR 仍缓存原生 scRGB；其它格式经 canonical→scRGB 桥接）
- [x] 直通优化判断逻辑（`core/passthrough.py`；Gain Map / 跨格式保守拒绝）
- [x] `convert_file`/`convert_batch` 接入，保持现有 API 签名兼容（`ConvertSettings`/`ConvertResult` 不破坏性变更）
- [x] `preview_worker.py` 接口对接

**验收标准**：
- [x] `scripts/_regress_stage_e.py`：多格式 encode→`load_source_raw` 冒烟（HEIF 依赖缺失时跳过）
- [x] 直通优化：PNG→PNG 同参数字节相同；gamut 变更 / 跨格式不误触发
- [ ] 完整 6×6 组合矩阵深度验收（冒烟已覆盖主路径；跨工具打开待补）

---

## Stage F（可选）— Gain Map remux 快速通道

**目标**：高频格式对（先做 AVIF gainmap ↔ HEIF gainmap）的字节级 remux，不重算 tone map。

本阶段优先级低于 G/H，且明确是"可选"——只有 Stage A-E 全部稳定、且有实际性能/保真度需求驱动时才做。**不要在没有需求信号的情况下主动开始这一阶段**。

---

## Stage G — GUI 预览层适配

**目标**：预览计算同步到 canonical BT.2020，呈现层继续走 scRGB（DXGI 硬限制，见 `MULTI_FORMAT_PLAN.md` §5.3a）。

**涉及文件**：
- `gui/preview_frame.py`：入参为 canonical BT.2020 → 目标色域 → scRGB 呈现
- `build_hdr_preview_scrgb`：统一走 `canonical → linear_to_preview_scrgb`（无 JXR 透传捷径）
- `gui/preview_worker.py`：`need_hdr` 门控加上 `SourceImage.is_hdr` 判断（非 HDR 源不启用 D3D 交换链）

**任务清单**：
- [x] `preview_frame.py` 入参改为 canonical BT.2020
- [x] `build_hdr_preview_scrgb` 统一路径（去掉 JXR 专属透传捷径）
- [x] `preview_worker.py` 接入多格式加载 + `is_hdr` 门控 + canonical
- [x] GUI：文件过滤器 / 拖放 / i18n 支持多格式
- [x] `scripts/_regress_stage_g.py`：JXR BT.2020 预览 vs 旧透传（p99.9 ≈ 2e-4）
- [x] 移除可选亮度校准（CLI / GUI / 管线）

**验收标准**：
- [x] JXR + BT.2020：与旧透传残差在预览可接受范围（矩阵往返）
- [x] 新格式输入路径接通；非 HDR 不误启 D3D
- [ ] 目视/性能与重构前同量级（建议本机再点验一次 GUI）

---

## Stage H — 统一元数据管理器（⚠️ 高风险阶段，务必谨慎）

**目标**：EXIF/XMP/ICC/IPTC 透传 + 方向转正 + 隐私开关。**这是整个计划里最容易"看起来做完了但实际会让某些严格查看器打不开文件"的阶段**，必须按下面的谨慎清单执行，不能只用本工具自己的代码互相验证就认为完成。

### H.1 为什么这个阶段特别危险（执行者必读）

色彩管线的 bug 大多会让图片"看起来不对"，容易发现。元数据的 bug 大多是**结构性的**——文件在宽松的解码器（比如 Pillow、大多数网页浏览器）里打开完全正常，但在某些严格实现（相机厂商自己的看图软件、印刷 RIP、部分 ISOBMFF 一致性检查工具、甚至某些手机相册的 HEIF 解码器）里会直接拒绝打开或崩溃。**这类 bug 在开发时几乎不可见，只有上线后才会被用户发现**，所以验证方式必须比色彩管线更严格。

### H.2 最小侵入原则的具体落实

**能不解析就不解析**：EXIF/XMP/IPTC 默认整块提取成 `bytes`，原样搬运，**不要**把它们反序列化成 Python 对象（dict/IFD 结构）再重新序列化回去——除非要修改的字段真的需要。反序列化/重序列化整块 EXIF IFD 有明确风险：
- 相机厂商的 **MakerNote** 经常是不完全遵循 EXIF 标准的私有二进制结构，很多 MakerNote 内部用**相对于 TIFF 头起始位置**的偏移量指向自己的数据；如果解析代码不认识某个厂商的 MakerNote 格式而"善意地"重新排布 IFD entries，会让这些偏移量失效，指向错误数据甚至越界——**很多专业修图软件过去都踩过这个坑**
- 非标准/扩展的私有 IFD（GPS IFD、缩略图 IFD、Nikon/Canon 私有 tag）同理

**只改必须改的字段，用原地 patch**：本项目唯一必须改的 EXIF 字段是 **Orientation（tag 0x0112）**——它是固定长度的 `SHORT`（2 字节）类型，在 IFD entry 里的偏移是可以直接定位、直接原地覆写的，**不需要**解析整个 IFD 结构。做法：
1. 在原始 EXIF blob 里扫描 IFD0，找到 tag=0x0112 的 entry
2. 直接覆写该 entry 的 value 字段（2 字节，注意 TIFF 头声明的字节序，大端/小端要读 IFD 头的 `II`/`MM` 标记）
3. 如果原本没有这个 tag（很多截图/非相机来源本来就没有），不强行插入，视为"已经是 normal"，不需要处理
4. **ICC profile 永远不做部分 patch**：要么整块替换（本工具自己生成新 ICC）要么整块保留，绝不修改 ICC 内部任何字节——ICC 头部的 `size` 字段和内部 tag table 是强耦合的，局部改动几乎必然破坏一致性
5. **XMP 如果需要新增字段**（比如 H2 阶段要新增/读取 `hdrgm:` 命名空间），必须用真正的 XML 解析器（`lxml`/`xml.etree`）操作 DOM 树后重新序列化，**不要用字符串替换/拼接**；序列化后必须保留 `<?xpacket begin=... id="W5M0MpCehiHzreSzNTczkc9d"?> ... <?xpacket end="w"?>` 包装完整

### H.3 各容器格式的具体地雷（逐格式列出）

**JPEG**：
- 段顺序敏感：EXIF APP1（`"Exif\0\0"` 前缀）应该是紧跟 SOI 的最早的 APP 段；XMP APP1（`"http://ns.adobe.com/xap/1.0/\0"` 前缀，注意跟 EXIF 用的是**同一个 marker 号但不同签名**，靠签名字符串区分，不是靠段序号）应该排在 EXIF 之后；ICC APP2 在再之后
- 如果原文件同时有 JFIF APP0 和 EXIF APP1（不完全合规但现实中常见），本工具重新编码时**优先保留 EXIF、可以丢弃 JFIF APP0**，不要让两者同时存在导致顺序歧义
- 每个 segment 的长度字段是**大端 2 字节，值 = payload 长度 + 2（包含长度字段自身，不包含 marker 本身的 2 字节）**——这是最容易手写错的地方，必须用 `struct.pack(">H", len(payload) + 2)` 算，不要手算
- ICC 若超过单段 65533 字节上限需要分段（本工具已有 `baseline_icc.py` 的分段经验，复用同一套机制读写透传的 ICC），分段序号必须从 1 开始连续、总段数在每段里都要一致，缺一段/序号错一个都会让严格解码器整体拒绝

**PNG**：
- Chunk 顺序规则是标准强制的：`eXIf`/`iTXt`（XMP）必须在第一个 `IDAT` 之前；本项目现有 `cICP`/`iCCP`/`cLLi` 的顺序约定（`docs/PROJECT.md` §7.3：`IHDR → cICP → iCCP → cLLi → IDAT → IEND`）不能因为插入 EXIF/XMP chunk 被打乱，新 chunk 应该按规范建议顺序插在这几个 chunk 附近（EXIF/XMP 是 ancillary chunk，顺序相对宽松，但都必须在 IDAT 之前）
- **每个新写的 chunk 必须正确计算并写入 CRC32**（chunk 格式是 `length(4B) + type(4B) + data + crc32(4B)`，crc32 覆盖 type+data，不含 length）——用 `zlib.crc32`，不要留 0 或复制别的 chunk 的 CRC
- 延续项目现有纪律：**不要因为加了 EXIF chunk 就顺手加回 `gAMA`/`cHRM`/`mDCV`**，这几个是本项目已经验证过会跟部分严格解码器冲突而故意不写的（`PROJECT.md` §7.3/§9.3），元数据阶段不应该重新引入

**HEIF/AVIF（ISOBMFF）**：
- `Exif`/`mime`(XMP) 是独立的 item，通过 `iref` 的 `cdsc` 类型关联到主图 item——插入新 item 会牵动 `iinf`（item 计数）、`iloc`（每个 item 的偏移长度）、可能还有 `ipma`（property association，**这是按索引关联的，插入/移动 item 会导致已有的索引错位**，这是本类格式最容易出的隐蔽 bug）
- **强烈建议直接复用 `isobmff_gainmap.py` 里已有的 `_rebuild_isobmff_with_iprp`/`_iloc_box`/`_iinf_box`/`_ipma_box` 那套重建逻辑**，不要另起一套平行的 box 拼装代码——本项目已经在 Gain Map 场景下把这套逻辑跑通过，复用能显著降低"两套逻辑各自有各自的 off-by-one"的风险
- 新增 item 的 `item_ID` 不能跟已使用的冲突，必须先扫描现有 `iinf` 拿到当前最大 ID 再 +1

**JXL**：
- 容器规范定义了专门的 `Exif`/`xml ` box 类型；有些流式解码器会假设特定 box 出现在编码流（`jxlc`/`jxlp`）之前或之后，写入顺序参照 libjxl 官方编码器的实际输出顺序（可以拿 `cjxl` 编一个带 EXIF 的样本，观察它自己的 box 顺序作为参照，不要自己猜）

### H.4 验证方式（不能只靠本工具自己的代码互相验证）

**必须用独立的外部工具做验证，这是本阶段的硬性要求**：
- **`exiftool`**：覆盖面最广（JPEG/PNG/HEIF/AVIF/TIFF/JXL 均支持读取），且有严格的警告/校验输出（`exiftool -validate -warning`），是本阶段最主要的验证工具。每一种格式的每一类元数据（EXIF/XMP/ICC/IPTC）写入后，都要过一遍 `exiftool` 确认能正确读出且没有 warning
- **PNG**：额外用 `libpng` 严格模式或 `pnglint` 类工具校验 chunk 结构和 CRC
- **JPEG**：额外用至少两个独立实现打开对比（比如 Pillow + `libjpeg-turbo` 命令行工具），确认 segment 结构双方都能正确解析
- **HEIF/AVIF**：额外用 `MP4Box -info` 或 `ffprobe -show_format -show_streams` 检查 box 结构完整性；有条件的话在真机（iPhone 相册导入 / Android 相册导入）上测试打开
- **JXL**：额外用 `djxl` 官方解码器验证
- **回归测试自动化**：把上面这些外部工具的调用写成脚本（`scripts/_validate_metadata.py`），作为这个阶段的 CI 式验收脚本，而不是只跑一次手动检查——因为元数据结构的回归很容易在后续阶段被不相关的改动意外破坏

**测试样本要求**：不能只用本工具自己生成的"干净"样本——必须用真实世界的、带复杂/非标准元数据的文件测试：
- 至少一张有相机 MakerNote 的真实照片（任意主流相机/手机拍摄的原始 JPEG，不要用截图代替）
- 至少一张有 Lightroom/Photoshop 编辑历史的 XMP（这类 XMP 通常比较大、结构复杂）
- 至少一张带非标准/自定义 ICC 的文件（比如显示器厂商标定后导出的 ICC，或者 Stage D2 用到的 ProPhoto/AdobeRGB 样本顺带复用）

### H.5 隐私开关

**任务清单**：
- [x] `MetadataBag` dataclass + Orientation 扫描/原地 patch 骨架（`core/metadata_bag.py`；各格式 extract/embed 待接线）
- [ ] 各格式 extract 函数（先只做"整块提取"，不做深度解析，除了 Orientation）
- [ ] 各格式 embed 函数（严格按 §H.3 的顺序/校验规则实现）
- [x] Orientation 原地 patch 函数（骨架已实现；合成样本单测待补）
- [ ] `metadata_policy` 参数接入 `ConvertSettings`（`preserve_icc_when_unchanged` / `always_regenerate`，见 `MULTI_FORMAT_PLAN.md` §7.4）
- [ ] 隐私清除开关（`保留全部 / 仅方向+ICC / 清除全部`）接入 CLI/GUI
- [ ] `scripts/_validate_metadata.py` 校验脚本

**验收标准**：
- `exiftool -validate -warning` 对本阶段产出的每种格式样本跑一遍，**零 warning**（有 warning 必须能解释清楚原因，不能"看起来能用就算了"）
- 真实世界样本（含 MakerNote/复杂 XMP/自定义 ICC）走一遍完整转换，元数据能被 `exiftool` 正确读出，像素结果本身仍满足前面阶段建立的精度标准
- 方向转正：找几张 Orientation≠1 的真实照片（手机常见竖拍横拍混合），转换后目视确认方向正确，且输出文件里不再有会导致"二次旋转"的 Orientation residual
- 隐私开关："清除全部元数据"模式下，`exiftool` 对输出文件应该基本读不出 EXIF/XMP/GPS（ICC 按策略可能仍保留，取决于开关粒度设计）

---

## Stage H2 — Gain Map 的 XMP（`hdrgm:`）解析路径

**目标**：Stage D 的 Gain Map demux 补上 XMP 备选数据源，覆盖 Google 风格 Ultra HDR JPEG。

**涉及文件**：
- `core/gainmap_math.py` 或新增 `core/gainmap_xmp.py`：解析 XMP `hdrgm:` 命名空间字段（`GainMapMin`/`GainMapMax`/`Gamma`/`OffsetSDR`/`OffsetHDR`/`HDRCapacityMin`/`HDRCapacityMax`），映射到项目已有的 `GainmapMetadata` 结构
- Stage D 的 JPEG demux：APP2 二进制解析失败/缺失时，尝试从 XMP 里找 `hdrgm:` 字段作为备选

**任务清单**：
- [ ] `hdrgm:` XMP 字段解析（用 XML 解析器读 `rdf:Description` 上的属性，不要用正则强行抠字符串——XMP 字段可能以属性形式或子元素形式出现，两种写法都要兼容）
- [ ] 映射到 `GainmapMetadata`，接入 Stage D 已有的 `apply_gainmap`
- [ ] JPEG demux 分支：APP2 缺失时的 XMP 兜底路径

**验收标准**：
- 用 Stage D 找到的"只有 XMP、没有 APP2"的真实 UltraHDR JPEG 样本，确认现在能正确 demux 出 HDR 效果（Stage D 里这类样本是允许退化处理的，Stage H2 要把退化路径补上）
- 同一张样本如果同时有 APP2 和 XMP 两种描述（有些工具会同时写两份保证兼容性），确认两者数值一致时结果正确；如果不一致（现实中确实可能发生），优先用 APP2 二进制（更贴近本工具自己的写入格式，视为更权威），并打日志提醒不一致

---

## 附录：给子代理的执行提示

- 每个 Stage 开始前，先读一遍对应的 `MULTI_FORMAT_PLAN.md` 章节（每个 Stage 标题里已经标注对应章节号），不要只看本文档的任务清单就动手，任务清单是"做什么"，设计文档是"为什么"，遇到任务清单没覆盖到的边界情况要回去查设计文档的原则。
- 遇到"这个细节到底该怎么处理"的犹豫时，优先级顺序：① 不破坏 JXR 现状回归 > ② 不让严格查看器打不开文件（尤其 Stage H/H2）> ③ 精度 > ④ 性能 > ⑤ 代码简洁。性能和简洁是可以后续迭代的，前三项一旦出问题会直接影响用户能不能用。
- 如果某个真实世界测试样本找不到（比如没有 iPhone/Android 设备），在验收报告里明确写"此项未测试，原因：xxx"，不要跳过不提，方便后续针对性补测。
