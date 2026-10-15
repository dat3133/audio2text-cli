"""Offline progress, resumed downloads, stderr isolation and terminal regressions."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("progress_worker", ROOT / "worker/worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".test-data" / ("progress-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        settings = patch.dict(os.environ, TERM="xterm-256color", AUDIO2TEXT_PLAIN="0")
        settings.start()
        self.addCleanup(settings.stop)

    def test_known_unknown_and_long_unicode_lines_stay_in_one_row(self):
        known = worker.progress_line("model.bin", 40 << 20, 100 << 20, 0, 100)
        self.assertIn("40%", known)
        self.assertIn("40.0/100.0 MiB", known)
        unknown = worker.progress_line("uv", 40 << 20, None, 1, 100)
        self.assertNotIn("%", unknown)
        self.assertIn("40.0 MiB", unknown)
        for columns in (1, 16, 40, 80, 120):
            for total in (None, 100 << 20):
                line = worker.progress_line("模型 e\u0301\n\x1b文件" * 30, 30 << 20, total, 0, columns)
                self.assertLess(worker.text_width(line), columns)
                self.assertFalse(any(ord(c) < 32 for c in line))

    def test_updates_are_limited_and_finish_always_ends_the_row(self):
        stream, clock = Terminal(), [0.0]
        with worker.DownloadProgress("model.bin", 1000, stream, lambda: clock[0]) as progress:
            for value in range(1000):
                clock[0] = value / 1000
                progress.update(value)
            self.assertEqual(stream.getvalue().count("\r"), 5)
            progress.update(1000)
        self.assertIn("100%", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("下载完成，准备校验\n"))

    def test_non_terminal_plain_and_dumb_modes_have_no_control_codes(self):
        for stream, env in [(io.StringIO(), {}), (Terminal(), {"TERM": "dumb"}), (Terminal(), {"AUDIO2TEXT_PLAIN": "1"})]:
            with self.subTest(env=env), patch.dict(os.environ, env):
                with worker.DownloadProgress("model.bin", 6, stream) as progress:
                    progress.update(3)
                    progress.update(6)
                self.assertNotIn("\r", stream.getvalue())
                self.assertNotIn("\x1b", stream.getvalue())
                self.assertIn("下载完成", stream.getvalue())

    def test_cancellation_closes_the_row_without_claiming_success(self):
        stream = Terminal()
        with self.assertRaises(KeyboardInterrupt):
            with worker.DownloadProgress("model.bin", 6, stream) as progress:
                progress.update(3)
                raise KeyboardInterrupt
        self.assertIn("50%", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("下载未完成，保留续传数据\n"))
        self.assertNotIn("100%", stream.getvalue())

    def test_completion_and_failure_summaries_fit_narrow_terminals(self):
        for cancel in (False, True):
            stream = Terminal()
            label = "中文 模型文件-" * 20
            with patch.object(worker.DownloadProgress, "columns", return_value=40):
                progress = worker.DownloadProgress(label, 6, stream)
                progress.update(3 if cancel else 6)
                progress.__exit__(KeyboardInterrupt if cancel else None, None, None)
            summary = stream.getvalue().splitlines()[-1]
            self.assertLess(worker.text_width(summary), 40)
            self.assertIn("下载未完成" if cancel else "下载完成", summary)

    def test_resume_and_server_restart_begin_at_the_actual_offset(self):
        for status, payload, expected_start in [(206, b"def", 3), (200, b"abcdef", 0)]:
            with self.subTest(status=status):
                part = self.root / f"model-{status}.part"
                part.write_bytes(b"abc")
                updates, stream = [], Terminal()
                class Response(io.BytesIO):
                    headers = {"Content-Range": "bytes 3-5/6"}
                Response.status = status
                class Recording(worker.DownloadProgress):
                    def __init__(self, label, total):
                        super().__init__(label, total, stream)
                    def update(self, done):
                        updates.append(done)
                        super().update(done)
                with patch.object(worker, "DownloadProgress", Recording):
                    worker.download_file("https://example.invalid/model", part, 6, lambda *a, **k: Response(payload))
                self.assertEqual(updates[0], expected_start)
                self.assertEqual(updates[-1], 6)
                self.assertEqual(part.read_bytes(), b"abcdef")

    def test_interrupted_stream_retains_real_bytes_and_can_resume(self):
        part, stream = self.root / "model.part", Terminal()
        class Interrupted(io.BytesIO):
            status, headers = 200, {}
            def read(self, size=-1):
                if self.tell():
                    raise OSError("connection interrupted")
                return super().read(size)
        original = worker.DownloadProgress
        with patch.object(worker, "DownloadProgress", lambda label, total: original(label, total, stream)):
            with self.assertRaises(OSError):
                worker.download_file("https://example.invalid/model", part, 6, lambda *a, **k: Interrupted(b"abc"))
        self.assertEqual(part.read_bytes(), b"abc")
        self.assertNotIn("100%", stream.getvalue())
        self.assertIn("下载未完成", stream.getvalue())
        class Resumed(io.BytesIO):
            status, headers = 206, {"Content-Range": "bytes 3-5/6"}
        with patch.object(worker, "DownloadProgress", lambda label, total: original(label, total, stream)):
            worker.download_file("https://example.invalid/model", part, 6, lambda *a, **k: Resumed(b"def"))
        self.assertEqual(part.read_bytes(), b"abcdef")
        self.assertIn("100%", stream.getvalue())

    def test_checksum_failure_retries_and_cached_files_skip_downloads(self):
        model = {"revision": "fixture", "files": [["model.bin", 3, "sha256", "0" * 64]]}
        stream, original = io.StringIO(), worker.download_file
        class Response(io.BytesIO):
            status, headers = 200, {}
        def download(url, part, size):
            return original(url, part, size, lambda *a, **k: Response(b"abc"))
        with patch.object(worker, "source_url", return_value="https://example.invalid/model"), patch.object(worker.urllib.request, "urlopen", return_value=contextlib.nullcontext()), patch.object(worker, "download_file", side_effect=download) as downloads, patch.object(worker.time, "sleep"), contextlib.redirect_stderr(stream):
            with self.assertRaises(worker.WorkerError) as failure:
                worker.download(model, self.root, "huggingface")
            self.assertEqual(failure.exception.code, "DOWNLOAD_FAILED")
            self.assertEqual(downloads.call_count, 3)
        self.assertFalse((self.root / "model.bin").exists())
        self.assertIn("校验 model.bin", stream.getvalue())
        self.assertIn("准备第 2/3 次尝试", stream.getvalue())
        self.assertNotIn("\r", stream.getvalue())
        (self.root / "model.bin").write_bytes(b"abc")
        model["files"][0][3] = hashlib.sha256(b"abc").hexdigest()
        with patch.object(worker, "source_url", return_value="https://example.invalid/model"), patch.object(worker.urllib.request, "urlopen", return_value=contextlib.nullcontext()), patch.object(worker, "download_file") as downloads, contextlib.redirect_stderr(stream):
            self.assertTrue(worker.download(model, self.root, "huggingface")["verified"])
            downloads.assert_not_called()
        self.assertIn("已缓存，跳过下载", stream.getvalue())


class HttpProgressTests(unittest.TestCase):
    def test_real_redirect_download_preserves_the_json_protocol(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        payload = b"progress fixture" * 10000
        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/file")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        target = ROOT / ".test-data" / ("progress-http-" + uuid.uuid4().hex)
        target.mkdir(parents=True)
        model = {"revision": "fixture", "files": [["模型 文件.bin", len(payload), "sha256", hashlib.sha256(payload).hexdigest()]]}
        request = {"action": "download", "path": str(target), "model": model, "source": "huggingface"}
        driver = "import importlib.util,sys; s=importlib.util.spec_from_file_location('worker',sys.argv[1]); w=importlib.util.module_from_spec(s); s.loader.exec_module(w); w.source_url=lambda *a: sys.argv[2]; sys.exit(w.main())"
        url = f"http://127.0.0.1:{server.server_port}/redirect"
        result = subprocess.run([sys.executable, "-c", driver, str(ROOT / "worker/worker.py"), url], input=json.dumps(request), capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["verified"])
        self.assertEqual((target / "模型 文件.bin").read_bytes(), payload)
        self.assertIn("下载完成，准备校验", result.stderr)
        self.assertNotIn("\x1b", result.stderr)
        self.assertNotIn("\r", result.stderr)


@unittest.skipUnless(os.name == "posix", "POSIX terminal emulator check")
class ProgressTerminalTests(unittest.TestCase):
    def test_unicode_progress_preserves_previous_rows_and_closes_on_ctrl_c(self):
        import codecs
        import fcntl
        import pty
        import select
        import signal
        import struct
        import termios
        import pyte
        driver = """
import importlib.util, io, sys, time
s=importlib.util.spec_from_file_location('worker',sys.argv[1])
w=importlib.util.module_from_spec(s); s.loader.exec_module(w)
sys.stdin=io.StringIO('{}')
print('准备计划 / 已确认的模型与目录',file=sys.stderr,flush=True)
def execute(request):
    with w.DownloadProgress('中文 模型文件-' * 5,100) as p:
        for done in range(101):
            p.update(done)
            time.sleep(0.01)
    return {'ready':True}
w.execute=execute
sys.exit(w.main())
"""
        for columns, cancel in ((40, False), (80, True)):
            with self.subTest(columns=columns, cancel=cancel):
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 20, columns, 0, 0))
                env = dict(os.environ, TERM="xterm-256color", AUDIO2TEXT_PLAIN="0")
                process = subprocess.Popen([sys.executable, "-c", driver, str(ROOT / "worker/worker.py")], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=slave, env=env)
                os.close(slave)
                screen = pyte.Screen(columns, 20)
                stream = pyte.Stream(screen)
                decoder = codecs.getincrementaldecoder("utf-8")()
                raw, sent = bytearray(), False
                deadline = time.monotonic() + 15
                try:
                    while time.monotonic() < deadline:
                        if select.select([master], [], [], 0.2)[0]:
                            try:
                                data = os.read(master, 65536)
                            except OSError:
                                break
                            if not data:
                                break
                            raw.extend(data)
                            stream.feed(decoder.decode(data))
                            if cancel and not sent and b"%" in data:
                                process.send_signal(signal.SIGINT)
                                sent = True
                        elif process.poll() is not None:
                            break
                    stdout, _ = process.communicate(timeout=5)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    os.close(master)
                    process.stdout.close()
                value = json.loads(stdout)
                self.assertEqual(process.returncode, 130 if cancel else 0)
                self.assertEqual(value.get("error", {}).get("code"), "CANCELLED" if cancel else None)
                display = "\n".join(screen.display)
                self.assertIn("准备计划 / 已确认的模型与目录", display)
                self.assertIn("下载未完成" if cancel else "100%", display)
                self.assertNotIn(b"\x1b[A", raw)
                self.assertFalse(screen.cursor.hidden)


if __name__ == "__main__":
    unittest.main()
