"""Sync engine, rules, sources, sinks, and daemon tests.

Everything runs against a synthesized xochitl-layout cache in tmp_path
(real fixture .rm pages) — never the real desktop cache — and against a
tmp config path so rules.toml/status.json stay out of ~/.config.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from inkterop.config import Config
from inkterop.sync.engine import STATE_NAME, SyncEngine
from inkterop.sync.rules import DocRule, Rules
from inkterop.sync.sources import FolderSource, RemarkableCacheSource

FIXTURES = Path(__file__).parent / "fixtures"
RM_PAGE = FIXTURES / "remarkable" / "ballpoint-small.rm"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_cache(root: Path, docs: dict[str, dict]) -> Path:
    """Synthesize a xochitl-format cache. docs: uuid -> metadata overrides."""
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    for uuid, spec in docs.items():
        meta = {"visibleName": spec.get("name", uuid),
                "parent": spec.get("parent", ""),
                "type": spec.get("type", "DocumentType"),
                "lastModified": str(spec.get("mtime", 1000))}
        (cache / f"{uuid}.metadata").write_text(json.dumps(meta))
        if meta["type"] == "DocumentType":
            content = {"fileType": "notebook", "orientation": "portrait",
                       "cPages": {"pages": [{"id": "p1"}]}}
            (cache / f"{uuid}.content").write_text(json.dumps(content))
            page_dir = cache / uuid
            page_dir.mkdir(exist_ok=True)
            shutil.copy(RM_PAGE, page_dir / "p1.rm")
    return cache


@pytest.fixture
def env(tmp_path):
    """(cfg, cache, engine) with two docs, one inside a folder."""
    cache = make_cache(tmp_path, {
        "doc-a": {"name": "Note A", "mtime": 1000},
        "folder-1": {"name": "School", "type": "CollectionType"},
        "doc-b": {"name": "Note B", "parent": "folder-1", "mtime": 2000},
    })
    cfg = Config(output_dir=tmp_path / "out",
                 path=tmp_path / "config" / "config.toml")
    cfg.path.parent.mkdir(parents=True)
    engine = SyncEngine(cfg, sources=[RemarkableCacheSource(cache)])
    return cfg, cache, engine


def touch_doc(cache: Path, uuid: str, mtime: int) -> None:
    meta_path = cache / f"{uuid}.metadata"
    meta = json.loads(meta_path.read_text())
    meta["lastModified"] = str(mtime)
    meta_path.write_text(json.dumps(meta))


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


def test_rules_roundtrip(tmp_path):
    path = tmp_path / "rules.toml"
    rules = Rules()
    rules.set_doc("remarkable", "u1", blocked=True)
    rules.set_doc("remarkable", "u2", name="Renamed", format="svg")
    rules.set_folder("remarkable", "School/Old", blocked=True)
    rules.save(path)

    loaded = Rules.load(path)
    assert loaded.rule_for("remarkable", "u1").blocked
    assert loaded.rule_for("remarkable", "u2").name == "Renamed"
    assert loaded.rule_for("remarkable", "u2").format == "svg"
    assert not loaded.wanted("remarkable", "u1")
    assert loaded.wanted("remarkable", "u2")
    assert not loaded.wanted("remarkable", "x", "School/Old/Deep")
    assert loaded.wanted("remarkable", "x", "School")


def test_rules_allowlist_mode():
    rules = Rules(mode="allowlist")
    rules.set_doc("remarkable", "u1", allowed=True)
    rules.set_folder("remarkable", "Keep", allowed=True)
    assert rules.wanted("remarkable", "u1")
    assert rules.wanted("remarkable", "any", "Keep/Sub")
    assert not rules.wanted("remarkable", "u2")


def test_rules_clearing_empties_entry():
    rules = Rules()
    rules.set_doc("remarkable", "u1", blocked=True)
    rules.set_doc("remarkable", "u1", blocked=False)
    assert rules.docs == {}


def test_rules_rejects_bad_format():
    rules = Rules()
    with pytest.raises(ValueError):
        rules.set_doc("remarkable", "u1", format="docx")


def test_rules_corrupt_file_recovers(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text("mode = [broken")
    loaded = Rules.load(path)
    assert loaded.mode == "blocklist"
    assert path.with_suffix(".toml.broken").exists()


def test_docrule_roundtrip():
    r = DocRule(blocked=True, name="X", format="png")
    assert DocRule.from_dict(r.to_dict()) == r


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


def test_sync_renders_then_skips(env):
    cfg, cache, engine = env
    s1 = engine.sync_once()
    assert s1["rendered"] == 2 and s1["failed"] == 0
    assert (cfg.output_dir / "Note A.pdf").exists()
    assert (cfg.output_dir / "School" / "Note B.pdf").exists()

    s2 = engine.sync_once()
    assert s2["rendered"] == 0 and s2["skipped"] == 2

    touch_doc(cache, "doc-a", 1001)
    s3 = engine.sync_once()
    assert s3["rendered"] == 1 and s3["skipped"] == 1


def test_sync_block_removes_output(env):
    cfg, _, engine = env
    engine.sync_once()
    rules = engine.load_rules()
    rules.set_doc("remarkable", "doc-a", blocked=True)
    rules.save(engine.rules_path)

    s = engine.sync_once()
    assert s["removed"] == 1
    assert not (cfg.output_dir / "Note A.pdf").exists()
    assert (cfg.output_dir / "School" / "Note B.pdf").exists()

    # Unblock: it comes back.
    rules.set_doc("remarkable", "doc-a", blocked=False)
    rules.save(engine.rules_path)
    assert engine.sync_once()["rendered"] == 1
    assert (cfg.output_dir / "Note A.pdf").exists()


def test_sync_rename_and_folder_override(env):
    cfg, _, engine = env
    engine.sync_once()
    rules = engine.load_rules()
    rules.set_doc("remarkable", "doc-a", name="Aliased", folder="Custom/Deep")
    rules.save(engine.rules_path)

    engine.sync_once()
    assert (cfg.output_dir / "Custom" / "Deep" / "Aliased.pdf").exists()
    assert not (cfg.output_dir / "Note A.pdf").exists()


def test_sync_format_override_multifile(env):
    cfg, _, engine = env
    rules = engine.load_rules()
    rules.set_doc("remarkable", "doc-a", format="png")
    rules.set_doc("remarkable", "doc-b", format="svg")
    rules.save(engine.rules_path)

    s = engine.sync_once()
    assert s["failed"] == 0
    assert (cfg.output_dir / "Note A.png").exists()
    assert (cfg.output_dir / "School" / "Note B.svg").exists()
    # And skipping works for non-pdf outputs on the second pass.
    assert engine.sync_once()["skipped"] == 2


def test_sync_folder_block(env):
    cfg, _, engine = env
    rules = engine.load_rules()
    rules.set_folder("remarkable", "School", blocked=True)
    rules.save(engine.rules_path)
    s = engine.sync_once()
    assert s["rendered"] == 1
    assert not (cfg.output_dir / "School").exists()


def test_sync_scope_config(env):
    cfg, _, engine = env
    cfg.notebooks = False
    assert engine.sync_once()["documents"] == 0


def test_legacy_v1_state_migrates(env):
    cfg, _, engine = env
    engine.sync_once()
    state_path = cfg.output_dir / STATE_NAME
    state = json.loads(state_path.read_text())
    # Rewrite as the pre-sync mirror would have left it.
    legacy = {key.split(":", 1)[1]: entry["mtime"]
              for key, entry in state["docs"].items()}
    state_path.write_text(json.dumps(legacy))

    s = engine.sync_once()
    assert s["rendered"] == 0 and s["skipped"] == 2
    upgraded = json.loads(state_path.read_text())
    assert upgraded["version"] == 2
    assert set(upgraded["docs"]) == {"remarkable:doc-a", "remarkable:doc-b"}


def test_failed_render_keeps_previous_output(env, monkeypatch):
    cfg, cache, engine = env
    engine.sync_once()
    touch_doc(cache, "doc-a", 5000)

    from inkterop.sync import engine as engine_mod

    def boom(*a, **k):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(engine_mod.sinks, "write_doc", boom)
    s = engine.sync_once()
    # Only the changed doc hits the sink; the unchanged one skips.
    assert s["failed"] == 1 and s["skipped"] == 1
    # A failed re-render must not delete the good older output.
    assert (cfg.output_dir / "Note A.pdf").exists()

    monkeypatch.undo()
    s = engine.sync_once()
    assert s["rendered"] == 1 and s["failed"] == 0


def test_snapshot_states(env):
    cfg, _, engine = env
    rules = engine.load_rules()
    rules.set_doc("remarkable", "doc-b", blocked=True)
    rules.save(engine.rules_path)

    snap = engine.snapshot()
    assert snap["sources"][0]["id"] == "remarkable"
    states = {d["id"]: d["state"] for d in snap["docs"]}
    assert states == {"doc-a": "pending", "doc-b": "blocked"}

    engine.sync_once()
    snap = engine.snapshot()
    states = {d["id"]: d["state"] for d in snap["docs"]}
    assert states == {"doc-a": "synced", "doc-b": "blocked"}


# ---------------------------------------------------------------------------
# folder source
# ---------------------------------------------------------------------------


def test_folder_source(tmp_path):
    root = tmp_path / "notes"
    (root / "sub").mkdir(parents=True)
    shutil.copy(RM_PAGE, root / "sub" / "page.rm")
    (root / "ignore.txt").write_text("not a note")

    src = FolderSource(root, "folder-1", "Test notes")
    docs = src.list_documents()
    assert [d.doc_id for d in docs] == ["sub/page.rm"]
    assert docs[0].folder == "sub"
    assert docs[0].kind == "file"

    cfg = Config(output_dir=tmp_path / "out",
                 path=tmp_path / "config" / "config.toml")
    cfg.path.parent.mkdir(parents=True)
    engine = SyncEngine(cfg, sources=[src])
    s = engine.sync_once()
    assert s["rendered"] == 1 and s["failed"] == 0
    assert (cfg.output_dir / "sub" / "page.pdf").exists()


# ---------------------------------------------------------------------------
# daemon (in-process)
# ---------------------------------------------------------------------------


@pytest.fixture
def daemon(tmp_path):
    from inkterop.sync.daemon import Daemon
    cache = make_cache(tmp_path, {"doc-a": {"name": "Note A", "mtime": 1000}})
    cfg = Config(output_dir=tmp_path / "out",
                 path=tmp_path / "config" / "config.toml")
    cfg.path.parent.mkdir(parents=True)
    cfg.source_remarkable = True
    cfg.remarkable_cache_dir = cache
    d = Daemon(cfg, watch=False)
    d.engine = SyncEngine(cfg, sources=[RemarkableCacheSource(cache)])
    return d


def test_daemon_ping_and_formats(daemon):
    assert daemon.dispatch("ping", {})["pong"] is True
    fmts = daemon.dispatch("formats.list", {})
    assert "pdf" in fmts["sink_formats"]
    assert any(w["id"] == "xopp" and w["validated"] for w in fmts["writers"])


def test_daemon_library_rules_sync(daemon, tmp_path):
    lib = daemon.dispatch("library.list", {})
    assert [d["name"] for d in lib["docs"]] == ["Note A"]
    assert lib["docs"][0]["state"] == "pending"

    daemon.dispatch("rules.set_doc",
                    {"source": "remarkable", "id": "doc-a", "name": "Zed"})
    summary = daemon.dispatch("sync.now", {})
    assert summary["rendered"] == 1
    assert (daemon.cfg.output_dir / "Zed.pdf").exists()

    lib = daemon.dispatch("library.list", {})
    assert lib["docs"][0]["state"] == "synced"
    assert lib["docs"][0]["output"] == "Zed.pdf"


def test_daemon_thumbnail(daemon, tmp_path, monkeypatch):
    import inkterop.sync.daemon as dmod
    monkeypatch.setattr(dmod, "THUMB_DIR", tmp_path / "thumbs")
    out = daemon.dispatch("thumbnail.get", {"key": "remarkable:doc-a"})
    p = Path(out["path"])
    assert p.exists() and p.suffix == ".png" and p.stat().st_size > 0
    # Cached on the second call (same mtime -> same path).
    assert daemon.dispatch("thumbnail.get",
                           {"key": "remarkable:doc-a"})["path"] == out["path"]


def test_daemon_convert_run(daemon, tmp_path):
    out = tmp_path / "converted.pdf"
    res = daemon.dispatch("convert.run",
                          {"input": str(RM_PAGE), "output": str(out)})
    assert out.exists() and res["pages"] == 1


def test_daemon_config_set(daemon):
    cfgd = daemon.dispatch("config.set", {"changes": {"pdfs": True}})
    assert cfgd["pdfs"] is True
    # Round-trips through the TOML file on disk.
    assert Config.load(daemon.cfg.path).pdfs is True


def test_daemon_unknown_method(daemon):
    from inkterop.sync.daemon import RpcError
    with pytest.raises(RpcError):
        daemon.dispatch("nope.nothing", {})


# ---------------------------------------------------------------------------
# daemon (subprocess smoke: real pipes, real EOF shutdown)
# ---------------------------------------------------------------------------


def test_daemon_subprocess_smoke(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f'[output]\ndir = "{tmp_path / "out"}"\n'
        '[sources.remarkable]\nenabled = false\n')
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 2, "method": "library.list"},
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "inkterop.cli",
         "--config", str(cfg_path), "daemon", "--no-watch"],
        input="".join(json.dumps(r) + "\n" for r in reqs),
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    by_id = {l.get("id"): l for l in lines if "id" in l}
    assert by_id[1]["result"]["pong"] is True
    assert by_id[2]["result"]["docs"] == []
    assert any(l.get("method") == "daemon.ready" for l in lines)
