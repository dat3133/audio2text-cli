use crate::{
    catalog, menu,
    setup::{self, Options, Settings},
    state::{self, AppError, Config, Result},
};
use console::Term;
use dialoguer::{Input, theme::ColorfulTheme};
use std::{
    io::IsTerminal,
    path::{Path, PathBuf},
};

pub fn interactive() -> bool {
    std::io::stdin().is_terminal() && std::io::stderr().is_terminal()
}
fn ui_error(e: impl std::fmt::Display) -> AppError {
    AppError::new(
        "CANCELLED",
        e.to_string(),
        130,
        "重新运行 init 可继续配置。",
    )
}
fn choose(title: &str, labels: &[String], default: usize, back: bool) -> Result<Option<usize>> {
    state::check_cancel()?;
    let mut items = labels.to_vec();
    if back {
        items.push("← 返回上一步".into());
    }
    let default = default.min(items.len() - 1);
    let plain = std::env::var("TERM").as_deref() == Ok("dumb")
        || std::env::var("AUDIO2TEXT_PLAIN").as_deref() == Ok("1");
    let selected = if plain {
        choose_number(title, &items, default)?
    } else {
        match menu::select(&Term::stderr(), title, &items, default).map_err(ui_error)? {
            menu::Selection::Chosen(index) => index,
            menu::Selection::Plain(index) => {
                eprintln!("终端空间不足，改用编号选择；直接回车保留当前选项。");
                choose_number(title, &items, index)?
            }
        }
    };
    if back && selected == labels.len() {
        Ok(None)
    } else {
        Ok(Some(selected))
    }
}
fn choose_number(title: &str, items: &[String], default: usize) -> Result<usize> {
    loop {
        eprintln!("\n{title}");
        for (i, item) in items.iter().enumerate() {
            eprintln!(
                "  {}. {}{}",
                i + 1,
                item,
                if i == default { "（默认）" } else { "" }
            );
        }
        let answer: String = Input::new()
            .with_prompt("输入编号")
            .allow_empty(true)
            .interact_on(&Term::stderr())
            .map_err(ui_error)?;
        if answer.is_empty() {
            return Ok(default);
        }
        if let Ok(n) = answer.parse::<usize>()
            && n > 0
            && n <= items.len()
        {
            return Ok(n - 1);
        }
        eprintln!("请输入有效编号。");
    }
}
fn input(title: &str, default: String) -> Result<String> {
    Input::with_theme(&ColorfulTheme::default())
        .with_prompt(title)
        .default(default)
        .interact_on(&Term::stderr())
        .map_err(ui_error)
}
pub fn run(root: &Path, cfg: &Config, opts: &Options) -> Result<Settings> {
    let mut root = root.to_path_buf();
    let mut opts = opts.clone();
    let mut advanced = false;
    let mut step: usize = 0;
    let mut show_all = false;
    let mut hw = state::hardware(&root);
    eprintln!(
        "\naudio2text · 初始化向导\n检测：{} {} / {} / 内存 {:.1} GiB",
        hw.os,
        hw.arch,
        hw.cpu,
        hw.ram_bytes as f64 / 1024f64.powi(3)
    );
    for gpu in &hw.gpus {
        eprintln!(
            "GPU {}：{}，空闲显存 {} MiB",
            gpu.index, gpu.name, gpu.free_mib
        );
    }
    eprintln!("方向键选择，Enter 确认；每步可返回，Ctrl+C 取消。\n");
    loop {
        state::check_cancel()?;
        let mut back = false;
        match step {
            0 => {
                advanced = choose(
                    "配置模式",
                    &["推荐设置".into(), "高级配置".into()],
                    usize::from(advanced),
                    false,
                )?
                .unwrap()
                    == 1;
            }
            1 => {
                let values = ["auto", "zh", "en", "yue", "ja", "ko", "other"];
                let labels = [
                    "自动识别",
                    "中文",
                    "英语",
                    "粤语",
                    "日语",
                    "韩语",
                    "其他语言代码",
                ]
                .map(String::from);
                let current = opts.language.as_deref().unwrap_or(&cfg.language);
                if let Some(i) = choose(
                    "识别语言",
                    &labels,
                    values.iter().position(|v| *v == current).unwrap_or(0),
                    true,
                )? {
                    opts.language = Some(if values[i] == "other" {
                        input("语言代码", current.into())?
                    } else {
                        values[i].into()
                    });
                } else {
                    back = true;
                }
            }
            2 => {
                let values = ["balanced", "quality", "speed"];
                if let Some(i) = choose(
                    "推荐偏好",
                    &[
                        "准确率与速度均衡".into(),
                        "准确率优先".into(),
                        "速度与资源优先".into(),
                    ],
                    values
                        .iter()
                        .position(|v| *v == opts.profile.as_deref().unwrap_or(&cfg.profile))
                        .unwrap_or(0),
                    true,
                )? {
                    opts.profile = Some(values[i].into());
                } else {
                    back = true;
                }
            }
            3 => {
                let listing = catalog::listing(
                    &hw,
                    cfg,
                    opts.device.as_deref().unwrap_or(&cfg.device),
                    opts.language.as_deref().unwrap_or(&cfg.language),
                    opts.profile.as_deref().unwrap_or(&cfg.profile),
                )?;
                let visible: Vec<_> = listing
                    .iter()
                    .filter(|v| show_all || v["status"] != "incompatible")
                    .collect();
                let labels: Vec<String> = visible
                    .iter()
                    .enumerate()
                    .map(|(i, v)| {
                        let m = &v["model"];
                        format!(
                            "{}{} · {} · {}种语言 · {:.2} GiB · RAM {} GiB / VRAM {} GiB（估计） · {} · {}{}",
                            if i == 0 && v["status"] != "incompatible" {
                                "推荐："
                            } else {
                                ""
                            },
                            m["name"].as_str().unwrap(),
                            m["backend"].as_str().unwrap(),
                            v["supported_languages"].as_array().unwrap().len(),
                            m["files"]
                                .as_array()
                                .unwrap()
                                .iter()
                                .map(|f| f[1].as_u64().unwrap())
                                .sum::<u64>() as f64
                                / 1024f64.powi(3),
                            m["ram_gib"],
                            m["vram_gib"],
                            v["device"].as_str().unwrap(),
                            v["reasons"]
                                .as_array()
                                .unwrap()
                                .iter()
                                .map(|s| s.as_str().unwrap())
                                .collect::<Vec<_>>()
                                .join("；"),
                            if v["installed"] == true {
                                " · 已安装"
                            } else {
                                ""
                            }
                        )
                    })
                    .chain([
                        "使用已有本地模型目录".into(),
                        "调整设备或数据目录后重新筛选".into(),
                        if show_all {
                            "仅显示兼容模型".into()
                        } else {
                            "显示完整模型目录".into()
                        },
                    ])
                    .collect();
                let default = visible
                    .iter()
                    .position(|v| {
                        v["model"]["id"].as_str()
                            == opts.model.as_deref().or(cfg.default_model.as_deref())
                    })
                    .unwrap_or(0);
                match choose("模型选择（推荐为默认，可更换）", &labels, default, true)?
                {
                    None => back = true,
                    Some(i) if i == visible.len() => {
                        let path = PathBuf::from(input(
                            "本地模型目录",
                            opts.local_model
                                .as_ref()
                                .map(|p| p.display().to_string())
                                .unwrap_or_default(),
                        )?);
                        match catalog::detect(&path) {
                            Ok(m) => {
                                opts.model = Some(m.id);
                                opts.local_model = Some(state::absolute(&path)?);
                            }
                            Err(e) => {
                                eprintln!("{}", e.message);
                                continue;
                            }
                        }
                    }
                    Some(i) if i == visible.len() + 1 => {
                        let mut devices = vec!["auto".into(), "cpu".into()];
                        devices.extend(hw.gpus.iter().map(|g| format!("cuda:{}", g.index)));
                        if let Some(index) = choose("重新选择运行设备", &devices, 0, true)?
                        {
                            opts.device = Some(devices[index].clone());
                            root = state::absolute(Path::new(&input(
                                "数据目录",
                                root.display().to_string(),
                            )?))?;
                            hw = state::hardware(&root);
                            show_all = false;
                        }
                        continue;
                    }
                    Some(i) if i == visible.len() + 2 => {
                        show_all = !show_all;
                        continue;
                    }
                    Some(i) => {
                        if visible[i]["status"] == "incompatible" {
                            eprintln!("此模型不兼容：{}", visible[i]["reasons"]);
                            continue;
                        }
                        opts.model = Some(visible[i]["model"]["id"].as_str().unwrap().into());
                        opts.local_model = None;
                    }
                }
            }
            4 => {
                if opts.local_model.is_none() {
                    let m = catalog::find(opts.model.as_deref().unwrap())?;
                    let mut sources = vec!["auto", "huggingface"];
                    if m.modelscope_repo.is_some() {
                        sources.push("modelscope");
                    }
                    let labels: Vec<_> = sources
                        .iter()
                        .map(|s| match *s {
                            "auto" => "自动选择可用来源".into(),
                            "huggingface" => "Hugging Face".into(),
                            _ => "ModelScope".into(),
                        })
                        .collect();
                    if let Some(i) = choose(
                        "模型下载源",
                        &labels,
                        sources
                            .iter()
                            .position(|v| Some(*v) == opts.source.as_deref())
                            .unwrap_or(0),
                        true,
                    )? {
                        opts.source = Some(sources[i].into());
                    } else {
                        back = true;
                    }
                }
            }
            5 => {
                if advanced {
                    let mut devices = vec!["auto".into(), "cpu".into()];
                    devices.extend(hw.gpus.iter().map(|g| format!("cuda:{}", g.index)));
                    if let Some(i) = choose(
                        "运行设备",
                        &devices,
                        devices
                            .iter()
                            .position(|v| Some(v.as_str()) == opts.device.as_deref())
                            .unwrap_or(0),
                        true,
                    )? {
                        opts.device = Some(devices[i].clone());
                        let path = input("数据目录", root.display().to_string())?;
                        root = state::absolute(Path::new(&path))?;
                        opts.batch_size = Some(input(
                            "批大小：auto 或 1–32",
                            opts.batch_size.clone().unwrap_or_else(|| {
                                if cfg.batch_size == 0 {
                                    "auto".into()
                                } else {
                                    cfg.batch_size.to_string()
                                }
                            }),
                        )?);
                        let segment = input(
                            "目标分段秒数：5–1200",
                            opts.segment_seconds
                                .unwrap_or(cfg.segment_seconds)
                                .to_string(),
                        )?;
                        match segment.parse::<u32>() {
                            Ok(n) if (5..=1200).contains(&n) => opts.segment_seconds = Some(n),
                            _ => {
                                eprintln!("分段长度无效。");
                                continue;
                            }
                        }
                        hw = state::hardware(&root);
                        if let Err(e) = setup::settings(&root, cfg, &hw, &opts) {
                            eprintln!("{}", e.message);
                            continue;
                        }
                    } else {
                        back = true;
                    }
                }
            }
            6 => {
                let s = match setup::settings(&root, cfg, &hw, &opts) {
                    Ok(s) => s,
                    Err(e) => {
                        eprintln!("{}", e.message);
                        step = 3;
                        continue;
                    }
                };
                let p = match setup::plan(&s) {
                    Ok(p) => p,
                    Err(e) => {
                        eprintln!("{}", e.message);
                        step = 3;
                        continue;
                    }
                };
                eprintln!(
                    "\n准备计划\n模型：{}\n设备：{}\n语言：{}\n模型下载：{:.2} GiB\n运行时下载估计：{:.2} GiB\n目录：{}\n将设置默认模型：{}",
                    s.model,
                    s.device,
                    s.language,
                    p["model_download_bytes"].as_u64().unwrap() as f64 / 1024f64.powi(3),
                    p["runtime_download_estimate_bytes"].as_u64().unwrap() as f64 / 1024f64.powi(3),
                    s.root.display(),
                    s.model
                );
                match choose(
                    "确认以上选择",
                    &[
                        "开始准备并自检".into(),
                        "返回模型选择".into(),
                        "取消".into(),
                    ],
                    0,
                    true,
                )? {
                    Some(0) => return Ok(s),
                    Some(1) => {
                        step = 3;
                        continue;
                    }
                    Some(2) => return Err(ui_error("用户取消")),
                    _ => back = true,
                }
            }
            _ => unreachable!(),
        }
        if back {
            step = step.saturating_sub(1);
            if step == 5 && !advanced {
                step = 4;
            }
            if step == 4 && opts.local_model.is_some() {
                step = 3;
            }
        } else {
            step += 1;
        }
    }
}
pub fn recovery(error: &AppError) -> Result<usize> {
    eprintln!("\n准备失败：{}\n{}", error.message, error.hint);
    Ok(choose(
        "下一步",
        &[
            "重新尝试（复用已完成资源）".into(),
            "返回修改设置".into(),
            "退出".into(),
        ],
        0,
        false,
    )?
    .unwrap())
}
