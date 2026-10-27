fn valid_document(name:&str, content:&[u8])->bool {
    if content.is_empty() || content.len()>20*1024*1024 || name.len()>240 || name.starts_with('.') || name.trim()!=name || name.chars().any(|c|c.is_control()||matches!(c,'/'|'\\'|':'|'*'|'?'|'"'|'<'|'>'|'|'|'\u{202a}'..='\u{202e}'|'\u{2066}'..='\u{2069}')) {return false;}
    (name.ends_with(".docx")&&content.starts_with(b"PK\x03\x04"))||(name.ends_with(".pdf")&&content.starts_with(b"%PDF-"))
}
#[tauri::command]
pub async fn save_litigation_document(app:tauri::AppHandle,default_name:String,content:Vec<u8>)->Result<Option<String>,String>{
    if !valid_document(&default_name,&content){return Err("文书文件名、格式或大小无效，未保存。".into());}
    super::save_document_copy(app,default_name,content).await
}
#[cfg(test)] mod tests {
    use super::valid_document;
    #[test] fn restrict_to_bounded_word_and_pdf(){
        assert!(valid_document("答辩状.docx",b"PK\x03\x04"));assert!(valid_document("答辩状.pdf",b"%PDF-1.7"));
        for name in ["../a.pdf","a.exe",".pdf","a\u{202e}.pdf","a.pdf "]{assert!(!valid_document(name,b"%PDF-1.7"));}
        assert!(!valid_document("a.pdf",b"PK\x03\x04"));assert!(!valid_document("a.docx",&vec![0;20*1024*1024+1]));
    }
}
