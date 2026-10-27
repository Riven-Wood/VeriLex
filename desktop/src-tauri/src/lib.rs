use std::collections::HashMap;
use std::collections::HashSet;
use std::fs::{File, OpenOptions};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use sha2::{Digest, Sha256};
use tauri::{
    ipc::Response, webview::NewWindowResponse, AppHandle, DragDropEvent, Manager, RunEvent, State,
    TitleBarStyle, Url, WebviewEvent, WebviewUrl, WebviewWindowBuilder,
};
use uuid::Uuid;

mod browser_panel;
mod case_calendar;
mod command_shortcuts;
mod desktop_preferences;
mod diagram_export;
mod drafting_export;
#[cfg(any(target_os = "macos", test))]
mod notification_identity;

struct RuntimeProcess(Mutex<Option<Child>>);

#[derive(Default)]
struct DroppedFileGrants(Mutex<HashMap<PathBuf, Instant>>);

const DROPPED_FILE_GRANT_TTL: Duration = Duration::from_secs(30);
const MAX_DROPPED_FILE_BYTES: u64 = 30 * 1024 * 1024;
const YUANDIAN_CONSOLE_URL: &str = "https://open.chineselaw.com/profile";
const DOCUMENT_RENDERER_FIXTURE_ID: &str = "safe-basic";

struct DocumentRendererSelfTestState {
    report_path: PathBuf,
    completed: AtomicBool,
    exit_code: AtomicI32,
}

fn app_context() -> tauri::Context<tauri::Wry> {
    tauri::generate_context!()
}

fn yuandian_navigation_allowed(url: &Url) -> bool {
    let Some(host) = url.host_str() else {
        return false;
    };
    url.scheme() == "https"
        && url.port_or_known_default() == Some(443)
        && url.username().is_empty()
        && url.password().is_none()
        && yuandian_vendor_host_allowed(host)
}

// 元典开放平台在 chineselaw.com，登录跳转到华宇元典统一登录 legalmind.cn；扫码登录的
// 二维码由页面内嵌的微信开放平台页提供。wry 对 iframe 加载同样触发导航回调，因此这里
// 除厂商域外还须放行该确切主机。
fn yuandian_vendor_host_allowed(host: &str) -> bool {
    const VENDOR_DOMAINS: [&str; 2] = ["chineselaw.com", "legalmind.cn"];
    const VENDOR_EMBEDDED_HOSTS: [&str; 1] = ["open.weixin.qq.com"];
    VENDOR_DOMAINS.iter().any(|domain| {
        host == *domain
            || host
                .strip_suffix(*domain)
                .is_some_and(|prefix| prefix.ends_with('.'))
    }) || VENDOR_EMBEDDED_HOSTS.contains(&host)
}

fn native_dropped_file_supported(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    if matches!(
        name,
        "README" | "LICENSE" | "Makefile" | "Dockerfile" | ".gitignore" | ".editorconfig"
    ) {
        return true;
    }
    matches!(
        path.extension()
            .and_then(|extension| extension.to_str())
            .map(str::to_ascii_lowercase)
            .as_deref(),
        Some(
            "txt"
                | "md"
                | "markdown"
                | "csv"
                | "tsv"
                | "json"
                | "yaml"
                | "yml"
                | "toml"
                | "xml"
                | "html"
                | "htm"
                | "css"
                | "sql"
                | "py"
                | "js"
                | "jsx"
                | "ts"
                | "tsx"
                | "java"
                | "kt"
                | "go"
                | "rs"
                | "c"
                | "h"
                | "cpp"
                | "hpp"
                | "cs"
                | "rb"
                | "php"
                | "swift"
                | "sh"
                | "zsh"
                | "fish"
                | "ps1"
                | "log"
                | "pdf"
                | "doc"
                | "docx"
                | "png"
                | "jpg"
                | "jpeg"
                | "webp"
        )
    )
}

fn replace_dropped_file_grants(grants: &DroppedFileGrants, paths: &[PathBuf]) {
    let now = Instant::now();
    let mut allowed = grants.0.lock().expect("dropped file grant lock poisoned");
    allowed.clear();
    for path in paths {
        let Ok(metadata) = std::fs::symlink_metadata(path) else {
            continue;
        };
        if metadata.is_file()
            && !metadata.file_type().is_symlink()
            && native_dropped_file_supported(path)
        {
            allowed.insert(path.clone(), now);
        }
    }
}

// With Tauri's `unstable` (multi-webview) feature, even the main view receives
// WebviewEvent::DragDrop rather than WindowEvent::DragDrop. Only local main-view
// drops may grant reads; remote browser views must not authorize app commands.
fn grant_dropped_files_from_webview(
    grants: &DroppedFileGrants,
    label: &str,
    event: &WebviewEvent,
) {
    if !browser_panel::trusted_caller(label) {
        return;
    }
    if let WebviewEvent::DragDrop(DragDropEvent::Drop { paths, .. }) = event {
        replace_dropped_file_grants(grants, paths);
    }
}

fn claim_dropped_file(grants: &DroppedFileGrants, path: &Path) -> bool {
    let now = Instant::now();
    let mut allowed = grants.0.lock().expect("dropped file grant lock poisoned");
    allowed.retain(|_, granted_at| now.duration_since(*granted_at) <= DROPPED_FILE_GRANT_TTL);
    allowed.remove(path).is_some()
}

fn read_dropped_file_payload(path: &Path) -> Result<Vec<u8>, &'static str> {
    if !native_dropped_file_supported(path) {
        return Err("dropped_file_unsupported");
    }
    let metadata = std::fs::symlink_metadata(path).map_err(|_| "dropped_file_unreadable")?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err("dropped_file_unreadable");
    }
    if metadata.len() > MAX_DROPPED_FILE_BYTES {
        return Err("dropped_file_too_large");
    }

    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path).map_err(|_| "dropped_file_unreadable")?;
    let opened_metadata = file.metadata().map_err(|_| "dropped_file_unreadable")?;
    if !opened_metadata.is_file() || opened_metadata.len() > MAX_DROPPED_FILE_BYTES {
        return Err("dropped_file_too_large");
    }

    let last_modified = opened_metadata
        .modified()
        .ok()
        .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|value| value.as_millis().min(u128::from(u64::MAX)) as u64)
        .unwrap_or(0);
    let mut bytes = Vec::with_capacity(opened_metadata.len() as usize);
    file.take(MAX_DROPPED_FILE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "dropped_file_unreadable")?;
    if bytes.len() as u64 > MAX_DROPPED_FILE_BYTES {
        return Err("dropped_file_too_large");
    }

    let mut payload = Vec::with_capacity(8 + bytes.len());
    payload.extend_from_slice(&last_modified.to_le_bytes());
    payload.extend_from_slice(&bytes);
    Ok(payload)
}

#[tauri::command]
async fn read_dropped_file(
    path: String,
    grants: tauri::State<'_, DroppedFileGrants>,
) -> Result<Response, String> {
    let path = PathBuf::from(path);
    if !claim_dropped_file(&grants, &path) {
        return Err("dropped_file_not_allowed".to_owned());
    }
    tauri::async_runtime::spawn_blocking(move || read_dropped_file_payload(&path))
        .await
        .map_err(|_| "dropped_file_unreadable".to_owned())?
        .map(Response::new)
        .map_err(str::to_owned)
}

#[tauri::command]
fn open_yuandian_console(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("yuandian-console") {
        window
            .show()
            .map_err(|_| "yuandian_window_unavailable".to_owned())?;
        window
            .set_focus()
            .map_err(|_| "yuandian_window_unavailable".to_owned())?;
        return Ok(());
    }

    let url = YUANDIAN_CONSOLE_URL
        .parse()
        .map_err(|_| "yuandian_window_unavailable".to_owned())?;
    let popup_app = app.clone();
    WebviewWindowBuilder::new(&app, "yuandian-console", WebviewUrl::External(url))
        .title("元典智库 · 获取 API Key")
        .inner_size(1120.0, 820.0)
        .min_inner_size(760.0, 560.0)
        .center()
        // The vendor page never receives VeriLex's Runtime initialization script.
        // Keep navigation inside the vendor's HTTPS boundary (open.chineselaw.com,
        // its legalmind.cn SSO login, and the embedded WeChat QR page) and deny
        // pop-up windows.
        .on_navigation(yuandian_navigation_allowed)
        .on_new_window(move |url, _| {
            if yuandian_navigation_allowed(&url) {
                if let Some(window) = popup_app.get_webview_window("yuandian-console") {
                    let _ = window.navigate(url);
                }
            }
            NewWindowResponse::Deny
        })
        .build()
        .map_err(|_| "yuandian_window_unavailable".to_owned())?;
    Ok(())
}

const LAW_DATABASE_URL: &str = "https://flk.npc.gov.cn/index";

// 国家法律法规数据库的页面与文件直链都在 npc.gov.cn 系域名内；官方 docx 走
// flkoss CDN。外链一律拒绝。
fn law_database_host_allowed(host: &str) -> bool {
    host == "flk.npc.gov.cn" || host.ends_with(".npc.gov.cn") || host == "flkoss.obs-bj2.cucloud.cn"
}

fn law_database_navigation_allowed(url: &Url) -> bool {
    let Some(host) = url.host_str() else {
        return false;
    };
    url.scheme() == "https"
        && url.port_or_known_default() == Some(443)
        && url.username().is_empty()
        && url.password().is_none()
        && law_database_host_allowed(host)
}

#[tauri::command]
fn open_law_database(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("law-database") {
        window
            .show()
            .map_err(|_| "law_database_window_unavailable".to_owned())?;
        window
            .set_focus()
            .map_err(|_| "law_database_window_unavailable".to_owned())?;
        return Ok(());
    }

    let url = LAW_DATABASE_URL
        .parse()
        .map_err(|_| "law_database_window_unavailable".to_owned())?;
    let popup_app = app.clone();
    WebviewWindowBuilder::new(&app, "law-database", WebviewUrl::External(url))
        .title("国家法律法规数据库")
        .inner_size(1200.0, 860.0)
        .min_inner_size(760.0, 560.0)
        .center()
        // Same discipline as the yuandian console window: the official page
        // never receives VeriLex's Runtime initialization script, navigation
        // stays inside the official HTTPS boundary, and pop-ups are denied.
        .on_navigation(law_database_navigation_allowed)
        .on_new_window(move |url, _| {
            if law_database_navigation_allowed(&url) {
                if let Some(window) = popup_app.get_webview_window("law-database") {
                    let _ = window.navigate(url);
                }
            }
            NewWindowResponse::Deny
        })
        .build()
        .map_err(|_| "law_database_window_unavailable".to_owned())?;
    Ok(())
}

fn case_database_url(database: &str) -> Result<&'static str, String> {
    match database {
        "verilex-feedback" => Ok("https://github.com/Riven-Wood/VeriLex/issues"),
        "library" => Ok("https://rmfyalk.court.gov.cn/"),
        "judgments" => Ok("https://wenshu.court.gov.cn/"),
        "templates" => Ok("https://www.court.gov.cn/susong.html"),
        "ima-setup" => Ok("https://ima.qq.com/agent-interface"),
        "ima" => Ok("https://ima.qq.com/"),
        "plugin-yuandian" => Ok("https://github.com/yuandian-ailaw/yuandian-mcp-server"),
        "plugin-wkinfo" => Ok("https://mcp.wkinfo.com.cn/docs"),
        "plugin-pkulaw" => Ok("https://mcp.pkulaw.com/"),
        "plugin-qcc" => Ok("https://agent.qcc.com/guide"),
        "plugin-tianyancha" => Ok("https://github.com/tyc-tech/tyc-cli"),
        "plugin-tavily" => Ok("https://docs.tavily.com/documentation/mcp"),
        "plugin-firecrawl" => Ok("https://github.com/firecrawl/firecrawl-mcp-server"),
        "plugin-excel-local" => Ok("https://github.com/haris-musa/excel-mcp-server"),
        "plugin-officecli" => Ok("https://github.com/iOfficeAI/OfficeCLI"),
        "plugin-markitdown" => {
            Ok("https://github.com/microsoft/markitdown/tree/main/packages/markitdown-mcp")
        }
        "plugin-pdf-local" => Ok("https://github.com/PurpleDirective/quillpdf-mcp"),
        "plugin-filesystem" => {
            Ok("https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem")
        }
        "plugin-ima" => Ok("https://ima.qq.com/agent-interface"),
        "plugin-github" => Ok("https://github.com/github/github-mcp-server"),
        "plugin-playwright" => Ok("https://github.com/microsoft/playwright-mcp"),
        "plugin-key-yuandian" => Ok("https://open.chineselaw.com/"),
        "plugin-key-wkinfo" => Ok("https://mcp.wkinfo.com.cn/"),
        "plugin-key-pkulaw" => Ok("https://mcp.pkulaw.com/"),
        "plugin-key-qcc" => Ok("https://agent.qcc.com/guide"),
        "plugin-key-tianyancha" => Ok("https://www.tianyancha.com/ai/profile"),
        "plugin-key-tavily" => Ok("https://app.tavily.com/"),
        "plugin-key-firecrawl" => Ok("https://www.firecrawl.dev/app/api-keys"),
        "plugin-key-ima" => Ok("https://ima.qq.com/agent-interface"),
        "plugin-key-github" => Ok("https://github.com/settings/personal-access-tokens/new"),
        "model-console-deepseek" => Ok("https://platform.deepseek.com/api_keys"),
        "model-docs-deepseek" => Ok("https://api-docs.deepseek.com/zh-cn/"),
        "model-console-qwen" => Ok("https://bailian.console.aliyun.com/"),
        "model-docs-qwen" => Ok("https://help.aliyun.com/zh/model-studio/get-api-key/"),
        "model-console-glm" => Ok("https://bigmodel.cn/usercenter/proj-mgmt/apikeys"),
        "model-docs-glm" => Ok("https://docs.bigmodel.cn/cn/guide/develop/openai/introduction"),
        "model-console-kimi" => Ok("https://platform.moonshot.cn/console/api-keys"),
        "model-docs-kimi" => Ok("https://platform.kimi.com/docs/guide/start-using-kimi-api"),
        "model-console-doubao" => Ok("https://ark.volcengine.com/region:cn-beijing/apikey"),
        "model-docs-doubao" => Ok("https://docs.volcengine.com/docs/ark/quick-start"),
        "model-console-minimax" => Ok("https://platform.minimax.cn/"),
        "model-docs-minimax" => {
            Ok("https://platform.minimax.cn/docs/api-reference/text-openai-api")
        }
        _ => Err("case_database_invalid".to_owned()),
    }
}

#[tauri::command]
async fn open_case_database(database: String) -> Result<(), String> {
    let url = case_database_url(&database)?;
    tauri::async_runtime::spawn_blocking(move || {
        #[cfg(target_os = "macos")]
        let mut command = Command::new("/usr/bin/open");
        #[cfg(target_os = "windows")]
        let mut command = {
            let mut command = Command::new("rundll32.exe");
            command.arg("url.dll,FileProtocolHandler");
            command
        };
        #[cfg(not(any(target_os = "macos", target_os = "windows")))]
        let mut command = Command::new("xdg-open");
        let status = command
            .arg(url)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|_| "case_database_open_failed".to_owned())?;
        if status.success() {
            Ok(())
        } else {
            Err("case_database_open_failed".to_owned())
        }
    })
    .await
    .map_err(|_| "case_database_open_failed".to_owned())?
}

struct RuntimeLaunch {
    child: Child,
    port: u16,
}

fn compiled_document_manifest_sha256() -> Option<&'static str> {
    option_env!("VERILEX_COMPILED_DOCUMENT_MANIFEST_SHA256").filter(|value| {
        value.len() == 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

fn valid_bundle_relative_path(value: &str) -> bool {
    !value.is_empty()
        && !value.contains('\\')
        && !Path::new(value).is_absolute()
        && Path::new(value)
            .components()
            .all(|part| matches!(part, std::path::Component::Normal(_)))
}

fn version_parts(value: &str) -> Option<Vec<u32>> {
    let parts: Option<Vec<u32>> = value.split('.').map(|part| part.parse().ok()).collect();
    parts.filter(|parts| !parts.is_empty())
}

const DOCUMENT_SYSTEM_READ_ALLOWLIST: [&str; 10] = [
    "/System/Library",
    "/System/Volumes/Preboot/Cryptexes",
    "/usr/lib",
    "/usr/bin",
    "/usr/share",
    "/bin",
    "/dev",
    "/private/etc",
    "/private/var/db/timezone",
    "/Library/Fonts",
];

fn verify_document_bundle(
    root: &Path,
    compiled_manifest_sha256: Option<&str>,
    expected_platform: &str,
    expected_arch: &str,
    current_macos: &str,
) -> Result<(), &'static str> {
    let compiled_hash = compiled_manifest_sha256.ok_or("manifest_trust_root_unavailable")?;
    let manifest_path = root.join("component-manifest.json");
    let manifest_bytes = std::fs::read(&manifest_path).map_err(|_| "manifest_missing")?;
    let manifest_hash = format!("{:x}", Sha256::digest(&manifest_bytes));
    if manifest_hash != compiled_hash {
        return Err("manifest_hash_mismatch");
    }
    let manifest: serde_json::Value =
        serde_json::from_slice(&manifest_bytes).map_err(|_| "manifest_invalid")?;
    if serde_json::to_vec(&manifest).map_err(|_| "manifest_invalid")? != manifest_bytes {
        return Err("manifest_not_canonical");
    }
    let object = manifest.as_object().ok_or("manifest_invalid")?;
    let expected_keys: HashSet<&str> = [
        "schema_version",
        "selected_engine",
        "system_read_allowlist",
        "components",
    ]
    .into_iter()
    .collect();
    if object.keys().map(String::as_str).collect::<HashSet<_>>() != expected_keys
        || object
            .get("schema_version")
            .and_then(|value| value.as_u64())
            != Some(1)
        || !matches!(
            object
                .get("selected_engine")
                .and_then(|value| value.as_str()),
            Some("docxodus" | "adeu_stack")
        )
    {
        return Err("manifest_invalid");
    }
    let allowlist = object
        .get("system_read_allowlist")
        .and_then(|value| value.as_array())
        .filter(|items| !items.is_empty())
        .ok_or("system_read_allowlist_invalid")?;
    let actual_allowlist: Vec<&str> = allowlist
        .iter()
        .filter_map(|value| value.as_str())
        .collect();
    if actual_allowlist != DOCUMENT_SYSTEM_READ_ALLOWLIST {
        return Err("system_read_allowlist_invalid");
    }
    let current_version = version_parts(current_macos).ok_or("current_macos_invalid")?;
    let components = object
        .get("components")
        .and_then(|value| value.as_array())
        .filter(|items| !items.is_empty())
        .ok_or("manifest_invalid")?;
    let mut ids = HashSet::new();
    let mut paths = HashSet::from(["component-manifest.json".to_owned()]);
    let mut component_paths = HashSet::new();
    let mut referenced_license_paths = HashSet::new();
    for raw in components {
        let item = raw.as_object().ok_or("component_record_invalid")?;
        let expected_component_keys: HashSet<&str> = [
            "id",
            "version",
            "platform",
            "arch",
            "relative_path",
            "size_bytes",
            "sha256",
            "license_paths",
            "minimum_macos",
            "role",
        ]
        .into_iter()
        .collect();
        if item.keys().map(String::as_str).collect::<HashSet<_>>() != expected_component_keys {
            return Err("component_record_invalid");
        }
        let id = item
            .get("id")
            .and_then(|value| value.as_str())
            .ok_or("component_id_invalid")?;
        if id.is_empty() || !ids.insert(id) {
            return Err("component_id_invalid");
        }
        if item
            .get("version")
            .and_then(|value| value.as_str())
            .filter(|value| !value.trim().is_empty())
            .is_none()
        {
            return Err("component_version_invalid");
        }
        if item
            .get("role")
            .and_then(|value| value.as_str())
            .filter(|value| !value.trim().is_empty())
            .is_none()
        {
            return Err("component_role_invalid");
        }
        if item.get("platform").and_then(|value| value.as_str()) != Some(expected_platform)
            || item.get("arch").and_then(|value| value.as_str()) != Some(expected_arch)
        {
            return Err("component_platform_mismatch");
        }
        let minimum = item
            .get("minimum_macos")
            .and_then(|value| value.as_str())
            .and_then(version_parts)
            .ok_or("component_minimum_macos_invalid")?;
        if minimum > current_version {
            return Err("component_minimum_macos_mismatch");
        }
        let relative = item
            .get("relative_path")
            .and_then(|value| value.as_str())
            .filter(|value| valid_bundle_relative_path(value))
            .ok_or("component_path_invalid")?;
        if !paths.insert(relative.to_owned()) {
            return Err("component_path_invalid");
        }
        component_paths.insert(relative.to_owned());
        let path = root.join(relative);
        let metadata = std::fs::symlink_metadata(&path).map_err(|_| "component_path_invalid")?;
        if !metadata.is_file() || metadata.file_type().is_symlink() {
            return Err("component_path_invalid");
        }
        let size = item
            .get("size_bytes")
            .and_then(|value| value.as_u64())
            .ok_or("component_size_mismatch")?;
        if metadata.len() != size {
            return Err("component_size_mismatch");
        }
        let expected_hash = item
            .get("sha256")
            .and_then(|value| value.as_str())
            .filter(|value| {
                value.len() == 64
                    && value
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            })
            .ok_or("component_hash_invalid")?;
        let component_bytes = std::fs::read(path).map_err(|_| "component_path_invalid")?;
        if format!("{:x}", Sha256::digest(component_bytes)) != expected_hash {
            return Err("component_hash_mismatch");
        }
        let licenses = item
            .get("license_paths")
            .and_then(|value| value.as_array())
            .filter(|items| !items.is_empty())
            .ok_or("component_license_missing")?;
        for license in licenses {
            let relative = license
                .as_str()
                .filter(|value| valid_bundle_relative_path(value))
                .ok_or("component_license_missing")?;
            let path = root.join(relative);
            let metadata =
                std::fs::symlink_metadata(path).map_err(|_| "component_license_missing")?;
            if !metadata.is_file() || metadata.file_type().is_symlink() {
                return Err("component_license_missing");
            }
            referenced_license_paths.insert(relative.to_owned());
        }
    }
    if !referenced_license_paths.is_subset(&component_paths) {
        return Err("component_license_unhashed");
    }

    fn visit(root: &Path, directory: &Path, files: &mut HashSet<String>) -> Result<(), ()> {
        for entry in std::fs::read_dir(directory).map_err(|_| ())? {
            let entry = entry.map_err(|_| ())?;
            let file_type = entry.file_type().map_err(|_| ())?;
            if file_type.is_symlink() {
                return Err(());
            }
            if file_type.is_dir() {
                visit(root, &entry.path(), files)?;
            } else if file_type.is_file() {
                files.insert(
                    entry
                        .path()
                        .strip_prefix(root)
                        .map_err(|_| ())?
                        .to_string_lossy()
                        .replace('\\', "/"),
                );
            }
        }
        Ok(())
    }
    let mut actual_files = HashSet::new();
    visit(root, root, &mut actual_files).map_err(|_| "component_inventory_mismatch")?;
    if actual_files != paths {
        return Err("component_inventory_mismatch");
    }
    Ok(())
}

fn packaged_document_bundle_dir() -> Result<Option<PathBuf>, &'static str> {
    let Some(compiled_hash) = compiled_document_manifest_sha256() else {
        return Ok(None);
    };
    if !cfg!(target_os = "macos") || !cfg!(target_arch = "aarch64") {
        return Err("document_bundle_platform_mismatch");
    }
    let executable = std::env::current_exe().map_err(|_| "document_bundle_path_unavailable")?;
    let macos_dir = executable
        .parent()
        .ok_or("document_bundle_path_unavailable")?;
    let contents_dir = macos_dir
        .parent()
        .ok_or("document_bundle_path_unavailable")?;
    let bundle_dir = contents_dir.join("Resources/sidecar/documents");
    let version = Command::new("/usr/bin/sw_vers")
        .args(["-productVersion"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .map_err(|_| "current_macos_unavailable")?;
    if !version.status.success() {
        return Err("current_macos_unavailable");
    }
    let current_macos =
        String::from_utf8(version.stdout).map_err(|_| "current_macos_unavailable")?;
    verify_document_bundle(
        &bundle_dir,
        Some(compiled_hash),
        "macos",
        "arm64",
        current_macos.trim(),
    )?;
    Ok(Some(bundle_dir))
}

fn document_renderer_self_test_request() -> Result<Option<PathBuf>, &'static str> {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    let Some(position) = arguments
        .iter()
        .position(|value| value == "--documents-renderer-self-test")
    else {
        return Ok(None);
    };
    if position != 0 || arguments.len() != 3 {
        return Err("renderer_self_test_arguments_invalid");
    }
    if arguments[1] != DOCUMENT_RENDERER_FIXTURE_ID {
        return Err("renderer_self_test_fixture_invalid");
    }
    let report = PathBuf::from(&arguments[2]);
    if !report.is_absolute()
        || report.extension().and_then(|value| value.to_str()) != Some("json")
        || report.parent().filter(|value| value.is_dir()).is_none()
    {
        return Err("renderer_self_test_report_invalid");
    }
    Ok(Some(report))
}

fn write_renderer_self_test_report(path: &Path, report: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "report_parent_missing"))?;
    let temporary = parent.join(format!(
        ".verilex-renderer-smoke-{}.tmp",
        Uuid::new_v4().simple()
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        use std::io::Write;
        file.write_all(report)?;
        file.sync_all()?;
        std::fs::rename(&temporary, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}

#[tauri::command]
fn document_renderer_self_test_complete(
    app: AppHandle,
    state: State<'_, DocumentRendererSelfTestState>,
    result: serde_json::Value,
) -> Result<(), &'static str> {
    if state.completed.swap(true, Ordering::SeqCst) {
        return Err("renderer_self_test_already_completed");
    }
    let object = result
        .as_object()
        .filter(|value| value.len() == 5)
        .ok_or("renderer_self_test_result_invalid")?;
    let fixture = object.get("fixture_id").and_then(|value| value.as_str());
    let ready = object.get("ready").and_then(|value| value.as_bool());
    let violations = object
        .get("csp_violations")
        .and_then(|value| value.as_u64());
    let directive = object
        .get("csp_directive")
        .and_then(|value| value.as_str())
        .filter(|value| {
            matches!(
                *value,
                "none"
                    | "script-src"
                    | "connect-src"
                    | "worker-src"
                    | "style-src"
                    | "default-src"
                    | "other"
            )
        });
    let error_code = object
        .get("error_code")
        .and_then(|value| value.as_str())
        .filter(|value| {
            matches!(
                *value,
                "none"
                    | "renderer_unavailable"
                    | "fixture_unavailable"
                    | "renderer_conversion_failed"
                    | "renderer_output_invalid"
                    | "renderer_stylesheet_failed"
                    | "renderer_layout_invalid"
                    | "self_test_failed"
            )
        });
    if fixture != Some(DOCUMENT_RENDERER_FIXTURE_ID)
        || ready.is_none()
        || violations.filter(|value| *value <= 32).is_none()
        || directive.is_none()
        || error_code.is_none()
    {
        app.exit(2);
        return Err("renderer_self_test_result_invalid");
    }
    let report = format!(
        "{{\"fixture_id\":\"safe-basic\",\"ready\":{},\"csp_violations\":{}}}",
        if ready == Some(true) { "true" } else { "false" },
        violations.unwrap_or(33)
    );
    if write_renderer_self_test_report(&state.report_path, report.as_bytes()).is_err() {
        app.exit(2);
        return Err("renderer_self_test_report_write_failed");
    }
    let exit_code = if ready == Some(true) && violations == Some(0) {
        0
    } else {
        2
    };
    state.exit_code.store(exit_code, Ordering::SeqCst);
    app.exit(exit_code);
    Ok(())
}

fn run_document_renderer_self_test(report_path: PathBuf) -> Result<(), &'static str> {
    packaged_document_bundle_dir()?.ok_or("renderer_self_test_bundle_unavailable")?;
    let app = tauri::Builder::default()
        .manage(DocumentRendererSelfTestState {
            report_path,
            completed: AtomicBool::new(false),
            exit_code: AtomicI32::new(2),
        })
        .invoke_handler(tauri::generate_handler![
            document_renderer_self_test_complete
        ])
        .setup(|app| {
            WebviewWindowBuilder::new(
                app,
                "documents-renderer-self-test",
                WebviewUrl::App("documents-renderer/renderer-self-test.html".into()),
            )
            .title("VeriLex Documents renderer self-test")
            .visible(false)
            .build()?;
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_secs(25));
                let completed = handle
                    .state::<DocumentRendererSelfTestState>()
                    .completed
                    .swap(true, Ordering::SeqCst);
                if !completed {
                    handle.exit(2);
                }
            });
            Ok(())
        })
        .build(app_context())
        .map_err(|_| "renderer_self_test_app_build_failed")?;
    let handle = app.handle().clone();
    app.run_return(|_, _| {});
    if handle
        .state::<DocumentRendererSelfTestState>()
        .exit_code
        .load(Ordering::SeqCst)
        == 0
    {
        Ok(())
    } else {
        Err("renderer_self_test_failed")
    }
}

fn launch_token() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

fn initialization_script(endpoint: &str, token: &str) -> String {
    let endpoint = serde_json::to_string(endpoint).expect("loopback endpoint is JSON serializable");
    let token = serde_json::to_string(token).expect("launch token is JSON serializable");
    format!(
        "Object.defineProperties(globalThis,{{__VERILEX_HTTP__:{{value:{endpoint},writable:false,configurable:false}},__VERILEX_API_TOKEN__:{{value:{token},writable:false,configurable:false}}}});"
    )
}

fn server_bin() -> PathBuf {
    if let Ok(path) = std::env::var("VERILEX_SERVER_BIN") {
        if !path.trim().is_empty() {
            return PathBuf::from(path);
        }
    }

    let executable = if cfg!(windows) {
        "verilex-runtime.exe"
    } else {
        "verilex-runtime"
    };
    if let Ok(current_executable) = std::env::current_exe() {
        if let Some(directory) = current_executable.parent() {
            let mut candidates = vec![directory.join("sidecar").join(executable)];
            if let Some(contents) = directory.parent() {
                candidates.push(contents.join("Resources").join("sidecar").join(executable));
            }
            if let Some(path) = candidates.into_iter().find(|candidate| candidate.exists()) {
                return path;
            }
        }
    }

    let mut development = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    if cfg!(windows) {
        development.push("../../runtime/.venv/Scripts/verilex-runtime.exe");
    } else {
        development.push("../../runtime/.venv/bin/verilex-runtime");
    }
    development
}

fn state_dir() -> PathBuf {
    if let Ok(path) = std::env::var("VERILEX_STATE_DIR") {
        if !path.trim().is_empty() {
            return PathBuf::from(path);
        }
    }

    #[cfg(windows)]
    if let Ok(path) = std::env::var("APPDATA") {
        return PathBuf::from(path).join("VeriLex");
    }

    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_owned());
    PathBuf::from(home).join(".config").join("verilex")
}

#[tauri::command]
fn open_app_data_directory() -> Result<(), String> {
    let directory = state_dir();
    if !directory.is_dir() {
        return Err("应用数据文件夹尚未就绪".into());
    }
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("/usr/bin/open")
        .arg(&directory)
        .status();
    #[cfg(windows)]
    let result = std::process::Command::new("explorer.exe")
        .arg(&directory)
        .status();
    #[cfg(target_os = "linux")]
    let result = std::process::Command::new("xdg-open")
        .arg(&directory)
        .status();
    result
        .map_err(|_| "无法打开应用数据文件夹".to_string())?
        .success()
        .then_some(())
        .ok_or_else(|| "无法打开应用数据文件夹".to_string())
}

fn reject_linked_or_non_directory(path: &Path) -> std::io::Result<()> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "private directory must not be a symbolic link",
        )),
        Ok(metadata) if !metadata.is_dir() => Err(std::io::Error::new(
            std::io::ErrorKind::AlreadyExists,
            "private directory path is not a directory",
        )),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn create_private_directory(path: &Path) -> std::io::Result<()> {
    reject_linked_or_non_directory(path)?;
    std::fs::create_dir_all(path)?;
    reject_linked_or_non_directory(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn runtime_log_file(root: &Path) -> Option<File> {
    let logs = root.join("logs");
    create_private_directory(&logs).ok()?;
    let mut options = OpenOptions::new();
    options.create(true).truncate(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(logs.join("verilex-runtime.log")).ok()?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(std::fs::Permissions::from_mode(0o600))
            .ok()?;
    }
    Some(file)
}

fn configure_runtime_process(command: &mut Command) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
}

#[cfg(any(windows, test))]
fn windows_taskkill_args(pid: u32) -> [String; 4] {
    [
        "/PID".to_owned(),
        pid.to_string(),
        "/T".to_owned(),
        "/F".to_owned(),
    ]
}

fn wait_for_child_exit(child: &mut Child, deadline: Instant) -> bool {
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return true,
            Err(_) => return false,
            Ok(None) if Instant::now() >= deadline => return false,
            Ok(None) => std::thread::sleep(Duration::from_millis(25)),
        }
    }
}

#[cfg(unix)]
fn process_group_exists(process_group: i32) -> bool {
    (unsafe { libc::killpg(process_group, 0) }) == 0
        || io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH)
}

fn stop_runtime(child: &mut Child) {
    if matches!(child.try_wait(), Ok(Some(_))) {
        return;
    }

    #[cfg(unix)]
    {
        let process_group = child.id() as i32;
        unsafe {
            libc::killpg(process_group, libc::SIGTERM);
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if !process_group_exists(process_group) {
                let _ = wait_for_child_exit(child, deadline);
                return;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        if process_group_exists(process_group) {
            unsafe {
                libc::killpg(process_group, libc::SIGKILL);
            }
        }
        let _ = wait_for_child_exit(child, Instant::now() + Duration::from_secs(1));
    }
    #[cfg(windows)]
    {
        let deadline = Instant::now() + Duration::from_secs(2);
        if let Ok(mut taskkill) = Command::new("taskkill")
            .args(windows_taskkill_args(child.id()))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        {
            if !wait_for_child_exit(&mut taskkill, deadline) {
                let _ = taskkill.kill();
            }
        }
        let _ = child.kill();
        let _ = wait_for_child_exit(child, Instant::now() + Duration::from_secs(2));
    }
}

fn ready_proof(token: &str, nonce: &str, port: u16) -> String {
    const BLOCK_BYTES: usize = 64;
    let mut key = [0_u8; BLOCK_BYTES];
    let token_bytes = token.as_bytes();
    if token_bytes.len() > BLOCK_BYTES {
        key[..32].copy_from_slice(&Sha256::digest(token_bytes));
    } else {
        key[..token_bytes.len()].copy_from_slice(token_bytes);
    }

    let mut inner_pad = [0x36_u8; BLOCK_BYTES];
    let mut outer_pad = [0x5c_u8; BLOCK_BYTES];
    for index in 0..BLOCK_BYTES {
        inner_pad[index] ^= key[index];
        outer_pad[index] ^= key[index];
    }

    let mut inner = Sha256::new();
    inner.update(inner_pad);
    inner.update(nonce.as_bytes());
    inner.update([0]);
    inner.update(port.to_string().as_bytes());
    let inner_digest = inner.finalize();

    let mut outer = Sha256::new();
    outer.update(outer_pad);
    outer.update(inner_digest);
    outer
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn proofs_match(provided: &str, expected: &str) -> bool {
    provided.len() == expected.len()
        && provided
            .bytes()
            .zip(expected.bytes())
            .fold(0_u8, |difference, (left, right)| {
                difference | (left ^ right)
            })
            == 0
}

fn wait_for_runtime_ready(
    child: &mut Child,
    ready_path: &Path,
    token: &str,
    nonce: &str,
) -> io::Result<u16> {
    let deadline = Instant::now() + Duration::from_secs(30);
    let mut last_invalid_payload = false;
    while Instant::now() < deadline {
        match std::fs::read_to_string(ready_path) {
            Ok(payload) => {
                let lines: Vec<_> = payload.lines().collect();
                if lines.len() == 2 {
                    if let Ok(port) = lines[0].parse::<u16>() {
                        if port > 0 {
                            let expected = ready_proof(token, nonce, port);
                            if proofs_match(lines[1], &expected) {
                                return Ok(port);
                            }
                        }
                    }
                }
                last_invalid_payload = true;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }

        if let Some(status) = child.try_wait()? {
            return Err(io::Error::new(
                io::ErrorKind::ConnectionAborted,
                format!("Runtime exited before readiness ({status})"),
            ));
        }
        std::thread::sleep(Duration::from_millis(25));
    }

    let detail = if last_invalid_payload {
        "Runtime readiness proof was invalid"
    } else {
        "Runtime readiness timed out"
    };
    Err(io::Error::new(io::ErrorKind::TimedOut, detail))
}

fn spawn_runtime(token: &str) -> Option<RuntimeLaunch> {
    let document_bundle = match packaged_document_bundle_dir() {
        Ok(value) => value,
        Err(error) => {
            eprintln!("[verilex] packaged Documents bundle rejected: {error}");
            return None;
        }
    };
    let root = state_dir();
    if let Err(error) = create_private_directory(&root) {
        eprintln!("[verilex] failed to create local state directory: {error}");
        return None;
    }

    let nonce = Uuid::new_v4().simple().to_string();
    let ready_path = root.join(format!("runtime-{nonce}.ready"));
    let mut command = Command::new(server_bin());
    command
        .args(["--host", "127.0.0.1", "--port", "0", "--state-dir"])
        .arg(&root)
        .args(["--ready-nonce", &nonce])
        .env("VERILEX_API_TOKEN", token)
        .env("VERILEX_EXIT_WITH_PARENT", "1")
        .env("VERILEX_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::null());
    if let Some(bundle_dir) = document_bundle {
        command.arg("--documents-bundle-dir").arg(bundle_dir);
    }

    match runtime_log_file(&root) {
        Some(log) => match log.try_clone() {
            Ok(stderr) => {
                command.stdout(Stdio::from(log)).stderr(Stdio::from(stderr));
            }
            Err(_) => {
                command.stdout(Stdio::from(log)).stderr(Stdio::null());
            }
        },
        None => {
            command.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }

    configure_runtime_process(&mut command);

    match command.spawn() {
        Ok(mut child) => match wait_for_runtime_ready(&mut child, &ready_path, token, &nonce) {
            Ok(port) => {
                let _ = std::fs::remove_file(&ready_path);
                Some(RuntimeLaunch { child, port })
            }
            Err(error) => {
                eprintln!("[verilex] local runtime did not become ready: {error}");
                stop_runtime(&mut child);
                let _ = std::fs::remove_file(&ready_path);
                None
            }
        },
        Err(error) => {
            eprintln!("[verilex] failed to start local runtime: {error}");
            None
        }
    }
}

// The native dialog owns the only writable path; web content cannot supply one.
#[tauri::command]
async fn save_conversation(
    app: tauri::AppHandle,
    default_name: String,
    content: String,
) -> Result<bool, String> {
    use tauri_plugin_dialog::DialogExt;
    tauri::async_runtime::spawn_blocking(move || {
        let filename: String = default_name
            .chars()
            .filter(|c| !c.is_control() && !matches!(c, '/' | '\\' | ':'))
            .take(120)
            .collect();
        let Some(file) = app
            .dialog()
            .file()
            .set_file_name(filename)
            .add_filter("Markdown", &["md"])
            .blocking_save_file()
        else {
            return Ok(false);
        };
        let path = file
            .into_path()
            .map_err(|_| "无法保存对话，请重新选择位置。".to_string())?;
        write_conversation_atomic(&path, content.as_bytes())
            .map_err(|_| "无法保存对话，请检查所选目录是否可写。".to_string())?;
        Ok(true)
    })
    .await
    .map_err(|_| "无法打开保存对话框。".to_string())?
}

fn write_conversation_atomic(path: &std::path::Path, content: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    let temp = path.with_file_name(format!(".verilex-export-{}.tmp", uuid::Uuid::new_v4()));
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)?;
    let result = (|| {
        file.write_all(content)?;
        file.sync_all()?;
        drop(file);
        std::fs::rename(&temp, path)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temp);
    }
    result
}

#[derive(Default)]
struct RedactionExportGrants(Mutex<std::collections::HashSet<PathBuf>>);

#[tauri::command]
async fn save_redaction(
    app: tauri::AppHandle,
    default_name: String,
    content: Vec<u8>,
    directory: Option<String>,
) -> Result<Option<String>, String> {
    if content.len() > 25 * 1024 * 1024
        || ![
            "脱敏版.docx",
            "永久抹除版.docx",
            "文字还原版.docx",
            "脱敏版.md",
        ]
        .contains(&default_name.as_str())
    {
        return Err("无法保存此文件。".to_string());
    }
    if let Some(directory) = directory {
        return tauri::async_runtime::spawn_blocking(move || {
            let selected = app
                .state::<RedactionDirectoryGrant>()
                .0
                .lock()
                .map_err(|_| "请重新选择输出目录。".to_string())?
                .clone();
            let root = checked_redaction_directory(Path::new(&directory), selected.as_deref())?;
            let path = write_redaction_in_directory(&root, &default_name, &content)
                .map_err(|_| "保存失败，请检查输出目录是否可写，或重新选择目录。".to_string())?;
            app.state::<RedactionExportGrants>()
                .0
                .lock()
                .map_err(|_| "文件已保存。".to_string())?
                .insert(path.clone());
            Ok(Some(path.to_string_lossy().to_string()))
        })
        .await
        .map_err(|_| "无法保存文件。".to_string())?;
    }
    save_document_copy(app, default_name, content).await
}

#[derive(Default)]
struct RedactionDirectoryGrant(Mutex<Option<PathBuf>>);

#[tauri::command]
async fn choose_redaction_directory(app: tauri::AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    tauri::async_runtime::spawn_blocking(move || {
        let Some(folder) = app
            .dialog()
            .file()
            .set_title("选择脱敏文件输出目录")
            .blocking_pick_folder()
        else {
            return Ok(None);
        };
        let path = folder
            .into_path()
            .map_err(|_| "无法选择此目录。".to_string())?;
        let canonical = std::fs::canonicalize(path).map_err(|_| "所选目录不存在。".to_string())?;
        if !canonical.is_dir() {
            return Err("请选择一个文件夹。".to_string());
        }
        *app.state::<RedactionDirectoryGrant>()
            .0
            .lock()
            .map_err(|_| "无法选择此目录。".to_string())? = Some(canonical.clone());
        Ok(Some(canonical.to_string_lossy().to_string()))
    })
    .await
    .map_err(|_| "无法打开目录选择窗口。".to_string())?
}

fn checked_redaction_directory(path: &Path, selected: Option<&Path>) -> Result<PathBuf, String> {
    let canonical = std::fs::canonicalize(path)
        .map_err(|_| "输出目录已移动或不存在，请重新选择。".to_string())?;
    if !canonical.is_dir() || selected != Some(canonical.as_path()) || path != canonical {
        return Err("请先通过“选择目录”指定输出位置。".to_string());
    }
    Ok(canonical)
}

fn write_redaction_in_directory(
    root: &Path,
    name: &str,
    content: &[u8],
) -> std::io::Result<PathBuf> {
    let (stem, ext) = name
        .rsplit_once('.')
        .ok_or_else(|| std::io::Error::from(std::io::ErrorKind::InvalidInput))?;
    for index in 0..1000 {
        let filename = if index == 0 {
            name.to_string()
        } else {
            format!("{stem}（{index}）.{ext}")
        };
        let path = root.join(filename);
        match write_redaction_new(&path, content) {
            Ok(()) => return Ok(path),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(std::io::Error::from(std::io::ErrorKind::AlreadyExists))
}

fn valid_contract_review_filename(name: &str) -> bool {
    let Some(stem) = name
        .strip_suffix("（批注版）.docx")
        .or_else(|| name.strip_suffix("（审查意见书）.docx"))
        .or_else(|| name.strip_suffix("（修改副本）.docx"))
    else {
        return false;
    };
    !stem.trim().is_empty() && name.len() <= 240 && name.trim() == name
        && !name.starts_with('.')
        && !name.chars().any(|c| c.is_control() || matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' | '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}'))
}

#[cfg(test)]
mod contract_review_export_tests {
    #[test]
    fn accepts_only_safe_bounded_contract_copy_names() {
        assert!(super::valid_contract_review_filename(
            "技术服务合同（批注版）.docx"
        ));
        assert!(super::valid_contract_review_filename(
            "技术服务合同（审查意见书）.docx"
        ));
        assert!(super::valid_contract_review_filename(
            "技术服务合同（修改副本）.docx"
        ));
        for name in [
            "（修改副本）.docx",
            "../合同（修改副本）.docx",
            ".合同（修改副本）.docx",
            "合同\n（修改副本）.docx",
            "合同\u{202e}（修改副本）.docx",
            "合同（修改副本）.doc",
        ] {
            assert!(
                !super::valid_contract_review_filename(name),
                "accepted invalid name: {name:?}"
            );
        }
        assert!(!super::valid_contract_review_filename(&format!(
            "{}（修改副本）.docx",
            "长".repeat(100)
        )));
        for name in [
            "（审查意见书）.docx",
            "../合同（审查意见书）.docx",
            ".合同（审查意见书）.docx",
            "合同\n（审查意见书）.docx",
        ] {
            assert!(!super::valid_contract_review_filename(name));
        }
        for name in [
            "（批注版）.docx",
            "合同.docx",
            "合同（批注版）.doc",
            "../合同（批注版）.docx",
            "/合同（批注版）.docx",
            "dir\\合同（批注版）.docx",
            "C:合同（批注版）.docx",
            ".合同（批注版）.docx",
            " 合同（批注版）.docx",
            "合同\n（批注版）.docx",
            "合同\u{202e}（批注版）.docx",
        ] {
            assert!(
                !super::valid_contract_review_filename(name),
                "accepted invalid name: {name:?}"
            );
        }
        assert!(!super::valid_contract_review_filename(&format!(
            "{}（批注版）.docx",
            "长".repeat(100)
        )));
    }
}

#[tauri::command]
async fn save_contract_review(
    app: tauri::AppHandle,
    default_name: String,
    content: Vec<u8>,
) -> Result<Option<String>, String> {
    if content.len() > 25 * 1024 * 1024 || !valid_contract_review_filename(&default_name) {
        return Err("无法保存此审查成果：文件名无效或文件超过 25 MiB。".to_string());
    }
    save_document_copy(app, default_name, content).await
}

async fn save_document_copy(
    app: tauri::AppHandle,
    default_name: String,
    content: Vec<u8>,
) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    tauri::async_runtime::spawn_blocking(move || {
        let ext = if default_name.ends_with(".pdf") {
            "pdf"
        } else if default_name.ends_with(".md") {
            "md"
        } else if default_name.ends_with(".svg") {
            "svg"
        } else if default_name.ends_with(".png") {
            "png"
        } else if default_name.ends_with(".zip") {
            "zip"
        } else {
            "docx"
        };
        let Some(file) = app
            .dialog()
            .file()
            .set_file_name(default_name)
            .add_filter("文件", &[ext])
            .blocking_save_file()
        else {
            return Ok(None);
        };
        let path = file
            .into_path()
            .map_err(|_| "无法保存，请重新选择位置。".to_string())?;
        if path.extension().and_then(|x| x.to_str()) != Some(ext) {
            return Err(format!("请使用 .{ext} 文件扩展名。"));
        }
        write_redaction_new(&path, &content).map_err(|e| {
            if e.kind() == std::io::ErrorKind::AlreadyExists {
                "所选位置已有同名文件，请改用新文件名。原文件未修改。".to_string()
            } else {
                "保存失败，请检查所选目录是否可写。".to_string()
            }
        })?;
        let canonical =
            std::fs::canonicalize(&path).map_err(|_| "文件已保存，但无法显示路径。".to_string())?;
        app.state::<RedactionExportGrants>()
            .0
            .lock()
            .map_err(|_| "文件已保存。".to_string())?
            .insert(canonical.clone());
        Ok(Some(canonical.to_string_lossy().to_string()))
    })
    .await
    .map_err(|_| "无法打开保存对话框。".to_string())?
}

// Publish a complete new file without replacing anything, including a raced-in source.
fn write_redaction_new(path: &Path, content: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    let temp = path.with_file_name(format!(".verilex-redaction-{}.tmp", uuid::Uuid::new_v4()));
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temp)?;
    let result = (|| {
        file.write_all(content)?;
        file.sync_all()?;
        drop(file);
        std::fs::hard_link(&temp, path)
    })();
    let _ = std::fs::remove_file(&temp);
    result
}

#[tauri::command]
async fn open_redaction_export(
    app: tauri::AppHandle,
    path: String,
    reveal: bool,
) -> Result<(), String> {
    let canonical = std::fs::canonicalize(&path).map_err(|_| "文件已移动或不存在。".to_string())?;
    if !app
        .state::<RedactionExportGrants>()
        .0
        .lock()
        .map_err(|_| "无法打开文件。".to_string())?
        .contains(&canonical)
    {
        return Err("请先保存文件。".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || {
        #[cfg(target_os = "macos")]
        {
            let mut command = std::process::Command::new("/usr/bin/open");
            if reveal {
                command.arg("-R");
            }
            let status = command
                .arg(&canonical)
                .status()
                .map_err(|_| "无法打开文件。".to_string())?;
            if status.success() {
                Ok(())
            } else {
                Err("无法打开文件。".to_string())
            }
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = (canonical, reveal);
            Err("请在保存位置打开文件。".to_string())
        }
    })
    .await
    .map_err(|_| "无法打开文件。".to_string())?
}

#[cfg(test)]
mod redaction_export_tests {
    use super::*;
    #[test]
    fn selected_directory_is_required_and_duplicates_get_new_names() {
        let root = std::env::temp_dir().join(format!(
            "verilex-redaction-directory-{}",
            uuid::Uuid::new_v4()
        ));
        std::fs::create_dir(&root).unwrap();
        let root = std::fs::canonicalize(root).unwrap();
        assert!(checked_redaction_directory(&root, None).is_err());
        assert!(checked_redaction_directory(&root, Some(root.parent().unwrap())).is_err());
        assert_eq!(
            checked_redaction_directory(&root, Some(&root)).unwrap(),
            root
        );
        std::fs::write(root.join("脱敏版.docx"), b"existing").unwrap();
        let first = write_redaction_in_directory(&root, "脱敏版.docx", b"first").unwrap();
        let second = write_redaction_in_directory(&root, "脱敏版.docx", b"second").unwrap();
        assert_eq!(first.file_name().unwrap(), "脱敏版（1）.docx");
        assert_eq!(second.file_name().unwrap(), "脱敏版（2）.docx");
        assert_eq!(
            std::fs::read(root.join("脱敏版.docx")).unwrap(),
            b"existing"
        );
        assert_eq!(std::fs::read(first).unwrap(), b"first");
        assert_eq!(std::fs::read(second).unwrap(), b"second");
        assert_eq!(std::fs::read_dir(&root).unwrap().count(), 3);
        std::fs::remove_dir_all(&root).unwrap();
        assert!(checked_redaction_directory(&root, Some(&root)).is_err());
    }

    #[test]
    fn never_overwrites_and_cleans_temporary_file() {
        let root =
            std::env::temp_dir().join(format!("verilex-redaction-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&root).unwrap();
        let target = root.join("原件.docx");
        std::fs::write(&target, b"original").unwrap();
        assert_eq!(
            write_redaction_new(&target, b"changed").unwrap_err().kind(),
            std::io::ErrorKind::AlreadyExists
        );
        assert_eq!(std::fs::read(&target).unwrap(), b"original");
        let output = root.join("new.docx");
        write_redaction_new(&output, b"complete").unwrap();
        assert_eq!(std::fs::read(output).unwrap(), b"complete");
        assert_eq!(std::fs::read_dir(&root).unwrap().count(), 2);
        std::fs::remove_dir_all(root).unwrap();
    }
}

pub fn run() {
    match document_renderer_self_test_request() {
        Ok(Some(report_path)) => {
            if let Err(error) = run_document_renderer_self_test(report_path) {
                eprintln!("[verilex] Documents renderer self-test failed: {error}");
                std::process::exit(2);
            }
            return;
        }
        Ok(None) => {}
        Err(error) => {
            eprintln!("[verilex] Documents renderer self-test rejected: {error}");
            std::process::exit(2);
        }
    }
    let token = launch_token();

    tauri::Builder::default()
        .manage(DroppedFileGrants::default())
        .manage(case_calendar::ReminderState::default())
        .manage(desktop_preferences::DesktopState::default())
        .manage(browser_panel::BrowserState::default())
        .manage(RedactionExportGrants::default())
        .manage(RedactionDirectoryGrant::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(|invoke| {
            if !browser_panel::trusted_caller(invoke.message.webview().label()) {
                invoke.resolver.reject("网页无权调用应用命令。");
                return true;
            }
            let handler: fn(tauri::ipc::Invoke<tauri::Wry>) -> bool = tauri::generate_handler![
                browser_panel::browser_action,
                read_dropped_file,
                save_conversation,
                save_redaction,
                choose_redaction_directory,
                save_contract_review,
                diagram_export::save_litigation_diagram,
                drafting_export::save_litigation_document,
                open_redaction_export,
                open_yuandian_console,
                open_law_database,
                open_case_database,
                command_shortcuts::set_global_wake_shortcut,
                desktop_preferences::desktop_status,
                open_app_data_directory,
                desktop_preferences::desktop_test_notification,
                case_calendar::case_calendar_notification_failed
            ];
            handler(invoke)
        })
        .on_webview_event(|webview, event| {
            grant_dropped_files_from_webview(
                &webview.state::<DroppedFileGrants>(),
                webview.label(),
                event,
            );
        })
        .setup(move |app| {
            #[cfg(any(target_os = "macos", windows, target_os = "linux"))]
            app.handle()
                .plugin(tauri_plugin_updater::Builder::new().build())?;

            let mut launch = spawn_runtime(&token);
            let endpoint = match launch.as_ref() {
                Some(runtime) => format!("http://127.0.0.1:{}", runtime.port),
                None => "http://127.0.0.1:0".to_owned(),
            };
            let webview_token = if launch.is_some() { token.as_str() } else { "" };
            let injection = initialization_script(&endpoint, webview_token);
            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("VeriLex")
                    .inner_size(1360.0, 900.0)
                    .min_inner_size(920.0, 640.0)
                    // macOS overlay traffic lights and native title text: the
                    // web app draws its own top toolbar (sidebar toggle,
                    // back/forward) to the right of both — the toolbar's
                    // leading spacer reserves their native positions.
                    .title_bar_style(TitleBarStyle::Overlay)
                    .initialization_script(&injection)
                    .on_navigation(|url| {
                        url.as_str() == "about:blank"
                            || (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
                            || (matches!(url.scheme(), "http" | "https")
                                && url.host_str() == Some("tauri.localhost"))
                            || (cfg!(debug_assertions)
                                && url.host_str() == Some("localhost")
                                && url.port() == Some(1420))
                    })
                    .build();
            if let Err(error) = window {
                if let Some(mut runtime) = launch.take() {
                    stop_runtime(&mut runtime.child);
                }
                return Err(Box::new(error));
            }
            if let Some(runtime) = launch.as_ref() {
                desktop_preferences::start(app.handle(), runtime.port, token.clone());
                case_calendar::start(app.handle(), runtime.port, token.clone());
            }
            app.manage(RuntimeProcess(Mutex::new(
                launch.map(|runtime| runtime.child),
            )));
            Ok(())
        })
        .build(app_context())
        .expect("error while building the VeriLex desktop app")
        .run(|app, event| {
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                if let Some(state) = app.try_state::<desktop_preferences::DesktopState>() {
                    state.stop.store(true, Ordering::Relaxed);
                }
                if let Some(state) = app.try_state::<case_calendar::ReminderState>() {
                    state.stop.store(true, Ordering::Relaxed);
                }
                if let Some(state) = app.try_state::<RuntimeProcess>() {
                    if let Some(mut child) = state.0.lock().expect("runtime lock poisoned").take() {
                        stop_runtime(&mut child);
                    }
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn launch_tokens_are_long_and_unique() {
        let first = launch_token();
        let second = launch_token();
        assert!(first.len() >= 64);
        assert_ne!(first, second);
    }

    #[test]
    fn case_database_destination_is_fixed() {
        assert_eq!(
            case_database_url("verilex-feedback").unwrap(),
            "https://github.com/Riven-Wood/VeriLex/issues"
        );
        assert!(case_database_url("verilex-feedback?body=private").is_err());
        assert_eq!(
            case_database_url("library").unwrap(),
            "https://rmfyalk.court.gov.cn/"
        );
        assert_eq!(
            case_database_url("judgments").unwrap(),
            "https://wenshu.court.gov.cn/"
        );
        assert_eq!(
            case_database_url("templates").unwrap(),
            "https://www.court.gov.cn/susong.html"
        );
        assert_eq!(
            case_database_url("plugin-key-wkinfo").unwrap(),
            "https://mcp.wkinfo.com.cn/"
        );
        assert_eq!(
            case_database_url("plugin-key-github").unwrap(),
            "https://github.com/settings/personal-access-tokens/new"
        );
        assert!(case_database_url("plugin-key-https://example.com").is_err());
        assert!(case_database_url("plugin-key-officecli").is_err());
        assert!(case_database_url("https://example.com").is_err());
        assert!(case_database_url("library;open /tmp").is_err());
        for id in ["deepseek", "qwen", "glm", "kimi", "doubao", "minimax"] {
            for site in ["console", "docs"] {
                assert!(case_database_url(&format!("model-{site}-{id}"))
                    .unwrap()
                    .starts_with("https://"));
            }
        }
        assert!(case_database_url("model-console-https://example.com").is_err());
        assert!(case_database_url("model-console-deepseek;open /tmp").is_err());
    }

    #[test]
    fn native_drop_accepts_only_standard_docx() {
        assert!(native_dropped_file_supported(Path::new("contract.docx")));
        assert!(!native_dropped_file_supported(Path::new("contract.docm")));
        assert!(!native_dropped_file_supported(Path::new("contract.dotx")));
    }

    #[test]
    fn injected_values_are_json_escaped_and_read_only() {
        let script = initialization_script(
            "http://127.0.0.1:8766",
            "token\";\nalert('not executable')//",
        );

        assert_eq!(
            script,
            "Object.defineProperties(globalThis,{__VERILEX_HTTP__:{value:\"http://127.0.0.1:8766\",writable:false,configurable:false},__VERILEX_API_TOKEN__:{value:\"token\\\";\\nalert('not executable')//\",writable:false,configurable:false}});"
        );
        assert!(!script.contains(";\nalert"));
    }

    fn write_document_bundle_fixture(root: &Path) -> String {
        let component_dir = root.join("bin");
        let license_dir = root.join("licenses");
        std::fs::create_dir_all(&component_dir).unwrap();
        std::fs::create_dir_all(&license_dir).unwrap();
        std::fs::write(component_dir.join("component"), b"fixed component").unwrap();
        std::fs::write(license_dir.join("component.txt"), b"MIT\n").unwrap();
        let component_hash = format!("{:x}", Sha256::digest(b"fixed component"));
        let license_hash = format!("{:x}", Sha256::digest(b"MIT\n"));
        let manifest = format!(
            "{{\"components\":[{{\"arch\":\"arm64\",\"id\":\"fixture\",\"license_paths\":[\"licenses/component.txt\"],\"minimum_macos\":\"12.0\",\"platform\":\"macos\",\"relative_path\":\"bin/component\",\"role\":\"test_fixture\",\"sha256\":\"{component_hash}\",\"size_bytes\":15,\"version\":\"1.0.0\"}},{{\"arch\":\"arm64\",\"id\":\"fixture-license\",\"license_paths\":[\"licenses/component.txt\"],\"minimum_macos\":\"12.0\",\"platform\":\"macos\",\"relative_path\":\"licenses/component.txt\",\"role\":\"license\",\"sha256\":\"{license_hash}\",\"size_bytes\":4,\"version\":\"1.0.0\"}}],\"schema_version\":1,\"selected_engine\":\"docxodus\",\"system_read_allowlist\":[\"/System/Library\",\"/System/Volumes/Preboot/Cryptexes\",\"/usr/lib\",\"/usr/bin\",\"/usr/share\",\"/bin\",\"/dev\",\"/private/etc\",\"/private/var/db/timezone\",\"/Library/Fonts\"]}}"
        );
        std::fs::write(root.join("component-manifest.json"), manifest.as_bytes()).unwrap();
        format!("{:x}", Sha256::digest(manifest.as_bytes()))
    }

    #[test]
    fn packaged_document_verifier_rejects_component_and_manifest_tampering() {
        let root = std::env::temp_dir().join(format!("verilex-documents-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let manifest_hash = write_document_bundle_fixture(&root);

        assert!(
            verify_document_bundle(&root, Some(&manifest_hash), "macos", "arm64", "12.7.6").is_ok()
        );

        std::fs::write(root.join("bin/component"), b"fixed componenx").unwrap();
        assert_eq!(
            verify_document_bundle(&root, Some(&manifest_hash), "macos", "arm64", "12.7.6"),
            Err("component_hash_mismatch")
        );

        let replacement_hash = format!("{:x}", Sha256::digest(b"fixed componenx"));
        let manifest_path = root.join("component-manifest.json");
        let tampered = std::fs::read_to_string(&manifest_path).unwrap().replace(
            &format!("{:x}", Sha256::digest(b"fixed component")),
            &replacement_hash,
        );
        std::fs::write(&manifest_path, tampered).unwrap();
        assert_eq!(
            verify_document_bundle(&root, Some(&manifest_hash), "macos", "arm64", "12.7.6"),
            Err("manifest_hash_mismatch")
        );

        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn development_environment_cannot_supply_document_manifest_trust_root() {
        let root = std::env::temp_dir().join(format!("verilex-documents-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let manifest_hash = write_document_bundle_fixture(&root);
        std::env::set_var("VERILEX_DOCUMENT_MANIFEST_SHA256", &manifest_hash);

        assert_eq!(compiled_document_manifest_sha256(), None);
        assert_eq!(
            verify_document_bundle(
                &root,
                compiled_document_manifest_sha256(),
                "macos",
                "arm64",
                "12.7.6"
            ),
            Err("manifest_trust_root_unavailable")
        );

        std::env::remove_var("VERILEX_DOCUMENT_MANIFEST_SHA256");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn yuandian_window_only_allows_vendor_https_navigation() {
        for allowed in [
            "https://open.chineselaw.com/profile",
            "https://account.chineselaw.com/login",
            "https://chineselaw.com/",
            "https://open.chineselaw.com:443/mcp-config",
            "https://passport.legalmind.cn/ssologin",
            "https://legalmind.cn/",
            "https://open.weixin.qq.com/connect/qrconnect",
        ] {
            assert!(
                yuandian_navigation_allowed(&allowed.parse().unwrap()),
                "{allowed}"
            );
        }
        for denied in [
            "http://open.chineselaw.com/profile",
            "http://passport.legalmind.cn/ssologin",
            "https://open.chineselaw.com:444/profile",
            "https://open.chineselaw.com.evil.test/profile",
            "https://evilchineselaw.com/profile",
            "https://user@open.chineselaw.com/profile",
            "https://legalmind.cn.evil.test/ssologin",
            "https://evilegalmind.cn/ssologin",
            "https://passport.legalmind.cn:8443/ssologin",
            "https://weixin.qq.com/",
            "https://open.weixin.qq.com.evil.test/connect",
            "file:///tmp/profile",
        ] {
            assert!(
                !yuandian_navigation_allowed(&denied.parse().unwrap()),
                "{denied}"
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn existing_runtime_log_permissions_are_tightened() {
        use std::os::unix::fs::PermissionsExt;

        let root = std::env::temp_dir().join(format!("verilex-log-test-{}", Uuid::new_v4()));
        let logs = root.join("logs");
        std::fs::create_dir_all(&logs).unwrap();
        let path = logs.join("verilex-runtime.log");
        std::fs::write(&path, "previous launch").unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();

        drop(runtime_log_file(&root).unwrap());

        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn runtime_log_refuses_symbolic_links() {
        use std::os::unix::fs::symlink;

        let root = std::env::temp_dir().join(format!("verilex-log-test-{}", Uuid::new_v4()));
        let logs = root.join("logs");
        std::fs::create_dir_all(&logs).unwrap();
        let victim = root.join("must-not-change.txt");
        std::fs::write(&victim, "keep this content").unwrap();
        symlink(&victim, logs.join("verilex-runtime.log")).unwrap();

        assert!(runtime_log_file(&root).is_none());
        assert_eq!(
            std::fs::read_to_string(&victim).unwrap(),
            "keep this content"
        );
        std::fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn runtime_log_refuses_symbolic_linked_log_directory() {
        use std::os::unix::fs::symlink;

        let root = std::env::temp_dir().join(format!("verilex-log-test-{}", Uuid::new_v4()));
        let outside = std::env::temp_dir().join(format!("verilex-log-outside-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        let victim = outside.join("verilex-runtime.log");
        std::fs::write(&victim, "keep this content").unwrap();
        symlink(&outside, root.join("logs")).unwrap();

        let result = runtime_log_file(&root);
        let refused = result.is_none();
        let victim_content = std::fs::read_to_string(&victim).unwrap();
        drop(result);
        std::fs::remove_file(root.join("logs")).unwrap();
        std::fs::remove_dir_all(root).unwrap();
        std::fs::remove_dir_all(outside).unwrap();

        assert!(refused);
        assert_eq!(victim_content, "keep this content");
    }

    #[cfg(unix)]
    #[test]
    fn stopping_runtime_terminates_descendant_processes() {
        use std::time::Duration;

        let root = std::env::temp_dir().join(format!("verilex-process-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let pid_file = root.join("grandchild.pid");
        let mut command = Command::new("/bin/sh");
        command
            .arg("-c")
            .arg("sleep 30 & echo $! > \"$1\"; wait")
            .arg("verilex-process-test")
            .arg(&pid_file)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        configure_runtime_process(&mut command);
        let mut child = command.spawn().unwrap();

        for _ in 0..50 {
            if pid_file.exists() {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let grandchild_pid: i32 = std::fs::read_to_string(&pid_file)
            .unwrap()
            .trim()
            .parse()
            .unwrap();

        stop_runtime(&mut child);
        std::thread::sleep(Duration::from_millis(50));
        let still_running = unsafe { libc::kill(grandchild_pid, 0) == 0 };
        if still_running {
            unsafe { libc::kill(grandchild_pid, libc::SIGKILL) };
        }
        std::fs::remove_dir_all(root).unwrap();

        assert!(!still_running, "runtime descendant survived app shutdown");
    }

    #[cfg(unix)]
    #[test]
    fn stopping_runtime_is_bounded_when_sigterm_is_ignored() {
        use std::sync::mpsc;
        use std::time::Duration;

        let mut command = Command::new("/bin/sh");
        command
            .arg("-c")
            .arg("trap '' TERM; while :; do sleep 30; done")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        configure_runtime_process(&mut command);
        let child = command.spawn().unwrap();
        let process_group = child.id() as i32;
        std::thread::sleep(Duration::from_millis(100));
        let (completed_tx, completed_rx) = mpsc::channel();

        let stopper = std::thread::spawn(move || {
            let mut child = child;
            stop_runtime(&mut child);
            let _ = completed_tx.send(());
        });
        let completed_within_bound = completed_rx.recv_timeout(Duration::from_secs(3)).is_ok();
        if !completed_within_bound {
            unsafe { libc::killpg(process_group, libc::SIGKILL) };
            let _ = completed_rx.recv_timeout(Duration::from_secs(2));
        }
        stopper.join().unwrap();

        assert!(
            completed_within_bound,
            "Runtime shutdown waited forever after SIGTERM"
        );
    }

    #[test]
    fn dropped_file_grants_are_exact_short_lived_and_single_use() {
        let root = std::env::temp_dir().join(format!("verilex-drop-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).expect("create test directory");
        let source = root.join("example.py");
        let unsupported = root.join("archive.zip");
        std::fs::write(&source, b"print()\n").expect("write source fixture");
        std::fs::write(&unsupported, b"zip").expect("write unsupported fixture");

        let grants = DroppedFileGrants::default();
        replace_dropped_file_grants(&grants, &[source.clone(), unsupported.clone()]);

        assert!(!claim_dropped_file(&grants, &unsupported));
        assert!(claim_dropped_file(&grants, &source));
        assert!(!claim_dropped_file(&grants, &source));
        std::fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[test]
    fn dropped_webview_event_grants_reads_only_after_main_view_drop() {
        let root = std::env::temp_dir().join(format!("verilex-event-drop-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let source = root.join("拖放合同.doc");
        let bytes = b"synthetic contract event fixture";
        std::fs::write(&source, bytes).unwrap();
        let grants = DroppedFileGrants::default();
        let enter = WebviewEvent::DragDrop(DragDropEvent::Enter {
            paths: vec![source.clone()],
            position: tauri::PhysicalPosition::new(20.0, 20.0),
        });
        let drop = WebviewEvent::DragDrop(DragDropEvent::Drop {
            paths: vec![source.clone()],
            position: tauri::PhysicalPosition::new(20.0, 20.0),
        });
        grant_dropped_files_from_webview(&grants, "main", &enter);
        assert!(!claim_dropped_file(&grants, &source));
        grant_dropped_files_from_webview(&grants, "browser-panel", &drop);
        assert!(!claim_dropped_file(&grants, &source));
        grant_dropped_files_from_webview(&grants, "main", &drop);
        assert!(claim_dropped_file(&grants, &source));
        assert_eq!(&read_dropped_file_payload(&source).unwrap()[8..], bytes);
        assert!(!claim_dropped_file(&grants, &source));
        assert_eq!(std::fs::read(&source).unwrap(), bytes);
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn dropped_word_files_receive_single_use_grants_and_preserve_bytes() {
        let root = std::env::temp_dir().join(format!("verilex-word-drop-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        for name in ["合同.doc", "合同.DOC", "合同.docx"] {
            let source = root.join(name);
            let bytes = b"\xd0\xcf\x11\xe0synthetic Word transport fixture";
            std::fs::write(&source, bytes).unwrap();
            let grants = DroppedFileGrants::default();
            replace_dropped_file_grants(&grants, &[source.clone()]);
            assert!(claim_dropped_file(&grants, &source), "missing grant: {name}");
            assert!(!claim_dropped_file(&grants, &source), "grant reused: {name}");
            let payload = read_dropped_file_payload(&source).expect("read Word file");
            assert_eq!(&payload[8..], bytes);
            assert_eq!(std::fs::read(&source).unwrap(), bytes);
        }
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn dropped_file_payload_has_mtime_header_without_changing_contents() {
        let root = std::env::temp_dir().join(format!("verilex-drop-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).expect("create test directory");
        let source = root.join("settings.toml");
        std::fs::write(&source, b"name = 'demo'\n").expect("write source fixture");

        let payload = read_dropped_file_payload(&source).expect("read supported dropped file");

        assert!(payload.len() >= 8);
        assert_eq!(&payload[8..], b"name = 'demo'\n");
        std::fs::remove_dir_all(&root).expect("remove test directory");
    }

    #[cfg(unix)]
    #[test]
    fn stopping_runtime_kills_descendants_that_ignore_sigterm() {
        use std::time::Duration;

        let root =
            std::env::temp_dir().join(format!("verilex-stubborn-child-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let pid_file = root.join("stubborn-child.pid");
        let mut command = Command::new("/bin/sh");
        command
            .arg("-c")
            .arg(
                "/bin/sh -c 'trap \"\" TERM; echo $$ > \"$1\"; \
                 while :; do sleep 30; done' descendant \"$1\" & wait",
            )
            .arg("verilex-stubborn-child-test")
            .arg(&pid_file)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        configure_runtime_process(&mut command);
        let mut child = command.spawn().unwrap();
        let process_group = child.id() as i32;

        for _ in 0..100 {
            if pid_file.exists() {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let stubborn_pid: i32 = std::fs::read_to_string(&pid_file)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        std::thread::sleep(Duration::from_millis(100));

        stop_runtime(&mut child);
        std::thread::sleep(Duration::from_millis(50));
        let still_running = unsafe { libc::kill(stubborn_pid, 0) == 0 };
        if still_running {
            unsafe { libc::killpg(process_group, libc::SIGKILL) };
        }
        std::fs::remove_dir_all(root).unwrap();

        assert!(
            !still_running,
            "SIGTERM-ignoring Runtime descendant survived app shutdown"
        );
    }

    #[test]
    fn windows_tree_kill_arguments_include_descendants() {
        assert_eq!(windows_taskkill_args(4321), ["/PID", "4321", "/T", "/F"]);
    }

    #[cfg(unix)]
    #[test]
    fn runtime_launch_uses_the_port_reported_after_child_binding() {
        use std::os::unix::fs::PermissionsExt;

        let root = std::env::temp_dir().join(format!("verilex-ready-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let fake_runtime = root.join("fake-runtime.sh");
        std::fs::write(
            &fake_runtime,
            r#"#!/usr/bin/env python3
import hashlib
import hmac
import os
from pathlib import Path
import signal
import sys
import time

arguments = iter(sys.argv[1:])
options = dict(zip(arguments, arguments))
state_dir = Path(options["--state-dir"])
nonce = options["--ready-nonce"]
port = 43210
proof = hmac.new(
    os.environ["VERILEX_API_TOKEN"].encode(),
    f"{nonce}\0{port}".encode(),
    hashlib.sha256,
).hexdigest()
(state_dir / f"runtime-{nonce}.ready").write_text(f"{port}\n{proof}\n")
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(30)
"#,
        )
        .unwrap();
        std::fs::set_permissions(&fake_runtime, std::fs::Permissions::from_mode(0o700)).unwrap();

        let old_bin = std::env::var_os("VERILEX_SERVER_BIN");
        let old_state = std::env::var_os("VERILEX_STATE_DIR");
        std::env::set_var("VERILEX_SERVER_BIN", &fake_runtime);
        std::env::set_var("VERILEX_STATE_DIR", &root);
        let launch = spawn_runtime("test-token");
        match old_bin {
            Some(value) => std::env::set_var("VERILEX_SERVER_BIN", value),
            None => std::env::remove_var("VERILEX_SERVER_BIN"),
        }
        match old_state {
            Some(value) => std::env::set_var("VERILEX_STATE_DIR", value),
            None => std::env::remove_var("VERILEX_STATE_DIR"),
        }

        let mut launch = launch.expect("fake Runtime should report readiness");
        assert_eq!(launch.port, 43210);
        stop_runtime(&mut launch.child);
        std::fs::remove_dir_all(root).unwrap();
    }
}

#[cfg(test)]
mod conversation_export_tests {
    #[test]
    fn export_replaces_complete_file_and_cleans_temporary_file() {
        let directory =
            std::env::temp_dir().join(format!("verilex-export-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&directory).unwrap();
        let target = directory.join("对话.md");
        std::fs::write(&target, "old").unwrap();
        super::write_conversation_atomic(&target, "完整对话".as_bytes()).unwrap();
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "完整对话");
        assert_eq!(std::fs::read_dir(&directory).unwrap().count(), 1);
        let blocked = directory.join("directory");
        std::fs::create_dir(&blocked).unwrap();
        assert!(super::write_conversation_atomic(&blocked, b"new").is_err());
        assert_eq!(std::fs::read_dir(&directory).unwrap().count(), 2);
        std::fs::remove_dir_all(&directory).unwrap();
    }
}
