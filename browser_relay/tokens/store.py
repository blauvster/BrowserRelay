"""
browser_relay/tokens/store.py
─────────────────────────────────────────────────────────────────────────────
Token persistence layer.

``AbstractTokenStore`` defines the interface that all storage backends must
implement.  Swap the class passed to ``get_token_store()`` to change the
backend without touching any other module.

``TinyDBTokenStore`` provides a file-backed JSON implementation using TinyDB.
It is synchronous under the hood but wrapped in ``asyncio.to_thread`` so
FastAPI's async handlers never block the event loop.
"""

import asyncio
import hashlib
import logging
import secrets
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from tinydb import Query, TinyDB

from browser_relay.config import settings
from browser_relay.tokens.models import TokenRecord

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Token generation helpers
# ─────────────────────────────────────────────────────────────────────────────

_TOKEN_PREFIX = "br_"
_ID_PREFIX = "tok_"


def _generate_raw_token() -> str:
    """Return a cryptographically random URL-safe token string."""
    return _TOKEN_PREFIX + secrets.token_urlsafe(32)


def _generate_token_id() -> str:
    """Return a short opaque identifier for a token record."""
    return _ID_PREFIX + secrets.token_hex(8)


def _hash(raw: str) -> str:
    """SHA-256 hex digest of *raw*."""
    return hashlib.sha256(raw.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Abstract interface
# ─────────────────────────────────────────────────────────────────────────────


class AbstractTokenStore(ABC):
    """
    Interface for token persistence backends.

    All methods are async.  Concrete implementations that use synchronous I/O
    must wrap their calls in ``asyncio.to_thread()``.
    """

    @abstractmethod
    async def create(self, label: str) -> tuple[TokenRecord, str]:
        """
        Create and persist a new token.

        Returns ``(TokenRecord, raw_token)`` where *raw_token* must be
        returned to the caller **once** and then discarded.
        """

    @abstractmethod
    async def list_all(self) -> list[TokenRecord]:
        """Return all token records (including revoked ones)."""

    @abstractmethod
    async def get(self, token_id: str) -> Optional[TokenRecord]:
        """Return a single record by ``token_id``, or ``None``."""

    @abstractmethod
    async def get_by_hash(self, token_hash: str) -> Optional[TokenRecord]:
        """Lookup a record by the SHA-256 hash of the raw token value."""

    @abstractmethod
    async def revoke(self, token_id: str) -> bool:
        """
        Mark a token as revoked.

        Returns ``True`` if the token was found, ``False`` if not.
        """

    @abstractmethod
    async def rotate(self, token_id: str) -> Optional[tuple[TokenRecord, str]]:
        """
        Replace the token value for an existing record.

        Returns ``(updated TokenRecord, new raw_token)`` or ``None`` if the
        token_id does not exist.
        """

    @abstractmethod
    async def touch(self, token_id: str) -> None:
        """Update ``last_used_at`` to now for the given token_id."""

    @abstractmethod
    async def delete(self, token_id: str) -> bool:
        """Permanently delete a token record.  Returns True if found."""


# ─────────────────────────────────────────────────────────────────────────────
# TinyDB implementation
# ─────────────────────────────────────────────────────────────────────────────


class TinyDBTokenStore(AbstractTokenStore):
    """
    File-backed JSON token store using TinyDB.

    TinyDB is single-threaded.  All synchronous calls are executed via
    ``asyncio.to_thread`` so the event loop is not blocked.
    """

    def __init__(self, db_path: str) -> None:
        # TinyDB creates the file automatically if it does not exist.
        self._db = TinyDB(db_path)
        self._table = self._db.table("tokens")
        logger.info("TinyDBTokenStore initialised at %s", db_path)

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _record_from_doc(doc: dict) -> TokenRecord:
        """Convert a raw TinyDB document dict to a TokenRecord."""
        # TinyDB stores datetimes as ISO strings.
        for field in ("created_at", "last_used_at"):
            if isinstance(doc.get(field), str):
                doc[field] = datetime.fromisoformat(doc[field])
        return TokenRecord(**doc)

    def _sync_list_all(self) -> list[TokenRecord]:
        return [self._record_from_doc(dict(d)) for d in self._table.all()]

    def _sync_get(self, token_id: str) -> Optional[TokenRecord]:
        T = Query()
        doc = self._table.get(T.token_id == token_id)
        return self._record_from_doc(dict(doc)) if doc else None

    def _sync_get_by_hash(self, token_hash: str) -> Optional[TokenRecord]:
        T = Query()
        doc = self._table.get(T.token_hash == token_hash)
        return self._record_from_doc(dict(doc)) if doc else None

    # ── AbstractTokenStore implementation ─────────────────────────────────

    async def create(self, label: str) -> tuple[TokenRecord, str]:
        raw = _generate_raw_token()
        record = TokenRecord(
            token_id=_generate_token_id(),
            token_hash=_hash(raw),
            token_prefix=raw[:10],  # "br_" + 7 chars
            label=label,
        )

        def _sync_insert():
            data = record.model_dump()
            # Convert datetimes to ISO strings for TinyDB serialisation.
            data["created_at"] = data["created_at"].isoformat()
            if data["last_used_at"]:
                data["last_used_at"] = data["last_used_at"].isoformat()
            self._table.insert(data)

        await asyncio.to_thread(_sync_insert)
        logger.info("Created token %s (label=%r)", record.token_id, label)
        return record, raw

    async def list_all(self) -> list[TokenRecord]:
        return await asyncio.to_thread(self._sync_list_all)

    async def get(self, token_id: str) -> Optional[TokenRecord]:
        return await asyncio.to_thread(self._sync_get, token_id)

    async def get_by_hash(self, token_hash: str) -> Optional[TokenRecord]:
        return await asyncio.to_thread(self._sync_get_by_hash, token_hash)

    async def revoke(self, token_id: str) -> bool:
        def _sync_revoke():
            T = Query()
            updated = self._table.update({"revoked": True}, T.token_id == token_id)
            return bool(updated)

        result = await asyncio.to_thread(_sync_revoke)
        if result:
            logger.info("Revoked token %s", token_id)
        return result

    async def rotate(self, token_id: str) -> Optional[tuple[TokenRecord, str]]:
        record = await self.get(token_id)
        if record is None:
            return None

        new_raw = _generate_raw_token()
        new_hash = _hash(new_raw)
        new_prefix = new_raw[:10]

        def _sync_rotate():
            T = Query()
            self._table.update(
                {
                    "token_hash": new_hash,
                    "token_prefix": new_prefix,
                    "revoked": False,
                    "last_used_at": None,
                },
                T.token_id == token_id,
            )

        await asyncio.to_thread(_sync_rotate)
        updated = await self.get(token_id)
        logger.info("Rotated token %s", token_id)
        return updated, new_raw  # type: ignore[return-value]

    async def touch(self, token_id: str) -> None:
        now_iso = datetime.utcnow().isoformat()

        def _sync_touch():
            T = Query()
            self._table.update({"last_used_at": now_iso}, T.token_id == token_id)

        await asyncio.to_thread(_sync_touch)

    async def delete(self, token_id: str) -> bool:
        def _sync_delete():
            T = Query()
            removed = self._table.remove(T.token_id == token_id)
            return bool(removed)

        result = await asyncio.to_thread(_sync_delete)
        if result:
            logger.info("Deleted token %s", token_id)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI dependency
# ─────────────────────────────────────────────────────────────────────────────

# Module-level singleton to avoid re-opening TinyDB on every request.
_store: Optional[AbstractTokenStore] = None


def get_token_store() -> AbstractTokenStore:
    """
    FastAPI dependency that returns the shared token store singleton.

    To swap the backend, change the implementation instantiated here.
    The rest of the application only sees ``AbstractTokenStore``.
    """
    global _store
    if _store is None:
        _store = TinyDBTokenStore(str(settings.token_db_path))
    return _store
