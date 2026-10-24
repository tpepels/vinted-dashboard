"""In-place updates must keep Chrome's extension ID and local pairing storage."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.update_chrome_bridge import main, update_extension


def manifest(version: str, origin: str = "https://dashboard.example") -> bytes:
    return json.dumps({
        "manifest_version": 3,
        "name": "Reseller Dashboard Chrome Bridge",
        "version": version,
        "homepage_url": origin,
    }).encode()


def extension_zip(path: Path, version: str, *, origin: str = "https://dashboard.example",
                  extras: dict[str, bytes] | None = None) -> None:
    files = {
        "manifest.json": manifest(version, origin),
        "background.js": b"// updated background",
        "content.js": b"// updated content",
        "popup.js": b"// updated popup",
        "popup.html": b"<html>Updated</html>",
        "icons/icon16.png": b"test-icon",
    }
    files.update(extras or {})
    with ZipFile(path, "w") as zf:
        for name, contents in files.items():
            zf.writestr(name, contents)


def installed_extension(path: Path, version: str = "3.4.15") -> None:
    path.mkdir()
    (path / "manifest.json").write_bytes(manifest(version))
    (path / "background.js").write_text("old background", encoding="utf-8")
    (path / "local-user-notes").write_text("untouched", encoding="utf-8")


def test_updates_current_folder_without_reinstalling_or_touching_other_files(tmp_path: Path):
    folder = tmp_path / "already-loaded-in-chrome"
    installed_extension(folder)
    archive = tmp_path / "downloaded.zip"
    extension_zip(archive, "3.4.16")

    assert update_extension(archive, folder) == ("3.4.15", "3.4.16")
    assert json.loads((folder / "manifest.json").read_text())["version"] == "3.4.16"
    assert (folder / "background.js").read_bytes() == b"// updated background"
    assert (folder / "icons" / "icon16.png").read_bytes() == b"test-icon"
    assert (folder / "local-user-notes").read_text() == "untouched"
    # Same file system directory, no extension removal, no Chrome profile writes.
    assert folder.exists()


def test_never_overwrite_for_different_dashboard_or_downgrade(tmp_path: Path):
    folder = tmp_path / "extension"
    installed_extension(folder)
    wrong = tmp_path / "other-dashboard.zip"
    extension_zip(wrong, "3.4.16", origin="https://elsewhere.example")
    with pytest.raises(ValueError, match="different dashboard"):
        update_extension(wrong, folder)
    assert (folder / "background.js").read_text() == "old background"

    old = tmp_path / "old.zip"
    extension_zip(old, "3.4.14")
    with pytest.raises(ValueError, match="older"):
        update_extension(old, folder)
    assert (folder / "background.js").read_text() == "old background"


@pytest.mark.parametrize("unsafe", ["../escape", "/absolute.js", "icons\\malicious.js"])
def test_refuses_unsafe_zip_paths_before_changing_files(tmp_path: Path, unsafe: str):
    folder = tmp_path / "extension"
    installed_extension(folder)
    archive = tmp_path / "malicious.zip"
    extension_zip(archive, "3.4.16", extras={unsafe: b"malicious"})
    with pytest.raises(ValueError, match="Unsafe file path"):
        update_extension(archive, folder)
    assert (folder / "background.js").read_text() == "old background"


def test_refuses_writes_through_installed_symlinks(tmp_path: Path):
    folder = tmp_path / "extension"
    installed_extension(folder)
    external = tmp_path / "outside"
    external.mkdir()
    (folder / "icons").symlink_to(external, target_is_directory=True)
    archive = tmp_path / "downloaded.zip"
    extension_zip(archive, "3.4.16")
    with pytest.raises(ValueError, match="symlink"):
        update_extension(archive, folder)
    assert not (external / "icon16.png").exists()
    assert (folder / "background.js").read_text() == "old background"


def test_cli_remembers_the_exact_original_folder(tmp_path: Path, monkeypatch, capsys):
    folder = tmp_path / "existing-extension-folder"
    installed_extension(folder)
    first = tmp_path / "bridge16.zip"
    extension_zip(first, "3.4.16")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(sys, "argv", ["update_chrome_bridge.py", str(first),
                                      "--extension-dir", str(folder)])
    main()
    assert "v3.4.16 IN PLACE" in capsys.readouterr().out
    remembered = tmp_path / "config" / "vinted-dashboard" / "chrome-bridge-folder.json"
    assert json.loads(remembered.read_text())["extension_dir"] == str(folder)
    second = tmp_path / "bridge17.zip"
    extension_zip(second, "3.4.17")
    monkeypatch.setattr(sys, "argv", ["update_chrome_bridge.py", str(second)])
    main()
    assert json.loads((folder / "manifest.json").read_text())["version"] == "3.4.17"
    assert "Your saved pairing is unchanged" in capsys.readouterr().out
