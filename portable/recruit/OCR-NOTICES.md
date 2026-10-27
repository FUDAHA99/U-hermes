# 本地 OCR 依赖、模型与许可

招聘版使用 RapidOCR 3.4.2、ONNX Runtime 1.23.2（CPU）、PDFium / pypdfium2
5.14.0、Pillow 12.3.0（与锁定版 Hermes 一致）和 OpenCV 4.12.0.88。所有推理和图片处理在本机进行，
不使用云 OCR，也不需要另外安装 tesseract、Ghostscript 或 LibreOffice。

## 选型

- RapidOCR 的 PP-OCRv4 中文 mobile 模型可以兼顾中文与英文，在 CPU 上串行运行；
  不需要 PaddlePaddle 或 PyTorch。选择固定版 3.4.2，是因为它的 wheel 包含三个
  所需 ONNX 模型，模型共 **16,189,007 字节（15.44 MiB）**，且允许 Python 3.13。
  [固定版元数据与 wheel 哈希](https://pypi.org/project/rapidocr/3.4.2/)。
- 旧包 rapidocr-onnxruntime 1.4.4 虽也包含轻量模型，但元数据要求 Python <3.13，
  与本项目嵌入解释器不符；官方也在逐步退役旧的后端专用包。
  [旧包元数据](https://pypi.org/project/rapidocr-onnxruntime/1.4.4/)、
  [官方安装说明](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/install/)。
- 已检查 RapidOCR 3.10.0 wheel：改为 PP-OCRv6 small 模型，三个模型共
  31,749,509 字节，约为本方案两倍。当前针对低配置便携包保留 v4；将来升级时
  必须重新测量质量、时间和峰值内存，不能直接更换版本号。
- Tesseract/OCRmyPDF 需要额外的本地程序与语言包；EasyOCR 的 PyTorch 运行栈更重。
  本方案直接打包 Python wheel 中的原生库。PDFium 有预编译 wheel，按页渲染并
  显式释放页面和位图；相较需要额外评估 AGPL / 商业授权的 MuPDF，本方案更易
  随现有便携包分发。[PDFium 包装器说明](https://pypdfium2.readthedocs.io/en/stable/readme.html)。

## 离线携带与构建

`requirements.txt` 固定下载 RapidOCR wheel，并校验 URL 中的 SHA-256。
`ocr-models.json` 记录每个模型的文件名、大小及 SHA-256，安装、构建和运行时
都校验实际文件。模型存放在便携 Python 环境的 `site-packages/rapidocr/models/`；
不需要提交进 Git，也不触及 GitHub 单文件 100 MB 限制。

启动 OCR 时只传本地模型绝对路径，路径由当前安装位置计算；禁止库自动下载
模型、字典或可视化字体。结果只取文字，不生成依赖额外字体的 OCR 标注图。
组件缺失或损坏会提示管理员修复，不会在 HR 上传时临时联网补包。

Windows 的 ONNX wheel 需要 VC 运行库。固定 `msvc-runtime==14.44.35112` 的
wheel 将 DLL 放在便携虚拟环境解释器旁的 `Scripts/`，无需管理员权限或系统安装。
适配器校验四个关键 DLL 的固定哈希，再把这个目录显式加入 DLL 搜索路径。
来源是 [msvc-runtime 14.44.35112](https://pypi.org/project/msvc-runtime/14.44.35112/)
的 Windows cp313/x64 wheel，其 URL / SHA-256 也记在 manifest 中。

Windows cp313/x64 全部依赖已在 Linux 上用 uv 的目标平台模式下载并解包核对：
新增依赖（排除 Hermes 原有 Pillow、requests、PyYAML 等共享包）约 **246.93 MiB**，
ZIP deflate-6 约 **91.19 MiB**，含模型及 VC DLL。包含共享基础包的完整 OCR 依赖
集合约 265.37 MiB。这是依赖目录的模拟增量，完整发布包还会受已有依赖、压缩方式
和清理步骤影响；Windows 实际加载与移动盘符仍以该平台 CI / 真机验收为准。

## 许可与归属

以下是主要组件的许可索引；完整条款及内含第三方库条款随已安装 wheel 的
`*.dist-info` 目录保留。打包时不要删除这些许可证文件。

| 组件 | 许可 / 归属 |
| --- | --- |
| RapidOCR 3.4.2 | Apache-2.0，RapidOCR Authors；该 wheel 未包含完整 LICENSE，已补充同版本上游的 [Apache-2.0.txt](licenses/Apache-2.0.txt) |
| PP-OCRv4 检测、识别模型与 v2 方向分类模型 | PaddleOCR / Baidu 模型，经 RapidOCR 转为 ONNX；Apache-2.0，模型版权属于上游权利人，U-Hermes 未改模型 |
| ONNX Runtime 1.23.2 | MIT，Microsoft；保留 wheel 中的 LICENSE 与第三方通知 |
| pypdfium2 5.14.0 | Apache-2.0 或 BSD-3-Clause；PDFium 为 BSD 风格许可，wheel 附带各原生依赖的 BUILD_LICENSES |
| Pillow 12.3.0 | MIT-CMU；另有内含图片编解码库许可 |
| OpenCV 4.12.0.88 | Apache-2.0；保留 LICENSE-3RD-PARTY.txt，包含 FFmpeg 等分发组件条款 |
| NumPy 2.2.6 | BSD-3-Clause；wheel 中 BLAS / 编译器运行库另有许可与运行库例外 |
| Shapely / GEOS | Shapely BSD-3-Clause；所含 GEOS LGPL-2.1-or-later，以原 wheel 动态库形式保留 |
| pyclipper、OmegaConf、SymPy 等 | 各自 MIT / BSD 等许可，详见对应 dist-info |
| Windows VC runtime | Microsoft 专有可再分发条款，**不是开源许可**；保留 msvc_runtime 的许可证，不修改 DLL；再分发遵循 Visual Studio 的 Distributable Code 条款 |

官方来源：
[RapidOCR v3.4.2 LICENSE](https://github.com/RapidAI/RapidOCR/blob/v3.4.2/LICENSE)、
[PaddleOCR LICENSE](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE)、
[RapidOCR 模型归属说明](https://github.com/RapidAI/RapidOCR/blob/main/python/MODEL_LICENSES.md)、
[ONNX Runtime 安装要求](https://onnxruntime.ai/docs/install/)、
[Microsoft 本地目录部署与再分发条款](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170)。

此清单不改变上游许可证。未打包额外字体、真实简历、第三方样例或联网识别服务。

## 本机性能记录（2026-10-26）

以下是 Pillow 12.0.0 环境的历史测量；Windows 完整构建将共享依赖对齐为
Hermes 要求的 Pillow 12.3.0，实际性能以该构建在目标机器上的复测为准。

Linux x86_64 / i5-10210U / Python 3.13.16；单线程 CPU，逐份新进程，
包含导入、模型校验、渲染及识别。下表耗时为三次墙钟中位数；峰值为三次
整子进程 RSS 最大值，非仅 Python 分配。基准命令见开发说明及
`tools/benchmark_recruit_ocr.py`（仓库内运行）。

| 演示文件 | 文字 / OCR 页数 | 耗时 | 峰值 RSS |
| --- | --- | ---: | ---: |
| 01 文字 PDF | 1 / 0 | 0.428 秒 | 38.4 MiB |
| 06 单页扫描 PDF | 0 / 1 | 10.895 秒 | 451.9 MiB |
| 07 两页混合 PDF | 1 / 1 | 9.089 秒 | 454.2 MiB |
| 08 倾斜阴影照片 | 0 / 1 | 9.708 秒 | 445.1 MiB |

另将 06 扫描件复制为 **20 个独立页面/图片对象**，文件 4,807,045 B，
受限进程实际识别 20 页耗时 **166.270 秒**，峰值 **562.6 MiB**，提取 9339 字符。
模型在同文件中复用，未并行处理页面；该样本仍在 180 秒预算内，但已接近上限。

对 i3-9100F + 8GB 的保守容量估算是单页含启动 10–25 秒、worker 典型
RSS 约 0.45–0.7 GiB。它不是该机器实测；U 盘速度、其它程序和复杂版面会影响结果。
长扫描件可能超时，建议拆为 2–5 页；不能把 20 页上限理解为任意 20 页都能
按时完成。实际 Windows 加载、i3 性能和真实照片质量仍需现场验收。
