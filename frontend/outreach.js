// outreach.js — standalone Outreach Log page controller
(function () {
  const API_URL = localStorage.getItem("fp_api_url") || "http://localhost:8080";
  let outreachLogs = [];
  let currentSelectedEmail = null;

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

  // ─── Auth / Session Initialization ──────────────────────────────────────────
  const userEmail = getSessionUser();
  document.getElementById("userEmail").textContent = userEmail;
  document.getElementById("userAvatar").textContent = userEmail[0].toUpperCase();

  // Watchlist Count for Sidebar nav
  try {
    const wl = JSON.parse(localStorage.getItem("fp_watchlist") || "[]");
    document.getElementById("navWatchlistCount").textContent = wl.length;
  } catch {
    document.getElementById("navWatchlistCount").textContent = 0;
  }

  // Sign out Modal Wiring
  const modal = document.getElementById("signOutModal");
  document.getElementById("signOutBtn")?.addEventListener("click", () => {
    modal.hidden = false;
    modal.style.display = "flex";
    requestAnimationFrame(() => modal.classList.add("show"));
  });
  document.getElementById("modalCancel")?.addEventListener("click", () => {
    modal.classList.remove("show");
    setTimeout(() => {
      modal.style.display = "none";
      modal.hidden = true;
    }, 180);
  });
  document.getElementById("modalConfirm")?.addEventListener("click", () => {
    sessionStorage.removeItem("fp_session");
    window.location.replace("login.html");
  });

  // ─── Data Loading ─────────────────────────────────────────────────────────────
  async function loadData() {
    showLoader(true, "Loading outreach history…");
    const tbody = document.getElementById("outreachTableBody");
    const empty = document.getElementById("outreachEmpty");
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="6" class="table-loading">Loading outreach logs…</td></tr>';
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
    initColumnResizer("outreachTable");
    showLoader(false);
  }

  // ─── Render Logs ────────────────────────────────────────────────────────────
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

    tbody.innerHTML = items.map((l, idx) => {
      const dateStr = l.sent_at ? new Date(l.sent_at).toLocaleString() : "—";
      const status = l.status || "sent";
      const isReplied = status.toLowerCase() === "replied";
      const statusBadge = isReplied
        ? '<span class="status-pill status-pill-replied">Replied</span>'
        : '<span class="status-pill status-pill-sent">Sent</span>';

      return `
        <tr class="outreach-row" data-index="${idx}">
          <td><strong class="outreach-domain-click" data-domain="${esc(l.domain)}">${esc(l.domain || "—")}</strong></td>
          <td><a href="mailto:${esc(l.to)}" class="contact-link" onclick="event.stopPropagation()">${esc(l.to)}</a></td>
          <td class="cell-truncate" title="${esc(l.subject)}">${esc(l.subject)}</td>
          <td class="cell-muted">${esc(l.flexype_user)}</td>
          <td>${statusBadge}</td>
          <td class="change-date-cell">${esc(dateStr)}</td>
        </tr>
      `;
    }).join("");

    // Wire clicks to open email view modal
    tbody.querySelectorAll(".outreach-row").forEach((row) => {
      row.addEventListener("click", (e) => {
        if (e.target.closest(".outreach-domain-click")) return;
        const idx = parseInt(row.dataset.index, 10);
        openEmailView(outreachLogs[idx]);
      });
    });

    tbody.querySelectorAll(".outreach-domain-click").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const domain = el.dataset.domain;
        if (domain) {
          window.location.href = `index.html?focus=${domain}`;
        }
      });
    });
  }

  // ─── Modal Email View ────────────────────────────────────────────────────────
  const emailModal = document.getElementById("emailViewModal");

  async function openEmailView(emailLog) {
    currentSelectedEmail = emailLog;
    document.getElementById("modalEmailSubject").textContent = emailLog.subject || "(No Subject)";
    document.getElementById("modalEmailTo").textContent = emailLog.to || "—";

    const ccRow = document.getElementById("modalCcRow");
    if (emailLog.cc) {
      document.getElementById("modalEmailCc").textContent = emailLog.cc;
      ccRow.style.display = "flex";
    } else {
      ccRow.style.display = "none";
    }

    document.getElementById("modalEmailFrom").textContent = emailLog.flexype_user || "—";
    document.getElementById("modalEmailDate").textContent = emailLog.sent_at ? new Date(emailLog.sent_at).toLocaleString() : "—";

    const isReplied = (emailLog.status || "").toLowerCase() === "replied";
    document.getElementById("modalEmailStatus").innerHTML = isReplied
      ? `<span class="status-pill status-pill-replied">
           <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
           Replied
         </span>`
      : `<span class="status-pill status-pill-sent">Sent</span>`;

    const bodyContainer = document.getElementById("modalEmailBody");
    bodyContainer.innerHTML = `
      <div class="modal-loading">
        <svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
          <circle cx="12" cy="12" r="10" stroke-opacity="0.25"/>
          <path d="M4 12a8 8 0 0 1 8-8"/>
        </svg>
        <span>Loading conversation thread…</span>
      </div>`;

    const openInGmailBtn = document.getElementById("modalOpenInGmailBtn");
    if (openInGmailBtn) {
      const threadId = emailLog.gmail_thread_id;
      const domain = emailLog.domain || "";
      const gmailUserIndex = localStorage.getItem("fp_gmail_user_index") || "4";
      openInGmailBtn.onclick = () => {
        const url = threadId
          ? `https://mail.google.com/mail/u/${gmailUserIndex}/#sent/${threadId}`
          : `https://mail.google.com/mail/u/${gmailUserIndex}/#search/${encodeURIComponent(domain)}`;
        window.open(url, "_blank", "noopener,noreferrer");
      };
    }

    emailModal.hidden = false;
    emailModal.style.display = "flex";
    requestAnimationFrame(() => emailModal.classList.add("show"));

    try {
      const threadId = emailLog.gmail_thread_id;
      const res = await api(`/api/outreach/thread?thread_id=${threadId || ""}&domain=${emailLog.domain || ""}`);
      if (res.ok) {
        const data = await res.json();
        if (data.ok && data.messages && data.messages.length) {
          if (data.thread_id && openInGmailBtn) {
            const gmailUserIndex = localStorage.getItem("fp_gmail_user_index") || "4";
            openInGmailBtn.onclick = () => {
              window.open(`https://mail.google.com/mail/u/${gmailUserIndex}/#sent/${data.thread_id}`, "_blank", "noopener,noreferrer");
            };
          }
          let html = '<div class="email-thread-wrap">';
          data.messages.forEach((msg) => {
            const fromLower = (msg.from || "").toLowerCase();
            const isOutgoing = fromLower.includes("flexype.in") || fromLower.includes("myselfkushu");
            const sideClass = isOutgoing ? "outgoing" : "incoming";
            html += `
              <div class="thread-message ${sideClass}">
                <div class="thread-msg-header">
                  <span class="thread-msg-sender">${esc(msg.from)}</span>
                  <span class="thread-msg-date">${esc(msg.date)}</span>
                </div>
                <div class="thread-msg-body">${esc(msg.body || "(Empty message)")}</div>
              </div>
            `;
          });
          html += '</div>';
          bodyContainer.innerHTML = html;
          return;
        }
      }
    } catch (e) {
      console.warn("Failed to load full thread:", e);
    }

    // Fallback if fetch fails
    bodyContainer.innerHTML = `<div class="thread-message outgoing"><div class="thread-msg-body">${esc(emailLog.body || "(No message body)")}</div></div>`;
  }

  function closeEmailView() {
    emailModal.classList.remove("show");
    setTimeout(() => {
      emailModal.style.display = "none";
      emailModal.hidden = true;
      currentSelectedEmail = null;
    }, 180);
  }

  document.getElementById("modalCloseEmailView")?.addEventListener("click", closeEmailView);
  document.getElementById("modalCloseEmailViewBtn")?.addEventListener("click", closeEmailView);
  document.getElementById("modalEmailBackToDashboard")?.addEventListener("click", () => {
    if (currentSelectedEmail && currentSelectedEmail.domain) {
      window.location.href = `index.html?focus=${currentSelectedEmail.domain}`;
    }
  });

  // Close modal on outside click
  emailModal?.addEventListener("click", (e) => {
    if (e.target === emailModal) closeEmailView();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && emailModal?.classList.contains("show")) closeEmailView();
  });

  // ─── Sync Replies ──────────────────────────────────────────────────────────
  async function syncReplies() {
    const btn = document.getElementById("syncRepliesBtn");
    btn.disabled = true;
    btn.innerHTML = `
      <svg class="spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
        <circle cx="12" cy="12" r="10" stroke-opacity="0.25"/>
        <path d="M4 12a8 8 0 0 1 8-8"/>
      </svg>
      <span>Syncing…</span>
    `;
    toast("Syncing replies from Gmail…", "info");

    try {
      const res = await api("/api/outreach/sync-replies", { method: "POST" });
      const data = await res.json().catch(() => ({}));

      if (res.ok && data.ok) {
        if (data.updated_count > 0) {
          toast(`Synced successfully! Found ${data.updated_count} new replies.`, "success");
        } else {
          toast("Replies synced. No new replies found.", "info");
        }
        await loadData();
      } else {
        toast(data.error || "Gmail synchronization failed", "error");
      }
    } catch (e) {
      toast("Sync failed: backend server unreachable.", "error");
    } finally {
      btn.disabled = false;
      btn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
        <span>Sync Replies</span>
      `;
    }
  }

  function initColumnResizer(tableId) {
    const table = typeof tableId === "string" ? document.getElementById(tableId) : tableId;
    if (!table) return;

    const cols = table.querySelectorAll("thead th");
    cols.forEach((col) => {
      if (col.querySelector(".col-resizer")) return;

      const resizer = document.createElement("div");
      resizer.className = "col-resizer";
      col.appendChild(resizer);

      let startX = 0;
      let startWidth = 0;
      let isDragging = false;

      resizer.addEventListener("click", (e) => {
        e.stopPropagation();
      });

      const onMouseMove = (e) => {
        if (!isDragging) return;
        const deltaX = e.clientX - startX;
        const newWidth = Math.max(50, startWidth + deltaX);
        col.style.width = `${newWidth}px`;
      };

      const onMouseUp = () => {
        if (isDragging) {
          isDragging = false;
          resizer.classList.remove("dragging");
          document.body.style.cursor = "";
          document.body.style.userSelect = "";
          document.removeEventListener("mousemove", onMouseMove);
          document.removeEventListener("mouseup", onMouseUp);
        }
      };

      resizer.addEventListener("mousedown", (e) => {
        e.stopPropagation();
        e.preventDefault();

        isDragging = true;
        startX = e.clientX;
        startWidth = col.offsetWidth;
        resizer.classList.add("dragging");

        const allHeaders = table.querySelectorAll("thead th");
        allHeaders.forEach((th) => {
          th.style.width = `${th.offsetWidth}px`;
        });
        table.style.tableLayout = "fixed";

        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";

        document.addEventListener("mousemove", onMouseMove);
        document.addEventListener("mouseup", onMouseUp);
      });
    });
  }

  document.getElementById("syncRepliesBtn")?.addEventListener("click", syncReplies);
  document.getElementById("outreachSearch")?.addEventListener("input", filterAndRenderOutreach);

  // Load baseline
  document.addEventListener("DOMContentLoaded", loadData);
})();