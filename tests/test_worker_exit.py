"""Exercise native-model teardown through real one-shot worker subprocesses."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


class WorkerExitTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".test-data" / ("worker-exit-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.output = self.root / "结果 空格.txt"

    def run_worker(self, *, action="transcribe", mode="ok", platform="win32",
                   device="cuda:0", backend="faster-whisper", destructive=True, no_text=True):
        # A destructor that exits the process reproduces the lost-response bug
        # without a GPU, an OS crash dialog, or dependency downloads in CI.
        code = f"""
import os, sys
from pathlib import Path
import numpy as np
namespace = {{"__name__": "worker_fixture"}}
path = Path({str(ROOT / 'worker/worker.py')!r})
exec(compile(path.read_text('utf-8'), str(path), 'exec'), namespace)
mode = {mode!r}
class Backend:
    batch_size = 1
    def __init__(self, *args): pass
    def infer(self, wavs, *args):
        if mode == 'error': raise RuntimeError('fixture inference failed')
        if mode == 'cancel': raise KeyboardInterrupt()
        if mode == 'crash': os._exit(73)
        os.write(1, b'native backend diagnostic\\n')
        text = ' '.join(map(str, range(20000))) if mode == 'large' else '已识别'
        return [(text, ['zh'], False) for wav in wavs]
    def __del__(self):
        if {destructive!r}: os._exit(99)
        os.write(2, b'NORMAL_MODEL_RELEASED\\n')
namespace['Backend'] = Backend
namespace['decode_segments'] = lambda *a, **k: (chunk for chunk in [(np.zeros(16000, dtype=np.float32), 0)])
namespace['check'] = lambda *a: {{'ffmpeg': 'fixture', 'versions': {{}}}}
sys.platform = {platform!r}
raise SystemExit(namespace['main']())
"""
        request = {
            "action": action, "model": {"id": "fixture", "backend": backend, "files": []},
            "path": str(self.root), "device": device, "batch_size": 1,
            "segment_seconds": 60, "language": "zh", "no_text": no_text,
            "files": [{"input": "fixture.wav", "output": str(self.output)}],
        }
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        return subprocess.run([sys.executable, "-u", "-c", code], input=json.dumps(request),
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)

    def test_windows_cuda_returns_complete_result_and_file_without_destroying_model(self):
        proc = self.run_worker()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)["results"][0]
        self.assertEqual(result["status"], "ok", result)
        self.assertIsNone(result["text"])
        self.assertEqual(Path(result["output"]), self.output.resolve())
        self.assertEqual(self.output.read_text("utf-8"), "已识别\n")
        self.assertIn("native backend diagnostic", proc.stderr)

    def test_windows_cuda_selftest_returns_before_destructive_cleanup(self):
        proc = self.run_worker(action="selftest")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ready"])

    def test_large_response_is_completely_flushed_before_process_exit(self):
        proc = self.run_worker(mode="large", no_text=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        item = json.loads(proc.stdout)["results"][0]
        self.assertEqual(item["text"], " ".join(map(str, range(20000))))
        self.assertGreater(len(proc.stdout), 100000)

    def test_inference_error_and_cancel_keep_their_error_codes(self):
        for mode, status, code in (("error", 5, "INFERENCE_FAILED"), ("cancel", 130, "CANCELLED")):
            with self.subTest(mode=mode):
                proc = self.run_worker(action="selftest", mode=mode)
                self.assertEqual(proc.returncode, status, proc.stderr)
                self.assertEqual(json.loads(proc.stdout)["error"]["code"], code)

    def test_transcription_failure_is_not_replaced_with_success(self):
        proc = self.run_worker(mode="error")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # The CLI maps each item's exit to its public exit code after parsing.
        item = json.loads(proc.stdout)["results"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["error"]["code"], "INFERENCE_FAILED")
        self.assertEqual(item["exit"], 5)
        self.assertFalse(self.output.exists())

    def test_output_conflict_keeps_existing_file_and_error(self):
        self.output.write_text("existing", encoding="utf-8")
        proc = self.run_worker()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        item = json.loads(proc.stdout)["results"][0]
        self.assertEqual(item["error"]["code"], "OUTPUT_EXISTS")
        self.assertEqual(item["exit"], 7)
        self.assertEqual(self.output.read_text("utf-8"), "existing")

    def test_native_crash_during_inference_still_fails_without_a_result(self):
        proc = self.run_worker(mode="crash")
        self.assertEqual(proc.returncode, 73)
        self.assertEqual(proc.stdout, "")
        self.assertFalse(self.output.exists())

    def test_other_devices_platforms_and_backends_keep_normal_cleanup(self):
        for options in ({"device": "cpu"}, {"platform": "linux"}, {"backend": "qwen3-asr"}):
            with self.subTest(options=options):
                proc = self.run_worker(action="selftest", destructive=False, **options)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(json.loads(proc.stdout)["ready"])
                self.assertIn("NORMAL_MODEL_RELEASED", proc.stderr)


if __name__ == "__main__":
    unittest.main()
