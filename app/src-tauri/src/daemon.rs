//! Sidecar management: spawn `inkterop daemon`, speak line-delimited
//! JSON-RPC over its stdio, fan responses back to callers and forward
//! notifications (sync progress etc.) as Tauri events.
//!
//! Dev builds spawn the daemon via `uv run` from the repo's core/ so the
//! Python you edit is the Python that runs; release builds spawn the
//! bundled PyInstaller sidecar next to the app executable.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::oneshot;

pub struct DaemonManager {
    inner: Arc<Inner>,
}

struct Inner {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
    pending: Mutex<HashMap<u64, oneshot::Sender<Value>>>,
    next_id: AtomicU64,
    shutting_down: AtomicBool,
}

impl DaemonManager {
    pub fn new() -> Self {
        DaemonManager {
            inner: Arc::new(Inner {
                stdin: Mutex::new(None),
                child: Mutex::new(None),
                pending: Mutex::new(HashMap::new()),
                next_id: AtomicU64::new(1),
                shutting_down: AtomicBool::new(false),
            }),
        }
    }

    fn daemon_command(app: &AppHandle) -> Command {
        if cfg!(debug_assertions) {
            // Dev: run the live Python tree via uv.
            let core = concat!(env!("CARGO_MANIFEST_DIR"), "/../../core");
            let mut cmd = Command::new("uv");
            cmd.args(["run", "--project", core, "inkterop", "daemon"]);
            cmd
        } else {
            // Release: the bundled sidecar sits next to the app binary.
            let exe_dir = tauri::process::current_binary(&app.env())
                .ok()
                .and_then(|p| p.parent().map(|d| d.to_path_buf()))
                .expect("cannot locate app binary dir");
            let mut cmd = Command::new(exe_dir.join("inkterop-daemon"));
            cmd.arg("daemon");
            cmd
        }
    }

    pub fn spawn(&self, app: &AppHandle) -> Result<(), String> {
        let mut cmd = Self::daemon_command(app);
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("failed to start inkterop daemon: {e}"))?;

        let stdout = child.stdout.take().expect("stdout piped");
        *self.inner.stdin.lock().unwrap() = child.stdin.take();
        *self.inner.child.lock().unwrap() = Some(child);

        let inner = Arc::clone(&self.inner);
        let app = app.clone();
        std::thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines() {
                let Ok(line) = line else { break };
                let Ok(msg) = serde_json::from_str::<Value>(&line) else {
                    continue;
                };
                match msg.get("id").and_then(Value::as_u64) {
                    Some(id) => {
                        if let Some(tx) =
                            inner.pending.lock().unwrap().remove(&id)
                        {
                            let _ = tx.send(msg);
                        }
                    }
                    None => {
                        // Notification: forward to the webview + tray glue.
                        let _ = app.emit("daemon-event", &msg);
                    }
                }
            }
            // stdout closed: daemon died (or we are quitting).
            inner.pending.lock().unwrap().clear();
            if !inner.shutting_down.load(Ordering::SeqCst) {
                let _ = app.emit("daemon-event",
                                 json!({"method": "daemon.died"}));
                let mgr = DaemonManager { inner: Arc::clone(&inner) };
                std::thread::sleep(Duration::from_secs(2));
                if !inner.shutting_down.load(Ordering::SeqCst) {
                    let _ = mgr.spawn(&app);
                }
            }
        });
        Ok(())
    }

    pub async fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.inner.next_id.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = oneshot::channel();
        self.inner.pending.lock().unwrap().insert(id, tx);

        let req = json!({"jsonrpc": "2.0", "id": id, "method": method,
                         "params": params});
        {
            let mut guard = self.inner.stdin.lock().unwrap();
            let stdin = guard.as_mut().ok_or("daemon not running")?;
            let line = serde_json::to_string(&req).map_err(|e| e.to_string())?;
            stdin
                .write_all(format!("{line}\n").as_bytes())
                .and_then(|_| stdin.flush())
                .map_err(|e| {
                    format!("daemon pipe broken: {e} (restarting)")
                })?;
        }

        let resp = rx.await.map_err(|_| "daemon exited mid-request".to_string())?;
        if let Some(err) = resp.get("error") {
            let msg = err
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("unknown daemon error");
            return Err(msg.to_string());
        }
        Ok(resp.get("result").cloned().unwrap_or(Value::Null))
    }

    pub fn shutdown(&self) {
        self.inner.shutting_down.store(true, Ordering::SeqCst);
        // Dropping stdin sends EOF; the daemon exits cleanly on it.
        *self.inner.stdin.lock().unwrap() = None;
        if let Some(mut child) = self.inner.child.lock().unwrap().take() {
            let deadline = std::time::Instant::now() + Duration::from_secs(3);
            loop {
                match child.try_wait() {
                    Ok(Some(_)) => break,
                    Ok(None) if std::time::Instant::now() < deadline => {
                        std::thread::sleep(Duration::from_millis(100));
                    }
                    _ => {
                        let _ = child.kill();
                        break;
                    }
                }
            }
        }
    }
}

/// Frontend entry point: every UI action is one JSON-RPC method.
#[tauri::command]
pub async fn rpc(
    state: tauri::State<'_, DaemonManager>,
    method: String,
    params: Option<Value>,
) -> Result<Value, String> {
    state.call(&method, params.unwrap_or(json!({}))).await
}

/// Tray helpers call the daemon without going through the webview.
pub async fn tray_call(app: &AppHandle, method: &str) {
    let state = app.state::<DaemonManager>();
    if let Err(e) = state.call(method, json!({})).await {
        eprintln!("tray {method} failed: {e}");
    }
}
