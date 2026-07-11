import { useCallback, useEffect, useMemo, useState } from "react";
import "./App.css";
import { api, onDaemonEvent, type DaemonEvent, type Snapshot } from "./rpc";
import Library from "./views/Library";
import Convert from "./views/Convert";
import Activity, { type ActivityEntry } from "./views/Activity";
import Settings from "./views/Settings";

type View = "library" | "convert" | "activity" | "settings";

const MAX_LOG = 500;

export default function App() {
  const [view, setView] = useState<View>("library");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [daemonUp, setDaemonUp] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [log, setLog] = useState<ActivityEntry[]>([]);
  const [lastSummary, setLastSummary] = useState<string>("");

  const refresh = useCallback(async () => {
    try {
      setSnapshot(await api.library());
      setDaemonUp(true);
    } catch {
      setDaemonUp(false);
      throw new Error("daemon not up");
    }
  }, []);

  useEffect(() => {
    // The daemon may still be starting when the webview loads; poll until
    // the first snapshot lands, then rely on events.
    let stopped = false;
    const tryOnce = () =>
      refresh().then(
        () => true,
        () => false,
      );
    (async () => {
      for (let i = 0; i < 40 && !stopped; i++) {
        if (await tryOnce()) return;
        await new Promise((r) => setTimeout(r, 1500));
      }
    })();
    return () => {
      stopped = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const un = onDaemonEvent((ev: DaemonEvent) => {
      if (ev.method === "daemon.ready") {
        setDaemonUp(true);
        refresh().catch(() => {});
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
        setLastSummary(
          `last pass: ${p.rendered} rendered, ${p.skipped} unchanged` +
            (Number(p.failed) ? `, ${p.failed} FAILED` : ""),
        );
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
  }, [refresh]);

  const failedCount = useMemo(
    () => log.filter((e) => e.event === "doc-failed").length,
    [log],
  );

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
        <button
          className={view === "library" ? "active" : ""}
          onClick={() => setView("library")}
        >
          Library
        </button>
        <button
          className={view === "convert" ? "active" : ""}
          onClick={() => setView("convert")}
        >
          Convert
        </button>
        <button
          className={view === "activity" ? "active" : ""}
          onClick={() => setView("activity")}
        >
          Activity{failedCount > 0 ? ` (${failedCount})` : ""}
        </button>
        <button
          className={view === "settings" ? "active" : ""}
          onClick={() => setView("settings")}
        >
          Settings
        </button>

        <div className="sidebar-foot">
          <button
            className="primary"
            disabled={!daemonUp || syncing}
            onClick={async () => {
              setSyncing(true);
              try {
                await api.syncNow();
              } finally {
                setSyncing(false);
                refresh().catch(() => {});
              }
            }}
          >
            {syncing ? "Syncing…" : "Sync Now"}
          </button>
          <div className="statusline">
            {daemonUp ? lastSummary || "engine ready" : "engine starting…"}
          </div>
        </div>
      </nav>

      <main className="content">
        {view === "library" && (
          <Library snapshot={snapshot} onChanged={() => refresh().catch(() => {})} />
        )}
        {view === "convert" && <Convert />}
        {view === "activity" && <Activity entries={log} />}
        {view === "settings" && <Settings onChanged={() => refresh().catch(() => {})} />}
      </main>
    </div>
  );
}
