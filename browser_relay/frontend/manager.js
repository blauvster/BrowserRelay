/**
 * BrowserRelay – Manager (manager.js)
 * ─────────────────────────────────────────────────────────────────────────────
 * Client-side logic for the admin manager page.
 *
 * Responsibilities:
 *  - Token management: list, create, revoke, rotate.
 *  - Session management: list all, force-close, open in client viewer.
 *  - Auto-refresh of the sessions table.
 *  - Toast notifications and status-pill feedback.
 */

"use strict";

// ── Constants ─────────────────────────────────────────────────────────────────
const LS_ADMIN_KEY = "br_admin_secret";

const API = {
  tokens:      "/api/tokens",
  token:       (id) => `/api/tokens/${id}`,
  tokenDelete: (id) => `/api/tokens/${id}/delete`,
  tokenRotate: (id) => `/api/tokens/${id}/rotate`,
  tokenTicket: (id) => `/api/tokens/${id}/ticket`,
  sessions:    "/api/sessions",
  sessionForce:(id) => `/api/sessions/${id}/force`,
};

// ── State ─────────────────────────────────────────────────────────────────────
let _autoRefreshTimer = null;   // setInterval handle for session auto-refresh
let _toastTimer       = null;   // clearTimeout handle for toast hide
let adminSecret       = "";    // in-memory copy of persisted admin secret

// ── DOM helpers ───────────────────────────────────────────────────────────────

const adminSecretInput  = () => adminSecret;

function setStatusPill(ok, message) {
  const el = document.getElementById("statusPill");
  el.className = ok ? "ok" : "error";
  el.textContent = message;
}

/**
 * Show a toast notification.
 * @param {string} message
 * @param {"ok"|"error"|""} type
 * @param {number} duration  ms to display (default 3500)
 */
function toast(message, type = "", duration = 3500) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className   = "show" + (type ? " " + type : "");
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = ""; }, duration);
}

/**
 * Format an ISO datetime string to a locale short form.
 * Returns "–" for falsy values.
 */
function fmtDate(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** Truncate a string if it exceeds maxLen characters. */
function trunc(str, maxLen = 48) {
  if (!str) return "";
  return str.length > maxLen ? str.slice(0, maxLen) + "…" : str;
}

// ── Auth ─────────────────────────────────────────────────────────────────────

/**
 * Called when the user submits the login overlay.
 * Validates the secret against the server; on success persists to localStorage.
 */
async function mgLogin() {
  const secret = document.getElementById("mgSecretInput").value.trim();
  const errEl  = document.getElementById("mgLoginError");
  errEl.textContent = "";

  if (!secret) {
    errEl.textContent = "Admin secret is required.";
    return;
  }

  // Temporarily set module-level secret so apiFetch can use it.
  adminSecret = secret;

  try {
    // Use a lightweight read to verify the credential.
    await apiFetch(API.tokens);
  } catch (err) {
    adminSecret = "";
    errEl.textContent = err.message.includes("401") || err.message.includes("403")
      ? "Invalid admin secret – check your configuration."
      : err.message;
    return;
  }

  // Credential is valid – persist and show app.
  localStorage.setItem(LS_ADMIN_KEY, secret);
  _applyLoggedIn(secret);
}

/** Sign out and return to the login overlay. */
function mgLogout() {
  localStorage.removeItem(LS_ADMIN_KEY);
  adminSecret = "";

  // Clear tables.
  document.getElementById("tokensBody").innerHTML = "";
  document.getElementById("sessionsBody").innerHTML = "";
  document.getElementById("badge-tokens").textContent = "0";
  document.getElementById("badge-sessions").textContent = "0";

  // Hide auth pill and show login overlay.
  document.getElementById("authPill").style.display = "none";
  document.getElementById("loginOverlay").style.display = "flex";
  document.getElementById("mgSecretInput").value = "";
  document.getElementById("mgLoginError").textContent = "";

  setStatusPill(false, "Signed out");
}

/** Apply logged-in UI state (shared by mgLogin and init). */
function _applyLoggedIn(secret) {
  adminSecret = secret;

  // Update auth pill label and show it.
  const pill = document.getElementById("authPill");
  document.getElementById("authPillLabel").textContent = secret.slice(0, 6) + "\u2026";
  pill.style.display = "flex";

  // Hide login overlay.
  document.getElementById("loginOverlay").style.display = "none";

  // Load data on login.
  loadTokens();
}

// ── API wrapper ───────────────────────────────────────────────────────────────

/**
 * Fetch wrapper that injects X-Admin-Secret and handles non-2xx errors.
 *
 * @param {string} url
 * @param {RequestInit} opts
 * @returns {Promise<any>}  Parsed JSON response body.
 * @throws {Error} with a descriptive message on failure.
 */
async function apiFetch(url, opts = {}) {
  const secret = adminSecretInput();
  if (!secret) throw new Error("Admin secret is required.");

  const headers = {
    "X-Admin-Secret": secret,
    ...(opts.body ? { "Content-Type": "application/json" } : {}),
    ...(opts.headers || {}),
  };

  const resp = await fetch(url, { ...opts, headers });

  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(`${resp.status} – ${detail}`);
  }

  // 204 No Content
  if (resp.status === 204) return null;
  return resp.json();
}

// ── Token management ──────────────────────────────────────────────────────────

async function loadTokens() {
  const secret = adminSecretInput();
  if (!secret) {
    setStatusPill(false, "No admin secret");
    return;
  }

  let tokens, sessions;
  try {
    [tokens, sessions] = await Promise.all([
      apiFetch(API.tokens),
      apiFetch(API.sessions),
    ]);
    setStatusPill(true, "Authenticated ✓");
  } catch (err) {
    setStatusPill(false, err.message);
    toast(err.message, "error");
    renderTokensEmpty(`Failed to load: ${err.message}`);
    return;
  }

  // Build token_id → first active session map for quick lookup.
  const sessionByToken = new Map();
  for (const s of sessions) {
    if (s.token_id && !sessionByToken.has(s.token_id)) {
      sessionByToken.set(s.token_id, s);
    }
  }

  // Update stat chip: "N tokens · N live"
  const liveCount = sessionByToken.size;
  const statEl = document.getElementById("tokensStat");
  if (statEl) {
    statEl.innerHTML = `<span class="live-dot"></span>${tokens.length} token${tokens.length !== 1 ? 's' : ''} &middot; ${liveCount} live`;
  }

  const tbody = document.getElementById("tokensBody");
  if (!tokens.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">No tokens yet – create one above.</td></tr>`;
    return;
  }

  tbody.innerHTML = tokens.map((t) => {
    const liveSession = sessionByToken.get(t.token_id);
    const openBtn = !t.revoked
      ? liveSession
        ? `<button class="btn btn-cyan btn-xs"
              onclick="openClientWithTicket('${esc(t.token_id)}', '${esc(liveSession.session_id)}', ${liveSession.width}, ${liveSession.height})"
              title="${esc(liveSession.current_url || liveSession.url)}">View live →</button>`
        : `<button class="btn btn-accent btn-xs"
              onclick="openClientWithTicket('${esc(t.token_id)}')"
              title="Open client pre-authenticated as this token">Start client</button>`
      : "";

    const sessionIdCell = liveSession
      ? `<span class="mono" style="font-size:.75rem;color:var(--muted)">${esc(liveSession.session_id)}</span>`
      : `<span style="color:var(--muted)">–</span>`;

    const currentUrl = liveSession ? (liveSession.current_url || liveSession.url) : "";
    const urlCell = liveSession
      ? `<span title="${esc(currentUrl)}">${esc(trunc(currentUrl, 38))}</span>`
      : `<span style="color:var(--muted)">–</span>`;

    return `
    <tr data-id="${esc(t.token_id)}">
      <td><strong>${esc(t.label)}</strong></td>
      <td class="mono">${esc(t.token_id)}</td>
      <td class="mono">${esc(t.token_prefix + "…")}</td>
      <td>${fmtDate(t.created_at)}</td>
      <td>${fmtDate(t.last_used_at)}</td>
      <td>
        ${t.revoked
          ? `<span class="tag tag-red">Revoked</span>`
          : liveSession
            ? `<span class="tag tag-green">Active · live</span>`
            : `<span class="tag tag-green">Active</span>`}
      </td>
      <td>${sessionIdCell}</td>
      <td>${urlCell}</td>
      <td>
        <div class="actions">
          ${openBtn}
          ${liveSession ? `<button class="btn btn-danger btn-xs" onclick="forceCloseSession('${esc(liveSession.session_id)}')">Force Close</button>` : ""}
          ${!t.revoked ? `
            <button class="btn btn-amber btn-xs" onclick="rotateToken('${esc(t.token_id)}')">Rotate</button>
            <button class="btn btn-danger btn-xs" onclick="revokeToken('${esc(t.token_id)}')">Revoke</button>
          ` : `
            <button class="btn btn-danger btn-xs" onclick="deleteToken('${esc(t.token_id)}')" >Delete</button>
          `}
        </div>
      </td>
    </tr>
  `;
  }).join("");
}

function renderTokensEmpty(msg) {
  document.getElementById("tokensBody").innerHTML =
    `<tr><td colspan="9" class="empty">${esc(msg)}</td></tr>`;
}

async function createToken() {
  const label = document.getElementById("newTokenLabel").value.trim();
  if (!label) { toast("Enter a label for the new token.", "error"); return; }

  // Hide any previous reveal
  const revealBox = document.getElementById("tokenReveal");
  revealBox.classList.remove("visible");

  let result;
  try {
    result = await apiFetch(API.tokens, {
      method: "POST",
      body: JSON.stringify({ label }),
    });
  } catch (err) {
    toast(err.message, "error");
    return;
  }

  document.getElementById("newTokenLabel").value = "";
  showTokenReveal(result.token, `Created: ${result.label} (${result.token_id})`);
  toast("Token created successfully.", "ok");
  await loadTokens();
}

async function revokeToken(tokenId) {
  if (!confirm(`Revoke token ${tokenId}?\n\nAll API calls using this token will immediately fail.`)) return;

  try {
    await apiFetch(API.token(tokenId), { method: "DELETE" });
    toast(`Token ${tokenId} revoked.`, "ok");
    await loadTokens();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function rotateToken(tokenId) {
  if (!confirm(`Rotate token ${tokenId}?\n\nThe old value will stop working immediately. The new value will be shown once.`)) return;

  let result;
  try {
    result = await apiFetch(API.tokenRotate(tokenId), { method: "POST" });
  } catch (err) {
    toast(err.message, "error");
    return;
  }

  showTokenReveal(result.token, `Rotated: ${result.label} (${result.token_id})`);
  toast("Token rotated – copy the new value now!", "ok", 6000);
  await loadTokens();
}

async function deleteToken(tokenId) {
  if (!confirm(`Permanently delete token ${tokenId}?\n\nThis cannot be undone.`)) return;

  try {
    await apiFetch(API.tokenDelete(tokenId), { method: "POST" });
    toast(`Token ${tokenId} deleted.`, "ok");
    await loadTokens();
  } catch (err) {
    toast(err.message, "error");
  }
}

/**
 * Open the client viewer for *tokenId*, pre-authenticated via a server-issued
 * short-lived ticket.  No raw token is ever exposed in the URL or localStorage.
 *
 * @param {string} tokenId     - Token ID to impersonate.
 * @param {string} [sessionId] - Optional: jump straight into this session.
 * @param {number} [width]     - Session viewport width (pixels); forwarded so
 *                               the client can scale mouse coordinates correctly.
 * @param {number} [height]    - Session viewport height (pixels).
 */
async function openClientWithTicket(tokenId, sessionId, width, height) {
  let ticket;
  try {
    const res = await apiFetch(API.tokenTicket(tokenId), { method: "POST" });
    ticket = res.ticket;
  } catch (err) {
    toast(`Could not issue ticket: ${err.message}`, "error");
    return;
  }

  const params = new URLSearchParams({ token: ticket });
  if (sessionId) params.set("session_id", sessionId);
  if (width)     params.set("width",      width);
  if (height)    params.set("height",     height);
  window.open(`/index.html?${params.toString()}`, "_blank", "noopener");
}

/**
 * Display the token reveal box with the raw token value.
 * The raw value is shown only on creation / rotation.
 *
 * @param {string} rawToken   - Raw token string (br_…).
 * @param {string} headerText - Label shown above the value.
 */
function showTokenReveal(rawToken, headerText) {
  const box   = document.getElementById("tokenReveal");
  const label = box.querySelector(".label");
  const value = document.getElementById("tokenRevealValue");

  label.textContent = `⚠ ${headerText} – copy this token now, it will not be shown again!`;
  value.textContent = rawToken;
  box.classList.add("visible");

  // Scroll reveal into view
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ── Session management ────────────────────────────────────────────────────────

async function forceCloseSession(sessionId) {
  if (!confirm(`Force-close session ${sessionId}?\n\nThe browser will be closed immediately.`)) return;

  try {
    await apiFetch(API.sessionForce(sessionId), { method: "DELETE" });
    toast(`Session ${sessionId} closed.`, "ok");
    await loadTokens();
  } catch (err) {
    toast(err.message, "error");
  }
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────

function setAutoRefresh(seconds) {
  if (_autoRefreshTimer) {
    clearInterval(_autoRefreshTimer);
    _autoRefreshTimer = null;
  }
  const n = parseInt(seconds, 10);
  if (n > 0) {
    _autoRefreshTimer = setInterval(loadTokens, n * 1000);
  }
}

// ── Utility ───────────────────────────────────────────────────────────────────

/**
 * HTML-escape a string to prevent XSS when inserting into innerHTML.
 * @param {any} value
 * @returns {string}
 */
function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ── Initialisation ────────────────────────────────────────────────────────────

(function init() {
  const stored = localStorage.getItem(LS_ADMIN_KEY);
  if (stored) {
    _applyLoggedIn(stored);
  } else {
    // Show the login overlay; hide auth pill.
    document.getElementById("loginOverlay").style.display = "flex";
    document.getElementById("authPill").style.display = "none";
  }

  // Allow pressing Enter in the secret field to submit login.
  document.getElementById("mgSecretInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") mgLogin();
  });
})();

// Start default auto-refresh (10 s as per option default).
setAutoRefresh(10);
