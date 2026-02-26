# BrowserRelay

Interactive browser-streaming server that lets authenticated clients control a headless Chromium instance via REST + WebSocket.  Built with **FastAPI**, **Uvicorn**, and **Playwright** (asyncio API), managed with **Poetry**.  Ships with a **Docker** image and a **Home Assistant OS add-on** for zero-dependency deployment.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Installation & Setup](#installation--setup)
5. [Configuration](#configuration)
6. [Running the Server](#running-the-server)
7. [Docker](#docker)
8. [Home Assistant OS Add-on](#home-assistant-os-add-on)
9. [Authentication Model](#authentication-model)
10. [API Reference](#api-reference)
    - [Token Management (admin)](#token-management-admin)
    - [Session Management (client)](#session-management-client)
    - [WebSocket Streaming](#websocket-streaming)
11. [Frontend Demo Client](#frontend-demo-client)
12. [Security Considerations](#security-considerations)
13. [Extending the Project](#extending-the-project)
14. [Development](#development)

---

## Overview

BrowserRelay exposes a headless Chromium browser as a service.  An administrator creates **API tokens** for clients.  Clients use those tokens to:

1. Open a **browser session** at a target URL (REST).
2. Connect via **WebSocket** to receive a live JPEG screenshot stream.
3. Send **mouse / keyboard events** back over the same WebSocket.

The token management API is protected by a separate **admin secret** declared in the environment.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  FastAPI Application                │
│                                                     │
│  ┌──────────────┐   ┌──────────────────────────┐   │
│  │  /api/tokens │   │     /api/sessions         │   │
│  │  (admin auth)│   │ (client API-token auth)   │   │
│  └──────────────┘   └──────────────────────────┘   │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │   /ws/session/{session_id}  (WebSocket)       │   │
│  │   client API-token required as query param   │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ TokenStore │  │ SessionMgr   │  │ Browser-   │  │
│  │ (TinyDB)   │  │ (in-memory)  │  │ Controller │  │
│  └────────────┘  └──────────────┘  │ (Playwright│  │
│                                    └────────────┘  │
└─────────────────────────────────────────────────────┘
         ▲                             ▲
         │ admin HTTP                  │ client WebSocket / HTTP
         │                             │
   Admin CLI / curl              Browser / JS client
```

---

## Prerequisites

**Poetry (local) installation:**

| Requirement | Version |
|---|---|
| Python | 3.10+ |
| Poetry | 1.8+ |
| Chromium (via Playwright) | installed automatically |

**Docker installation:**

| Requirement | Version |
|---|---|
| Docker | 24+ |
| Docker Compose | v2 (`docker compose`) |

---

## Installation & Setup

### 1. Install Poetry (if not already installed)

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

Or follow the [official guide](https://python-poetry.org/docs/#installation).

### 2. Clone & install Python dependencies

```bash
git clone <repo-url> BrowserRelay
cd BrowserRelay
poetry install
```

### 3. Install Playwright browsers

```bash
poetry run playwright install chromium
```

> Only Chromium is required.  Add `--with-deps` on a fresh Linux system to install OS-level dependencies automatically:
> ```bash
> poetry run playwright install --with-deps chromium
> ```

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env with your admin secret and desired settings
```

At minimum, change `ADMIN_SECRET` to a long random string:

```bash
# Generate a strong secret (Linux/macOS)
openssl rand -hex 32
```

---

## Configuration

All configuration is loaded from environment variables (or `.env`).  See `.env.example` for the full list:

| Variable | Default | Description |
|---|---|---|
| `ADMIN_SECRET` | *(required)* | Password for admin endpoints |
| `TOKEN_DB_PATH` | `data/tokens.json` | Path to TinyDB JSON file |
| `SESSION_TIMEOUT_SECONDS` | `3600` | Seconds before idle sessions expire |
| `HOST` | `0.0.0.0` | Uvicorn bind host |
| `PORT` | `8000` | Uvicorn bind port |
| `LOG_LEVEL` | `info` | Uvicorn / Python log level |
| `PLAYWRIGHT_HEADED` | `0` | Set to `1` to show the browser window |

---

## Running the Server

```bash
# Standard (uses PORT / HOST from .env)
poetry run serve

# Or directly with Uvicorn (useful for --reload during development)
poetry run uvicorn browser_relay.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Docker

The repository ships a `Dockerfile` and `docker-compose.yml` so you can run BrowserRelay without installing Python, Poetry, or Playwright locally.  The image is based on Microsoft's official `playwright/python` base image, which bundles Chromium and all required system libraries.

### Quick start with Docker Compose

```bash
# 1. Set your admin secret (or export it in your shell)
echo 'ADMIN_SECRET=your-long-random-secret' > .env

# 2. Build the image
docker compose build

# 3. Start the server
docker compose up -d

# 4. Tail logs
docker compose logs -f

# 5. Stop
docker compose down
```

The server is available at `http://localhost:8000` and the interactive API docs at `http://localhost:8000/docs`.

### Quick start with plain Docker

```bash
# Build
docker build -t browser-relay .

# Run
docker run -d \
  --name browser-relay \
  -p 8000:8000 \
  -v "$(pwd)/data:/app/data" \
  -e ADMIN_SECRET=your-long-random-secret \
  --ipc=host \
  --security-opt seccomp=unconfined \
  browser-relay
```

### Data persistence

The `data/` directory (TinyDB token store) is mounted as a Docker volume:

```yaml
volumes:
  - ./data:/app/data
```

Tokens survive container restarts and upgrades as long as this bind-mount (or a named volume) is kept.

### Environment variables in Docker

All settings from the [Configuration](#configuration) table can be passed as environment variables.  The easiest approach is a `.env` file in the project root — `docker compose` reads it automatically:

```bash
# .env
ADMIN_SECRET=your-long-random-secret
SESSION_TIMEOUT_SECONDS=1800
SCREENSHOT_QUALITY=60
```

### Chromium sandbox notes

Chromium requires either the `--no-sandbox` flag or elevated kernel privileges inside a container.  The provided `docker-compose.yml` uses:

```yaml
ipc: host                        # prevents shared-memory exhaustion
security_opt:
  - seccomp=unconfined           # allows Chromium sandbox syscalls
```

If your container runtime supports user namespaces (e.g. `userns-remap` in Docker daemon config), you can tighten the `seccomp` profile instead.

### Building for production

For a production deployment, consider:

1. Pinning the base image digest instead of the tag.
2. Running behind a TLS-terminating reverse proxy (nginx, Caddy, Traefik).
3. Using Docker secrets or a vault for `ADMIN_SECRET` instead of a `.env` file.

---

## Home Assistant OS Add-on

BrowserRelay ships as a native **Home Assistant OS add-on**, so you can run it directly on your Home Assistant instance without any separate server or Docker knowledge.

### Add-on file structure

```
addon/
├── config.yaml      ← add-on manifest & user-configurable options
├── Dockerfile       ← HA-adapted image using Playwright base
├── build.yaml       ← per-arch base image overrides
├── run.sh           ← startup script (reads /data/options.json via bashio)
├── DOCS.md          ← documentation shown in the HA UI
└── CHANGELOG.md
repository.yaml      ← repository manifest (repo root)
```

### Installation

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**.
2. Click ⋮ (top-right) → **Repositories**.
3. Paste your GitHub repository URL and click **Add**.
4. Find **BrowserRelay** in the store and click **Install**.

### Configuration

Before starting the add-on, set **at minimum** the `admin_secret` in the add-on's **Configuration** tab:

| Option | Default | Description |
|---|---|---|
| `admin_secret` | *(required)* | Secret for admin API endpoints |
| `session_timeout_seconds` | `3600` | Idle session expiry (60–86400) |
| `screenshot_interval_seconds` | `0.1` | Frame interval in seconds |
| `screenshot_quality` | `75` | JPEG quality 1–100 |
| `log_level` | `info` | `debug`, `info`, `warning`, or `error` |

Generate a strong secret:

```bash
openssl rand -hex 32
```

### Data persistence

The token store (`tokens.json`) is written to `/data` inside the container, which is managed by the HA Supervisor.  Tokens survive add-on restarts, updates, and HA reboots, and are included in standard HA backups automatically.

### Ingress & web UI

The add-on registers with HA Ingress so the browser UI is accessible from the HA sidebar without exposing any extra ports.  For direct API access (e.g. from ESPHome devices on your LAN), also enable port `8000` in the **Network** tab.

### Building the add-on image locally

The add-on uses Microsoft's `playwright/python` image as its base (configured in `addon/build.yaml`) rather than the default HA Alpine images, because Chromium requires Debian/Ubuntu system libraries.  To build locally for testing:

```bash
docker build \
  --build-arg BUILD_FROM=mcr.microsoft.com/playwright/python:v1.44.0-jammy \
  --build-arg BUILD_ARCH=amd64 \
  --build-arg BUILD_VERSION=0.1.0 \
  -f addon/Dockerfile \
  -t local/browser_relay:latest \
  .
```

### Publishing

To distribute to others, push the repository to GitHub (public).  Users add your repo URL to their HA add-on repositories.  For wider distribution, you can submit to the [Home Assistant Community Add-ons](https://github.com/hassio-addons) organisation or the official HA add-on store (requires review).

---

## Authentication Model

### Admin authentication

Pass the `ADMIN_SECRET` value in the `X-Admin-Secret` HTTP header for all `/api/tokens` endpoints.

### Client API-token authentication

Pass the client's API token in the `X-API-Token` HTTP header for all `/api/sessions` endpoints and as the `token` query-parameter for WebSocket connections.

---

## API Reference

### Token Management (admin)

All endpoints require: `X-Admin-Secret: <your-admin-secret>`

---

#### `POST /api/tokens` — Create a new client API token

**Request body (JSON):**

```json
{
  "label": "acme-corp-client"
}
```

**Response:**

```json
{
  "token_id": "tok_a1b2c3d4",
  "token": "br_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "label": "acme-corp-client",
  "created_at": "2026-02-26T12:00:00Z",
  "last_used_at": null,
  "revoked": false
}
```

> **Important:** The `token` value is only returned on creation.  Store it securely; it cannot be retrieved again.

**curl example:**

```bash
curl -X POST http://localhost:8000/api/tokens \
  -H "X-Admin-Secret: your-admin-secret" \
  -H "Content-Type: application/json" \
  -d '{"label": "acme-corp-client"}'
```

**httpie example:**

```bash
http POST http://localhost:8000/api/tokens \
  X-Admin-Secret:your-admin-secret \
  label=acme-corp-client
```

---

#### `GET /api/tokens` — List all tokens

```bash
curl http://localhost:8000/api/tokens \
  -H "X-Admin-Secret: your-admin-secret"
```

**Response:** array of token metadata objects (token values are NOT included).

---

#### `DELETE /api/tokens/{token_id}` — Revoke a token

```bash
curl -X DELETE http://localhost:8000/api/tokens/tok_a1b2c3d4 \
  -H "X-Admin-Secret: your-admin-secret"
```

**Response:**

```json
{"detail": "Token tok_a1b2c3d4 revoked."}
```

---

#### `POST /api/tokens/{token_id}/rotate` — Rotate (regenerate) a token

Generates a new token value for the same `token_id`, immediately invalidating the old value.

```bash
curl -X POST http://localhost:8000/api/tokens/tok_a1b2c3d4/rotate \
  -H "X-Admin-Secret: your-admin-secret"
```

---

### Session Management (client)

All endpoints require: `X-API-Token: <client-token>`

---

#### `POST /api/sessions` — Start a browser session

**Request body:**

```json
{
  "url": "https://example.com",
  "width": 1280,
  "height": 800
}
```

**Response:**

```json
{
  "session_id": "sess_abc123",
  "url": "https://example.com",
  "width": 1280,
  "height": 800,
  "created_at": "2026-02-26T12:00:00Z",
  "ws_url": "/ws/session/sess_abc123"
}
```

**curl example:**

```bash
curl -X POST http://localhost:8000/api/sessions \
  -H "X-API-Token: br_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "width": 1280, "height": 800}'
```

**Python example:**

```python
import httpx

TOKEN = "br_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
resp = httpx.post(
    "http://localhost:8000/api/sessions",
    headers={"X-API-Token": TOKEN},
    json={"url": "https://example.com", "width": 1280, "height": 800},
)
session = resp.json()
print(session["session_id"])
```

---

#### `DELETE /api/sessions/{session_id}` — Close a session

```bash
curl -X DELETE http://localhost:8000/api/sessions/sess_abc123 \
  -H "X-API-Token: br_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

---

#### `GET /api/sessions/{session_id}/screenshot` — Snapshot

Returns the latest JPEG screenshot as raw bytes (`image/jpeg`).

```bash
curl http://localhost:8000/api/sessions/sess_abc123/screenshot \
  -H "X-API-Token: br_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  --output screenshot.jpg
```

---

### WebSocket Streaming

Connect to:

```
ws://localhost:8000/ws/session/{session_id}?token=<client-token>
```

#### Messages sent **from server → client**

| Type | Payload | Description |
|---|---|---|
| `screenshot` | `{"type":"screenshot","data":"<base64-jpeg>"}` | Periodic screenshot frame |
| `error` | `{"type":"error","message":"..."}` | Server-side error notification |

#### Messages sent **from client → server**

All messages are JSON objects with a `type` field.

| type | Additional fields | Description |
|---|---|---|
| `click` | `x`, `y`, `button` (`left`/`right`/`middle`) | Mouse click |
| `move` | `x`, `y` | Mouse move |
| `scroll` | `x`, `y`, `delta_x`, `delta_y` | Mouse scroll |
| `keydown` | `key` (e.g. `"Enter"`, `"a"`) | Key press |
| `keyup` | `key` | Key release |
| `type` | `text` | Type a string of characters |
| `navigate` | `url` | Navigate to a new URL |
| `resize` | `width`, `height` | Resize the browser viewport |
| `screenshot` | — | Request an immediate screenshot |

**JavaScript example:**

```javascript
const ws = new WebSocket(
  `ws://localhost:8000/ws/session/${sessionId}?token=${apiToken}`
);

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "screenshot") {
    document.getElementById("screen").src = "data:image/jpeg;base64," + msg.data;
  }
};

// Send a click
ws.send(JSON.stringify({ type: "click", x: 640, y: 400, button: "left" }));

// Type text
ws.send(JSON.stringify({ type: "type", text: "Hello, world!" }));
```

---

## Frontend Demo Client

The static client lives in `client/index.html`.  It is served automatically by FastAPI at `http://localhost:8000/`.

**Features:**

- Enter your API token and a starting URL, then click **Start Session**.
- The live screenshot is shown as a `<img>` updated ~10 fps.
- Click on the image to send click events.
- A text box lets you send keystrokes.

---

## Security Considerations

### Admin secret

- Keep `ADMIN_SECRET` long (≥32 random bytes) and out of version control.
- Rotate it if you suspect leakage (all issued tokens remain valid until individually revoked).
- In production, place the admin endpoints behind a private network or VPN.

### Client API tokens

- Tokens are stored **hashed** (SHA-256) in the database.  Only the prefix is stored in plain text for display.
- Revoke tokens immediately if a client is decommissioned.
- Use the `rotate` endpoint to cycle tokens periodically.

### WebSocket token exposure

- The token appears in the WebSocket URL query string.  Use TLS (`wss://`) in production to prevent interception.
- For higher security, consider exchanging the token for a short-lived one-time ticket before upgrading to WebSocket.

### General

- Always run behind a reverse proxy (nginx, Caddy) with TLS in production.
- Restrict `CORS` origins in `browser_relay/main.py` to your specific domains.
- Rate-limit session creation to avoid resource exhaustion.

---

## Extending the Project

### Swapping the token database

Replace `browser_relay/tokens/store.py` with any class implementing the `AbstractTokenStore` interface defined in `browser_relay/tokens/store.py`.  A SQLAlchemy implementation can be dropped in without changing any other file.

### Adding session persistence / clustering

`SessionManager` in `browser_relay/sessions/manager.py` holds sessions in a plain `dict`.  Replace it with a Redis-backed store and use `playwright` with a remote browser endpoint (`connect_over_cdp`) to support multi-process deployments.

### Scaling Playwright

Playwright browsers are single-process.  For high concurrency, run multiple worker processes and load-balance sessions across them, or use a Playwright cluster (e.g., `browserless.io`).

---

## Development

```bash
# Lint
poetry run ruff check .

# Type-check
poetry run mypy browser_relay

# Run tests
poetry run pytest

# Auto-reload dev server
poetry run uvicorn browser_relay.main:app --reload
```
