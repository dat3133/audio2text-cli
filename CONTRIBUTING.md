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

Update the public support status and known limitations in README when these checks are completed. Keep model weights, recordings, transcripts, local environments, build products and internal development notes outside Git.

Preview releases use matching `v<version>` tags and `beta` npm dist-tags. Run `release.yml` on the tag with `publish_npm=false` for the first release: it validates, builds, tests, packages, publishes the GitHub prerelease and checks anonymous installation. Enable npm account 2FA, log in to the official registry, publish the exact validated tarball with `scripts/publish-npm.py`, and run `npm-smoke.yml` on the same tag. Release tooling explicitly targets registry.npmjs.org without changing user mirror settings. Configure the package's trusted publisher for `dat3133/audio2text-cli`, workflow `release.yml`; subsequent releases can enable `publish_npm` for OIDC publication and registry installation checks. Published version contents are immutable to this tooling; use a new version for repairs.

The npm registry also assigned `latest` to the first published beta and rejected removal with HTTP 400. This documented first-publication exception was accepted for `0.1.0-beta.2`; it does not establish a stable release. Use `--verify-only --allow-initial-latest` to verify the existing tarball, or dispatch `npm-smoke.yml` from `main` with `release_tag=v0.1.0-beta.2` and `allow_initial_latest=true`. This explicit exception is limited to one actual published version, excluding the registry's `0.0.0-stage` placeholder. Later preview releases must pass the default strict tag check. Registry smoke checks compare the downloaded npm tarball with the immutable GitHub Release checksum.
