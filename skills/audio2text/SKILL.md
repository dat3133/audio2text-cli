---
name: audio2text
description: Transcribe local audio/video offline with the audio2text CLI. Use for local recordings; returns structured JSON and supports large-result files.
---

# audio2text agent integration

Use the `audio2text` executable on the same host as the media and GPU/runtime. Do not assume a cloud sandbox can access them.

1. Verify local paths and run `audio2text doctor` if readiness is unknown.
   In a filesystem sandbox, initialize with `--data-dir <writable-cache-directory>` and pass the same directory to subsequent commands. Already initialized environments can be read without rewriting their model/runtime cache.
2. For preparation, inspect `audio2text init --dry-run`. If initialization is authorized, run `audio2text init --non-interactive` with the user's model/device/language choices. This downloads runtime and model resources. Never run an interactive init in an automated session.
3. Transcribe with `audio2text transcribe <file> --format json`. Large results should use `--output <path> --no-text`, then read the UTF-8 file.
4. Parse the single stdout JSON object and inspect exit code, status, per-file error, and warnings. stderr contains progress only.
5. Do not overwrite files unless the user authorized it. Do not silently choose a different model after failure. For OOM, reduce `--batch-size` or `--segment-seconds`, or discuss an explicitly smaller model.

Batch: `audio2text transcribe <file1> <file2> --output-dir <directory>`. Exit 8 means partial success; process each result separately.

Exit codes: 0 success, 2 arguments/interaction, 3 environment/model, 4 input, 5 inference, 6 preparation/download, 7 output, 8 partial, 130 cancelled.

`models list` and `models recommend` are read-only. `models install`, `models import`, `models use`, and `init` prepare or change local configuration. Transcription never downloads models and always operates offline.
