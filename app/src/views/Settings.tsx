import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { enable, disable, isEnabled } from "@tauri-apps/plugin-autostart";
import { api, SINK_FORMATS, type AppConfig } from "../rpc";

function LegacyDaemon() {
  const [loaded, setLoaded] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    invoke<boolean>("legacy_daemon_loaded").then(setLoaded).catch(() => {});
  }, []);
  if (!loaded) return null;
  return (
    <>
      <h2>Background daemon</h2>
      <p className="hint">
        The old command-line watch daemon (launchd:{" "}
        <code>com.inkterop.watch</code>) is still running. The app now does
        the watching — running both would sync twice.
      </p>
      <button
        onClick={async () => {
          try {
            await invoke("disable_legacy_daemon");
            setLoaded(false);
          } catch (e) {
            setError(String(e));
          }
        }}
      >
        Disable the old daemon
      </button>
      {error && <p className="hint">Failed: {error}</p>}
    </>
  );
}

export default function Settings({ onChanged }: { onChanged: () => void }) {
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [autostart, setAutostart] = useState(false);
  const [closeToTray, setCloseToTray] = useState(
    localStorage.getItem("closeToTray") !== "false",
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.configGet().then(setCfg).catch(console.error);
    isEnabled().then(setAutostart).catch(() => {});
  }, []);

  async function apply(changes: Partial<AppConfig>) {
    setSaving(true);
    try {
      setCfg(await api.configSet(changes));
      onChanged();
    } finally {
      setSaving(false);
    }
  }

  if (!cfg) return <div className="empty">Loading configuration…</div>;

  return (
    <div className="settings">
      <h2>Output</h2>
      <div className="field-row">
        <span className="mono grow" title={cfg.output_dir}>
          {cfg.output_dir}
        </span>
        <button
          disabled={saving}
          onClick={async () => {
            const dir = await open({ directory: true, title: "Choose output folder" });
            if (typeof dir === "string") await apply({ output_dir: dir });
          }}
        >
          Change…
        </button>
      </div>
      <label className="field">
        Default format
        <select
          value={cfg.default_format}
          disabled={saving}
          onChange={(e) => apply({ default_format: e.target.value })}
        >
          {SINK_FORMATS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </label>

      <h2>What to sync</h2>
      {(
        [
          ["notebooks", "Notebooks"],
          ["pdfs", "Annotated PDFs (handwriting only for now)"],
          ["epubs", "Annotated EPUBs (handwriting only for now)"],
        ] as const
      ).map(([key, label]) => (
        <label className="row" key={key}>
          <input
            type="checkbox"
            checked={cfg[key]}
            disabled={saving}
            onChange={(e) => apply({ [key]: e.target.checked })}
          />
          {label}
        </label>
      ))}

      <h2>Sources</h2>
      <label className="row">
        <input
          type="checkbox"
          checked={cfg.source_remarkable}
          disabled={saving}
          onChange={(e) => apply({ source_remarkable: e.target.checked })}
        />
        reMarkable (desktop app cache)
      </label>
      <label className="row">
        <input
          type="checkbox"
          checked={cfg.source_goodnotes}
          disabled={saving}
          onChange={(e) => apply({ source_goodnotes: e.target.checked })}
        />
        GoodNotes app library (experimental)
      </label>
      <label className="row">
        <input
          type="checkbox"
          checked={cfg.source_notability}
          disabled={saving}
          onChange={(e) => apply({ source_notability: e.target.checked })}
        />
        Notability app library (experimental)
      </label>

      <h3>Watched folders</h3>
      <p className="hint">
        Any folder of note files inkterop can read (.goodnotes, .ntb, .sba,
        .xopp, …) — e.g. an iCloud Drive folder an iPad app exports into.
      </p>
      <ul className="folderlist">
        {cfg.source_folders.map((f, i) => (
          <li key={i}>
            <span className="mono grow">{f.path}</span>
            <button
              className="linkish"
              disabled={saving}
              onClick={() =>
                apply({
                  source_folders: cfg.source_folders.filter(
                    (_, j) => j !== i,
                  ),
                })
              }
            >
              Remove
            </button>
          </li>
        ))}
      </ul>
      <button
        disabled={saving}
        onClick={async () => {
          const dir = await open({ directory: true, title: "Watch folder" });
          if (typeof dir === "string")
            await apply({
              source_folders: [...cfg.source_folders, { path: dir }],
            });
        }}
      >
        Add folder…
      </button>

      <h2>Rendering</h2>
      <label className="field">
        Page sizing
        <select
          value={cfg.normalize}
          disabled={saving}
          onChange={(e) => apply({ normalize: e.target.value })}
        >
          <option value="uniform">uniform — every page the same size</option>
          <option value="native">native — keep grown page sizes</option>
        </select>
      </label>
      <label className="field">
        Pen style
        <select
          value={cfg.pen_style}
          disabled={saving}
          onChange={(e) => apply({ pen_style: e.target.value })}
        >
          <option value="faithful">faithful — device-like ink</option>
          <option value="rmc">rmc — community-renderer look</option>
        </select>
      </label>

      <LegacyDaemon />

      <h2>App</h2>
      <label className="row">
        <input
          type="checkbox"
          checked={autostart}
          onChange={async (e) => {
            if (e.target.checked) await enable();
            else await disable();
            setAutostart(await isEnabled());
          }}
        />
        Launch Inkterop at login
      </label>
      <label className="row">
        <input
          type="checkbox"
          checked={closeToTray}
          onChange={async (e) => {
            const v = e.target.checked;
            setCloseToTray(v);
            localStorage.setItem("closeToTray", String(v));
            await invoke("set_close_to_tray", { enabled: v });
          }}
        />
        Closing the window keeps Inkterop running in the menu bar
      </label>
    </div>
  );
}
