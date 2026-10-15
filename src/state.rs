use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::atomic::{AtomicBool, Ordering},
};
use sysinfo::{Disks, System};

pub static CANCELLED: AtomicBool = AtomicBool::new(false);
pub type Result<T> = std::result::Result<T, AppError>;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    pub hint: String,
    #[serde(skip)]
    pub exit: i32,
}
impl AppError {
    pub fn new(code: &str, message: impl Into<String>, exit: i32, hint: &str) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            exit,
            retryable: false,
            hint: hint.into(),
        }
    }
    pub fn io(e: impl std::fmt::Display) -> Self {
        Self::new(
            "WRITE_FAILED",
            e.to_string(),
            7,
            "检查目录权限和剩余磁盘空间。",
        )
    }
}
pub fn check_cancel() -> Result<()> {
    if CANCELLED.load(Ordering::SeqCst) {
        Err(AppError::new(
            "CANCELLED",
            "任务已取消",
            130,
            "可重新运行命令；正常配置和已完成结果会保留。",
        ))
    } else {
        Ok(())
    }
}
pub fn absolute(path: &Path) -> Result<PathBuf> {
    let path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().map_err(AppError::io)?.join(path)
    };
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            std::path::Component::CurDir => (),
            std::path::Component::ParentDir => {
                normalized.pop();
            }
            other => normalized.push(other.as_os_str()),
        }
    }
    Ok(normalized)
}
pub fn data_dir(value: Option<&Path>) -> Result<PathBuf> {
    if let Some(p) = value {
        return absolute(p);
    }
    if let Some(p) = std::env::var_os("AUDIO2TEXT_HOME") {
        return absolute(Path::new(&p));
    }
    let base = platform_data_dir()?;
    Ok(crate::storage::load(&base)?.unwrap_or(base))
}
pub fn platform_data_dir() -> Result<PathBuf> {
    let p = if cfg!(windows) {
        PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or_else(|| {
            AppError::new(
                "CONFIG_INVALID",
                "LOCALAPPDATA 未设置",
                2,
                "传入 --data-dir。",
            )
        })?)
        .join("audio2text")
    } else if let Some(p) = std::env::var_os("XDG_DATA_HOME") {
        PathBuf::from(p).join("audio2text")
    } else {
        PathBuf::from(std::env::var_os("HOME").ok_or_else(|| {
            AppError::new("CONFIG_INVALID", "HOME 未设置", 2, "传入 --data-dir。")
        })?)
        .join(".local/share/audio2text")
    };
    absolute(&p)
}
pub fn atomic_write(path: &Path, bytes: &[u8], overwrite: bool) -> Result<()> {
    if !overwrite && path.exists() {
        return Err(AppError::new(
            "OUTPUT_EXISTS",
            format!("文件已存在：{}", path.display()),
            7,
            "更换路径，或使用 --overwrite。",
        ));
    }
    let parent = path
        .parent()
        .ok_or_else(|| AppError::io("路径没有父目录"))?;
    fs::create_dir_all(parent).map_err(AppError::io)?;
    let tmp = parent.join(format!(
        ".{}.{}.{}.partial",
        path.file_name().unwrap_or_default().to_string_lossy(),
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&tmp)
        .map_err(AppError::io)?;
    use std::io::Write;
    file.write_all(bytes).map_err(AppError::io)?;
    file.sync_all().map_err(AppError::io)?;
    drop(file);
    if overwrite {
        fs::rename(&tmp, path).map_err(AppError::io)?;
    } else {
        #[cfg(windows)]
        {
            use std::os::windows::ffi::OsStrExt;
            #[link(name = "kernel32")]
            unsafe extern "system" {
                fn MoveFileExW(existing: *const u16, destination: *const u16, flags: u32) -> i32;
            }
            let from: Vec<u16> = tmp.as_os_str().encode_wide().chain(Some(0)).collect();
            let to: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
            if unsafe { MoveFileExW(from.as_ptr(), to.as_ptr(), 8) } == 0 {
                let e = std::io::Error::last_os_error();
                return Err(if path.exists() {
                    AppError::new("OUTPUT_EXISTS", e.to_string(), 7, "更换输出路径。")
                } else {
                    AppError::io(e)
                });
            }
        }
        #[cfg(not(windows))]
        {
            fs::hard_link(&tmp, path).map_err(|e| {
                if path.exists() {
                    AppError::new("OUTPUT_EXISTS", e.to_string(), 7, "更换输出路径。")
                } else {
                    AppError::io(e)
                }
            })?;
            // Retain the staging link for recovery instead of deleting it.
            let recovery = parent.join(".audio2text-recovery");
            fs::create_dir_all(&recovery).map_err(AppError::io)?;
            fs::rename(&tmp, recovery.join(tmp.file_name().unwrap())).map_err(AppError::io)?;
        }
    }
    Ok(())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Installed {
    pub id: String,
    pub path: PathBuf,
    pub revision: String,
    pub verified: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Config {
    pub version: u32,
    pub default_model: Option<String>,
    pub device: String,
    pub language: String,
    pub profile: String,
    pub source: String,
    pub batch_size: usize,
    pub segment_seconds: u32,
    pub installed: Vec<Installed>,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            version: 1,
            default_model: None,
            device: "auto".into(),
            language: "auto".into(),
            profile: "balanced".into(),
            source: "auto".into(),
            batch_size: 0,
            segment_seconds: 60,
            installed: vec![],
        }
    }
}
impl Config {
    pub fn load(root: &Path) -> Result<Self> {
        let p = root.join("config.json");
        if !p.exists() {
            return Ok(Self::default());
        }
        let data = fs::read(&p).map_err(AppError::io)?;
        let c: Self = serde_json::from_slice(&data).map_err(|e| {
            AppError::new(
                "CONFIG_INVALID",
                e.to_string(),
                2,
                "修复 config.json，或指定其他 --data-dir。",
            )
        })?;
        if c.version != 1 {
            return Err(AppError::new(
                "CONFIG_INVALID",
                "不支持的配置版本",
                2,
                "使用兼容版本的 CLI。",
            ));
        }
        Ok(c)
    }
    pub fn save(&self, root: &Path) -> Result<()> {
        atomic_write(
            &root.join("config.json"),
            &serde_json::to_vec_pretty(self).map_err(AppError::io)?,
            true,
        )
    }
    pub fn register(&mut self, installed: Installed) {
        self.installed.retain(|i| i.id != installed.id);
        self.installed.push(installed);
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Gpu {
    pub index: usize,
    pub name: String,
    pub total_mib: u64,
    pub free_mib: u64,
    pub driver: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Hardware {
    pub os: String,
    pub arch: String,
    pub cpu: String,
    pub cpu_count: usize,
    pub ram_bytes: u64,
    pub available_ram_bytes: u64,
    pub disk_available_bytes: Option<u64>,
    pub gpus: Vec<Gpu>,
    pub warnings: Vec<String>,
}
pub fn hardware(root: &Path) -> Hardware {
    let s = System::new_all();
    let disks = Disks::new_with_refreshed_list();
    let disk = disks
        .list()
        .iter()
        .filter(|d| root.starts_with(d.mount_point()))
        .max_by_key(|d| d.mount_point().as_os_str().len())
        .map(|d| d.available_space());
    let mut gpus = vec![];
    let mut warnings = vec![];
    if let Ok(out) = Command::new("nvidia-smi")
        .args([
            "--query-gpu=index,name,memory.total,memory.free,driver_version",
            "--format=csv,noheader,nounits",
        ])
        .output()
        && out.status.success()
    {
        for l in String::from_utf8_lossy(&out.stdout).lines() {
            let p: Vec<_> = l.split(',').map(str::trim).collect();
            if p.len() == 5
                && let (Ok(index), Ok(total_mib), Ok(free_mib)) =
                    (p[0].parse(), p[2].parse(), p[3].parse())
            {
                gpus.push(Gpu {
                    index,
                    name: p[1].into(),
                    total_mib,
                    free_mib,
                    driver: p[4].into(),
                });
            }
        }
    }
    if gpus.is_empty() {
        warnings.push("未检测到可用 NVIDIA GPU；CPU 模式仍可使用。".into());
    }
    if disk.is_none() {
        warnings.push("无法确定目标目录可用空间，资源判断存在不确定性。".into());
    }
    Hardware {
        os: std::env::consts::OS.into(),
        arch: std::env::consts::ARCH.into(),
        cpu: s
            .cpus()
            .first()
            .map(|c| c.brand().into())
            .unwrap_or_default(),
        cpu_count: s.cpus().len(),
        ram_bytes: s.total_memory(),
        available_ram_bytes: s.available_memory(),
        disk_available_bytes: disk,
        gpus,
        warnings,
    }
}
pub fn envelope(
    command: &str,
    status: &str,
    data: Value,
    warnings: Vec<String>,
    error: Option<AppError>,
) -> Value {
    json!({"schema_version":"1", "command":command, "status":status, "data":data, "warnings":warnings, "error":error})
}
