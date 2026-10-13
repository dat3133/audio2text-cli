# Contributing

Use Rust for the native CLI and Python for backend adapters. Changes must preserve the single JSON stdout envelope, stderr logging, bounded audio buffers, and transactional default configuration.

Run Rust tests/clippy, Python worker and CLI tests, and npm tests. For backend changes, test CPU/CUDA with the pinned model and record hardware, dependency versions, input provenance, timings and memory. Do not invent benchmark results or silently upgrade model revisions.

Model catalog updates require upstream repository/revision, license, complete file sizes/hashes, language support and measured resource notes. Source mirrors must pass the same immutable content checks.

## Release acceptance

- Verify installation and initialization on clean Windows and Linux x86_64 systems without preinstalled Python, Conda or FFmpeg. Check interactive setup, non-interactive setup, cancellation and configuration recovery.
- Validate at least one CPU and one CUDA configuration for each backend, including the default managed runtimes. Record model revisions, hardware, drivers and dependency versions; expand the matrix before claiming support for additional models or devices.
- Transcribe with the network disconnected after initialization. Missing resources must produce a stable JSON error without downloading; check long audio, batch partial failures, output conflicts and stdout isolation.
- Use audio with clear redistribution or evaluation permissions for Chinese, English and mixed-language accuracy checks. Report CER/WER, loading and processing time, memory and GPU memory with reproducible settings. Synthetic stability tests do not establish natural-speech accuracy or model rankings.
- Inspect platform binaries, generate the combined SHA256SUMS, verify third-party license notices, and confirm the npm package name and publishing permissions. Installation metadata must point to `dat3133/audio2text-cli` or an explicitly selected release repository.

Update the public support status and known limitations in README when these checks are completed. Keep model weights, recordings, transcripts, local environments, build products and internal development notes outside Git. GitHub Releases and npm publishing are separate release steps; the release workflow only builds candidate artifacts.
