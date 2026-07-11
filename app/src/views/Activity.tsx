export interface ActivityEntry {
  time: number;
  event: string;
  detail: Record<string, unknown>;
}

const LABELS: Record<string, string> = {
  "pass-started": "Sync pass started",
  "pass-finished": "Sync pass finished",
  "doc-started": "Rendering",
  "doc-synced": "Synced",
  "doc-failed": "FAILED",
  "source-unavailable": "Source unavailable",
  "source-error": "Source error",
};

export default function Activity({ entries }: { entries: ActivityEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="empty">
        No activity yet this session. Sync passes and per-note results show
        up here as they happen.
      </div>
    );
  }
  return (
    <ul className="activity">
      {entries.map((e, i) => (
        <li key={i} className={`act act-${e.event}`}>
          <span className="act-time">
            {new Date(e.time).toLocaleTimeString()}
          </span>
          <span className="act-label">{LABELS[e.event] ?? e.event}</span>
          <span className="act-detail">
            {e.event === "pass-finished"
              ? `${e.detail.rendered} rendered, ${e.detail.skipped} unchanged, ` +
                `${e.detail.failed} failed, ${e.detail.removed} removed ` +
                `(${e.detail.seconds}s)`
              : String(e.detail.name ?? e.detail.source ?? "")}
            {e.event === "doc-failed" && (
              <span className="act-error"> — {String(e.detail.error)}</span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}
