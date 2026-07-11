"""Per-document sync rules: allow/block + output-side overrides.

Stored in ~/.config/inkterop/rules.toml, separate from config.toml so GUI
shells can rewrite it freely without clobbering a hand-edited config. The
source library is NEVER modified — every rule is about what we write.

Schema:

    mode = "blocklist"            # "blocklist" (default): sync all except
                                  # blocked; "allowlist": sync only allowed
    [docs."remarkable:<uuid>"]
    blocked = true                # blocklist mode
    allowed = true                # allowlist mode
    name = "Custom output name"   # output filename override (no extension)
    folder = "Custom/Dest"        # output subfolder override
    format = "svg"                # per-doc sink: pdf | svg | png | inkz

    [folders."remarkable:School/Old"]
    blocked = true                # applies to every doc under that path
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import tomlkit

DEFAULT_PATH = Path.home() / ".config/inkterop/rules.toml"

SINK_FORMATS = ("pdf", "svg", "png", "inkz")


@dataclass
class DocRule:
    blocked: bool = False
    allowed: bool = False
    name: str | None = None
    folder: str | None = None
    format: str | None = None

    def to_dict(self) -> dict:
        out: dict = {}
        if self.blocked:
            out["blocked"] = True
        if self.allowed:
            out["allowed"] = True
        for k in ("name", "folder", "format"):
            v = getattr(self, k)
            if v:
                out[k] = v
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "DocRule":
        return cls(
            blocked=bool(d.get("blocked", False)),
            allowed=bool(d.get("allowed", False)),
            name=d.get("name") or None,
            folder=d.get("folder") or None,
            format=d.get("format") or None,
        )

    @property
    def is_empty(self) -> bool:
        return not (self.blocked or self.allowed or self.name
                    or self.folder or self.format)


@dataclass
class Rules:
    mode: str = "blocklist"  # or "allowlist"
    docs: dict[str, DocRule] = field(default_factory=dict)
    folders: dict[str, DocRule] = field(default_factory=dict)

    # -- evaluation ----------------------------------------------------

    def doc_key(self, source_id: str, doc_id: str) -> str:
        return f"{source_id}:{doc_id}"

    def rule_for(self, source_id: str, doc_id: str) -> DocRule:
        return self.docs.get(self.doc_key(source_id, doc_id), DocRule())

    def _folder_rules(self, source_id: str, folder: str) -> list[DocRule]:
        """Rules on the doc's folder or any ancestor, root-first."""
        out = []
        parts = PurePosixPath(folder).parts if folder else ()
        for i in range(len(parts) + 1):
            key = f"{source_id}:{'/'.join(parts[:i])}"
            if key in self.folders:
                out.append(self.folders[key])
        return out

    def wanted(self, source_id: str, doc_id: str, folder: str = "") -> bool:
        """Does the current mode + rules include this document?"""
        rule = self.rule_for(source_id, doc_id)
        frules = self._folder_rules(source_id, folder)
        if self.mode == "allowlist":
            if rule.blocked:
                return False
            return rule.allowed or any(f.allowed for f in frules)
        # blocklist: doc-level rule wins over folder-level
        if rule.blocked:
            return False
        if rule.allowed:
            return True
        return not any(f.blocked for f in frules)

    # -- mutation (used by the daemon) ---------------------------------

    def set_doc(self, source_id: str, doc_id: str, **fields) -> None:
        key = self.doc_key(source_id, doc_id)
        rule = self.docs.get(key, DocRule())
        for k, v in fields.items():
            if not hasattr(rule, k):
                raise ValueError(f"unknown rule field {k!r}")
            if k == "format" and v and v not in SINK_FORMATS:
                raise ValueError(f"unknown sink format {v!r}")
            setattr(rule, k, v)
        if rule.is_empty:
            self.docs.pop(key, None)
        else:
            self.docs[key] = rule

    def set_folder(self, source_id: str, folder: str, **fields) -> None:
        key = f"{source_id}:{folder}"
        rule = self.folders.get(key, DocRule())
        for k, v in fields.items():
            if not hasattr(rule, k):
                raise ValueError(f"unknown rule field {k!r}")
            setattr(rule, k, v)
        if rule.is_empty:
            self.folders.pop(key, None)
        else:
            self.folders[key] = rule

    # -- persistence ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "docs": {k: r.to_dict() for k, r in sorted(self.docs.items())},
            "folders": {k: r.to_dict()
                        for k, r in sorted(self.folders.items())},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Rules":
        mode = data.get("mode", "blocklist")
        if mode not in ("blocklist", "allowlist"):
            mode = "blocklist"
        return cls(
            mode=mode,
            docs={k: DocRule.from_dict(v)
                  for k, v in data.get("docs", {}).items()},
            folders={k: DocRule.from_dict(v)
                     for k, v in data.get("folders", {}).items()},
        )

    @classmethod
    def load(cls, path: Path | None = None) -> "Rules":
        path = path or DEFAULT_PATH
        try:
            return cls.from_dict(tomlkit.parse(path.read_text()).unwrap())
        except FileNotFoundError:
            return cls()
        except Exception:
            # A corrupt rules file must not kill the sync engine; keep the
            # broken file aside for the user rather than overwriting it.
            backup = path.with_suffix(".toml.broken")
            try:
                path.replace(backup)
            except OSError:
                pass
            return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or DEFAULT_PATH
        doc = tomlkit.document()
        doc.add(tomlkit.comment("inkterop per-document sync rules "
                                "(managed by the app; hand-edits are kept)"))
        doc["mode"] = self.mode
        for section, items in (("docs", self.docs), ("folders", self.folders)):
            if not items:
                continue
            table = tomlkit.table(is_super_table=True)
            for key, rule in sorted(items.items()):
                entry = tomlkit.table()
                for k, v in rule.to_dict().items():
                    entry[k] = v
                table[key] = entry
            doc[section] = table
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".toml.tmp")
        tmp.write_text(tomlkit.dumps(doc))
        tmp.replace(path)
