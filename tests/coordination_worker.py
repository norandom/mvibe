"""Explicit synthetic subprocess fixture; no selectable production test backend."""
import asyncio
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # installs the same keyring/browser/socket default-deny guards
from mvibe_auth._coordination import MutationCoordinator
from mvibe_auth._oidc_credentials import OidcCredentialStore, OidcTokens, OidcTenant

root, mode = Path(sys.argv[1]), sys.argv[2]


def wait(path):
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() > deadline:
            raise RuntimeError("synthetic barrier timed out")
        time.sleep(.01)


class Store(OidcCredentialStore):
    def _read(self):
        value = OidcTokens.model_validate_json((root / "slot.json").read_text())
        if mode == "tenant" and not (root / "tenant-read").exists():
            (root / "tenant-read").touch()
            wait(root / "release-tenant")
        return value

    def _save(self, value):
        (root / "slot.json").write_text(value.model_dump_json())


store = Store()
store.coordinator = MutationCoordinator(root / "coord.lock", timeout=5)
original = OidcTokens.model_validate_json((root / "original.json").read_text())
if mode == "tenant":
    assert store.save_tenant(original, OidcTenant(selection="", organization_id="org", workspace_id="workspace"))
else:
    (root / "refresh-started").touch()
    updated = OidcTokens.model_validate(original.model_dump() | {"access_token": "rotated-access", "refresh_token": "rotated-refresh", "id_token": "rotated-id"})
    assert asyncio.run(store.save_if_current(updated, expected=original))
    (root / "refresh-committed").touch()
