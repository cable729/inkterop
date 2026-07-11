"""TOML config for the mirror engine.

Default location: ~/.config/inkterop/config.toml (created on first run).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .render import LETTER_LANDSCAPE, LETTER_PORTRAIT, RenderConfig

DEFAULT_PATH = Path.home() / ".config/inkterop/config.toml"

DEFAULT_TOML = """\
# inkterop configuration

[output]
# Where mirrored PDFs go. Default: iCloud Drive/reMarkable
dir = "~/Library/Mobile Documents/com~apple~CloudDocs/reMarkable"

[pages]
# "uniform": every page the same size (grown pages scaled to fit)
# "native": pages keep their natural (possibly grown) size
normalize = "uniform"
# Target size in points when uniform. letter = 792x612 / 612x792.
landscape = [792, 612]
portrait = [612, 792]

[pens]
# "faithful": solid device-like ink   "rmc": community-renderer look
style = "faithful"

[scope]
# Document types to mirror. Annotated PDFs/EPUBs render handwriting only
# for now (base-PDF merge is planned).
notebooks = true
pdfs = false
epubs = false
# Folder paths to exclude (library paths, e.g. "Books" or "School/Old")
exclude = []

[sync]
# Default output format: pdf | svg | png | inkz (per-note overrides live
# in rules.toml, managed by the app).
format = "pdf"

[sources.remarkable]
enabled = true
# cache_dir = "/path/to/xochitl-format/dir"   # override the desktop cache

# Additional folder sources: any folder of note files inkterop can read
# (.goodnotes, .ntb, .sba, .xopp, ...). Repeat the block per folder.
# [[sources.folders]]
# path = "~/Notes/GoodNotes exports"
# name = "GoodNotes exports"

[sources.goodnotes]
enabled = false   # experimental: scan the Mac GoodNotes app container

[sources.notability]
enabled = false   # experimental: scan the Mac Notability app container
"""


@dataclass
class Config:
    output_dir: Path = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/reMarkable"
    normalize: str = "uniform"
    landscape: tuple[float, float] = LETTER_LANDSCAPE
    portrait: tuple[float, float] = LETTER_PORTRAIT
    pen_style: str = "faithful"
    notebooks: bool = True
    pdfs: bool = False
    epubs: bool = False
    exclude: list[str] = field(default_factory=list)
    # sync
    default_format: str = "pdf"
    source_remarkable: bool = True
    remarkable_cache_dir: Path | None = None
    source_folders: list[dict] = field(default_factory=list)
    source_goodnotes: bool = False
    source_notability: bool = False
    path: Path | None = None  # where this config was loaded from

    def render_config(self) -> RenderConfig:
        return RenderConfig(
            pen_style=self.pen_style,
            normalize=self.normalize,
            target_landscape=self.landscape,
            target_portrait=self.portrait,
        )

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or DEFAULT_PATH
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_TOML)
        data = tomllib.loads(path.read_text())
        cfg = cls()
        out = data.get("output", {})
        if "dir" in out:
            cfg.output_dir = Path(out["dir"]).expanduser()
        pages = data.get("pages", {})
        cfg.normalize = pages.get("normalize", cfg.normalize)
        if "landscape" in pages:
            cfg.landscape = tuple(pages["landscape"])
        if "portrait" in pages:
            cfg.portrait = tuple(pages["portrait"])
        cfg.pen_style = data.get("pens", {}).get("style", cfg.pen_style)
        scope = data.get("scope", {})
        cfg.notebooks = scope.get("notebooks", cfg.notebooks)
        cfg.pdfs = scope.get("pdfs", cfg.pdfs)
        cfg.epubs = scope.get("epubs", cfg.epubs)
        cfg.exclude = list(scope.get("exclude", []))
        cfg.default_format = data.get("sync", {}).get("format",
                                                      cfg.default_format)
        sources = data.get("sources", {})
        rm = sources.get("remarkable", {})
        cfg.source_remarkable = rm.get("enabled", cfg.source_remarkable)
        if "cache_dir" in rm:
            cfg.remarkable_cache_dir = Path(rm["cache_dir"]).expanduser()
        cfg.source_folders = [dict(f) for f in sources.get("folders", [])
                              if isinstance(f, dict) and f.get("path")]
        cfg.source_goodnotes = sources.get("goodnotes", {}).get(
            "enabled", cfg.source_goodnotes)
        cfg.source_notability = sources.get("notability", {}).get(
            "enabled", cfg.source_notability)
        cfg.path = path
        return cfg

    # -- GUI support -----------------------------------------------------

    def to_dict(self) -> dict:
        """JSON-safe view for the daemon's config.get."""
        return {
            "output_dir": str(self.output_dir),
            "normalize": self.normalize,
            "landscape": list(self.landscape),
            "portrait": list(self.portrait),
            "pen_style": self.pen_style,
            "notebooks": self.notebooks,
            "pdfs": self.pdfs,
            "epubs": self.epubs,
            "exclude": self.exclude,
            "default_format": self.default_format,
            "source_remarkable": self.source_remarkable,
            "remarkable_cache_dir": (str(self.remarkable_cache_dir)
                                     if self.remarkable_cache_dir else None),
            "source_folders": self.source_folders,
            "source_goodnotes": self.source_goodnotes,
            "source_notability": self.source_notability,
        }

    def update_file(self, changes: dict) -> None:
        """Apply `changes` (keys as in to_dict) to the TOML on disk,
        preserving comments and unknown keys (tomlkit round-trip)."""
        import tomlkit

        path = self.path or DEFAULT_PATH
        doc = tomlkit.parse(path.read_text()) if path.exists() \
            else tomlkit.parse(DEFAULT_TOML)

        def section(*names):
            cur = doc
            for n in names:
                if n not in cur:
                    cur[n] = tomlkit.table()
                cur = cur[n]
            return cur

        simple = {
            "output_dir": ("output", "dir"),
            "normalize": ("pages", "normalize"),
            "landscape": ("pages", "landscape"),
            "portrait": ("pages", "portrait"),
            "pen_style": ("pens", "style"),
            "notebooks": ("scope", "notebooks"),
            "pdfs": ("scope", "pdfs"),
            "epubs": ("scope", "epubs"),
            "exclude": ("scope", "exclude"),
            "default_format": ("sync", "format"),
            "source_remarkable": ("sources", "remarkable", "enabled"),
            "source_goodnotes": ("sources", "goodnotes", "enabled"),
            "source_notability": ("sources", "notability", "enabled"),
            "remarkable_cache_dir": ("sources", "remarkable", "cache_dir"),
        }
        for key, value in changes.items():
            if key == "source_folders":
                aot = tomlkit.aot()
                for f in value:
                    t = tomlkit.table()
                    t["path"] = str(f["path"])
                    if f.get("name"):
                        t["name"] = f["name"]
                    if f.get("id"):
                        t["id"] = f["id"]
                    aot.append(t)
                section("sources")["folders"] = aot
            elif key in simple:
                *parents, leaf = simple[key]
                if value is None:
                    section(*parents).pop(leaf, None)
                else:
                    section(*parents)[leaf] = value
            else:
                raise ValueError(f"unknown config key {key!r}")

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".toml.tmp")
        tmp.write_text(tomlkit.dumps(doc))
        tmp.replace(path)
