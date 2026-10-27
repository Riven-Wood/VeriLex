//! Human-only, ephemeral remote WebView. Never receives the runtime bootstrap script.
use serde_json::{json, Value};
use std::sync::Mutex;
use tauri::webview::{NewWindowResponse, PageLoadEvent, WebviewBuilder};
use tauri::{AppHandle, LogicalPosition, LogicalSize, Manager, Webview, WebviewUrl};
const LABEL: &str = "browser-panel";
#[derive(Default)]
pub struct BrowserState(pub Mutex<Value>);

pub fn trusted_caller(label: &str) -> bool {
    label == "main"
}

pub fn allowed(url: &tauri::Url) -> bool {
    if !matches!(url.scheme(), "http" | "https")
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return false;
    }
    let Some(host) = url.host_str() else {
        return false;
    };
    let host = host
        .trim_end_matches('.')
        .trim_matches(['[', ']'])
        .to_ascii_lowercase();
    if let Ok(ip) = host.parse::<std::net::IpAddr>() {
        return public_unicast(ip);
    }
    if !host.contains('.')
        || [
            "localhost",
            "local",
            "localdomain",
            "internal",
            "lan",
            "test",
            "invalid",
            "example",
            "home.arpa",
            "onion",
        ]
        .iter()
        .any(|suffix| host == *suffix || host.ends_with(&format!(".{suffix}")))
    {
        return false;
    }
    true
}
// Same public-literal policy as Runtime web_hosts.py and Desktop citations.ts.
fn public_unicast(ip: std::net::IpAddr) -> bool {
    use std::net::IpAddr;
    match ip {
        IpAddr::V4(ip) => {
            let [a, b, c, d] = ip.octets();
            !(a == 0
                || a == 10
                || a == 127
                || a >= 224
                || (a == 100 && (64..=127).contains(&b))
                || (a == 169 && b == 254)
                || (a == 172 && (16..=31).contains(&b))
                || (a == 192 && b == 0 && c == 0 && d != 9 && d != 10)
                || (a == 192 && b == 0 && c == 2)
                || (a == 192 && b == 168)
                || (a == 198 && (b == 18 || b == 19))
                || (a == 198 && b == 51 && c == 100)
                || (a == 203 && b == 0 && c == 113))
        }
        IpAddr::V6(ip) => {
            if let Some(mapped) = ip.to_ipv4_mapped() {
                return public_unicast(IpAddr::V4(mapped));
            }
            let value = u128::from(ip);
            let inside = |prefix: &str, bits: u32| {
                let network = u128::from(
                    prefix
                        .parse::<std::net::Ipv6Addr>()
                        .expect("fixed IPv6 prefix"),
                );
                let mask = u128::MAX << (128 - bits);
                value & mask == network & mask
            };
            if !inside("2000::", 3) {
                return false;
            }
            if inside("2001::", 23)
                && ![
                    ("2001:1::1", 128),
                    ("2001:1::2", 128),
                    ("2001:3::", 32),
                    ("2001:4:112::", 48),
                    ("2001:20::", 28),
                    ("2001:30::", 28),
                ]
                .iter()
                .any(|(prefix, bits)| inside(prefix, *bits))
            {
                return false;
            }
            !inside("2001:db8::", 32) && !inside("2002::", 16) && !inside("3fff::", 20)
        }
    }
}

fn parse(value: &str) -> Result<tauri::Url, String> {
    let url = tauri::Url::parse(value).map_err(|_| "请输入有效的网页地址。".to_owned())?;
    if !allowed(&url) {
        return Err("仅支持公开网页，不支持本机地址、应用协议或带账号密码的网址。".into());
    }
    Ok(url)
}
fn state(app: &AppHandle, url: &str, phase: &str, notice: &str) {
    *app.state::<BrowserState>().0.lock().unwrap() =
        json!({"url":url,"phase":phase,"notice":notice});
}
#[tauri::command]
pub async fn browser_action(
    app: AppHandle,
    webview: Webview,
    action: String,
    url: Option<String>,
    bounds: Option<Vec<f64>>,
) -> Result<Value, String> {
    if !trusted_caller(webview.label()) {
        return Err("网页无权操作应用。".into());
    }
    let current = app.get_webview(LABEL);
    match action.as_str() {
        "status" => {
            let mut value = app.state::<BrowserState>().0.lock().unwrap().clone();
            if value["phase"] == "ready" {
                if let Some(view) = current {
                    if let Ok(url) = view.url() {
                        if allowed(&url) {
                            value["url"] = json!(url.as_str());
                        }
                    }
                }
            }
            return Ok(value);
        }
        "close" => {
            if let Some(view) = current {
                view.close().map_err(|_| "关闭网页失败。")?;
            }
            state(&app, "", "idle", "");
        }
        "hide" => {
            if let Some(view) = current {
                view.hide().map_err(|_| "收起网页失败。")?;
            }
        }
        "bounds" => {
            if let Some(view) = current {
                let b = bounds.ok_or("网页区域不可用。")?;
                if b.len() != 4
                    || b.iter().any(|n| !n.is_finite() || *n < 0.0 || *n > 20000.0)
                    || b[2] < 20.0
                    || b[3] < 20.0
                {
                    return Err("网页区域不可用。".into());
                }
                view.set_bounds(tauri::Rect {
                    position: LogicalPosition::new(b[0], b[1]).into(),
                    size: LogicalSize::new(b[2], b[3]).into(),
                })
                .map_err(|_| "调整网页区域失败。")?;
                view.show().map_err(|_| "显示网页失败。")?;
            }
        }
        "open" => {
            let url = parse(url.as_deref().unwrap_or(""))?;
            state(&app, url.as_str(), "loading", "");
            if let Some(view) = current {
                view.navigate(url).map_err(|_| "网页打开失败，请重试。")?;
            } else {
                let nav_app = app.clone();
                let popup_app = app.clone();
                let download_app = app.clone();
                let builder = WebviewBuilder::new(LABEL, WebviewUrl::External(url))
                    .incognito(true)
                    .on_navigation(move |url| {
                        if allowed(url) {
                            true
                        } else {
                            let mut s = nav_app.state::<BrowserState>().0.lock().unwrap().clone();
                            s["notice"] = json!("已阻止非公开网页地址。");
                            *nav_app.state::<BrowserState>().0.lock().unwrap() = s;
                            false
                        }
                    })
                    .on_new_window(move |url, _| {
                        if allowed(&url) {
                            let app = popup_app.clone();
                            tauri::async_runtime::spawn(async move {
                                if let Some(view) = app.get_webview(LABEL) {
                                    let _ = view.navigate(url);
                                }
                            });
                        }
                        NewWindowResponse::Deny
                    })
                    .on_download(move |_, _| {
                        let mut s = download_app
                            .state::<BrowserState>()
                            .0
                            .lock()
                            .unwrap()
                            .clone();
                        s["notice"] = json!("如需下载文件，请使用“系统浏览器打开”。");
                        *download_app.state::<BrowserState>().0.lock().unwrap() = s;
                        false
                    })
                    .on_page_load(|view, payload| {
                        state(
                            view.app_handle(),
                            payload.url().as_str(),
                            if matches!(payload.event(), PageLoadEvent::Finished) {
                                "ready"
                            } else {
                                "loading"
                            },
                            "",
                        );
                    });
                app.get_window("main")
                    .ok_or("应用窗口不可用。")?
                    .add_child(
                        builder,
                        LogicalPosition::new(0.0, 0.0),
                        LogicalSize::new(1.0, 1.0),
                    )
                    .map_err(|_| "内置浏览器启动失败，请重试或使用系统浏览器。")?
                    .hide()
                    .map_err(|_| "内置浏览器启动失败。")?;
            }
        }
        "back" | "forward" | "reload" | "stop" => {
            let view = current.ok_or("请先打开一个网页。")?;
            match action.as_str() {
                "reload" => {
                    let current_url = view.url().map_err(|_| "网页地址不可用。")?;
                    state(&app, current_url.as_str(), "loading", "");
                    view.reload()
                }
                "back" => view.eval("history.back()"),
                "forward" => view.eval("history.forward()"),
                _ => {
                    let mut s = app.state::<BrowserState>().0.lock().unwrap().clone();
                    s["phase"] = json!("stopped");
                    *app.state::<BrowserState>().0.lock().unwrap() = s;
                    view.eval("window.stop()")
                }
            }
            .map_err(|_| "网页操作失败，请重试。")?;
        }
        "external" => {
            let url = parse(url.as_deref().unwrap_or(""))?;
            #[cfg(target_os = "macos")]
            let mut command = std::process::Command::new("/usr/bin/open");
            #[cfg(target_os = "windows")]
            let mut command = {
                let mut c = std::process::Command::new("rundll32.exe");
                c.arg("url.dll,FileProtocolHandler");
                c
            };
            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            let mut command = std::process::Command::new("xdg-open");
            if !command
                .arg(url.as_str())
                .status()
                .map_err(|_| "系统浏览器打开失败。")?
                .success()
            {
                return Err("系统浏览器打开失败。".into());
            }
        }
        _ => return Err("未知浏览器操作。".into()),
    }
    Ok(json!(null))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn remote_view_cannot_invoke_or_inherit_main_capabilities() {
        assert!(trusted_caller("main"));
        for label in ["browser-panel", "yuandian-console", "law-database", ""] {
            assert!(!trusted_caller(label));
        }
        let capability: Value =
            serde_json::from_str(include_str!("../capabilities/default.json")).unwrap();
        assert_eq!(capability["webviews"], json!(["main"]));
        assert!(capability.get("windows").is_none());
        assert!(capability.get("remote").is_none());
    }
    #[test]
    fn public_http_only() {
        for url in [
            "https://example.com/a?q=b",
            "http://www.court.gov.cn",
            "https://8.8.8.8",
            "https://[2606:4700:4700::1111]/",
            "https://[2001:4860:4860::8888]/",
            "https://[::ffff:8.8.8.8]/",
        ] {
            assert!(parse(url).is_ok(), "{url}");
        }
        for url in [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,test",
            "tauri://localhost",
            "https://user:pass@example.com",
            "http://127.1",
            "http://0x7f000001",
            "http://localhost.",
            "http://host.local",
            "http://10.0.0.1",
            "http://192.168.1.1",
            "http://169.254.169.254",
            "http://[::1]",
            "http://[::ffff:127.0.0.1]",
            "http://100.64.0.1",
            "http://[fc00::1]",
            "http://[fe80::1]",
            "http://[ff02::1]",
            "https://[2001:db8::1]",
            "https://[2002::1]",
            "https://[3fff::1]",
        ] {
            assert!(parse(url).is_err(), "{url}");
        }
    }
}
