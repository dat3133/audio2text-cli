"""Build artifact output only; never edits source or publishes a release."""
import hashlib
from pathlib import Path
import shutil
import sys

source, name = Path(sys.argv[1]), sys.argv[2]
if Path(name).name != name or name not in ("audio2text-windows-x86_64.exe", "audio2text-linux-x86_64"):
    raise SystemExit("Unexpected asset name")
root = Path("dist")
root.mkdir(exist_ok=True)
target = root / name
shutil.copy2(source, target)
digest = hashlib.sha256(target.read_bytes()).hexdigest()
(root / "SHA256SUMS").write_text(f"{digest}  {name}\n", encoding="utf-8")
for file in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "install.ps1", "install.sh"):
    shutil.copy2(file, root / file)
print(f"Prepared {name}; publication remains a separate explicit action.")
