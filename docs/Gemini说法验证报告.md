# Gemini 说法网络验证报告

> 验证时间：2026-06-28
> 验证方式：GitHub 源码 + 官方文档交叉比对

---


## 二、HDR 增益图相关

### ISO 21496-1 标准
- 说法：苹果、谷歌、Adobe 共用的增益图元数据标准
- 结论：**正确** — ISO 21496-1:2025 已发布，Adobe 官方文档明确声明对齐该标准
- 来源：iso.org/standard/86775.html, helpx.adobe.com

### libultrahdr
- 说法：谷歌开源全链路库，含色调映射+增益图生成+JPEG/R 打包
- 结论：**正确** — README 确认 API-0 可接受仅 HDR raw 输入，内部完成 tone mapping、gain map 计算和 MPF 封装
- 来源：github.com/google/libultrahdr

### libultrahdr 默认色调映射算法
- 说法：默认基于 Hable 改进算法
- 结论：**错误** — 源码 gainmapmath.cpp 显示使用标准传输函数（HLG OETF/EOTF、PQ OETF、sRGB OETF），非 Hable 胶片曲线
- 来源：github.com/google/libultrahdr/blob/main/lib/src/gainmapmath.cpp

### JPEG/R 封装方式
- 说法：APP1(XMP) + APP2(MPF) + 二进制尾部拼接
- 结论：**正确** — MPF 在 APP2 存储增益图作为第二张 JPEG，追加在主图 EOI 之后
- 来源：github.com/google/libultrahdr

### HEIF/AVIF 增益图引用类型
- 说法：使用 ISOBMFF 容器的 `auxl` 引用类型
- 结论：**不精确** — 增益图通常使用 `dimg`（derived image）引用类型，`auxl` 用于 alpha 通道等辅助数据
- 来源：ISO 14496-12, ISO 23008-12

### AVIF 增益图格式
- 说法：存储为 YUV400（单通道灰度）
- 结论：**正确** — ultrahdr_api.h 定义 UHDR_IMG_FMT_8bppYCbCr400 = 2
- 来源：github.com/google/libultrahdr

### 增益图分辨率
- 说法：通常为基础图的 1/4
- 结论：**不精确** — 默认是全分辨率（scale factor = 1），1/4 是可选配置而非默认值
- 来源：ultrahdr_api.h

### 增益图位深
- 说法：8-bit 灰度
- 结论：**正确** — 默认 uint8 (0-255)，encodeGain() 返回值类型确认
- 来源：gainmapmath.cpp

### 增益图公式
- 说法：G = log2(HDR/SDR)
- 结论：**正确** — computeGain() 原文 `float gain = log2((hdr + kHdrOffset) / (sdr + kSdrOffset))`
- 来源：gainmapmath.cpp

### Ultra HDR 与 ISO 21496-1 关系
- 说法：是同一标准
- 结论：**错误** — Ultra HDR 是 Google 的 Android 实现，ISO 21496-1 是国际标准。两者相关但不同，libultrahdr 同时写入两种元数据
- 来源：github.com/google/libultrahdr

---

## 三、ProPhoto RGB 与 Lightroom 相关（文档二）

### ProPhoto RGB 白点
- 说法：使用 D50 白点
- 结论：**正确** — ICC 注册表明确记载 D50 色度坐标
- 来源：color.org/chardata/rgb/rommrgb.xalter

### ProPhoto RGB 传递函数
- 说法：Gamma 1.8
- 结论：**基本正确** — 严格说是分段函数：低值区线性（斜率 16），高值区按 E^(1/1.8)。工程中常简化为 Gamma 1.8
- 来源：同上

### ProPhoto 亮度权重
- 说法：Y = 0.288037×R + 0.711877×G + 0.000086×B
- 结论：**正确** — 来自 ProPhoto→XYZ (D50) 矩阵的 Y 行，ISO 22028-2:2013 确认
- 来源：ICC ProPhoto RGB profile

### Lightroom 内部色彩空间
- 说法：Melissa RGB（基于 ProPhoto RGB 原色 + 32-bit 浮点）
- 结论：**部分正确** — Adobe 确认 LR 默认使用 ProPhoto RGB 线性空间渲染，但 "Melissa RGB" 是社区非官方名称，Adobe 公开文档中未使用此名称
- 来源：helpx.adobe.com/lightroom-classic/kb/color-faq.html

### LR 导出 HDR JPEG 流程
- 说法：ProPhoto 内部→P3/sRGB JPEG 主图→对数空间计算增益图→双标准封装
- 结论：**部分正确** — Adobe 确认导出 HDR JPEG 支持 sRGB/P3/Rec.2020 色彩空间，但增益图计算细节和双标准 XMP 打包方式在公开文档中未完全证实
- 来源：helpx.adobe.com/lightroom-classic/help/hdr-output.html


