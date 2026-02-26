"""
browser_relay/sessions/models.py
─────────────────────────────────────────────────────────────────────────────
Pydantic models for browser session data.
"""

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SessionCreateRequest(BaseModel):
    """Body schema for POST /api/sessions."""

    url: str = Field(..., description="Initial URL to load in the browser.")
    width: int = Field(1280, ge=320, le=3840, description="Viewport width in pixels.")
    height: int = Field(800, ge=200, le=2160, description="Viewport height in pixels.")

    @field_validator("url", mode="before")
    @classmethod
    def ensure_scheme(cls, v: str) -> str:
        """Prepend http:// to bare domain/path URLs that have no scheme."""
        v = str(v).strip()
        if not re.match(r"^https?://", v, re.IGNORECASE):
            v = "http://" + v
        return v


class SessionResponse(BaseModel):
    """Response returned when a session is created or queried."""

    session_id: str
    token_id: Optional[str] = None
    url: str
    current_url: Optional[str] = Field(
        None,
        description="Live URL the browser is currently on (may differ from the initial url after navigation).",
    )
    width: int
    height: int
    created_at: datetime
    ws_url: str = Field(..., description="WebSocket URL for interactive streaming.")
