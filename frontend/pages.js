// pages.js — v4 — routing, watchlist page, auth UI, Gmail API send
// Loads AFTER app.js so it can read `leads`, `watchlist`, `selectedLead`

(function () {

  const API_URL = "http://localhost:8080";
  let selectedDomains = new Set();
  let gmailStatus = { connected: false };

  // ═══════════════════════════════════════════════════════════
  // 1. HELPERS
  // ═══════════════════════════════════════════════════════════

  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function getLeads() { return Array.isArray(window.leads) ? window.leads : []; }
  function getWatchlist() {
    if (window.watchlist instanceof Set) return window.watchlist;
    try { return new Set(JSON.parse(localStorage.getItem("fp_watchlist") || "[]")); }
    catch { return new Set(); }
  }

  function getCurrentUserEmail() {
    return window.FP_currentUser || "";
  }

  // All API calls include the current FlexyPe user for backend identification
  async function api(path, opts = {}) {
    const headers = Object.assign(
      { "X-User-Email": getCurrentUserEmail() },
      opts.headers || {}
    );
    if (opts.body && typeof opts.body === "object") {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(API_URL + path, { ...opts, headers });
  }

  function toast(msg, type = "info") {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.className = `toast show toast-${type}`;
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 2400);
  }

  function scoreClass(s) {
    if (s >= 80) return "sc-crit";
    if (s >= 65) return "sc-high";
    if (s >= 50) return "sc-med";
    return "sc-low";
  }
  function priChipClass(p) { return `pri-${(p || "low").toLowerCase()}`; }

  function getStatus(domain) {
    return localStorage.getItem(`fp_status_${domain}`) || "Not Contacted";
  }
  function getAddedTime(domain) {
    return parseInt(localStorage.getItem(`fp_wl_added_${domain}`) || "0", 10);
  }

  // ═══════════════════════════════════════════════════════════
  // 2. AUTH UI (user block + sign-out modal)
  // ═══════════════════════════════════════════════════════════

  const email = getCurrentUserEmail() || "user@flexype.in";
  document.getElementById("userEmail").textContent = email;
  document.getElementById("userAvatar").textContent = email[0].toUpperCase();

  const modal = document.getElementById("signOutModal");
  function openModal() { modal.style.display = "flex"; requestAnimationFrame(() => modal.classList.add("show")); }
  function closeModal() { modal.classList.remove("show"); setTimeout(() => (modal.style.display = "none"), 180); }

  document.getElementById("signOutBtn")?.addEventListener("click", openModal);
  document.getElementById("modalCancel")?.addEventListener("click", closeModal);
  modal?.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal?.classList.contains("show")) closeModal();
  });
  document.getElementById("modalConfirm")?.addEventListener("click", () => window.FP_signOut && window.FP_signOut());

  // ═══════════════════════════════════════════════════════════
  // 3. ROUTING
  // ═══════════════════════════════════════════════════════════

  const ROUTES = {
    dashboard: { title: "Dashboard", section: "page-dashboard" },
    watchlist: { title: "Watchlist", section: "page-watchlist" },
    outreach:  { title: "Outreach Log", section: "page-outreach" },
  };

  function currentRoute() {
    const h = (location.hash || "#dashboard").slice(1).split("?")[0];
    return ROUTES[h] ? h : "dashboard";
  }

  function setActiveNav(route) {
    document.querySelectorAll(".nav-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.route === route);
    });
  }

  function showRoute(route) {
    const r = ROUTES[route] || ROUTES.dashboard;
    document.querySelectorAll(".page-route").forEach((p) => (p.style.display = "none"));
    const sec = document.getElementById(r.section);
    if (sec) sec.style.display = "flex";
    document.getElementById("pageTitle").textContent = r.title;
    setActiveNav(route);
    if (route === "watchlist") renderWatchlistPage();
    if (route === "outreach") renderOutreachPage();
  }

  window.addEventListener("hashchange", () => showRoute(currentRoute()));
  document.querySelectorAll(".nav-item[data-route]").forEach((el) => {
    el.addEventListener("click", (e) => { e.preventDefault(); location.hash = "#" + el.dataset.route; });
  });

  // ═══════════════════════════════════════════════════════════
  // 4. GMAIL CONNECTION STATUS
  // ═══════════════════════════════════════════════════════════

  async function refreshGmailStatus() {
    try {
      const res = await api("/api/auth/google/status");
      if (res.ok) {
        gmailStatus = await res.json();
        renderGmailPill();
        return gmailStatus;
      }
    } catch (e) {
      console.warn("[Gmail] status check failed:", e);
    }
    gmailStatus = { connected: false };
    renderGmailPill();
    return gmailStatus;
  }

  function renderGmailPill() {
    const pill = document.getElementById("gmailPill");
    if (!pill) return;
    if (gmailStatus.connected) {
      pill.className = "gmail-pill gmail-pill-connected";
      pill.innerHTML = `
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="20 6 9 17 4 12"/></svg>
        Gmail · ${esc(gmailStatus.google_email || "connected")}
      `;
      pill.title = `Connected as ${gmailStatus.google_name || gmailStatus.google_email}. Click to disconnect.`;
    } else {
      pill.className = "gmail-pill gmail-pill-disconnected";
      pill.innerHTML = `
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
        Connect Gmail
      `;
      pill.title = "Click to connect your Google account to send emails from this dashboard";
    }
  }

  async function startGmailConnect() {
    try {
      const res = await api("/api/auth/google/start");
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast(err.error || "Failed to start Gmail connect", "error");
        return;
      }
      const { authorize_url } = await res.json();
      window.location.href = authorize_url;
    } catch (e) {
      toast("Backend unreachable", "error");
    }
  }

  async function disconnectGmail() {
    if (!confirm("Disconnect Gmail? You'll need to re-authorize to send again.")) return;
    try {
      const res = await api("/api/auth/google/disconnect", { method: "POST" });
      if (res.ok) {
        toast("Gmail disconnected");
        await refreshGmailStatus();
      }
    } catch {
      toast("Failed to disconnect", "error");
    }
  }

  // Wire up pill (will be added to DOM by the inject step below)
  function wireGmailPill() {
    document.getElementById("gmailPill")?.addEventListener("click", () => {
      if (gmailStatus.connected) disconnectGmail();
      else startGmailConnect();
    });
  }

  // Inject the pill into the topbar (before the export button)
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
  injectGmailPill();

  // Handle the post-OAuth redirect
  (function handleOAuthReturn() {
    const params = new URLSearchParams(location.search);
    const g = params.get("gmail");
    if (!g) return;
    if (g === "connected") toast("Gmail connected ✓", "success");
    else if (g === "error") toast("Gmail connection failed: " + (params.get("reason") || "unknown"), "error");
    // Clean URL
    history.replaceState({}, "", location.pathname + location.hash);
    refreshGmailStatus();
  })();

  // ═══════════════════════════════════════════════════════════
  // 5. EMAIL SEND (via Gmail API now, with mailto fallback)
  // ═══════════════════════════════════════════════════════════

  function getDisplayName() {
    if (gmailStatus.connected && gmailStatus.google_name) return gmailStatus.google_name;
    const e = getCurrentUserEmail();
    return e ? e.split("@")[0].replace(/[._]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "FlexyPe Team";
  }

  function getDefaultCC() {
    return localStorage.getItem("fp_default_cc") || "";
  }
  function setDefaultCC(cc) {
    if (cc) localStorage.setItem("fp_default_cc", cc);
    else localStorage.removeItem("fp_default_cc");
  }

  function fillTemplateVars(body) {
    return body
      .replace(/\[Your Name\]/g, getDisplayName())
      .replace(/\[\[YOUR_NAME\]\]/g, getDisplayName());
  }

  async function sendEmailViaGmail(lead, opts = {}) {
    if (!lead?.emails?.length) { toast("No email address for this merchant", "error"); return; }

    if (!gmailStatus.connected) {
      const ok = confirm("Gmail is not connected yet. Connect now?");
      if (ok) startGmailConnect();
      return;
    }

    const to      = opts.to      || lead.emails[0];
    const cc      = opts.cc      ?? getCCFieldValue();
    const subject = opts.subject || `FlexyPe × ${lead.domain} — Quick chat?`;
    const rawBody = opts.body    || document.getElementById("emailPreview")?.value || "";
    const body    = fillTemplateVars(rawBody);

    const btn = document.getElementById("sendGmailBtn");
    if (btn) { btn.disabled = true; btn.dataset.orig = btn.textContent; btn.textContent = "Sending…"; }

    try {
      const res = await api("/api/send-email", {
        method: "POST",
        body: { to, cc, subject, body, domain: lead.domain },
      });

      const data = await res.json().catch(() => ({}));

      if (res.ok && data.ok) {
        toast(`Email sent to ${to}`, "success");
        // Update local memory lead object with contacted details
        lead.status = "Contacted";
        lead.contacted_by = data.contacted_by;
        lead.contacted_at = data.contacted_at;
        lead.contacted_to = data.contacted_to;
        lead.contacted_subject = data.contacted_subject;

        localStorage.setItem(`fp_status_${lead.domain}`, "Contacted");

        // Refresh details panel if it's the active view
        if (window.selectedLead?.domain === lead.domain) {
          window.showLead(lead);
        }
        if (currentRoute() === "watchlist") renderWatchlistPage();
      } else if (data.code === "not_connected") {
        const ok = confirm("Gmail authorization expired. Reconnect now?");
        if (ok) startGmailConnect();
      } else {
        toast(data.error || "Send failed", "error");
      }
    } catch (e) {
      toast("Backend unreachable. Falling back to mail client…", "error");
      // Mailto fallback so the user can still send manually
      const mailto = `mailto:${encodeURIComponent(lead.emails[0])}?subject=${encodeURIComponent(`FlexyPe × ${lead.domain}`)}&body=${encodeURIComponent(fillTemplateVars(document.getElementById("emailPreview")?.value || ""))}`;
      window.location.href = mailto;
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = btn.dataset.orig || "Send via Gmail"; }
    }
  }

  function getCCFieldValue() {
    const el = document.getElementById("emailCC");
    if (el) return el.value.trim();
    return getDefaultCC();
  }

  // Inject CC field into the email block of the dashboard
  function injectCCField() {
    const tpl = document.getElementById("emailPreview");
    if (!tpl || document.getElementById("emailCC")) return;
    const wrap = document.createElement("div");
    wrap.className = "email-cc-row";
    wrap.innerHTML = `
      <input type="text" id="emailCC" placeholder="CC (comma-separated)" value="${esc(getDefaultCC())}">
      <button class="cc-save-btn" id="emailCCSaveBtn" title="Remember this CC for future emails">Save default</button>
    `;
    tpl.parentNode.insertBefore(wrap, tpl);
    document.getElementById("emailCCSaveBtn")?.addEventListener("click", () => {
      setDefaultCC(document.getElementById("emailCC").value.trim());
      toast("CC default saved");
    });
  }
  injectCCField();

  // Wire the send button
  document.getElementById("sendGmailBtn")?.addEventListener("click", () => {
    if (window.selectedLead) sendEmailViaGmail(window.selectedLead);
    else toast("Select a merchant first", "error");
  });

  // ═══════════════════════════════════════════════════════════
  // 6. WATCHLIST PAGE
  // ═══════════════════════════════════════════════════════════

  function renderWatchlistPage() {
    const leads = getLeads();
    const wl = getWatchlist();
    document.getElementById("navWatchlistCount").textContent = wl.size;

    let items = leads.filter((l) => wl.has(l.domain));
    const search = (document.getElementById("wlSearch")?.value || "").toLowerCase();
    const status = document.getElementById("wlStatusFilter")?.value || "";
    if (search) items = items.filter((l) => l.domain.toLowerCase().includes(search));
    if (status) items = items.filter((l) => getStatus(l.domain) === status);

    const sort = document.getElementById("wlSort")?.value || "score";
    if (sort === "domain") items.sort((a, b) => a.domain.localeCompare(b.domain));
    else if (sort === "status") items.sort((a, b) => getStatus(a.domain).localeCompare(getStatus(b.domain)));
    else if (sort === "added") items.sort((a, b) => getAddedTime(b.domain) - getAddedTime(a.domain));
    else items.sort((a, b) => b.lead_score - a.lead_score);

    document.getElementById("wlTotal").textContent = wl.size;
    document.getElementById("wlLive").textContent = items.filter((l) => l.live_checkout).length;
    document.getElementById("wlHot").textContent = items.filter((l) => l.lead_score >= 80).length;
    document.getElementById("wlContacted").textContent = items.filter((l) => getStatus(l.domain) !== "Not Contacted").length;

    const grid = document.getElementById("wlGrid");
    const empty = document.getElementById("wlEmpty");

    if (wl.size === 0) { grid.innerHTML = ""; empty.style.display = "flex"; return; }
    empty.style.display = "none";

    if (items.length === 0) {
      grid.innerHTML = '<div class="wl-empty-filter">No merchants match the current filters.</div>';
      return;
    }

    grid.innerHTML = items.map((l) => {
      const isSel = selectedDomains.has(l.domain);
      const s = getStatus(l.domain);
      const contactCount = (l.emails?.length || 0) + (l.phone_numbers?.length || 0) + (l.whatsapp_number ? 1 : 0);

      return `
        <div class="wl-card ${isSel ? "selected" : ""}" data-domain="${esc(l.domain)}">
          <div class="wl-card-checkbox" data-action="select">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
          <div class="wl-card-header">
            <div class="wl-card-domain" title="${esc(l.domain)}">${esc(l.domain)}</div>
            <span class="score-chip ${scoreClass(l.lead_score)}">${l.lead_score}</span>
          </div>
          <div class="wl-card-tags">
            ${l.live_checkout ? `<span class="live-chip">${esc(l.live_checkout)}</span>` : ""}
            ${l.has_kwikpass ? '<span class="badge badge-tech" style="background:rgba(251,191,36,0.1);color:#fbbf24;border-color:rgba(251,191,36,0.2)">Kwikpass</span>' : ""}
            <span class="pri-chip ${priChipClass(l.priority)}">${esc(l.priority)}</span>
          </div>
          <div class="wl-card-meta">
            <div class="wl-meta-row"><span class="wl-meta-key">Status</span><span class="wl-meta-val wl-status-${s.replace(/\s+/g, "-").toLowerCase()}">${esc(s)}</span></div>
            <div class="wl-meta-row"><span class="wl-meta-key">Contacts</span><span class="wl-meta-val">${contactCount > 0 ? `${contactCount} channel${contactCount > 1 ? "s" : ""}` : "none"}</span></div>
            <div class="wl-meta-row"><span class="wl-meta-key">Scanned</span><span class="wl-meta-val">${esc(l.last_scan || "—")}</span></div>
          </div>
          <div class="wl-card-actions">
            <button class="wl-card-btn" data-action="open" title="Open in dashboard"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>View</button>
            <button class="wl-card-btn" data-action="visit" title="Visit website"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></button>
            ${l.emails?.length ? `<button class="wl-card-btn" data-action="email" title="Send email via Gmail"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg></button>` : ""}
            ${l.whatsapp_link ? `<a class="wl-card-btn" data-action="wa" href="${esc(l.whatsapp_link)}" target="_blank" rel="noopener" title="WhatsApp" onclick="event.stopPropagation()"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg></a>` : ""}
            <button class="wl-card-btn wl-card-remove" data-action="remove" title="Remove from watchlist"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
        </div>
      `;
    }).join("");

    wireCards();
    updateBulkBar();
  }

  function wireCards() {
    document.querySelectorAll(".wl-card").forEach((card) => {
      const domain = card.dataset.domain;
      card.querySelectorAll("[data-action]").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          handleCardAction(btn.dataset.action, domain);
        });
      });
      card.addEventListener("click", (e) => {
        if (e.target.closest("[data-action]") || e.target.closest("a")) return;
        handleCardAction("open", domain);
      });
    });
  }

  function handleCardAction(action, domain) {
    const lead = getLeads().find((l) => l.domain === domain);
    if (!lead) return;
    switch (action) {
      case "select":
        if (selectedDomains.has(domain)) selectedDomains.delete(domain);
        else selectedDomains.add(domain);
        renderWatchlistPage();
        break;
      case "open":
        location.hash = "#dashboard";
        setTimeout(() => {
          if (typeof window.showLead === "function") window.showLead(lead);
          document.querySelectorAll("#leadTable tbody tr").forEach((r) => r.classList.remove("active"));
          const row = document.querySelector(`#leadTable tbody tr[data-domain="${domain}"]`);
          if (row) { row.classList.add("active"); row.scrollIntoView({ block: "nearest" }); }
        }, 80);
        break;
      case "visit": window.open(`https://${domain}`, "_blank", "noopener,noreferrer"); break;
      case "email": sendEmailViaGmail(lead); break;
      case "remove":
        if (window.watchlist instanceof Set) window.watchlist.delete(domain);
        else {
          const wl = getWatchlist(); wl.delete(domain);
          localStorage.setItem("fp_watchlist", JSON.stringify([...wl]));
        }
        localStorage.removeItem(`fp_wl_added_${domain}`);
        selectedDomains.delete(domain);
        if (typeof window.saveWatchlist === "function") window.saveWatchlist();
        if (typeof window.renderTable === "function") window.renderTable(getLeads());
        renderWatchlistPage();
        toast(`Removed ${domain}`);
        break;
    }
  }

  // ═══════════════════════════════════════════════════════════
  // 7. BULK ACTIONS
  // ═══════════════════════════════════════════════════════════

  function updateBulkBar() {
    const bar = document.getElementById("wlBulkBar");
    const count = selectedDomains.size;
    bar.style.display = count > 0 ? "flex" : "none";
    document.getElementById("wlBulkCount").textContent = `${count} selected`;
  }

  document.getElementById("wlBulkClear")?.addEventListener("click", () => {
    selectedDomains.clear(); renderWatchlistPage();
  });
  document.getElementById("wlBulkOpen")?.addEventListener("click", () => {
    if (selectedDomains.size > 8 && !confirm(`Open ${selectedDomains.size} tabs?`)) return;
    [...selectedDomains].forEach((d) => window.open(`https://${d}`, "_blank", "noopener,noreferrer"));
  });
  document.getElementById("wlBulkRemove")?.addEventListener("click", () => {
    if (!confirm(`Remove ${selectedDomains.size} merchants from watchlist?`)) return;
    [...selectedDomains].forEach((d) => {
      if (window.watchlist instanceof Set) window.watchlist.delete(d);
      localStorage.removeItem(`fp_wl_added_${d}`);
    });
    if (typeof window.saveWatchlist === "function") window.saveWatchlist();
    if (typeof window.renderTable === "function") window.renderTable(getLeads());
    selectedDomains.clear();
    renderWatchlistPage();
    toast("Removed");
  });
  document.getElementById("wlBulkExport")?.addEventListener("click", () => {
    const selected = getLeads().filter((l) => selectedDomains.has(l.domain));
    if (!selected.length) return;
    const headers = ["domain", "lead_score", "priority", "live_checkout", "emails", "phones", "whatsapp", "status"];
    const rows = selected.map((l) => [
      l.domain, l.lead_score, l.priority, l.live_checkout || "",
      (l.emails || []).join("; "), (l.phone_numbers || []).join("; "),
      l.whatsapp_number || "", getStatus(l.domain),
    ]);
    const csv = [headers, ...rows].map((r) => r.map((v) => JSON.stringify(v ?? "")).join(",")).join("\n");
    const a = Object.assign(document.createElement("a"), {
      href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })),
      download: `watchlist-${new Date().toISOString().slice(0, 10)}.csv`,
    });
    a.click();
    URL.revokeObjectURL(a.href);
    toast("Exported");
  });

  // ═══════════════════════════════════════════════════════════
  // 8. FILTER WIRING
  // ═══════════════════════════════════════════════════════════

  ["wlSearch", "wlSort", "wlStatusFilter"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", renderWatchlistPage);
    el.addEventListener("change", renderWatchlistPage);
  });

  document.getElementById("outreachSearch")?.addEventListener("input", filterAndRenderOutreach);

  // ═══════════════════════════════════════════════════════════
  // 8b. OUTREACH PAGE LOGIC
  // ═══════════════════════════════════════════════════════════

  let outreachLogs = [];

  async function renderOutreachPage() {
    const tbody = document.getElementById("outreachTableBody");
    const empty = document.getElementById("outreachEmpty");
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--t3);padding:40px;">Loading outreach logs…</td></tr>';
    if (empty) empty.style.display = "none";
    document.getElementById("outreachTotal").textContent = "…";

    try {
      const res = await api("/api/sent-log");
      if (res.ok) {
        outreachLogs = await res.json();
      } else {
        outreachLogs = [];
      }
    } catch (e) {
      console.warn("[Outreach] failed to fetch logs:", e);
      outreachLogs = [];
    }

    filterAndRenderOutreach();
  }

  function filterAndRenderOutreach() {
    const tbody = document.getElementById("outreachTableBody");
    const empty = document.getElementById("outreachEmpty");
    if (!tbody) return;

    const search = (document.getElementById("outreachSearch")?.value || "").toLowerCase();
    
    let items = outreachLogs;
    if (search) {
      items = items.filter(l => 
        (l.domain || "").toLowerCase().includes(search) || 
        (l.to || "").toLowerCase().includes(search) ||
        (l.subject || "").toLowerCase().includes(search)
      );
    }

    document.getElementById("outreachTotal").textContent = outreachLogs.length;

    if (items.length === 0) {
      tbody.innerHTML = "";
      if (empty) empty.style.display = "flex";
      return;
    }

    if (empty) empty.style.display = "none";

    tbody.innerHTML = items.map((l) => {
      const dateStr = l.sent_at ? new Date(l.sent_at).toLocaleString() : "—";
      return `
        <tr>
          <td><strong style="color:var(--t1);cursor:pointer;" class="outreach-domain-click" data-domain="${esc(l.domain)}">${esc(l.domain || "—")}</strong></td>
          <td><a href="mailto:${esc(l.to)}" style="color:var(--t2);text-decoration:none;">${esc(l.to)}</a></td>
          <td style="color:var(--t2);max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(l.subject)}">${esc(l.subject)}</td>
          <td style="color:var(--t3);font-size:11px;">${esc(l.flexype_user)}</td>
          <td style="color:var(--t3);font-size:11px;">${esc(dateStr)}</td>
        </tr>
      `;
    }).join("");

    tbody.querySelectorAll(".outreach-domain-click").forEach((el) => {
      el.addEventListener("click", () => {
        const domain = el.dataset.domain;
        if (!domain) return;
        const lead = getLeads().find((l) => l.domain === domain);
        location.hash = "#dashboard";
        setTimeout(() => {
          if (lead) {
            if (typeof window.showLead === "function") window.showLead(lead);
            document.querySelectorAll("#leadTable tbody tr").forEach((r) => r.classList.remove("active"));
            const row = document.querySelector(`#leadTable tbody tr[data-domain="${domain}"]`);
            if (row) { row.classList.add("active"); row.scrollIntoView({ block: "nearest" }); }
          }
        }, 80);
      });
    });
  }

  // ═══════════════════════════════════════════════════════════
  // 9. WATCHLIST TIMESTAMP HOOK
  // ═══════════════════════════════════════════════════════════

  const origSet = Storage.prototype.setItem;
  Storage.prototype.setItem = function (k, v) {
    origSet.apply(this, arguments);
    if (k === "fp_watchlist") {
      try {
        const arr = JSON.parse(v);
        arr.forEach((d) => {
          if (!localStorage.getItem(`fp_wl_added_${d}`)) {
            origSet.call(localStorage, `fp_wl_added_${d}`, String(Date.now()));
          }
        });
      } catch {}
      const c = document.getElementById("navWatchlistCount");
      try { if (c) c.textContent = JSON.parse(v).length; } catch {}
      if (currentRoute() === "watchlist") renderWatchlistPage();
    }
  };

  // ═══════════════════════════════════════════════════════════
  // 10. BOOT
  // ═══════════════════════════════════════════════════════════

  setTimeout(() => {
    if (!location.hash) location.hash = "#dashboard";
    showRoute(currentRoute());
    document.getElementById("navWatchlistCount").textContent = getWatchlist().size;
    refreshGmailStatus();
  }, 100);

  setInterval(() => {
    if (currentRoute() === "watchlist" && document.visibilityState === "visible") {
      renderWatchlistPage();
    }
  }, 5000);

})();