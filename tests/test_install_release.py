"""Release archives contain only runnable artifacts and a locked wheel runtime."""
from pathlib import Path
import importlib.util
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def test_release_artifact_contract(tmp_path):
    spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts/build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stage = tmp_path / "mvibe-0.1.0"
    stage.mkdir()
    for name in ["release.json", "uv.lock", "pyproject.toml", "wheels/mvibe_auth-0.1.0-py3-none-any.whl",
                 "packages/opencode-vibe-oidc/dist/index.js", "packages/opencode-vibe-oidc/scripts/install.js"]:
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": "0.1.0"}) if name == "release.json" else "artifact")
    archive = module.archive_release(stage, tmp_path)
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert any(name.endswith("-py3-none-any.whl") for name in names)
        assert any(name.endswith("scripts/install.js") for name in names)
        assert not any("node_modules" in name or name.endswith(".ts") for name in names)
    checksums = [line.split() for line in (tmp_path / "SHA256SUMS").read_text().splitlines()]
    assert checksums[0] == [hashlib.sha256(archive.read_bytes()).hexdigest(), archive.name]
    wheel = tmp_path / "mvibe_auth-0.1.0-py3-none-any.whl"
    assert checksums[1] == [hashlib.sha256(wheel.read_bytes()).hexdigest(), wheel.name]


def test_builder_stages_wheel_and_bundles_without_shipping_build_inputs(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts/build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def prepared_tool(command, cwd):
        calls.append(command)
        if command[1:3] == ["build", "--wheel"]:
            (Path(command[-1]) / "mvibe_auth-0.1.0-py3-none-any.whl").write_bytes(b"wheel fixture")
        elif command[0] == "native-bun":
            Path(command[-1]).write_text("export const bundled = true;")
        elif command[1] == "lock":
            (Path(command[3]) / "uv.lock").write_text("version = 1\n")
        else:
            raise AssertionError(command)

    monkeypatch.setattr(module, "run", prepared_tool)
    archive = module.build(tmp_path, "native-uv", "native-bun")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        prefix = "mvibe-0.1.0/"
        manifest = json.loads(bundle.read(prefix + "release.json"))
        assert prefix + manifest["wheel"] in names
        assert not any("node_modules" in name or "/src/" in name or name.endswith(".ts") for name in names)
        runtime = bundle.read(prefix + "pyproject.toml").decode()
        assert 'mvibe-auth[proxy]==0.1.0' in runtime
        assert 'wheels/mvibe_auth-0.1.0-py3-none-any.whl' in runtime
        assert '[build-system]' not in runtime
        wrapper = bundle.read(prefix + "install.sh").decode()
        assert 'scripts/install.js' in wrapper
        assert prefix + "LICENSE.vibe" in names
        for guide in ("desktop-setup.md", "proxy-guide.md", "development.md", "vibe-authentication.md"):
            assert prefix + "docs/" + guide in names
    assert sum(command[0] == "native-bun" for command in calls) == 2
    assert ["native-uv", "lock"] == calls[-1][:2]
    provider_command = next(command for command in calls if any(arg.replace("\\", "/").endswith("src/index.ts") for arg in command))
    installer_command = next(command for command in calls if any(arg.replace("\\", "/").endswith("scripts/install.ts") for arg in command))
    for dependency in ["@opencode/plugin", "@opencode/plugin/*", "effect", "effect/*"]:
        assert f"--external={dependency}" in provider_command, "Provider must share the host SDK and Effect runtime"
        assert f"--external={dependency}" not in installer_command
