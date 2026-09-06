FROM node:24-bookworm-slim AS webui-builder

WORKDIR /app
COPY webui/package.json webui/package-lock.json ./webui/
WORKDIR /app/webui
RUN npm ci
COPY webui/ ./
RUN mkdir -p /app/navin/web && npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

LABEL org.opencontainers.image.title="Navin" \
      org.opencontainers.image.source="https://github.com/navinspire-ai/navin-agi" \
      org.opencontainers.image.url="https://navin.live" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.description="Navin gateway and bundled WebUI. Public source: navinspire-ai/navin-agi."

# ffmpeg comes from apt here rather than from packaging/vendor: this image is
# installed with pip, so it has no PyInstaller bundle to carry the static build
# the desktop packages ship. Without it, video attachments, montage and social
# exports are dead in a container, and the runtime installer cannot help because
# a container filesystem is usually read-only and always thrown away.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates git bubblewrap openssh-client libmagic1 curl ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Node.js runtime (node/npm/npx) copied from the build stage: same architecture,
# no external apt repository, and lets agents run npx/eslint/tsc/npm audit.
COPY --from=webui-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=webui-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Pre-baked audit toolbelt: Python scanners in isolated uv tool venvs plus
# static Go binaries, so /probe, /unmask, /lineage and friends get real
# scanners on first run instead of bootstrapping them.
ARG TARGETARCH=amd64
ARG GITLEAKS_VERSION=8.30.1
ARG OSV_SCANNER_VERSION=2.4.0
# Tool dirs are scoped to this RUN only: at runtime the non-root user must keep
# installing on-demand tools into its own ~/.local via the toolbelt skill.
RUN export UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin && \
    uv tool install --no-cache ruff && \
    uv tool install --no-cache bandit && \
    uv tool install --no-cache pip-audit && \
    uv tool install --no-cache detect-secrets && \
    case "$TARGETARCH" in amd64) GITLEAKS_ARCH=x64 ;; *) GITLEAKS_ARCH="$TARGETARCH" ;; esac && \
    curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${GITLEAKS_ARCH}.tar.gz" \
      | tar -xz -C /usr/local/bin gitleaks && \
    curl -fsSL -o /usr/local/bin/osv-scanner \
      "https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_linux_${TARGETARCH}" && \
    chmod +x /usr/local/bin/osv-scanner

WORKDIR /app

# Install Python dependencies first (cached layer). Hatch reads the custom build
# hook from hatch_build.py even for this metadata-only install.
ARG NAVIN_EXTRAS=whatsapp
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md hatch_build.py ./
RUN mkdir -p navin && touch navin/__init__.py && \
    NAVIN_SKIP_WEBUI_BUILD=1 uv pip install --system --no-cache ".[$NAVIN_EXTRAS]" && \
    rm -rf navin

# Copy the full source and install
COPY navin/ navin/
COPY --from=webui-builder /app/navin/web/dist/ navin/web/dist/
RUN NAVIN_SKIP_WEBUI_BUILD=1 uv pip install --system --no-cache ".[$NAVIN_EXTRAS]"

# Create non-root user and config directory
RUN useradd -m -u 1000 -s /bin/bash navin && \
    mkdir -p /home/navin/.navin && \
    chown -R navin:navin /home/navin /app

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

USER navin
ENV HOME=/home/navin

# Gateway health endpoint and optional WebUI/WebSocket channel ports
EXPOSE 18790 8765

ENTRYPOINT ["entrypoint.sh"]
CMD ["status"]
