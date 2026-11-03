"""Release-critical version coupling: release major, helper API major and lockfiles stay in lockstep.

The helper answers ``hello`` with its distribution version as ``api_version``
(``mvibe_auth/bridge.py``), and the provider bridge accepts only the
``HELPER_API_MAJOR`` major (``packages/opencode-vibe-oidc/src/bridge.ts``).
A release-major bump without a matching ``HELPER_API_MAJOR`` bump made the
v2.0.0 helper look incompatible on 2026-11-03 and turned CI red; these checks
fail offline before that can happen again.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_helper_api_major_matches_release_major():
    version = _project_version()
    bridge = (ROOT / "packages" / "opencode-vibe-oidc" / "src" / "bridge.ts").read_text(encoding="utf-8")
    match = re.search(r"const HELPER_API_MAJOR = (\d+);", bridge)
    assert match, "HELPER_API_MAJOR declaration not found in src/bridge.ts"
    assert match.group(1) == version.split(".")[0], (
        f"HELPER_API_MAJOR {match.group(1)} does not match release major {version.split('.')[0]}; "
        "bump HELPER_API_MAJOR (and the synthetic hello doubles in test/bridge.test.ts and "
        "test/synthetic-bridge.py) together with the release version"
    )


def test_synthetic_helper_doubles_report_release_major():
    version = _project_version()
    major = version.split(".")[0]
    typescript = (ROOT / "packages" / "opencode-vibe-oidc" / "test" / "bridge.test.ts").read_text(encoding="utf-8")
    auto_hello = re.search(r'api_version: "(\d+)\.', typescript)
    assert auto_hello, "auto-hello api_version not found in test/bridge.test.ts"
    assert auto_hello.group(1) == major
    synthetic = (ROOT / "packages" / "opencode-vibe-oidc" / "test" / "synthetic-bridge.py").read_text(encoding="utf-8")
    py_hello = re.search(r'api_version="(\d+)\.', synthetic)
    assert py_hello, "hello api_version not found in test/synthetic-bridge.py"
    assert py_hello.group(1) == major


def test_lockfile_and_provider_package_match_project_version():
    version = _project_version()
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    entry = next((p for p in lock["package"] if p["name"] == "mvibe-auth"), None)
    assert entry, "mvibe-auth entry missing from uv.lock"
    assert entry["version"] == version
    package = json.loads((ROOT / "packages" / "opencode-vibe-oidc" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == version
