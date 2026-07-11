import { useEffect, useRef, useState } from "react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open, save } from "@tauri-apps/plugin-dialog";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { api } from "../rpc";

/** A conversion started from elsewhere in the app (Library inspector):
 * `input` is whatever convert.run accepts — a file path or a reMarkable
 * library uuid. */
export interface ConvertRequest {
  input: string;
  name: string;
}

interface Item {
  input: string;
  name: string;
}

interface Job {
  input: string;
  name: string;
  output?: string;
  state: "converting" | "done" | "error";
  detail?: string;
}

const OUT_FORMATS = [
  { ext: "pdf", label: "PDF", validated: true },
  { ext: "svg", label: "SVG", validated: true },
  { ext: "png", label: "PNG", validated: true },
  { ext: "xopp", label: "Xournal++ (.xopp)", validated: true },
  { ext: "inkml", label: "InkML", validated: true },
  { ext: "inkz", label: "Ink interchange (.inkz)", validated: true },
  { ext: "json", label: "IR JSON (raw ink data)", validated: true },
  { ext: "excalidraw", label: "Excalidraw", validated: true },
  { ext: "sbn2", label: "Saber (experimental)", validated: false },
  { ext: "rmdoc", label: "reMarkable (experimental)", validated: false },
  { ext: "note", label: "Supernote (experimental)", validated: false },
  { ext: "goodnotes", label: "GoodNotes (experimental)", validated: false },
  { ext: "ntb", label: "Notability (experimental)", validated: false },
];

function basename(p: string): string {
  return p.split(/[\\/]/).pop() ?? p;
}

function stripExt(p: string): string {
  const b = basename(p);
  const i = b.lastIndexOf(".");
  return i > 0 ? b.slice(0, i) : b;
}

export default function Convert({
  request,
  onRequestConsumed,
}: {
  request: ConvertRequest | null;
  onRequestConsumed: () => void;
}) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [fmt, setFmt] = useState("pdf");
  const [fidelity, setFidelity] = useState("exact");
  const [dragOver, setDragOver] = useState(false);
  const [pendingItem, setPendingItem] = useState<Item | null>(null);
  const fmtRef = useRef(fmt);
  fmtRef.current = fmt;
  const fidRef = useRef(fidelity);
  fidRef.current = fidelity;

  useEffect(() => {
    const un = getCurrentWebview().onDragDropEvent((event) => {
      if (event.payload.type === "over") setDragOver(true);
      else if (event.payload.type === "leave") setDragOver(false);
      else if (event.payload.type === "drop") {
        setDragOver(false);
        void convertItems(
          event.payload.paths.map((p) => ({ input: p, name: stripExt(p) })),
        );
      }
    });
    return () => {
      un.then((f) => f());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A note handed over from the Library: show it queued; the user picks
  // the target format, then hits Convert.
  useEffect(() => {
    if (request) {
      setPendingItem({ input: request.input, name: request.name });
      onRequestConsumed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);

  async function convertItems(items: Item[]) {
    if (items.length === 0) return;
    const format = fmtRef.current;
    const experimental = !OUT_FORMATS.find((f) => f.ext === format)?.validated;

    for (const item of items) {
      const output = await save({
        title: `Save "${item.name}" as ${format}`,
        defaultPath: `${item.name}.${format}`,
      });
      if (!output) continue;
      setJobs((j) => [
        { input: item.input, name: item.name, output, state: "converting" },
        ...j,
      ]);
      try {
        const res = await api.convert(item.input, output, {
          fidelity: fidRef.current,
          experimental,
        });
        setJobs((j) =>
          j.map((job) =>
            job.input === item.input && job.output === output
              ? {
                  ...job,
                  state: "done",
                  detail: `${res.pages} page${res.pages === 1 ? "" : "s"} from ${res.source_format}`,
                }
              : job,
          ),
        );
      } catch (e) {
        setJobs((j) =>
          j.map((job) =>
            job.input === item.input && job.output === output
              ? { ...job, state: "error", detail: String(e) }
              : job,
          ),
        );
      }
    }
  }

  return (
    <div className="convert">
      <div
        className={"dropzone" + (dragOver ? " over" : "")}
        onClick={async () => {
          const picked = await open({
            multiple: true,
            title: "Choose note files to convert",
          });
          if (picked) {
            const paths = Array.isArray(picked) ? picked : [picked];
            void convertItems(
              paths.map((p) => ({ input: p, name: stripExt(p) })),
            );
          }
        }}
      >
        <div className="dropzone-big">Drop note files here</div>
        <div className="dropzone-small">
          .goodnotes, .ntb, .note, .sba, .rm, .xopp, .one, .pkdrawing, … —
          or click to browse
        </div>
      </div>

      <div className="convert-options">
        <label>
          Convert to{" "}
          <select value={fmt} onChange={(e) => setFmt(e.target.value)}>
            {OUT_FORMATS.map((f) => (
              <option key={f.ext} value={f.ext}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Fidelity{" "}
          <select
            value={fidelity}
            onChange={(e) => setFidelity(e.target.value)}
          >
            <option value="exact">exact — look of the source app</option>
            <option value="native">native — restyle in the target</option>
            <option value="raw">raw — per-point pen data</option>
          </select>
        </label>
      </div>

      {pendingItem && (
        <div className="pending-item">
          <span>
            From your library: <strong>{pendingItem.name}</strong>
          </span>
          <button
            className="primary"
            onClick={() => {
              const item = pendingItem;
              setPendingItem(null);
              if (item) void convertItems([item]);
            }}
          >
            Convert…
          </button>
          <button className="linkish" onClick={() => setPendingItem(null)}>
            Cancel
          </button>
        </div>
      )}

      {jobs.length > 0 && (
        <ul className="joblist">
          {jobs.map((j, i) => (
            <li key={i} className={`job job-${j.state}`}>
              <span className="job-name" title={j.input}>
                {j.name} → {j.output ? basename(j.output) : "…"}
              </span>
              <span className="job-state">{j.detail ?? j.state}</span>
              {j.state === "done" && j.output && (
                <>
                  <button
                    className="linkish"
                    onClick={() => openPath(j.output!).catch(console.error)}
                  >
                    Open
                  </button>
                  <button
                    className="linkish"
                    onClick={() =>
                      revealItemInDir(j.output!).catch(console.error)
                    }
                  >
                    Reveal
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
