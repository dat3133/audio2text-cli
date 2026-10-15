//! User-selected storage lives separately from the stable, small location record.
use crate::state::{self, AppError, Result};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
};

#[derive(Serialize, Deserialize)]
struct Location {
    version: u32,
    data_dir: PathBuf,
}

fn invalid(message: impl Into<String>) -> AppError {
    AppError::new(
        "CONFIG_INVALID",
        message,
        2,
        "恢复存储目录，或使用 --data-dir <目录> 显式指定位置。",
    )
}

pub fn load(base: &Path) -> Result<Option<PathBuf>> {
    let path = base.join("location.json");
    let bytes = match fs::read(&path) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(invalid(format!("无法读取 {}：{e}", path.display()))),
    };
    let location: Location = serde_json::from_slice(&bytes)
        .map_err(|e| invalid(format!("存储位置记录无效（{}）：{e}", path.display())))?;
    if location.version != 1 || !location.data_dir.is_absolute() {
        return Err(invalid("存储位置记录的版本或路径无效"));
    }
    if !location.data_dir.is_dir() {
        return Err(invalid(format!(
            "已保存的存储目录不可用：{}",
            location.data_dir.display()
        )));
    }
    Ok(Some(location.data_dir))
}

pub fn validate(path: &Path) -> Result<PathBuf> {
    if path.as_os_str().is_empty() {
        return Err(invalid("存储目录不能为空"));
    }
    let path = state::absolute(path)?;
    // New directories are allowed, but the volume and all existing ancestors
    // must be accessible directories. This check never creates anything.
    for ancestor in path.ancestors() {
        match fs::metadata(ancestor) {
            Ok(meta) if meta.is_dir() => return Ok(path),
            Ok(_) => {
                return Err(invalid(format!(
                    "路径指向文件而非目录：{}",
                    ancestor.display()
                )));
            }
            Err(e) if e.kind() == ErrorKind::NotFound => (),
            Err(e) => {
                return Err(invalid(format!(
                    "存储目录不可用（{}）：{e}",
                    path.display()
                )));
            }
        }
    }
    Err(invalid(format!("无法访问目标磁盘：{}", path.display())))
}

pub fn prepare(root: &Path) -> Result<()> {
    let root = validate(root)?;
    // Run only after confirmation, before downloads. Retain this tiny probe
    // instead of deleting user-directory files during cleanup.
    state::atomic_write(
        &root.join(".storage-check"),
        b"audio2text storage writable\n",
        true,
    )
}

pub fn save(base: &Path, root: &Path) -> Result<()> {
    let root = validate(root)?;
    let record = Location {
        version: 1,
        data_dir: root.clone(),
    };
    let bytes = serde_json::to_vec_pretty(&record).map_err(AppError::io)?;
    state::atomic_write(&base.join("location.json"), &bytes, true).map_err(|e| {
        AppError::new("WRITE_FAILED", format!("资源已准备，但默认存储位置保存失败：{}", e.message), 7,
            &format!("原默认位置未改变。检查配置目录权限后重试；也可使用 audio2text --data-dir \"{}\" transcribe <file>。", root.display()))
    })
}
