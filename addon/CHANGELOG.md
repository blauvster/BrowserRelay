# Changelog

All notable changes to the BrowserRelay Home Assistant add-on will be
documented here.  This project follows [Keep a Changelog](https://keepachangelog.com/).

## [0.1.0] – 2026-02-26

### Added
- Initial Home Assistant OS add-on release.
- Headless Chromium browser via Playwright (asyncio).
- REST API for session management (create, delete, screenshot).
- WebSocket streaming of JPEG screenshots at configurable frame rate.
- Mouse and keyboard event forwarding over WebSocket.
- Token-based client authentication with admin-secret-protected management API.
- TinyDB token store persisted to `/data/tokens.json`.
- Configurable session timeout, screenshot quality, and log level.
- Built-in static frontend client served at the add-on root.
