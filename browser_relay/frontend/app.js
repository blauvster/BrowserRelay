/**
 * BrowserRelay – Demo Client (app.js)
 * ─────────────────────────────────────────────────────────────────────────────
 * Single-page WebSocket client.
 *
 * Auth flow (localStorage)
 * ────────────────────────
 * 1. On load, checks localStorage["br_api_token"] for a saved token.
 * 2. Also checks ?token= URL param (set by manager's "Start client" / "View live" link).
 *    URL param wins over localStorage and is immediately saved.
 * 3. If neither exists, the login overlay is shown.
 * 4. Once signed in, all session operations use the stored token.
 * 5. "Sign out" closes the WebSocket but leaves the server session running.
 *    The session_id is kept in sessionStorage so re-logging in reconnects it.
 * 6. "Close" (was Disconnect) sends DELETE to the server and destroys the session.
 *
 * If ?session_id= is also present, the client auto-connects to that
 * existing session immediately after sign-in.
 */

"use strict";

// ── Storage keys ─────────────────────────────────────────────────────────────
const LS_TOKEN_KEY  = "br_api_token";
// sessionStorage: scoped to the current tab so each tab can hold its own
// session, and the value is automatically cleared when the tab is closed.
const SS_SESSION_KEY = "br_session_id";

// ── DOM references ────────────────────────────────────────────────────────────
const loginOverlay    = document.getElementById("loginOverlay");
const loginTokenInput = document.getElementById("loginTokenInput");
const loginError      = document.getElementById("loginError");
const authPillLabel   = document.getElementById("authPillLabel");
const urlInput        = document.getElementById("urlInput");
const widthInput      = document.getElementById("widthInput");
const heightInput     = document.getElementById("heightInput");
const dispWidthInput  = document.getElementById("dispWidthInput");
const dispHeightInput = document.getElementById("dispHeightInput");
const connectBtn      = document.getElementById("connectBtn");
const disconnectBtn   = document.getElementById("disconnectBtn");
const statusText      = document.getElementById("statusText");
const screen          = document.getElementById("screen");
const placeholder     = document.getElementById("placeholder");
const sendTypeBtn     = document.getElementById("sendTypeBtn");
const typeInput       = document.getElementById("typeInput");
const eventLog        = document.getElementById("eventLog");

// ── State ─────────────────────────────────────────────────────────────────────
let ws               = null;   // Active WebSocket
let sessionId        = null;   // Current session_id from the server
let apiToken         = "";     // Active API token (set after login)
let _disconnecting   = false;  // True while disconnect() is running (intentional)

// ── Auth – localStorage management ───────────────────────────────────────────

/**
 * Sign in with the given raw API token.
 * Stores it in localStorage and hides the login overlay.
 * Called from the overlay button (no arg) or programmatically (with arg).
 *
 * @param {string} [token] - Raw br_… token. Reads the overlay input if omitted.
 */
function clientLogin(token) {
  if (token === undefined) {
    token = loginTokenInput.value.trim();
  }
  if (!token) {
    loginError.textContent = "Please enter your API token.";
    return;
  }
  loginError.textContent = "";
  apiToken = token;
  localStorage.setItem(LS_TOKEN_KEY, token);
  authPillLabel.textContent = `Token: ${token.slice(0, 10)}…`;
  loginOverlay.classList.add("hidden");
}

/**
 * Sign out: detach the client from the server session without destroying it.
 * Closes the WebSocket but does NOT send DELETE — the browser keeps running.
 * The session_id is preserved in sessionStorage so that re-logging in with
 * the same token will automatically rejoin the session.
 */
async function clientLogout() {
  // Close the WebSocket quietly (no DELETE, no sessionStorage wipe).
  if (ws) { ws.close(); ws = null; }
  apiToken = "";
  localStorage.removeItem(LS_TOKEN_KEY);
  loginTokenInput.value  = "";
  loginError.textContent = "";
  setConnected(false);
  loginOverlay.classList.remove("hidden");
}

// ── Startup: restore credentials from localStorage / URL params ───────────────

(function init() {
  const params     = new URLSearchParams(location.search);
  const urlToken   = params.get("token");
  const savedToken = localStorage.getItem(LS_TOKEN_KEY);

  // URL param always wins (and overwrites localStorage so subsequent visits
  // remember this token too).
  const activeToken = urlToken || savedToken;

  // Pre-fill session controls from URL params (used by manager's "View →" link).
  if (params.get("url"))    urlInput.value    = params.get("url");
  if (params.get("width"))  { widthInput.value  = params.get("width");  dispWidthInput.value  = params.get("width");  }
  if (params.get("height")) { heightInput.value = params.get("height"); dispHeightInput.value = params.get("height"); }

  if (activeToken) {
    clientLogin(activeToken);

    // Priority 1: session_id from URL (?session_id= set by manager's "View →").
    // Priority 2: session_id saved in sessionStorage from a previous load.
    // Priority 3: nothing – show disconnected state so the user can start fresh.
    const preSession   = params.get("session_id");
    const savedSession = !preSession && sessionStorage.getItem(SS_SESSION_KEY);
    const targetSession = preSession || savedSession || null;
    if (targetSession) {
      setTimeout(() => connectToExisting(targetSession), 200);
    }
  } else {
    // No token found – reveal the login overlay.
    loginOverlay.classList.remove("hidden");
  }
})();

// ── Utilities ─────────────────────────────────────────────────────────────────

/**
 * Append a line to the event log panel.
 * @param {string} text   - Message to display.
 * @param {"sent"|"recv"|"error"|"info"} cls - CSS class for colouring.
 */
function log(text, cls = "info") {
  const el = document.createElement("div");
  el.className = cls;
  const ts = new Date().toLocaleTimeString();
  el.textContent = `[${ts}] ${text}`;
  eventLog.prepend(el);            // newest at top
  // Keep log short to avoid memory growth.
  while (eventLog.children.length > 200) {
    eventLog.removeChild(eventLog.lastChild);
  }
}

function setStatus(msg) {
  statusText.textContent = msg;
}

/** Send a JSON object over the WebSocket (if connected). */
function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
    log(JSON.stringify(obj), "sent");
  }
}

/** Set the UI into "connected" or "disconnected" state. */
function setConnected(connected) {
  connectBtn.disabled    =  connected;
  disconnectBtn.disabled = !connected;
  sendTypeBtn.disabled   = !connected;

  if (connected) {
    screen.classList.remove("hidden");
    placeholder.style.display = "none";
  } else {
    screen.classList.add("hidden");
    placeholder.style.display = "flex";
    screen.src = "";
    setStatus("Disconnected");
  }
}

// ── Session lifecycle ─────────────────────────────────────────────────────────

/**
 * Join an existing session identified by existingSessionId.
 * Called when launched from the manager's "View →" link (?session_id=…),
 * or when resuming a session saved in sessionStorage.
 * @param {string} existingSessionId
 */
async function connectToExisting(existingSessionId) {
  if (!apiToken) {
    log("Cannot auto-connect: no API token.", "error");
    return;
  }
  sessionId = existingSessionId;
  sessionStorage.setItem(SS_SESSION_KEY, sessionId);
  log(`Joining existing session: ${sessionId}`, "info");
  setStatus(`Joining session ${sessionId} – connecting WebSocket…`);
  // fallbackToNew=true: if the session has expired, automatically create a
  // fresh one rather than leaving the client in an error state.
  // applyDisplayOnOpen=false: session_info from the server is authoritative.
  await _openWebSocket(true, false);
}

/**
 * Create a new browser session via REST, then open a WebSocket for it.
 * If a session is already preserved in sessionStorage (e.g. from a
 * previous page load or unexpected disconnect), rejoin it instead of
 * spinning up a new browser instance.
 */
async function connect() {
  if (!apiToken) { await clientLogout(); return; }

  // Rejoin a preserved session before creating a new one.
  const preserved = sessionStorage.getItem(SS_SESSION_KEY);
  if (preserved) {
    log(`Rejoining preserved session: ${preserved}`, "info");
    await connectToExisting(preserved);
    return;
  }

  const raw    = urlInput.value.trim();
  const width  = parseInt(widthInput.value,  10);
  const height = parseInt(heightInput.value, 10);

  if (!raw) { alert("Please enter a URL."); return; }

  // Prepend http:// if no scheme is present so bare domains work.
  const url = /^https?:\/\//i.test(raw) ? raw : `http://${raw}`;
  urlInput.value = url;  // reflect normalised value back to the input

  setStatus("Creating session…");
  log(`Creating session → ${url} (${width}×${height})`, "info");

  let session;
  try {
    const resp = await fetch("/api/sessions", {
      method:  "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Token":  apiToken,
      },
      body: JSON.stringify({ url, width, height }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      // 401 = token revoked or invalid → force sign-out.
      if (resp.status === 401) {
        log("Token rejected by server – signing out.", "error");
        await clientLogout();
        return;
      }
      throw new Error(err.detail || resp.statusText);
    }
    session = await resp.json();
  } catch (err) {
    log(`Session creation failed: ${err.message}`, "error");
    setStatus(`Error: ${err.message}`);
    return;
  }

  sessionId = session.session_id;
  sessionStorage.setItem(SS_SESSION_KEY, sessionId);
  log(`Session created: ${sessionId}`, "info");
  setStatus(`Session ${sessionId} – connecting WebSocket…`);
  await _openWebSocket();
}

/**
 * Internal: open the WebSocket for the current sessionId / apiToken.
 * Shared by connect() and connectToExisting().
 *
 * @param {boolean} fallbackToNew       When true, a WebSocket-level rejection
 *   (session gone / expired) will automatically fall through to connect()
 *   to create a fresh session rather than showing an error.
 * @param {boolean} applyDisplayOnOpen  When true (new session), send the
 *   current display-resolution inputs to the server immediately on open.
 *   When false (existing session), the server's session_info message is
 *   authoritative and the inputs are populated from it, so no set_display
 *   is sent on open.
 */
async function _openWebSocket(fallbackToNew = false, applyDisplayOnOpen = true) {
  const wsProto = location.protocol === "https:" ? "wss" : "ws";
  const wsUrl   = `${wsProto}://${location.host}/ws/session/${sessionId}?token=${encodeURIComponent(apiToken)}`;
  ws = new WebSocket(wsUrl);
  ws.binaryType = "blob";

  // Track whether the connection was ever established so we can distinguish
  // a pre-open rejection (session gone) from a mid-stream error.
  let _everConnected = false;

  ws.onopen = () => {
    _everConnected = true;
    log("WebSocket connected.", "info");
    setStatus(`Streaming – session ${sessionId}`);
    setConnected(true);
    // Only push dimensions for brand-new sessions.  For existing sessions
    // the server is already running at the correct size and will send
    // session_info to sync all inputs.
    if (applyDisplayOnOpen) {
      _sendBrowserResize();
      _sendDisplayRes();
    }
  };

  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (msg.type === "screenshot") {
      screen.src = "data:image/jpeg;base64," + msg.data;
    } else if (msg.type === "session_info") {
      // Populate all session-related inputs from the server's live state.
      urlInput.value        = msg.url;
      widthInput.value      = msg.width;
      heightInput.value     = msg.height;
      dispWidthInput.value  = msg.display_width;
      dispHeightInput.value = msg.display_height;
      log(`Session info: ${msg.width}\xd7${msg.height} browser, ${msg.display_width}\xd7${msg.display_height} display, ${msg.url}`, "info");
    } else if (msg.type === "url") {
      // Keep the address bar in sync with the browser's current URL.
      urlInput.value = msg.url;
    } else if (msg.type === "error") {
      log(`Server error: ${msg.message}`, "error");
    } else {
      log(`recv: ${event.data}`, "recv");
    }
  };

  ws.onerror = () => {
    if (!_everConnected && !_disconnecting && fallbackToNew) {
      // The session no longer exists on the server (e.g. it timed out while
      // the page was reloaded).  Clear state and spin up a new session.
      log("Session not found – creating a new session.", "info");
      setStatus("Previous session gone – starting a new session…");
      sessionStorage.removeItem(SS_SESSION_KEY);
      sessionId = null;
      ws = null;
      connect();
      return;
    }
    log("WebSocket error.", "error");
    setStatus("WebSocket error – check console.");
    // Clear the preserved session on a WS-level error: the server likely
    // rejected the connection (bad session_id / expired ticket) rather than
    // this being a transient network issue, so reconnecting would fail again.
    if (!_disconnecting) {
      sessionStorage.removeItem(SS_SESSION_KEY);
      sessionId = null;
    }
  };

  ws.onclose = (ev) => {
    // Swallow the close that follows a pre-open rejection when we already
    // handled it in onerror and kicked off a new connect().
    if (!_everConnected && !_disconnecting && fallbackToNew) return;
    log(`WebSocket closed (code ${ev.code}).`, "info");
    setConnected(false);
    ws = null;
    if (_disconnecting) {
      // Intentional disconnect – wipe the preserved session.
      sessionStorage.removeItem(SS_SESSION_KEY);
      sessionId = null;
    } else {
      // Unexpected close (network drop, server restart, etc.).
      // Keep sessionId and sessionStorage so the next connect() or page
      // load can rejoin the same session instead of spawning a new browser.
      setStatus(`Disconnected – press Connect to rejoin session ${sessionId}`);
    }
  };
}

/**
 * Close: disconnect WebSocket and ask the server to destroy the session.
 * Sets `_disconnecting = true` so `ws.onclose` knows this is intentional
 * and clears the preserved session from sessionStorage.
 */
async function disconnect() {
  _disconnecting = true;
  if (ws) { ws.close(); ws = null; }

  sessionStorage.removeItem(SS_SESSION_KEY);

  if (sessionId) {
    try {
      await fetch(`/api/sessions/${sessionId}`, {
        method:  "DELETE",
        headers: { "X-API-Token": apiToken },
      });
      log(`Session ${sessionId} closed on server.`, "info");
    } catch {
      log("Failed to close session on server.", "error");
    }
    sessionId = null;
  }
  _disconnecting = false;
  setConnected(false);
}

// ── Mouse events ──────────────────────────────────────────────────────────────

screen.addEventListener("click", (e) => {
  if (!ws) return;
  const rect = screen.getBoundingClientRect();
  // Map client coordinates to browser viewport coordinates.
  const scaleX = parseInt(widthInput.value,  10) / rect.width;
  const scaleY = parseInt(heightInput.value, 10) / rect.height;
  const x = Math.round((e.clientX - rect.left)  * scaleX);
  const y = Math.round((e.clientY - rect.top)   * scaleY);

  const button = e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
  send({ type: "click", x, y, button });
});

screen.addEventListener("mousemove", throttle((e) => {
  if (!ws) return;
  const rect  = screen.getBoundingClientRect();
  const scaleX = parseInt(widthInput.value,  10) / rect.width;
  const scaleY = parseInt(heightInput.value, 10) / rect.height;
  const x = Math.round((e.clientX - rect.left)  * scaleX);
  const y = Math.round((e.clientY - rect.top)   * scaleY);
  send({ type: "move", x, y });
}, 50));

screen.addEventListener("wheel", (e) => {
  if (!ws) return;
  e.preventDefault();
  send({ type: "scroll", x: 0, y: 0, delta_x: e.deltaX, delta_y: e.deltaY });
}, { passive: false });

// Prevent the context menu on right-click so right-clicks can be forwarded.
screen.addEventListener("contextmenu", (e) => e.preventDefault());

// ── Keyboard events ───────────────────────────────────────────────────────────

document.addEventListener("keydown", (e) => {
  // Only forward keypresses when the screen image is "focused" (i.e. the user
  // is not typing in one of the panel inputs).
  if (!ws || document.activeElement !== document.body) return;
  e.preventDefault();
  send({ type: "keydown", key: e.key });
});

document.addEventListener("keyup", (e) => {
  if (!ws || document.activeElement !== document.body) return;
  e.preventDefault();
  send({ type: "keyup", key: e.key });
});

// ── Side panel actions ────────────────────────────────────────────────────────

sendTypeBtn.addEventListener("click", () => {
  const text = typeInput.value;
  if (!text) return;
  send({ type: "type", text });
  typeInput.value = "";
});

// ── URL address bar ───────────────────────────────────────────────────────────
// When connected, Enter in the URL bar navigates the browser instead of
// creating a new session.  When disconnected, Enter starts a new session.

urlInput.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  if (ws && ws.readyState === WebSocket.OPEN) {
    // Navigate the running session to the new URL.
    const raw = urlInput.value.trim();
    if (!raw) return;
    const url = /^https?:\/\//i.test(raw) ? raw : `http://${raw}`;
    urlInput.value = url;
    send({ type: "navigate", url });
    urlInput.blur();
  } else {
    // Not connected – start a new session.
    connect();
  }
});

// ── Toolbar buttons ───────────────────────────────────────────────────────────

connectBtn.addEventListener("click",    connect);
disconnectBtn.addEventListener("click", disconnect);

// ── Browser viewport resize ─────────────────────────────────────────────────

/**
 * Send the current browser viewport dimensions to the server via resize.
 * Called whenever the browser W/H inputs change while connected.
 */
function _sendBrowserResize() {
  const w = parseInt(widthInput.value,  10);
  const h = parseInt(heightInput.value, 10);
  if (!w || !h) return;
  send({ type: "resize", width: w, height: h });
  log(`Browser viewport resized to ${w}\xd7${h}`, "info");
}

// Live-update browser viewport while connected.
widthInput.addEventListener("change",  () => { if (ws && ws.readyState === WebSocket.OPEN) _sendBrowserResize(); });
heightInput.addEventListener("change", () => { if (ws && ws.readyState === WebSocket.OPEN) _sendBrowserResize(); });

// ── Display resolution ────────────────────────────────────────────────────────

/**
 * Send the current display resolution to the server via set_display.
 * Called immediately on WS open and whenever the display inputs change.
 */
function _sendDisplayRes() {
  const w = parseInt(dispWidthInput.value,  10);
  const h = parseInt(dispHeightInput.value, 10);
  if (!w || !h) return;
  send({ type: "set_display", width: w, height: h });
  log(`Display resolution set to ${w}×${h}`, "info");
}

// Live-update display resolution while connected.
dispWidthInput.addEventListener("change",  () => { if (ws && ws.readyState === WebSocket.OPEN) _sendDisplayRes(); });
dispHeightInput.addEventListener("change", () => { if (ws && ws.readyState === WebSocket.OPEN) _sendDisplayRes(); });

// ── Helper: throttle ─────────────────────────────────────────────────────────

function throttle(fn, limitMs) {
  let last = 0;
  return (...args) => {
    const now = Date.now();
    if (now - last >= limitMs) { last = now; fn(...args); }
  };
}
