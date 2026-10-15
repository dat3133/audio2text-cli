//! A bounded menu frame: count terminal cells, never UTF-8 bytes, when redrawing.
use console::{Key, Term, measure_text_width, strip_ansi_codes, style, truncate_str};
use std::{io, ops::Range};

pub enum Selection {
    Chosen(usize),
    Plain(usize),
}

struct Frame {
    lines: Vec<String>,
    active: Range<usize>,
}

fn wrap_label(text: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    let mut line = String::new();
    let mut cells = 0;
    for ch in strip_ansi_codes(text).chars() {
        let ch = if ch.is_control() { ' ' } else { ch };
        let mut encoded = [0; 4];
        let size = measure_text_width(ch.encode_utf8(&mut encoded));
        if cells + size > width && !line.is_empty() {
            lines.push(std::mem::take(&mut line));
            cells = 0;
        }
        line.push(ch);
        cells += size;
    }
    lines.push(line);
    lines
}

impl Frame {
    fn new(
        title: &str,
        items: &[String],
        selected: usize,
        rows: usize,
        columns: usize,
    ) -> Option<Self> {
        if rows < 16 || columns < 40 {
            return None;
        }
        // Leave room for the environment and completed steps above this menu.
        // A spare column also avoids the terminal's right-margin auto-wrap.
        let width = columns - 1;
        let budget = rows - 14;
        let wrapped: Vec<_> = items.iter().map(|s| wrap_label(s, width - 2)).collect();
        let mut used = wrapped[selected].len();
        if used > budget {
            return None;
        }
        let (mut start, mut end) = (selected, selected + 1);
        while start > 0 && used + wrapped[start - 1].len() <= budget / 2 {
            start -= 1;
            used += wrapped[start].len();
        }
        while end < items.len() && used + wrapped[end].len() <= budget {
            used += wrapped[end].len();
            end += 1;
        }
        while start > 0 && used + wrapped[start - 1].len() <= budget {
            start -= 1;
            used += wrapped[start].len();
        }
        let mut lines = vec![truncate_str(&format!("? {title}"), width, "…").into_owned()];
        let mut active = 0..0;
        for (index, item) in wrapped.iter().enumerate().take(end).skip(start) {
            let first = lines.len();
            for (part, text) in item.iter().enumerate() {
                let prefix = if index == selected && part == 0 {
                    "> "
                } else {
                    "  "
                };
                lines.push(format!("{prefix}{text}"));
            }
            if index == selected {
                active = first..lines.len();
            }
        }
        lines.push(
            truncate_str(
                &format!("↑/↓ 选择 · Enter 确认 · {}/{}", selected + 1, items.len()),
                width,
                "…",
            )
            .into_owned(),
        );
        Some(Self { lines, active })
    }

    fn clear_rows(&self, rows: usize, columns: usize) -> usize {
        // A resize can reflow a previous frame before the next key arrives.
        self.lines
            .iter()
            .map(|line| measure_text_width(line).max(1).div_ceil(columns.max(1)))
            .sum::<usize>()
            .min(rows.saturating_sub(1))
    }
}

struct CursorGuard<'a>(&'a Term);
impl Drop for CursorGuard<'_> {
    fn drop(&mut self) {
        let _ = self.0.show_cursor();
        let _ = self.0.flush();
    }
}

pub fn select(term: &Term, title: &str, items: &[String], default: usize) -> io::Result<Selection> {
    let mut selected = default.min(items.len() - 1);
    term.hide_cursor()?;
    let _cursor = CursorGuard(term);
    loop {
        let (rows, columns) = term.size();
        let Some(frame) = Frame::new(title, items, selected, rows.into(), columns.into()) else {
            return Ok(Selection::Plain(selected));
        };
        for (index, line) in frame.lines.iter().enumerate() {
            if frame.active.contains(&index) {
                term.write_line(&style(line).for_stderr().cyan().to_string())?;
            } else {
                term.write_line(line)?;
            }
        }
        term.flush()?;
        let key = term.read_key_raw()?;
        let (rows, columns) = term.size();
        term.clear_last_lines(frame.clear_rows(rows.into(), columns.into()))?;
        match key {
            Key::CtrlC | Key::Char('\x03') => {
                return Err(io::Error::new(io::ErrorKind::Interrupted, "用户取消"));
            }
            Key::Enter | Key::Char(' ') => {
                // Keep the completed-step summary short; full details were in the menu.
                let label = items[selected]
                    .split(" · ")
                    .next()
                    .unwrap_or(&items[selected]);
                term.write_line(&format!("✓ {title} · {label}"))?;
                return Ok(Selection::Chosen(selected));
            }
            Key::ArrowDown | Key::Tab | Key::Char('j') => selected = (selected + 1) % items.len(),
            Key::ArrowUp | Key::BackTab | Key::Char('k') => {
                selected = (selected + items.len() - 1) % items.len()
            }
            Key::Home => selected = 0,
            Key::End => selected = items.len() - 1,
            Key::PageUp | Key::ArrowLeft | Key::Char('h') => selected = selected.saturating_sub(5),
            Key::PageDown | Key::ArrowRight | Key::Char('l') => {
                selected = (selected + 5).min(items.len() - 1)
            }
            _ => (),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn model_items() -> Vec<String> {
        (0..12).map(|index| format!(
            "模型 {index} · faster-whisper · 100种语言 · 1.51 GiB · RAM 6 GiB / VRAM 4 GiB（估计） · cuda:0 · 当前可用内存低于保守估计；当前空闲显存低于保守估计"
        )).collect()
    }

    #[test]
    fn chinese_utf8_bytes_do_not_inflate_clear_height() {
        let items = vec!["中文选项".repeat(8), "返回上一步".into()];
        assert!(items[0].len() > 80);
        let frame = Frame::new("模型选择", &items, 0, 40, 80).unwrap();
        assert_eq!(frame.clear_rows(40, 80), frame.lines.len());
        assert!(frame.lines.iter().all(|line| measure_text_width(line) < 80));
    }

    #[test]
    fn long_model_details_remain_complete_and_every_item_is_reachable() {
        let items = model_items();
        for (rows, columns) in [(24, 80), (32, 120), (40, 160)] {
            for selected in 0..items.len() {
                let frame = Frame::new(
                    "模型选择（推荐为默认，可更换）",
                    &items,
                    selected,
                    rows,
                    columns,
                )
                .unwrap();
                assert!(frame.lines.len() <= rows - 12);
                assert!(
                    frame
                        .lines
                        .iter()
                        .all(|line| measure_text_width(line) < columns)
                );
                let shown = frame.lines[frame.active.clone()]
                    .iter()
                    .map(|line| &line[2..])
                    .collect::<String>();
                assert_eq!(shown, items[selected]);
            }
        }
    }

    #[test]
    fn resize_recounts_physical_rows_without_clearing_past_the_viewport() {
        let frame = Frame::new("模型选择", &model_items(), 4, 32, 120).unwrap();
        let reflowed = frame
            .lines
            .iter()
            .map(|line| measure_text_width(line).max(1).div_ceil(60))
            .sum::<usize>();
        assert_eq!(frame.clear_rows(50, 60), reflowed);
        assert_eq!(frame.clear_rows(6, 20), 5);
        assert!(Frame::new("模型选择", &model_items(), 0, 10, 80).is_none());
        assert!(Frame::new("模型选择", &model_items(), 0, 24, 30).is_none());
    }

    #[test]
    fn label_controls_cannot_add_untracked_terminal_rows() {
        let lines = wrap_label("中文\n换行\t内容\x1b[31m红色\x1b[0m", 12);
        assert!(
            lines
                .iter()
                .all(|line| measure_text_width(line) <= 12 && !line.chars().any(char::is_control))
        );
        assert_eq!(lines.concat(), "中文 换行 内容红色");
    }
}
