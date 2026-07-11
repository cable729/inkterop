# Releasing the Inkterop desktop app

## How a release happens

1. Bump versions (keep in sync): `app/package.json`,
   `app/src-tauri/tauri.conf.json`, `app/src-tauri/Cargo.toml`,
   `core/pyproject.toml` + `core/src/inkterop/__init__.py`.
2. `git tag vX.Y.Z && git push --tags`.
3. `.github/workflows/release.yml` builds per-OS (macOS arm64 signed +
   notarized, Windows x64 and Linux x64 unsigned community builds) and
   attaches installers to a **draft** GitHub Release.
4. Sanity-check the artifacts, publish the release, update the Homebrew
   cask (below).

Each job first runs `core/packaging/build-sidecar.sh <triple>`, which
PyInstaller-freezes the Python engine into
`app/src-tauri/binaries/inkterop-daemon-<triple>` (Tauri bundles it next to
the app binary; the Rust shell spawns it with the `daemon` argument).
Release builds MUST contain the real frozen sidecar — dev placeholders
exit(1) so a bad bundle fails loudly on first launch.

## Required repo secrets (macOS signing)

| secret | what |
| --- | --- |
| `APPLE_CERTIFICATE` | base64 of the Developer ID Application `.p12` (`base64 -i cert.p12 \| pbcopy`) |
| `APPLE_CERTIFICATE_PASSWORD` | password the `.p12` was exported with |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Caleb … (TEAMID)` |
| `APPLE_ID` | Apple ID email used for notarization |
| `APPLE_APP_SPECIFIC_PASSWORD` | app-specific password for that Apple ID (appleid.apple.com) |
| `APPLE_TEAM_ID` | 10-char team id |

Prereqs (one-time): Apple Developer Program membership; create a
"Developer ID Application" certificate in Xcode or developer.apple.com,
export as `.p12`.

Until the secrets exist, the macOS job still builds — unsigned (Gatekeeper
will warn). Windows/Linux are always unsigned for now.

## Homebrew cask

After publishing a release, update the tap (cable729/homebrew-tap):

```ruby
cask "inkterop" do
  version "X.Y.Z"
  sha256 "<shasum -a 256 Inkterop_X.Y.Z_aarch64.dmg>"

  url "https://github.com/cable729/inkterop/releases/download/v#{version}/Inkterop_#{version}_aarch64.dmg"
  name "Inkterop"
  desc "Sync and convert handwritten notes (reMarkable, GoodNotes, Notability, …)"
  homepage "https://cable729.github.io/inkterop/"

  depends_on macos: ">= :ventura"
  depends_on arch: :arm64

  app "Inkterop.app"

  zap trash: [
    "~/.cache/inkterop",
    "~/.config/inkterop",
  ]
end
```

## Local release build

```sh
core/packaging/build-sidecar.sh          # freeze the engine for this host
cd app && npm run tauri build            # .app + .dmg in src-tauri/target/release/bundle/
```

`tauri dev` never needs the sidecar (it spawns the daemon via
`uv run --project ../core inkterop daemon`).
