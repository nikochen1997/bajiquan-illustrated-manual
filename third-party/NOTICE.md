# 第三方来源与许可说明

本项目的 MIT 声明只覆盖有权授予许可的本项目代码和原创贡献，不改变下列工具、模型、参考视频或截图原有的许可。完整历史归档保留原始文件；它们的公开保存不表示第三方素材也被重新授予 MIT。

## RIFE / rife-ncnn-vulkan

- 本地版本：`rife-ncnn-vulkan-20221029-macos`；当前实际使用模型为 `rife-v4.6`，原始 ZIP 另含其他模型版本。
- 上游与原发行包：[nihui/rife-ncnn-vulkan，20221029](https://github.com/nihui/rife-ncnn-vulkan/releases/tag/20221029)。
- 随原包的 LICENSE 为 MIT，署名 `Copyright (c) 2020 nihui`。原文及原 README 已复制保留于 [rife-ncnn-vulkan-20221029/](rife-ncnn-vulkan-20221029/)，原始归档内也保留原文件。
- 上游 README 另列 RIFE 研究实现、ncnn、libwebp、stb、dirent 等来源。本地包未为每套预训练模型和所有内嵌依赖单列许可，未将它们一概声明为本项目 MIT，也未把第三方二进制或模型署为本项目原创。

## yt-dlp

- 实际本地可执行版本：`2026.10.21`，已执行 `--version` 核对；下载元数据记录提交 `8cdf4fea48ecf4064387375301d7f294ebdb17c1`。
- 原文件：`work/八极拳完整篇/tools/yt-dlp_macos`；SHA-256：`7a6fe71d7d680f1fb0ac18d4949873d77db2859e90017c0f44bd94b23be4ee40`，与同目录原下载校验清单一致。
- [准确版本源码](https://github.com/yt-dlp/yt-dlp/tree/8cdf4fea48ecf4064387375301d7f294ebdb17c1)；[对应发行版](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.10.21)。
- yt-dlp 自身源码为 Unlicense，但官方说明 PyInstaller 独立可执行包含 GPLv3+ 组件，组合体按 GPLv3+ 分发，不能只套用源码的 Unlicense 或本项目 MIT。参见[官方发行文件许可说明](https://github.com/yt-dlp/yt-dlp#licensing)。
- 已按上述准确提交下载并原样保留 [LICENSE](yt-dlp-2026.10.21/LICENSE) 和 [THIRD_PARTY_LICENSES.txt](yt-dlp-2026.10.21/THIRD_PARTY_LICENSES.txt)。后者含内嵌组件的许可文本、原项目与源码获取说明；对应版本全部源文件也可从上述源码链接核对。未修改原可执行文件。

## 参考动作视频与截图

- 完整主套路：[hsu363《小八極》](https://www.youtube.com/watch?v=nfBG8Je-wVY)。
- 初期撑拳样片：[国家体育总局《居家健身教学视频—八极拳》](https://www.sport.gov.cn/n20001280/n20001265/n20066978/c20287390/content.html)，页面注明来源吉林省体育局；视频片头为《八极拳健身操》。
- 独立补充：WuTan NJ + BajiShu 的[两仪顶](https://www.youtube.com/watch?v=mlGl7kjr1NA)、[收势献肘](https://www.youtube.com/watch?v=DnzZKZXqZ7U)、[铁山靠](https://www.youtube.com/watch?v=gVWY7j2HFyg)。
- `source/wu-first240.mp4` 与同目录 `wu-alternative.info.json` 属于曾比较的 Bilibili 备选片段，不是最终35段主套路来源。

这些第三方参考素材用于来源追溯和制作历史；本地资料中未见其另行授予本项目 MIT 或通用再分发许可。原作者、来源和第三方权利保留；不要将参考素材、原视频人物或历史备选当成本项目原创。AI重绘和神经补间为视觉观摩材料，不代表原作者背书或拳师校订。

## 外部运行环境

Python、NumPy、Pillow、OpenCV、Node.js、FFmpeg/ffprobe 与 macOS 系统字体是运行环境依赖，按其各自许可使用。项目引用系统 Songti/Helvetica 字体路径，未将系统字体文件作为本项目素材打包，也不授予字体再分发权。完整历史中的本机绝对路径仅为当时运行记录；复用前应修改清单输出路径和旧样片脚本根目录。
