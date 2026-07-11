import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import {
  api,
  thumbnailUrl,
  SINK_FORMATS,
  type DocInfo,
  type Snapshot,
} from "../rpc";

type Mode = "columns" | "grid" | "list";

interface Props {
  snapshot: Snapshot | null;
  onChanged: () => void;
}

/* ---------- folder tree helpers ---------- */

function splitFolder(f: string): string[] {
  return f === "" ? [] : f.split("/");
}

/** Immediate subfolder names + docs directly at `path` for one source. */
function levelAt(docs: DocInfo[], path: string[]) {
  const prefix = path.join("/");
  const folders = new Set<string>();
  const here: DocInfo[] = [];
  for (const d of docs) {
    const segs = splitFolder(d.folder);
    if (prefix === "" || d.folder === prefix || d.folder.startsWith(prefix + "/")) {
      const rel = segs.slice(path.length);
      if (d.folder === prefix) here.push(d);
      else if (
        prefix === "" ? true : d.folder.startsWith(prefix + "/")
      ) {
        if (rel.length > 0) folders.add(rel[0]);
      }
    }
  }
  here.sort((a, b) => a.name.localeCompare(b.name));
  return { folders: [...folders].sort(), docs: here };
}

function countUnder(docs: DocInfo[], path: string[]): number {
  const prefix = path.join("/");
  return docs.filter(
    (d) => d.folder === prefix || d.folder.startsWith(prefix + "/"),
  ).length;
}

/* ---------- thumbnails ---------- */

function Thumb({ doc, className }: { doc: DocInfo; className: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let gone = false;
    setSrc(null);
    setFailed(false);
    api
      .thumbnail(doc.key)
      .then((r) => {
        if (!gone) setSrc(thumbnailUrl(r.path));
      })
      .catch(() => {
        if (!gone) setFailed(true);
      });
    return () => {
      gone = true;
    };
  }, [doc.key, doc.mtime]);
  if (failed) return <div className={`${className} thumb-missing`}>—</div>;
  if (!src) return <div className={`${className} thumb-loading`} />;
  return <img className={className} src={src} alt="" loading="lazy" />;
}

/* ---------- shared doc details / rule editing ---------- */

function DocDetails({
  doc,
  outputDir,
  onChanged,
  withPreview,
}: {
  doc: DocInfo;
  outputDir: string;
  onChanged: () => void;
  withPreview?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  async function setRule(fields: Record<string, unknown>) {
    setBusy(true);
    try {
      await api.setDocRule(doc.source, doc.id, fields);
      onChanged();
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="doc-details">
      {withPreview && <Thumb doc={doc} className="thumb preview-thumb" />}
      <h3 title={doc.name}>{doc.name}</h3>
      <div className="kv">
        <span>Status</span>
        <span className={`badge badge-${doc.state}`}>{doc.state}</span>
        <span>Pages</span>
        <span>{doc.pages ?? "—"}</span>
        <span>Kind</span>
        <span>{doc.kind}</span>
        <span>Modified</span>
        <span>{new Date(doc.mtime).toLocaleString()}</span>
        <span>Output</span>
        <span className="mono" title={doc.output}>
          {doc.output}
        </span>
      </div>

      <label className="row">
        <input
          type="checkbox"
          checked={!doc.rule.blocked}
          disabled={busy}
          onChange={(e) => setRule({ blocked: !e.target.checked })}
        />
        Sync this note
      </label>

      <label className="field">
        Output name
        <input
          type="text"
          placeholder={doc.name}
          defaultValue={doc.rule.name ?? ""}
          disabled={busy}
          onBlur={(e) => {
            const v = e.target.value.trim();
            if (v !== (doc.rule.name ?? "")) setRule({ name: v || false });
          }}
        />
      </label>

      <label className="field">
        Destination folder
        <input
          type="text"
          placeholder={doc.folder || "(root)"}
          defaultValue={doc.rule.folder ?? ""}
          disabled={busy}
          onBlur={(e) => {
            const v = e.target.value.trim();
            if (v !== (doc.rule.folder ?? "")) setRule({ folder: v || false });
          }}
        />
      </label>

      <label className="field">
        Format
        <select
          value={doc.rule.format ?? ""}
          disabled={busy}
          onChange={(e) => setRule({ format: e.target.value || false })}
        >
          <option value="">default</option>
          {SINK_FORMATS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </label>

      {doc.state === "synced" && doc.outputs.length > 0 && (
        <button
          onClick={() =>
            revealItemInDir(`${outputDir}/${doc.outputs[0]}`).catch(
              console.error,
            )
          }
        >
          Reveal in Finder
        </button>
      )}
    </div>
  );
}

/* ---------- folder sync toggle ---------- */

function FolderToggle({
  source,
  folderPath,
  docs,
  onChanged,
}: {
  source: string;
  folderPath: string[];
  docs: DocInfo[];
  onChanged: () => void;
}) {
  const prefix = folderPath.join("/");
  const inFolder = docs.filter(
    (d) => d.folder === prefix || d.folder.startsWith(prefix + "/"),
  );
  const allBlocked =
    inFolder.length > 0 && inFolder.every((d) => d.state === "blocked");
  return (
    <input
      type="checkbox"
      onClick={(e) => e.stopPropagation()}
      title={allBlocked ? "Folder excluded from sync" : "Folder syncs"}
      checked={!allBlocked}
      onChange={async (e) => {
        e.stopPropagation();
        await api.setFolderRule(source, prefix, {
          blocked: !e.target.checked,
        });
        onChanged();
      }}
    />
  );
}

/* ---------- main view ---------- */

export default function Library({ snapshot, onChanged }: Props) {
  const [mode, setMode] = useState<Mode>(
    () => (localStorage.getItem("libMode") as Mode) || "columns",
  );
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [path, setPath] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const columnsRef = useRef<HTMLDivElement>(null);

  // Keep the rightmost (newest / preview) column in view, Finder-style.
  useEffect(() => {
    const el = columnsRef.current;
    if (el) el.scrollTo({ left: el.scrollWidth, behavior: "smooth" });
  }, [path, selected]);

  const sources = snapshot?.sources ?? [];
  const activeSource =
    sourceId ?? sources.find((s) => s.available)?.id ?? sources[0]?.id ?? null;
  const docs = useMemo(
    () => (snapshot?.docs ?? []).filter((d) => d.source === activeSource),
    [snapshot, activeSource],
  );
  const sel = docs.find((d) => d.key === selected) ?? null;

  function switchMode(m: Mode) {
    setMode(m);
    localStorage.setItem("libMode", m);
  }

  function navSource(id: string) {
    setSourceId(id);
    setPath([]);
    setSelected(null);
  }

  if (!snapshot) {
    return <div className="empty">Waiting for the sync engine…</div>;
  }

  const toolbar = (
    <div className="lib-toolbar">
      <div className="crumbs">
        <button className="crumb" onClick={() => navSource(activeSource!)}>
          {sources.find((s) => s.id === activeSource)?.label ?? "Library"}
        </button>
        {path.map((seg, i) => (
          <span key={i}>
            <span className="crumb-sep">›</span>
            <button
              className="crumb"
              onClick={() => {
                setPath(path.slice(0, i + 1));
                setSelected(null);
              }}
            >
              {seg}
            </button>
          </span>
        ))}
      </div>
      <div className="mode-toggle">
        {(
          [
            ["columns", "▥ Columns"],
            ["grid", "▦ Grid"],
            ["list", "☰ List"],
          ] as const
        ).map(([m, label]) => (
          <button
            key={m}
            className={mode === m ? "active" : ""}
            onClick={() => switchMode(m)}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );

  /* ----- columns mode ----- */

  if (mode === "columns") {
    const columns: ReactNode[] = [];
    // Column 0: sources (one per app).
    columns.push(
      <div className="col" key="sources">
        {sources.map((s) => (
          <div
            key={s.id}
            className={
              "col-item col-folder" + (s.id === activeSource ? " active" : "")
            }
            onClick={() => navSource(s.id)}
          >
            <span className="col-icon">🗂</span>
            <span className="col-name">{s.label}</span>
            {!s.available && <span className="col-note">off</span>}
            <span className="col-chev">›</span>
          </div>
        ))}
      </div>,
    );
    // One column per level along `path` (plus the current level).
    for (let depth = 0; depth <= path.length; depth++) {
      const here = path.slice(0, depth);
      const { folders, docs: hereDocs } = levelAt(docs, here);
      if (folders.length === 0 && hereDocs.length === 0 && depth > 0) break;
      columns.push(
        <div className="col" key={"lvl" + depth}>
          {folders.map((f) => (
            <div
              key={"f:" + f}
              className={
                "col-item col-folder" +
                (path[depth] === f ? " active" : "")
              }
              onClick={() => {
                setPath([...here, f]);
                setSelected(null);
              }}
            >
              <span className="col-icon">📁</span>
              <span className="col-name">{f}</span>
              <span className="col-count">
                {countUnder(docs, [...here, f])}
              </span>
              <FolderToggle
                source={activeSource!}
                folderPath={[...here, f]}
                docs={docs}
                onChanged={onChanged}
              />
              <span className="col-chev">›</span>
            </div>
          ))}
          {hereDocs.map((d) => (
            <div
              key={d.key}
              className={
                "col-item" +
                (selected === d.key ? " active" : "") +
                (d.state === "blocked" ? " dimmed" : "")
              }
              onClick={() => {
                setSelected(d.key);
                setPath(here);
              }}
            >
              <span className="col-icon">📝</span>
              <span className="col-name">{d.name}</span>
              <span className={`dot dot-${d.state}`} title={d.state} />
            </div>
          ))}
        </div>,
      );
      if (path[depth] === undefined) break;
    }
    if (sel) {
      columns.push(
        <div className="col col-preview" key="preview">
          <DocDetails
            doc={sel}
            outputDir={snapshot.output_dir}
            onChanged={onChanged}
            withPreview
          />
        </div>,
      );
    }
    return (
      <div className="library">
        {toolbar}
        <div className="columns" ref={columnsRef}>
          {columns}
        </div>
      </div>
    );
  }

  /* ----- grid + list modes (breadcrumb drill-down) ----- */

  const { folders, docs: hereDocs } = levelAt(docs, path);

  const content =
    mode === "grid" ? (
      <div className="grid">
        {folders.map((f) => (
          <div
            key={"f:" + f}
            className="card card-folder"
            onClick={() => {
              setPath([...path, f]);
              setSelected(null);
            }}
          >
            <div className="folder-glyph">📁</div>
            <div className="card-name">{f}</div>
            <div className="card-meta">
              <span className="pages">
                {countUnder(docs, [...path, f])} notes
              </span>
              <FolderToggle
                source={activeSource!}
                folderPath={[...path, f]}
                docs={docs}
                onChanged={onChanged}
              />
            </div>
          </div>
        ))}
        {hereDocs.map((d) => (
          <div
            key={d.key}
            className={
              "card" +
              (d.key === selected ? " selected" : "") +
              (d.state === "blocked" ? " dimmed" : "")
            }
            onClick={() => setSelected(d.key)}
          >
            <Thumb doc={d} className="thumb" />
            <div className="card-name" title={d.name}>
              {d.name}
            </div>
            <div className="card-meta">
              <span className={`badge badge-${d.state}`}>{d.state}</span>
              <span className="pages">
                {d.pages != null ? `${d.pages}p` : d.kind}
              </span>
              <span className="fmt">{d.format}</span>
            </div>
          </div>
        ))}
        {folders.length === 0 && hereDocs.length === 0 && (
          <div className="empty">Empty folder.</div>
        )}
      </div>
    ) : (
      <table className="doclist">
        <thead>
          <tr>
            <th></th>
            <th>Name</th>
            <th>Status</th>
            <th>Pages</th>
            <th>Format</th>
            <th>Modified</th>
            <th>Output</th>
          </tr>
        </thead>
        <tbody>
          {folders.map((f) => (
            <tr
              key={"f:" + f}
              className="row-folder"
              onClick={() => {
                setPath([...path, f]);
                setSelected(null);
              }}
            >
              <td>📁</td>
              <td>{f}</td>
              <td colSpan={3}>{countUnder(docs, [...path, f])} notes</td>
              <td></td>
              <td
                onClick={(e) => e.stopPropagation()}
                style={{ textAlign: "right" }}
              >
                <FolderToggle
                  source={activeSource!}
                  folderPath={[...path, f]}
                  docs={docs}
                  onChanged={onChanged}
                />
              </td>
            </tr>
          ))}
          {hereDocs.map((d) => (
            <tr
              key={d.key}
              className={
                (d.key === selected ? "selected " : "") +
                (d.state === "blocked" ? "dimmed" : "")
              }
              onClick={() => setSelected(d.key)}
            >
              <td>
                <Thumb doc={d} className="thumb-row" />
              </td>
              <td className="cell-name" title={d.name}>
                {d.name}
              </td>
              <td>
                <span className={`badge badge-${d.state}`}>{d.state}</span>
              </td>
              <td>{d.pages ?? "—"}</td>
              <td>{d.format}</td>
              <td>{new Date(d.mtime).toLocaleDateString()}</td>
              <td className="mono cell-output" title={d.output}>
                {d.output}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );

  return (
    <div className="library">
      {toolbar}
      <div className="lib-body">
        <div className="lib-main">{content}</div>
        {sel && (
          <aside className="inspector">
            <DocDetails
              doc={sel}
              outputDir={snapshot.output_dir}
              onChanged={onChanged}
            />
            <button className="linkish" onClick={() => setSelected(null)}>
              Close
            </button>
          </aside>
        )}
      </div>
    </div>
  );
}
