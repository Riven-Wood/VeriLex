// Limited Carbon hotkey lifecycle adapted from tauri-apps/global-hotkey
// src/platform_impl/macos/{mod,ffi}.rs (dev, inspected 2026-10-12), MIT.
// Copyright (c) 2022-2022 Tauri Programme within The Commons Conservancy.
// License: third_party/licenses/commands-shortcuts/global-hotkey-MIT.txt.
// Changes: one opt-in fixed wake key, main-thread state, main-window-only IPC;
// no media keys/event taps, arbitrary keys, or cross-platform registration.
use tauri::{AppHandle, Webview};

#[tauri::command]
pub async fn set_global_wake_shortcut(
    app: AppHandle,
    webview: Webview,
    enabled: bool,
) -> Result<bool, String> {
    if webview.label() != "main" {
        return Err("仅主窗口可更改快捷键".into());
    }
    #[cfg(target_os = "macos")]
    {
        let (tx, rx) = std::sync::mpsc::channel();
        let handle = app.clone();
        app.run_on_main_thread(move || {
            let _ = tx.send(mac::set(handle, enabled));
        })
        .map_err(|_| "无法更改系统快捷键".to_string())?;
        tauri::async_runtime::spawn_blocking(move || {
            rx.recv().map_err(|_| "无法更改系统快捷键".to_string())
        })
        .await
        .map_err(|_| "无法更改系统快捷键".to_string())??
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (app, enabled);
        Err("此平台不支持系统唤起快捷键".into())
    }
}

#[cfg(target_os = "macos")]
mod mac {
    use std::{cell::RefCell, ffi::c_void, ptr};
    use tauri::{AppHandle, Manager};
    type Ref = *mut c_void;
    #[repr(C, packed(2))]
    #[derive(Default)]
    struct HotKeyId {
        signature: u32,
        id: u32,
    }
    #[repr(C, packed(2))]
    struct EventSpec {
        class: u32,
        kind: u32,
    }
    #[link(name = "Carbon", kind = "framework")]
    extern "C" {
        fn GetApplicationEventTarget() -> Ref;
        fn InstallEventHandler(
            target: Ref,
            callback: Option<unsafe extern "C" fn(Ref, Ref, Ref) -> i32>,
            count: u32,
            types: *const EventSpec,
            data: Ref,
            handler: *mut Ref,
        ) -> i32;
        fn RegisterEventHotKey(
            key: u32,
            modifiers: u32,
            id: HotKeyId,
            target: Ref,
            options: u32,
            hotkey: *mut Ref,
        ) -> i32;
        fn UnregisterEventHotKey(hotkey: Ref) -> i32;
        fn GetEventParameter(
            event: Ref,
            name: u32,
            kind: u32,
            actual_kind: *mut u32,
            size: usize,
            actual_size: *mut usize,
            data: Ref,
        ) -> i32;
    }
    const SIGNATURE: u32 = u32::from_be_bytes(*b"VLWK");
    struct Registration {
        hotkey: Ref,
        handler: Ref,
        app: Box<AppHandle>,
    }
    thread_local! { static REGISTRATION: RefCell<Option<Registration>> = const { RefCell::new(None) }; }
    unsafe extern "C" fn on_key(_next: Ref, event: Ref, data: Ref) -> i32 {
        let mut id = HotKeyId::default();
        if GetEventParameter(
            event,
            u32::from_be_bytes(*b"----"),
            u32::from_be_bytes(*b"hkid"),
            ptr::null_mut(),
            std::mem::size_of::<HotKeyId>(),
            ptr::null_mut(),
            &mut id as *mut _ as Ref,
        ) != 0
            || id.signature != SIGNATURE
            || id.id != 1
            || data.is_null()
        {
            return -9874;
        }
        let app = &*(data as *const AppHandle);
        if let Some(window) = app.get_window("main") {
            let _ = app.show();
            let _ = window.unminimize();
            let _ = window.show();
            let _ = window.set_focus();
        }
        0
    }
    pub fn set(app: AppHandle, enabled: bool) -> Result<bool, String> {
        REGISTRATION.with(|cell| {
            let mut state = cell.borrow_mut();
            unsafe {
                if state.is_none() && enabled {
                    let mut registration = Registration {
                        hotkey: ptr::null_mut(),
                        handler: ptr::null_mut(),
                        app: Box::new(app),
                    };
                    let spec = EventSpec {
                        class: u32::from_be_bytes(*b"keyb"),
                        kind: 5,
                    };
                    if InstallEventHandler(
                        GetApplicationEventTarget(),
                        Some(on_key),
                        1,
                        &spec,
                        &mut *registration.app as *mut _ as Ref,
                        &mut registration.handler,
                    ) != 0
                    {
                        return Err("无法注册快捷键处理器".into());
                    }
                    // Keep one handler and its stable callback data until process exit.
                    // Disabling unregisters the OS hotkey, so no key remains reserved.
                    *state = Some(registration);
                }
                let Some(registration) = state.as_mut() else {
                    return Ok(false);
                };
                if enabled == !registration.hotkey.is_null() {
                    return Ok(enabled);
                }
                if !enabled {
                    if UnregisterEventHotKey(registration.hotkey) != 0 {
                        return Err("无法释放系统快捷键".into());
                    }
                    registration.hotkey = ptr::null_mut();
                    return Ok(false);
                }
                // Physical V + Command + Option + Shift. No input monitoring.
                // kEventHotKeyExclusive rejects an existing registration instead of sharing it.
                let mut hotkey = ptr::null_mut();
                if RegisterEventHotKey(
                    0x09,
                    256 | 512 | 2048,
                    HotKeyId {
                        signature: SIGNATURE,
                        id: 1,
                    },
                    GetApplicationEventTarget(),
                    1,
                    &mut hotkey,
                ) != 0
                {
                    return Err("快捷键已被占用或无法注册".into());
                }
                registration.hotkey = hotkey;
                Ok(true)
            }
        })
    }
}
