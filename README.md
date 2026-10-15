# audio2text

本地语音转文字 CLI，支持音视频文件、批量转写和多步模型配置。可直接在终端使用，也可由 Claude Code、Codex 等 agent 调用；转写离线进行，不上传音频或正文。

当前为 **0.1.0-beta.5 预览版本**，支持 **Windows/Linux x86_64**。[npm 包](https://www.npmjs.com/package/audio2text-cli) · [GitHub Release](https://github.com/dat3133/audio2text-cli/releases/tag/v0.1.0-beta.5)

## 快速开始

npm 安装需要 **Node.js 18+ 和 npm**，无需预装 Python、Conda 或 FFmpeg。在终端执行：

```powershell
npm install -g audio2text-cli@beta
audio2text init
audio2text transcribe "meeting.m4a" --language zh --format text
```

安装只准备 CLI；首次 `init` 会引导选择模型，下载模型及运行环境。完成初始化后即可离线转写。初始化需要联网，npm 安装还需要连接 GitHub 下载平台程序。

下载时终端会显示进度：模型文件显示百分比和已下载/总大小，Python 及依赖使用 uv 原生进度，随后分别进行校验、安装和自检。续传按已有字节继续，缓存资源跳过下载。输出重定向、终端能力不足或设置 `AUDIO2TEXT_PLAIN=1` 时使用普通日志；进度和日志均写入 stderr，不影响 JSON 结果。

当前仍为预览版，请明确安装 `@beta` 或固定版本；首发的 `latest` 标签也指向预览版。

```powershell
audio2text --version
# 固定到当前版本
npm install -g audio2text-cli@0.1.0-beta.5
# npm 镜像未同步时，临时使用官方注册表
npm install -g audio2text-cli@beta --registry=https://registry.npmjs.org
```

## 其他安装方式

无需 Node.js，也可使用安装脚本或从 [GitHub Release](https://github.com/dat3133/audio2text-cli/releases/tag/v0.1.0-beta.5) 下载对应平台的程序；直接下载后将程序命名为 `audio2text.exe`（Windows）或 `audio2text`（Linux），加入 PATH，并运行 `audio2text init`。Linux 程序还需赋予执行权限。

<details>
<summary>Windows / Linux 安装脚本</summary>

Windows PowerShell：

```powershell
$installer = Join-Path $PWD 'audio2text-install.ps1'
Invoke-WebRequest 'https://github.com/dat3133/audio2text-cli/releases/download/v0.1.0-beta.5/install.ps1' -OutFile $installer
& $installer
# 跳过初始化向导：& $installer -NoInit
```

Linux：

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/dat3133/audio2text-cli/releases/download/v0.1.0-beta.5/install.sh | sh
# 跳过初始化向导：在上面的管道末尾使用 sh -s -- --no-init
```

脚本验证下载校验值，并在有交互终端时自动进入向导。Windows 安装后请重开终端，使 PATH 更新生效；Linux 默认安装到 `~/.local/bin`，请确保该目录在 PATH 中。npm 安装不会打开向导，需手动执行 `audio2text init`。

</details>

## 初始化与模型

`audio2text init` 依次检测环境，并让你选择语言、均衡/准确率/速度偏好、模型及下载源。推荐模型会预选，仍可更换；高级配置可设置设备、数据目录、批大小和分段长度。方向键选择、Enter 确认，可返回上一步，Ctrl+C 取消。长模型说明按窗口宽度换行，菜单随选择滚动；窗口空间不足时改用编号选择，也可设置 `AUDIO2TEXT_PLAIN=1` 使用编号界面。

在最后的“准备计划”页选择 **“修改存储目录（模型和运行环境）”**，即可将模型、运行环境、托管 Python、缓存和配置放到其他磁盘，例如 `D:\audio2text`。推荐模式也能修改；路径支持中文、空格和相对路径，显示为绝对路径。编辑时直接回车保留原值，Ctrl+U 清空，Esc 返回。页面会更新实际路径、下载量和目标磁盘可用空间，确认前不下载。

初始化和自检成功后会记住通过页面修改的位置，后续直接运行 `audio2text doctor` 或 `transcribe` 即可；取消或失败保留原默认位置。切换目录不自动搬迁或删除旧资源，新位置已有资源经校验后复用；外部模型和 Python 环境保留原位置。

```powershell
# 仅查看计划，不下载、不修改配置
audio2text init --dry-run --profile balanced --language zh
# 使用 CPU，适合无 NVIDIA GPU 的电脑
audio2text init --non-interactive --model whisper/tiny --device cpu
# 仅为本次命令覆盖存储目录
audio2text --data-dir "./audio2text-data" init
```

位置优先级为 `--data-dir` → `AUDIO2TEXT_HOME` → 页面保存的位置 → 平台默认目录。单独传入 `--data-dir` 不改变已记住的位置，后续仍需相同参数或环境变量；设置了 `AUDIO2TEXT_HOME` 时，它继续覆盖页面保存的位置。已保存目录不可用时会明确报错，可用显式参数恢复，不会悄悄改用 C 盘。

平台默认目录为 Windows 的 `%LOCALAPPDATA%\audio2text`，Linux 的 `$XDG_DATA_HOME/audio2text` 或 `~/.local/share/audio2text`。通过页面选择其他位置后，原默认目录仅新增很小的 `location.json` 用于定位；已有文件保留。CUDA 需要 NVIDIA GPU 和兼容驱动；工具不安装 GPU 驱动。

| 后端 | 可用模型 ID |
|---|---|
| Qwen3-ASR | `qwen3-asr/0.6b`、`qwen3-asr/1.7b` |
| faster-whisper | `whisper/tiny`、`whisper/base`、`whisper/small`、`whisper/medium`、`whisper/large-v3`、`whisper/turbo` |

```powershell
audio2text models list --language zh
audio2text models recommend --profile speed --device cpu
audio2text models install whisper/base --device cpu
audio2text models use whisper/base --device cpu
```

`models install` 准备模型及对应运行环境，`models use` 切换已安装的默认模型。下载源可选择自动或 Hugging Face，Qwen 模型另支持 ModelScope；重复初始化会复用已校验的资源。

## 常用转写

支持 WAV、MP3、M4A、FLAC、MP4、MKV 等常见格式；视频默认使用第一条音轨，长录音自动分段合并。路径包含空格或中文时使用引号。

```powershell
# 单文件，默认返回 JSON；不指定语言时使用初始化设置
audio2text transcribe "meeting.m4a" --language zh
# 只在终端显示正文
audio2text transcribe "meeting.m4a" --format text
# 保存 TXT，JSON 中仅返回文件位置等信息
audio2text transcribe "meeting.m4a" --output "meeting.txt" --no-text
# 批量转写到目录
audio2text transcribe "a.wav" "b.mp4" --output-dir "transcripts"
# 提供 UTF-8 背景文本或术语文件
audio2text transcribe "meeting.m4a" --context-file "terms.txt"
```

`--format text` 仅用于单文件；批量使用 JSON。输出文件为 UTF-8 TXT，默认不覆盖已有文件，确认需要覆盖时添加 `--overwrite`。`--output` 用于单文件，`--output-dir` 用于批量；`--no-text` 必须搭配文件输出，此时 JSON 正文为 `null`，`output` 为实际输出文件的绝对路径。

## agent 使用

可按下面的方式为 Claude Code、Codex 等 agent 安装使用指南（当前 Skills CLI 需要 **Node.js 22.20+ 和 Git**）：

```powershell
npx skills add https://github.com/dat3133/audio2text-cli --skill audio2text
# 跨项目使用：全局安装到 Codex 和 Claude Code
npx skills add https://github.com/dat3133/audio2text-cli --skill audio2text --global --agent codex claude-code
```

默认安装到当前项目，`--global` 安装到用户范围；Windows 无法创建链接时可加 `--copy`。安装 skill 只获取 agent 指南，CLI 仍按快速开始安装，模型和运行环境由 `init` 准备。agent 会优先复用已有环境，缺失时查看准备计划，并按用户已有授权继续。

自动化调用应显式使用 `--non-interactive`，转写不会自动下载模型或打开向导：

```powershell
audio2text init --non-interactive --profile balanced --language zh
audio2text transcribe "meeting.m4a" --format json
```

stdout 返回一个 JSON 对象，进度与日志在 stderr。成功结果示例：

```json
{
  "schema_version": "1",
  "command": "transcribe",
  "status": "ok",
  "data": {
    "results": [{
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
    }]
  },
  "warnings": [],
  "error": null
}
```

顶层状态为 `ok`、`partial` 或 `error`，批量结果保持输入顺序。退出码 `0` 表示成功，`8` 表示部分成功；失败时读取顶层或各文件的 `error.code`、`message`、`retryable` 和 `hint`。完整接入流程见 [agent SKILL.md](skills/audio2text/SKILL.md)，错误示例与退出码表见 [贡献指南](CONTRIBUTING.md#cli-interface-reference)。

## 常见问题与限制

| 问题 | 处理方式 |
|---|---|
| 找不到 `audio2text` 命令 | 重开终端，确认 npm 全局命令目录或脚本安装目录已加入 PATH。 |
| npm 找不到 beta 版本 | 使用快速开始中的官方注册表安装命令。 |
| 模型或环境未就绪 | 运行 `audio2text doctor`，再执行 `audio2text init`；转写不会自动初始化。 |
| 显存或内存不足 | 减小 `--batch-size` 或 `--segment-seconds`；需要更小模型或 CPU 环境时，通过 `init` 明确重新选择。 |

当前支持 Windows/Linux x86_64，Linux 程序使用 glibc，暂不支持 macOS、ARM 或 Alpine/musl。两平台安装已有验证，Linux 完整推理、默认托管 CUDA 环境及全部模型规格仍待补齐。推荐排序和资源估计仅供选型参考，尚无自然录音准确率基准；混合语言结果需复核，返回 `ok` 表示流程成功，不保证正文准确。

暂不提供实时录音、流式识别、字幕、词级时间戳、说话人分离、云端 ASR、MCP 或常驻推理服务。更多参数可通过 `audio2text --help`、`audio2text init --help` 和 `audio2text transcribe --help` 查看；问题反馈请使用 [GitHub Issues](https://github.com/dat3133/audio2text-cli/issues)。

## 许可证及贡献

项目代码采用 [Apache-2.0](LICENSE)，原始设计来自 qwen3-asr-transcribe 技能；模型与第三方依赖各自的许可证见 [第三方声明](THIRD_PARTY_NOTICES.md)。源码构建、开发测试和发布维护见 [贡献指南](CONTRIBUTING.md)。
