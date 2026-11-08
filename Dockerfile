# Local Vibe proxy image: OpenAI-compatible /v1 endpoint backed by Vibe credentials.
# Build:  docker build -t mvibe-proxy:local .
# Run:    docker compose up   (see docker-compose.yml; publishes 127.0.0.1 only)
#
# mmhmem embed (proxy-econ-client Amendment 1): the token-economics child is
# the CI-built binary from mmhmem's published release for the pinned
# MMHMEM_VERSION tag - the same signed artifact set the installers ship -
# downloaded and checksum-verified against the release SHA256SUMS instead of
# compiled from a cloned git ref. The binary stays inert unless
# MVIBE_ECON_MMHMEM_BIN selects it (off by default), and the stdio child
# opens no listener, so no port is published.
FROM python:3.12-slim

# mmhmem release whose CI-built linux binary is embedded (no leading v).
# Must always reference an already-published release on GitHub.
ARG MMHMEM_VERSION=0.5.2

RUN pip install --no-cache-dir uv

# Pull the mmhmem release archive and its checksum list, verify, and install
# the statically linked musl binary. SHA256SUMS itself is signed by mmhmem's
# publish pipeline; over HTTPS this pins the exact archive bytes.
RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
        && rm -rf /var/lib/apt/lists/*; \
    base="https://github.com/norandom/mmhmem/releases/download/v${MMHMEM_VERSION}"; \
    api="https://api.github.com/repos/norandom/mmhmem/releases/tags/v${MMHMEM_VERSION}"; \
    arch="mmhmem-v${MMHMEM_VERSION}-x86_64-unknown-linux-musl.tar.gz"; \
    fetch() { \
        curl -fsSL --connect-timeout 10 --max-time 30 -o "/tmp/$1" "${base}/$1" || { \
            test -s /tmp/release.json || curl -fsSL -o /tmp/release.json "${api}"; \
            asset_url="$(python3 -c 'import json,sys; r=json.load(open("/tmp/release.json")); print(next(a["url"] for a in r["assets"] if a["name"]==sys.argv[1]))' "$1")"; \
            curl -fsSL -H "Accept: application/octet-stream" -o "/tmp/$1" "${asset_url}"; \
        }; \
    }; \
    fetch "${arch}"; \
    fetch SHA256SUMS; \
    grep " ${arch}\$" /tmp/SHA256SUMS > /tmp/sums.check; \
    cd /tmp && sha256sum -c sums.check; \
    tar -xzf "/tmp/${arch}" -C /tmp; \
    install -m 0755 /tmp/mmhmem /usr/local/bin/mmhmem; \
    /usr/local/bin/mmhmem --version; \
    rm -rf "/tmp/${arch}" /tmp/SHA256SUMS /tmp/sums.check /tmp/release.json /tmp/mmhmem

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY LICENSE README.md THIRD_PARTY_NOTICES ./
COPY mvibe_auth ./mvibe_auth
COPY mvibe_proxy ./mvibe_proxy
COPY proxy.py ./

# Locked project environment, including the proxy and docker (file keyring) extras.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    XDG_DATA_HOME=/data
RUN uv sync --locked --extra proxy --extra docker --no-dev --python /usr/local/bin/python

RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4)" || exit 1

# 0.0.0.0 inside the container is required for the published port; compose maps
# it to 127.0.0.1 on the host. Credentials arrive read-only under /data
# (XDG_DATA_HOME): /data/mvibe-auth/credentials.v1.enc plus the keyring file
# /data/python_keyring/keyring_pass.cfg via PYTHON_KEYRING_BACKEND (compose).
ENTRYPOINT [".venv/bin/python", "proxy.py"]
CMD ["--host", "0.0.0.0", "--port", "8765"]
