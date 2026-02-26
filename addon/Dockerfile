ARG BUILD_FROM
# The Playwright/python base image is specified per-arch in build.yaml.
# BUILD_FROM is substituted by the HA builder / local build script.
FROM ${BUILD_FROM}

# Keep Python output unbuffered so logs appear immediately in the HA log panel.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# ── Install dependencies ───────────────────────────────────────────────────────
RUN pip install --no-cache-dir poetry==1.8.3

COPY pyproject.toml README.md ./

RUN poetry config virtualenvs.create false && \
    poetry install --no-root --only main --no-interaction --no-ansi

# ── Copy application source ───────────────────────────────────────────────────
COPY browser_relay/ ./browser_relay/

RUN poetry install --only main --no-interaction --no-ansi

# ── Add-on startup script ─────────────────────────────────────────────────────
COPY addon/run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]

# ── HA-required image labels ──────────────────────────────────────────────────
ARG BUILD_ARCH
ARG BUILD_VERSION
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="addon" \
    io.hass.arch="${BUILD_ARCH}"
