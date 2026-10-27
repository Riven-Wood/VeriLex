use sha2::{Digest, Sha256};

fn main() {
    println!("cargo:rerun-if-env-changed=VERILEX_DOCUMENT_MANIFEST_SHA256");
    println!("cargo:rerun-if-env-changed=TAURI_CONFIG");
    let supplied_hash = std::env::var("VERILEX_DOCUMENT_MANIFEST_SHA256").ok();
    let overlay_selected = std::env::var("TAURI_CONFIG")
        .map(|config| config.contains("generated/documents-macos-arm64"))
        .unwrap_or(false);
    let manifest_hash = if overlay_selected {
        let manifest = std::path::Path::new(&std::env::var("CARGO_MANIFEST_DIR").unwrap())
            .join("generated/documents-macos-arm64/component-manifest.json");
        println!("cargo:rerun-if-changed={}", manifest.display());
        let bytes = std::fs::read(&manifest)
            .unwrap_or_else(|_| panic!("Documents overlay requires {}", manifest.display()));
        let value = format!("{:x}", Sha256::digest(bytes));
        if supplied_hash
            .as_deref()
            .is_some_and(|supplied| supplied != value)
        {
            panic!("VERILEX_DOCUMENT_MANIFEST_SHA256 does not match the generated manifest");
        }
        Some(value)
    } else {
        if supplied_hash.is_some() {
            panic!("VERILEX_DOCUMENT_MANIFEST_SHA256 requires the Documents overlay");
        }
        None
    };
    if let Some(value) = manifest_hash {
        let valid = value.len() == 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte));
        if !valid {
            panic!("VERILEX_DOCUMENT_MANIFEST_SHA256 must be a lowercase SHA-256");
        }
        println!("cargo:rustc-env=VERILEX_COMPILED_DOCUMENT_MANIFEST_SHA256={value}");
    }
    tauri_build::build();
}
