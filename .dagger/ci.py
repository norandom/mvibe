"""The same container pipeline runs from WSL and GitHub Actions via dagger run."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

import dagger

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE = [
    ".git", ".venv", ".venv-*", "**/.venv", "**/__pycache__", "**/node_modules",
    "**/dist", "dist", "reports", ".tools", ".agents", ".codex",
    ".kiro/settings", ".tmp", "HANDOFF.md", "**/.host", "**/.compat",
    "**/.task43-debug-*", ".pytest_cache", ".ruff_cache",
]


def prepared(client: dagger.Client) -> dagger.Container:
    source = client.host().directory(str(ROOT), exclude=EXCLUDE)
    uv = client.container().from_("ghcr.io/astral-sh/uv:0.13.5").file("/uv")
    bun = client.container().from_("oven/bun:1.4.4").file("/usr/local/bin/bun")
    return (
        client.container().from_("python:3.12-slim-bookworm")
        .with_exec(["apt-get", "update"])
        .with_exec(["apt-get", "install", "-y", "--no-install-recommends", "git", "ca-certificates", "shellcheck=0.9.0-1"])
        .with_file("/usr/local/bin/uv", uv)
        .with_file("/usr/local/bin/bun", bun)
        .with_mounted_cache("/root/.cache/uv", client.cache_volume("mvibe-uv"))
        .with_mounted_cache("/root/.bun/install/cache", client.cache_volume("mvibe-bun"))
        .with_directory("/src", source).with_workdir("/src")
        .with_env_variable("UV_LINK_MODE", "copy")
        # Test installation probes retain this prepared uv cache across isolated homes.
        .with_env_variable("UV_CACHE_DIR", "/root/.cache/uv")
        .with_env_variable("LOCALAPPDATA", "/root/.local/share")
        .with_exec(["uv", "sync", "--locked", "--python", "3.12"])
        # PEP 723 uses independent registry metadata, not just the project lock's
        # wheel cache. Prepare its inert --help path before guarded offline tests.
        .with_exec(["uv", "run", "--python", "3.12", "--script", "proxy.py", "--help"])
        .with_workdir("/src/packages/opencode-vibe-oidc")
        .with_exec(["bun", "install", "--frozen-lockfile", "--ignore-scripts"])
        .with_workdir("/src")
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "build"])
    parser.add_argument("--output", default="dist/release")
    args = parser.parse_args()
    async with dagger.Connection(dagger.Config(log_output=sys.stderr)) as client:
        container = prepared(client)
        if args.command == "check":
            actionlint = client.container().from_("rhysd/actionlint:1.7.12").file("/usr/local/bin/actionlint")
            container = (
                container.with_file("/usr/local/bin/actionlint", actionlint)
                .with_exec(["uv", "run", "--locked", "--offline", "ruff", "check", "mvibe_auth", "mvibe_proxy", "proxy.py", "scripts", "tests", ".dagger"])
                .with_exec(["shellcheck", "install.sh"])
                .with_exec(["actionlint", ".github/workflows/release.yml"])
                .with_workdir("/src/packages/opencode-vibe-oidc")
                .with_exec(["bun", "run", "lint"])
                .with_exec(["bun", "run", "typecheck"])
                .with_exec(["bun", "run", "build"])
                .with_exec(["bun", "test"])
                .with_workdir("/src")
                .with_exec(["uv", "run", "--locked", "--offline", "pytest"])
            )
            print(await container.stdout())
        else:
            container = container.with_exec(["uv", "run", "--locked", "--offline", "python", "scripts/build_release.py", "--output", "/out", "--bun", "/usr/local/bin/bun", "--uv", "/usr/local/bin/uv"])
            await container.directory("/out").export(str(ROOT / args.output))
            print(f"Release artifacts exported to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
