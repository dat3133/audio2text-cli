import argparse
import os
from release_common import validate_version, verify_release

parser = argparse.ArgumentParser()
parser.add_argument("--tag")
parser.add_argument("--directory")
args = parser.parse_args()
if os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_REF_TYPE") != "tag":
    raise SystemExit("Run this workflow at a version tag: gh workflow run release.yml --ref v<version>")
print("Verified version:", validate_version(args.tag))
if args.directory:
    verify_release(args.directory)
    print("Release files and npm tarball verified.")
