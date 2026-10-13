import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from release_common import BINARIES, NPM_FILES, read_sums, validate_version, verify_release, version

class ReleaseTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows installer regression')
    def test_windows_binary_http_manifest(self):
        binary=Path(os.environ.get('AUDIO2TEXT_BIN',ROOT/'target/debug/audio2text.exe'))
        if not binary.is_file():
            self.skipTest('Build CLI before installer regression')
        import shutil
        proc=subprocess.run([shutil.which('pwsh') or 'powershell','-NoProfile','-File',str(ROOT/'tests/test_installer.ps1'),'-Binary',str(binary.resolve())],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)

    def test_tag_and_version_consistency(self):
        self.assertEqual(validate_version("v"+version()),version())
        with self.assertRaises(ValueError):
            validate_version("v0.0.0-beta.1")

    def test_checksum_manifest_rejects_duplicates_and_paths(self):
        work=ROOT/".test-data"/("manifest-"+uuid.uuid4().hex)
        work.mkdir(parents=True)
        file=work/"SHA256SUMS"
        for text in (f"{'a'*64}  file\n{'b'*64}  file\n",f"{'a'*64}  ../file\n"):
            file.write_text(text,encoding="utf-8")
            with self.assertRaises(ValueError): read_sums(file)

    def test_clean_staging_and_exact_tarball_files(self):
        work=ROOT/".test-data"/("release 中文 空格-"+uuid.uuid4().hex)
        parts=work/"parts"
        parts.mkdir(parents=True)
        for name in BINARIES:
            directory=parts/name
            directory.mkdir()
            (directory/name).write_bytes(("binary fixture "+name).encode())
        (parts/"native-notices-fixture.md").write_text("Fixture dependency notices",encoding="utf-8")
        output=work/"release"
        proc=subprocess.run([sys.executable,"scripts/assemble-release.py",str(parts),str(output)],cwd=ROOT,capture_output=True,text=True,encoding="utf-8")
        self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)
        sums=verify_release(output)
        with tarfile.open(output/f"audio2text-cli-{version()}.tgz","r:gz") as archive:
            self.assertEqual(set(archive.getnames()),NPM_FILES)
            metadata=json.load(archive.extractfile("package/release.json"))
            self.assertEqual(metadata["assets"],{name:sums[name] for name in BINARIES})
            readme=archive.extractfile("package/README.md").read().decode()
            self.assertNotIn("](CONTRIBUTING.md)",readme)
        (output/BINARIES[0]).write_bytes(b"corrupt")
        with self.assertRaises(ValueError): verify_release(output)
        repeated=subprocess.run([sys.executable,"scripts/assemble-release.py",str(parts),str(output)],cwd=ROOT,capture_output=True,text=True)
        self.assertNotEqual(repeated.returncode,0)

    def test_packager_rejects_embedded_binary_option(self):
        result=subprocess.run(["node","scripts/prepare-npm.mjs","--binary","unused"],cwd=ROOT,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("unsupported",result.stderr)

if __name__ == "__main__":
    unittest.main()
