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
  state: "synced" | "pending" | "failed" | "blocked";
  format: string;
  output: string;
  outputs: string[];
  rule: DocRule;
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
