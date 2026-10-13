import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tracemalloc
import unittest
from unittest.mock import patch
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("worker", ROOT / "worker/worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".test-data" / ("worker-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)

    def test_git_blob_and_sha256_verification(self):
        file = self.root / "config.json"
        file.write_bytes(b"{}")
        import hashlib
        expected = hashlib.sha1(b"blob 2\0{}").hexdigest()
        self.assertEqual(worker.digest(file, "git-sha1"), expected)
        self.assertTrue(worker.verified(self.root, ["config.json", 2, "git-sha1", expected]))
        self.assertFalse(worker.verified(self.root, ["config.json", 3, "git-sha1", expected]))
        with self.assertRaises(worker.WorkerError):
            worker.verify({"revision": "test", "files": [["config.json", 2, "sha256", "bad"]]}, self.root)

    def test_embedded_worker_protocol_preserves_unicode_and_stdout(self):
        import hashlib
        import subprocess
        file = self.root / "配置 空格.json"
        file.write_bytes(b"{}")
        request = {"action": "verify", "path": str(self.root), "model": {"revision": "test", "files": [[file.name, 2, "git-sha1", hashlib.sha1(b"blob 2\0{}").hexdigest()]]}}
        code = (ROOT / "worker/worker.py").read_bytes()
        payload = str(len(code)).encode() + b"\n" + code + json.dumps(request, ensure_ascii=False).encode("utf-8")
        bootstrap = "import sys; n=int(sys.stdin.buffer.readline()); code=sys.stdin.buffer.read(n).decode('utf-8'); exec(compile(code, '<audio2text-worker>', 'exec'), globals())"
        result = subprocess.run([sys.executable, "-u", "-c", bootstrap], input=payload, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertTrue(json.loads(result.stdout)["verified"])
        self.assertIn("校验", result.stderr.decode("utf-8"))

    def test_download_resume_validates_range(self):
        part = self.root / "part"
        part.write_bytes(b"abc")

        class Response(io.BytesIO):
            status = 206
            headers = {"Content-Range": "bytes 3-5/6"}

        def opener(req, timeout):
            self.assertEqual(req.get_header("Range"), "bytes=3-")
            return Response(b"def")

        worker.download_file("https://example.com", part, 6, opener)
        self.assertEqual(part.read_bytes(), b"abcdef")

    def test_ignored_range_restarts_file(self):
        part = self.root / "part"
        part.write_bytes(b"abc")

        class Response(io.BytesIO):
            status = 200
            headers = {}

        worker.download_file("https://example.com", part, 6, lambda *a, **k: Response(b"abcdef"))
        self.assertEqual(part.read_bytes(), b"abcdef")

    def test_wrong_range_is_rejected(self):
        part = self.root / "part"
        part.write_bytes(b"abc")

        class Response(io.BytesIO):
            status = 206
            headers = {"Content-Range": "bytes 2-5/6"}

        with self.assertRaises(worker.WorkerError):
            worker.download_file("https://example.com", part, 6, lambda *a, **k: Response(b"def"))

    def test_modelscope_unavailable_is_explicit(self):
        with self.assertRaises(worker.WorkerError):
            worker.source_url({"repo": "a/b", "revision": "pin"}, "modelscope", "config.json")

    def test_atomic_output_does_not_overwrite(self):
        file = self.root / "结果 空格.txt"
        worker.atomic_text(file, "第一版", False)
        with self.assertRaises(worker.WorkerError):
            worker.atomic_text(file, "第二版", False)
        self.assertEqual(file.read_text(encoding="utf-8"), "第一版\n")
        worker.atomic_text(file, "第三版", True)
        self.assertEqual(file.read_text(encoding="utf-8"), "第三版\n")

    def test_suspicious_generation_is_not_short_repeated_speech(self):
        self.assertFalse(worker.suspicious("yes yes yes"))
        self.assertTrue(worker.suspicious("some prefix " + "abcde" * 20))

    def test_oom_batch_reduction_is_bounded(self):
        import numpy as np

        class Backend:
            batch_size = 4
            calls = 0
            def infer(self, wavs, language, context):
                self.calls += 1
                if len(wavs) > 1:
                    raise RuntimeError("CUDA out of memory")
                return [(str(float(wavs[0][0])), ["en"], False)]

        backend = Backend()
        chunks = [(np.array([i], dtype=np.float32), i) for i in range(4)]
        warnings = []
        result = worker.recognize(backend, chunks, "en", "", warnings)
        self.assertEqual([r[0] for r in result], ["0.0", "1.0", "2.0", "3.0"])
        self.assertLessEqual(backend.calls, 7)
        self.assertEqual(backend.batch_size, 1)
        self.assertTrue(warnings)

    def test_single_chunk_oom_does_not_change_model(self):
        import numpy as np
        class Backend:
            batch_size = 1
            def infer(self, *args):
                raise RuntimeError("out of memory")
        with self.assertRaises(worker.WorkerError) as error:
            worker.recognize(Backend(), [(np.zeros(10), 0)], "auto", "", [])
        self.assertEqual(error.exception.code, "OUT_OF_MEMORY")

    def test_suspicious_chunk_recursion_has_limit(self):
        import numpy as np
        class Backend:
            batch_size = 1
            calls = 0
            def infer(self, wavs, *args):
                self.calls += 1
                return [("long suspicious output", ["en"], True) for w in wavs]
        backend = Backend()
        warnings = []
        result = worker.recognize(backend, [(np.zeros(worker.SR * 60), 0)], "auto", "", warnings)
        self.assertLessEqual(backend.calls, 15)
        self.assertTrue(result)
        self.assertTrue(any("请复核" in w for w in warnings))

    def test_two_hour_decode_has_bounded_buffers(self):
        # A virtual ffmpeg pipe produces two hours of PCM without a huge fixture.
        # This measures the actual buffering/splitting code, not inference speed.
        total = worker.SR * 7200 * 4
        reads = []

        class Pipe:
            remaining = total
            def read(self, count):
                reads.append(count)
                n = min(count, self.remaining)
                self.remaining -= n
                return b"\0" * n
            def close(self):
                pass

        class Process:
            stdout = Pipe()
            stderr = io.BytesIO()
            def poll(self): return 0
            def wait(self): return 0

        with patch.object(worker.subprocess, "Popen", return_value=Process()):
            tracemalloc.start()
            duration = 0
            count = 0
            for wav, offset in worker.decode_segments(self.root / "virtual.wav", 60, "fake-ffmpeg"):
                self.assertAlmostEqual(offset, duration, places=6)
                duration += len(wav) / worker.SR
                count += 1
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        self.assertAlmostEqual(duration, 7200)
        self.assertGreater(count, 100)
        self.assertLessEqual(max(reads), 63 * worker.SR * 4)
        self.assertLess(peak, 40 * 1024 * 1024)

    def test_real_ffmpeg_unicode_path_and_duration(self):
        path = self.root / "录音 空格.wav"
        with wave.open(str(path), "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(worker.SR)
            file.writeframes(b"\0" * worker.SR * 2)
        segments = list(worker.decode_segments(path, 5))
        self.assertEqual(sum(len(w) for w, _ in segments), worker.SR)

    def test_invalid_media_is_input_error(self):
        path = self.root / "invalid.mp4"
        path.write_bytes(b"invalid")
        with self.assertRaises(worker.WorkerError) as e:
            list(worker.decode_segments(path, 5))
        self.assertEqual(e.exception.exit, 4)

    def test_batch_failure_keeps_success_and_order(self):
        import numpy as np
        class Backend:
            batch_size = 1
            def infer(self, wavs, language, context): return [("recognized", ["en"], False)]
        request = {"files": [{"input": "bad.wav", "error": {"code": "INPUT_INVALID", "message": "missing", "hint": "fix"}}, {"input": "good.wav"}], "model": {"id": "test", "backend": "fake"}, "device": "cpu", "language": "auto", "segment_seconds": 60}
        def decoder(*args):
            yield np.zeros(16000), 0
        with patch.object(worker, "decode_segments", side_effect=decoder):
            results = worker.transcribe(request, Backend())["results"]
        self.assertEqual([r["input"] for r in results], ["bad.wav", "good.wav"])
        self.assertEqual([r["status"] for r in results], ["error", "ok"])

    def test_offline_worker_network_is_blocked(self):
        # Isolate the socket monkeypatch from the test runner.
        import subprocess
        code = f"import runpy,socket; m=runpy.run_path({str(ROOT / 'worker/worker.py')!r}); m['offline'](); socket.create_connection(('example.com',443))"
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("network access is disabled", proc.stderr)
        for operation in ("socket.getaddrinfo('example.com',443)", "socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'test',('127.0.0.1',1))"):
            code = f"import runpy,socket; m=runpy.run_path({str(ROOT / 'worker/worker.py')!r}); m['offline'](); {operation}"
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("network access is disabled", proc.stderr)


if __name__ == "__main__":
    unittest.main()
