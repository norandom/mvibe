"""Export only the normal Vibe credential for a read-only container mount."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

from keyrings.alt.file import PlaintextKeyring
from mvibe_auth.existing_vibe import _read_native_credential


def export(directory: Path) -> None:
    credential = _read_native_credential()
    if not credential:
        raise RuntimeError("No native Vibe login found; sign in through Vibe first.")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix=".keyring-", dir=directory)
    os.close(fd)
    try:
        backend = PlaintextKeyring()
        backend.file_path = temporary
        backend.set_password("ai.mistral.vibe", "MISTRAL_API_KEY", credential)
        Path(temporary).chmod(0o600)
        os.replace(temporary, directory / "keyring_pass.cfg")
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Private container keyring directory")
    args = parser.parse_args()
    export(args.directory.expanduser().resolve())
    print("Exported existing Vibe login. Repeat after signing in again; never commit this directory.")
