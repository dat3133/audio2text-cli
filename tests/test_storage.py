"""Storage resolution is isolated from the real user's location record."""
import json
import os
from pathlib import Path
import subprocess
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get("AUDIO2TEXT_BIN", ROOT / "target/debug" / ("audio2text.exe" if os.name == "nt" else "audio2text")))


@unittest.skipUnless(BIN.is_file(), "Build CLI first")
class StorageTests(unittest.TestCase):
    def setUp(self):
        self.work = ROOT / ".test-data" / ("storage-" + uuid.uuid4().hex)
        self.work.mkdir(parents=True)
        self.base = self.work / "profile/audio2text"
        self.env = os.environ.copy()
        self.env.pop("AUDIO2TEXT_HOME", None)
        self.env.update(LOCALAPPDATA=str(self.work / "profile"), XDG_DATA_HOME=str(self.work / "profile"), HOME=str(self.work / "home"))

    def call(self, *args, env=None):
        process = subprocess.run([str(BIN), *map(str, args)], capture_output=True, text=True, encoding="utf-8", timeout=30, env=env or self.env)
        value = json.loads(process.stdout)
        self.assertEqual(value["schema_version"], "1")
        return process, value

    def remember(self, target, create=True):
        if create:
            target.mkdir(parents=True, exist_ok=True)
        self.base.mkdir(parents=True, exist_ok=True)
        record = self.base / "location.json"
        record.write_text(json.dumps({"version": 1, "data_dir": str(target)}, ensure_ascii=False), encoding="utf-8")
        return record

    def test_legacy_default_and_read_only_queries(self):
        process, value = self.call("doctor")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(Path(value["data"]["data_dir"]), self.base)
        self.assertFalse(self.base.exists())
        target = self.work / "模型 中文 空格"
        record = self.remember(target)
        before = record.read_bytes()
        for command in (("doctor",), ("models", "list"), ("init", "--dry-run", "--model", "whisper/tiny", "--device", "cpu")):
            process, value = self.call(*command)
            self.assertEqual(process.returncode, 0, value)
            if command[0] != "models":
                self.assertEqual(Path(value["data"]["data_dir"]), target)
            self.assertEqual(record.read_bytes(), before)
            self.assertEqual(list(target.iterdir()), [])

    def test_explicit_and_environment_overrides_do_not_rewrite_location(self):
        saved = self.work / "saved"
        record = self.remember(saved)
        before = record.read_bytes()
        explicit = self.work / "explicit"
        environment = self.work / "environment"
        env = dict(self.env, AUDIO2TEXT_HOME=str(environment))
        for args, settings, expected in [
            (("--data-dir", explicit, "doctor"), env, explicit),
            (("doctor",), env, environment),
            (("doctor",), self.env, saved),
        ]:
            process, value = self.call(*args, env=settings)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(Path(value["data"]["data_dir"]), expected)
            self.assertEqual(record.read_bytes(), before)
        self.assertFalse(explicit.exists())
        self.assertFalse(environment.exists())

    def test_invalid_or_missing_saved_location_never_falls_back(self):
        target = self.work / "unavailable"
        record = self.remember(target, create=False)
        blocker = self.work / "saved-file"
        blocker.write_text("keep", encoding="utf-8")
        for content in (record.read_text(encoding="utf-8"), "{", '{"version":9,"data_dir":"relative"}', '{"version":1,"data_dir":"relative"}', json.dumps({"version": 1, "data_dir": str(blocker)})):
            record.write_text(content, encoding="utf-8")
            process, value = self.call("doctor")
            self.assertEqual(process.returncode, 2)
            self.assertEqual(value["error"]["code"], "CONFIG_INVALID")
            process, value = self.call("--data-dir", self.work / "override", "doctor")
            self.assertEqual(process.returncode, 0)
        self.assertFalse(target.exists())

    def test_relative_directory_is_reported_as_absolute_without_creating_it(self):
        relative = self.work.relative_to(ROOT) / "相对路径 空格" / ".." / "新目录"
        process, value = self.call("--data-dir", relative, "init", "--dry-run", "--model", "whisper/tiny", "--device", "cpu")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(Path(value["data"]["data_dir"]), self.work / "新目录")
        self.assertFalse((self.work / "新目录").exists())
        self.assertFalse(self.base.exists())

    @unittest.skipUnless(os.name == "nt", "Windows missing-volume validation")
    def test_unavailable_windows_volume_is_rejected_without_fallback(self):
        from string import ascii_uppercase
        volume = next((Path(letter + ":/") for letter in reversed(ascii_uppercase) if not Path(letter + ":/").exists()), None)
        if volume is None:
            self.skipTest("All drive letters exist")
        process, value = self.call("--data-dir", volume / "audio2text", "init", "--dry-run", "--model", "whisper/tiny", "--device", "cpu")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(value["error"]["code"], "CONFIG_INVALID")
        self.assertFalse(self.base.exists())

    def test_path_pointing_to_file_is_rejected_before_preparation(self):
        target = self.work / "file"
        target.write_text("keep this file", encoding="utf-8")
        for arguments in (("--dry-run",), ("--non-interactive",)):
            process, value = self.call("--data-dir", target, "init", *arguments, "--model", "whisper/tiny", "--device", "cpu")
            self.assertEqual(process.returncode, 2)
            self.assertEqual(value["error"]["code"], "CONFIG_INVALID")
            self.assertEqual(target.read_text(encoding="utf-8"), "keep this file")
            self.assertFalse(self.base.exists())

    def test_all_commands_resolve_the_saved_configuration(self):
        target = self.work / "saved"
        self.remember(target)
        model_dir = target / "models/whisper-tiny"
        model_dir.mkdir(parents=True)
        config = {"version": 1, "default_model": "whisper/tiny", "device": "cpu", "language": "auto", "profile": "balanced", "source": "auto", "batch_size": 0, "segment_seconds": 60,
                  "installed": [{"id": "whisper/tiny", "path": str(model_dir), "revision": "fixture", "verified": True}]}
        (target / "config.json").write_text(json.dumps(config), encoding="utf-8")
        _, value = self.call("doctor")
        self.assertEqual(value["data"]["default_model"], "whisper/tiny")
        _, value = self.call("models", "list")
        tiny = next(item for item in value["data"]["models"] if item["model"]["id"] == "whisper/tiny")
        self.assertTrue(tiny["installed"])
        media = self.work / "input.wav"
        media.write_bytes(b"RIFF")
        process, value = self.call("transcribe", media)
        self.assertEqual(process.returncode, 3)
        self.assertEqual(value["error"]["message"], "推理运行时尚未准备")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0, "POSIX unprivileged permissions check")
    def test_unwritable_target_fails_before_downloading_and_preserves_location(self):
        record = self.remember(self.work / "saved")
        before = record.read_bytes()
        target = self.work / "readonly"
        target.mkdir()
        target.chmod(0o555)
        self.addCleanup(target.chmod, 0o755)
        process, value = self.call("--data-dir", target, "init", "--non-interactive", "--model", "whisper/tiny", "--device", "cpu")
        self.assertEqual(process.returncode, 7)
        self.assertEqual(value["error"]["code"], "WRITE_FAILED")
        self.assertEqual(record.read_bytes(), before)
        self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
