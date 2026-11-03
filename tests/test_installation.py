"""Operational examples run with cached uv dependencies and no live account."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from queue import Queue
from threading import Thread
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name,heading", [
    ("vibe-provenance.md", "# Vibe source provenance"),
    ("opencode-provider.md", "# OpenCode Mistral-Vibe provider"),
])
def test_required_operator_artifact(name, heading):
    assert (ROOT / "docs" / name).read_text(encoding="utf-8").startswith(heading)


def test_script_runtime_dependency_parity():
    script = (ROOT / "proxy.py").read_text(encoding="utf-8").split("# ///")[1]
    inline = tomllib.loads("\n".join(line.removeprefix("# ") for line in script.splitlines()[1:]))
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert inline["requires-python"] == package["requires-python"] == ">=3.12"
    assert set(inline["dependencies"]) == set(package["dependencies"] + package["optional-dependencies"]["proxy"])


def test_preserved_source_inputs_match_documented_baseline():
    text = (ROOT / "docs/vibe-provenance.md").read_text(encoding="utf-8").upper()
    for name in ("THIRD_PARTY_NOTICES", "mvibe_auth/_credentials_port.py", "mvibe_auth/_http.py", "mvibe_auth/concurrency.py"):
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest().upper() in text, name


def test_clean_no_dev_install_and_copied_script(tmp_path):
    # Preserve the prepared cache location, while every application home remains
    # isolated by conftest. Never discover/install another Python interpreter.
    from conftest import _original_env
    uv = shutil.which("uv")
    assert uv, "Prepare uv and its locked dependency cache before offline checks"
    cache = _original_env.get("UV_CACHE_DIR") or str(Path(_original_env["LOCALAPPDATA"]) / "uv/cache")
    env = {**os.environ, "UV_CACHE_DIR": cache, "UV_OFFLINE": "true", "UV_PYTHON_DOWNLOADS": "never",
           "UV_PROJECT_ENVIRONMENT": str(tmp_path / "runtime"), "VIBE_TEST_DISABLE_KEYRING": "1"}
    source = tmp_path / "source"
    source.mkdir()
    for name in ("pyproject.toml", "uv.lock", "README.md", "LICENSE", "THIRD_PARTY_NOTICES", "proxy.py"):
        shutil.copy2(ROOT / name, source / name)
    for name in ("mvibe_auth", "mvibe_proxy"):
        shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns("__pycache__"))

    def run(args, *, input=None, cwd=source):
        result = subprocess.run(args, cwd=cwd, env=env, input=input, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    run([uv, "sync", "--locked", "--offline", "--python", sys.executable, "--no-dev", "--no-editable"])
    runtime = tmp_path / "runtime" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    probe = """import importlib.util, importlib.metadata, pathlib, sys
assert importlib.util.find_spec('fastapi') is None
assert importlib.util.find_spec('uvicorn') is None
import mvibe_auth, mvibe_proxy
assert not pathlib.Path(mvibe_auth.__file__).is_relative_to(pathlib.Path(sys.argv[1]))
assert importlib.metadata.version('mvibe-auth') == VERSION
""".replace("VERSION", repr(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]))
    run([str(runtime), "-I", "-c", probe, str(source)], cwd=tmp_path)
    # Wait for each reply before EOF: EOF deliberately cancels pending dispatch.
    frames = [{"version": 1, "id": "hello", "method": "hello", "params": {}},
              {"version": 9, "id": "old", "method": "hello", "params": {}}]
    child = subprocess.Popen([str(runtime), "-I", "-m", "mvibe_auth", "bridge"], cwd=tmp_path,
              env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = Queue()
    reader = Thread(target=lambda: [output.put(line) for line in child.stdout], daemon=True)
    reader.start()
    replies = []
    try:
        for frame in frames:
            child.stdin.write((json.dumps(frame) + "\n").encode("utf-8"))
            child.stdin.flush()
            replies.append(json.loads(output.get(timeout=15)))
    finally:
        child.stdin.close()
        assert child.wait(timeout=15) == 0
        reader.join(timeout=2)
        assert child.stderr.read() == b""
        child.stdout.close()
        child.stderr.close()
    hello = next(reply for reply in replies if reply["id"] == "hello")
    assert hello["ok"], hello
    assert hello["result"]["protocol"] == 1
    assert hello["result"]["api_version"] == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    mismatch = next(reply for reply in replies if not reply["ok"])
    assert mismatch["error"]["code"] == "unsupported_version"
    assert "install compatible" in mismatch["error"]["message"].lower()
    # Install optional proxy separately; --help never logs in or opens a socket.
    run([uv, "sync", "--locked", "--offline", "--python", sys.executable, "--no-dev", "--no-editable", "--extra", "proxy"])
    run([str(runtime), "-I", "-c", "import mvibe_proxy.streaming"], cwd=tmp_path)
    assert "--login" in run([str(runtime), str(source / "proxy.py"), "--help"])
    # PEP 723 is an independent environment; the copied two packages must suffice.
    assert "--port" in run([uv, "run", "--offline", "--script", str(source / "proxy.py"), "--help"])
