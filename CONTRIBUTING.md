# Contributing

Use Rust for the native CLI and Python for backend adapters. Changes must preserve the single JSON stdout envelope, stderr logging, bounded audio buffers, and transactional default configuration.

For backend changes, test CPU/CUDA with the pinned model and record hardware, dependency versions, input provenance, timings and memory. Do not invent benchmark results or silently upgrade model revisions.

Model catalog updates require upstream repository/revision, license, complete file sizes/hashes, language support and measured resource notes. Source mirrors must pass the same immutable content checks.

## Build and test

Source builds require the Rust toolchain. On Windows PowerShell:

```powershell
cargo build --release --locked
.\target\release\audio2text.exe --help
.\target\release\audio2text.exe doctor
```

On Linux, use `./target/release/audio2text`. Published executables need no user-managed Python, Conda or FFmpeg; `init` prepares the selected model and inference runtime separately.

Run the existing checks before contributing code:

```powershell
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
cargo build --locked
python -m pip install numpy==2.2.6 imageio-ffmpeg==0.6.0 pyte==0.8.2
python -m unittest discover -s tests -v
node --test npm/test.mjs
```

Python worker tests require NumPy and imageio-ffmpeg. On POSIX, pyte and a pseudo-terminal exercise real wizard navigation and check that Chinese model labels do not erase completed steps; these tests skip on Windows. Real inference tests additionally require the matching runtime and model. Windows/Linux CI checks unit tests, CLI behavior and packaging; these checks do not replace hardware, inference or long-audio acceptance. Documentation-only edits require command/link checks and `git diff --check`, without repeating inference benchmarks.

Storage tests isolate the platform configuration directory from the developer profile. They cover location precedence, invalid or missing saved locations, relative/Unicode paths, per-command overrides, missing volumes and permission failures. Setup tests inject resource preparation and location persistence failures to verify that only successful initialization commits the new default. POSIX terminal tests also exercise repeated plan-page edits, advanced settings, rejected paths, returning to model selection, Escape and Ctrl+C without creating resources. Keep these tests offline; real model self-tests remain part of inference acceptance.

Download-progress checks use fake streams, a local HTTP redirect and POSIX terminal emulation to verify resumption, retries, cancellation, layout and JSON stdout isolation. The fixed uv version can be checked with a small wheel and an isolated cache/target; stdout must be piped and stderr attached to the terminal, matching the CLI. uv deliberately hides native progress when `RUST_LOG` is set, so omit that development-only variable in this interactive smoke test. Do not change the user's logging or `UV_NO_PROGRESS` preferences. These checks require no model or Torch download.

## CLI interface reference

Business commands return one JSON object on stdout; progress and dependency logs go to stderr. Top-level status is `ok`, `partial` or `error`; batch items keep input order and carry their own status, warnings and error. `--no-text` requires file output and returns `text: null` with the absolute output path. Agents must explicitly initialize with `--non-interactive`; transcription never downloads resources.

Example of an unprepared environment:

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

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | Internal error |
| 2 | Arguments, configuration or interactive terminal required |
| 3 | Environment or model not ready |
| 4 | Input or decoding error |
| 5 | Inference failure or insufficient resources |
| 6 | Preparation or download failure |
| 7 | Output conflict or write failure |
| 8 | Partial batch success |
| 130 | Cancelled |

See the [agent skill](skills/audio2text/SKILL.md) for integration guidance. Breaking result-shape changes require a new `schema_version`.

## Release acceptance

- Verify installation and initialization on clean Windows and Linux x86_64 systems without preinstalled Python, Conda or FFmpeg. Check interactive setup, non-interactive setup, cancellation and configuration recovery.
- Validate at least one CPU and one CUDA configuration for each backend, including the default managed runtimes. Record model revisions, hardware, drivers and dependency versions; expand the matrix before claiming support for additional models or devices.
- Transcribe with the network disconnected after initialization. Missing resources must produce a stable JSON error without downloading; check long audio, batch partial failures, output conflicts and stdout isolation.
- Use audio with clear redistribution or evaluation permissions for Chinese, English and mixed-language accuracy checks. Report CER/WER, loading and processing time, memory and GPU memory with reproducible settings. Synthetic stability tests do not establish natural-speech accuracy or model rankings.
- Inspect platform binaries, generate the combined SHA256SUMS, verify third-party license notices, and confirm the npm package name and publishing permissions. Installation metadata must point to `dat3133/audio2text-cli` or an explicitly selected release repository.

Update the public support status and known limitations in README when these checks are completed. Keep model weights, recordings, transcripts, local environments, build products and internal development notes outside Git.

## Release workflow

Preview releases use matching `v<version>` tags and `beta` npm dist-tags. Synchronize Cargo.toml, Cargo.lock, npm/package.json and both installers' default version, commit the source, then create and push the matching tag. Run `release.yml` on that tag; the workflow rejects branch refs and mismatched versions. The command below illustrates the first publication, without automatic npm publishing:

```powershell
gh workflow run release.yml --ref v0.1.0-beta.2 -f publish_npm=false
```

The workflow validates, tests and builds both platforms, performs local installation checks, and assembles the exact platform-neutral npm tarball. It creates a GitHub draft, uploads and verifies every asset against SHA256SUMS, publishes a prerelease without marking it Latest, then verifies anonymous Windows/Linux installation. Packaging uses fresh directories and an explicit file whitelist. The npm package contains the launcher, version/checksum metadata, README and license notices; native executables, model weights and inference runtimes are excluded.

For the first npm publication, download all verified GitHub Release assets to `dist/release`, enable npm account 2FA and authenticate against the official registry:

```powershell
npm login --registry=https://registry.npmjs.org
python scripts/check-release.py --tag v0.1.0-beta.2 --directory dist/release
python scripts/publish-npm.py
```

Publishing may require an additional browser verification. The helper uploads the same verified tarball with `--tag beta --access public`, waits for registry availability, and compares published integrity and tags. Same-version retries verify and reuse identical content without uploading again; different bytes require a new version. Use `--verify-only` for read-only verification. Release tooling explicitly targets registry.npmjs.org without changing user mirror settings.

Normally, run `npm-smoke.yml` on the release tag to check registry installation. Registry smoke checks compare the downloaded tarball with the immutable GitHub Release checksum and cover normal installation plus first launch after `--ignore-scripts`. The accepted first-publication tag exception below uses updated checks from `main`, preserving the existing release tag.

### First-publication tag exception

The npm registry assigned both `beta` and `latest` to `0.1.0-beta.2` despite publishing with `--tag beta`. Two authenticated `npm dist-tag rm audio2text-cli latest` attempts returned HTTP 400; the response gave no detailed explanation. The npm CLI repository has a [report of the same behavior](https://github.com/npm/cli/issues/8490). This first-publication exception was accepted; it does not establish a stable release. Keep user installation examples explicit about `@beta` or the exact preview version.

```powershell
python scripts/publish-npm.py --verify-only --allow-initial-latest
gh workflow run npm-smoke.yml --ref main -f release_tag=v0.1.0-beta.2 -f allow_initial_latest=true
```

This explicit exception is limited to one actual published version, excluding the registry's `0.0.0-stage` placeholder. Later preview releases must pass the default strict tag check. Do not republish an existing version or change its assets to repair metadata; use a new version for payload changes. Future npm packages automatically use the repository README at packaging time; editing the repository does not change the README inside an already published tarball.

### Trusted publishing

After the first publication, configure the package's trusted publisher:

```powershell
npm trust github audio2text-cli --file release.yml --repo dat3133/audio2text-cli --allow-publish --registry=https://registry.npmjs.org
```

This setup also requires npm 2FA verification. Subsequent releases can enable `publish_npm` in `release.yml` for OIDC publication and registry installation checks. The publishing job uses Node 24 and `id-token: write`, without a long-lived npm publishing token. Trusted publishing for a public GitHub repository generates provenance automatically. Continue to publish previews with `beta` without actively updating `latest`, and preserve immutable published contents.
