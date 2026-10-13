"""Combine the two platform candidates into one checksum manifest."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
from release_common import BINARIES, DOCUMENTS, ROOT, npm_command, public_readme, run, sha256, validate_version, verify_release

parts = Path(sys.argv[1] if len(sys.argv) > 1 else ".release-parts")
target = Path(sys.argv[2] if len(sys.argv) > 2 else "dist/release").resolve()
target.mkdir(parents=True, exist_ok=True)
if list(target.iterdir()):
    raise SystemExit("Release output must be a fresh directory")
value = validate_version()
entries = []
for name in BINARIES:
    sources = [path for path in parts.rglob(name) if path.is_file()]
    if len(sources) != 1:
        raise SystemExit(f"Expected exactly one {name}, found {len(sources)}")
    file = target / name
    shutil.copy2(sources[0], file)
    entries.append(f"{sha256(file)}  {name}")
(target / "SHA256SUMS").write_text("\n".join(entries) + "\n", encoding="utf-8")
for name in DOCUMENTS:
    shutil.copy2(name, target / name)
notices = sorted(path for path in parts.rglob("native-notices-*.md") if path.is_file())
if not notices:
    raise SystemExit("Native dependency notices are missing")
with (target/"THIRD_PARTY_NOTICES.md").open("a",encoding="utf-8") as stream:
    for report in notices:
        stream.write("\n\n"+report.read_text("utf-8"))
(target / "README.md").write_text(public_readme(),encoding="utf-8")
staging_root = ROOT / ".release-parts"
staging_root.mkdir(exist_ok=True)
staging = Path(tempfile.mkdtemp(prefix="npm-",dir=staging_root))
run(["node", "scripts/prepare-npm.mjs", "--checksums", target/"SHA256SUMS", "--notices", target/"THIRD_PARTY_NOTICES.md", "--output-dir", staging])
packed = run(npm_command("pack",staging,"--pack-destination",target,"--ignore-scripts","--json","--loglevel=error"),capture_output=True,text=True,encoding="utf-8")
if json.loads(packed.stdout)[0]["filename"] != f"audio2text-cli-{value}.tgz":
    raise SystemExit("Unexpected package filename")
entries = [f"{sha256(p)}  {p.name}" for p in sorted(target.iterdir()) if p.name != "SHA256SUMS"]
(target/"SHA256SUMS").write_text("\n".join(entries)+"\n",encoding="utf-8")
verify_release(target)
print("Assembled and verified release artifacts. No release has been published.")
