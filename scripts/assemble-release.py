"""Combine the two platform candidates into one checksum manifest."""
import hashlib
from pathlib import Path
import shutil
import sys

parts = Path(sys.argv[1] if len(sys.argv) > 1 else ".release-parts")
target = Path("dist")
target.mkdir(exist_ok=True)
entries = []
for name in ("audio2text-windows-x86_64.exe", "audio2text-linux-x86_64"):
    sources = list(parts.rglob(name))
    if len(sources) != 1:
        raise SystemExit(f"Expected exactly one {name}, found {len(sources)}")
    file = target / name
    shutil.copy2(sources[0], file)
    entries.append(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {name}")
(target / "SHA256SUMS").write_text("\n".join(entries) + "\n", encoding="utf-8")
for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "install.ps1", "install.sh"):
    shutil.copy2(name, target / name)
print("Assembled platform candidates. No release has been published.")
