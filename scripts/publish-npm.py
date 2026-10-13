"""Publish the exact verified CI tarball with the beta dist-tag."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
from release_common import npm_command, run, validate_version, verify_release

directory = Path("dist/release")
value = validate_version()
verify_release(directory)
archive = directory/f"audio2text-cli-{value}.tgz"
integrity = "sha512-"+base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
query = subprocess.run(npm_command("view",f"audio2text-cli@{value}","--json","--loglevel=error"),capture_output=True,text=True,encoding="utf-8")
if query.returncode == 0:
    if json.loads(query.stdout)["dist"]["integrity"] != integrity:
        raise SystemExit("Existing npm version contains different bytes; use a new version")
    print("Existing identical npm version verified; no publication repeated.")
elif 'E404' in query.stderr:
    run(npm_command("publish",archive,"--tag","beta","--access","public"))
else:
    raise SystemExit(query.stderr)
published = json.loads(run(npm_command("view",f"audio2text-cli@{value}","--json","--loglevel=error"),capture_output=True,text=True,encoding="utf-8").stdout)
if published["dist"]["integrity"] != integrity or published["dist-tags"].get("beta") != value or published["dist-tags"].get("latest") == value:
    raise SystemExit("Published tarball integrity or beta/latest tags differ")
print(f"Published https://www.npmjs.com/package/audio2text-cli/v/{value}")
