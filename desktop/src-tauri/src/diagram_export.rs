// The chosen native save-dialog path is the only destination. Existing files stay intact.
fn valid_export(name: &str, content: &[u8]) -> bool {
    if content.is_empty() || content.len() > 25 * 1024 * 1024 || name.len() > 240
        || name.starts_with('.') || name.trim() != name
        || name.chars().any(|c| c.is_control() || matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' | '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}')) {
        return false;
    }
    let Some((stem, ext)) = name.rsplit_once('.') else { return false; };
    if stem.trim().is_empty() { return false; }
    match ext {
        "png" => content.starts_with(b"\x89PNG\r\n\x1a\n"),
        "zip" => content.starts_with(b"PK\x03\x04"),
        "svg" => std::str::from_utf8(content).is_ok_and(|s| s.trim_start().starts_with("<svg")),
        _ => false,
    }
}

#[tauri::command]
pub async fn save_litigation_diagram(app: tauri::AppHandle, default_name: String, content: Vec<u8>) -> Result<Option<String>, String> {
    if !valid_export(&default_name, &content) {
        return Err("图表文件名、格式或大小无效，未保存。".into());
    }
    super::save_document_copy(app, default_name, content).await
}

#[cfg(test)]
mod tests {
    use super::valid_export;
    #[test]
    fn only_bounded_chart_files_can_reach_the_save_dialog() {
        assert!(valid_export("案件时间轴.svg", b"<svg xmlns=\"http://www.w3.org/2000/svg\"/>"));
        assert!(valid_export("案件关系.png", b"\x89PNG\r\n\x1a\n"));
        assert!(valid_export("案件大事记.zip", b"PK\x03\x04"));
        for name in ["../a.svg", "a.exe", ".svg", "a\u{202e}.svg", "a/b.svg", "a.svg ", "a:foo.svg"] {
            assert!(!valid_export(name, b"<svg/>"));
        }
        assert!(!valid_export("a.png", b"<svg/>"));
        assert!(!valid_export("a.svg", &vec![0; 25 * 1024 * 1024 + 1]));
    }
}
