// guard.js — FlexyPe auth guard
// Drop into the dashboard's <head> BEFORE app.js
//
// Provides:
//   window.FP_currentUser   → email of signed-in user
//   window.FP_signOut()     → clears session + redirects to login
//   window.FP_extendSession() → reset idle timer (call on user activity)
//
// Behaviour:
//   - Redirects to login.html if no valid session
//   - 8-hour absolute session lifetime
//   - 60-minute idle timeout (resets on mouse/keyboard activity)
//   - Cross-tab sync: signing out in one tab signs out all tabs

(function () {
  const SESSION_KEY = "fp_session";
  const SESSION_TTL_MS = 8 * 60 * 60 * 1000; // 8h absolute
  const IDLE_TIMEOUT_MS = 60 * 60 * 1000; // 60min idle
  const LOGIN_PAGE = "login.html";

  function readSession() {
    try {
      const s = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
      if (!s || !s.email || !s.startedAt) return null;
      if (Date.now() - s.startedAt > SESSION_TTL_MS) return null;
      if (s.lastActivity && Date.now() - s.lastActivity > IDLE_TIMEOUT_MS)
        return null;
      return s;
    } catch {
      return null;
    }
  }

  function writeSession(s) {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
  }

  function clearAndRedirect(reason) {
    sessionStorage.removeItem(SESSION_KEY);
    // Signal other tabs via localStorage flag
    try {
      localStorage.setItem("fp_signout_signal", String(Date.now()));
      localStorage.removeItem("fp_signout_signal");
    } catch {}
    const url = reason ? `${LOGIN_PAGE}?reason=${reason}` : LOGIN_PAGE;
    window.location.replace(url);
  }

  // ── INITIAL CHECK ──────────────────────────
  const session = readSession();
  if (!session) {
    clearAndRedirect("expired");
    return;
  }

  // Mark activity now
  session.lastActivity = Date.now();
  writeSession(session);

  // ── PUBLIC API ─────────────────────────────
  window.FP_currentUser = session.email;
  window.FP_currentRole = session.role;
  window.FP_currentUserName = session.name;
  window.FP_sessionStarted = session.startedAt;
  
  window.FP_requireRole = (...roles) => {
    if (!roles.includes(window.FP_currentRole)) {
      window.location.replace("403.html");
    }
  };

  window.FP_signOut = function () {
    clearAndRedirect();
  };

  window.FP_extendSession = function () {
    const s = readSession();
    if (!s) return clearAndRedirect("expired");
    s.lastActivity = Date.now();
    writeSession(s);
  };

  // ── IDLE TIMER ─────────────────────────────
  // Reset activity timestamp on user interaction (throttled to once per 30s)
  let lastTouch = Date.now();
  function touch() {
    const now = Date.now();
    if (now - lastTouch < 30 * 1000) return;
    lastTouch = now;
    window.FP_extendSession();
  }

  ["mousemove", "keydown", "click", "scroll", "touchstart"].forEach((ev) =>
    window.addEventListener(ev, touch, { passive: true }),
  );

  // Periodic re-check (every 60s) — handles cases where the tab is left open
  setInterval(() => {
    if (!readSession()) clearAndRedirect("expired");
  }, 60 * 1000);

  // ── CROSS-TAB SIGN-OUT ─────────────────────
  window.addEventListener("storage", (e) => {
    if (e.key === "fp_signout_signal") {
      window.FP_signOut();
    }
  });

  // ── HIDE TABS BASED ON ROLES ─────────
  document.addEventListener("DOMContentLoaded", () => {
    const role = session.role;
    const usersNav = document.getElementById("usersNav");
    const supportNav = document.getElementById("supportNav");
    
    // Hide Admin Users Tab
    if (usersNav) {
      usersNav.style.display = role === "admin" ? "flex" : "none";
    }
    
    // Hide Support Dashboard from Sales
    if (supportNav) {
      supportNav.style.display = (role === "admin" || role === "supportteammember") ? "flex" : "none";
    }

    // Hide Sales Dashboards from Support
    if (role === "supportteammember") {
      const salesRoutes = ["dashboard", "watchlist", "outreach", "changes", "batch"];
      salesRoutes.forEach(r => {
        const el = document.querySelector(`[data-route="${r}"]`);
        if (el) el.style.display = "none";
      });
    }
  });
})();
