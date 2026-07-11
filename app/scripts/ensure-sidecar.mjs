// Dev convenience: tauri-build refuses to compile unless the externalBin
// file exists. Real sidecars come from core/packaging/build-sidecar.sh;
// for `tauri dev` (which spawns the daemon via uv instead) any file will
// do, so create a placeholder if nothing is there yet.
import { execSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const binDir = join(here, "..", "src-tauri", "binaries");

const host = execSync("rustc -vV", { encoding: "utf8" })
  .split("\n")
  .find((l) => l.startsWith("host: "))
  .slice("host: ".length)
  .trim();

const ext = host.includes("windows") ? ".exe" : "";
const target = join(binDir, `inkterop-daemon-${host}${ext}`);

if (!existsSync(target)) {
  mkdirSync(binDir, { recursive: true });
  if (ext === ".exe") {
    // A zero-byte .exe satisfies tauri-build; dev mode never runs it.
    writeFileSync(target, "");
  } else {
    writeFileSync(
      target,
      "#!/bin/sh\n" +
        "# Placeholder sidecar (dev builds spawn the daemon via uv).\n" +
        "# Release builds must overwrite this: core/packaging/build-sidecar.sh\n" +
        'echo "placeholder sidecar — run core/packaging/build-sidecar.sh" >&2\n' +
        "exit 1\n",
    );
    chmodSync(target, 0o755);
  }
  console.log(`created placeholder sidecar: ${target}`);
}
