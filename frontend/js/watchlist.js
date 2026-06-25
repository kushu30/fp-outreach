// watchlist.js — standalone Watchlist page logic
(function () {
  const API_URL = localStorage.getItem("fp_api_url") || "http://localhost:8080";
  let leads = [];
  let watchlist = new Set();
  let selectedDomains = new Set();

  // ─── Utilities ──────────────────────────────────────────────────────────────
  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function getSessionUser() {
    return window.FP_currentUser || "admin@flexype.in";
  }
  async function api(path, opts = {}) {
    const headers = Object.assign(
      { "X-User-Email": getSessionUser() },
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

  function showLoader(show = true, text = "Loading…") {
    const el = document.getElementById("loaderOverlay");
    if (el) el.style.display = show ? "flex" : "none";
    const lt = document.getElementById("loaderText");
    if (lt && text) lt.textContent = text;
  }

  function setProgress(pct, status) {
    const bar = document.getElementById("loaderProgressBar");
    const sub = document.getElementById("loaderStatus");
    if (bar) bar.style.width = `${pct}%`;
    if (sub && status) sub.textContent = status;
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

  // ─── Auth / Session Initialization ──────────────────────────────────────────
  const userEmail = window.FP_currentUser;
  const userName = window.FP_currentUserName || userEmail;

  document.getElementById("userEmail").textContent = userName;
  document.getElementById("userAvatar").textContent = userName[0].toUpperCase();

  // Sign out Modal Wiring
  const modal = document.getElementById("signOutModal");
  document.getElementById("signOutBtn")?.addEventListener("click", () => {
    modal.style.display = "flex";
    requestAnimationFrame(() => modal.classList.add("show"));
  });
  document.getElementById("modalCancel")?.addEventListener("click", () => {
    modal.classList.remove("show");
    setTimeout(() => (modal.style.display = "none"), 180);
  });
  document.getElementById("modalConfirm")?.addEventListener("click", () => {
    window.FP_signOut();
  });

  // ─── Data Loading ─────────────────────────────────────────────────────────────
  function loadWatchlist() {
    try {
      watchlist = new Set(JSON.parse(localStorage.getItem("fp_watchlist") || "[]"));
    } catch {
      watchlist = new Set();
    }
    document.getElementById("navWatchlistCount").textContent = watchlist.size;
  }

  function saveWatchlist() {
    localStorage.setItem("fp_watchlist", JSON.stringify([...watchlist]));
    document.getElementById("navWatchlistCount").textContent = watchlist.size;
  }

  async function loadData() {
    showLoader(true, "Loading watchlist data…");
    setProgress(20, "Fetching merchants");
    try {
      const res = await fetch(`${API_URL}/results.json`);
      if (res.ok) {
        leads = await res.json();
      }
    } catch (e) {
      console.warn("Failed to load leads from server:", e);
    }
    setProgress(80, "Rendering page");
    loadWatchlist();
    renderWatchlistPage();
    showLoader(false);
  }

  // ─── Watchlist Rendering ──────────────────────────────────────────────────
  function renderWatchlistPage() {
    let items = leads.filter((l) => watchlist.has(l.domain));
    const search = (document.getElementById("wlSearch")?.value || "").toLowerCase();
    const status = document.getElementById("wlStatusFilter")?.value || "";

    if (search) items = items.filter((l) => l.domain.toLowerCase().includes(search));
    if (status) items = items.filter((l) => getStatus(l.domain) === status);

    const sort = document.getElementById("wlSort")?.value || "score";
    if (sort === "domain") items.sort((a, b) => a.domain.localeCompare(b.domain));
    else if (sort === "status") items.sort((a, b) => getStatus(a.domain).localeCompare(getStatus(b.domain)));
    else if (sort === "added") items.sort((a, b) => getAddedTime(b.domain) - getAddedTime(a.domain));
    else items.sort((a, b) => b.lead_score - a.lead_score);

    document.getElementById("wlTotal").textContent = watchlist.size;
    document.getElementById("wlLive").textContent = items.filter((l) => l.live_checkout).length;
    document.getElementById("wlHot").textContent = items.filter((l) => l.lead_score >= 80).length;
    document.getElementById("wlContacted").textContent = items.filter((l) => getStatus(l.domain) !== "Not Contacted").length;

    const grid = document.getElementById("wlGrid");
    const empty = document.getElementById("wlEmpty");

    if (watchlist.size === 0) {
      grid.innerHTML = "";
      empty.style.display = "flex";
      return;
    }
    empty.style.display = "none";

    if (items.length === 0) {
      grid.innerHTML = '<div class="wl-empty-filter" style="grid-column: 1/-1; text-align: center; color: var(--t3); padding: 40px;">No merchants match the current filters.</div>';
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
            ${l.emails?.length ? `<button class="wl-card-btn" data-action="email" title="Send Email"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg></button>` : ""}
            ${l.whatsapp_link ? `<a class="wl-card-btn" href="${esc(l.whatsapp_link)}" target="_blank" rel="noopener" title="WhatsApp" onclick="event.stopPropagation()"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg></a>` : ""}
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
    const lead = leads.find((l) => l.domain === domain);
    if (!lead) return;

    switch (action) {
      case "select":
        if (selectedDomains.has(domain)) selectedDomains.delete(domain);
        else selectedDomains.add(domain);
        renderWatchlistPage();
        break;
      case "open":
        window.location.href = `index.html?focus=${domain}`;
        break;
      case "visit":
        window.open(/^https?:\/\//i.test(domain) ? domain : `https://${domain}`, "_blank", "noopener,noreferrer");
        break;
      case "email":
        window.location.href = `index.html?focus=${domain}&email=true`;
        break;
      case "remove":
        watchlist.delete(domain);
        saveWatchlist();
        selectedDomains.delete(domain);
        renderWatchlistPage();
        toast(`Removed ${domain}`);
        break;
    }
  }

  // ─── Bulk Action Handlers ───────────────────────────────────────────────────
  function updateBulkBar() {
    const bar = document.getElementById("wlBulkBar");
    const count = selectedDomains.size;
    bar.style.display = count > 0 ? "flex" : "none";
    document.getElementById("wlBulkCount").textContent = `${count} selected`;
  }

  document.getElementById("wlBulkClear")?.addEventListener("click", () => {
    selectedDomains.clear();
    renderWatchlistPage();
  });

  document.getElementById("wlBulkOpen")?.addEventListener("click", () => {
    if (selectedDomains.size > 8 && !confirm(`Open ${selectedDomains.size} tabs?`)) return;
    [...selectedDomains].forEach((d) => window.open(`https://${d}`, "_blank", "noopener,noreferrer"));
  });

  document.getElementById("wlBulkRemove")?.addEventListener("click", () => {
    if (!confirm(`Remove ${selectedDomains.size} merchants from watchlist?`)) return;
    [...selectedDomains].forEach((d) => {
      watchlist.delete(d);
      localStorage.removeItem(`fp_wl_added_${d}`);
    });
    saveWatchlist();
    selectedDomains.clear();
    renderWatchlistPage();
    toast("Removed");
  });

  document.getElementById("wlBulkExport")?.addEventListener("click", () => {
    const selected = leads.filter((l) => selectedDomains.has(l.domain));
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

  // ─── Setup Input Events ─────────────────────────────────────────────────────
  ["wlSearch", "wlSort", "wlStatusFilter"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener("input", renderWatchlistPage);
      el.addEventListener("change", renderWatchlistPage);
    }
  });

  // Load baseline on DOM Load
  document.addEventListener("DOMContentLoaded", loadData);
})();
