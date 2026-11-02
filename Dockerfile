# Local Vibe proxy image: OpenAI-compatible /v1 endpoint backed by Vibe credentials.
# Build:  docker build -t mvibe-proxy:local .
# Run:    docker compose up   (see docker-compose.yml; publishes 127.0.0.1 only)
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

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
