"""
browser_relay/main.py
─────────────────────────────────────────────────────────────────────────────
FastAPI application factory and Uvicorn entry point.

Startup / shutdown lifecycle
────────────────────────────
* On startup  – Playwright is launched; the token DB directory is created.
* On shutdown – All open browser sessions are closed gracefully.
"""

import logging
import uvicorn

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from browser_relay.config import settings
from browser_relay.sessions.manager import session_manager
from browser_relay.tokens.router import router as tokens_router
from browser_relay.sessions.router import router as sessions_router
from browser_relay.websocket.handler import router as ws_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown tasks."""
    # ── Startup ───────────────────────────────────────────────────────────
    logger.info("BrowserRelay starting up…")

    # Ensure the data directory exists for TinyDB.
    settings.token_db_path.parent.mkdir(parents=True, exist_ok=True)

    # Start the Playwright browser pool / context.
    await session_manager.startup()

    logger.info("BrowserRelay ready.  Listening on %s:%s", settings.host, settings.port)

    yield  # ← application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("BrowserRelay shutting down…")
    await session_manager.shutdown()
    logger.info("BrowserRelay stopped.")


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    app = FastAPI(
        title="BrowserRelay",
        description="Interactive browser-streaming server with client authentication.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────
    # In production, replace ["*"] with your specific frontend origin(s).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API routers ───────────────────────────────────────────────────────
    app.include_router(tokens_router, prefix="/api/tokens", tags=["Token Management"])
    app.include_router(sessions_router, prefix="/api/sessions", tags=["Sessions"])
    app.include_router(ws_router, tags=["WebSocket"])

    # ── Static frontend ───────────────────────────────────────────────────
    # Serve the manager and client from /frontend/ at the root path.
    client_dir = Path(__file__).parent / "frontend"
    if client_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(client_dir), html=True), name="client")

    return app


# Module-level app instance (used by Uvicorn and the `serve` Poetry script).
app = create_app()


def run() -> None:
    """Entry point for `poetry run serve`."""
    uvicorn.run(
        "browser_relay.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    run()
