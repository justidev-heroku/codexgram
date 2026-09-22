import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import server


def test_save_load_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "models.json"
    monkeypatch.setattr(server, "MODELS_FILE", f)
    server.save_custom_models([("opus47", "claude-opus-4-7", "Opus 4.7")])
    assert server.load_custom_models() == [("opus47", "claude-opus-4-7", "Opus 4.7")]


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MODELS_FILE", tmp_path / "nope.json")
    assert server.load_custom_models() == []


def test_load_corrupt_returns_empty(tmp_path, monkeypatch):
    f = tmp_path / "models.json"; f.write_text("{ broken")
    monkeypatch.setattr(server, "MODELS_FILE", f)
    assert server.load_custom_models() == []


def test_get_models_appends_custom(tmp_path, monkeypatch):
    f = tmp_path / "models.json"
    monkeypatch.setattr(server, "MODELS_FILE", f)
    server.save_custom_models([("fable51", "claude-fable-5-1", "Fable 5.1")])
    models = server.get_models()
    assert ("fable51", "claude-fable-5-1", "Fable 5.1") in models
    # built-ins preserved
    assert any(f == "claude-opus-5" for _, f, _ in models)


def test_get_models_dedup_builtin_id_wins(tmp_path, monkeypatch):
    f = tmp_path / "models.json"
    monkeypatch.setattr(server, "MODELS_FILE", f)
    # custom tries to shadow a built-in id -> skipped
    server.save_custom_models([("dup", "claude-opus-5", "Dup Opus")])
    models = server.get_models()
    assert ("dup", "claude-opus-5", "Dup Opus") not in models
    assert sum(1 for _, fid, _ in models if fid == "claude-opus-5") == 1
