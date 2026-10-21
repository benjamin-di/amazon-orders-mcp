# syntax=docker/dockerfile:1.7
# The fleet image of amazon-orders-mcp (benjamin-di fork, branch fleet): the MCP server and the sign-in keeper
# (fleet/keeper.py) from one image. nas-hk runs it twice: the server (CMD) and the keeper
# (`python -m fleet.keeper run`). Built by .github/workflows/fleet-release.yml; see fleet/README.md.
#
# Chromium is here for amazon-orders' [browser] extra, which clears Amazon's JavaScript challenges in a headless
# browser. The 1Password CLI is here for the keeper only: the server is started without a service-account token.
FROM ghcr.io/astral-sh/uv:0.9.26@sha256:9a23023be68b2ed09750ae636228e903a54a05ea56ed03a934d00fe9fbeded4b AS uv

FROM python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/state \
    PATH=/app/.venv/bin:$PATH

COPY --from=uv /uv /usr/local/bin/

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl dumb-init \
 && rm -rf /var/lib/apt/lists/*

ARG OP_CLI_VERSION=2.35.0
ARG OP_CLI_SHA256=4457ade59850b852c64c77164235b34dd0b984ef7826eb0ccd32f1fd78a2ceb7
RUN curl --fail --silent --show-error --location \
      "https://cache.agilebits.com/dist/1P/op2/pkg/v${OP_CLI_VERSION}/op_linux_amd64_v${OP_CLI_VERSION}.zip" \
      -o /tmp/op.zip \
 && printf '%s  /tmp/op.zip\n' "${OP_CLI_SHA256}" | sha256sum --check --status \
 && apt-get update \
 && apt-get install -y --no-install-recommends unzip \
 && unzip -j /tmp/op.zip op -d /usr/local/bin \
 && chmod 0755 /usr/local/bin/op \
 && apt-get purge -y unzip \
 && apt-get autoremove -y \
 && rm -rf /tmp/op.zip /var/lib/apt/lists/*

WORKDIR /app
# Locked dependencies first, so a code-only release reuses the Chromium layer.
COPY fleet/requirements.lock /app/fleet/requirements.lock
RUN uv venv /app/.venv \
 && uv pip install --python /app/.venv/bin/python --no-deps --no-cache -r /app/fleet/requirements.lock \
 && /app/.venv/bin/python -m playwright install --with-deps --no-shell chromium \
 && rm -rf /var/lib/apt/lists/* \
 && chmod -R a+rX /ms-playwright \
 && ln -s /ms-playwright/chromium-*/chrome-linux*/chrome /usr/local/bin/chromium \
 && chromium --version

# Chromium runs headless itself, so Playwright's separate headless shell is left out and amazon-orders launches
# Chromium by its path (as upstream's Dockerfile does).
ENV AMAZON_BROWSER_EXECUTABLE_PATH=/usr/local/bin/chromium

COPY pyproject.toml README.md LICENSE /app/
COPY amazonorders_mcp /app/amazonorders_mcp
COPY fleet /app/fleet
RUN uv pip install --python /app/.venv/bin/python --no-deps --no-cache /app \
 && python -c "import amazonorders_mcp.server, fleet.keeper, fleet.server" \
 && install -d -o 1000 -g 1000 -m 0700 /state

ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.source="https://github.com/benjamin-di/amazon-orders-mcp" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.description="amazon-orders-mcp (fleet build) and its sign-in keeper"

USER 1000:1000
ENTRYPOINT ["dumb-init", "--"]
# The server: upstream's CLI plus keeper_status (fleet/server.py). JSON replies, not event streams, through the
# gateway; the bearer comes from AMAZON_ORDERS_MCP_TOKEN. The keeper runs `python -m fleet.keeper run`.
CMD ["python", "-m", "fleet.server", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000", \
     "--json-response"]
