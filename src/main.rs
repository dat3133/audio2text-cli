mod catalog;
mod menu;
mod runtime;
mod setup;
mod state;
mod storage;
mod wizard;

use clap::{Args, Parser, Subcommand};
use serde_json::{Value, json};
use state::{AppError, Config, Result};
use std::{
    io::Write,
    path::{Path, PathBuf},
};

#[derive(Parser)]
#[command(
    name = "audio2text",
    version,
    about = "Local speech-to-text · 本地语音转文字",
    disable_help_subcommand = true
)]
struct Cli {
    #[arg(long, global = true)]
    data_dir: Option<PathBuf>,
    #[arg(long, global=true, default_value="json", value_parser=["json","text"])]
    format: String,
    #[command(subcommand)]
    command: Option<Command>,
}
#[derive(Subcommand)]
enum Command {
    /// 检查环境，不下载、不初始化。
    Doctor,
    /// 多步初始化向导；agent 使用 --non-interactive。
    Init(Init),
    /// 查看、安装、导入和切换模型。
    Models {
        #[command(subcommand)]
        command: ModelCommand,
    },
    /// 离线转写本地音频或视频。
    Transcribe(Transcribe),
}
#[derive(Args)]
struct Init {
    #[arg(long)]
    non_interactive: bool,
    #[arg(long)]
    dry_run: bool,
    #[command(flatten)]
    options: setup::Options,
}
#[derive(Subcommand)]
enum ModelCommand {
    List(Query),
    Recommend(Query),
    Install {
        id: String,
        #[command(flatten)]
        options: setup::Options,
    },
    Import {
        path: PathBuf,
        #[arg(long)]
        id: String,
    },
    Use {
        id: String,
        #[arg(long)]
        device: Option<String>,
    },
}
#[derive(Args)]
struct Query {
    #[arg(long, default_value = "auto")]
    device: String,
    #[arg(long, default_value = "auto")]
    language: String,
    #[arg(long, default_value="balanced", value_parser=["balanced","quality","speed"])]
    profile: String,
}
#[derive(Args)]
struct Transcribe {
    #[arg(required = true)]
    files: Vec<PathBuf>,
    #[arg(long)]
    model: Option<String>,
    #[arg(long)]
    language: Option<String>,
    #[arg(long)]
    device: Option<String>,
    #[arg(long, conflicts_with = "context_file")]
    context: Option<String>,
    #[arg(long)]
    context_file: Option<PathBuf>,
    #[arg(long)]
    batch_size: Option<String>,
    #[arg(long)]
    segment_seconds: Option<u32>,
    #[arg(short = 'o', long, conflicts_with = "output_dir")]
    output: Option<PathBuf>,
    #[arg(long)]
    output_dir: Option<PathBuf>,
    #[arg(long)]
    no_text: bool,
    #[arg(long)]
    overwrite: bool,
}
fn command_name(command: &Command) -> &'static str {
    match command {
        Command::Doctor => "doctor",
        Command::Init(_) => "init",
        Command::Transcribe(_) => "transcribe",
        Command::Models { command } => match command {
            ModelCommand::List(_) => "models list",
            ModelCommand::Recommend(_) => "models recommend",
            ModelCommand::Install { .. } => "models install",
            ModelCommand::Import { .. } => "models import",
            ModelCommand::Use { .. } => "models use",
        },
    }
}
fn main() {
    let _ = ctrlc::set_handler(|| {
        state::CANCELLED.store(true, std::sync::atomic::Ordering::SeqCst);
    });
    let args: Vec<String> = std::env::args().collect();
    let cli = match Cli::try_parse_from(&args) {
        Ok(v) => v,
        Err(e) => {
            use clap::error::ErrorKind;
            if matches!(e.kind(), ErrorKind::DisplayHelp | ErrorKind::DisplayVersion) {
                print!("{e}");
                return;
            }
            let name = args
                .iter()
                .skip(1)
                .find(|v| ["init", "doctor", "models", "transcribe"].contains(&v.as_str()))
                .map(|v| v.as_str())
                .unwrap_or("cli");
            let v = state::envelope(
                name,
                "error",
                json!({}),
                vec![],
                Some(AppError::new(
                    "INVALID_ARGUMENT",
                    e.to_string(),
                    2,
                    "运行 audio2text --help。",
                )),
            );
            println!("{v}");
            std::process::exit(2);
        }
    };
    let Some(ref command) = cli.command else {
        use clap::CommandFactory;
        let _ = Cli::command().print_help();
        println!();
        return;
    };
    let name = command_name(command);
    let result = (|| {
        let root = state::data_dir(cli.data_dir.as_deref())?;
        let cfg = Config::load(&root)?;
        dispatch(command, &root, &cfg, &cli.format)
    })();
    let (value, exit) = match result {
        Ok(v) => v,
        Err(e) => {
            let exit = e.exit;
            (
                state::envelope(name, "error", json!({}), vec![], Some(e)),
                exit,
            )
        }
    };
    let output = if cli.format == "text" && name == "transcribe" && value["status"] == "ok" {
        value["data"]["results"][0]["text"]
            .as_str()
            .unwrap_or("")
            .to_string()
    } else if cli.format == "text" {
        serde_json::to_string_pretty(&value).unwrap()
    } else {
        value.to_string()
    };
    if writeln!(std::io::stdout().lock(), "{output}").is_err() {
        std::process::exit(7);
    }
    std::process::exit(exit);
}
fn dispatch(command: &Command, root: &Path, cfg: &Config, format: &str) -> Result<(Value, i32)> {
    let name = command_name(command);
    let hw = state::hardware(root);
    let data = match command {
        Command::Doctor => {
            let default = cfg
                .default_model
                .as_deref()
                .and_then(|id| cfg.installed.iter().find(|i| i.id == id));
            let ready = default.is_some_and(|i| {
                i.verified
                    && i.path.is_dir()
                    && catalog::find(&i.id)
                        .is_ok_and(|m| runtime::python(root, &m.backend, &cfg.device).is_ok())
            });
            json!({"version":env!("CARGO_PKG_VERSION"),"data_dir":root,"hardware":hw,"default_model":cfg.default_model,"ready":ready,"installed":cfg.installed,"next_command":if ready{"audio2text transcribe <file>"}else{"audio2text init"}})
        }
        Command::Init(init) => {
            if init.dry_run {
                setup::plan(&setup::settings(root, cfg, &hw, &init.options)?)?
            } else if init.non_interactive {
                setup::execute(&setup::settings(root, cfg, &hw, &init.options)?, true)?
            } else {
                if !wizard::interactive() {
                    return Err(AppError::new(
                        "INTERACTION_REQUIRED",
                        "初始化向导需要交互终端",
                        2,
                        "使用 audio2text init --non-interactive --profile balanced，或先执行 init --dry-run。",
                    ));
                }
                let mut settings = wizard::run(root, cfg, &init.options)?;
                loop {
                    match setup::execute(&settings, true) {
                        Ok(data) => {
                            eprintln!(
                                "\n初始化成功。示例：{}",
                                data["next_command"]
                                    .as_str()
                                    .unwrap_or("audio2text transcribe <file>")
                            );
                            break data;
                        }
                        Err(e) if e.exit == 130 => return Err(e),
                        Err(e) => match wizard::recovery(&e)? {
                            0 => continue,
                            1 => {
                                let remember = settings.remember_location;
                                let options = settings.options();
                                settings = wizard::run(
                                    &settings.root,
                                    &Config::load(&settings.root)?,
                                    &options,
                                )?;
                                settings.remember_location |= remember;
                            }
                            _ => return Err(e),
                        },
                    }
                }
            }
        }
        Command::Models { command } => match command {
            ModelCommand::List(q) => {
                json!({"models":catalog::listing(&hw,cfg,&q.device,&q.language.to_lowercase(),&q.profile)?,"hardware":hw})
            }
            ModelCommand::Recommend(q) => {
                json!({"recommendation":catalog::recommend(&hw,cfg,&q.device,&q.language.to_lowercase(),&q.profile)?,"hardware":hw})
            }
            ModelCommand::Install { id, options } => {
                let mut opts = options.clone();
                opts.model = Some(id.clone());
                setup::execute(&setup::settings(root, cfg, &hw, &opts)?, false)?
            }
            ModelCommand::Import { path, id } => {
                let m = catalog::find(id)?;
                let path = state::absolute(path)?;
                catalog::verify(&m, &path)?;
                let mut cfg = cfg.clone();
                cfg.register(state::Installed {
                    id: id.clone(),
                    path: path.clone(),
                    revision: m.revision,
                    verified: true,
                });
                cfg.save(root)?;
                json!({"model":id,"path":path,"verified":true,"next_command":format!("audio2text init --model {id}")})
            }
            ModelCommand::Use { id, device } => {
                let m = catalog::find(id)?;
                if !m.supports(&cfg.language) {
                    return Err(AppError::new(
                        "LANGUAGE_UNSUPPORTED",
                        "新模型不支持当前默认语言",
                        2,
                        "通过 init --model <id> --language auto 重新选择语言。",
                    ));
                }
                let installed = cfg
                    .installed
                    .iter()
                    .find(|i| i.id == *id && i.verified && i.path.is_dir())
                    .ok_or_else(|| {
                        AppError::new(
                            "MODEL_NOT_INSTALLED",
                            format!("模型尚未安装：{id}"),
                            3,
                            "运行 models install 或 models import。",
                        )
                    })?;
                let (dev, py, _) =
                    available_runtime(root, &m.backend, &hw, device.as_deref().unwrap_or("auto"))?;
                catalog::verify(&m, &installed.path)?;
                runtime::self_test(root, &py, &m, &installed.path, &dev)?;
                let mut cfg = cfg.clone();
                cfg.default_model = Some(id.clone());
                cfg.device = dev;
                cfg.save(root)?;
                json!({"default_model":id,"device":cfg.device})
            }
        },
        Command::Transcribe(t) => return transcribe(root, cfg, &hw, t, format),
    };
    Ok((state::envelope(name, "ok", data, vec![], None), 0))
}
fn same_path(a: &Path, b: &Path) -> bool {
    let a = a.canonicalize().unwrap_or_else(|_| a.to_path_buf());
    let b = b.canonicalize().unwrap_or_else(|_| b.to_path_buf());
    if cfg!(windows) {
        a.to_string_lossy()
            .eq_ignore_ascii_case(&b.to_string_lossy())
    } else {
        a == b
    }
}
fn available_runtime(
    root: &Path,
    backend: &str,
    hw: &state::Hardware,
    requested: &str,
) -> Result<(String, PathBuf, bool)> {
    let mut dev = catalog::device(hw, requested)?;
    let mut fallback = false;
    if requested == "auto"
        && runtime::python(root, backend, &dev).is_err()
        && runtime::python(root, backend, "cpu").is_ok()
    {
        dev = "cpu".into();
        fallback = true;
    }
    let py = runtime::python(root, backend, &dev)?;
    Ok((dev, py, fallback))
}
fn input_files(t: &Transcribe) -> Result<Vec<Value>> {
    if t.output.is_some() && t.files.len() != 1 {
        return Err(AppError::new(
            "INVALID_ARGUMENT",
            "--output 仅适用于单文件",
            2,
            "批量使用 --output-dir。",
        ));
    }
    if t.no_text && t.output.is_none() && t.output_dir.is_none() {
        return Err(AppError::new(
            "INVALID_ARGUMENT",
            "--no-text 需要文件输出路径",
            2,
            "传入 --output 或 --output-dir。",
        ));
    }
    let mut files = vec![];
    let mut outputs: Vec<PathBuf> = vec![];
    for path in &t.files {
        let input = state::absolute(path)?;
        let output = t
            .output
            .clone()
            .or_else(|| {
                t.output_dir.as_ref().map(|d| {
                    d.join(format!(
                        "{}.transcript.txt",
                        path.file_stem().unwrap_or_default().to_string_lossy()
                    ))
                })
            })
            .map(|p| state::absolute(&p))
            .transpose()?;
        if let Some(p) = &output {
            if outputs.iter().any(|v| same_path(v, p)) {
                return Err(AppError::new(
                    "OUTPUT_CONFLICT",
                    format!("批量输出路径冲突：{}", p.display()),
                    7,
                    "分别指定输出位置或修改同名输入文件。",
                ));
            }
            if t.files
                .iter()
                .any(|source| state::absolute(source).is_ok_and(|s| same_path(&s, p)))
            {
                return Err(AppError::new(
                    "OUTPUT_CONFLICT",
                    "输出路径不能覆盖输入音视频",
                    7,
                    "更换输出路径。",
                ));
            }
            outputs.push(p.clone());
        }
        let error = if !input.is_file() {
            Some(AppError::new(
                "INPUT_INVALID",
                format!("输入不是可读取文件：{}", input.display()),
                4,
                "检查本地文件路径。",
            ))
        } else if output.as_ref().is_some_and(|p| p.exists()) && !t.overwrite {
            Some(AppError::new(
                "OUTPUT_EXISTS",
                "输出文件已存在",
                7,
                "使用 --overwrite 或其他路径。",
            ))
        } else {
            None
        };
        files.push(json!({"input":input,"output":output,"error":error,"exit":error.as_ref().map(|e|e.exit)}));
    }
    Ok(files)
}
fn transcribe(
    root: &Path,
    cfg: &Config,
    hw: &state::Hardware,
    t: &Transcribe,
    format: &str,
) -> Result<(Value, i32)> {
    if format == "text" && (t.files.len() != 1 || t.no_text) {
        return Err(AppError::new(
            "INVALID_ARGUMENT",
            "text 输出要求单文件且包含正文",
            2,
            "使用 --format json。",
        ));
    }
    let files = input_files(t)?;
    let id = t
        .model
        .as_ref()
        .or(cfg.default_model.as_ref())
        .ok_or_else(|| {
            AppError::new(
                "ENV_NOT_READY",
                "没有默认模型",
                3,
                "运行 audio2text init，agent 使用 init --non-interactive。",
            )
        })?;
    let m = catalog::find(id)?;
    let installed = cfg
        .installed
        .iter()
        .find(|i| i.id == *id && i.verified)
        .ok_or_else(|| {
            AppError::new(
                "MODEL_NOT_INSTALLED",
                format!("模型未安装：{id}"),
                3,
                "运行 models install 或 init。",
            )
        })?;
    let language = t
        .language
        .as_deref()
        .unwrap_or(&cfg.language)
        .to_lowercase();
    if !m.supports(&language) {
        return Err(AppError::new(
            "LANGUAGE_UNSUPPORTED",
            format!("{id} 不支持语言 {language}"),
            2,
            "选择支持此语言的模型。",
        ));
    }
    let requested = t.device.as_deref().unwrap_or(&cfg.device);
    let (dev, py, fallback) = available_runtime(root, &m.backend, hw, requested)?;
    let mut warnings = vec![];
    if fallback {
        warnings.push("自动模式使用已就绪的 CPU 运行时。".into());
    }
    let segment = t.segment_seconds.unwrap_or(cfg.segment_seconds);
    if !(5..=1200).contains(&segment) {
        return Err(AppError::new(
            "INVALID_ARGUMENT",
            "分段长度必须是 5–1200 秒",
            2,
            "使用 --segment-seconds 60。",
        ));
    }
    let batch = if let Some(v) = &t.batch_size {
        setup::parse_batch(v)?
    } else {
        cfg.batch_size
    };
    let context = if let Some(p) = &t.context_file {
        std::fs::read_to_string(p).map_err(|e| {
            AppError::new("INPUT_INVALID", e.to_string(), 4, "提供 UTF-8 上下文文件。")
        })?
    } else {
        t.context.clone().unwrap_or_default()
    };
    let mut data = runtime::worker(
        root,
        &py,
        json!({"action":"transcribe","model":m,"path":installed.path,"device":dev,"language":language,"context":context,"batch_size":batch,"segment_seconds":segment,"files":files,"overwrite":t.overwrite,"no_text":t.no_text}),
        true,
    )?;
    let results = data["results"]
        .as_array_mut()
        .ok_or_else(|| AppError::new("INTERNAL_ERROR", "缺少转写结果", 1, "查看 stderr。"))?;
    let fail = results.iter().filter(|v| v["status"] != "ok").count();
    let total = results.len();
    let (status, exit, error) = if fail == 0 {
        ("ok", 0, None)
    } else if fail < total {
        (
            "partial",
            8,
            Some(AppError::new(
                "PARTIAL_FAILURE",
                format!("{fail}/{total} 个文件失败"),
                8,
                "检查每个文件的 error。",
            )),
        )
    } else {
        let first = &results[0];
        let mut e: AppError =
            serde_json::from_value(first["error"].clone()).map_err(AppError::io)?;
        e.exit = first["exit"].as_i64().unwrap_or(5) as i32;
        ("error", e.exit, Some(e))
    };
    for result in results {
        if let Some(map) = result.as_object_mut() {
            map.remove("exit");
        }
    }
    Ok((
        state::envelope("transcribe", status, data, warnings, error),
        exit,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn format_is_global_and_interspersed() {
        assert!(
            Cli::try_parse_from([
                "audio2text",
                "transcribe",
                "a.wav",
                "--format",
                "json",
                "b.wav",
                "--data-dir",
                "data"
            ])
            .is_ok()
        );
    }
    #[test]
    fn zero_batch_is_rejected() {
        assert!(setup::parse_batch("0").is_err());
        assert_eq!(setup::parse_batch("auto").unwrap(), 0);
    }
    #[test]
    fn no_text_needs_output() {
        let cli = Cli::try_parse_from(["audio2text", "transcribe", "a.wav", "--no-text"]).unwrap();
        if let Some(Command::Transcribe(t)) = cli.command {
            assert!(input_files(&t).is_err());
        }
    }
    #[test]
    fn batch_same_stem_rejected() {
        let cli = Cli::try_parse_from([
            "audio2text",
            "transcribe",
            "a/test.wav",
            "b/test.mp4",
            "--output-dir",
            "out",
        ])
        .unwrap();
        if let Some(Command::Transcribe(t)) = cli.command {
            assert_eq!(input_files(&t).unwrap_err().code, "OUTPUT_CONFLICT");
        }
    }
    #[test]
    fn output_cannot_replace_input() {
        let cli = Cli::try_parse_from([
            "audio2text",
            "transcribe",
            "a.wav",
            "--output",
            "a.wav",
            "--overwrite",
        ])
        .unwrap();
        if let Some(Command::Transcribe(t)) = cli.command {
            assert_eq!(input_files(&t).unwrap_err().code, "OUTPUT_CONFLICT");
        }
    }
    #[test]
    fn catalogs_are_pinned_and_complete() {
        let models = catalog::models();
        assert_eq!(models.len(), 8);
        for m in models {
            assert_eq!(m.revision.len(), 40);
            assert!(!m.files.is_empty());
            assert!(m.files.iter().all(|f| f.1 > 0));
        }
    }
    fn cpu() -> state::Hardware {
        state::Hardware {
            os: "linux".into(),
            arch: "x86_64".into(),
            cpu: "test".into(),
            cpu_count: 4,
            ram_bytes: 16 << 30,
            available_ram_bytes: 12 << 30,
            disk_available_bytes: Some(100 << 30),
            gpus: vec![],
            warnings: vec![],
        }
    }
    #[test]
    fn cpu_recommendation_uses_lightweight_backend() {
        let result =
            catalog::recommend(&cpu(), &Config::default(), "auto", "auto", "balanced").unwrap();
        assert_eq!(result["model"]["backend"], "faster-whisper");
    }
    #[test]
    fn explicit_gpu_does_not_fallback() {
        assert_eq!(
            catalog::device(&cpu(), "cuda:0").unwrap_err().code,
            "DEVICE_UNAVAILABLE"
        );
        assert_eq!(catalog::device(&cpu(), "auto").unwrap(), "cpu");
    }
    #[test]
    fn low_disk_has_no_install_candidate() {
        let mut hw = cpu();
        hw.disk_available_bytes = Some(1);
        assert_eq!(
            catalog::recommend(&hw, &Config::default(), "auto", "auto", "balanced")
                .unwrap_err()
                .code,
            "NO_COMPATIBLE_MODEL"
        );
    }
    #[test]
    fn disk_assessment_uses_the_actual_missing_resource_budget() {
        let mut hw = cpu();
        hw.disk_available_bytes = Some(1024);
        let model = catalog::find("whisper/tiny").unwrap();
        assert_ne!(
            catalog::assess_required(&model, &hw, "cpu", "auto", 1024).0,
            "incompatible"
        );
        assert_eq!(
            catalog::assess_required(&model, &hw, "cpu", "auto", 1025).0,
            "incompatible"
        );
        assert_ne!(
            catalog::assess_required(&model, &hw, "cpu", "auto", 0).0,
            "incompatible"
        );
    }
    #[test]
    fn unsupported_language_not_recommended() {
        let result =
            catalog::recommend(&cpu(), &Config::default(), "auto", "yue", "balanced").unwrap();
        assert!(
            result["model"]["languages"] == "qwen" || result["model"]["languages"] == "whisper-v3"
        );
    }
    #[test]
    fn unknown_disk_does_not_hide_resource_fit() {
        let mut hw = cpu();
        hw.disk_available_bytes = None;
        hw.available_ram_bytes = 3 << 30;
        let value = catalog::recommend(&hw, &Config::default(), "cpu", "auto", "balanced").unwrap();
        assert_eq!(value["model"]["id"], "whisper/base");
        assert_eq!(value["status"], "information_incomplete");
    }
    #[test]
    fn changing_backend_does_not_reuse_an_invalid_mirror() {
        let cfg = Config {
            source: "modelscope".into(),
            ..Config::default()
        };
        let opts = setup::Options {
            model: Some("whisper/tiny".into()),
            device: Some("cpu".into()),
            ..Default::default()
        };
        let value = setup::settings(Path::new("test"), &cfg, &cpu(), &opts).unwrap();
        assert_eq!(value.source, "auto");
        let explicit = setup::Options {
            source: Some("modelscope".into()),
            ..opts
        };
        assert_eq!(
            setup::settings(Path::new("test"), &cfg, &cpu(), &explicit)
                .unwrap_err()
                .code,
            "SOURCE_UNAVAILABLE"
        );
    }
    #[test]
    #[ignore = "downloads an isolated CPU runtime; run explicitly for release validation"]
    fn managed_qwen_runtime_imports_from_unicode_directory() {
        let root = state::absolute(Path::new(".test-data/managed-Qwen 中文 空格")).unwrap();
        let m = catalog::find("qwen3-asr/0.6b").unwrap();
        let py = runtime::prepare(&root, &m, "cpu", None).unwrap();
        assert!(py.is_file());
        assert!(runtime::python(&root, &m.backend, "cpu").is_ok());
    }
    #[test]
    fn auto_device_uses_installed_cpu_runtime_on_gpu_host() {
        let root = state::absolute(Path::new(&format!(
            ".test-data/runtime-selection-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        )))
        .unwrap();
        let py = std::env::current_exe().unwrap();
        let path = runtime::runtime_dir(&root, "faster-whisper", "cpu").join("ready.json");
        state::atomic_write(
            &path,
            json!({"python":py,"runtime_version":runtime::RUNTIME_VERSION})
                .to_string()
                .as_bytes(),
            true,
        )
        .unwrap();
        let mut hw = cpu();
        hw.gpus.push(state::Gpu {
            index: 0,
            name: "test GPU".into(),
            total_mib: 8192,
            free_mib: 8192,
            driver: "560.0".into(),
        });
        let value = available_runtime(&root, "faster-whisper", &hw, "auto").unwrap();
        assert_eq!(value.0, "cpu");
        assert!(value.2);
        assert_eq!(
            available_runtime(&root, "faster-whisper", &hw, "cuda:0")
                .unwrap_err()
                .code,
            "ENV_NOT_READY"
        );
    }
    #[test]
    fn parent_directory_alias_cannot_replace_input() {
        let cli = Cli::try_parse_from([
            "audio2text",
            "transcribe",
            "a.wav",
            "--output",
            "child/../a.wav",
            "--overwrite",
        ])
        .unwrap();
        if let Some(Command::Transcribe(t)) = cli.command {
            assert_eq!(input_files(&t).unwrap_err().code, "OUTPUT_CONFLICT");
        }
    }
}
