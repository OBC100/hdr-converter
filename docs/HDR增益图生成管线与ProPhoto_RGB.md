# HDR 增益图生成管线与 ProPhoto RGB

> 来源：Gemini 对话 — JPEG/HEIF 压缩优化库对比 (2026-06-28)

---

## 目录

1. [HDR 增益图生成管线 — 两种方案](#一hdr-增益图生成管线--两种方案)
2. [方案一：libultrahdr（官方全链路库）](#方案一libultrahdr官方全链路库)
3. [方案二：手动自主流 — ExifTool / Exiv2](#方案二手动自主流--exiftool--exiv2)
4. [HEIF/AVIF 增益图打包（libheif + ISO 21496-1）](#三heifavif-增益图打包libheif--iso-21496-1)
5. [终极选型建议](#四终极选型建议)
6. [统一大一统流水线](#五统一大一统流水线)
7. [核心数学引擎 — 增益图生成算法](#六核心数学引擎--增益图生成算法)
8. [ProPhoto RGB — 完整改造指南](#七prophoto-rgb--完整改造指南)（含 Lightroom 实际管线）
9. [附录：关键概念速查](#八附录关键概念速查)

---

## 一、HDR 增益图生成管线 — 两种方案

### 总体架构图

```
                  【原始 HDR 数据源】
                       │
                       ▼ (统一解码并转换)
         【物理层】 32-bit Float Linear RGB (绝对物理光强)
                       │
                       ├──────────────────────────────┐
                       ▼                              ▼ (Hable 算子)
             [原始 Linear HDR]               [SDR 基础图 (Linear)]
                       │                              │
                       └──────────────┬───────────────┘
                                      ▼ (轧差 & 4倍降采样 & 8-bit量化)
                                [单通道灰度增益图]
                                      │
   ┌────────────────────────────┼────────────────────────────┐
   ▼ (分支 A: JPEG/R 适配器)    ▼ (分支 B: HEIF 适配器)      ▼ (分支 C: AVIF 适配器)
 8-bit JPEG 主图               10-bit HEVC 主图            10-bit AV1 主图
 8-bit JPEG 增益图             8-bit Mono HEVC 增益图      8-bit Mono AV1 增益图
   │                            │                            │
   ▼                            ▼                            ▼
 利用 XMP/MPF 尾部拼接        利用 ISOBMFF (auxl) 封装     利用 ISOBMFF (auxl) 封装
 (.jpg)                        (.heic)                      (.avif)
```

---

## 方案一：libultrahdr（官方全链路库）

它是一个**全链路闭环库**。你只需要把原始的 Linear HDR 像素阵列（RGBA_RGBA 浮点数）或者 PQ/HLG 数据丢给它，它内部会自动运行：

1. 自动运行色调映射（默认基于 Hable 改进算法）生成 SDR 基础图。
2. 自动生成并降采样增益图。
3. **最终的核心功能**：自动调用 JPEG 编码器，并把 XMP 元数据、MPF（多图片格式）标记区和两张图的字节流自动打包，直接吐给你一个标准的 `.jpg`（JPEG/R）成品。

**适用场景**：从零构建一条标准、高性能、工业级的 HDR 图像生成管线。

---

## 方案二：手动自主流 — ExifTool / Exiv2

如果你前面坚持"不想要庞大的官方库"，并且已经自己用 Hable 或 BT.2446 算法在内存里算出了 `Base.jpg` 和 `GainMap.jpg`，那么你只需要一个**元数据注入与容器打包工具**。

在这种情况下，你需要的"算法"其实是 **ISO 21496-1 / Adobe Gain Map 封装规范**。

### 核心封装算法（打包三步法）

无需底层魔改，任何能操作 JPEG 段（Segments）的库都可以实现以下算法逻辑：

1. **注入 XMP 元数据（APP1 标记区）**：在 `Base.jpg` 的头部注入一段标准的 XMP 文本，声明"我是一个 Ultra HDR 容器，我的尾部藏着一张增益图，它的最大提亮倍数是 N"。

2. **构建 MPF 索引（APP2 标记区）**：写入多图片格式（Multi-Picture Format）索引，精确标注副图（增益图）在最终文件里的**字节偏移量（Offset）和大小（Size）**。

3. **二进制尾部拼接**：将 `GainMap.jpg` 的全部二进制字节，直接追加（Append）到 `Base.jpg` 的文件末尾。

### 推荐使用的轻量库/工具

#### 1. 命令行/脚本流：ExifTool (Phil Harvey)

这是整个图像工程界的"瑞士军刀"。如果你在写 Python、Node.js 或者是全自动化脚本，直接调用 ExifTool 是最稳妥的捷径。

- **实现方式**：你只需要写一个标准的 XMP 配置文件（.args），然后运行一行命令，ExifTool 会自动帮你计算增益图的大小、自动生成 MPF 索引并完成二进制合并。

#### 2. C++ / 嵌入式流：Exiv2

如果你在写 C++ 且不想引入谷歌庞大的 `libultrahdr`，`Exiv2` 是专门用来高效读写图像元数据的轻量库。它允许你在内存中精准地往 JPEG 的 APP1/APP2 段里塞入合法的 XMP 结构体。

#### 3. Python 纯代码流：python-xmp-toolkit + 传统二进制操作

- 用 `python-xmp-toolkit`（底层依赖 Exempi）将 Adobe/Google 规范的增益图元数据写入基础图。
- 用 Python 的标准文件操作 `open('output.jpg', 'wb')`，先写基础图字节，再写增益图字节。

---

## 三、HEIF/AVIF 增益图打包（libheif + ISO 21496-1）

### C 代码示例

```c
heif_context* ctx = heif_context_alloc();
heif_image* base_img = ...; // 你的 10-bit SDR Hable 像素
heif_encoder* encoder_10bit = ...;
heif_context_encode_image(ctx, base_img, encoder_10bit, ..., &base_item_handle);

// 2. 添加增益图
heif_image* gain_img = ...; // 你的 8-bit 1/4 尺寸灰度增益图
heif_encoder* encoder_8bit_mono = ...; // 灰度编码器
heif_item_id gain_item_id;
heif_context_encode_image(ctx, gain_img, encoder_8bit_mono, ..., &gain_item_handle);

// 3. 建立纽带：将增益图绑定为主图的辅助图 (auxl)
heif_context_add_item_reference(ctx, gain_item_handle, "auxl", base_item_handle);

// 4. 写入 ISO 21496-1 标准元数据
struct heif_gain_map_metadata meta;
meta.max_gain_nits = 1000.0; // 举例
heif_context_set_gain_map_metadata(ctx, gain_item_handle, &meta);

// 5. 保存文件，大功告成
heif_context_write_to_file(ctx, "output.avif");
```

### 泛化后的终极红利

一旦你把管线切到了 HEIF/AVIF 上，你会享受到 JPEG 时代不可同日而语的优势：

1. **彻底告别暗部断层**：基础图终于可以用上原生的 10-bit 规格，SDR 下的平滑渐变美轮美奂。
2. **极高的压缩率**：HEVC/AV1 的帧内压缩效率是老旧 JPEG 的 2~3 倍。一张全血满规格的 **10-bit基础 + 8-bit增益** 的 AVIF 照片，体积可能只有同等画质 JPEG/R 的 **40%** 左右。
3. **生态全面通车**：苹果的相册天然支持 HEIC 容器的增益图（Apple HDR 摄影的基础），而 Chrome/Android 生态对 AVIF 增益图的支持也早已在底层统一到了 ISO 21496-1 标准下。

---

## 四、终极选型建议

| 维度 | libultrahdr (C++) | ExifTool (CLI) | Exiv2 (C++) | python-xmp-toolkit |
|------|-------------------|----------------|-------------|---------------------|
| 性能 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| 灵活度 | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 集成难度 | 高（C++ 编译链） | 低（命令行） | 中 | 低 |
| 适用场景 | 工业级高性能管线 | 脚本自动化 | 嵌入式 C++ | Python 原型验证 |

---

## 五、统一大一统流水线

### 核心步骤：三段式大一统流水线

#### 1. 核心数学引擎（The Core Engine）

在内存中，图像统一表达为浮点数矩阵。利用你确定下来的 **Hable 算子**，核心算法逻辑只写一次：

```python
def core_gainmap_engine(linear_hdr_rgb):
    # Step 1: 一刀切生成 SDR 基础图
    sdr_base_linear = hable_tone_mapping(linear_hdr_rgb)
    
    # Step 2: 计算增益图 (在对数域进行差值计算)
    # 公式：G = log2(HDR / SDR)
    # 为防止分母为0，引入微小微量 epsilon
    gain_map_linear = (linear_hdr_rgb + 1e-5) / (sdr_base_linear + 1e-5)
    gain_map_log = log2(gain_map_linear)
    
    # Step 3: 提取亮度通道并降采样 (统一转为单通道灰度图)
    gain_map_gray = transform_to_monochrome(gain_map_log)
    gain_map_scaled = downsample_4x(gain_map_gray)
    
    # Step 4: 将数值量化到 8-bit (0-255)
    # 同时导出一组在容器层通用的元数据：Max_Gain 和 Min_Gain
    gain_map_8bit, metadata = quantize_to_8bit(gain_map_scaled)
    
    return sdr_base_linear, gain_map_8bit, metadata
```

#### 2. 分支编码与规格自适应

当核心引擎吐出 `sdr_base_linear`（10-bit 级别精度的 SDR 物理光强）、`gain_map_8bit`（单通道灰度图）和 `metadata` 后，流水线根据输出格式分流：

- **若输出为 JPEG/R**：
  - 将 `sdr_base_linear` 进行标准的 Gamma 2.2 转换，量化为 **8-bit RGB**，送入 JPEG 编码器。
  - 将 `gain_map_8bit` 直接送入 JPEG 编码器。

- **若输出为 HEIF/AVIF**：
  - 将 `sdr_base_linear` 进行 BT.709 或 P3 的 Gamma 转换，保留 **10-bit 精度**（转为 YUV 4:2:0），送入 HEVC (`x265`) 或 AV1 (`libaom`) 编码器。
  - 将 `gain_map_8bit` 送入支持 **Monochrome（纯灰度）** 规格的编码器，以极低的码率暴力压缩。

#### 3. 统一基于 ISO 21496-1 标准的元数据封装

这是能将流水线统一的最强工程保障。**ISO 21496-1** 是现代图像行业通用的增益图元数据标准（无论是苹果、谷歌、还是 Adobe 均已对齐该标准）。

无论你用什么容器，写入的元数据核心字典是完全一致的：

```json
{
  "GainMapVersion": "1.0",
  "BaseIsSDR": true,
  "GainMapMin": -3.0,
  "GainMapMax": 3.0,
  "Gamma": 1.0,
  "OffsetSDR": 0.015625,
  "OffsetHDR": 0.015625
}
```

- **在 JPEG 适配器中**：将这个 JSON 转化为 XMP 字符串，塞进 JPEG 的 `APP1` Marker 段。
- **在 HEIF/AVIF 适配器中**：将这个 JSON 序列化为二进制数据块（Binary Blob），塞进 ISOBMFF 的 `auxC` 盒子里。

### 统一流水线的工程红利

1. **算法维护成本极低**：未来如果你想对色调映射进行微调（比如优化 Hable 算子的暗部细节），你**只需要修改核心数学引擎这一处地方**，JPEG、HEIF 和 AVIF 的输出画质会同步提升，绝无"修 A 破 B"的风险。

2. **极高的数据纯净度**：由于底层增益图全部收拢为单通道灰度（Monochrome），在转向 HEIF/AVIF 时，现代编码器对纯灰度矩阵的无损/高压缩比优化可以发挥到极致，你的 `.avif` 文件的副轨体积会小到可以忽略不计。

---

## 六、核心数学引擎 — 增益图生成算法

### ISO 21496-1 增益图计算

```python
def generate_iso_gainmap(linear_hdr_rgb, linear_sdr_rgb):
    # 1. 声明防零溢出常量
    k_sdr = 1e-5
    k_hdr = 1e-5
    
    # 2. RGB 转单通道亮度 (以 Rec.709 权重为例)
    weights = np.array([0.2126, 0.7152, 0.0722])
    y_hdr = np.dot(linear_hdr_rgb[..., :3], weights)
    y_sdr = np.dot(linear_sdr_rgb[..., :3], weights)
    
    # 3. 核心对数求商算法
    g_raw = np.log2((y_hdr + k_hdr) / (y_sdr + k_sdr))
    
    # 4. 健壮地提取极值 (排除 0.1% 的极端噪点)
    g_min = float(np.percentile(g_raw, 0.1))
    g_max = float(np.percentile(g_raw, 99.9))
    
    # 5. 归一化并裁剪到 0~1
    g_norm = (g_raw - g_min) / (g_max - g_min)
    g_norm = np.clip(g_norm, 0.0, 1.0)
    
    # 6. 量化为 8-bit 整数矩阵 (后续可直接送去降采样和编码)
    g_8bit = np.floor(g_norm * 255.0 + 0.5).astype(np.uint8)
    
    # 7. 导出必须写进容器元数据的核心参数
    metadata = {
        "GainMapMin": g_min,
        "GainMapMax": g_max,
        "BaseOffset": k_sdr,
        "AlternateOffset": k_hdr
    }
    
    return g_8bit, metadata
```

### 最后的工程闭环

当你拿到了 `g_8bit` 和 `metadata` 字典：

- **`g_8bit`**：把它降采样后送入任意最基础的编码器，压出来存作文件。
- **`metadata`**：里面的 `GainMapMin` 和 `GainMapMax` 就是在合体时，需要利用 `ExifTool` 或 `libheif` 强行写进图片属性里的数据。

> 这四个元数据参数是**绝对不能丢的**。因为解码端（比如苹果手机屏幕）在反向恢复 HDR 时，必须通过这几个参数知道当初归一化时"缩放了多少倍"，才能用反向公式完美的把 HDR 画面还原出来。

---

## 七、ProPhoto RGB — 完整改造指南

> 以下是 Gemini 回答用户关于"能否用 ProPhoto RGB 作为基础图色域来生成 HDR 增益图"的完整回复。Gemini 先说明了 ProPhoto RGB 的特性，然后给出了完整的改造方案，最后解释了 Lightroom 实际如何处理这个问题（因为 Lightroom 内部就是基于 ProPhoto RGB 工作的）。

### ProPhoto RGB 的特性与挑战

将基础图（Base SDR）指定为 **ProPhoto RGB** 来拼合 Ultra HDR（Gain Map）图像，是一个非常硬核且极具远见的色域选择（Adobe Lightroom 内核在处理 HDR 导出时就大量采用类似逻辑）。

但由于 ProPhoto RGB 的色彩空间过于巨大（包含了大约 90% 的可见光谱甚至一些虚构色），并且使用了 **D50 白点** 和 **Gamma 1.8** 的传递函数，你在构建这条大一统流水线时，**必须对底层的数学公式做出重大修正。**

如果你强行套用普通的 sRGB/P3 流水线，拼出来的图像在解码时会出现严重的偏色（尤其是蓝色通道）或高光溢出。

> **注意**：JPEG/R 容器是可以支持 ProPhoto RGB 的（Lightroom 的导出管线就是明证）。以下是 ProPhoto RGB 流水线所需的修正步骤。

### 第一步：换掉亮度系数

在之前计算单通道灰度增益图时，我们用的是 Rec.709 权重。但在 ProPhoto RGB（ROMM RGB 标准）中，红绿蓝三原色的物理位置发生了巨大偏移，其 **CIE 1931 XYZ 变换矩阵的 Y 通道（亮度）系数变成了极其极端的数值**：

$$Y_{\text{ProPhoto}} = 0.288037 \times R + 0.711877 \times G + 0.000086 \times B$$

#### 计算 ProPhoto 亮度权重的代码

```python
import numpy as np

# ProPhoto RGB 的 XYZ 原色矩阵 (D50 白点)
xyz_to_prophoto = np.array([
    [ 1.34595631, -0.25560998, -0.05111226],
    [-0.54459674,  1.50816142,  0.02053506],
    [ 0.        ,  0.        ,  1.21184464]
])

prophoto_to_xyz = np.linalg.inv(xyz_to_prophoto)
print("ProPhoto to XYZ matrix:\n", prophoto_to_xyz)
print("Luminance row (Y):", prophoto_to_xyz[1])
```

**代码输出：**

```
ProPhoto to XYZ matrix:
 [[7.97667235e-01 1.35192230e-01 3.13525293e-02]
 [2.88037454e-01 7.11876881e-01 8.56634334e-05]
 [0.00000000e+00 0.00000000e+00 8.25188285e-01]]
Luminance row (Y): [2.88037454e-01 7.11876881e-01 8.56634334e-05]
```

#### ProPhoto 专用增益图生成

```python
def generate_prophoto_gainmap(linear_hdr_prophoto, linear_sdr_prophoto):
    # 1. 使用 ProPhoto (ROMM RGB) 专用的亮度权重
    prophoto_weights = np.array([0.288037, 0.711877, 0.000086])
    
    y_hdr = np.dot(linear_hdr_prophoto[..., :3], prophoto_weights)
    y_sdr = np.dot(linear_sdr_prophoto[..., :3], prophoto_weights)
    
    # 2. 对数空间轧差 (防零溢出)
    g_raw = np.log2((y_hdr + 1e-5) / (y_sdr + 1e-5))
    
    # 3. 健壮提取极值并归一化
    g_min, g_max = float(np.percentile(g_raw, 0.1)), float(np.percentile(g_raw, 99.9))
    g_norm = np.clip((g_raw - g_min) / (g_max - g_min), 0.0, 1.0)
    
    # 4. 4倍降采样并量化为 8-bit 单通道灰度图
    # (降采样通常使用 OpenCV 的 resize 或高斯金字塔)
    gain_map_8bit = (g_norm * 255.0 + 0.5).astype(np.uint8)
    
    return gain_map_8bit, g_min, g_max
```

### 第二步：基础图进行 Gamma 1.8 编码

千万别对基础图施加常规的 Gamma 2.2。标准的 ProPhoto RGB 传递函数（EOTF）是 **Gamma 1.8**（在极暗部有一小段斜率为 16 的线性尾巴，但在实际工程中通常直接用 $1/1.8$ 次方来做近似非线性编码）：

$$\text{SDR}_{\text{ProPhoto}} = (\text{sdr\_base\_linear})^{1/1.8}$$

### 第三步：AVIF 打包（libavif C 代码）

```c
avifEncoder* encoder = avifEncoderCreate();
avifImage* avif_main = avifImageCreate(width, height, 10, AVIF_PIXEL_FORMAT_YUV420);

// 【核心魔术】：强行给主图塞入 ProPhoto RGB 的 ICC Profile
// 解码器读到这个文件时，会根据这个 ICC 知道这是 ProPhoto 色域，并正确做色管理
FILE* icc_file = fopen("ProPhotoRGB.icc", "rb");
// 读取 icc_data 和 icc_size ...
avifImageSetProfileICC(avif_main, icc_data, icc_size);

// 2. 将 Gamma 1.8 的 ProPhoto SDR 像素填入 avif_main 并编码...
avifEncoderAddImage(encoder, avif_main, ...);

// 3. 创建辅助图像 (Gain Map)
avifImage* avif_gainmap = avifImageCreate(width/4, height/4, 8, AVIF_PIXEL_FORMAT_YUV400); // 纯灰度
// 填入前面算出来的 gain_map_8bit 并编码为辅助条目 (Auxiliary Item)
// 声明它的关联类型为 "auxl"

// 4. 根据 ISO 21496-1 规范，将 g_min 和 g_max 写入 auxC 盒子中
avifGainMapMetadata iso_meta;
iso_meta.gainMapMin[0] = g_min;
iso_meta.gainMapMax[0] = g_max;
iso_meta.baseIsSDR = AVIF_TRUE;
avifEncoderSetGainMapMetadata(encoder, &iso_meta);

// 5. 输出最终的全血版 ProPhoto RGB AVIF 图像
avifEncoderWrite(encoder, avif_main, output_bytes);
```

### 终极成果：Adobe 标准的完美闭环

通过这种方式拼合出来的 AVIF/HEIF 图像，其精妙之处在于：

1. **SDR 状态下**：当图片被发送到不支持 HDR 的老设备或者普通的 8-bit 屏幕上时，色彩管理系统（CMS）会精准识别主图里嵌入的 **ProPhoto RGB ICC**，并将其完美转换到当前屏幕的色域展示，绝对不会偏色。

2. **HDR 状态下**：当你在最新的高亮显示器或最新版 macOS/Windows 的 Lightroom、Photoshop 中打开它时，系统会提取增益图，并**使用你写进元数据的 `g_min` / `g_max` 以及 ProPhoto 逆矩阵**，将画面一路狂飙恢复到上千 nits 的原生超广色域 HDR 状态。

### Lightroom 如何处理 ProPhoto RGB + JPEG/R

> 以下是 Gemini 进一步解释 Lightroom 实际如何处理 ProPhoto RGB 的回复（因为用户追问了 LR 的做法）。用户反驳了 Gemini 关于"JPEG/R 不支持 ProPhoto RGB"的说法，指出 LR 本身就是这么做的。

#### LR 真正的四步导出管线

Lightroom 在后台偷偷跑了以下四步，成功地将 ProPhoto RGB 内部处理与 JPEG/R 导出结合：

**1. 内部高位深冲洗（Melissa RGB）**

当你把 Raw 文件拉进 LR 的修改图片模块并开启 HDR 时，LR 确实是在一个基于 ProPhoto RGB 原色、但拥有 **32位浮点精度** 的内部色彩空间（Melissa RGB）中进行所有高光、暗部和曲线的算法运算。在这个阶段，绝对不会有色彩断层。

**2. 导出时的"色域智能压缩"**

当你点击导出并选择 `HDR P3` 或 `HDR Rec. 2020` 时，LR 开始执行收拢算法：

- 它把你辛辛苦苦调整好的 32位 宽色域画面的 **SDR 部分**（通常是 0-100 nits 的部分），利用感知映射（Perceptual Mapping）压缩并转换到 **8-bit P3 或 sRGB** 空间，作为 JPEG 的主图。因为 P3 空间小得多，256 个台阶足够平滑，断层消失。

**3. 在对数空间计算 Adobe Gain Map**

接着，LR 拿内存中原始的 32位 宽色域 HDR 数据，与刚刚降维转换出来的 8-bit SDR 主图数据，进行我们在前一问提到的**对数空间轧差运算（Logarithmic Ratio）**，算出一张高度平滑的单通道灰度增益图，并同样压成 8-bit。

**4. 完美打补丁：双标准封装**

最后，LR 将这两部分二进制流打包。为了让全世界的设备都能读懂，Adobe 采用了一个极其聪明的做法：它在 JPEG 的元数据里**同时写入了两种行业标记**：

- 塞进符合 **Adobe 规范** 的 XMP 元数据；
- 塞进符合 **Google Ultra HDR 规范** 的 XMP 元数据。

#### LR 内部色彩空间转换流程

在计算增益图之前，流水线的第一步，就是通过一个 $3 \times 3$ 的色域转换矩阵（Color Space Conversion Matrix），**将原始 HDR 图像的每一个像素，全部转换到 Linear ProPhoto RGB 空间**。

- 转换后，图像依然保留着 32-bit 浮点数的高动态范围（高光部分数值依然远大于 1.0）。
- 此时，我们得到了：$\text{HDR}_{\text{Linear\_ProPhoto}}$。

**在同一个空间内做 Tone Mapping（调色与映射）：**

在这个已经对齐的 Linear ProPhoto 空间内部，Lightroom 运行它的色调映射算法，把高动态范围压下来，生成一张适合 0-100 nits 显示的 SDR 图像。

- 因为是在同一个空间内计算，此时我们得到了：$\text{SDR}_{\text{Linear\_ProPhoto}}$。

#### HDR 解码还原流程图

```
【文件输入】
     │
     ├──> 【头部主图 (JPEG)】 ──> 读取 ProPhoto ICC ──> 应用 Gamma 1.8 逆函数
     │                                                        │
     │                                                        ▼
     │                                          【Linear SDR_ProPhoto】
     │                                                        │
     │                                                        │
                                                                  │
【尾部增益图 (Gain Map)】 ───> 读取元数据并反归一化 ───────────────> 乘以
                                                                  │
                                                                  ▼
                                                   【生成 Linear ProPhoto HDR】
                                                                  │
                                     通过 3x3 矩阵转换到当前显示器色域 (如 BT.2020 PQ)
                                                                  │
                                                                  ▼
                                                     【屏幕亮瞎，完美呈现】
```

#### 解码步骤详解

1. **解压基础图**：软件读取 JPEG 主图，发现有 ProPhoto ICC，于是应用 **Gamma 1.8 的逆函数**，把像素吐回到内存里，形成 $\text{Linear SDR}_{\text{ProPhoto}}$。

2. **应用增益图**：软件提取尾部的增益图，根据元数据恢复出原始的对数差值，然后**直接乘以**刚才解出来的 $\text{Linear SDR}_{\text{ProPhoto}}$，从而**一路狂飙恢复到上千 nits 的原生超广色域 HDR 状态**。

---

## 八、附录：关键概念速查

| 概念 | 说明 |
|------|------|
| **ISO 21496-1** | 现代图像行业通用的增益图元数据标准，苹果、谷歌、Adobe 均已对齐 |
| **Hable 算子** | 色调映射算法，用于将 HDR 压缩为 SDR |
| **对数空间轧差** | 计算增益图的核心公式：`G = log2(HDR / SDR)` |
| **Melissa RGB** | Lightroom 内部使用的 ProPhoto RGB 原色 + 32-bit 浮点精度的处理空间 |
| **MPF (Multi-Picture Format)** | JPEG 多图片格式索引，用于标注增益图的字节偏移量和大小 |
| **ISOBMFF (auxl)** | HEIF/AVIF 容器中用于关联辅助图像（增益图）的机制 |
| **Gamma 1.8** | ProPhoto RGB 标准的传递函数（EOTF），与 sRGB/P3 的 Gamma 2.2 不同 |
| **D50 白点** | ProPhoto RGB 使用的参考白点（色温约 5000K），与 D65 不同 |
| **XMP 元数据** | 嵌入 JPEG APP1 段的 XML 元数据，声明增益图参数 |
| **auxC 盒子** | ISOBMFF 容器中存储增益图元数据的二进制数据块 |
