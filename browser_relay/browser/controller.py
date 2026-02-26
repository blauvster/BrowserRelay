"""
browser_relay/browser/controller.py
─────────────────────────────────────────────────────────────────────────────
Asyncio wrapper around a single Playwright browser context + page.

Each ``BrowserController`` instance owns:
* exactly one Playwright ``Browser`` context (isolated cookies / storage)
* exactly one ``Page`` within that context

All public methods are ``async`` and run on the asyncio event loop via
Playwright's native async API.
"""

import base64
import io
import logging
from typing import Optional

from PIL import Image

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from browser_relay.config import settings

logger = logging.getLogger(__name__)


class BrowserController:
    """
    Controls a single headless Chromium page for one client session.

    Lifecycle
    ─────────
    1. Instantiate with desired viewport dimensions.
    2. Call ``await start(playwright_instance, url)`` to launch the browser.
    3. Use ``screenshot()``, ``click()``, ``type_text()``, etc. during the session.
    4. Call ``await close()`` to release all resources.
    """

    def __init__(self, width: int = 1280, height: int = 800) -> None:
        self.width = width
        self.height = height

        # Set after start()
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self, playwright: Playwright, url: str) -> None:
        """
        Launch a Chromium browser context and navigate to *url*.

        Parameters
        ----------
        playwright:
            A running ``async_playwright()`` context manager result.  The
            session manager is responsible for creating and sharing this.
        url:
            Initial URL to navigate to after the browser opens.
        """
        browser: Browser = await playwright.chromium.launch(
            headless=not settings.playwright_headed,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                # Disable GPU acceleration – not needed in headless mode.
                "--disable-gpu",
            ],
        )

        self._context = await browser.new_context(
            viewport={"width": self.width, "height": self.height},
            # Persist a reasonable user-agent string.
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 BrowserRelay/0.1"
            ),
        )

        self._page = await self._context.new_page()
        logger.info("Browser launched – navigating to %s", url)
        await self.navigate(url)

    async def close(self) -> None:
        """Close the browser context and all associated pages."""
        if self._context:
            try:
                await self._context.close()
            except Exception as exc:
                logger.warning("Error closing browser context: %s", exc)
            finally:
                self._context = None
                self._page = None
        logger.info("Browser context closed.")

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def page(self) -> Page:
        """The active Playwright Page.  Raises if the browser has not started."""
        if self._page is None:
            raise RuntimeError("BrowserController has not been started (call start() first).")
        return self._page

    @property
    def current_url(self) -> str:
        """The URL the page is currently on, or empty string if not started."""
        return self._page.url if self._page else ""

    # ── Navigation ────────────────────────────────────────────────────────

    async def navigate(self, url: str) -> None:
        """Navigate the page to *url* and wait for the network to become idle."""
        logger.debug("Navigating to %s", url)
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)

    # ── Screenshot ────────────────────────────────────────────────────────

    async def screenshot_bytes(self) -> bytes:
        """Capture a JPEG screenshot and return raw bytes."""
        return await self.page.screenshot(
            type="jpeg",
            quality=settings.screenshot_quality,
            full_page=False,
        )

    async def screenshot_b64(self) -> str:
        """Capture a JPEG screenshot and return a base64-encoded string."""
        raw = await self.screenshot_bytes()
        return base64.b64encode(raw).decode()

    async def screenshot_scaled_b64(self, display_width: int, display_height: int) -> str:
        """
        Capture a JPEG screenshot, resize it to *display_width* × *display_height*,
        and return a base64-encoded string.

        If display dimensions match the viewport, the raw screenshot is returned
        without an extra encode round-trip.
        """
        raw = await self.screenshot_bytes()
        if display_width == self.width and display_height == self.height:
            return base64.b64encode(raw).decode()
        img = Image.open(io.BytesIO(raw))
        img = img.resize((display_width, display_height), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=settings.screenshot_quality)
        return base64.b64encode(buf.getvalue()).decode()

    # ── Mouse ─────────────────────────────────────────────────────────────

    async def click(
        self,
        x: float,
        y: float,
        button: str = "left",
    ) -> None:
        """Send a mouse click at page coordinates (x, y)."""
        logger.debug("click(%s, %s, button=%s)", x, y, button)
        await self.page.mouse.click(x, y, button=button)  # type: ignore[arg-type]

    async def move(self, x: float, y: float) -> None:
        """Move the mouse cursor to (x, y) without clicking."""
        await self.page.mouse.move(x, y)

    async def scroll(
        self,
        x: float,
        y: float,
        delta_x: float = 0,
        delta_y: float = 100,
    ) -> None:
        """Scroll the page at position (x, y) by (delta_x, delta_y) pixels."""
        await self.page.mouse.wheel(delta_x, delta_y)

    # ── Keyboard ──────────────────────────────────────────────────────────

    async def key_down(self, key: str) -> None:
        """Press and hold a keyboard key (Playwright key name, e.g. 'Enter')."""
        await self.page.keyboard.down(key)

    async def key_up(self, key: str) -> None:
        """Release a keyboard key."""
        await self.page.keyboard.up(key)

    async def type_text(self, text: str) -> None:
        """Type a string of characters with realistic delays."""
        await self.page.keyboard.type(text, delay=20)

    # ── Viewport ──────────────────────────────────────────────────────────

    async def resize(self, width: int, height: int) -> None:
        """Change the viewport size. Updates stored dimensions."""
        self.width = width
        self.height = height
        await self.page.set_viewport_size({"width": width, "height": height})
        logger.debug("Viewport resized to %sx%s", width, height)
