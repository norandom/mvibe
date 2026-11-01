#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
bun=''
bun_path=''
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  case "${args[i]}" in
    --help|-h)
      printf '%s\n' 'Install direct Mistral-Vibe: ./install.sh [--target cli|desktop|both] [--interactive | --yes] [--auth-source existing-vibe|oidc] [--model vibe-oidc/<model-id>] [--connect] [--restart] [--config-path FILE] [--uv EXECUTABLE] [--bun EXECUTABLE] [--opencode EXECUTABLE] [--skip-prepare]' 'Requires native uv, Bun 1.4.4 and OpenCode 2. Preparation may download locked dependencies. Terminal-only prompts default on; --yes disables them. CLI/Desktop share global config. Restart/connect require opt-in; OIDC connection may open a browser.'
      exit 0 ;;
    --bun)
      ((i+=1))
      if ((i>=${#args[@]})); then printf '%s\n' 'Missing --bun executable.' >&2; exit 1; fi
      bun="${args[i]}" ;;
  esac
done
bun="${bun:-bun}"
bun_path="$(command -v -- "$bun" || true)"
if [[ -z "$bun_path" || "$bun_path" == *WindowsApps* ]]; then
  printf '%s\n' 'Native Bun 1.4.4 is required; install it explicitly or supply --bun.' >&2
  exit 1
fi
installer="$root/packages/opencode-vibe-oidc/scripts/install.ts"
if [[ -f "$root/packages/opencode-vibe-oidc/scripts/install.js" ]]; then
  installer="$root/packages/opencode-vibe-oidc/scripts/install.js"
fi
exec "$bun_path" "$installer" "${args[@]}"
