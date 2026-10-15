"""Exercise actual menu cursor movements in a PTY, with a terminal emulator."""
import codecs
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
import uuid

try:
    import pyte
except ImportError:
    pyte = None

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get("AUDIO2TEXT_BIN", ROOT / "target/debug/audio2text"))


@unittest.skipUnless(os.name == "posix" and pyte is not None and BIN.is_file(), "POSIX PTY, pyte and built CLI required")
class WizardTerminalTests(unittest.TestCase):
    def start_wizard(self, columns, rows=50, plain=False, options=()):
        import fcntl
        import pty
        import struct
        import termios
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        root = ROOT / ".test-data" / ("wizard-" + uuid.uuid4().hex)
        env = dict(os.environ, TERM="xterm-256color", NO_COLOR="1", AUDIO2TEXT_PLAIN="1" if plain else "0")
        env.pop("AUDIO2TEXT_HOME", None)
        profile = root.with_name(root.name + "-profile")
        env.update(LOCALAPPDATA=str(profile), XDG_DATA_HOME=str(profile), HOME=str(profile))
        process = subprocess.Popen([str(BIN), "--data-dir", str(root), "init", *options], stdin=slave, stderr=slave, stdout=subprocess.PIPE, env=env)
        os.close(slave)
        self.addCleanup(os.close, master)
        def stop():
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
        self.addCleanup(stop)
        screen = pyte.Screen(columns, rows)
        stream = pyte.Stream(screen)
        decoder = codecs.getincrementaldecoder("utf-8")()
        def pump(timeout=3):
            import select
            received = False
            ready, _, _ = select.select([master], [], [], timeout)
            if not ready:
                return False
            while ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    return received
                if not data:
                    return received
                stream.feed(decoder.decode(data))
                received = True
                ready, _, _ = select.select([master], [], [], 0.1)
            return True
        def wait_for(text):
            deadline = time.monotonic() + 15
            while text not in "\n".join(screen.display):
                self.assertLess(time.monotonic(), deadline, "Missing " + text + "\n" + "\n".join(screen.display))
                pump(timeout=0.2)
        def send(keys):
            os.write(master, keys)
            self.assertTrue(pump(), "No response to terminal input")
        return process, screen, root, wait_for, send

    def test_model_navigation_preserves_heading_and_completed_steps(self):
        for columns in (80, 120):
            with self.subTest(columns=columns):
                process, screen, root, wait_for, send = self.start_wizard(columns)
                wait_for("配置模式")
                send(b"\r")
                wait_for("识别语言")
                send(b"\r")
                wait_for("推荐偏好")
                send(b"\r")
                wait_for("模型选择")
                for key in (b"\x1b[B", b"\x1b[A") * 8:
                    send(key)
                    display = "\n".join(screen.display)
                    for heading in ("audio2text", "初始化向导", "配置模式", "识别语言", "推荐偏好", "模型选择"):
                        self.assertIn(heading, display)
                send(b"\x1b[F")  # End selects the final 'back' option.
                send(b"\r")
                wait_for("? 推荐偏好")
                send(b"\x03")
                stdout, _ = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130)
                self.assertEqual(json.loads(stdout)["error"]["code"], "CANCELLED")
                self.assertFalse(screen.cursor.hidden)
                self.assertFalse(root.exists())

    def test_small_terminal_uses_numbered_choices_without_preparing_resources(self):
        process, screen, root, wait_for, send = self.start_wizard(36, rows=20)
        wait_for("输入编号")
        self.assertIn("终端空间不足", "\n".join(screen.display))
        self.assertIn("1. 推荐设置", "\n".join(screen.display))
        send(b"\r")
        wait_for("识别语言")
        self.assertFalse(root.exists())

    def test_plan_directory_changes_return_and_cancel_without_writing(self):
        process, screen, root, wait_for, send = self.start_wizard(120, options=("--model", "whisper/tiny", "--device", "cpu"))
        for title in ("配置模式", "识别语言", "推荐偏好", "模型选择", "模型下载源"):
            wait_for("? " + title)
            send(b"\r")
        wait_for("? 确认以上选择")
        self.assertIn("修改存储目录（模型和运行环境）", "\n".join(screen.display))
        targets = [root.with_name(root.name + " 中文 目录"), root.with_name(root.name + " 再次修改")]
        for target in targets:
            send(b"\x1b[B")
            send(b"\r")
            wait_for("Ctrl+U")
            send(b"\x15")
            send(str(target).encode("utf-8"))
            send(b"\r")
            wait_for("? 确认以上选择")
            display = "\n".join(screen.display)
            for text in (target.name, "whisper/tiny", "设备：cpu", "模型目录", "运行环境目录", "目标磁盘可用", "自检成功后将记住"):
                self.assertIn(text, display)
            self.assertFalse(target.exists())
            self.assertFalse(root.exists())
        # A rejected file path leaves the previous directory selected; Esc returns.
        blocker = ROOT / ".test-data" / (root.name + "-file")
        blocker.write_text("keep", encoding="utf-8")
        send(b"\x1b[B")
        send(b"\r")
        wait_for("Ctrl+U")
        send(b"\x15")
        send(str(blocker).encode("utf-8"))
        send(b"\r")
        wait_for("无法使用该目录")
        send(b"\x1b")
        wait_for("? 确认以上选择")
        self.assertIn(targets[-1].name, "\n".join(screen.display))
        # Returning to model selection keeps the selection and the new directory.
        send(b"\x1b[B")
        send(b"\x1b[B")
        send(b"\r")
        wait_for("? 模型选择")
        send(b"\r")
        wait_for("? 模型下载源")
        send(b"\r")
        wait_for("? 确认以上选择")
        self.assertIn(targets[-1].name, "\n".join(screen.display))
        for _ in range(3):
            send(b"\x1b[B")
        send(b"\r")
        stdout, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 130)
        self.assertEqual(json.loads(stdout)["error"]["code"], "CANCELLED")
        self.assertFalse(screen.cursor.hidden)
        self.assertEqual(blocker.read_text(encoding="utf-8"), "keep")
        for path in [root, root.with_name(root.name + "-profile"), *targets]:
            self.assertFalse(path.exists())

    def test_advanced_and_plan_editors_allow_escape_and_immediate_ctrl_c(self):
        process, screen, root, wait_for, send = self.start_wizard(96, options=("--model", "whisper/tiny", "--device", "cpu"))
        wait_for("? 配置模式")
        send(b"\x1b[B")
        send(b"\r")
        for title in ("识别语言", "推荐偏好", "模型选择", "模型下载源", "运行设备"):
            wait_for("? " + title)
            send(b"\r")
        wait_for("Ctrl+U")
        send(b"\x1b")
        wait_for("批大小")
        send(b"\r")
        wait_for("目标分段秒数")
        send(b"\r")
        wait_for("? 确认以上选择")
        send(b"\x1b[B")
        send(b"\r")
        wait_for("Ctrl+U")
        send(b"\x03")
        stdout, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 130)
        self.assertEqual(json.loads(stdout)["error"]["code"], "CANCELLED")
        self.assertFalse(screen.cursor.hidden)
        self.assertFalse(root.exists())
        self.assertFalse(root.with_name(root.name + "-profile").exists())


if __name__ == "__main__":
    unittest.main()
