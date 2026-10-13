"""Validate local, anonymous GitHub, or registry installations without ASR downloads."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
from release_common import BINARIES, REPOSITORY, ROOT, expected_assets, npm_command, read_sums, run, sha256, validate_version, verify_npm_tags, verify_release

parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=("local", "online", "registry"), required=True)
parser.add_argument("--directory")
parser.add_argument("--tag",help="Verify that the target release tag matches the source version")
parser.add_argument("--allow-initial-latest",action="store_true",help="Accept the registry's latest tag on the first publication only")
args = parser.parse_args()
value = validate_version(args.tag)
base = ROOT / ".test-data"
base.mkdir(exist_ok=True)
work = Path(tempfile.mkdtemp(prefix="install 中文 空格-", dir=base))
env = os.environ.copy()
env["AUDIO2TEXT_HOME"] = str(work / "unused-model-home")

def check(binary):
    for arguments in (["--version"], ["--help"], ["doctor"], ["init", "--dry-run", "--device", "cpu"], ["init"]):
        proc = subprocess.run([*map(str,binary), *arguments], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        expected = 2 if arguments == ["init"] else 0
        if proc.returncode != expected:
            raise ValueError(f"CLI check failed: {arguments}: {proc.stderr} {proc.stdout}")
        if arguments == ["--version"] and proc.stdout.strip() != f"audio2text {value}":
            raise ValueError("CLI version differs from package/tag")
        if arguments[0] in ("doctor", "init"):
            result = json.loads(proc.stdout)
            if result["schema_version"] != "1" or (arguments == ["init"] and result["error"]["code"] != "INTERACTION_REQUIRED"):
                raise ValueError("Unexpected CLI JSON interface")
    if Path(env["AUDIO2TEXT_HOME"]).exists():
        raise ValueError("Installation or read-only commands prepared model resources")

if args.mode == "registry":
    raw = run(npm_command("view",f"audio2text-cli@{value}","--json","--loglevel=error"),capture_output=True,text=True,encoding="utf-8")
    metadata = json.loads(raw.stdout)
    if verify_npm_tags(metadata,value,args.allow_initial_latest):
        print("Accepted first-publication registry exception: latest also points to this beta.")
    archive = work/f"audio2text-cli-{value}.tgz"
    with urllib.request.urlopen(metadata["dist"]["tarball"],timeout=120) as response:
        archive.write_bytes(response.read(2*1024*1024))
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
    if integrity != metadata["dist"]["integrity"]:
        raise ValueError("Registry tarball integrity mismatch")
    manifest = work/"SHA256SUMS"
    with urllib.request.urlopen(f"https://github.com/{REPOSITORY}/releases/download/v{value}/SHA256SUMS",timeout=120) as response:
        manifest.write_bytes(response.read(4096))
    if sha256(archive) != read_sums(manifest).get(archive.name):
        raise ValueError("Registry tarball differs from the verified GitHub Release")
else:
    directory = Path(args.directory).resolve() if args.directory else work/"release"
    if args.mode == "online":
        directory.mkdir(parents=True)
        url = f"https://github.com/{REPOSITORY}/releases/download/v{value}"
        for name in ["SHA256SUMS", *sorted(expected_assets(value))]:
            with urllib.request.urlopen(f"{url}/{name}",timeout=120) as response:
                with (directory/name).open("wb") as stream:
                    size = 0
                    while chunk := response.read(1024*1024):
                        size += len(chunk)
                        if size > 100*1024*1024:
                            raise ValueError("Release attachment exceeds size limit")
                        stream.write(chunk)
        verify_release(directory)
    name = BINARIES[0 if os.name == "nt" else 1]
    sums = read_sums(directory/"SHA256SUMS")
    if sha256(directory/name) != sums[name]:
        raise ValueError("Installer input checksum mismatch")
    install = work/"script-bin"
    script = directory/("install.ps1" if os.name == "nt" else "install.sh")
    if not script.is_file():
        script = ROOT/script.name
    if os.name == "nt":
        command = [shutil.which("pwsh") or "powershell", "-NoProfile", "-File", script, "-InstallDir", install, "-Version", value, "-NoInit", "-NoPathUpdate"]
        if args.mode == "local":
            command.extend(["-ArtifactPath",directory/name,"-ChecksumPath",directory/"SHA256SUMS"])
    else:
        command = ["sh",script,"--install-dir",install,"--version",value,"--no-init"]
        if args.mode == "local":
            command.extend(["--artifact-path",directory/name,"--checksum-path",directory/"SHA256SUMS"])
    run(command,env=env,timeout=240)
    installed=install/("audio2text.exe" if os.name == "nt" else "audio2text")
    check([installed])
    if args.mode == "local":
        before=sha256(installed)
        broken=work/"bad-checksums"
        broken.write_text(f"{'0'*64}  {name}\n",encoding="utf-8")
        broken_command=[str(v) for v in command]
        flag="-ChecksumPath" if os.name == "nt" else "--checksum-path"
        broken_command[broken_command.index(flag)+1]=str(broken)
        failed=subprocess.run(broken_command,capture_output=True,text=True,encoding="utf-8",env=env,timeout=60)
        if failed.returncode == 0 or sha256(installed) != before:
            raise ValueError("Failed checksum damaged a working installation")
        print("Local installer, version and JSON smoke checks passed.")
        raise SystemExit(0)
    archive = directory/f"audio2text-cli-{value}.tgz"

for ignore_scripts in (False, True):
    prefix = work/("npm-skipped" if ignore_scripts else "npm-normal")
    command = npm_command("install","--global","--prefix",prefix,archive,"--foreground-scripts","--no-audit","--no-fund","--loglevel=error")
    if ignore_scripts:
        command.append("--ignore-scripts")
    run(command,env=env,timeout=240)
    launcher = prefix/("audio2text.cmd" if os.name == "nt" else "bin/audio2text")
    if not launcher.is_file():
        raise ValueError("npm command shim was not installed")
    package = prefix/("node_modules/audio2text-cli" if os.name == "nt" else "lib/node_modules/audio2text-cli")
    check([shutil.which("node") or "node",package/"bin/audio2text.mjs"])
print(f"{args.mode} script/npm installation checks passed on {os.name}.")
