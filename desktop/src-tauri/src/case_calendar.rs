//! Application-lifetime reminders. No OS jobs or notifications are scheduled in advance.
//! Calendar content stays in the authenticated local runtime; the OS sees only a count.
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::Duration;

#[derive(Default)]
pub struct ReminderState {
    pub stop: Arc<AtomicBool>,
    pub notification_failed: Arc<AtomicBool>,
}

fn count_from_response(value: &serde_json::Value) -> Option<u64> {
    value.get("count")?.as_u64().filter(|count| *count <= 5000)
}

fn local_client() -> reqwest::Result<reqwest::blocking::Client> {
    // The updater enables reqwest's rustls-no-provider feature. Rustls still
    // initializes for HTTP-only clients, so reuse the locked ring provider.
    // An existing provider installed by the host remains authoritative.
    let _ = rustls::crypto::ring::default_provider().install_default();
    reqwest::blocking::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(5))
        .build()
}

pub fn start(app: &tauri::AppHandle, port: u16, token: String) {
    use tauri::Manager;
    let state = app.state::<ReminderState>();
    let stop = state.stop.clone();
    let failed = state.notification_failed.clone();
    let identifier = app.config().identifier.clone();
    let desktop = app.state::<crate::desktop_preferences::DesktopState>();
    let enabled = desktop.calendar_enabled.clone();
    let sound = desktop.sound.clone();
    std::thread::spawn(move || {
        let Ok(client) = local_client() else {
            failed.store(true, Ordering::Relaxed);
            return;
        };
        let url = format!("http://127.0.0.1:{port}/v1/case-calendar/reminders/claim");
        #[cfg(target_os = "macos")]
        let application_ready =
            match crate::desktop_preferences::ensure_notification_application(&identifier) {
                Ok(()) => true,
                Err(error) => {
                    eprintln!("[calendar] notification identity unavailable: {error}");
                    false
                }
            };
        #[cfg(not(target_os = "macos"))]
        let application_ready = true;
        while !stop.load(Ordering::Relaxed) {
            // The transaction marks a single desktop attempt, not user acknowledgement.
            // Failures remain visible in the persistent in-app reminder list.
            let count = if enabled.load(Ordering::Relaxed) {
                client
                    .post(&url)
                    .header("X-VeriLex-Token", &token)
                    .send()
                    .ok()
                    .filter(|response| response.status().is_success())
                    .and_then(|response| response.json::<serde_json::Value>().ok())
                    .and_then(|value| count_from_response(&value))
            } else {
                None
            };
            if let Some(count) = count.filter(|count| *count > 0) {
                if stop.load(Ordering::Relaxed) {
                    break;
                }
                let sent = application_ready
                    && enabled.load(Ordering::Relaxed)
                    && crate::desktop_preferences::show_notification(
                        &identifier,
                        &format!("有 {count} 项日程到了提醒时间，请打开案件日程查看。"),
                        sound.load(Ordering::Relaxed),
                    )
                    .is_ok();
                failed.store(!sent, Ordering::Relaxed);
            }
            // Remains active when WebView timers are suspended on minimization.
            for _ in 0..20 {
                if stop.load(Ordering::Relaxed) {
                    return;
                }
                std::thread::sleep(Duration::from_millis(500));
            }
        }
    });
}

#[tauri::command]
pub fn case_calendar_notification_failed(state: tauri::State<'_, ReminderState>) -> bool {
    state.notification_failed.load(Ordering::Relaxed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loopback_client_initializes_with_the_updater_feature_set() {
        assert!(local_client().is_ok());
    }

    #[test]
    fn only_valid_counts_can_trigger_a_notification() {
        assert_eq!(
            count_from_response(&serde_json::json!({"count": 2})),
            Some(2)
        );
        assert_eq!(
            count_from_response(&serde_json::json!({"count": 0})),
            Some(0)
        );
        for value in [
            serde_json::json!({"count": -1}),
            serde_json::json!({"count": "2"}),
            serde_json::json!({"count": 5001}),
            serde_json::json!({"error": "unavailable"}),
        ] {
            assert_eq!(count_from_response(&value), None);
        }
    }
}
