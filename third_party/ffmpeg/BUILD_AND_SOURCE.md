# FFmpeg 8.1.2 构建与源码

## 快速理解

此文件对应 Better PianoTrans 私有目录里的 ffmpeg.exe。
它与 Qt Multimedia 自带的 FFmpeg DLL 是两套组件。
两套组件要分别核对许可证和构建来源。

## 核心流程

~~~mermaid
flowchart LR
    A[FFmpeg 8.1.2 源码] --> B[校验源码 SHA-256]
    B --> C[运行构建脚本]
    C --> D[校验 ffmpeg.exe SHA-256]
~~~

## 构建输入与输出

| 项 | 值 |
| --- | --- |
| 上游源码 | https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz |
| 源码 SHA-256 | 464BEB5E7BF0C311E68B45AE2F04E9CC2AF88851ABB4082231742A74D97B524C |
| 构建脚本 | tools/build_ffmpeg_lgpl.sh |
| 编译环境 | MSYS2 UCRT64，GCC 16.1.0 |
| 二进制 SHA-256 | A5F5FCDD4A5C6C6DB0CFB302DB19E668C875FE38A1E216BB0E07D8A08EED7D85 |
| 许可证 | third_party/ffmpeg/LICENSE-LGPL-2.1.txt |

脚本禁用 GPL、version3、nonfree 和自动检测组件。
完整配置选项保存在构建脚本里。
测试包内 ffmpeg.exe 的 buildconf 与该脚本一致。

## 参考：发行要求

发行渠道须提供对应的源码包与本构建脚本。
项目缓存中的源码包已核对上表 SHA-256。
上游源码地址也须写入发行说明。
还须保留 FFmpeg 版权信息与 LGPL 文本。
如果二进制哈希变化，重新核对构建与许可。

### 常见问题

**Q：Qt 的 avcodec DLL 也用这份脚本吗？**
A：不用。它来自 PySide6 wheel，须单独核对。

### 排错指南

若源码哈希不符，停止构建并重新获取上游原件。
若二进制哈希不符，保存 buildconf 并重跑格式测试。
