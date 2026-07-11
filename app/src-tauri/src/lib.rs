mod daemon;

use daemon::DaemonManager;
use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Listener, Manager, WindowEvent};

/// "Close hides to the menu bar" (default) vs "close quits".
static CLOSE_TO_TRAY: AtomicBool = AtomicBool::new(true);
static PAUSED: AtomicBool = AtomicBool::new(false);

#[tauri::command]
fn set_close_to_tray(enabled: bool) {
    CLOSE_TO_TRAY.store(enabled, Ordering::SeqCst);
}

fn show_main_window(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        #[cfg(target_os = "macos")]
        let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
        let _ = win.show();
        let _ = win.set_focus();
    }
}

fn hide_main_window(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.hide();
    }
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Inkterop", true, None::<&str>)?;
    let sync_now = MenuItem::with_id(app, "sync", "Sync Now", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "Pause Syncing", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Inkterop", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &sync_now, &pause, &sep, &quit])?;

    TrayIconBuilder::with_id("main-tray")
        .icon(app.default_window_icon().unwrap().clone())
        .icon_as_template(true)
        .tooltip("Inkterop")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(move |app, event| match event.id.as_ref() {
            "open" => show_main_window(app),
            "sync" => {
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    daemon::tray_call(&app, "sync.now").await;
                });
            }
            "pause" => {
                let was_paused = PAUSED.fetch_xor(true, Ordering::SeqCst);
                let method = if was_paused { "sync.resume" } else { "sync.pause" };
                let _ = pause.set_text(if was_paused {
                    "Pause Syncing"
                } else {
                    "Resume Syncing"
                });
                let app = app.clone();
                let method = method.to_string();
                tauri::async_runtime::spawn(async move {
                    daemon::tray_call(&app, &method).await;
                });
            }
            "quit" => {
                app.state::<DaemonManager>().shutdown();
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init());

    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    {
        builder = builder.plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ));
    }

    builder
        .manage(DaemonManager::new())
        .invoke_handler(tauri::generate_handler![
            daemon::rpc,
            set_close_to_tray
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            app.state::<DaemonManager>().spawn(&handle)?;
            build_tray(&handle)?;

            // Keep the tray tooltip in step with sync activity.
            let tray_handle = handle.clone();
            handle.listen("daemon-event", move |event| {
                let Ok(msg) = serde_json::from_str::<Value>(event.payload())
                else {
                    return;
                };
                let ev = msg
                    .pointer("/params/event")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                let tip = match ev {
                    "pass-started" => Some("Inkterop — syncing…".to_string()),
                    "pass-finished" => {
                        let n = msg
                            .pointer("/params/rendered")
                            .and_then(Value::as_u64)
                            .unwrap_or(0);
                        Some(format!("Inkterop — idle (last pass: {n} rendered)"))
                    }
                    _ => None,
                };
                if let (Some(tip), Some(tray)) =
                    (tip, tray_handle.tray_by_id("main-tray"))
                {
                    let _ = tray.set_tooltip(Some(&tip));
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" && CLOSE_TO_TRAY.load(Ordering::SeqCst)
                {
                    api.prevent_close();
                    hide_main_window(&window.app_handle());
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                app.state::<DaemonManager>().shutdown();
            }
        });
}
