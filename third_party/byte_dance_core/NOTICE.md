# ByteDance Piano Transcription Notice

## 来源

本目录的 Python 源码来自：
https://github.com/bytedance/piano_transcription

采用提交：`1ade7dcd4348add669a67c6e6282456c8c6633bd`。

上游 README 声明许可证为 Apache-2.0。
Apache-2.0 全文位于 `LICENSE-APACHE-2.0.txt`。

## 采用文件

- `pytorch/inference.py`
- `pytorch/models.py`
- `pytorch/pytorch_utils.py`
- `utils/utilities.py`
- `utils/piano_vad.py`
- `utils/config.py`

2026-07-18 复制时，以上文件与本地上游审查快照的 SHA-256 完全一致。
此时尚未修改源码。

## 归因

Qiuqiang Kong、Bochen Li、Xuchen Song、Yuan Wan、Yuxuan Wang。

论文：High-resolution Piano Transcription with Pedals by Regressing
Onsets and Offsets Times，arXiv:2010.01815，2020。

## 后续修改

Better PianoTrans 修改了 `pytorch/inference.py` 和 `utils/utilities.py`。
推理结果按原音频时长截去补零尾部。
生成 MIDI 时限制事件终点，并在原音频终点写曲终标记。
模型权重、声学特征与推理分段保持原样。
