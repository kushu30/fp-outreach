// auth.js — FlexyPe authentication
// TOTP (RFC 6238), session, demo user store

// ──────────────────────────────────────────────────────────────
// DEMO USER STORE
// Replace with API calls to your backend in production.
// Format: { email: { passwordHash, secret (Base32), twoFAEnabled } }
// ──────────────────────────────────────────────────────────────

const DEFAULT_USERS = {
  "admin@flexype.in": {
    // pw: admin@flexy2026
    passwordHash: "8c6976e5b5410415bde908bd4dee15dfb16751b2", // placeholder — see verifyPassword
    password: "admin@flexy2026",
    secret: "JBSWY3DPEHPK3PXPMFXFGZJWMRSXG5BR",
    twoFAEnabled: true,
  },
  "om@flexype.in": {
    password: "flexype2026",
    secret: "MFRGGZDFMZTWQ2LKNNSXS5BRGIYTONJV",
    twoFAEnabled: true,
  },
    "kushu@flexype.in": {
    password: "kushu2026",
    secret: "MFRGGZDFMZTWQ2LKNNSXS5BRGIYTONJV",
    twoFAEnabled: true,
  },
};

function loadUsers() {
  try {
    const stored = JSON.parse(localStorage.getItem("fp_users") || "null");
    if (stored && typeof stored === "object") return stored;
  } catch {}
  localStorage.setItem("fp_users", JSON.stringify(DEFAULT_USERS));
  return DEFAULT_USERS;
}
function saveUsers(users) {
  localStorage.setItem("fp_users", JSON.stringify(users));
}

// ──────────────────────────────────────────────────────────────
// TOTP (RFC 6238) — pure browser crypto, no dependencies
// ──────────────────────────────────────────────────────────────

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
const TOTP_PERIOD = 30; // seconds
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

// Verify with ±1 step drift tolerance (90 sec window)
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
const SESSION_TTL_MS = 8 * 60 * 60 * 1000; // 8 hours

function getSession() {
  try {
    const s = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
    if (!s) return null;
    if (Date.now() - s.startedAt > SESSION_TTL_MS) {
      clearSession();
      return null;
    }
    return s;
  } catch {
    return null;
  }
}
function createSession(email) {
  const s = { email, startedAt: Date.now() };
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
  return s;
}
function clearSession() {
  sessionStorage.removeItem(SESSION_KEY);
}

// ──────────────────────────────────────────────────────────────
// PASSWORD CHECK (demo — replace with backend call)
// ──────────────────────────────────────────────────────────────

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
  loadUsers,
  saveUsers,
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
