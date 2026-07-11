import { Fragment, useEffect, useState } from "react";
import { api, timeAgo, type HistoryPass } from "../rpc";
import {
  AlertIcon,
  CheckCircleIcon,
  ClockIcon,
  SyncIcon,
} from "../icons";

export interface ActivityEntry {
  time: number;
  event: string;
  detail: Record<string, unknown>;
}

const LIVE_LABELS: Record<string, string> = {
  "pass-started": "Sync pass started",
  "doc-started": "Rendering",
  "doc-synced": "Synced",
  "doc-failed": "FAILED",
  "source-unavailable": "Source unavailable",
  "source-error": "Source error",
  "watcher-disabled": "Watcher disabled",
};

export default function History({
  entries,
  syncing,
}: {
  entries: ActivityEntry[];
  syncing: boolean;
}) {
  const [passes, setPasses] = useState<HistoryPass[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    api
      .history()
      .then((h) => setPasses(h.passes))
      .catch(() => {});
  }, [syncing, entries.length]);

  const live = entries.filter((e) => e.event !== "pass-finished").slice(0, 40);

  return (
    <div className="history">
      {syncing && (
        <div className="live-banner">
          <SyncIcon width={14} height={14} className="spin" /> Sync pass
          running…
        </div>
      )}

      <h2>Sync passes</h2>
      {passes.length === 0 && (
        <div className="empty-inline">No sync passes recorded yet.</div>
      )}
      {passes.length > 0 && (
        <table className="doclist">
          <thead>
            <tr>
              <th></th>
              <th>When</th>
              <th>Result</th>
              <th>Changed</th>
              <th>Unchanged</th>
              <th>Removed</th>
              <th>Duration</th>
              <th>Trigger</th>
            </tr>
          </thead>
          <tbody>
            {passes.map((p, i) => (
              <Fragment key={i}>
                <tr
                  onClick={() => setExpanded(expanded === i ? null : i)}
                  className={p.failed ? "row-failed" : ""}
                >
                  <td>
                    {p.failed ? (
                      <AlertIcon width={15} height={15} className="ico-bad" />
                    ) : (
                      <CheckCircleIcon
                        width={15}
                        height={15}
                        className="ico-ok"
                      />
                    )}
                  </td>
                  <td title={new Date(p.time * 1000).toLocaleString()}>
                    {timeAgo(p.time)}
                  </td>
                  <td>
                    {p.failed
                      ? `${p.failed} failed`
                      : p.rendered
                        ? "ok"
                        : "no changes"}
                  </td>
                  <td>{p.rendered}</td>
                  <td>{p.skipped}</td>
                  <td>{p.removed}</td>
                  <td>{p.seconds}s</td>
                  <td>{p.trigger === "watch" ? "auto" : p.trigger}</td>
                </tr>
                {expanded === i && p.failures.length > 0 && (
                  <tr className="row-expand">
                    <td></td>
                    <td colSpan={7}>
                      {p.failures.map((f, j) => (
                        <div key={j} className="fail-line">
                          <strong>{f.name}</strong> — {f.error}
                        </div>
                      ))}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}

      <h2>This session</h2>
      {live.length === 0 ? (
        <div className="empty-inline">
          <ClockIcon width={14} height={14} /> No live activity yet — events
          stream here during sync passes.
        </div>
      ) : (
        <ul className="activity">
          {live.map((e, i) => (
            <li key={i} className={`act act-${e.event}`}>
              <span className="act-time">
                {new Date(e.time).toLocaleTimeString()}
              </span>
              <span className="act-label">
                {LIVE_LABELS[e.event] ?? e.event}
              </span>
              <span className="act-detail">
                {String(e.detail.name ?? e.detail.source ?? "")}
                {e.event === "doc-failed" && (
                  <span className="act-error">
                    {" "}
                    — {String(e.detail.error)}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
