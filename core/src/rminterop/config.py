"""TOML config for the mirror engine.

Default location: ~/.config/rminterop/config.toml (created on first run).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .render import LETTER_LANDSCAPE, LETTER_PORTRAIT, RenderConfig

DEFAULT_PATH = Path.home() / ".config/rminterop/config.toml"

DEFAULT_TOML = """\
# rminterop configuration

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
        return cfg
