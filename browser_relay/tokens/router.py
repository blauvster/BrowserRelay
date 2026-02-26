"""
browser_relay/tokens/router.py
─────────────────────────────────────────────────────────────────────────────
FastAPI router for API token management.

All routes are protected by the ``require_admin`` dependency.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from browser_relay.auth.dependencies import require_admin
from browser_relay.auth.tickets import ticket_store
from browser_relay.tokens.models import (
    TokenCreateRequest,
    TokenCreatedResponse,
    TokenMetaResponse,
)
from browser_relay.tokens.store import AbstractTokenStore, get_token_store

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/tokens  – create a new token
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=TokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new client API token",
    dependencies=[Depends(require_admin)],
)
async def create_token(
    body: TokenCreateRequest,
    store: AbstractTokenStore = Depends(get_token_store),
) -> TokenCreatedResponse:
    """
    Create a new API token for a client.

    The ``token`` field in the response is the **only** time the raw value
    is exposed.  Store it securely – it cannot be retrieved again.
    """
    record, raw_token = await store.create(label=body.label)
    return TokenCreatedResponse(
        token_id=record.token_id,
        token=raw_token,
        label=record.label,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        revoked=record.revoked,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/tokens  – list all tokens
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=List[TokenMetaResponse],
    summary="List all client API tokens",
    dependencies=[Depends(require_admin)],
)
async def list_tokens(
    store: AbstractTokenStore = Depends(get_token_store),
) -> List[TokenMetaResponse]:
    """Return metadata for all tokens (raw values and hashes are never included)."""
    records = await store.list_all()
    return [
        TokenMetaResponse(
            token_id=r.token_id,
            token_prefix=r.token_prefix,
            label=r.label,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            revoked=r.revoked,
        )
        for r in records
    ]


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/tokens/{token_id}  – revoke a token
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/{token_id}",
    summary="Revoke (permanently disable) a client API token",
    dependencies=[Depends(require_admin)],
)
async def revoke_token(
    token_id: str,
    store: AbstractTokenStore = Depends(get_token_store),
) -> dict:
    """
    Revoke a token immediately.  All subsequent API/WebSocket requests using
    that token will be rejected with 401.
    """
    found = await store.revoke(token_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Token '{token_id}' not found.",
        )
    return {"detail": f"Token {token_id} revoked."}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/tokens/{token_id}/delete  – permanently remove a revoked token
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/{token_id}/delete",
    summary="Permanently delete a revoked token record (admin)",
    dependencies=[Depends(require_admin)],
)
async def delete_token(
    token_id: str,
    store: AbstractTokenStore = Depends(get_token_store),
) -> dict:
    """
    Permanently remove a token record from the store.

    Only revoked tokens may be deleted; attempting to delete an active token
    returns 409 so the caller must revoke it first.
    """
    record = await store.get(token_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Token '{token_id}' not found.",
        )
    if not record.revoked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Token '{token_id}' is still active. Revoke it before deleting.",
        )
    await store.delete(token_id)
    return {"detail": f"Token {token_id} permanently deleted."}


@router.post(
    "/{token_id}/ticket",
    summary="Issue a short-lived manager ticket for a client token (admin)",
    dependencies=[Depends(require_admin)],
)
async def issue_ticket(
    token_id: str,
    store: AbstractTokenStore = Depends(get_token_store),
) -> dict:
    """
    Mint a short-lived ``brt_…`` ticket that authenticates as *token_id*.

    The ticket is stored only in process memory and expires in 5 minutes.
    It is intended for the manager UI to open the client viewer without
    requiring knowledge of the permanent raw token value.
    """
    record = await store.get(token_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Token '{token_id}' not found.",
        )
    if record.revoked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Token '{token_id}' is revoked; cannot issue ticket.",
        )
    raw_ticket = ticket_store.issue(token_id)
    return {"ticket": raw_ticket, "expires_in": 300}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/tokens/{token_id}/rotate  – rotate (regenerate) a token
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/{token_id}/rotate",
    response_model=TokenCreatedResponse,
    summary="Rotate (regenerate) a client API token",
    dependencies=[Depends(require_admin)],
)
async def rotate_token(
    token_id: str,
    store: AbstractTokenStore = Depends(get_token_store),
) -> TokenCreatedResponse:
    """
    Invalidate the existing token value and issue a new one for the same
    ``token_id``.  The new raw token is returned **once** and is not stored.
    """
    result = await store.rotate(token_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Token '{token_id}' not found.",
        )
    record, raw_token = result
    return TokenCreatedResponse(
        token_id=record.token_id,
        token=raw_token,
        label=record.label,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        revoked=record.revoked,
    )
