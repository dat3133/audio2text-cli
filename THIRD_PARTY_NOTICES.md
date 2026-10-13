# Third-party notices

Project code: Apache-2.0. Original transcription workflow derives from the user's qwen3-asr-transcribe skill; its local paths and configuration are not shipped.

| Component | Source | License / treatment |
|---|---|---|
| Qwen3-ASR weights and inference toolkit | https://github.com/QwenLM/Qwen3-ASR | Apache-2.0; retain upstream model license/card |
| Whisper model weights | https://github.com/openai/whisper | MIT; converted snapshots retain their source attribution |
| faster-whisper | https://github.com/SYSTRAN/faster-whisper | MIT |
| CTranslate2 | https://github.com/OpenNMT/CTranslate2 | MIT |
| PyTorch | https://github.com/pytorch/pytorch | BSD-style license and included notices |
| Transformers / Hugging Face Hub | https://github.com/huggingface | Apache-2.0 |
| uv | https://github.com/astral-sh/uv | MIT OR Apache-2.0; retain distribution licenses |
| imageio-ffmpeg | https://github.com/imageio/imageio-ffmpeg | BSD-2-Clause wrapper; included FFmpeg binary has its own license/build configuration |

The native CLI does not bundle model weights. Runtime initialization installs third-party packages into its isolated environment; their license files remain in package metadata. Runtime redistribution must retain all relevant notices, including FFmpeg's actual build license and NVIDIA library terms. Do not assume the wrapper's license covers its binary dependencies.

ModelScope mirrors are content-checked against the pinned source manifest. Whisper turbo uses the converted snapshot at https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo.
