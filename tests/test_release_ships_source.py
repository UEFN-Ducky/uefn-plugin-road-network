"""What the Store gets is said once, in plugin.json, and scripts/release.py follows it: this plugin ships
readable source (its repo is public); a plugin whose manifest does not say so ships the compiled build."""

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_zip  # noqa: E402
import release  # noqa: E402


def test_this_plugin_ships_source_and_one_that_does_not_say_ships_compiled():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["compiled"] is False and release.ships_compiled(manifest) is False
    assert release.ships_compiled({"id": "other"}) is True
    assert release.ships_compiled({"id": "other", "compiled": True}) is True


def test_a_source_zip_holds_what_the_plugin_runs_and_nothing_of_the_repo(tmp_path, monkeypatch):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    monkeypatch.setattr(build_zip, "OUT_DIR", deploy)
    kept = deploy / "older.ducky-plugin.zip"
    kept.write_bytes(b"an earlier build")
    (tmp_path / "elsewhere").mkdir()
    names = zipfile.ZipFile(build_zip.build_zip(out=tmp_path / "elsewhere" / "test.zip")).namelist()
    assert {name.split("/")[0] for name in names} == {
        "LICENSE", "assets", "backend", "listener", "plugin.json", "skills", "ui"}
    assert "backend/citygen/_kit.py" in names and "backend/worker.py" in names
    assert kept.is_file()  # a zip built somewhere else supersedes nothing in deploy/
