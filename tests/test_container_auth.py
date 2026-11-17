"""The container export copies only the native credential and rotates atomically."""
import importlib.util
from pathlib import Path
import sys

import pytest

pytest.importorskip("keyrings.alt.file")
spec = importlib.util.spec_from_file_location(
    "container_auth", Path(__file__).resolve().parents[1] / "scripts/export-container-auth.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_export_and_rotation(tmp_path, monkeypatch):
    directory = tmp_path / "private"
    monkeypatch.setattr(module, "_read_native_credential", lambda: "synthetic-one")
    module.export(directory)
    backend = module.PlaintextKeyring()
    backend.file_path = str(directory / "keyring_pass.cfg")
    assert backend.get_password("ai.mistral.vibe", "MISTRAL_API_KEY") == "synthetic-one"
    monkeypatch.setattr(module, "_read_native_credential", lambda: "synthetic-two")
    module.export(directory)
    assert backend.get_password("ai.mistral.vibe", "MISTRAL_API_KEY") == "synthetic-two"
    assert list(directory.iterdir()) == [directory / "keyring_pass.cfg"]
    if sys.platform != "win32":
        assert directory.stat().st_mode & 0o777 == 0o700
        assert Path(backend.file_path).stat().st_mode & 0o777 == 0o600


def test_missing_login_preserves_export(tmp_path, monkeypatch):
    target = tmp_path / "keyring_pass.cfg"
    target.write_text("existing snapshot")
    monkeypatch.setattr(module, "_read_native_credential", lambda: None)
    with pytest.raises(RuntimeError, match="No native Vibe login"):
        module.export(tmp_path)
    assert target.read_text() == "existing snapshot"
