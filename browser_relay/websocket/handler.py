"""
browser_relay/websocket/handler.py
─────────────────────────────────────────────────────────────────────────────
WebSocket endpoint for interactive browser streaming.

Protocol
────────
The server pushes JPEG screenshot frames encoded as base64 JSON messages:

    {"type": "screenshot", "data": "<base64>"}

On connect the server immediately sends session metadata:

    {"type": "session_info", "url": "...", "width": N, "height": N,
     "display_width": N, "display_height": N}

Subsequent navigations push URL-change frames:

    {"type": "url", "url": "..."}

The client sends JSON control messages.  Supported ``type`` values:

    click        – {"type":"click","x":N,"y":N,"button":"left"}
    move         – {"type":"move","x":N,"y":N}
    scroll       – {"type":"scroll","x":N,"y":N,"delta_x":N,"delta_y":N}
    keydown      – {"type":"keydown","key":"Enter"}
    keyup        – {"type":"keyup","key":"Enter"}
    type         – {"type":"type","text":"hello"}
    navigate     – {"type":"navigate","url":"https://..."}
    resize       – {"type":"resize","width":N,"height":N}
    set_display  – {"type":"set_display","width":N,"height":N}
    screenshot   – {"type":"screenshot"}  (request an immediate frame)
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status

from browser_relay.auth.dependencies import ws_require_token
from browser_relay.config import settings
from browser_relay.sessions.manager import SessionEntry, session_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _send_error(ws: WebSocket, message: str) -> None:
    """Send a JSON error frame to the client (best-effort; swallow send errors)."""
    try:
        await ws.send_json({"type": "error", "message": message})
    except Exception:
        pass


async def _handle_client_message(entry: SessionEntry, raw: str) -> None:
    """
    Parse and dispatch a single client control message.

    All Playwright calls are awaited directly - they run in the event loop's
    executor so the streaming loop is not blocked (Playwright's asyncio API
    is non-blocking by design).
    """
    try:
        msg: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Received non-JSON message from client; ignoring.")
        return

    t = msg.get("type")
    ctrl = entry.controller

    try:
        if t == "click":
            await ctrl.click(
                x=float(msg["x"]),
                y=float(msg["y"]),
                button=msg.get("button", "left"),
            )
        elif t == "move":
            await ctrl.move(x=float(msg["x"]), y=float(msg["y"]))
        elif t == "scroll":
            await ctrl.scroll(
                x=float(msg.get("x", 0)),
                y=float(msg.get("y", 0)),
                delta_x=float(msg.get("delta_x", 0)),
                delta_y=float(msg.get("delta_y", 100)),
            )
        elif t == "keydown":
            await ctrl.key_down(str(msg["key"]))
        elif t == "keyup":
            await ctrl.key_up(str(msg["key"]))
        elif t == "type":
            await ctrl.type_text(str(msg["text"]))
        elif t == "navigate":
            await ctrl.navigate(str(msg["url"]))
        elif t == "resize":
            w = max(320, min(3840, int(msg["width"])))
            h = max(200, min(2160, int(msg["height"])))
            await ctrl.resize(w, h)
            # Keep the SessionEntry in sync so session_info always reflects
            # the current viewport size.
            entry.width  = w
            entry.height = h
        elif t == "set_display":
            # Update the display resolution used by the streaming loop.
            # Clamp to sane bounds so a bad client can't OOM the server.
            entry.display_width  = max(160, min(3840, int(msg["width"])))
            entry.display_height = max(120, min(2160, int(msg["height"])))
            logger.debug(
                "Display resolution set to %dx%d for session %s",
                entry.display_width, entry.display_height, entry.session_id,
            )
        elif t == "screenshot":
            pass  # Screenshot is sent by the streaming loop; nothing to do here.
        else:
            logger.debug("Unknown message type from client: %r", t)
    except KeyError as exc:
        logger.warning("Missing field in %r message: %s", t, exc)
    except Exception as exc:
        logger.exception("Error handling message type %r: %s", t, exc)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket endpoint
# ─────────────────────────────────────────────────────────────────────────────


@router.websocket("/ws/session/{session_id}")
async def ws_session(
    websocket: WebSocket,
    session_id: str,
    # Validates the ?token= query param; raises HTTP 403 if invalid.
    _token: str = Depends(ws_require_token),
) -> None:
    """
    Interactive streaming WebSocket for a browser session.

    The endpoint runs two concurrent tasks:

    1. **Streaming loop** – captures a JPEG screenshot every
       ``settings.screenshot_interval_seconds`` seconds and pushes it as a
       base64-encoded JSON frame.
    2. **Receive loop** – reads control messages from the client and dispatches
       them to the ``BrowserController``.

    Both loops run until the client disconnects or an error occurs.
    """
    # Verify the session exists before accepting the WebSocket upgrade.
    entry = session_manager.get_session(session_id)
    if entry is None:
        # FastAPI will send a 403 response and close the connection.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Session '{session_id}' not found.",
        )

    await websocket.accept()
    logger.info("WebSocket client connected to session %s", session_id)

    # ── URL change notifications ───────────────────────────────────────────
    # Attach a Playwright page event so any navigation (explicit, click-driven,
    # or redirect) pushes the new URL to the client immediately.

    def _on_framenavigated(frame) -> None:  # type: ignore[no-untyped-def]
        if frame == entry.controller.page.main_frame:
            asyncio.create_task(
                websocket.send_json({"type": "url", "url": frame.url})
            )

    entry.controller.page.on("framenavigated", _on_framenavigated)

    # Send session metadata immediately on connect so the client can populate
    # its URL bar, viewport-size inputs, and display-resolution inputs without
    # needing a separate REST call.
    try:
        await websocket.send_json({
            "type":           "session_info",
            "url":            entry.controller.current_url or entry.url,
            "width":          entry.controller.width,
            "height":         entry.controller.height,
            "display_width":  entry.display_width,
            "display_height": entry.display_height,
        })
    except Exception:
        pass

    # ── Tasks ─────────────────────────────────────────────────────────────

    async def streaming_loop() -> None:
        """Push periodic screenshot frames to the client."""
        interval = settings.screenshot_interval_seconds
        while True:
            try:
                b64 = await entry.controller.screenshot_scaled_b64(
                    entry.display_width, entry.display_height
                )
            except WebSocketDisconnect:
                return
            except Exception as exc:
                # Transient capture failure (e.g. page mid-navigation, fonts
                # loading, renderer busy).  Notify the client but keep the
                # loop alive so it recovers on the next tick.
                logger.warning("Screenshot capture error on session %s: %s", session_id, exc)
                await _send_error(websocket, f"Screenshot error: {exc}")
                await asyncio.sleep(interval)
                continue

            try:
                await websocket.send_json({"type": "screenshot", "data": b64})
            except WebSocketDisconnect:
                return
            except Exception as exc:
                # Fatal: the WebSocket itself is broken.
                logger.warning("WebSocket send error on session %s: %s", session_id, exc)
                return

            await asyncio.sleep(interval)

    async def receive_loop() -> None:
        """Forward incoming client messages to the browser controller."""
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                return
            except Exception as exc:
                logger.warning("Receive error on session %s: %s", session_id, exc)
                return
            # Dispatch without blocking the receive loop.
            asyncio.create_task(_handle_client_message(entry, raw))

    # Run both loops concurrently; cancel the other when one exits.
    streaming_task = asyncio.create_task(streaming_loop())
    receive_task = asyncio.create_task(receive_loop())

    done, pending = await asyncio.wait(
        [streaming_task, receive_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # Clean up the page event listener to avoid lingering callbacks when
    # the same session is later re-joined by another WebSocket client.
    try:
        entry.controller.page.remove_listener("framenavigated", _on_framenavigated)
    except Exception:
        pass

    logger.info("WebSocket disconnected from session %s", session_id)
