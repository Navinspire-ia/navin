FROM node:24-bookworm-slim AS webui-builder

WORKDIR /app
COPY webui/package.json webui/package-lock.json ./webui/
WORKDIR /app/webui
RUN npm ci
COPY webui/ ./
RUN mkdir -p /app/navin/web && npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates git bubblewrap openssh-client libmagic1 && \
    rm -rf /var/lib/apt/lists/*

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
