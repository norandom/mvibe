#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
uv_bin="${UV:-uv}"
host='127.0.0.1'
rest=()
while (($#)); do
  case "$1" in
    --help|-h)
      printf '%s\n' 'Start the local Vibe proxy: ./start_proxy.sh [--host 127.0.0.1|0.0.0.0] [--uv EXECUTABLE] [proxy.py arguments]' 'Binds to loopback by default; --host 0.0.0.0 exposes all interfaces and prints a warning, because the proxy has no caller authentication.' 'Further arguments are forwarded to proxy.py: --port 8765, --auth-source existing-vibe|oidc, --login, --log.' 'Requires native uv. Runs the locked project environment with the proxy extra: uv run --locked --extra proxy -- python proxy.py.'
      exit 0 ;;
    --host)
      shift
      if ((! $#)); then printf '%s\n' 'Missing value for --host.' >&2; exit 1; fi
      host="$1" ;;
    --host=*)
      host="${1#--host=}" ;;
    --uv)
      shift
      if ((! $#)); then printf '%s\n' 'Missing value for --uv.' >&2; exit 1; fi
      uv_bin="$1" ;;
    --uv=*)
      uv_bin="${1#--uv=}" ;;
    *)
      rest+=("$1") ;;
  esac
  shift
done
if ! command -v -- "$uv_bin" >/dev/null 2>&1; then
  if command -v -- "${uv_bin}.exe" >/dev/null 2>&1; then
    uv_bin="${uv_bin}.exe"
  else
    printf '%s\n' "uv executable not found: $uv_bin (use --uv EXECUTABLE or the UV environment variable)." >&2
    exit 1
  fi
fi
exec "$uv_bin" run --locked --extra proxy -- python "$root/proxy.py" --host "$host" ${rest[@]+"${rest[@]}"}
