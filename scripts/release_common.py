"""Shared release checks; no publication side effects."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "dat3133/audio2text-cli"
BINARIES = ("audio2text-windows-x86_64.exe", "audio2text-linux-x86_64")
DOCUMENTS = ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "install.ps1", "install.sh")
NPM_FILES = {"package/" + name for name in ("LICENSE", "README.md", "THIRD_PARTY_NOTICES.md", "bin/audio2text.mjs", "install.mjs", "lib.mjs", "package.json", "release.json")}

def version():
    return tomllib.loads((ROOT / "Cargo.toml").read_text("utf-8"))["package"]["version"]

def validate_version(tag=None):
    value = version()
    if not re.fullmatch(r"\d+\.\d+\.\d+-beta\.\d+", value):
        raise ValueError("This workflow publishes beta previews only")
    if tag is not None and tag != "v" + value:
        raise ValueError("Git tag does not match the source version")
    if json.loads((ROOT / "npm/package.json").read_text("utf-8"))["version"] != value:
        raise ValueError("npm and Cargo versions differ")
    packages = tomllib.loads((ROOT / "Cargo.lock").read_text("utf-8"))["package"]
    if next(p["version"] for p in packages if p["name"] == "audio2text") != value:
        raise ValueError("Cargo.lock version differs")
    for name, pattern in (("install.ps1", r"\$Version = '([^']+)'"), ("install.sh", r"(?m)^version='([^']+)'")):
        match = re.search(pattern, (ROOT / name).read_text("utf-8"))
        if match is None or match[1] != value:
            raise ValueError(f"{name} default version differs")
    return value

def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def npm_command(*args):
    entry = Path(shutil.which("npm.cmd" if os.name == "nt" else "npm") or "").resolve()
    cli = entry.parent / "node_modules/npm/bin/npm-cli.js" if os.name == "nt" else entry
    if not cli.is_file():
        raise ValueError("Cannot locate npm CLI")
    return [shutil.which("node") or "node", str(cli), *map(str, args), "--registry=https://registry.npmjs.org"]

def verify_npm_tags(metadata, value, allow_initial_latest=False):
    tags = metadata["dist-tags"]
    if tags.get("beta") != value:
        raise ValueError("npm beta tag differs from the verified version")
    if tags.get("latest") != value:
        return False
    if not allow_initial_latest:
        raise ValueError("Preview is also tagged latest; the first-publication registry exception requires explicit --allow-initial-latest")
    # npm may retain its staging placeholder for a newly created package.
    versions = set(metadata.get("versions", []))
    if value not in versions or versions - {value, "0.0.0-stage"}:
        raise ValueError("The latest exception only applies to the first released npm version")
    return True

def run(args, **kwargs):
    return subprocess.run(list(map(str,args)), check=True, cwd=ROOT, **kwargs)

def expected_assets(value=None):
    return set(BINARIES + DOCUMENTS + (f"audio2text-cli-{value or version()}.tgz",))

def read_sums(path):
    values = {}
    for line in Path(path).read_text("utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in values:
            raise ValueError("Invalid or duplicate checksum entry")
        values[match[2]] = match[1]
    return values

def verify_release(directory):
    directory = Path(directory)
    value = validate_version()
    names = expected_assets(value)
    sums = read_sums(directory / "SHA256SUMS")
    if set(sums) != names:
        raise ValueError("Release manifest does not contain the exact asset list")
    if {p.name for p in directory.iterdir()} != names | {"SHA256SUMS"}:
        raise ValueError("Unexpected file in release output")
    for name in names:
        if sha256(directory/name) != sums[name]:
            raise ValueError(f"SHA256 mismatch: {name}")
    with tarfile.open(directory/f"audio2text-cli-{value}.tgz", "r:gz") as archive:
        members = archive.getmembers()
        if {m.name for m in members} != NPM_FILES or any(not m.isfile() for m in members) or len(members) != len(NPM_FILES):
            raise ValueError("Unexpected npm package contents")
        pkg = json.load(archive.extractfile("package/package.json"))
        metadata = json.load(archive.extractfile("package/release.json"))
        if pkg["name"] != "audio2text-cli" or pkg["version"] != value or metadata["version"] != value or metadata["repository"] != REPOSITORY:
            raise ValueError("npm package identity/version mismatch")
        if metadata["assets"] != {name:sums[name] for name in BINARIES}:
            raise ValueError("Embedded executable checksums differ from release assets")
    return sums

def public_readme():
    return re.sub(r"\]\((?![a-z]+:|#)([^)]+)\)", lambda m:f"](https://github.com/{REPOSITORY}/blob/v{version()}/{m[1]})", (ROOT/"README.md").read_text("utf-8"))
