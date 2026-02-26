"""
browser_relay/tokens/models.py
─────────────────────────────────────────────────────────────────────────────
Pydantic models for API token data.

Separation of *stored* vs *API response* models ensures that token hashes
are never accidentally serialised into HTTP responses.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TokenRecord(BaseModel):
    """
    Full token record as persisted in the database.

    ``token_hash`` is the SHA-256 digest of the raw token.  The raw value is
    handed to the client once on creation and is never stored in plain text.
    """

    token_id: str = Field(..., description="Stable, opaque identifier (e.g. tok_xxxx).")
    token_hash: str = Field(..., description="SHA-256 hex digest of the raw token.")
    token_prefix: str = Field(..., description="First 8 chars of raw token for display.")
    label: str = Field(..., description="Human-readable name for this token.")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None
    revoked: bool = False


class TokenCreateRequest(BaseModel):
    """Body schema for POST /api/tokens."""

    label: str = Field(..., min_length=1, max_length=128, description="Descriptive name.")


class TokenCreatedResponse(BaseModel):
    """
    Response returned **only** on token creation (and rotation).

    The ``token`` field is the raw secret – it MUST be stored by the client
    because it cannot be recovered later.
    """

    token_id: str
    token: str = Field(..., description="Raw token value – show once, store securely.")
    label: str
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked: bool


class TokenMetaResponse(BaseModel):
    """
    Safe metadata response used for list / status queries.
    Does NOT include the raw token value or its hash.
    """

    token_id: str
    token_prefix: str
    label: str
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked: bool
