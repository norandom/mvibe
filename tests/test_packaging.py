"""Packaging checks only; no live account activity is permitted."""
from importlib.metadata import metadata
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PackagingTests(unittest.TestCase):
    def test_package_metadata(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["project"]["requires-python"], ">=3.12")
        self.assertIn("proxy", config["project"]["optional-dependencies"])
        deps = config["project"]["dependencies"]
        self.assertFalse(any(d.startswith(("fastapi", "uvicorn")) for d in deps))
        self.assertTrue(any(d.startswith("filelock") for d in deps))
        self.assertNotEqual(metadata("mvibe-auth")["Version"], "3.0.4")

    def test_installed_imports_are_inert(self):
        # -I excludes the checkout and user site; cwd cannot provide the package.
        code = r"""
import asyncio, importlib, socket, webbrowser, keyring, sys, anyio
from unittest.mock import patch

# Windows asyncio creates a local socketpair; prepare it before denying all connects.
loop = asyncio.new_event_loop()

def deny(*args, **kwargs):
    raise AssertionError('external effect during import')

with patch.object(socket.socket, 'connect', deny), \
     patch.object(socket.socket, 'connect_ex', deny), \
     patch.object(socket, 'getaddrinfo', deny), \
     patch.object(webbrowser, 'open', deny), \
     patch.object(webbrowser, 'open_new', deny), \
     patch.object(webbrowser, 'open_new_tab', deny), \
     patch.object(keyring, 'get_keyring', deny), \
     patch.object(keyring, 'get_password', deny), \
     patch.object(keyring, 'set_password', deny), \
     patch.object(keyring, 'delete_password', deny):
    for name in ('mvibe_auth', 'mvibe_auth._oidc_client',
                 'mvibe_auth._oidc_service', 'mvibe_auth._oidc_runtime'):
        importlib.import_module(name)
    assert 'proxy' not in sys.modules
    assert 'fastapi' not in sys.modules
    assert 'uvicorn' not in sys.modules
    import anyio
    async def synthetic():
        await anyio.sleep(0)
        return 'synthetic-ok'
    assert anyio.run(synthetic, backend='asyncio', backend_options={'loop_factory': lambda: loop}) == 'synthetic-ok'
"""
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, "-I", "-c", code], cwd=cwd,
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == "__main__":
    unittest.main()

