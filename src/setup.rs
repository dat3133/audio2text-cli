use crate::{
    catalog, runtime,
    state::{self, AppError, Config, Hardware, Installed, Result},
    storage,
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
    pub remember_location: bool,
}
impl Settings {
    pub fn options(&self) -> Options {
        Options {
            model: Some(self.model.clone()),
            language: Some(self.language.clone()),
            profile: Some(self.profile.clone()),
            source: Some(self.source.clone()),
            device: Some(self.device.clone()),
            batch_size: Some(if self.batch_size == 0 {
                "auto".into()
            } else {
                self.batch_size.to_string()
            }),
            segment_seconds: Some(self.segment_seconds),
            local_model: self.local_model.clone(),
            runtime_python: self.runtime_python.clone(),
        }
    }
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
        remember_location: false,
    })
}
pub fn plan(s: &Settings) -> Result<Value> {
    storage::validate(&s.root)?;
    let cfg = Config::load(&s.root)?;
    let m = catalog::find(&s.model)?;
    let local = s.local_model.clone().or_else(|| {
        cfg.installed
            .iter()
            .find(|i| i.id == m.id && i.verified && i.path.is_dir())
            .map(|i| i.path.clone())
    });
    let path = local
        .clone()
        .unwrap_or_else(|| s.root.join("models").join(m.id.replace('/', "-")));
    let runtime_ready =
        runtime::python(&s.root, &m.backend, &s.device).is_ok() || s.runtime_python.is_some();
    let hw = state::hardware(&s.root);
    let model_download = if local.is_some() { 0 } else { m.bytes() };
    let runtime_download = if runtime_ready {
        0
    } else if s.device == "cpu" && m.backend == "faster-whisper" {
        1_000_000_000u64
    } else {
        9_000_000_000
    };
    let (status, reasons) = catalog::assess_required(
        &m,
        &hw,
        &s.device,
        &s.language,
        model_download + runtime_download,
    );
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
        "external_python":s.runtime_python,"disk_available_bytes":hw.disk_available_bytes,
        "model_download_bytes":model_download,
        "runtime_download_estimate_bytes":runtime_download,
        "resource_basis":"conservative_estimate", "compatibility":status,"reasons":reasons,"batch_size":s.batch_size,"segment_seconds":s.segment_seconds,
        "reuses_model":local.is_some(),"changes_default_model":true}),
    )
}
pub fn execute(s: &Settings, set_default: bool) -> Result<Value> {
    execute_with(
        s,
        set_default,
        |m, path, reuses_model| {
            let py = runtime::prepare(&s.root, m, &s.device, s.runtime_python.as_deref())?;
            if reuses_model {
                catalog::verify(m, path)?;
            } else {
                runtime::install(&s.root, &py, m, path, &s.source)?;
            }
            eprintln!("执行离线模型自检 …");
            runtime::self_test(&s.root, &py, m, path, &s.device)
        },
        |root| storage::save(&state::platform_data_dir()?, root),
    )
}

fn execute_with(
    s: &Settings,
    set_default: bool,
    resources: impl FnOnce(&catalog::Model, &Path, bool) -> Result<()>,
    remember: impl FnOnce(&Path) -> Result<()>,
) -> Result<Value> {
    state::check_cancel()?;
    let p = plan(s)?;
    let m = catalog::find(&s.model)?;
    let path = PathBuf::from(p["model_path"].as_str().unwrap());
    storage::prepare(&s.root)?;
    resources(&m, &path, p["reuses_model"] == true)?;
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
    if set_default && s.remember_location {
        remember(&s.root)?;
    }
    let next_command = if state::data_dir(None).is_ok_and(|root| root == s.root) {
        "audio2text transcribe <file>".to_owned()
    } else {
        format!(
            "audio2text --data-dir \"{}\" transcribe <file>",
            s.root.display()
        )
    };
    Ok(
        json!({"ready":true,"model":m.id,"device":s.device,"model_path":path,"data_dir":s.root,"default_model":cfg.default_model,"next_command":next_command}),
    )
}

#[cfg(test)]
mod storage_tests {
    use super::*;
    use std::fs;

    fn fixture() -> (PathBuf, PathBuf, Settings) {
        let work = state::absolute(Path::new(&format!(
            ".test-data/location-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        )))
        .unwrap();
        let base = work.join("user-config");
        let old = work.join("old-storage");
        let cfg = Config {
            default_model: Some("whisper/base".into()),
            ..Config::default()
        };
        cfg.save(&old).unwrap();
        storage::save(&base, &old).unwrap();
        let target = work.join("新 模型目录");
        let options = Options {
            model: Some("whisper/tiny".into()),
            device: Some("cpu".into()),
            language: Some("zh".into()),
            ..Options::default()
        };
        let mut s = settings(
            &target,
            &Config::default(),
            &state::hardware(&target),
            &options,
        )
        .unwrap();
        s.remember_location = true;
        (base, old, s)
    }

    #[test]
    fn location_commits_only_after_resources_and_target_configuration_succeed() {
        let (base, old, s) = fixture();
        let result = execute_with(
            &s,
            true,
            |_, path, reuse| {
                assert!(!reuse);
                assert_eq!(storage::load(&base).unwrap(), Some(old.clone()));
                assert!(!s.root.join("config.json").exists());
                fs::create_dir_all(path).map_err(AppError::io)
            },
            |root| {
                assert_eq!(
                    Config::load(root)?.default_model.as_deref(),
                    Some("whisper/tiny")
                );
                storage::save(&base, root)
            },
        )
        .unwrap();
        assert_eq!(storage::load(&base).unwrap(), Some(s.root.clone()));
        assert_eq!(
            Config::load(&old).unwrap().default_model.as_deref(),
            Some("whisper/base")
        );
        assert_eq!(result["data_dir"].as_str(), s.root.to_str());
    }

    #[test]
    fn download_self_test_and_cancellation_failures_preserve_the_old_default() {
        for (code, exit) in [
            ("DOWNLOAD_FAILED", 6),
            ("INFERENCE_FAILED", 5),
            ("CANCELLED", 130),
        ] {
            let (base, old, s) = fixture();
            let before = fs::read(base.join("location.json")).unwrap();
            let error = execute_with(
                &s,
                true,
                |_, _, _| {
                    Err(AppError::new(
                        code,
                        "injected resource phase failure",
                        exit,
                        "retry",
                    ))
                },
                |_| panic!("must not save location after a failed resource phase"),
            )
            .unwrap_err();
            assert_eq!(error.code, code);
            assert_eq!(fs::read(base.join("location.json")).unwrap(), before);
            assert_eq!(storage::load(&base).unwrap(), Some(old));
            assert!(!s.root.join("config.json").exists());
        }
    }

    #[test]
    fn explicit_per_command_directory_does_not_change_the_saved_default() {
        let (base, old, mut s) = fixture();
        s.remember_location = false;
        execute_with(
            &s,
            true,
            |_, path, _| fs::create_dir_all(path).map_err(AppError::io),
            |_| panic!("per-command directory must not persist"),
        )
        .unwrap();
        assert_eq!(storage::load(&base).unwrap(), Some(old));
        assert!(s.root.join("config.json").is_file());
    }

    #[test]
    fn location_save_failure_retains_prepared_resources_and_old_location() {
        let (base, old, s) = fixture();
        let blocker = base.join("not-a-directory");
        fs::write(&blocker, "file").unwrap();
        let error = execute_with(
            &s,
            true,
            |_, path, _| fs::create_dir_all(path).map_err(AppError::io),
            |root| storage::save(&blocker, root),
        )
        .unwrap_err();
        assert_eq!(error.code, "WRITE_FAILED");
        assert!(error.hint.contains("--data-dir"));
        assert!(error.hint.contains(s.root.to_str().unwrap()));
        assert_eq!(storage::load(&base).unwrap(), Some(old));
        assert!(s.root.join("config.json").is_file());
        assert!(s.root.join("models/whisper-tiny").is_dir());
    }

    #[test]
    fn a_different_storage_root_does_not_inherit_old_model_installations() {
        let (_, old, s) = fixture();
        let model = catalog::find(&s.model).unwrap();
        let path = old.join("models/whisper-tiny");
        fs::create_dir_all(&path).unwrap();
        let mut cfg = Config::load(&old).unwrap();
        cfg.register(Installed {
            id: model.id.clone(),
            path: path.clone(),
            revision: model.revision.clone(),
            verified: true,
        });
        cfg.save(&old).unwrap();
        assert_eq!(plan(&s).unwrap()["reuses_model"], false);
        let target = s.root.join("models/whisper-tiny");
        fs::create_dir_all(&target).unwrap();
        cfg.installed[0].path = target.clone();
        cfg.save(&s.root).unwrap();
        let p = plan(&s).unwrap();
        assert_eq!(p["reuses_model"], true);
        assert_eq!(p["model_path"].as_str(), target.to_str());
    }

    #[test]
    fn plan_counts_only_missing_resources_in_the_selected_directory() {
        let (_, _, s) = fixture();
        let model = catalog::find(&s.model).unwrap();
        let missing = plan(&s).unwrap();
        assert_eq!(missing["model_download_bytes"], model.bytes());
        assert!(missing["runtime_download_estimate_bytes"].as_u64().unwrap() > 0);
        let runtime = runtime::runtime_dir(&s.root, &model.backend, &s.device);
        state::atomic_write(&runtime.join("ready.json"), json!({"python":std::env::current_exe().unwrap(), "runtime_version":runtime::RUNTIME_VERSION}).to_string().as_bytes(), true).unwrap();
        let ready_runtime = plan(&s).unwrap();
        assert_eq!(ready_runtime["runtime_download_estimate_bytes"], 0);
        assert_eq!(ready_runtime["model_download_bytes"], model.bytes());
        let path = s.root.join("models/whisper-tiny");
        let mut cfg = Config::default();
        cfg.register(Installed {
            id: model.id.clone(),
            path: path.clone(),
            revision: model.revision.clone(),
            verified: true,
        });
        cfg.save(&s.root).unwrap();
        // A stale registration is not a reusable installation.
        assert_eq!(plan(&s).unwrap()["reuses_model"], false);
        fs::create_dir_all(path).unwrap();
        let ready = plan(&s).unwrap();
        assert_eq!(ready["model_download_bytes"], 0);
        assert_eq!(ready["runtime_download_estimate_bytes"], 0);
    }
}
