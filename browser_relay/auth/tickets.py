"""
browser_relay/auth/tickets.py
─────────────────────────────────────────────────────────────────────────────
Short-lived, in-memory ticket store.

A *ticket* is an opaque ``brt_…`` token the manager generates on behalf of a
client token so the admin can open the client viewer without knowing (or
exposing) the permanent raw API token.

Lifecycle
─────────
1. Admin calls ``POST /api/tokens/{token_id}/ticket`` (admin auth required).
2. Server mints a ticket and stores ``(ticket → token_id, expires_at)``.
3. Ticket string is passed as ``?token=brt_…`` to the client page URL.
4. Client page uses it exactly like a normal ``br_…`` token.
5. ``require_token`` / ``ws_require_token`` detect the ``brt_`` prefix and
   look up the ticket instead of hashing against the token store.
6. Once consumed or expired the ticket is discarded.

Tickets are stored only in process memory; they are intentionally ephemeral
and are not persisted across server restarts.
"""

import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

TICKET_PREFIX = "brt_"
TICKET_TTL_SECONDS = 300  # 5 minutes


# ── Ticket record ─────────────────────────────────────────────────────────────


@dataclass
class Ticket:
    token_id: str
    expires_at: float = field(default_factory=lambda: time.monotonic() + TICKET_TTL_SECONDS)

    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


# ── In-memory store ───────────────────────────────────────────────────────────


class TicketStore:
    """Thread-safe (GIL), process-local ticket store."""

    def __init__(self) -> None:
        self._store: dict[str, Ticket] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def issue(self, token_id: str) -> str:
        """
        Create a new ticket for *token_id* and return the raw ticket string.
        Old expired tickets are pruned on each call.
        """
        self._prune()
        raw = TICKET_PREFIX + secrets.token_urlsafe(32)
        self._store[raw] = Ticket(token_id=token_id)
        return raw

    def consume(self, raw: str) -> Optional[str]:
        """
        Validate *raw* and return the associated ``token_id``.

        The ticket is removed from the store (single-use) and ``None`` is
        returned for unknown or expired tickets.
        """
        ticket = self._store.pop(raw, None)
        if ticket is None or ticket.is_expired():
            return None
        return ticket.token_id

    def peek(self, raw: str) -> Optional[str]:
        """
        Return the ``token_id`` for *raw* **without** consuming the ticket.

        Used by the WebSocket authenticator, which may need to re-validate the
        token on reconnect within the same browser session.
        """
        ticket = self._store.get(raw)
        if ticket is None or ticket.is_expired():
            self._store.pop(raw, None)
            return None
        return ticket.token_id

    # ── Private ───────────────────────────────────────────────────────────────

    def _prune(self) -> None:
        """Remove all expired tickets."""
        expired = [k for k, v in self._store.items() if v.is_expired()]
        for k in expired:
            del self._store[k]


# ── Singleton ─────────────────────────────────────────────────────────────────

ticket_store = TicketStore()
