"""Native release-install smoke without login, credentials or inference."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


def run(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=240)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args[0]}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def smoke(archive: Path, uv: str, bun: str, opencode: str | None) -> None:
    uv = str(Path(shutil.which(uv) or uv).resolve())
    bun = str(Path(shutil.which(bun) or bun).resolve())
    with tempfile.TemporaryDirectory(prefix="mvibe release spaces ") as temporary:
        work = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            assert not any("node_modules" in n or n.endswith(".ts") or "/.venv/" in n for n in names)
            assert any(n.endswith("-py3-none-any.whl") for n in names)
            bundle.extractall(work)
        root, = [p for p in work.iterdir() if p.is_dir()]
        config = work / "isolated home/config/opencode.jsonc"
        config.parent.mkdir(parents=True)
        before = '{\n // retained comment\n "theme": "system",\n "plugins": [],\n}\n'
        config.write_text(before, encoding="utf-8")
        env = dict(os.environ)
        env.pop("UV_PROJECT_ENVIRONMENT", None)
        for key in ["HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                    "XDG_CONFIG_HOME", "XDG_STATE_HOME", "OPENCODE_TEST_HOME"]:
            env[key] = str(work / "isolated home" / key)
        env.update(OPENCODE_DISABLE_AUTOUPDATE="true", OPENCODE_DISABLE_MODELS_FETCH="true",
                   VIBE_TEST_DISABLE_KEYRING="1")
        if not opencode:
            fixture = work / "fake-opencode.js"
            fixture.write_text('if(process.argv.includes("--version")) console.log("opencode v2.2.5"); else process.exit(1);', encoding="utf-8")
            binary = work / ("opencode.exe" if os.name == "nt" else "opencode")
            run([bun, "build", "--compile", str(fixture), "--outfile", str(binary)], work, env)
            opencode = str(binary)
        else:
            opencode = str(Path(shutil.which(opencode) or opencode).resolve())
        if os.name == "nt":
            command = ["pwsh", "-NoProfile", "-File", str(root / "install.ps1"), "-ConfigPath", str(config),
                       "-Uv", uv, "-Bun", bun, "-OpenCode", opencode]
        else:
            command = ["bash", str(root / "install.sh"), "--config-path", str(config), "--uv", uv,
                       "--bun", bun, "--opencode", opencode]
        run(command, work, env)
        installed = config.read_text(encoding="utf-8")
        assert "// retained comment" in installed and '"theme": "system"' in installed
        backups = list(config.parent.glob("*.backup-*"))
        assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == before
        assert "already installed" in run(command, work, env)
        assert config.read_text(encoding="utf-8") == installed
        assert len(list(config.parent.glob("*.backup-*"))) == 1
        manifest = json.loads((root / "release.json").read_text(encoding="utf-8"))
        python_probe = '''import socket, webbrowser, keyring

def deny(*a, **k): raise AssertionError("External effect during import")
socket.socket.connect = deny
webbrowser.open = deny
keyring.get_password = keyring.set_password = deny
import mvibe_auth, mvibe_proxy, mvibe_proxy.streaming
import importlib.metadata
assert importlib.metadata.version("mvibe-auth") == VERSION
'''.replace("VERSION", repr(manifest["version"]))
        run([uv, "run", "--offline", "--no-sync", "--project", str(root), "python", "-c", python_probe], work, env)
        assert not (root / "packages/opencode-vibe-oidc/node_modules").exists()
        print("Native archive installation, JSONC backup/idempotency and Python import PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--uv", default=os.environ.get("MVIBE_UV", "uv"))
    parser.add_argument("--bun", default=os.environ.get("MVIBE_BUN", "bun"))
    parser.add_argument("--opencode")
    args = parser.parse_args()
    smoke(args.archive.resolve(), args.uv, args.bun, args.opencode)


if __name__ == "__main__":
    main()
