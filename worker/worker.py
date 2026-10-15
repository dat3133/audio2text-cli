"""Embedded inference worker. One JSON request in, one JSON response out.

All inference is local. Downloads are a separate action, with pinned file hashes.
Audio is decoded incrementally; at most one bounded inference batch is resident.
"""
from __future__ import annotations

import contextlib
import gc
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import deque

SR = 16000
LANGUAGES = dict(zip(
    "zh en yue ar de fr es pt id it ko ru th vi ja tr hi ms nl sv da fi pl cs fil fa el hu mk ro".split(),
    "Chinese English Cantonese Arabic German French Spanish Portuguese Indonesian Italian Korean Russian Thai Vietnamese Japanese Turkish Hindi Malay Dutch Swedish Danish Finnish Polish Czech Filipino Persian Greek Hungarian Macedonian Romanian".split(),
))


class WorkerError(Exception):
    def __init__(self, code, message, exit=5, hint="查看 stderr，修复问题后重试。", retryable=False):
        super().__init__(message)
        self.code, self.exit, self.hint, self.retryable = code, exit, hint, retryable

    def result(self):
        return {"error": {"code": self.code, "message": str(self), "retryable": self.retryable, "hint": self.hint}, "exit": self.exit}


def offline():
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ[key] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    def deny(*args, **kwargs):
        raise OSError("audio2text: network access is disabled during offline inference")

    socket.create_connection = deny
    socket.getaddrinfo = deny
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.socket.sendto = deny


def ffmpeg_path():
    configured = os.environ.get("FFMPEG_BINARY")
    if configured and Path(configured).is_file():
        return configured
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        path = shutil.which("ffmpeg")
        if path:
            return path
    raise WorkerError("ENV_NOT_READY", "运行时缺少 FFmpeg", 3, "重新执行 init。")


def configure_cuda_libraries():
    """Expose CUDA libraries supplied by the managed torch wheel to CTranslate2."""
    import importlib.util
    spec = importlib.util.find_spec("torch")
    if spec and spec.submodule_search_locations:
        torch_dir = Path(next(iter(spec.submodule_search_locations)))
        paths = [torch_dir / "lib"]
        nvidia = torch_dir.parent / "nvidia"
        if nvidia.is_dir():
            paths.extend(nvidia.glob("*/lib"))
        if os.name == "nt":
            global _DLL_HANDLES
            _DLL_HANDLES = [os.add_dll_directory(str(p)) for p in paths if p.is_dir()]
            os.environ["PATH"] = os.pathsep.join(map(str, paths)) + os.pathsep + os.environ.get("PATH", "")
        # Importing torch loads its packaged CUDA/cuDNN libraries on Linux as well.
        import torch  # noqa: F401


def defer_alignment_tokenizer():
    """qwen-asr 0.0.6 imports nagisa eagerly for an unused alignment feature.

    Nagisa's native dictionary loader cannot open Unicode paths on Windows.
    Keep its real import metadata, and load it only if an alignment operation
    actually requests an attribute. ASR never requests word alignment in v1.
    No installed package is patched; this affects only the worker process.
    """
    if "nagisa" in sys.modules:
        return
    spec = importlib.util.find_spec("nagisa")
    if spec is None:
        return
    module = types.ModuleType("nagisa")
    module.__spec__ = spec
    module.__file__ = spec.origin
    module.__loader__ = spec.loader
    module.__package__ = "nagisa"
    module.__path__ = list(spec.submodule_search_locations or [])
    module.__version__ = importlib.metadata.version("nagisa")

    def load_attribute(name):
        sys.modules.pop("nagisa", None)
        try:
            real = importlib.import_module("nagisa")
        except Exception:
            sys.modules["nagisa"] = module
            raise
        return getattr(real, name)

    module.__getattr__ = load_attribute
    sys.modules["nagisa"] = module


def check(backend, device):
    ffmpeg = ffmpeg_path()
    proc = subprocess.run([ffmpeg, "-version"], capture_output=True, timeout=15)
    if proc.returncode:
        raise WorkerError("ENV_NOT_READY", "FFmpeg 无法启动", 3)
    if device.startswith("cuda"):
        configure_cuda_libraries()
    if backend == "qwen3-asr":
        defer_alignment_tokenizer()
        import qwen_asr  # noqa: F401
        import torch
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise WorkerError("DEVICE_UNAVAILABLE", "PyTorch 无法使用 CUDA", 3, "检查驱动，或使用 --device cpu。")
    elif backend == "faster-whisper":
        import faster_whisper  # noqa: F401
        import ctranslate2
        if device.startswith("cuda") and not ctranslate2.get_cuda_device_count():
            raise WorkerError("DEVICE_UNAVAILABLE", "CTranslate2 无法使用 CUDA", 3)
    else:
        raise WorkerError("MODEL_UNKNOWN", "未知后端", 2)
    versions = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions() if d.metadata["Name"]}
    return {"ffmpeg": ffmpeg, "versions": versions, "python_version": sys.version.split()[0], "device": device}


def digest(path, method):
    h = hashlib.sha256() if method == "sha256" else hashlib.sha1()
    if method == "git-sha1":
        h.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as file:
        for block in iter(lambda: file.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verified(path, entry):
    name, size, method, expected = entry
    file = path / name
    return file.is_file() and file.stat().st_size == size and digest(file, method) == expected


def verify(model, path):
    failures = []
    for entry in model["files"]:
        print(f"校验 {entry[0]} …", file=sys.stderr)
        if not verified(path, entry):
            failures.append(entry[0])
    if failures:
        raise WorkerError("MODEL_INVALID", "模型缺失或校验不符：" + ", ".join(failures), 3, "使用目录内的固定模型版本，或重新安装模型。")
    return {"verified": True, "revision": model["revision"], "path": str(path.resolve())}


def source_url(model, source, name):
    if source == "modelscope":
        if not model.get("modelscope_repo"):
            raise WorkerError("SOURCE_UNAVAILABLE", "所选模型没有已验证的 ModelScope 来源", 2)
        # The mirror may use a branch, but every downloaded byte is checked against
        # the immutable HF snapshot manifest. Mirror drift fails closed.
        return f"https://modelscope.cn/models/{model['modelscope_repo']}/resolve/master/{name}"
    return f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{name}"


def progress_enabled(stream):
    return bool(getattr(stream, "isatty", lambda: False)()) and os.environ.get("TERM") != "dumb" and os.environ.get("AUDIO2TEXT_PLAIN") != "1"


def text_width(text):
    return sum(0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def truncate_cells(text, width):
    if text_width(text) <= width:
        return text
    if width <= 0:
        return ""
    result, used = [], 0
    for ch in text:
        size = text_width(ch)
        if used + size > width - 1:
            break
        result.append(ch)
        used += size
    return "".join(result) + "…"


def progress_line(label, done, total, tick, columns):
    width = max(0, columns - 1)
    value = total or done
    divisor, unit = next((divisor, unit) for divisor, unit in ((1024**3, "GiB"), (1024**2, "MiB"), (1024, "KiB"), (1, "B")) if value >= divisor or divisor == 1)
    if total:
        percentage = min(100, done * 100 // total)
        bar_size = 20 if width >= 76 else 10
        filled = percentage * bar_size // 100
        suffix = f" [{'#' * filled}{'-' * (bar_size - filled)}] {percentage:3}% {done / divisor:.1f}/{total / divisor:.1f} {unit}"
    else:
        suffix = f" [{'|/-'[tick % 3]}] {done / divisor:.1f} {unit}"
    label = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in label)
    budget = max(0, width - text_width(suffix))
    return truncate_cells(label, budget) + suffix if budget else truncate_cells(suffix.lstrip(), width)


class DownloadProgress:
    """A bounded, throttled stderr row. Context exit always ends the row."""
    def __init__(self, label, total, stream=None, clock=None):
        self.label, self.total = label, total
        self.stream = sys.stderr if stream is None else stream
        self.clock = time.monotonic if clock is None else clock
        self.dynamic = progress_enabled(self.stream)
        self.done, self.tick, self.last = 0, 0, None
        self.closed = False

    def __enter__(self):
        return self

    def update(self, done):
        self.done = done
        now = self.clock()
        if self.dynamic and (self.last is None or now - self.last >= 0.2):
            self.draw()
            self.last = now

    def draw(self):
        try:
            columns = os.get_terminal_size(self.stream.fileno()).columns
        except (OSError, ValueError, AttributeError):
            columns = 80
        self.stream.write("\r" + progress_line(self.label, self.done, self.total, self.tick, columns) + "\x1b[K")
        self.stream.flush()
        self.tick += 1

    def __exit__(self, exception_type, exception, traceback):
        if self.closed:
            return
        if self.dynamic:
            self.draw()
            self.stream.write("\n")
        status = "下载完成，准备校验" if exception_type is None else "下载未完成，保留续传数据"
        print(f"{self.label}：{status}", file=self.stream, flush=True)
        self.closed = True


def download_file(url, part, expected_size, opener=urllib.request.urlopen):
    offset = part.stat().st_size if part.exists() else 0
    if offset > expected_size:
        part.rename(part.with_name(part.name + ".invalid-" + uuid.uuid4().hex))
        offset = 0
    headers = {"User-Agent": "audio2text/0.1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    with DownloadProgress(part.name.removesuffix(".part"), expected_size) as progress:
        with opener(urllib.request.Request(url, headers=headers), timeout=60) as response:
            status = response.status
            if offset and status == 206:
                value = response.headers.get("Content-Range", "")
                if not value.startswith(f"bytes {offset}-"):
                    raise WorkerError("DOWNLOAD_FAILED", "服务器返回了错误的续传范围", 6)
            elif status == 200:
                offset = 0
            else:
                raise WorkerError("DOWNLOAD_FAILED", f"服务器返回 HTTP {status}", 6)
            progress.update(offset)
            with part.open("ab" if offset else "wb") as output:
                total = offset
                while True:
                    data = response.read(1024 * 1024)
                    if not data:
                        break
                    total += len(data)
                    if total > expected_size:
                        raise WorkerError("DOWNLOAD_FAILED", "下载内容超出固定模型文件大小", 6)
                    output.write(data)
                    progress.update(total)
                output.flush()
                os.fsync(output.fileno())
        if part.stat().st_size != expected_size:
            raise WorkerError("DOWNLOAD_FAILED", "下载不完整，已保留续传文件", 6, retryable=True)


def download(model, path, source):
    path.mkdir(parents=True, exist_ok=True)
    sources = [source] if source != "auto" else ["huggingface"] + (["modelscope"] if model.get("modelscope_repo") else [])
    selected = None
    last = "没有可用下载源"
    for candidate in sources:
        try:
            req = urllib.request.Request(source_url(model, candidate, "config.json"), method="HEAD")
            with urllib.request.urlopen(req, timeout=10):
                selected = candidate
            break
        except (OSError, WorkerError) as e:
            last = str(e)
    if not selected:
        raise WorkerError("DOWNLOAD_FAILED", last, 6, "通过 --source 指定可用来源。", True)
    for entry in model["files"]:
        name, size, method, expected = entry
        target = path / name
        if verified(path, entry):
            print(f"已缓存，跳过下载：{name}", file=sys.stderr)
            continue
        part = target.with_name(target.name + ".part")
        if part.is_file() and part.stat().st_size == size:
            if digest(part, method) == expected:
                os.replace(part, target)
                print(f"已缓存，跳过下载：{name}", file=sys.stderr)
                continue
            part.rename(part.with_name(part.name + ".invalid-" + uuid.uuid4().hex))
        if target.exists():
            target.rename(target.with_name(target.name + ".invalid-" + uuid.uuid4().hex))
        for attempt in range(3):
            try:
                print(f"下载 {name}，{size / 1024**2:.1f} MiB（{attempt + 1}/3，{selected}）", file=sys.stderr)
                download_file(source_url(model, selected, name), part, size)
                print(f"校验 {name} …", file=sys.stderr)
                if digest(part, method) != expected:
                    part.rename(part.with_name(part.name + ".invalid-" + uuid.uuid4().hex))
                    raise WorkerError("CHECKSUM_MISMATCH", f"{name} 校验不符", 6)
                os.replace(part, target)
                break
            except (OSError, WorkerError) as e:
                if attempt == 2:
                    raise WorkerError("DOWNLOAD_FAILED", str(e), 6, "重新运行；完整文件和可续传部分会复用。", True) from e
                print(f"下载失败：{e}；准备第 {attempt + 2}/3 次尝试", file=sys.stderr)
                time.sleep(attempt + 1)
    return {"source": selected, **verify(model, path)}


def split_point(wav, target, sr=SR):
    import numpy as np
    if len(wav) <= target:
        return len(wav)
    start = max(sr, target - 3 * sr)
    stop = min(len(wav), target + 3 * sr)
    frame = max(1, sr // 50)
    values = wav[start:stop]
    count = len(values) // frame
    if not count:
        return target
    energy = np.mean(values[:count * frame].reshape(count, frame) ** 2, axis=1)
    return start + int(np.argmin(energy)) * frame + frame // 2


def decode_segments(path, seconds, ffmpeg=None):
    import numpy as np
    command = [ffmpeg or ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    messages = deque(maxlen=64)

    def read_errors():
        for block in iter(lambda: process.stderr.read(1024), b""):
            messages.append(block)

    reader = threading.Thread(target=read_errors, daemon=True)
    reader.start()
    target = seconds * SR
    window = target + 3 * SR
    buffer = np.empty(0, dtype=np.float32)
    offset, total = 0, 0
    try:
        while True:
            data = process.stdout.read(max(4, (window - len(buffer)) * 4))
            if data:
                if len(data) % 4:
                    raise WorkerError("INPUT_INVALID", "音频解码输出不完整", 4)
                samples = np.frombuffer(data, dtype="<f4")
                total += len(samples)
                buffer = np.concatenate((buffer, samples))
            if len(buffer) >= window or (not data and len(buffer)):
                cut = split_point(buffer, target) if len(buffer) >= window else len(buffer)
                yield buffer[:cut].copy(), offset / SR
                buffer = buffer[cut:].copy()
                offset += cut
            if not data and not len(buffer):
                break
        status = process.wait()
        reader.join()
        if status or total == 0:
            details = b"".join(messages).decode("utf-8", errors="replace").strip()
            raise WorkerError("INPUT_INVALID", details or "没有解码出音频数据", 4, "检查文件是否有音轨以及是否损坏。")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
        reader.join(timeout=5)
        process.stderr.close()


class Backend:
    def __init__(self, model, path, device, batch_size):
        self.backend = model["backend"]
        self.device, self.batch_size = device, batch_size
        if self.backend == "qwen3-asr":
            defer_alignment_tokenizer()
            import torch
            from qwen_asr import Qwen3ASRModel
            if device.startswith("cuda:"):
                torch.cuda.set_device(int(device.split(":")[-1]))
            dtype = torch.float32 if device == "cpu" else (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
            self.model = Qwen3ASRModel.from_pretrained(str(path), dtype=dtype, device_map=device, max_inference_batch_size=batch_size, max_new_tokens=512, local_files_only=True)
        else:
            if device.startswith("cuda"):
                configure_cuda_libraries()
            from faster_whisper import WhisperModel
            self.model = WhisperModel(str(path), device="cuda" if device.startswith("cuda") else "cpu", device_index=int(device.split(":")[-1]) if ":" in device else 0, compute_type="float16" if device.startswith("cuda") else "int8", local_files_only=True)

    def infer(self, wavs, language, context):
        if self.backend == "qwen3-asr":
            budget = max(256, math.ceil(max(len(w) for w in wavs) / SR * 8))
            self.model.max_new_tokens = budget
            result = self.model.transcribe(audio=[(w, SR) for w in wavs], context=context, language=LANGUAGES.get(language) if language != "auto" else None)
            output = []
            for value in result:
                languages = [next((k for k, v in LANGUAGES.items() if v == name.strip()), name.strip()) for name in value.language.split(",") if name.strip()]
                near_limit = len(self.model.processor.tokenizer.encode(value.text)) >= budget * 0.95
                output.append((value.text.strip(), languages, near_limit))
            return output
        output = []
        for wav in wavs:
            segments, info = self.model.transcribe(wav, language=None if language == "auto" else language, initial_prompt=context or None, condition_on_previous_text=False, beam_size=5)
            output.append((" ".join(s.text.strip() for s in segments).strip(), [info.language], False))
        return output


def suspicious(text):
    if len(text) < 80:
        return False
    for size in range(3, min(80, len(text) // 10) + 1):
        if text.endswith(text[-size:] * 10):
            return True
    return False


def is_oom(error):
    message = str(error).lower()
    return "out of memory" in message or "cuda_error_out_of_memory" in message or "cublas_status_alloc_failed" in message


def clear_gpu():
    gc.collect()
    if "torch" in sys.modules:
        torch = sys.modules["torch"]
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def recognize(backend, chunks, language, context, warnings, depth=0):
    try:
        results = backend.infer([w for w, _ in chunks], language, context)
    except Exception as e:
        if not is_oom(e):
            raise
        # Release failed inference frames before retrying smaller batches.
        e.__traceback__ = None
        clear_gpu()
        if len(chunks) == 1:
            raise WorkerError("OUT_OF_MEMORY", str(e), 5, "降低分段长度、释放显存，或显式选择更小模型。") from e
        size = max(1, len(chunks) // 2)
        backend.batch_size = min(backend.batch_size, size)
        warnings.append("显存不足，已降低批大小重试。")
        output = []
        for index in range(0, len(chunks), size):
            output.extend(recognize(backend, chunks[index:index + size], language, context, warnings, depth))
        return output
    if len(results) != len(chunks):
        raise WorkerError("INFERENCE_FAILED", "后端返回数量与输入片段数量不符", 5)
    output = []
    for (wav, offset), result in zip(chunks, results):
        text, languages, near_limit = result
        if near_limit or suspicious(text):
            if len(wav) >= 20 * SR and depth < 3:
                cut = split_point(wav, len(wav) // 2)
                warnings.append(f"{offset:.1f}s 附近疑似异常生成，缩短片段重新识别。")
                output.extend(recognize(backend, [(wav[:cut], offset), (wav[cut:], offset + cut / SR)], language, context, warnings, depth + 1))
                continue
            warnings.append(f"{offset:.1f}s 附近结果可能重复或截断，请复核。")
        output.append(result)
    return output


def atomic_text(path, text, overwrite):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    with temp.open("x", encoding="utf-8", newline="\n") as file:
        file.write(text.rstrip() + "\n")
        file.flush()
        os.fsync(file.fileno())
    if overwrite:
        os.replace(temp, path)
    else:
        try:
            if os.name == "nt":
                # Windows rename is atomic and refuses an existing destination.
                os.rename(temp, path)
            else:
                os.link(temp, path)
        except FileExistsError as e:
            raise WorkerError("OUTPUT_EXISTS", f"文件已存在：{path}", 7, "使用 --overwrite 或其他路径。") from e
        if temp.exists():
            recovery = path.parent / ".audio2text-recovery"
            recovery.mkdir(exist_ok=True)
            temp.rename(recovery / temp.name)


def peak_memory_bytes():
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ("peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            api = ctypes.WinDLL("psapi").GetProcessMemoryInfo
            api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return counters.peak_rss
            return None
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    except (ImportError, OSError):
        return None


def transcribe(request, backend):
    results = []
    durations = []
    for item in request["files"]:
        result = {"input": item["input"], "status": "error", "model": request["model"]["id"], "backend": request["model"]["backend"], "device": request["device"], "languages": [], "audio_duration_seconds": 0.0, "text": None, "output": None, "warnings": [], "error": None}
        started = time.monotonic()
        try:
            if item.get("error"):
                e = item["error"]
                raise WorkerError(e["code"], e["message"], item.get("exit", 4), e["hint"])
            texts, languages = [], []
            chunks = []
            seconds = 0

            def flush():
                for text, detected, _ in recognize(backend, chunks, request["language"], request.get("context", ""), result["warnings"]):
                    if text:
                        texts.append(text)
                    for lang in detected:
                        if lang not in languages:
                            languages.append(lang)

            with contextlib.closing(decode_segments(Path(item["input"]), request["segment_seconds"])) as decoder:
                for wav, offset in decoder:
                    seconds = offset + len(wav) / SR
                    chunks.append((wav, offset))
                    if len(chunks) >= backend.batch_size:
                        flush()
                        chunks.clear()
                        print(f"{item['input']}：已处理 {seconds:.1f}s", file=sys.stderr)
                if chunks:
                    flush()
            text = "\n".join(texts)
            if item.get("output"):
                try:
                    atomic_text(Path(item["output"]), text, request.get("overwrite", False))
                except OSError as e:
                    raise WorkerError("WRITE_FAILED", str(e), 7, "检查目录权限和磁盘空间。") from e
                result["output"] = str(Path(item["output"]).resolve())
            result.update(status="ok", text=None if request.get("no_text") else text, languages=languages, audio_duration_seconds=round(seconds, 3))
        except WorkerError as e:
            result["error"] = e.result()["error"]
            result["exit"] = e.exit
        except Exception as e:
            result["error"] = WorkerError("OUT_OF_MEMORY" if is_oom(e) else "INFERENCE_FAILED", str(e), 5).result()["error"]
            result["exit"] = 5
            clear_gpu()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        results.append(result)
        durations.append(result["elapsed_seconds"])
    return {"results": results, "processing_seconds": round(sum(durations), 3), "peak_worker_rss_bytes": peak_memory_bytes()}


def execute(request):
    action = request["action"]
    if action != "download":
        offline()
    if action == "check":
        return check(request["backend"], request["device"])
    if action == "verify":
        return verify(request["model"], Path(request["path"]))
    if action == "download":
        return download(request["model"], Path(request["path"]), request["source"])
    if action in ("selftest", "transcribe"):
        import numpy as np
        model, path, device = request["model"], Path(request["path"]), request["device"]
        # Verify sizes on each inference; full hashes are verified at installation/import.
        for name, size, _, _ in model["files"]:
            file = path / name
            if not file.is_file() or file.stat().st_size != size:
                raise WorkerError("MODEL_INVALID", f"模型文件缺失或大小改变：{name}", 3, "重新安装或导入模型以校验。")
        batch_size = request.get("batch_size", 0) or (4 if device.startswith("cuda") and model["backend"] == "qwen3-asr" else 1)
        started = time.monotonic()
        backend = Backend(model, path, device, batch_size)
        loaded = time.monotonic() - started
        if action == "selftest":
            result = backend.infer([np.zeros(SR * 2, dtype=np.float32)], "auto", "")
            if len(result) != 1:
                raise WorkerError("SELFTEST_FAILED", "模型自检没有返回有效结果", 3)
            return {"ready": True, "model_load_seconds": round(loaded, 3), **check(model["backend"], device)}
        result = transcribe(request, backend)
        result["model_load_seconds"] = round(loaded, 3)
        return result
    raise WorkerError("INVALID_ARGUMENT", "未知 worker 操作", 2)


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # Protect the protocol even from C extensions printing directly to fd 1.
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    try:
        request = json.load(sys.stdin)
        result = execute(request)
        status = 0
    except KeyboardInterrupt:
        result = WorkerError("CANCELLED", "任务已取消", 130).result()
        status = 130
    except WorkerError as e:
        result, status = e.result(), e.exit
    except ImportError as e:
        result = WorkerError("ENV_NOT_READY", str(e), 3, "重新执行 init，或检查显式复用的环境依赖。").result()
        status = 3
    except Exception as e:
        result = WorkerError("INFERENCE_FAILED", str(e), 5).result()
        status = 5
    json.dump(result, protocol, ensure_ascii=False)
    protocol.write("\n")
    protocol.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
