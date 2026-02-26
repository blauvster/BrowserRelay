# BrowserRelay – Add-on Documentation

BrowserRelay exposes a headless Chromium browser as a networked service.
Authenticated clients can:

1. Open a **browser session** at any URL via REST.
2. Receive a **live JPEG screenshot stream** over WebSocket (~10 fps).
3. Send **mouse and keyboard events** back over the same WebSocket.

---

## Configuration

### `admin_secret` (required)

A long random string used to authenticate calls to the token-management API
(`/api/tokens`).  Generate a strong secret before starting the add-on:

```bash
openssl rand -hex 32
```

**This must be set before the add-on will start.**

### `session_timeout_seconds` (default: `3600`)

Seconds of inactivity before an open browser session is automatically closed
and its resources freed.  Minimum 60, maximum 86400 (24 h).

### `screenshot_interval_seconds` (default: `0.1`)

How often the streaming loop captures a new screenshot (~10 fps at 0.1 s).
Decrease for smoother video; increase to reduce CPU/bandwidth usage.

### `screenshot_quality` (default: `75`)

JPEG quality 1–100 sent to connected clients.  Lower values reduce payload
size at the cost of image fidelity.

### `log_level` (default: `info`)

Uvicorn / Python log verbosity: `debug`, `info`, `warning`, or `error`.

---

## Token management

After starting the add-on, use the **admin API** to issue tokens for your
clients.  Replace `YOUR_ADMIN_SECRET` with the value you configured above.

### Create a token

```bash
curl -X POST http://homeassistant.local:8000/api/tokens \
  -H "X-Admin-Secret: YOUR_ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"label": "my-client"}'
```

The response contains a `token` field.  **Copy it now** – it is not stored in
plain text and cannot be retrieved later.

### List tokens

```bash
curl http://homeassistant.local:8000/api/tokens \
  -H "X-Admin-Secret: YOUR_ADMIN_SECRET"
```

### Revoke a token

```bash
curl -X DELETE http://homeassistant.local:8000/api/tokens/tok_xxxxx \
  -H "X-Admin-Secret: YOUR_ADMIN_SECRET"
```

---

## Using the web UI

Open the add-on's **Web UI** button in the Home Assistant sidebar.  Enter your
API token and a starting URL to launch an interactive browser session.

---

## Data persistence

Tokens are stored in `/data/tokens.json` inside the container.  This path is
managed by the Supervisor and survives add-on restarts, updates, and
Home Assistant reboots.  Include it in HA backups to preserve your issued tokens.

---

## Security notes

- Keep `admin_secret` long and secret – it grants full control over all tokens.
- In production, access the add-on through the HA Ingress proxy (HTTPS) rather
  than exposing port 8000 directly.
- Restrict CORS origins in `browser_relay/main.py` if embedding the client in
  a specific frontend.
