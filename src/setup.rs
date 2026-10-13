use crate::{
    catalog, runtime,
    state::{self, AppError, Config, Hardware, Installed, Result},
};
use clap::Args;
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Default, Args)]
pub struct Options {
    #[arg(long)]
    pub model: Option<String>,
    #[arg(long, value_parser=["balanced", "quality", "speed"])]
    pub profile: Option<String>,
    #[arg(long)]
    pub language: Option<String>,
    #[arg(long)]
    pub device: Option<String>,
    #[arg(long, value_parser=["auto", "huggingface", "modelscope"])]
    pub source: Option<String>,
    #[arg(long)]
    pub batch_size: Option<String>,
    #[arg(long)]
    pub segment_seconds: Option<u32>,
    #[arg(long)]
    pub local_model: Option<PathBuf>,
    /// Optional reuse of an existing, independently managed Python environment.
    #[arg(long)]
    pub runtime_python: Option<PathBuf>,
}
#[derive(Clone, Debug)]
pub struct Settings {
    pub root: PathBuf,
    pub model: String,
    pub profile: String,
    pub language: String,
    pub device: String,
    pub source: String,
    pub batch_size: usize,
    pub segment_seconds: u32,
    pub local_model: Option<PathBuf>,
    pub runtime_python: Option<PathBuf>,
}
pub fn parse_batch(value: &str) -> Result<usize> {
    if value == "auto" {
        return Ok(0);
    }
    let v: usize = value.parse().map_err(|_| {
        AppError::new(
            "INVALID_ARGUMENT",
            "批大小必须是 auto 或 1–32",
            2,
            "设置 --batch-size auto。",
        )
    })?;
    if !(1..=32).contains(&v) {
        return Err(AppError::new(
            "INVALID_ARGUMENT",
            "批大小必须是 1–32",
            2,
            "设置 --batch-size auto。",
        ));
    }
    Ok(v)
}
pub fn settings(root: &Path, cfg: &Config, hw: &Hardware, o: &Options) -> Result<Settings> {
    let lang = o
        .language
        .clone()
        .unwrap_or_else(|| cfg.language.clone())
        .to_lowercase();
    let profile = o.profile.clone().unwrap_or_else(|| cfg.profile.clone());
    let dev = catalog::device(hw, o.device.as_deref().unwrap_or(&cfg.device))?;
    let model = if let Some(p) = &o.local_model {
        catalog::detect(p)?.id
    } else if let Some(id) = &o.model {
        catalog::find(id)?.id
    } else if let Some(id) = &cfg.default_model {
        catalog::find(id)?.id
    } else {
        catalog::recommend(hw, cfg, &dev, &lang, &profile)?["model"]["id"]
            .as_str()
            .unwrap()
            .into()
    };
    if let Some(id) = &o.model
        && *id != model
    {
        return Err(AppError::new(
            "MODEL_INVALID",
            "本地目录与指定模型 ID 不符",
            2,
            "检查 --model 和 --local-model。",
        ));
    }
    let m = catalog::find(&model)?;
    if !m.supports(&lang) {
        return Err(AppError::new(
            "LANGUAGE_UNSUPPORTED",
            format!("{} 不支持 {lang}", m.id),
            2,
            "选择支持此语言的模型。",
        ));
    }
    let segment = o.segment_seconds.unwrap_or(cfg.segment_seconds);
    if !(5..=1200).contains(&segment) {
        return Err(AppError::new(
            "INVALID_ARGUMENT",
            "分段长度必须是 5–1200 秒",
            2,
            "设置 --segment-seconds 60。",
        ));
    }
    let source = o.source.clone().unwrap_or_else(|| {
        if cfg.source == "modelscope" && m.modelscope_repo.is_none() {
            "auto".into()
        } else {
            cfg.source.clone()
        }
    });
    if source == "modelscope" && m.modelscope_repo.is_none() && o.local_model.is_none() {
        return Err(AppError::new(
            "SOURCE_UNAVAILABLE",
            "模型没有 ModelScope 下载源",
            2,
            "使用 --source huggingface。",
        ));
    }
    Ok(Settings {
        root: root.into(),
        model,
        profile,
        language: lang,
        device: dev,
        source,
        batch_size: if let Some(v) = &o.batch_size {
            parse_batch(v)?
        } else {
            cfg.batch_size
        },
        segment_seconds: segment,
        local_model: o
            .local_model
            .as_ref()
            .map(|p| state::absolute(p))
            .transpose()?,
        runtime_python: o.runtime_python.clone(),
    })
}
pub fn plan(s: &Settings) -> Result<Value> {
    let cfg = Config::load(&s.root)?;
    let m = catalog::find(&s.model)?;
    let local = s.local_model.clone().or_else(|| {
        cfg.installed
            .iter()
            .find(|i| i.id == m.id && i.verified)
            .map(|i| i.path.clone())
    });
    let path = local
        .clone()
        .unwrap_or_else(|| s.root.join("models").join(m.id.replace('/', "-")));
    let runtime_ready =
        runtime::python(&s.root, &m.backend, &s.device).is_ok() || s.runtime_python.is_some();
    let hw = state::hardware(&s.root);
    let (status, reasons) = catalog::assess(&m, &hw, &s.device, &s.language, local.is_some());
    if status == "incompatible" {
        return Err(AppError::new(
            "MODEL_INCOMPATIBLE",
            reasons.join("；"),
            3,
            "修改模型、语言、数据目录或设备。",
        ));
    }
    Ok(
        json!({"model":m, "device":s.device, "language":s.language, "profile":s.profile, "source":s.source,
        "data_dir":s.root,"model_path":path,"runtime_path":runtime::runtime_dir(&s.root,&m.backend,&s.device),
        "model_download_bytes":if local.is_some(){0}else{m.bytes()},
        "runtime_download_estimate_bytes":if runtime_ready{0}else if s.device=="cpu" && m.backend=="faster-whisper"{1_000_000_000u64}else{9_000_000_000},
        "resource_basis":"conservative_estimate", "compatibility":status,"reasons":reasons,"batch_size":s.batch_size,"segment_seconds":s.segment_seconds,
        "reuses_model":local.is_some(),"changes_default_model":true}),
    )
}
pub fn execute(s: &Settings, set_default: bool) -> Result<Value> {
    let p = plan(s)?;
    let m = catalog::find(&s.model)?;
    let path = PathBuf::from(p["model_path"].as_str().unwrap());
    let py = runtime::prepare(&s.root, &m, &s.device, s.runtime_python.as_deref())?;
    if p["reuses_model"] == true {
        catalog::verify(&m, &path)?;
    } else {
        runtime::install(&s.root, &py, &m, &path, &s.source)?;
    }
    eprintln!("执行离线模型自检 …");
    runtime::self_test(&s.root, &py, &m, &path, &s.device)?;
    state::check_cancel()?;
    let mut cfg = Config::load(&s.root)?;
    cfg.register(Installed {
        id: m.id.clone(),
        path: state::absolute(&path)?,
        revision: m.revision.clone(),
        verified: true,
    });
    if set_default {
        cfg.default_model = Some(m.id.clone());
        cfg.device = s.device.clone();
        cfg.language = s.language.clone();
        cfg.profile = s.profile.clone();
        cfg.source = s.source.clone();
        cfg.batch_size = s.batch_size;
        cfg.segment_seconds = s.segment_seconds;
    }
    cfg.save(&s.root)?;
    Ok(
        json!({"ready":true,"model":m.id,"device":s.device,"model_path":path,"data_dir":s.root,"default_model":cfg.default_model,"next_command":"audio2text transcribe <file>"}),
    )
}
