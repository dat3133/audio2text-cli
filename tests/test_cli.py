import json
import os
from pathlib import Path
import subprocess
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BIN = ROOT / "target/debug" / ("audio2text.exe" if os.name == "nt" else "audio2text")
BIN = Path(os.environ.get("AUDIO2TEXT_BIN", DEFAULT_BIN))


@unittest.skipUnless(BIN.is_file(), "Build CLI first: cargo build")
class CliTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".test-data" / ("CLI 中文 空格-" + uuid.uuid4().hex)

    def run_cli(self, *args):
        proc = subprocess.run([str(BIN), "--data-dir", str(self.root), *args], capture_output=True, text=True, encoding="utf-8", timeout=30)
        value = json.loads(proc.stdout)
        self.assertEqual(value["schema_version"], "1")
        return proc, value

    def test_doctor_is_read_only(self):
        proc, value = self.run_cli("doctor")
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(value["data"]["ready"])
        self.assertFalse(self.root.exists())

    def test_dry_run_does_not_create_data_directory(self):
        proc, value = self.run_cli("init", "--dry-run", "--model", "whisper/tiny", "--device", "cpu")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(value["data"]["model"]["id"], "whisper/tiny")
        self.assertFalse(self.root.exists())

    def test_non_tty_init_never_waits(self):
        proc, value = self.run_cli("init")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(value["error"]["code"], "INTERACTION_REQUIRED")
        self.assertFalse(self.root.exists())

    def test_unknown_argument_returns_json(self):
        proc, value = self.run_cli("transcribe", "a.wav", "--unknown")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(value["error"]["code"], "INVALID_ARGUMENT")

    def test_uninitialized_transcription_never_downloads(self):
        proc, value = self.run_cli("transcribe", "a.wav")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(value["error"]["code"], "ENV_NOT_READY")
        self.assertFalse(self.root.exists())

    def test_output_collision_precedes_inference(self):
        proc, value = self.run_cli("transcribe", "a/test.wav", "b/test.mp4", "--output-dir", str(self.root / "out"))
        self.assertEqual(proc.returncode, 7)
        self.assertEqual(value["error"]["code"], "OUTPUT_CONFLICT")

    def test_invalid_import_does_not_commit_config(self):
        proc, value = self.run_cli("models", "import", "missing", "--id", "whisper/tiny")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(value["error"]["code"], "MODEL_INVALID")
        self.assertFalse((self.root / "config.json").exists())

    def test_invalid_source_fails_before_setup(self):
        proc, value = self.run_cli("init", "--dry-run", "--model", "whisper/tiny", "--source", "modelscope", "--device", "cpu")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(value["error"]["code"], "SOURCE_UNAVAILABLE")
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
