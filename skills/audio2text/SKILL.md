---
name: audio2text
description: Transcribe local audio/video files offline with the audio2text CLI. Use for recordings, meetings, interviews, voice notes, and requests for 录音转写 or 音视频转文字; supports batch files and structured JSON results.
---

# audio2text agent integration

## Check the host and readiness

Run commands on the host that can access the media files and the installed runtime. A cloud sandbox may not see local paths or the host GPU. The CLI supports Windows/Linux x86_64; it is not a macOS/ARM executable.

Check `audio2text --version`. If the command is missing, explain the required installation and continue when covered by the user's authorization. The npm entry requires Node.js 18+:

```sh
npm install -g audio2text-cli --registry=https://registry.npmjs.org
```

If npm is unavailable, the [project installation guide](https://github.com/dat3133/audio2text-cli#快速开始) also provides native installers and executables. Installing this skill only installs instructions; it does not install the CLI, models, or inference dependencies.

Run `audio2text doctor` when readiness is unknown. Read `data.ready`, `data.default_model`, and `data.data_dir`: exit code 0 and top-level `status: "ok"` mean the diagnostic ran successfully, not that transcription is ready. This describes the default model; a separately requested model/device may still need preparation. Use `models list` and `models recommend` for read-only inventory and recommendations.

## Reuse storage and prepare only what is needed

Prefer the initialized location reported by the CLI. Directory precedence is `--data-dir` > `AUDIO2TEXT_HOME` > the location saved by the init wizard > the platform default. Do not create a fresh cache just because execution is sandboxed if the existing environment is readable and usable.

When the user selects another directory, or existing storage is unusable, pass `audio2text --data-dir "<directory>" ...` consistently for diagnosis, planning, preparation, and transcription. This flag overrides one command; it does not save a new default. An unavailable saved directory is an error, not a reason to silently initialize elsewhere. Confirm an alternative only if it is not already authorized.

If the required resources are missing:

1. Run `audio2text init --dry-run` with the user's model, device, language, and directory options. It does not download or save configuration. Inspect `data.model.id`, `data.device`, `data.data_dir`, `data.model_path`, `data.runtime_path`, `data.model_download_bytes`, and `data.runtime_download_estimate_bytes`.
2. Explain the selected resources, locations, and download estimates; inference dependencies can require several GiB. Reuse existing authorization for installation and preparation. Ask only about missing choices or preparation that is not yet authorized.
3. Execute `audio2text init --non-interactive` with the same options, pinning the model and device from the accepted plan. Never launch the interactive wizard in an automated session. Reuse an already ready environment instead of reinitializing it for each transcription.

`init` sets default configuration. `models install <id>` prepares an additional model without switching the default; `models use <id>` switches to an installed model. `models import <path> --id <id>` registers and verifies an existing model but does not prepare its runtime. These are preparation/configuration actions, not read-only queries.

## Transcribe and read the result

Quote paths, including Chinese names and spaces. Use the caller's language/model/device when supplied; otherwise keep the initialized defaults. Background context can be provided as text with `--context` or as a UTF-8 file with `--context-file`; the two options are mutually exclusive.

```sh
# Single file; stdout is JSON by default.
audio2text transcribe "meeting.m4a" --format json
# Background terminology; use the language requested by the caller.
audio2text transcribe "meeting.m4a" --language zh --context-file "terms.txt"
# Large result: read the UTF-8 file at the returned output path.
audio2text transcribe "meeting.m4a" --output "meeting.txt" --no-text
# Batch: each input has its own result, in input order.
audio2text transcribe "a.wav" "b.mp4" --output-dir "transcripts" --no-text
```

`--output` is for one file, `--output-dir` for batch output. Files are not overwritten unless `--overwrite` is explicitly supplied; use it only when overwrite is authorized. Choose file output for transcripts that would exceed the agent's context budget. `--no-text` requires file output and returns `text: null` plus an absolute `output` path. Read that file, in portions if needed, rather than assuming the requested filename or returning a giant JSON transcript.

Capture stdout and stderr separately. Do not merge stderr into the JSON stream: it contains download progress, warnings, dependency logs, and diagnostics. Except for help/version, business commands return a single JSON object with `schema_version: "1"`. For transcription, read top-level `status`, `warnings`, and `error`, then inspect every entry in `data.results` for `status`, `text`/`output`, `warnings`, and `error`.

## Handle errors and report accurately

| Exit | Meaning / next action |
|---|---|
| 0 | Command succeeded; still inspect readiness or transcription warnings. |
| 1 | Unclassified internal error; retain diagnostics and report the failure. |
| 2 | Invalid arguments/configuration or missing interactive terminal; correct arguments and use non-interactive init. |
| 3 | Environment/model/device not ready; inspect the structured error and plan required preparation. |
| 4 | Input/decoding failure; check the path, format, or audio stream. |
| 5 | Inference/resource failure; for OOM, reduce batch size or segment length with the same model. |
| 6 | Preparation/download failure; inspect the error and resume authorized preparation when retryable. |
| 7 | Output conflict or write failure; choose an authorized writable path or authorized overwrite. |
| 8 | Partial batch success; preserve successes and address failed entries individually. |
| 130 | User cancelled; stop without automatically restarting. |

Use `error.code`, `message`, `retryable`, and `hint` to choose the next action. A partial batch can have a top-level error while containing successful files. Do not discard them, retry indefinitely, or silently substitute a different model/device. If reducing memory use is insufficient, explain the remaining choice and use existing authorization or obtain the user's choice before changing it.

Transcription is offline and never implicitly downloads resources. This tool does not provide live recording, streaming recognition, subtitles, word timestamps, or speaker diarization. `ok` confirms execution, not recognition accuracy: report relevant warnings and uncertainty, and do not invent missing speech or claim the transcript was independently verified.
