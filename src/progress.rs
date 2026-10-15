//! Download status on stderr only; never move above the current terminal row.
use console::{Term, measure_text_width, truncate_str};
use std::{
    io::IsTerminal,
    time::{Duration, Instant},
};

const INTERVAL: Duration = Duration::from_millis(200);

fn due(last: Option<Instant>, now: Instant) -> bool {
    last.is_none_or(|last| now.duration_since(last) >= INTERVAL)
}

fn allowed(tty: bool, term: Option<&str>, plain: Option<&str>) -> bool {
    tty && term != Some("dumb") && plain != Some("1")
}

pub fn enabled() -> bool {
    allowed(
        std::io::stderr().is_terminal(),
        std::env::var("TERM").ok().as_deref(),
        std::env::var("AUDIO2TEXT_PLAIN").ok().as_deref(),
    )
}

/// A terminated child cannot run its own terminal cleanup.
pub fn interrupted() {
    if enabled() {
        let term = Term::stderr();
        let _ = term.write_line("");
        let _ = term.show_cursor();
        let _ = term.flush();
    }
}

fn amount(done: u64, total: Option<u64>) -> String {
    let value = total.unwrap_or(done);
    let (unit, divisor) = if value >= 1 << 30 {
        ("GiB", (1u64 << 30) as f64)
    } else if value >= 1 << 20 {
        ("MiB", (1u64 << 20) as f64)
    } else if value >= 1 << 10 {
        ("KiB", (1u64 << 10) as f64)
    } else {
        ("B", 1.0)
    };
    if let Some(total) = total {
        format!(
            "{:.1}/{:.1} {unit}",
            done as f64 / divisor,
            total as f64 / divisor
        )
    } else {
        format!("{:.1} {unit}", done as f64 / divisor)
    }
}

fn line(label: &str, done: u64, total: Option<u64>, tick: usize, columns: usize) -> String {
    let width = columns.saturating_sub(1);
    let bar_size = if width >= 76 { 20 } else { 10 };
    let suffix = if let Some(total) = total.filter(|v| *v > 0) {
        let percentage = ((u128::from(done) * 100) / u128::from(total)).min(100);
        let filled = (percentage as usize * bar_size) / 100;
        format!(
            " [{}{}] {percentage:3}% {}",
            "#".repeat(filled),
            "-".repeat(bar_size - filled),
            amount(done, Some(total))
        )
    } else {
        format!(
            " [{}] {}",
            ["|", "/", "-", "\\"][tick % 4],
            amount(done, None)
        )
    };
    let label: String = label
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect();
    let budget = width.saturating_sub(measure_text_width(&suffix));
    if budget == 0 {
        truncate_str(suffix.trim_start(), width, "").into_owned()
    } else {
        format!("{}{suffix}", truncate_str(&label, budget, "…"))
    }
}

pub struct Download {
    term: Term,
    label: String,
    total: Option<u64>,
    done: u64,
    last: Option<Instant>,
    tick: usize,
    dynamic: bool,
    closed: bool,
}

impl Download {
    pub fn new(label: &str, total: Option<u64>) -> Self {
        Self {
            term: Term::stderr(),
            label: label.into(),
            total: total.filter(|v| *v > 0),
            done: 0,
            last: None,
            tick: 0,
            dynamic: enabled(),
            closed: false,
        }
    }

    pub fn update(&mut self, done: u64) {
        self.done = done;
        let now = Instant::now();
        if self.dynamic && due(self.last, now) {
            self.draw();
            self.last = Some(now);
        }
    }

    fn draw(&mut self) {
        let columns = usize::from(self.term.size().1);
        let _ = self.term.write_str("\r");
        let _ = self.term.clear_line();
        let _ = self.term.write_str(&line(
            &self.label,
            self.done,
            self.total,
            self.tick,
            columns,
        ));
        let _ = self.term.flush();
        self.tick += 1;
    }

    pub fn finish(&mut self) {
        self.close(true);
    }

    fn close(&mut self, success: bool) {
        if self.closed {
            return;
        }
        if self.dynamic {
            self.draw();
            let _ = self.term.write_line("");
        }
        let _ = self.term.write_line(&format!(
            "{}：{}",
            self.label,
            if success {
                "下载完成，准备校验"
            } else {
                "下载未完成"
            }
        ));
        let _ = self.term.flush();
        self.closed = true;
    }
}

impl Drop for Download {
    fn drop(&mut self) {
        self.close(false);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminal_modes_disable_dynamic_output_when_requested() {
        assert!(allowed(true, Some("xterm-256color"), None));
        assert!(!allowed(false, Some("xterm-256color"), None));
        assert!(!allowed(true, Some("dumb"), None));
        assert!(!allowed(true, None, Some("1")));
    }

    #[test]
    fn byte_updates_are_throttled_to_five_frames_per_second() {
        let start = Instant::now();
        let mut last = None;
        let mut frames = 0;
        for ms in 0..1000 {
            let now = start + Duration::from_millis(ms);
            if due(last, now) {
                frames += 1;
                last = Some(now);
            }
        }
        assert_eq!(frames, 5);
    }

    #[test]
    fn known_and_unknown_sizes_have_truthful_progress() {
        let known = line("uv", 40 << 20, Some(100 << 20), 0, 100);
        assert!(known.contains("40%"));
        assert!(known.contains("40.0/100.0 MiB"));
        let unknown = line("uv", 40 << 20, None, 1, 100);
        assert!(unknown.contains("[/]"));
        assert!(unknown.contains("40.0 MiB"));
        assert!(!unknown.contains('%'));
    }

    #[test]
    fn long_unicode_names_and_narrow_frames_never_wrap_or_inject_controls() {
        for columns in [1, 16, 40, 80, 120] {
            for total in [None, Some(100 << 20)] {
                let shown = line(
                    &"中文模型\n\x1b文件".repeat(20),
                    30 << 20,
                    total,
                    0,
                    columns,
                );
                assert!(measure_text_width(&shown) < columns);
                assert!(!shown.chars().any(char::is_control));
            }
        }
    }
}
