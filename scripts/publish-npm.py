"""Publish the exact verified CI tarball with the beta dist-tag."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import time
from release_common import npm_command, run, validate_version, verify_npm_tags, verify_release

def wait_for_version(fetch, attempts=40, sleep=time.sleep):
    for attempt in range(attempts):
        result=fetch()
        if result.returncode == 0:
            return json.loads(result.stdout)
        if not any(code in result.stderr for code in ("E404","E503","ETIMEDOUT","ECONNRESET")):
            raise RuntimeError(result.stderr)
        if attempt+1 < attempts:
            print("npm is processing the publication; waiting for registry availability.",flush=True)
            sleep(15)
    raise RuntimeError("npm accepted publication but the version is not visible yet; rerun with --verify-only.")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--verify-only",action="store_true")
    parser.add_argument("--allow-initial-latest",action="store_true",help="Accept the registry's latest tag on the first publication only")
    args=parser.parse_args()
    directory=Path("dist/release")
    value=validate_version()
    verify_release(directory)
    archive=directory/f"audio2text-cli-{value}.tgz"
    integrity="sha512-"+base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
    def fetch():
        return subprocess.run(npm_command("view",f"audio2text-cli@{value}","--json","--loglevel=error"),capture_output=True,text=True,encoding="utf-8")
    query=fetch()
    if query.returncode == 0:
        if json.loads(query.stdout)["dist"]["integrity"] != integrity:
            raise RuntimeError("Existing npm version contains different bytes; use a new version")
        print("Existing identical npm version verified; no publication repeated.",flush=True)
    elif "E404" in query.stderr:
        if not args.verify_only:
            run(npm_command("publish",archive,"--tag","beta","--access","public"))
    else:
        raise RuntimeError(query.stderr)
    published=wait_for_version(fetch)
    if published["dist"]["integrity"] != integrity:
        raise RuntimeError("Published tarball integrity differs")
    if verify_npm_tags(published,value,args.allow_initial_latest):
        print("Accepted first-publication registry exception: latest also points to this beta; use @beta explicitly.",flush=True)
    print(f"Published https://www.npmjs.com/package/audio2text-cli/v/{value}")

if __name__ == "__main__":
    main()
