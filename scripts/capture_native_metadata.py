"""Refresh the offline golden from an explicitly supplied Vibe checkout.

Run with the project's uv environment; does not import auth or send requests.
Default tests consume the committed fixture and never need that checkout.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


def main() -> None:
    source = Path(sys.argv[1]).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    from vibe.core.telemetry import build_metadata as native
    from vibe.core.telemetry.types import LaunchContext

    paths = ['vibe/core/telemetry/build_metadata.py', 'vibe/core/telemetry/types.py',
             'vibe/app_server/_completion_attribution.py', 'vibe/utils/http.py', 'vibe/utils/platform.py']
    launch = LaunchContext(agent_entrypoint='cli', agent_version='3.0.4',
                           client_name='vibe_cli', client_version='3.0.4')
    cases = []
    with patch.object(native, 'get_platform_id', return_value='windows'), \
         patch.object(native, 'get_platform_version', return_value='synthetic-os-version'), \
         patch.object(native.platform, 'machine', return_value='AMD64'):
        for call_type in ['main_call', 'secondary_call', 'smart_approve', 'title_generation']:
            value = native.build_request_metadata(launch_context=launch, session_id='synthetic-session',
                call_type=call_type, message_id='synthetic-message')
            # Exact native completion-attribution serialization (exclude None,
            # then stringify); no copied or reimplemented metadata builder.
            cases.append({ 'call_type': call_type, 'metadata': {
                key: str(value) for key, value in value.model_dump(exclude_none=True).items()
            } })
    fixture = {'source_commit': subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip(),
               'source_sha256': {path: hashlib.sha256((source / path).read_bytes()).hexdigest() for path in paths},
               'cases': cases}
    target = Path(__file__).resolve().parents[1] / 'tests/fixtures/native-request-metadata.json'
    target.write_text(json.dumps(fixture, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
