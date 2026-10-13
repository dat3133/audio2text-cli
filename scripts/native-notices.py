"""Collect pinned native dependency license texts for binary distribution."""
import json
from pathlib import Path
import subprocess
import sys
from release_common import run

metadata=json.loads(run(["cargo","metadata","--locked","--format-version","1"],capture_output=True,text=True,encoding="utf-8").stdout)
sections=["# Native CLI dependency notices\n\nLicenses below are copied from the pinned Cargo dependencies used by the project. Both platform dependency sets are included.\n"]
for package in sorted(metadata["packages"],key=lambda p:(p["name"],p["version"])):
    if package["name"] == "audio2text":
        continue
    root=Path(package["manifest_path"]).parent
    files=set()
    for pattern in ("LICENSE*","LICENCE*","license*","COPYING*","UNLICENSE*"):
        files.update(file for file in root.glob(pattern) if file.is_file())
    if package.get("license_file"):
        files.add(root/package["license_file"])
    sections.append(f"## {package['name']} {package['version']}\n\nSPDX: {package.get('license') or 'See license file'}\n\nSource: https://crates.io/crates/{package['name']}/{package['version']}\n")
    for file in sorted(files):
        sections.append(f"### {file.name}\n\n```text\n{file.read_text('utf-8',errors='replace').strip()}\n```\n")
sysroot=Path(subprocess.check_output(["rustc","--print","sysroot"],text=True).strip())
for name in ("COPYRIGHT","LICENSE-APACHE","LICENSE-MIT"):
    for base in (sysroot,sysroot/"share/doc/rust"):
        file=base/name
        if file.is_file():
            sections.append(f"## Rust toolchain: {name}\n\n```text\n{file.read_text('utf-8',errors='replace').strip()}\n```\n")
            break
Path(sys.argv[1]).write_text("\n".join(sections),encoding="utf-8")
print("Collected native dependency notices.")
