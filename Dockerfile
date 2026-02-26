# ──────────────────────────────────────────────────────────────────────────────
# BrowserRelay – Docker image
#
# Uses the official Playwright Python base image so that Chromium and all of
# its system-level dependencies are already present.  No separate
# `playwright install --with-deps` step is required.
# ──────────────────────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Keep Python output unbuffered so logs appear immediately in `docker logs`.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Tell Playwright to use the pre-installed browsers in the base image.
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# ── Install dependencies (separate layer → cached unless pyproject.toml changes)
# Install Poetry, then use it to export a plain requirements file so the final
# image does not need Poetry at runtime.
RUN pip install --no-cache-dir poetry==1.8.3

COPY pyproject.toml README.md ./

# Install only production dependencies into the system Python (no venv needed
# inside a container).
RUN poetry config virtualenvs.create false && \
    poetry install --no-root --only main --no-interaction --no-ansi

# ── Copy application source
COPY browser_relay/ ./browser_relay/

# Install the package itself (registers the `serve` console script).
RUN poetry install --only main --no-interaction --no-ansi

# ── Runtime configuration
# Data directory for TinyDB – mount a volume here to persist tokens across
# container restarts.
VOLUME ["/app/data"]

EXPOSE 8000

# The `serve` script is defined in pyproject.toml → browser_relay.main:run
CMD ["serve"]
