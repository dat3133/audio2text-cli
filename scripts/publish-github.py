"""Publish verified assets; never overwrite assets of a published release."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from release_common import REPOSITORY, ROOT, expected_assets, github_release_flags, release_channel, run, sha256, validate_version, verify_release


def release_notes(value):
    preview = release_channel(value) == "beta"
    label = "a preview release" if preview else "the first stable release" if value == "0.1.0" else "a stable release"
    package = "audio2text-cli@beta" if preview else "audio2text-cli"
    return f"""audio2text {value} is {label} for Windows/Linux x86_64.

Provides guided initialization, remembered storage locations, download progress, Qwen3-ASR/faster-whisper model management, offline file/batch transcription and JSON output for agents. Includes the Windows faster-whisper CUDA worker-exit fix from beta.6.

Download the platform executable or installer and verify SHA256SUMS. After npm publication, install with `npm install -g {package} --registry=https://registry.npmjs.org`, then run `audio2text init` for first-time setup. Upgrading an existing installation reuses its models, runtimes and configuration.

Installation and automated checks cover both platforms. Windows CUDA transcription has been checked on real hardware, and Linux transcription has been tested by the maintainer. These checks do not cover every model/GPU configuration. Natural-speech accuracy benchmarks remain future work; review mixed-language output. See the [versioned README](https://github.com/{REPOSITORY}/blob/v{value}/README.md) for usage and limitations.
"""


def main(directory=None):
    directory = Path(directory or "dist/release").resolve()
    value = validate_version(os.environ.get("GITHUB_REF_NAME"))
    verify_release(directory)
    preview = release_channel(value) == "beta"
    flags = github_release_flags(value)
    tag = "v" + value
    query = subprocess.run(["gh", "api", f"repos/{REPOSITORY}/releases/tags/{tag}"], capture_output=True, text=True, encoding="utf-8")
    release = None
    if query.returncode == 0:
        release = json.loads(query.stdout)
    elif "HTTP 404" not in query.stderr:
        raise SystemExit(query.stderr)
    base = ROOT / ".test-data"
    base.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="release-verify-", dir=base))
    names = expected_assets(value) | {"SHA256SUMS"}
    if release:
        existing = {asset["name"] for asset in release["assets"]}
        if not existing <= names:
            raise SystemExit("Existing release has unexpected assets; publication stopped")
        if existing:
            run(["gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", work])
            for name in existing:
                if sha256(work / name) != sha256(directory / name):
                    raise SystemExit(f"Existing release content differs: {name}; use a new version")
        if not release["draft"]:
            if existing != names or release["prerelease"] != preview:
                raise SystemExit("Published release identity/asset set differs")
            print("Existing identical public release verified; no assets changed.")
            return
    else:
        notes = work / "notes.md"
        notes.write_text(release_notes(value), encoding="utf-8")
        run(["gh", "release", "create", tag, "--repo", REPOSITORY, "--verify-tag", "--draft", *github_release_flags(value, draft=True), "--title", f"audio2text {value}", "--notes-file", notes])
        existing = set()
    for name in sorted(names - existing):
        run(["gh", "release", "upload", tag, directory / name, "--repo", REPOSITORY])
    verify = Path(tempfile.mkdtemp(prefix="release-complete-", dir=base))
    run(["gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", verify])
    verify_release(verify)
    if (verify / "SHA256SUMS").read_bytes() != (directory / "SHA256SUMS").read_bytes():
        raise SystemExit("Uploaded manifest differs from the validated artifacts")
    run(["gh", "release", "edit", tag, "--repo", REPOSITORY, "--draft=false", *flags])
    print(f"Published https://github.com/{REPOSITORY}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
