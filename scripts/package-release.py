"""Build artifact output only; never edits source or publishes a release."""
import hashlib
from pathlib import Path
import shutil
import sys
from release_common import run

source, name = Path(sys.argv[1]), sys.argv[2]
if Path(name).name != name or name not in ("audio2text-windows-x86_64.exe", "audio2text-linux-x86_64"):
    raise SystemExit("Unexpected asset name")
root = Path(sys.argv[3] if len(sys.argv) > 3 else "dist/platform")
root.mkdir(parents=True, exist_ok=True)
if list(root.iterdir()):
    raise SystemExit("Platform output must be a fresh directory")
target = root / name
shutil.copy2(source, target)
digest = hashlib.sha256(target.read_bytes()).hexdigest()
(root / "SHA256SUMS").write_text(f"{digest}  {name}\n", encoding="utf-8")
run([sys.executable,"scripts/native-notices.py",root/("native-notices-windows.md" if name.endswith(".exe") else "native-notices-linux.md")])
print(f"Prepared {name}; publication remains a separate explicit action.")
