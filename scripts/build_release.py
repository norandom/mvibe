"""Build the portable wheel/Bun release; run with uv run --locked python."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def archive_release(stage: Path, output: Path) -> Path:
    """Archive the explicit staging tree, with a checksum for distribution."""
    archive = output / f"{stage.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(stage.parent).as_posix())
    artifacts = [archive]
    for wheel in sorted((stage / "wheels").glob("*.whl")):
        destination = output / wheel.name
        shutil.copy2(wheel, destination)
        artifacts.append(destination)
    checksums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in artifacts)
    (output / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    return archive


def build(output: Path, uv: str, bun: str) -> Path:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "packages/opencode-vibe-oidc/package.json").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    if version != package["version"]:
        raise ValueError("Python and provider versions must match")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mvibe-release-") as temporary:
        stage = Path(temporary) / f"mvibe-{version}"
        wheels = stage / "wheels"
        wheels.mkdir(parents=True)
        run([uv, "build", "--wheel", "--out-dir", str(wheels)], ROOT)
        wheel, = wheels.glob("*.whl")
        if not wheel.name.endswith("-py3-none-any.whl"):
            raise ValueError("Release requires a universal Python wheel")
        target = stage / "packages/opencode-vibe-oidc"
        source = ROOT / "packages/opencode-vibe-oidc"
        for entry, destination in [("src/index.ts", "dist/index.js"), ("scripts/install.ts", "scripts/install.js")]:
            path = target / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            externals = (["--external=@opencode/plugin", "--external=@opencode/plugin/*",
                          "--external=effect", "--external=effect/*"] if entry == "src/index.ts" else [])
            run([bun, "build", str(source / entry), "--target=bun", "--format=esm", *externals,
                 "--outfile", str(path)], source)
        # The host supplies its own plugin SDK and shared Effect instance.
        (target / "package.json").write_text(json.dumps({"name": package["name"], "version": version,
            "type": "module", "exports": {".": {"import": "./dist/index.js"}}}, indent=2) + "\n", encoding="utf-8")
        lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        constraints = sorted({f'{p["name"]}=={p["version"]}' for p in lock["package"]
                              if p["name"] != "mvibe-auth" and "registry" in p["source"]})
        runtime = ('[project]\nname = "mvibe-release-runtime"\nversion = ' + json.dumps(version)
                   + '\nrequires-python = ">=3.12"\ndependencies = ["mvibe-auth[proxy]==' + version + '"]\n'
                   + '\n[tool.uv.sources]\nmvibe-auth = { path = "wheels/' + wheel.name + '" }\n'
                   + '\n[tool.uv]\npackage = false\nconstraint-dependencies = ' + json.dumps(constraints) + '\n')
        (stage / "pyproject.toml").write_text(runtime, encoding="utf-8")
        run([uv, "lock", "--project", str(stage), "--python", "3.12"], ROOT)
        (stage / "release.json").write_text(json.dumps({"format": 1, "version": version,
            "python": ">=3.12", "bun": "1.4.4", "opencode": "2", "wheel": f"wheels/{wheel.name}"}, indent=2) + "\n", encoding="utf-8")
        for name in ["install.sh", "install.ps1", "proxy.py", "README.md", "LICENSE", "LICENSE.vibe"]:
            shutil.copy2(ROOT / name, stage / name)
        (stage / "docs").mkdir()
        # Ship the public guides linked from README, including adopter setup.
        # Reports, specifications and source trees are outside this allowlist.
        for guide in sorted((ROOT / "docs").glob("*.md")):
            shutil.copy2(guide, stage / "docs" / guide.name)
        shutil.copytree(ROOT / "docs/images", stage / "docs/images")
        # Preserve third-party notices for bundled JavaScript dependencies.
        for path in sorted((source / "node_modules").rglob("*")):
            if path.is_file() and path.name.lower().split(".")[0] in {"license", "licence", "notice", "notices"}:
                notice_name = path.relative_to(source / "node_modules").as_posix().replace("node_modules/", "").replace("/", "__")
                destination = stage / "licenses" / notice_name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
        return archive_release(stage, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", default=os.environ.get("MVIBE_UV", "uv"))
    parser.add_argument("--bun", default=os.environ.get("MVIBE_BUN", "bun"))
    args = parser.parse_args()
    print(build(args.output.resolve(), args.uv, args.bun))


if __name__ == "__main__":
    main()
