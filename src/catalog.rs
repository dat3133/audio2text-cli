use crate::state::{AppError, Config, Hardware, Result};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::Digest;
use std::{fs, io::Read, path::Path};

pub const CATALOG: &str = include_str!("../assets/models.json");
pub const QWEN_LANGUAGES: &str =
    "zh en yue ar de fr es pt id it ko ru th vi ja tr hi ms nl sv da fi pl cs fil fa el hu mk ro";
pub const WHISPER_LANGUAGES: &str = "en zh de es ru ko fr ja pt tr pl ca nl ar sv it id hi fi vi he uk el ms cs ro da hu ta no th ur hr bg lt la mi ml cy sk te fa lv bn sr az sl kn et mk br eu is hy ne mn bs kk sq sw gl mr pa si km sn yo so af oc ka be tg sd gu am yi lo uz fo ht ps tk nn mt sa lb my bo tl mg as tt haw ln ha ba jw su";

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Model {
    pub id: String,
    pub name: String,
    pub backend: String,
    pub repo: String,
    pub revision: String,
    pub license: String,
    pub ram_gib: u64,
    pub vram_gib: u64,
    pub quality_rank: i32,
    pub speed_rank: i32,
    pub languages: String,
    pub modelscope_repo: Option<String>,
    pub files: Vec<(String, u64, String, String)>,
}
impl Model {
    pub fn bytes(&self) -> u64 {
        self.files.iter().map(|f| f.1).sum()
    }
    pub fn supports(&self, lang: &str) -> bool {
        lang == "auto"
            || if self.languages == "qwen" {
                QWEN_LANGUAGES.split_whitespace().any(|l| l == lang)
            } else {
                WHISPER_LANGUAGES.split_whitespace().any(|l| l == lang)
                    || (lang == "yue" && self.languages == "whisper-v3")
            }
    }
}
pub fn models() -> Vec<Model> {
    serde_json::from_str(CATALOG).expect("embedded catalog must be valid")
}
pub fn find(id: &str) -> Result<Model> {
    models().into_iter().find(|m| m.id == id).ok_or_else(|| {
        AppError::new(
            "MODEL_UNKNOWN",
            format!("未知模型：{id}"),
            2,
            "运行 audio2text models list。",
        )
    })
}
pub fn verify(m: &Model, path: &Path) -> Result<()> {
    for (name, size, method, hash) in &m.files {
        crate::state::check_cancel()?;
        eprintln!("校验 {name} …");
        let mut f = fs::File::open(path.join(name)).map_err(|e| {
            AppError::new(
                "MODEL_INVALID",
                format!("{name}: {e}"),
                3,
                "重新安装模型，或导入对应固定版本。",
            )
        })?;
        if f.metadata().map_err(AppError::io)?.len() != *size {
            return Err(AppError::new(
                "MODEL_INVALID",
                format!("{name} 大小不符"),
                3,
                "重新安装模型。",
            ));
        }
        let mut sha256 = sha2::Sha256::new();
        let mut sha1 = sha1::Sha1::new();
        if method == "git-sha1" {
            sha1.update(format!("blob {size}\0").as_bytes());
        }
        let mut buf = [0u8; 65536];
        loop {
            crate::state::check_cancel()?;
            let n = f.read(&mut buf).map_err(AppError::io)?;
            if n == 0 {
                break;
            }
            if method == "sha256" {
                sha256.update(&buf[..n]);
            } else {
                sha1.update(&buf[..n]);
            }
        }
        let actual = if method == "sha256" {
            format!("{:x}", sha256.finalize())
        } else {
            format!("{:x}", sha1.finalize())
        };
        if actual != *hash {
            return Err(AppError::new(
                "MODEL_INVALID",
                format!("{name} 校验不符"),
                3,
                "模型必须匹配目录中的固定修订。",
            ));
        }
    }
    Ok(())
}
pub fn detect(path: &Path) -> Result<Model> {
    models()
        .into_iter()
        .find(|m| {
            m.files
                .iter()
                .all(|(n, s, _, _)| fs::metadata(path.join(n)).is_ok_and(|v| v.len() == *s))
        })
        .ok_or_else(|| {
            AppError::new(
                "MODEL_UNKNOWN",
                "本地目录不匹配模型目录中的固定版本",
                3,
                "指定目录内支持的模型，或下载模型。",
            )
        })
}
pub fn device(hw: &Hardware, requested: &str) -> Result<String> {
    if requested == "cpu" {
        return Ok("cpu".into());
    }
    let index = if requested == "auto" || requested == "cuda" {
        0
    } else if let Some(s) = requested.strip_prefix("cuda:") {
        s.parse().map_err(|_| {
            AppError::new(
                "INVALID_ARGUMENT",
                "CUDA 设备编号无效",
                2,
                "使用 cpu、auto、cuda 或 cuda:0。",
            )
        })?
    } else {
        return Err(AppError::new(
            "INVALID_ARGUMENT",
            "不支持的设备",
            2,
            "使用 cpu、auto、cuda 或 cuda:0。",
        ));
    };
    let gpu = hw.gpus.iter().find(|g| g.index == index);
    let usable = gpu.filter(|g| {
        g.driver
            .split('.')
            .next()
            .and_then(|v| v.parse::<u32>().ok())
            .unwrap_or(0)
            >= 560
    });
    if usable.is_some() {
        Ok(format!("cuda:{index}"))
    } else if requested == "auto" {
        Ok("cpu".into())
    } else {
        Err(AppError::new(
            "DEVICE_UNAVAILABLE",
            "CUDA 设备不存在或驱动不符合 cu126 运行配置",
            3,
            "使用 --device cpu，或检查 NVIDIA 驱动（最低 560 系列）。",
        ))
    }
}
pub fn assess(
    m: &Model,
    hw: &Hardware,
    dev: &str,
    lang: &str,
    installed: bool,
) -> (String, Vec<String>) {
    let runtime_budget = if dev == "cpu" && m.backend == "faster-whisper" {
        1_000_000_000
    } else {
        9_000_000_000
    };
    assess_required(
        m,
        hw,
        dev,
        lang,
        if installed {
            0
        } else {
            m.bytes() + runtime_budget
        },
    )
}

pub fn assess_required(
    m: &Model,
    hw: &Hardware,
    dev: &str,
    lang: &str,
    required_bytes: u64,
) -> (String, Vec<String>) {
    let mut reasons = vec![];
    if hw.arch != "x86_64" || !["windows", "linux"].contains(&hw.os.as_str()) {
        return (
            "incompatible".into(),
            vec!["首版只支持 Windows/Linux x86_64".into()],
        );
    }
    if !m.supports(lang) {
        return (
            "incompatible".into(),
            vec![format!("模型不支持语言 {lang}")],
        );
    }
    if hw.disk_available_bytes.is_some_and(|n| n < required_bytes) {
        return (
            "incompatible".into(),
            vec!["磁盘空间不足（包含运行时预留）".into()],
        );
    }
    let disk_unknown = hw.disk_available_bytes.is_none();
    if hw.available_ram_bytes < m.ram_gib * 1024 * 1024 * 1024 {
        reasons.push("当前可用内存低于保守估计".into());
    }
    if let Some(index) = dev.strip_prefix("cuda:") {
        let idx: usize = index.parse().unwrap_or(0);
        if hw
            .gpus
            .iter()
            .find(|g| g.index == idx)
            .is_none_or(|g| g.free_mib < m.vram_gib * 1024)
        {
            reasons.push("当前空闲显存低于保守估计".into());
        }
    } else if m.backend == "qwen3-asr" || m.ram_gib >= 6 {
        reasons.push("CPU 上可能较慢".into());
    }
    if reasons.is_empty() && disk_unknown {
        reasons.push("磁盘空间未知；其他估计资源条件满足，需确认剩余空间".into());
        ("information_incomplete".into(), reasons)
    } else if reasons.is_empty() {
        reasons.push("平台与估计资源条件满足；性能仍需本机验证".into());
        ("recommended_runtime".into(), reasons)
    } else {
        ("not_recommended".into(), reasons)
    }
}
pub fn listing(
    hw: &Hardware,
    cfg: &Config,
    requested: &str,
    lang: &str,
    profile: &str,
) -> Result<Vec<Value>> {
    let dev = device(hw, requested)?;
    let mut values: Vec<_> = models().into_iter().map(|m| {
        let installed = cfg.installed.iter().any(|i| i.id == m.id && i.verified && i.path.is_dir());
        let (status, reasons) = assess(&m, hw, &dev, lang, installed);
        let mut score = match profile { "quality" => m.quality_rank * 5 + m.speed_rank, "speed" => m.speed_rank * 5 + m.quality_rank, _ => m.quality_rank * 3 + m.speed_rank * 2 };
        if dev == "cpu" {
            if m.backend == "qwen3-asr" { score -= 15; }
            if m.ram_gib >= 6 { score -= 10; }
        }
        if (lang == "zh" || lang == "yue") && m.backend == "qwen3-asr" && dev != "cpu" { score += 4; }
        if status == "not_recommended" { score -= 30; }
        if status == "incompatible" { score -= 1000; }
        let mut languages: Vec<_> = if m.languages == "qwen" { QWEN_LANGUAGES } else { WHISPER_LANGUAGES }.split_whitespace().collect();
        if m.languages == "whisper-v3" { languages.push("yue"); }
        json!({"model":m, "supported_languages":languages, "status":status, "reasons":reasons, "installed":installed, "device":dev, "score":score, "resource_basis":"conservative_estimate", "ranking_basis":"heuristic_pending_benchmark"})
    }).collect();
    values.sort_by_key(|v| -(v["score"].as_i64().unwrap_or(0)));
    Ok(values)
}
pub fn recommend(
    hw: &Hardware,
    cfg: &Config,
    requested: &str,
    lang: &str,
    profile: &str,
) -> Result<Value> {
    listing(hw, cfg, requested, lang, profile)?
        .into_iter()
        .find(|v| v["status"] != "incompatible")
        .ok_or_else(|| {
            AppError::new(
                "NO_COMPATIBLE_MODEL",
                "没有兼容模型",
                3,
                "检查平台、语言和磁盘空间。",
            )
        })
}
