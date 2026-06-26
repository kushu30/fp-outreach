// auth.js — FlexyPe authentication
// TOTP (RFC 6238), session, demo user store

const _API_BASE = localStorage.getItem("fp_api_url") || "http://localhost:8080";

/**
 * Verify email + password against the backend MongoDB user store.
 */
async function verifyCredentialsAPI(email, password) {
  try {
    const res = await fetch(`${_API_BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password })
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.ok ? data : null;
  } catch {
    return null;
  }
}

// ──────────────────────────────────────────────────────────────
// TOTP (RFC 6238) — pure browser crypto, no dependencies
// ──────────────────────────────────────────────────────────────

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
const TOTP_PERIOD = 30;
const TOTP_DIGITS = 6;

function generateSecret(length = 32) {
  const arr = new Uint8Array(length);
  crypto.getRandomValues(arr);
  let s = "";
  for (let i = 0; i < length; i++) s += BASE32[arr[i] % 32];
  return s;
}

function base32Decode(s) {
  s = s.toUpperCase().replace(/=+$/, "").replace(/\s+/g, "");
  let bits = 0,
    value = 0;
  const out = [];
  for (const c of s) {
    const idx = BASE32.indexOf(c);
    if (idx < 0) continue;
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return new Uint8Array(out);
}

async function computeTOTP(secret, timestamp = Date.now()) {
  const key = base32Decode(secret);
  const counter = Math.floor(timestamp / 1000 / TOTP_PERIOD);
  const buf = new ArrayBuffer(8);
  const view = new DataView(buf);
  view.setUint32(0, Math.floor(counter / 0x100000000));
  view.setUint32(4, counter >>> 0);

  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    key,
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, buf);
  const h = new Uint8Array(sig);
  const offset = h[h.length - 1] & 0x0f;
  const code =
    (((h[offset] & 0x7f) << 24) |
      ((h[offset + 1] & 0xff) << 16) |
      ((h[offset + 2] & 0xff) << 8) |
      (h[offset + 3] & 0xff)) %
    Math.pow(10, TOTP_DIGITS);
  return String(code).padStart(TOTP_DIGITS, "0");
}

async function verifyTOTP(secret, code) {
  if (!code || code.length !== TOTP_DIGITS) return false;
  const now = Date.now();
  for (const drift of [-1, 0, 1]) {
    const t = now + drift * TOTP_PERIOD * 1000;
    if ((await computeTOTP(secret, t)) === code) return true;
  }
  return false;
}

function totpRemaining() {
  return TOTP_PERIOD - (Math.floor(Date.now() / 1000) % TOTP_PERIOD);
}

function otpauthURI(secret, email, issuer = "FlexyPe") {
  const label = encodeURIComponent(`${issuer}:${email}`);
  return `otpauth://totp/${label}?secret=${secret}&issuer=${encodeURIComponent(issuer)}&algorithm=SHA1&digits=${TOTP_DIGITS}&period=${TOTP_PERIOD}`;
}

// ──────────────────────────────────────────────────────────────
// SESSION
// ──────────────────────────────────────────────────────────────

const SESSION_KEY = "fp_session";
const SESSION_TTL_MS = 8 * 60 * 60 * 1000;
const IDLE_TIMEOUT_MS = 60 * 60 * 1000;

function getSession() {
  try {
    const s = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
    if (!s) return null;
    if (Date.now() - s.startedAt > SESSION_TTL_MS) {
      clearSession();
      return null;
    }
    if (s.lastActivity && Date.now() - s.lastActivity > IDLE_TIMEOUT_MS) {
      clearSession();
      return null;
    }
    return s;
  } catch {
    return null;
  }
}
function createSession(user) {
  const s = {
    name: user.name,
    email: user.email,
    role: user.role,
    startedAt: Date.now(),
    lastActivity: Date.now()
  };
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
  return s;
}
function clearSession() {
  sessionStorage.removeItem(SESSION_KEY);
}

// Kept for compatibility
function verifyPassword(user, password) {
  return user && user.password === password;
}

// ──────────────────────────────────────────────────────────────
// UTILITIES
// ──────────────────────────────────────────────────────────────

function $(id) {
  return document.getElementById(id);
}

function toast(msg, type = "info") {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = `toast show toast-${type}`;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2400);
}

function setErr(inputId, errId, show, msg) {
  const inp = $(inputId);
  const err = errId ? $(errId) : null;
  if (inp) inp.classList.toggle("err", !!show);
  if (err) {
    err.classList.toggle("show", !!show);
    if (msg) err.textContent = msg;
  }
}

function validateEmail(e) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(e).trim());
}

// Expose globally for the page scripts
window.FP_AUTH = {
  verifyCredentialsAPI,
  generateSecret,
  base32Decode,
  computeTOTP,
  verifyTOTP,
  totpRemaining,
  otpauthURI,
  getSession,
  createSession,
  clearSession,
  verifyPassword,
  $,
  toast,
  setErr,
  validateEmail,
  TOTP_PERIOD,
  TOTP_DIGITS,
};

// ──────────────────────────────────────────────────────────────
// GMAIL PILL & OAUTH RETURN HANDLER
// ──────────────────────────────────────────────────────────────
(function () {
  let gmailStatus = { connected: false };
  const API_URL = localStorage.getItem("fp_api_url") || "http://localhost:8080";

  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function checkGmailStatus() {
    try {
      const email = window.FP_currentUser || "admin@flexype.in";
      const res = await fetch(`${API_URL}/api/auth/google/status`, {
        headers: { "X-User-Email": email }
      });
      if (res.ok) {
        gmailStatus = await res.json();
      } else {
        gmailStatus = { connected: false };
      }
    } catch {
      gmailStatus = { connected: false };
    }
  }

  async function startGmailConnect() {
    try {
      const email = window.FP_currentUser || "admin@flexype.in";
      const res = await fetch(`${API_URL}/api/auth/google/start`, {
        headers: { "X-User-Email": email }
      });
      if (res.ok) {
        const data = await res.json();
        if (data.authorize_url) {
          window.location.href = data.authorize_url;
        }
      }
    } catch (e) {
      window.FP_AUTH.toast("OAuth connection failed", "error");
    }
  }

  async function disconnectGmail() {
    if (!confirm("Disconnect Gmail? You'll need to re-authorize to send again.")) return;
    try {
      const email = window.FP_currentUser || "admin@flexype.in";
      const res = await fetch(`${API_URL}/api/auth/google/disconnect`, {
        method: "POST",
        headers: { "X-User-Email": email }
      });
      if (res.ok) {
        window.FP_AUTH.toast("Gmail disconnected");
        await checkGmailStatus();
        renderGmailPill();
      }
    } catch {
      window.FP_AUTH.toast("Failed to disconnect", "error");
    }
  }

  function renderGmailPill() {
    const pill = document.getElementById("gmailPill");
    if (!pill) return;
    if (gmailStatus.connected) {
      pill.className = "gmail-pill gmail-pill-connected";
      pill.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
        <span>${esc(gmailStatus.google_email || "connected")}</span>
        <span class="gmail-status-dot connected"></span>
      `;
      pill.title = `Connected as ${gmailStatus.google_name || gmailStatus.google_email}. Click to disconnect.`;
    } else {
      pill.className = "gmail-pill gmail-pill-disconnected";
      pill.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
        <span>Connect Gmail</span>
        <span class="gmail-status-dot disconnected"></span>
      `;
      pill.title = "Click to connect your Google account to send emails from this dashboard";
    }
  }

  function wireGmailPill() {
    document.getElementById("gmailPill")?.addEventListener("click", () => {
      if (gmailStatus.connected) disconnectGmail();
      else startGmailConnect();
    });
  }

  function injectGmailPill() {
    if (document.getElementById("gmailPill")) return;
    const topRight = document.querySelector(".topbar-right");
    if (!topRight) return;
    const pill = document.createElement("button");
    pill.id = "gmailPill";
    pill.className = "gmail-pill gmail-pill-disconnected";
    topRight.insertBefore(pill, topRight.firstChild);
    wireGmailPill();
    renderGmailPill();
  }

  async function handleOAuthReturn() {
    const params = new URLSearchParams(location.search);
    const g = params.get("gmail");
    if (!g) return;
    if (g === "connected") {
      window.FP_AUTH.toast("Gmail connected", "success");
    } else if (g === "error") {
      window.FP_AUTH.toast("Gmail connection failed: " + (params.get("reason") || "unknown"), "error");
    }
    history.replaceState({}, "", location.pathname + location.hash);
    await checkGmailStatus();
    renderGmailPill();
  }

  function initSidebarToggle() {
    const btn = document.getElementById("sidebarToggleBtn");
    if (!btn) return;
    const collapsed = localStorage.getItem("sidebar_collapsed") === "true";
    if (collapsed) {
      document.body.classList.add("sidebar-collapsed");
    }
    btn.addEventListener("click", () => {
      const isCollapsed = document.body.classList.toggle("sidebar-collapsed");
      localStorage.setItem("sidebar_collapsed", isCollapsed ? "true" : "false");
    });
  }

  // Expose helpers globally
  window.FP_AUTH.checkGmailStatus = checkGmailStatus;
  window.FP_AUTH.startGmailConnect = startGmailConnect;
  window.FP_AUTH.disconnectGmail = disconnectGmail;
  window.FP_AUTH.getGmailStatus = () => gmailStatus;

  document.addEventListener("DOMContentLoaded", async () => {
    initSidebarToggle();

    const topRight = document.querySelector(".topbar-right");
    if (!topRight) return;

    injectGmailPill();
    await checkGmailStatus();
    renderGmailPill();
    await handleOAuthReturn();
  });
})();