import { invoke, convertFileSrc } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export interface SourceInfo {
  id: string;
  label: string;
  available: boolean;
  experimental: boolean;
}

export interface DocRule {
  blocked?: boolean;
  allowed?: boolean;
  name?: string;
  folder?: string;
  format?: string;
}

export interface DocInfo {
  key: string;
  source: string;
  id: string;
  name: string;
  folder: string;
  mtime: number;
  kind: string;
  pages: number | null;
  state: "synced" | "pending" | "failed" | "excluded";
  reason: string | null;
  error: string | null;
  synced_at: number | null;
  format: string;
  output: string;
  outputs: string[];
  rule: DocRule;
  convert_input: string;
  native_path: string | null;
}

export interface HistoryDocResult {
  key: string;
  name: string;
  action: "synced" | "failed";
  outputs?: string[];
  error?: string;
  seconds: number;
}

export interface HistoryPass {
  time: number;
  trigger: string;
  rendered: number;
  skipped: number;
  failed: number;
  removed: number;
  seconds: number;
  documents: number;
  failures: { key: string; name: string; error: string }[];
  docs?: HistoryDocResult[];
}

/** "5m ago" formatting for epoch seconds or ms. */
export function timeAgo(ts: number | null | undefined): string {
  if (!ts) return "never";
  const ms = ts > 1e12 ? ts : ts * 1000;
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Human text for an exclusion reason code from the engine. */
export function reasonText(reason: string | null | undefined): string {
  if (!reason) return "";
  if (reason === "note-rule") return "excluded — turned off for this note";
  if (reason.startsWith("folder-rule:"))
    return `excluded — folder "${reason.slice(12) || "(root)"}" is turned off`;
  if (reason === "scope-notebooks")
    return "excluded — notebooks are off (Settings → What to sync)";
  if (reason === "unsupported-kind")
    return "can't sync — annotated PDFs/EPUBs need the base-page merge " +
      "(planned); only the handwriting would render";
  if (reason === "config-exclude")
    return "excluded — folder listed in config.toml [scope] exclude";
  if (reason === "allowlist")
    return "excluded — allowlist mode: not explicitly allowed";
  return `excluded — ${reason}`;
}

export interface Snapshot {
  sources: SourceInfo[];
  docs: DocInfo[];
  output_dir: string;
  mode: "blocklist" | "allowlist";
}

export interface AppConfig {
  output_dir: string;
  normalize: string;
  landscape: number[];
  portrait: number[];
  pen_style: string;
  notebooks: boolean;
  pdfs: boolean;
  epubs: boolean;
  exclude: string[];
  default_format: string;
  source_remarkable: boolean;
  remarkable_cache_dir: string | null;
  source_folders: { path: string; name?: string; id?: string }[];
  source_goodnotes: boolean;
  source_notability: boolean;
}

export interface SyncSummary {
  rendered: number;
  skipped: number;
  failed: number;
  removed: number;
  seconds: number;
  documents: number;
}

export interface DaemonEvent {
  method: string;
  params?: Record<string, unknown> & { event?: string };
}

export const SINK_FORMATS = ["pdf", "svg", "png", "inkz"] as const;

/** One JSON-RPC call to the Python daemon via the Rust bridge. */
export function rpc<T = unknown>(
  method: string,
  params?: Record<string, unknown>,
): Promise<T> {
  return invoke<T>("rpc", { method, params: params ?? {} });
}

export function onDaemonEvent(
  handler: (ev: DaemonEvent) => void,
): Promise<UnlistenFn> {
  return listen<DaemonEvent>("daemon-event", (e) => handler(e.payload));
}

export function thumbnailUrl(path: string): string {
  return convertFileSrc(path);
}

export const api = {
  library: () => rpc<Snapshot>("library.list"),
  history: () => rpc<{ passes: HistoryPass[] }>("history.get"),
  syncNow: () => rpc<SyncSummary>("sync.now"),
  pause: () => rpc("sync.pause"),
  resume: () => rpc("sync.resume"),
  status: () => rpc<Record<string, unknown>>("status.get"),
  configGet: () => rpc<AppConfig>("config.get"),
  configSet: (changes: Partial<AppConfig>) =>
    rpc<AppConfig>("config.set", { changes }),
  setDocRule: (source: string, id: string, fields: DocRule) =>
    rpc("rules.set_doc", { source, id, ...fields }),
  setFolderRule: (source: string, folder: string, fields: DocRule) =>
    rpc("rules.set_folder", { source, folder, ...fields }),
  thumbnail: (key: string) => rpc<{ path: string }>("thumbnail.get", { key }),
  formats: () =>
    rpc<{
      readers: { id: string; extensions: string[] }[];
      writers: { id: string; extensions: string[]; validated: boolean }[];
      sink_formats: string[];
    }>("formats.list"),
  convert: (input: string, output: string, opts?: {
    fidelity?: string;
    experimental?: boolean;
  }) => rpc<{ output: string; pages: number; title: string | null;
              source_format: string }>("convert.run", {
    input,
    output,
    ...opts,
  }),
};
