#!/usr/bin/with-contenv bashio
# ──────────────────────────────────────────────────────────────────────────────
# BrowserRelay – Home Assistant add-on startup script
#
# bashio reads /data/options.json (written by the Supervisor from config.yaml
# schema) and exposes each key via `bashio::config '<key>'`.
# ──────────────────────────────────────────────────────────────────────────────

bashio::log.info "Starting BrowserRelay…"

# ── Validate required config ──────────────────────────────────────────────────
if bashio::config.is_empty 'admin_secret'; then
    bashio::log.fatal "admin_secret must be set in the add-on configuration before starting."
    exit 1
fi

# ── Export settings as environment variables ──────────────────────────────────
export ADMIN_SECRET="$(bashio::config 'admin_secret')"
export SESSION_TIMEOUT_SECONDS="$(bashio::config 'session_timeout_seconds')"
export SCREENSHOT_INTERVAL_SECONDS="$(bashio::config 'screenshot_interval_seconds')"
export SCREENSHOT_QUALITY="$(bashio::config 'screenshot_quality')"
export LOG_LEVEL="$(bashio::config 'log_level')"

# Always headless inside the container – no display server is available.
export PLAYWRIGHT_HEADED=false

# Store the TinyDB token file in /data so it survives add-on updates.
export TOKEN_DB_PATH=/data/tokens.json

export HOST="0.0.0.0"
export PORT="8000"

bashio::log.info "admin_secret  : [set]"
bashio::log.info "session timeout : ${SESSION_TIMEOUT_SECONDS}s"
bashio::log.info "screenshot quality: ${SCREENSHOT_QUALITY}"
bashio::log.info "log level     : ${LOG_LEVEL}"

exec serve
