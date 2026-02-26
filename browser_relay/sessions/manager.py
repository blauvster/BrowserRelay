"""
browser_relay/sessions/manager.py
─────────────────────────────────────────────────────────────────────────────
In-memory session registry and lifecycle manager.

``SessionManager`` holds the shared Playwright instance and a mapping of
``session_id → BrowserController``.  It is instantiated once as a module-level
singleton (``session_manager``) that is imported wherever sessions are needed.

Extension notes
───────────────
* To support multiple server processes, replace the ``_sessions`` dict with a
  Redis-based registry and ``BrowserController`` with a remote CDP client.
* The background cleanup task sweeps idle sessions every minute.  Adjust
  ``settings.session_timeout_seconds`` to taste.
"""

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from playwright.async_api import Playwright, async_playwright

from browser_relay.browser.controller import BrowserController
from browser_relay.config import settings

logger = logging.getLogger(__name__)

_SESSION_ID_PREFIX = "sess_"


def _make_session_id() -> str:
    return _SESSION_ID_PREFIX + secrets.token_hex(8)


@dataclass
class SessionEntry:
    """Everything the server needs to know about an active session."""

    session_id: str
    url: str
    width: int
    height: int
    created_at: datetime
    controller: BrowserController
    token_id: Optional[str] = None
    last_active: datetime = field(default_factory=datetime.utcnow)
    # Display resolution: screenshots are scaled to these dims before sending.
    # Defaults to the full viewport; updated live via the set_display WS message.
    display_width: int = 0   # 0 means "same as width" – resolved post-init
    display_height: int = 0  # 0 means "same as height" – resolved post-init

    def __post_init__(self) -> None:
        if self.display_width == 0:
            self.display_width = self.width
        if self.display_height == 0:
            self.display_height = self.height


class SessionManager:
    """
    Owns the shared Playwright instance and all active browser sessions.

    Methods
    ───────
    startup()        Called once during FastAPI lifespan startup.
    shutdown()       Called once during FastAPI lifespan shutdown.
    create_session() Launch a new browser session.
    get_session()    Look up an existing session by ID.
    close_session()  Gracefully terminate a session.
    """

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        # session_id → SessionEntry
        self._sessions: dict[str, SessionEntry] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Start the Playwright engine and the background cleanup task."""
        self._playwright = await async_playwright().start()
        # Start the background task that closes idle sessions.
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="session-cleanup"
        )
        logger.info("SessionManager started.")

    async def shutdown(self) -> None:
        """Close all sessions and stop Playwright."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Close every open session gracefully.
        for session_id in list(self._sessions.keys()):
            await self.close_session(session_id)

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        logger.info("SessionManager stopped.")

    # ── Session CRUD ──────────────────────────────────────────────────────

    async def create_session(
        self,
        url: str,
        width: int = 1280,
        height: int = 800,
        token_id: Optional[str] = None,
    ) -> SessionEntry:
        """
        Launch a new Playwright browser, navigate to *url*, and register the session.

        Returns the ``SessionEntry`` for the new session.
        """
        if self._playwright is None:
            raise RuntimeError("SessionManager has not been started.")

        session_id = _make_session_id()
        controller = BrowserController(width=width, height=height)
        await controller.start(self._playwright, url)

        entry = SessionEntry(
            session_id=session_id,
            url=url,
            width=width,
            height=height,
            created_at=datetime.utcnow(),
            controller=controller,
            token_id=token_id,
        )
        self._sessions[session_id] = entry
        logger.info(
            "Session created: %s → %s (%dx%d)", session_id, url, width, height
        )
        return entry

    def get_session(self, session_id: str) -> Optional[SessionEntry]:
        """Return a session by ID, or None if it does not exist."""
        entry = self._sessions.get(session_id)
        if entry:
            # Update the last-active timestamp on every access.
            entry.last_active = datetime.utcnow()
        return entry

    async def close_session(self, session_id: str) -> bool:
        """
        Close the browser for *session_id* and remove it from the registry.

        Returns True if the session existed, False otherwise.
        """
        entry = self._sessions.pop(session_id, None)
        if entry is None:
            return False
        await entry.controller.close()
        logger.info("Session closed: %s", session_id)
        return True

    def list_all(self) -> list[SessionEntry]:
        """Return a snapshot of all currently active sessions."""
        return list(self._sessions.values())

    def active_count(self) -> int:
        """Return the number of currently active sessions."""
        return len(self._sessions)

    # ── Background cleanup ────────────────────────────────────────────────

    async def _cleanup_loop(self) -> None:
        """Periodically sweep and close sessions that have been idle too long."""
        timeout = timedelta(seconds=settings.session_timeout_seconds)
        while True:
            await asyncio.sleep(60)  # check every minute
            now = datetime.utcnow()
            expired = [
                sid
                for sid, entry in self._sessions.items()
                if (now - entry.last_active) > timeout
            ]
            for sid in expired:
                logger.info("Closing idle session %s (timeout=%ds)", sid, timeout.seconds)
                await self.close_session(sid)


# Module-level singleton – imported by routers and the WebSocket handler.
session_manager = SessionManager()
