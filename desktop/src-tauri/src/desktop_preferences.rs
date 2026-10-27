//! Native lifecycle monitor: remains active when the WebView is minimized.
//! Only opaque event ids and kinds cross this boundary, never conversation text.
use serde_json::{json, Value};
use std::collections::HashSet;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::Duration;
use tauri::Manager;

#[derive(Default)]
pub struct DesktopState {
    pub stop: Arc<AtomicBool>,
    pub awake: Arc<AtomicBool>,
    pub power_failed: Arc<AtomicBool>,
    pub notification_failed: Arc<AtomicBool>,
    pub calendar_enabled: Arc<AtomicBool>,
    pub sound: Arc<AtomicBool>,
}

#[cfg(target_os = "macos")]
pub(crate) fn ensure_notification_application(identifier: &str) -> Result<(), String> {
    static IDENTITY: crate::notification_identity::NotificationIdentity =
        crate::notification_identity::NotificationIdentity::new();
    IDENTITY.ensure(|| {
        notify_rust::set_application(identifier)
            .map_err(|error| format!("无法初始化系统通知：{error}"))
    })
}

pub fn show_notification(identifier: &str, body: &str, sound: bool) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    ensure_notification_application(identifier)?;
    let mut notification = notify_rust::Notification::new();
    notification.summary("VeriLex").body(body);
    #[cfg(windows)]
    notification.app_id(identifier);
    if sound {
        #[cfg(target_os = "macos")]
        notification.sound_name("default");
        #[cfg(windows)]
        notification.sound_name("Default");
        #[cfg(target_os = "linux")]
        notification.sound_name("message-new-instant");
    }
    notification
        .show()
        .map(|_| ())
        .map_err(|_| "无法向系统发送通知，请检查系统通知设置".to_string())
}

#[tauri::command]
pub fn desktop_test_notification(app: tauri::AppHandle) -> Result<(), String> {
    let state = app.state::<DesktopState>();
    let result = show_notification(
        &app.config().identifier,
        "这是一条测试通知。任务提醒不会包含案件内容。",
        state.sound.load(Ordering::Relaxed),
    );
    state
        .notification_failed
        .store(result.is_err(), Ordering::Relaxed);
    result
}

#[tauri::command]
pub fn desktop_status(state: tauri::State<'_, DesktopState>) -> Value {
    json!({"awake":state.awake.load(Ordering::Relaxed),
        "power_failed":state.power_failed.load(Ordering::Relaxed),
        "notification_failed":state.notification_failed.load(Ordering::Relaxed),
        "power_supported":cfg!(any(target_os="macos", windows))})
}

#[derive(Default)]
struct PowerGuard {
    #[cfg(target_os = "macos")]
    child: Option<std::process::Child>,
    active: bool,
}
impl PowerGuard {
    fn set(&mut self, enabled: bool) -> bool {
        #[cfg(target_os = "macos")]
        {
            if let Some(child) = self.child.as_mut() {
                if !matches!(child.try_wait(), Ok(None)) {
                    self.child = None;
                    self.active = false;
                }
            }
            if enabled && !self.active {
                self.child = std::process::Command::new("/usr/bin/caffeinate")
                    .args(["-i", "-w", &std::process::id().to_string()])
                    .stdin(std::process::Stdio::null())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .spawn()
                    .ok();
                self.active = self.child.is_some();
            } else if !enabled {
                if let Some(mut child) = self.child.take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                self.active = false;
            }
        }
        #[cfg(windows)]
        {
            #[link(name = "kernel32")]
            extern "system" {
                fn SetThreadExecutionState(flags: u32) -> u32;
            }
            // All calls run on this same native thread, including release.
            if enabled != self.active {
                let result =
                    unsafe { SetThreadExecutionState(0x80000000 | if enabled { 1 } else { 0 }) };
                if result != 0 {
                    self.active = enabled;
                }
            }
        }
        self.active == enabled
    }
}
impl Drop for PowerGuard {
    fn drop(&mut self) {
        self.set(false);
    }
}

#[derive(Default)]
struct EventTracker {
    seen: Option<HashSet<String>>,
    watermark: String,
}
impl EventTracker {
    fn observe(&mut self, events: &[Value]) -> Vec<String> {
        let now: HashSet<String> = events
            .iter()
            .filter_map(|e| e["id"].as_str().map(str::to_owned))
            .collect();
        let kinds = self
            .seen
            .as_ref()
            .map(|seen| {
                events
                    .iter()
                    .filter(|e| {
                        e["id"].as_str().is_some_and(|id| !seen.contains(id))
                            && e["created_at"].as_str().unwrap_or("") >= self.watermark.as_str()
                    })
                    .filter_map(|e| e["type"].as_str().map(str::to_owned))
                    .collect()
            })
            .unwrap_or_default();
        if let Some(latest) = events.iter().filter_map(|e| e["created_at"].as_str()).max() {
            if latest > self.watermark.as_str() {
                self.watermark = latest.to_owned();
            }
        }
        self.seen = Some(now);
        kinds
    }
}

pub fn start(app: &tauri::AppHandle, port: u16, token: String) {
    let app = app.clone();
    std::thread::spawn(move || {
        let state = app.state::<DesktopState>();
        let _ = rustls::crypto::ring::default_provider().install_default();
        let Ok(client) = reqwest::blocking::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(4))
            .build()
        else {
            return;
        };
        let url = format!("http://127.0.0.1:{port}/v1/preferences/desktop-activity");
        let mut tracker = EventTracker::default();
        let mut power = PowerGuard::default();
        while !state.stop.load(Ordering::Relaxed) {
            let snapshot = client
                .get(&url)
                .header("X-VeriLex-Token", &token)
                .send()
                .ok()
                .filter(|r| r.status().is_success())
                .and_then(|r| r.json::<Value>().ok());
            if state.stop.load(Ordering::Relaxed) {
                break;
            }
            let mut want_awake = false;
            if let Some(snapshot) = snapshot {
                let pref = &snapshot["preferences"];
                let enabled = |key: &str| pref[key].as_u64() == Some(1);
                state
                    .calendar_enabled
                    .store(enabled("notify_calendar"), Ordering::Relaxed);
                state
                    .sound
                    .store(enabled("notification_sound"), Ordering::Relaxed);
                want_awake =
                    enabled("keep_awake") && snapshot["active_count"].as_u64().unwrap_or(0) > 0;
                if let Some(events) = snapshot["events"].as_array() {
                    let kinds = tracker.observe(events);
                    let background = app
                        .get_window("main")
                        .map(|w| !w.is_focused().unwrap_or(true))
                        .unwrap_or(false);
                    if background {
                        for (kind, key, body) in [
                            (
                                "turn_completed",
                                "notify_completed",
                                "任务已完成，请回到 VeriLex 查看结果。",
                            ),
                            (
                                "turn_failed",
                                "notify_failed",
                                "任务未能完成，请回到 VeriLex 查看并重试。",
                            ),
                            (
                                "approval_requested",
                                "notify_approval",
                                "任务需要你的确认，请回到 VeriLex 处理。",
                            ),
                        ] {
                            if enabled(key) && kinds.iter().any(|k| k == kind) {
                                let failed = show_notification(
                                    &app.config().identifier,
                                    body,
                                    enabled("notification_sound"),
                                )
                                .is_err();
                                state.notification_failed.store(failed, Ordering::Relaxed);
                            }
                        }
                    }
                }
            }
            let success = power.set(want_awake);
            state.awake.store(power.active, Ordering::Relaxed);
            state.power_failed.store(!success, Ordering::Relaxed);
            for _ in 0..10 {
                if state.stop.load(Ordering::Relaxed) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
        }
        power.set(false);
        state.awake.store(false, Ordering::Relaxed);
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn startup_history_is_silent_and_repeated_polls_are_deduplicated() {
        let mut tracker = EventTracker::default();
        let old = json!({"id":"old","type":"turn_completed","created_at":"2026-10-12T00:00:00Z"});
        let new =
            json!({"id":"new","type":"approval_requested","created_at":"2026-10-12T00:00:01Z"});
        assert!(tracker.observe(&[old.clone()]).is_empty());
        assert_eq!(
            tracker.observe(&[new.clone(), old.clone()]),
            vec!["approval_requested"]
        );
        assert!(tracker.observe(&[new, old]).is_empty());
    }
    #[test]
    fn deleting_recent_history_does_not_notify_older_events() {
        let mut tracker = EventTracker::default();
        tracker.observe(&[
            json!({"id":"new","type":"turn_completed","created_at":"2026-10-12T00:01:00Z"}),
        ]);
        assert!(tracker
            .observe(&[
                json!({"id":"older","type":"turn_completed","created_at":"2026-10-12T00:00:00Z"})
            ])
            .is_empty());
    }
    #[cfg(target_os = "macos")]
    #[test]
    fn power_guard_releases_its_native_process() {
        let mut power = PowerGuard::default();
        assert!(power.set(true));
        assert!(power.child.is_some());
        assert!(power.set(false));
        assert!(power.child.is_none());
        assert!(!power.active);
    }
    #[test]
    fn disabled_power_guard_does_not_start_a_process() {
        let mut power = PowerGuard::default();
        assert!(power.set(false));
        assert!(!power.active);
    }
}
