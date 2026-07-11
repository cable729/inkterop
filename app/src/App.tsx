import { useCallback, useEffect, useState } from "react";
import "./App.css";
import {
  api,
  onDaemonEvent,
  timeAgo,
  type DaemonEvent,
  type Snapshot,
} from "./rpc";
import Library from "./views/Library";
import Convert, { type ConvertRequest } from "./views/Convert";
import History, { type ActivityEntry } from "./views/History";
import Settings from "./views/Settings";
import { SyncIcon } from "./icons";

type View = "library" | "convert" | "history" | "settings";

const MAX_LOG = 500;

export default function App() {
  const [view, setView] = useState<View>("library");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [daemonUp, setDaemonUp] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [log, setLog] = useState<ActivityEntry[]>([]);
  const [lastSync, setLastSync] = useState<number | null>(null);
  const [lastFailed, setLastFailed] = useState(0);
  const [convertRequest, setConvertRequest] = useState<ConvertRequest | null>(
    null,
  );
  const [, bumpClock] = useState(0);

  const refresh = useCallback(async () => {
    const snap = await api.library(); // throws while the daemon is down
    setSnapshot(snap);
    setDaemonUp(true);
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const st = await api.status();
      if (typeof st.time === "number" && st.state !== "syncing")
        setLastSync(st.time as number);
      if (typeof st.failed === "number") setLastFailed(st.failed as number);
    } catch {
      /* daemon still starting */
    }
  }, []);

  useEffect(() => {
    // The daemon may still be starting when the webview loads; poll until
    // the first snapshot lands, then rely on events.
    let stopped = false;
    (async () => {
      for (let i = 0; i < 40 && !stopped; i++) {
        try {
          await refresh();
          await refreshStatus();
          return;
        } catch {
          await new Promise((r) => setTimeout(r, 1500));
        }
      }
    })();
    // Keep "x mins ago" fresh.
    const tick = setInterval(() => bumpClock((n) => n + 1), 30000);
    return () => {
      stopped = true;
      clearInterval(tick);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const un = onDaemonEvent((ev: DaemonEvent) => {
      if (ev.method === "daemon.ready") {
        setDaemonUp(true);
        refresh().catch(() => {});
        refreshStatus();
        return;
      }
      if (ev.method === "daemon.died") {
        setDaemonUp(false);
        return;
      }
      if (ev.method !== "sync.progress") return;
      const p = ev.params ?? {};
      const kind = String(p.event ?? "");
      if (kind === "pass-started") setSyncing(true);
      if (kind === "pass-finished") {
        setSyncing(false);
        setLastSync(Math.floor(Date.now() / 1000));
        setLastFailed(Number(p.failed) || 0);
        refresh().catch(() => {});
      }
      setLog((old) =>
        [{ time: Date.now(), event: kind, detail: p }, ...old].slice(
          0,
          MAX_LOG,
        ),
      );
    });
    return () => {
      un.then((f) => f());
    };
  }, [refresh, refreshStatus]);

  function startConvert(req: ConvertRequest) {
    setConvertRequest(req);
    setView("convert");
  }

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand">
          <span
            className="brand-dot"
            data-state={daemonUp ? (syncing ? "syncing" : "ok") : "down"}
          />
          Inkterop
        </div>
        {(
          [
            ["library", "Library"],
            ["convert", "Convert"],
            ["history", "Sync History"],
            ["settings", "Settings"],
          ] as const
        ).map(([v, label]) => (
          <button
            key={v}
            className={view === v ? "active" : ""}
            onClick={() => setView(v)}
          >
            {label}
          </button>
        ))}

        <div className="sidebar-foot">
          <button
            className="primary"
            disabled={!daemonUp || syncing}
            onClick={() => {
              setView("history"); // watch it happen
              setSyncing(true);
              api
                .syncNow()
                .catch(() => {})
                .finally(() => {
                  setSyncing(false);
                  refresh().catch(() => {});
                });
            }}
          >
            <SyncIcon
              width={13}
              height={13}
              className={syncing ? "spin" : ""}
            />{" "}
            {syncing ? "Syncing…" : "Sync Now"}
          </button>
          <button
            className="statusline linkish"
            title="Open sync history"
            onClick={() => setView("history")}
          >
            {!daemonUp
              ? "engine starting…"
              : syncing
                ? "syncing now…"
                : `synced ${timeAgo(lastSync)}` +
                  (lastFailed ? ` · ${lastFailed} failed` : "")}
          </button>
        </div>
      </nav>

      <main className="content">
        {view === "library" && (
          <Library
            snapshot={snapshot}
            onChanged={() => refresh().catch(() => {})}
            onConvert={startConvert}
          />
        )}
        {view === "convert" && (
          <Convert
            request={convertRequest}
            onRequestConsumed={() => setConvertRequest(null)}
          />
        )}
        {view === "history" && (
          <History entries={log} syncing={syncing} />
        )}
        {view === "settings" && (
          <Settings onChanged={() => refresh().catch(() => {})} />
        )}
      </main>
    </div>
  );
}
