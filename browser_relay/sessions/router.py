"""
browser_relay/sessions/router.py
─────────────────────────────────────────────────────────────────────────────
FastAPI router for browser session management.

Client routes  – require a valid API token in X-API-Token.
Admin routes   – require the admin secret in X-Admin-Secret.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from browser_relay.auth.dependencies import require_admin, require_token
from browser_relay.sessions.manager import SessionEntry, session_manager
from browser_relay.sessions.models import SessionCreateRequest, SessionResponse
from browser_relay.tokens.models import TokenRecord

logger = logging.getLogger(__name__)

router = APIRouter()


def _session_response(entry: SessionEntry) -> SessionResponse:
    """Build a ``SessionResponse`` from a ``SessionEntry``."""
    return SessionResponse(
        session_id=entry.session_id,
        token_id=entry.token_id,
        url=entry.url,
        current_url=entry.controller.current_url or entry.url,
        width=entry.width,
        height=entry.height,
        created_at=entry.created_at,
        ws_url=f"/ws/session/{entry.session_id}",
    )


def _get_or_404(session_id: str) -> SessionEntry:
    entry = session_manager.get_session(session_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found or has expired.",
        )
    return entry


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/sessions  – create a new session
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new browser session",
)
async def create_session(
    body: SessionCreateRequest,
    token_record: TokenRecord = Depends(require_token),
) -> SessionResponse:
    """
    Spin up a headless Chromium instance, navigate to *url*, and return the
    session details including the WebSocket URL for interactive streaming.
    """
    try:
        entry = await session_manager.create_session(
            url=body.url,
            width=body.width,
            height=body.height,
            token_id=token_record.token_id,
        )
    except Exception as exc:
        logger.exception("Failed to create session: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to launch browser: {exc}",
        )
    return _session_response(entry)


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/sessions/{session_id}  – close a session
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/{session_id}",
    summary="Close and destroy a browser session",
    dependencies=[Depends(require_token)],
)
async def close_session(session_id: str) -> dict:
    """Gracefully close the browser for *session_id* and release resources."""
    found = await session_manager.close_session(session_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return {"detail": f"Session {session_id} closed."}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/sessions/{session_id}/screenshot  – retrieve a snapshot
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{session_id}/screenshot",
    summary="Capture a one-off JPEG screenshot of the session",
    response_class=Response,
    dependencies=[Depends(require_token)],
)
async def get_screenshot(session_id: str) -> Response:
    """Return the current viewport as a JPEG image."""
    entry = _get_or_404(session_id)
    try:
        img_bytes = await entry.controller.screenshot_bytes()
    except Exception as exc:
        logger.exception("Screenshot failed for session %s: %s", session_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Screenshot error: {exc}",
        )
    return Response(content=img_bytes, media_type="image/jpeg")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/sessions  – list all active sessions (admin)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=List[SessionResponse],
    summary="List all active browser sessions (admin)",
    dependencies=[Depends(require_admin)],
)
async def list_sessions() -> List[SessionResponse]:
    """Return metadata for every currently active browser session."""
    return [_session_response(e) for e in session_manager.list_all()]


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/sessions/{session_id}/force  – admin force-close any session
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/{session_id}/force",
    summary="Force-close any browser session (admin)",
    dependencies=[Depends(require_admin)],
)
async def force_close_session(session_id: str) -> dict:
    """Admin-only endpoint to close any session regardless of which client owns it."""
    found = await session_manager.close_session(session_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return {"detail": f"Session {session_id} force-closed."}
