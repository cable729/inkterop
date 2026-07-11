import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import {
  api,
  reasonText,
  thumbnailUrl,
  timeAgo,
  SINK_FORMATS,
  type DocInfo,
  type Snapshot,
} from "../rpc";
import type { ConvertRequest } from "./Convert";
import {
  AlertIcon,
  BookIcon,
  CheckCircleIcon,
  ChevronIcon,
  ClockIcon,
  FolderIcon,
  MinusCircleIcon,
  NoteIcon,
  SearchIcon,
  SwapIcon,
  TabletIcon,
} from "../icons";

type Mode = "columns" | "grid" | "list";
type SortKey = "name" | "state" | "pages" | "format" | "mtime" | "output";

interface Props {
  snapshot: Snapshot | null;
  onChanged: () => void;
  onConvert: (req: ConvertRequest) => void;
  onOpenHistory: () => void;
}

/* ---------- helpers ---------- */

function splitFolder(f: string): string[] {
  return f === "" ? [] : f.split("/");
}

function levelAt(docs: DocInfo[], path: string[]) {
  const prefix = path.join("/");
  const folders = new Set<string>();
  const here: DocInfo[] = [];
  for (const d of docs) {
    if (prefix !== "" && d.folder !== prefix && !d.folder.startsWith(prefix + "/"))
      continue;
    if (d.folder === prefix) here.push(d);
    else folders.add(splitFolder(d.folder)[path.length]);
  }
  here.sort((a, b) => a.name.localeCompare(b.name));
  return { folders: [...folders].sort(), docs: here };
}

function docsUnder(docs: DocInfo[], path: string[]): DocInfo[] {
  const prefix = path.join("/");
  return docs.filter(
    (d) => d.folder === prefix || d.folder.startsWith(prefix + "/"),
  );
}

function statusTitle(d: DocInfo): string {
  if (d.state === "synced") return `synced ${timeAgo(d.synced_at)}`;
  if (d.state === "pending") return "waiting for the next sync pass";
  if (d.state === "failed")
    return `last sync attempt failed: ${d.error ?? "unknown error"}`;
  return reasonText(d.reason);
}

function isUnsupported(d: DocInfo): boolean {
  return d.reason === "unsupported-kind" || d.kind === "pdf" || d.kind === "epub";
}

const KIND_TITLES: Record<string, string> = {
  notebook: "notebook",
  pdf: "annotated PDF",
  epub: "annotated EPUB",
  file: "note file",
};

/* ---------- small components ---------- */

function StatusIcon({
  doc,
  onClick,
}: {
  doc: DocInfo;
  onClick?: () => void;
}) {
  const title = statusTitle(doc) + (onClick ? " — click for sync history" : "");
  const props = { width: 15, height: 15 };
  const wrap = (cls: string, icon: ReactNode) => (
    <span
      className={"status-ico " + cls + (onClick ? " clickable" : "")}
      title={title}
      onClick={
        onClick
          ? (e) => {
              e.stopPropagation();
              onClick();
            }
          : undefined
      }
    >
      {icon}
    </span>
  );
  if (doc.state === "synced") return wrap("ico-ok", <CheckCircleIcon {...props} />);
  if (doc.state === "pending") return wrap("ico-warn", <ClockIcon {...props} />);
  if (doc.state === "failed") return wrap("ico-bad", <AlertIcon {...props} />);
  return wrap("ico-dim", <MinusCircleIcon {...props} />);
}

function FolderStatusIcon({ docs }: { docs: DocInfo[] }) {
  const props = { width: 15, height: 15 };
  if (docs.some((d) => d.state === "failed"))
    return (
      <span className="status-ico ico-bad" title="some notes failed to sync">
        <AlertIcon {...props} />
      </span>
    );
  if (docs.some((d) => d.state === "pending"))
    return (
      <span className="status-ico ico-warn" title="some notes wait to sync">
        <ClockIcon {...props} />
      </span>
    );
  if (docs.some((d) => d.state === "synced")) {
    const newest = Math.max(
      ...docs.map((d) => d.synced_at ?? 0).filter(Boolean),
    );
    return (
      <span className="status-ico ico-ok" title={`synced ${timeAgo(newest)}`}>
        <CheckCircleIcon {...props} />
      </span>
    );
  }
  return (
    <span className="status-ico ico-dim" title="everything here is excluded">
      <MinusCircleIcon {...props} />
    </span>
  );
}

function KindIcon({ kind }: { kind: string }) {
  const props = { width: 15, height: 15, className: "kind-ico" };
  const title = KIND_TITLES[kind] ?? kind;
  if (kind === "pdf" || kind === "epub")
    return (
      <span title={title}>
        <BookIcon {...props} />
      </span>
    );
  return (
    <span title={title}>
      <NoteIcon {...props} />
    </span>
  );
}

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

/** Sync on/off. Checking an item that scope excludes writes an explicit
 * allow (which overrides scope); checking a note the user blocked just
 * clears the block. */
function DocCheckbox({
  doc,
  onChanged,
}: {
  doc: DocInfo;
  onChanged: () => void;
}) {
  const checked = doc.state !== "excluded";
  const unsupported = isUnsupported(doc);
  return (
    <input
      type="checkbox"
      className="sync-check"
      checked={checked && !unsupported}
      disabled={unsupported}
      title={
        unsupported
          ? reasonText("unsupported-kind")
          : checked
            ? "Syncing — click to exclude"
            : statusTitle(doc)
      }
      onClick={(e) => e.stopPropagation()}
      onChange={async (e) => {
        e.stopPropagation();
        const on = e.target.checked;
        await api.setDocRule(doc.source, doc.id, {
          blocked: !on,
          allowed: on && doc.reason !== "note-rule",
        });
        onChanged();
      }}
    />
  );
}

function FolderCheckbox({
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
  const inFolder = docsUnder(docs, folderPath).filter(
    (d) => !isUnsupported(d),
  );
  const empty = inFolder.length === 0;
  const checked = inFolder.some((d) => d.state !== "excluded");
  // Checking a folder whose contents are scope-excluded needs an explicit
  // allow; if it was just folder-blocked, clearing the block is enough.
  const needsAllow = inFolder.every(
    (d) =>
      d.state !== "excluded" ||
      (d.reason ?? "").startsWith("scope-") ||
      d.reason === "config-exclude" ||
      d.reason === "allowlist",
  ) && inFolder.some((d) => d.state === "excluded");
  return (
    <input
      type="checkbox"
      className="sync-check"
      checked={checked}
      disabled={empty}
      title={
        empty
          ? "Nothing syncable here — only annotated PDFs/EPUBs (not supported yet)"
          : checked
            ? "Folder syncs — click to exclude everything in it"
            : "Excluded — click to sync this folder" +
              (needsAllow ? " (overrides the Settings scope for it)" : "")
      }
      onClick={(e) => e.stopPropagation()}
      onChange={async (e) => {
        e.stopPropagation();
        const on = e.target.checked;
        await api.setFolderRule(source, prefix, {
          blocked: !on,
          allowed: on && needsAllow,
        });
        onChanged();
      }}
    />
  );
}

/* ---------- doc details / inspector ---------- */

function DocDetails({
  doc,
  outputDir,
  onChanged,
  onConvert,
  onOpenHistory,
  withPreview,
}: {
  doc: DocInfo;
  outputDir: string;
  onChanged: () => void;
  onConvert: (req: ConvertRequest) => void;
  onOpenHistory: () => void;
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
  const pdfOutput =
    doc.outputs.find((o) => o.endsWith(".pdf")) ?? doc.outputs[0];
  return (
    <div className="doc-details">
      {withPreview && <Thumb doc={doc} className="thumb preview-thumb" />}
      <h3 title={doc.name}>{doc.name}</h3>
      <div className="kv">
        <span>Status</span>
        <span>
          <span
            className={`badge badge-${doc.state} clickable`}
            title={statusTitle(doc) + " — click for sync history"}
            onClick={onOpenHistory}
          >
            {doc.state}
          </span>{" "}
          {doc.state === "synced" && (
            <span className="muted">{timeAgo(doc.synced_at)}</span>
          )}
        </span>
        {doc.state === "excluded" && (
          <>
            <span>Why</span>
            <span className="muted">{reasonText(doc.reason)}</span>
          </>
        )}
        {doc.state === "failed" && (
          <>
            <span>Error</span>
            <span className="error-text">
              {doc.error}{" "}
              <button className="linkish" onClick={onOpenHistory}>
                sync history
              </button>
            </span>
          </>
        )}
        <span>Kind</span>
        <span>{KIND_TITLES[doc.kind] ?? doc.kind}</span>
        <span>Pages</span>
        <span>{doc.pages ?? "—"}</span>
        <span>Modified</span>
        <span>{new Date(doc.mtime).toLocaleString()}</span>
        <span>Output</span>
        <span className="mono" title={doc.output}>
          {doc.output}
        </span>
      </div>

      {isUnsupported(doc) ? (
        <p className="hint">{reasonText("unsupported-kind")}</p>
      ) : (
        <label className="row">
          <DocCheckbox doc={doc} onChanged={onChanged} />
          Sync this note
        </label>
      )}

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

      <div className="action-col">
        <button
          onClick={() =>
            onConvert({ input: doc.convert_input, name: doc.name })
          }
        >
          <SwapIcon width={13} height={13} /> Convert to another format…
        </button>
        {doc.state === "synced" && pdfOutput && (
          <button
            onClick={() =>
              openPath(`${outputDir}/${pdfOutput}`).catch(console.error)
            }
          >
            Open synced {doc.format.toUpperCase()}
          </button>
        )}
        {doc.native_path && (
          <button
            title="Open the original file with whatever app owns it"
            onClick={() => openPath(doc.native_path!).catch(console.error)}
          >
            Open original file
          </button>
        )}
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
    </div>
  );
}

/* ---------- main view ---------- */

export default function Library({
  snapshot,
  onChanged,
  onConvert,
  onOpenHistory,
}: Props) {
  const [mode, setMode] = useState<Mode>(
    () => (localStorage.getItem("libMode") as Mode) || "columns",
  );
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [path, setPath] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const columnsRef = useRef<HTMLDivElement>(null);

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

  const searching = query.trim() !== "" || statusFilter !== "";
  const searchResults = useMemo(() => {
    if (!searching) return [];
    const q = query.trim().toLowerCase();
    return docs
      .filter(
        (d) =>
          (!q || d.name.toLowerCase().includes(q)) &&
          (!statusFilter || d.state === statusFilter),
      )
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [docs, query, statusFilter, searching]);

  function switchMode(m: Mode) {
    setMode(m);
    localStorage.setItem("libMode", m);
  }

  function navSource(id: string) {
    setSourceId(id);
    setPath([]);
    setSelected(null);
  }

  function sortDocs(list: DocInfo[]): DocInfo[] {
    const dir = sortDir;
    return [...list].sort((a, b) => {
      let r = 0;
      if (sortKey === "name") r = a.name.localeCompare(b.name);
      else if (sortKey === "state") r = a.state.localeCompare(b.state);
      else if (sortKey === "pages") r = (a.pages ?? -1) - (b.pages ?? -1);
      else if (sortKey === "format") r = a.format.localeCompare(b.format);
      else if (sortKey === "mtime") r = a.mtime - b.mtime;
      else if (sortKey === "output") r = a.output.localeCompare(b.output);
      return r * dir;
    });
  }

  function header(label: string, key: SortKey) {
    const active = sortKey === key;
    return (
      <th
        className="th-sort"
        onClick={() => {
          if (active) setSortDir((d) => (d === 1 ? -1 : 1));
          else {
            setSortKey(key);
            setSortDir(1);
          }
        }}
      >
        {label}
        {active ? (sortDir === 1 ? " ↑" : " ↓") : ""}
      </th>
    );
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
      <div className="searchbox">
        <SearchIcon width={13} height={13} />
        <input
          type="search"
          placeholder="Search notes…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          value={statusFilter}
          title="Filter by sync status"
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">any status</option>
          <option value="synced">synced</option>
          <option value="pending">pending</option>
          <option value="failed">failed</option>
          <option value="excluded">excluded</option>
        </select>
      </div>
      <div className="mode-toggle">
        {(
          [
            ["columns", "Columns"],
            ["grid", "Grid"],
            ["list", "List"],
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

  /* ----- search results override the normal browsing surface ----- */

  if (searching) {
    return (
      <div className="library">
        {toolbar}
        <div className="lib-body">
          <div className="lib-main">
            <table className="doclist">
              <thead>
                <tr>
                  <th></th>
                  <th></th>
                  <th>Name</th>
                  <th>Folder</th>
                  <th>Status</th>
                  <th>Pages</th>
                </tr>
              </thead>
              <tbody>
                {searchResults.map((d) => (
                  <tr
                    key={d.key}
                    className={d.key === selected ? "selected" : ""}
                    onClick={() => setSelected(d.key)}
                  >
                    <td>
                      <DocCheckbox doc={d} onChanged={onChanged} />
                    </td>
                    <td>
                      <Thumb doc={d} className="thumb-row" />
                    </td>
                    <td className="cell-name">
                      <KindIcon kind={d.kind} /> {d.name}
                    </td>
                    <td className="muted">{d.folder || "(root)"}</td>
                    <td>
                      <StatusIcon doc={d} onClick={onOpenHistory} />
                    </td>
                    <td>{d.pages ?? "—"}</td>
                  </tr>
                ))}
                {searchResults.length === 0 && (
                  <tr>
                    <td colSpan={6} className="muted">
                      No matches.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {sel && (
            <aside className="inspector">
              <DocDetails
                doc={sel}
                outputDir={snapshot.output_dir}
                onChanged={onChanged}
                onConvert={onConvert}
                onOpenHistory={onOpenHistory}
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

  /* ----- columns mode ----- */

  if (mode === "columns") {
    const columns: ReactNode[] = [];
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
            <TabletIcon width={15} height={15} className="kind-ico" />
            <span className="col-name">{s.label}</span>
            {!s.available && <span className="col-note">off</span>}
            <ChevronIcon width={13} height={13} className="chev" />
          </div>
        ))}
      </div>,
    );
    for (let depth = 0; depth <= path.length; depth++) {
      const here = path.slice(0, depth);
      const { folders, docs: hereDocs } = levelAt(docs, here);
      if (folders.length === 0 && hereDocs.length === 0 && depth > 0) break;
      columns.push(
        <div className="col" key={"lvl" + depth}>
          {folders.map((f) => {
            const fp = [...here, f];
            const under = docsUnder(docs, fp);
            return (
              <div
                key={"f:" + f}
                className={
                  "col-item col-folder" + (path[depth] === f ? " active" : "")
                }
                onClick={() => {
                  setPath(fp);
                  setSelected(null);
                }}
              >
                <FolderCheckbox
                  source={activeSource!}
                  folderPath={fp}
                  docs={docs}
                  onChanged={onChanged}
                />
                <FolderIcon width={15} height={15} className="kind-ico" />
                <span className="col-name">{f}</span>
                <span className="col-count">{under.length}</span>
                <FolderStatusIcon docs={under} />
                <ChevronIcon width={13} height={13} className="chev" />
              </div>
            );
          })}
          {hereDocs.map((d) => (
            <div
              key={d.key}
              className={
                "col-item" +
                (selected === d.key ? " active" : "") +
                (d.state === "excluded" || isUnsupported(d) ? " dimmed" : "")
              }
              onClick={() => {
                setSelected(d.key);
                setPath(here);
              }}
            >
              <DocCheckbox doc={d} onChanged={onChanged} />
              <KindIcon kind={d.kind} />
              <span className="col-name">{d.name}</span>
              <StatusIcon doc={d} onClick={onOpenHistory} />
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
            onConvert={onConvert}
            onOpenHistory={onOpenHistory}
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

  /* ----- grid + list modes ----- */

  const { folders, docs: hereDocs } = levelAt(docs, path);

  const content =
    mode === "grid" ? (
      <div className="grid">
        {folders.map((f) => {
          const fp = [...path, f];
          const under = docsUnder(docs, fp);
          return (
            <div
              key={"f:" + f}
              className="card card-folder"
              onClick={() => {
                setPath(fp);
                setSelected(null);
              }}
            >
              <div className="folder-glyph">
                <FolderIcon width={42} height={42} strokeWidth={1.4} />
              </div>
              <div className="card-name">{f}</div>
              <div className="card-meta">
                <FolderCheckbox
                  source={activeSource!}
                  folderPath={fp}
                  docs={docs}
                  onChanged={onChanged}
                />
                <span className="pages">{under.length} notes</span>
                <span className="meta-right">
                  <FolderStatusIcon docs={under} />
                </span>
              </div>
            </div>
          );
        })}
        {hereDocs.map((d) => (
          <div
            key={d.key}
            className={
              "card" +
              (d.key === selected ? " selected" : "") +
              (d.state === "excluded" ? " dimmed" : "")
            }
            onClick={() => setSelected(d.key)}
          >
            <Thumb doc={d} className="thumb" />
            <div className="card-name" title={d.name}>
              {d.name}
            </div>
            <div className="card-meta">
              <DocCheckbox doc={d} onChanged={onChanged} />
              <span className="pages">
                {d.pages != null ? `${d.pages}p` : KIND_TITLES[d.kind]}
              </span>
              <span className="fmt">{d.format}</span>
              <span className="meta-right">
                <StatusIcon doc={d} onClick={onOpenHistory} />
              </span>
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
            <th></th>
            {header("Name", "name")}
            {header("Status", "state")}
            {header("Pages", "pages")}
            {header("Format", "format")}
            {header("Modified", "mtime")}
            {header("Output", "output")}
          </tr>
        </thead>
        <tbody>
          {folders.map((f) => {
            const fp = [...path, f];
            const under = docsUnder(docs, fp);
            return (
              <tr
                key={"f:" + f}
                className="row-folder"
                onClick={() => {
                  setPath(fp);
                  setSelected(null);
                }}
              >
                <td onClick={(e) => e.stopPropagation()}>
                  <FolderCheckbox
                    source={activeSource!}
                    folderPath={fp}
                    docs={docs}
                    onChanged={onChanged}
                  />
                </td>
                <td>
                  <FolderIcon width={16} height={16} />
                </td>
                <td className="cell-name">{f}</td>
                <td>
                  <FolderStatusIcon docs={under} />
                </td>
                <td colSpan={3} className="muted">
                  {under.length} notes
                </td>
                <td></td>
              </tr>
            );
          })}
          {sortDocs(hereDocs).map((d) => (
            <tr
              key={d.key}
              className={
                (d.key === selected ? "selected " : "") +
                (d.state === "excluded" || isUnsupported(d) ? "dimmed" : "")
              }
              onClick={() => setSelected(d.key)}
            >
              <td onClick={(e) => e.stopPropagation()}>
                <DocCheckbox doc={d} onChanged={onChanged} />
              </td>
              <td>
                <Thumb doc={d} className="thumb-row" />
              </td>
              <td className="cell-name" title={d.name}>
                <KindIcon kind={d.kind} /> {d.name}
              </td>
              <td>
                <StatusIcon doc={d} onClick={onOpenHistory} />
              </td>
              <td>{d.pages ?? "—"}</td>
              <td>{d.format}</td>
              <td title={new Date(d.mtime).toLocaleString()}>
                {new Date(d.mtime).toLocaleDateString()}
              </td>
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
              onConvert={onConvert}
              onOpenHistory={onOpenHistory}
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
