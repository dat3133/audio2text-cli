# audio2text

本地语音转文字 CLI：多步初始化向导、硬件感知的模型推荐，以及 agent 可直接解析的 JSON。

项目目前为 **0.1.0 开发版本**，源码维护于 [dat3133/audio2text-cli](https://github.com/dat3133/audio2text-cli)。本项目尚未发布 GitHub Release 或 npm 包，当前请通过源码构建使用。下文的发布安装方式需要对应版本的发布产物。

## 当前状态与限制

- 目标平台为 Windows、Linux x86_64。Windows 已完成本地构建、安装和真实转写检查；Linux 的原生安装、完整推理和硬件验收仍需补齐。
- Windows 上已验证 Qwen3-ASR 0.6B CPU、1.7B CUDA，以及 Whisper tiny CPU/CUDA 调用。部分 CUDA 检查复用已有依赖，默认托管 CUDA 环境和全部模型规格仍需验证。
- 模型推荐目前采用启发式排序，资源指标为保守估计；自然录音的 CER/WER 和可比较的质量、速度基准尚未建立。
- 中英混合识别存在差异。当前合成样例中，Qwen3-ASR 1.7B 自动识别出现开头遗漏和局部语言转换。返回 `ok` 表示流程成功，不保证正文准确。
- 两小时合成语音已通过连续处理检查，用于验证时长覆盖和内存行为；该结果不代表自然录音质量，也不作为跨机器性能承诺。

实时录音、流式识别、字幕、词级时间戳、说话人分离、云端 ASR、MCP 和常驻推理服务暂未提供。

## 从源码运行

需要 Rust 开发工具链；普通用户使用发布的原生可执行程序，无需 Rust、Python、Conda 或 FFmpeg。

```powershell
cargo build --release --locked
.\target\release\audio2text.exe --help
.\target\release\audio2text.exe doctor
.\target\release\audio2text.exe init
```

Linux 源码构建入口为 `./target/release/audio2text`。首版只支持 Windows/Linux x86_64。

## 初始化向导

`audio2text init` 在交互终端中依次展示环境、推荐/高级模式、语言、偏好、模型、下载源和准备计划。推荐模型会预选，仍可更换；高级模式允许修改设备、数据目录、批大小和分段长度。方向键/Enter 操作，选择返回项可修改前一步，Ctrl+C 取消。

`TERM=dumb` 或 `AUDIO2TEXT_PLAIN=1` 使用数字选择界面。交互提示写入 stderr，stdout 只有最终结果。

```powershell
audio2text init --dry-run --profile balanced --language zh
audio2text init --non-interactive --profile balanced --language zh
audio2text init --non-interactive --model whisper/tiny --device cpu
```

初始化下载固定版本的 uv，校验官方 SHA256，再准备隔离 Python 3.12.12、对应后端和内置 FFmpeg。CPU/CUDA 运行时分开管理。CUDA 运行配置使用 cu126，要求可用 NVIDIA GPU 和兼容驱动；自动模式可推荐 CPU。自检成功后才写入默认模型。

托管环境使用随程序嵌入的已测试依赖版本约束；运行时记录实际依赖版本。Python 路径、全局用户包和其他工具的 PYTHONPATH 不会污染托管推理进程。

`--data-dir` 优先于 `AUDIO2TEXT_HOME`，否则 Windows 使用 `%LOCALAPPDATA%\audio2text`，Linux 使用 `$XDG_DATA_HOME/audio2text` 或 `~/.local/share/audio2text`。CLI 不修改已有 Python 环境或安装 GPU 驱动。

Qwen 后端延迟加载仅用于时间对齐的 nagisa，避免其原生字典加载器的 Windows 中文路径问题。纯 ASR 包括日语识别仍使用公开转写接口；首版不启用时间对齐，不修改已安装的第三方包。

## 模型

```powershell
audio2text models list --language zh
audio2text models recommend --profile speed --device cpu
audio2text models install whisper/base --device cpu
audio2text models use whisper/base
audio2text models import "D:\models\Qwen3-ASR-1.7B" --id qwen3-asr/1.7b
```

`models use` 默认选择已经准备好的可用设备，也支持 `--device cpu` 等显式选择。显式指定设备不可用时会失败。

目录包含 Qwen3-ASR 0.6B/1.7B，以及 Whisper tiny/base/small/medium/large-v3/turbo。模型固定到具体仓库修订，文件按 SHA256 或 Git blob SHA1 验证。导入目录也必须匹配该版本，不能将任意模型目录注册为同一 ID。

只有 Qwen 模型提供 ModelScope 镜像。镜像文件仍须通过固定清单校验，内容改变会失败。`auto` 依次检测可用来源，转写期间不访问任何来源。

推荐和资源指标当前明确标为 **保守估计/启发式排序**，不代表已经建立跨模型精度或速度基线。实际排名需要发布前基准测试；不要把可安装理解为保证快速运行。

已有推理环境可显式复用，无需修改该环境：

```powershell
audio2text init --non-interactive --model qwen3-asr/1.7b --local-model "D:\models\Qwen3-ASR-1.7B" --runtime-python "D:\envs\asr\python.exe" --device cuda:0
```

复用是可选高级功能；缺失依赖时失败，不向外部环境安装包。普通初始化自动准备托管环境。

## 转写

```powershell
audio2text transcribe "meeting.m4a" --language zh
audio2text transcribe "meeting.m4a" --output "meeting.txt" --no-text
audio2text transcribe "a.wav" "b.mp4" --output-dir "transcripts"
audio2text transcribe "meeting.m4a" --format text
audio2text transcribe "meeting.m4a" --context-file "terms.txt" --batch-size 2
```

默认 stdout 返回 JSON，进度在 stderr。视频选择第一条音轨；长音频增量解码，并在低能量附近切分，默认目标 60 秒。批量复用模型，返回顺序与输入一致。每个文件的语言可能包含多个代码。

`--output` 只用于单文件；批量使用 `--output-dir`。不同目录下同名文件造成输出冲突时，推理前失败。已有结果默认不覆盖，使用 `--overwrite` 才允许；始终禁止把输出设置为输入文件。输出为 UTF-8 TXT，原子提交。

Windows 使用不覆盖目标的原子移动；Linux 非覆盖写入的 staging 链接移到输出目录下 `.audio2text-recovery/`，共享相同文件内容。失败的 staging 文件保留供恢复，工具不自动清理旧模型或恢复记录。

转写强制离线；除了后端离线参数，还阻止 Python 网络连接，FFmpeg 仅允许 file/pipe 协议。没有模型或运行时直接报错，不隐式初始化。

推理代码从原生程序通过 stdin 传入解释器，转写不改写运行时或模型缓存目录。agent 可使用已经初始化的只读模型环境，并将文本输出到其可写工作区。

## agent 接口

```json
{
  "schema_version": "1",
  "command": "transcribe",
  "status": "ok",
  "data": {
    "results": [
      {
        "input": "/absolute/file.wav",
        "status": "ok",
        "model": "whisper/tiny",
        "backend": "faster-whisper",
        "device": "cpu",
        "languages": ["en"],
        "audio_duration_seconds": 10.0,
        "text": "Example text",
        "output": null,
        "warnings": [],
        "error": null
      }
    ]
  },
  "warnings": [],
  "error": null
}
```

顶层状态为 `ok`、`partial`、`error`；批量结果保留输入顺序，各文件有独立状态。错误包含稳定的 `code`、可读的 `message`、`retryable` 和处理建议 `hint`。`--no-text` 必须搭配文件输出，此时正文为 `null`，`output` 返回实际输出文件的绝对路径。

未初始化时的错误结构示例：

```json
{
  "schema_version": "1",
  "command": "transcribe",
  "status": "error",
  "data": null,
  "warnings": [],
  "error": {
    "code": "ENV_NOT_READY",
    "message": "运行环境尚未准备完成",
    "retryable": false,
    "hint": "运行 audio2text init --non-interactive --profile balanced。"
  }
}
```

agent 根据退出码和错误代码决定下一步。转写不会隐式下载或打开向导；初始化应显式使用 `--non-interactive`。单文件可用 `--format text` 输出正文，多文件使用 JSON。

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 内部错误 |
| 2 | 参数/配置/交互终端要求 |
| 3 | 环境或模型未就绪 |
| 4 | 输入或解码错误 |
| 5 | 推理/OOM |
| 6 | 准备或下载失败 |
| 7 | 写入或输出冲突 |
| 8 | 批量部分成功 |
| 130 | 取消 |

接入 agent 的通用指引见 [skills/audio2text/SKILL.md](skills/audio2text/SKILL.md)。

## 安装与发布

安装器下载同版本原生程序并验证发布清单。Windows 安装器更新用户 PATH；Linux 安装器默认放入 `~/.local/bin`，缺少 PATH 时显示说明。安装脚本在控制终端可用时自动运行向导；npm 仅提示 init。

安装器默认使用 `dat3133/audio2text-cli`。对应版本的 GitHub Release 发布后，可在源码目录执行：

```powershell
.\install.ps1 -Version 0.1.0
.\install.ps1 -Version 0.1.0 -NoInit
```

Linux 源码目录中执行 `sh install.sh --version 0.1.0`，自动化可追加 `--no-init`。两个安装器都允许显式覆盖仓库；Windows 使用 `-Repository`，Linux 使用 `--repository`。Linux 另支持 `--install-dir`。安装器只下载 CLI，不在跳过向导时准备模型。

本地 npm 包可包含已构建的程序：

```powershell
node scripts/prepare-npm.mjs --binary target/release/audio2text.exe
npm pack ./npm
```

公开 npm 发布使用 `node scripts/prepare-npm.mjs --repository dat3133/audio2text-cli` 写入发布元数据，安装后从 GitHub Releases 下载同版本程序；不在 postinstall 中运行 init。未指定仓库时，准备脚本优先使用 `GITHUB_REPOSITORY`，否则使用项目默认仓库。包名占用和账号权限须发布前确认。

## 开发验证

```powershell
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
python -m unittest discover -s tests -v
node --test npm/test.mjs
```

Python worker 测试需要 NumPy 和 imageio-ffmpeg；真实模型测试另外需要对应运行时和模型。CI 配置覆盖两平台离线单元与接口测试；其结果不代替硬件、模型和长音频验收。贡献及发布验收说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可与来源

项目代码 Apache-2.0；原始设计来自 qwen3-asr-transcribe 技能。模型和后端保留各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。程序使用后端公开转写接口，不依赖 `_infer_asr`。
