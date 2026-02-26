"""
browser_relay/config.py
─────────────────────────────────────────────────────────────────────────────
Centralised settings loaded from environment variables (or a .env file).

All other modules import `settings` from here rather than reading
`os.environ` directly – this makes configuration easy to test and override.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Admin authentication ──────────────────────────────────────────────
    admin_secret: str = "change-me"
    """Long random string required in X-Admin-Secret header for admin routes."""

    # ── Token persistence ─────────────────────────────────────────────────
    token_db_path: Path = Path("data/tokens.json")
    """Filesystem path to the TinyDB JSON store for API tokens."""

    # ── Session control ───────────────────────────────────────────────────
    session_timeout_seconds: int = 3600
    """Seconds of inactivity before a browser session is automatically closed."""

    screenshot_interval_seconds: float = 0.1
    """How often (seconds) the streaming loop captures a new screenshot (≈10 fps)."""

    screenshot_quality: int = 75
    """JPEG quality 1-100 for streamed screenshots.  Lower = smaller payload."""

    # ── Server ────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # ── Playwright ────────────────────────────────────────────────────────
    playwright_headed: bool = False
    """Set to True to launch the browser in headed (visible window) mode."""


# Singleton instance imported by the rest of the application.
settings = Settings()
