"""
browser_relay/auth/dependencies.py
─────────────────────────────────────────────────────────────────────────────
FastAPI dependency functions for authentication.

Two guards are provided:

* ``require_admin``  — validates X-Admin-Secret header for admin endpoints.
* ``require_token``  — validates X-API-Token header for client endpoints.
* ``ws_require_token`` — validates a token query parameter for WebSocket
                          connections (headers are not reliably settable from
                          browser WebSocket APIs).
"""

import hashlib
import logging

from fastapi import Depends, Header, HTTPException, Query, status

from browser_relay.auth.tickets import TICKET_PREFIX, ticket_store
from browser_relay.config import settings
from browser_relay.tokens.models import TokenRecord
from browser_relay.tokens.store import AbstractTokenStore, get_token_store

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _hash_token(raw: str) -> str:
    """SHA-256 hash of a raw token value (hex digest)."""
    return hashlib.sha256(raw.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Admin guard
# ─────────────────────────────────────────────────────────────────────────────


async def require_admin(
    x_admin_secret: str = Header(
        ...,
        alias="X-Admin-Secret",
        description="Static admin secret from server configuration.",
    ),
) -> None:
    """
    FastAPI dependency that raises 403 unless the request carries the correct
    admin secret in the ``X-Admin-Secret`` header.

    Usage::

        @router.get("/admin-only")
        async def admin_endpoint(_: None = Depends(require_admin)):
            ...
    """
    if x_admin_secret != settings.admin_secret:
        logger.warning("Admin auth failed – wrong secret supplied.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin secret.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Client API-token guard (HTTP)
# ─────────────────────────────────────────────────────────────────────────────


async def _resolve_token(raw: str, store: AbstractTokenStore) -> TokenRecord:
    """
    Resolve a raw token string to a ``TokenRecord``.

    Accepts two token forms:
    * ``brt_…`` — short-lived manager-issued ticket (resolved via ticket store)
    * ``br_…``  — permanent client API token (hashed and looked up in the store)

    Raises a suitable ``HTTPException`` for unknown, expired, or revoked tokens.
    """
    if raw.startswith(TICKET_PREFIX):
        # Ticket path: peek so the same ticket survives multiple API calls
        # during a single client session (create session + WS connect).
        token_id = ticket_store.peek(raw)
        if token_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Ticket is invalid or has expired.",
                headers={"WWW-Authenticate": "X-API-Token"},
            )
        record = await store.get(token_id)
    else:
        token_hash = _hash_token(raw)
        record = await store.get_by_hash(token_hash)

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unknown API token.",
            headers={"WWW-Authenticate": "X-API-Token"},
        )
    if record.revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token '{record.token_id}' has been revoked.",
            headers={"WWW-Authenticate": "X-API-Token"},
        )
    await store.touch(record.token_id)
    return record


async def require_token(
    x_api_token: str = Header(
        ...,
        alias="X-API-Token",
        description="Client API token (br_…) or manager-issued ticket (brt_…).",
    ),
    store: AbstractTokenStore = Depends(get_token_store),
) -> TokenRecord:
    """
    FastAPI dependency that validates an API token supplied in the
    ``X-API-Token`` header.

    Accepts both permanent ``br_…`` tokens and short-lived ``brt_…`` tickets
    issued by the manager via ``POST /api/tokens/{token_id}/ticket``.

    Returns the ``TokenRecord`` so downstream handlers can access ``token_id``
    and other metadata without an extra store lookup.

    Raises 401 if the token is missing, unknown, expired, or revoked.
    """
    return await _resolve_token(x_api_token, store)


# ─────────────────────────────────────────────────────────────────────────────
# Client API-token guard (WebSocket – token in query param)
# ─────────────────────────────────────────────────────────────────────────────


async def ws_require_token(
    token: str = Query(..., description="Client API token (br_…) or manager-issued ticket (brt_…)."),
    store: AbstractTokenStore = Depends(get_token_store),
) -> str:
    """
    Same as ``require_token`` but reads the token from the ``?token=`` query
    parameter, which the browser WebSocket API can set.

    Accepts both permanent ``br_…`` tokens and short-lived ``brt_…`` tickets.
    """
    try:
        await _resolve_token(token, store)
    except HTTPException:
        # For WebSocket, we cannot return an HTTP 401 response body — instead
        # raise 403 so FastAPI closes the handshake cleanly.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or revoked API token.",
        )
    return token
