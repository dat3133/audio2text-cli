use crate::{
    catalog::Model,
    progress,
    state::{self, AppError, Result},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::Duration,
};

const WORKER: &str = include_str!("../worker/worker.py");
const QWEN_CONSTRAINTS: &str = include_str!("../worker/constraints-qwen.txt");
const WHISPER_CONSTRAINTS: &str = include_str!("../worker/constraints-whisper.txt");
const UV_VERSION: &str = "0.11.32";
pub const RUNTIME_VERSION: &str = "py31212-backends-v1";

pub fn runtime_dir(root: &Path, backend: &str, device: &str) -> PathBuf {
    root.join("runtimes").join(format!(
        "{backend}-{}-{RUNTIME_VERSION}",
        if device.starts_with("cuda") {
            "cu126"
        } else {
            "cpu"
        }
    ))
}
pub fn python(root: &Path, backend: &str, device: &str) -> Result<PathBuf> {
    let p = runtime_dir(root, backend, device).join("ready.json");
    let v: Value = fs::read(&p)
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .ok_or_else(|| {
            AppError::new(
                "ENV_NOT_READY",
                "推理运行时尚未准备",
                3,
                "运行 audio2text init，或 init --non-interactive。",
            )
        })?;
    let py = PathBuf::from(v["python"].as_str().unwrap_or(""));
    if !py.is_file() || v["runtime_version"] != RUNTIME_VERSION {
        return Err(AppError::new(
            "ENV_NOT_READY",
            "运行时路径不存在或版本不符",
            3,
            "重新执行 init。",
        ));
    }
    Ok(py)
}
pub fn terminate(child: &mut std::process::Child) {
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    #[cfg(unix)]
    {
        let _ = Command::new("kill")
            .args(["-TERM", "--", &format!("-{}", child.id())])
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}
fn group(cmd: &mut Command) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    let _ = cmd;
}
pub fn run_command(mut cmd: Command) -> Result<()> {
    state::check_cancel()?;
    group(&mut cmd);
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| {
            AppError::new(
                "SETUP_FAILED",
                e.to_string(),
                6,
                "检查可执行文件和运行目录。",
            )
        })?;
    let stdout = child.stdout.take().unwrap();
    let reader = thread::spawn(move || {
        let _ = std::io::copy(&mut std::io::BufReader::new(stdout), &mut std::io::stderr());
    });
    loop {
        if state::check_cancel().is_err() {
            terminate(&mut child);
            let _ = reader.join();
            progress::interrupted();
            return state::check_cancel();
        }
        if let Some(status) = child.try_wait().map_err(AppError::io)? {
            let _ = reader.join();
            return if status.success() {
                Ok(())
            } else {
                Err(AppError::new(
                    "SETUP_FAILED",
                    format!("准备命令失败，退出状态 {status}"),
                    6,
                    "查看 stderr，修复网络或空间问题后重新初始化。",
                ))
            };
        }
        thread::sleep(Duration::from_millis(50));
    }
}
pub fn worker(_root: &Path, py: &Path, request: Value, offline: bool) -> Result<Value> {
    let mut cmd = Command::new(py);
    cmd.arg("-u")
        .arg("-c")
        .arg("import sys; n=int(sys.stdin.buffer.readline()); code=sys.stdin.buffer.read(n).decode('utf-8'); exec(compile(code, '<audio2text-worker>', 'exec'), globals())")
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("TOKENIZERS_PARALLELISM", "false")
        .env("HF_HUB_DISABLE_TELEMETRY", "1")
        .env("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        .env("HF_HUB_OFFLINE", if offline { "1" } else { "0" })
        .env("TRANSFORMERS_OFFLINE", if offline { "1" } else { "0" });
    group(&mut cmd);
    state::check_cancel()?;
    let mut child = cmd
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| AppError::new("ENV_NOT_READY", e.to_string(), 3, "重新执行 init。"))?;
    let out = child.stdout.take().unwrap();
    let reader = thread::spawn(move || {
        let mut b = Vec::new();
        let result = std::io::BufReader::new(out).read_to_end(&mut b);
        (result, b)
    });
    {
        let mut input = child.stdin.take().unwrap();
        writeln!(input, "{}", WORKER.len()).map_err(AppError::io)?;
        input.write_all(WORKER.as_bytes()).map_err(AppError::io)?;
        input
            .write_all(&serde_json::to_vec(&request).map_err(AppError::io)?)
            .map_err(AppError::io)?;
    }
    let status = loop {
        if state::check_cancel().is_err() {
            terminate(&mut child);
            let _ = reader.join();
            progress::interrupted();
            return Err(AppError::new(
                "CANCELLED",
                "任务已取消",
                130,
                "可重新执行任务。",
            ));
        }
        if let Some(s) = child.try_wait().map_err(AppError::io)? {
            break s;
        }
        thread::sleep(Duration::from_millis(50));
    };
    let (read, output) = reader
        .join()
        .map_err(|_| AppError::new("INTERNAL_ERROR", "读取 worker 结果失败", 1, "重新运行。"))?;
    read.map_err(AppError::io)?;
    let value: Value = serde_json::from_slice(&output).map_err(|_| {
        AppError::new(
            "WORKER_FAILED",
            format!("推理进程没有返回有效 JSON（{status}）"),
            5,
            "查看 stderr。",
        )
    })?;
    if let Some(e) = value.get("error").filter(|e| !e.is_null()) {
        let mut err: AppError = serde_json::from_value(e.clone()).map_err(AppError::io)?;
        err.exit = value["exit"].as_i64().unwrap_or(5) as i32;
        return Err(err);
    }
    if !status.success() {
        return Err(AppError::new(
            "WORKER_FAILED",
            status.to_string(),
            5,
            "查看 stderr。",
        ));
    }
    Ok(value)
}
fn uv(root: &Path) -> Result<PathBuf> {
    let tools = root.join("tools");
    let binary = tools.join(if cfg!(windows) { "uv.exe" } else { "uv" });
    if binary.is_file() {
        return Ok(binary);
    }
    fs::create_dir_all(&tools).map_err(AppError::io)?;
    let (asset, hash) = if cfg!(windows) {
        (
            "uv-x86_64-pc-windows-msvc.zip",
            "acfde570451cfdb8689fa159a138ee805ba4e241c466432750302c86254b0984",
        )
    } else {
        (
            "uv-x86_64-unknown-linux-gnu.tar.gz",
            "aab924fd522efd06f1c5f3b93a243864fc453132c94b2dc49f1371b528a4b967",
        )
    };
    let url = format!("https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{asset}");
    eprintln!("准备隔离环境管理器 uv {UV_VERSION} …");
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| AppError::new("DOWNLOAD_FAILED", e.to_string(), 6, "检查网络。"))?;
    let mut bytes = Vec::new();
    let mut last_error = String::new();
    for attempt in 1..=3 {
        state::check_cancel()?;
        match client.get(&url).send().and_then(|r| r.error_for_status()) {
            Ok(mut response) => {
                bytes.clear();
                let mut progress = progress::Download::new(asset, response.content_length());
                progress.update(0);
                let mut block = [0u8; 65536];
                loop {
                    state::check_cancel()?;
                    let n = response.read(&mut block).map_err(|e| {
                        AppError::new("DOWNLOAD_FAILED", e.to_string(), 6, "重新运行 init。")
                    })?;
                    if n == 0 {
                        break;
                    }
                    bytes.extend_from_slice(&block[..n]);
                    progress.update(bytes.len() as u64);
                    if bytes.len() > 256 * 1024 * 1024 {
                        return Err(AppError::new(
                            "DOWNLOAD_FAILED",
                            "uv 发布包超过大小限制",
                            6,
                            "检查发布包。",
                        ));
                    }
                }
                progress.finish();
                if format!("{:x}", Sha256::digest(&bytes)) == hash {
                    break;
                }
                last_error = "uv 发布包 SHA256 不匹配".into();
                bytes.clear();
            }
            Err(e) => {
                last_error = e.to_string();
            }
        }
        eprintln!("下载尝试 {attempt}/3 失败：{last_error}");
    }
    if bytes.is_empty() {
        return Err(AppError::new(
            "DOWNLOAD_FAILED",
            last_error,
            6,
            "检查网络或稍后重试。",
        ));
    }
    if cfg!(windows) {
        let mut archive =
            zip::ZipArchive::new(std::io::Cursor::new(bytes)).map_err(AppError::io)?;
        let mut entry = archive.by_name("uv.exe").map_err(AppError::io)?;
        let mut data = vec![];
        entry.read_to_end(&mut data).map_err(AppError::io)?;
        state::atomic_write(&binary, &data, true)?;
    } else {
        let mut archive =
            tar::Archive::new(flate2::read::GzDecoder::new(std::io::Cursor::new(bytes)));
        for file in archive.entries().map_err(AppError::io)? {
            let mut file = file.map_err(AppError::io)?;
            if file
                .path()
                .map_err(AppError::io)?
                .file_name()
                .is_some_and(|p| p == "uv")
            {
                let mut data = vec![];
                file.read_to_end(&mut data).map_err(AppError::io)?;
                state::atomic_write(&binary, &data, true)?;
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    fs::set_permissions(&binary, fs::Permissions::from_mode(0o755))
                        .map_err(AppError::io)?;
                }
                break;
            }
        }
    }
    if !binary.is_file() {
        return Err(AppError::new(
            "SETUP_FAILED",
            "uv 发布包缺少可执行文件",
            6,
            "检查发布资源。",
        ));
    }
    Ok(binary)
}
fn uv_command(binary: &Path, root: &Path) -> Command {
    uv_command_with_progress(binary, root, progress::enabled())
}
fn uv_command_with_progress(binary: &Path, root: &Path, dynamic: bool) -> Command {
    let mut cmd = Command::new(binary);
    cmd.env("UV_PYTHON_INSTALL_DIR", root.join("python"))
        .env("UV_CACHE_DIR", root.join("cache/uv"))
        .env_remove("VIRTUAL_ENV")
        .env_remove("PYTHONHOME")
        .env_remove("PYTHONPATH");
    if !dynamic {
        cmd.env("UV_NO_PROGRESS", "1")
            .env("NO_COLOR", "1")
            .args(["--color", "never"]);
    }
    cmd
}

pub fn prepare(
    root: &Path,
    m: &Model,
    device: &str,
    reuse_python: Option<&Path>,
) -> Result<PathBuf> {
    if reuse_python.is_none()
        && let Ok(p) = python(root, &m.backend, device)
    {
        return Ok(p);
    }
    let dir = runtime_dir(root, &m.backend, device);
    fs::create_dir_all(&dir).map_err(AppError::io)?;
    let py = if let Some(p) = reuse_python {
        state::absolute(p)?
    } else {
        let uv = uv(root)?;
        let env = dir.join("env");
        let mut cmd = uv_command(&uv, root);
        cmd.args(["venv", "--python", "3.12.12", "--managed-python"])
            .arg(&env);
        if !env.join("pyvenv.cfg").is_file() {
            run_command(cmd)?;
        }
        let py = env.join(if cfg!(windows) {
            "Scripts/python.exe"
        } else {
            "bin/python"
        });
        if m.backend == "qwen3-asr" || device.starts_with("cuda") {
            let mut cmd = uv_command(&uv, root);
            cmd.args(["pip", "install", "--python"]).arg(&py).args([
                "torch==2.6.0",
                "--index-url",
                if device.starts_with("cuda") {
                    "https://download.pytorch.org/whl/cu126"
                } else {
                    "https://download.pytorch.org/whl/cpu"
                },
            ]);
            run_command(cmd)?;
        }
        let mut cmd = uv_command(&uv, root);
        cmd.args(["pip", "install", "--python"]).arg(&py).args([
            "imageio-ffmpeg==0.6.0",
            "huggingface-hub==0.36.2",
            "numpy==2.2.6",
            "setuptools==80.9.0",
        ]);
        if m.backend == "qwen3-asr" {
            cmd.args(["qwen-asr==0.0.6", "transformers==4.57.6", "torch==2.6.0"]);
        } else {
            cmd.args(["faster-whisper==1.2.1", "ctranslate2==4.6.0"]);
        }
        let constraints = dir.join("constraints.txt");
        state::atomic_write(
            &constraints,
            if m.backend == "qwen3-asr" {
                QWEN_CONSTRAINTS
            } else {
                WHISPER_CONSTRAINTS
            }
            .as_bytes(),
            true,
        )?;
        cmd.arg("--constraint").arg(&constraints);
        run_command(cmd)?;
        py
    };
    let report = worker(
        root,
        &py,
        json!({"action":"check", "backend":m.backend, "device":device}),
        true,
    )?;
    // ready.json means the runtime imports; model readiness is committed separately after inference.
    state::atomic_write(&dir.join("ready.json"), &serde_json::to_vec_pretty(&json!({"python":py,"runtime_version":RUNTIME_VERSION,"backend":m.backend,"device":device,"external":reuse_python.is_some(),"dependencies":report["versions"],"python_version":report["python_version"]})).map_err(AppError::io)?, true)?;
    Ok(py)
}
pub fn install(root: &Path, py: &Path, m: &Model, path: &Path, source: &str) -> Result<()> {
    worker(
        root,
        py,
        json!({"action":"download", "model":m,"path":path,"source":source}),
        false,
    )?;
    Ok(())
}
pub fn self_test(root: &Path, py: &Path, m: &Model, path: &Path, device: &str) -> Result<()> {
    worker(
        root,
        py,
        json!({"action":"selftest", "model":m,"path":path,"device":device}),
        true,
    )?;
    Ok(())
}

#[cfg(test)]
mod progress_tests {
    use super::*;

    #[test]
    fn uv_terminal_mode_inherits_user_progress_preference() {
        let interactive = uv_command_with_progress(Path::new("uv"), Path::new("data"), true);
        assert!(
            interactive
                .get_envs()
                .all(|(name, _)| name != "UV_NO_PROGRESS")
        );
        assert!(interactive.get_args().next().is_none());
        let plain = uv_command_with_progress(Path::new("uv"), Path::new("data"), false);
        assert!(
            plain
                .get_envs()
                .any(|(name, value)| name == "UV_NO_PROGRESS"
                    && value == Some(std::ffi::OsStr::new("1")))
        );
        assert_eq!(plain.get_args().collect::<Vec<_>>(), ["--color", "never"]);
    }
}
