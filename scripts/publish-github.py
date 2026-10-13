"""Publish verified assets; never overwrite assets of a published release."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from release_common import REPOSITORY, ROOT, expected_assets, run, sha256, validate_version, verify_release

directory = Path("dist/release").resolve()
value = validate_version(os.environ.get("GITHUB_REF_NAME"))
verify_release(directory)
tag = "v" + value
query = subprocess.run(["gh","api",f"repos/{REPOSITORY}/releases/tags/{tag}"],capture_output=True,text=True,encoding="utf-8")
release = None
if query.returncode == 0:
    release = json.loads(query.stdout)
elif "HTTP 404" not in query.stderr:
    raise SystemExit(query.stderr)
base = ROOT/".test-data"
base.mkdir(exist_ok=True)
work = Path(tempfile.mkdtemp(prefix="release-verify-",dir=base))
names = expected_assets(value) | {"SHA256SUMS"}
if release:
    existing = {asset["name"] for asset in release["assets"]}
    if not existing <= names:
        raise SystemExit("Existing release has unexpected assets; publication stopped")
    if existing:
        run(["gh","release","download",tag,"--repo",REPOSITORY,"--dir",work])
        for name in existing:
            if sha256(work/name) != sha256(directory/name):
                raise SystemExit(f"Existing release content differs: {name}; use a new version")
    if not release["draft"]:
        if existing != names or not release["prerelease"]:
            raise SystemExit("Published release identity/asset set differs")
        print("Existing identical public prerelease verified; no assets changed.")
        raise SystemExit(0)
else:
    notes = work/"notes.md"
    notes.write_text(f"""audio2text {value} is a preview release for Windows/Linux x86_64.

Provides guided initialization, Qwen3-ASR/faster-whisper model management, local file/batch transcription and JSON output for agents. Models and inference runtimes are installed separately by `audio2text init`.

Download the platform executable or installer and verify SHA256SUMS. After npm publication, install with `npm install -g audio2text-cli@beta`, then run `audio2text init`.

Full Linux inference, managed CUDA configurations and natural-speech accuracy benchmarks remain under validation. See the versioned README for support status and limitations.
""",encoding="utf-8")
    run(["gh","release","create",tag,"--repo",REPOSITORY,"--verify-tag","--draft","--prerelease","--latest=false","--title",f"audio2text {value}","--notes-file",notes])
    existing = set()
for name in sorted(names-existing):
    run(["gh","release","upload",tag,directory/name,"--repo",REPOSITORY])
verify = Path(tempfile.mkdtemp(prefix="release-complete-",dir=base))
run(["gh","release","download",tag,"--repo",REPOSITORY,"--dir",verify])
verify_release(verify)
if (verify/"SHA256SUMS").read_bytes() != (directory/"SHA256SUMS").read_bytes():
    raise SystemExit("Uploaded manifest differs from the validated artifacts")
run(["gh","release","edit",tag,"--repo",REPOSITORY,"--draft=false","--prerelease","--latest=false"])
print(f"Published https://github.com/{REPOSITORY}/releases/tag/{tag}")
