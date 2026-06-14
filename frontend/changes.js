// changes.js — standalone Scan Changes page controller
(function () {
  const API_URL = localStorage.getItem("fp_api_url") || "http://localhost:8080";
  let fingerprintChanges = [];
  let activeTab = "critical"; // "critical" or "other"
  let showAcknowledged = false;
  let selectedIds = new Set();

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
    showLoader(true, "Loading scan history…");
    const tbody = document.getElementById("changesTableBody");
    const empty = document.getElementById("changesEmpty");
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="8" class="table-loading">Loading scan changes…</td></tr>';
    if (empty) empty.style.display = "none";
    document.getElementById("changesTotal").textContent = "…";

    try {
      const res = await api("/api/monitor/changes");
      if (res.ok) {
        fingerprintChanges = await res.json();
      } else {
        fingerprintChanges = [];
      }
    } catch (e) {
      console.warn("[Changes] failed to fetch logs:", e);
      fingerprintChanges = [];
    }

    selectedIds.clear();
    const selectAllCheck = document.getElementById("selectAllChanges");
    if (selectAllCheck) selectAllCheck.checked = false;

    filterAndRenderChanges();
    initColumnResizer("changesTable");
    showLoader(false);
  }

  // ─── Helpers to Detect Critical Shifts ──────────────────────────────────────
  function isCriticalShift(item) {
    if (item.field !== "live_checkout") {
      return false;
    }
    const oldVal = item.oldVal.toLowerCase();
    // Critical is only flexype (live) to others.
    return oldVal.includes("flexype");
  }

  function getShiftTag(item) {
    const oldVal = item.oldVal;
    const newVal = item.newVal || "None";
    
    if (oldVal.toLowerCase().includes("flexype")) {
      return `<span class="shift-badge shift-left-flexype">Left FlexyPe</span>`;
    }
    return `<span class="shift-badge shift-left-competitor">Shifted ${esc(oldVal)} → ${esc(newVal)}</span>`;
  }

  // ─── Render Log Items ──────────────────────────────────────────────────────
  function filterAndRenderChanges() {
    const tbody = document.getElementById("changesTableBody");
    const empty = document.getElementById("changesEmpty");
    if (!tbody) return;

    const search = (document.getElementById("changesSearch")?.value || "").toLowerCase();

    // Flatten nested changes in history logs
    let allFlattened = [];
    fingerprintChanges.forEach((log) => {
      const merchant = log.merchant || "";
      const isAck = log.acknowledged || false;

      const changesObj = log.changes || {};
      const fields = Object.keys(changesObj);

      fields.forEach((field) => {
        if (field !== "live_checkout") return;

        let oldVal = changesObj[field].old;
        let newVal = changesObj[field].new;

        if (Array.isArray(oldVal)) oldVal = oldVal.join(", ") || "[]";
        if (Array.isArray(newVal)) newVal = newVal.join(", ") || "[]";

        const strOld = String(oldVal);
        const strNew = String(newVal);

        allFlattened.push({
          id: log._id,
          acknowledged: isAck,
          merchant,
          field,
          oldVal: strOld,
          newVal: strNew,
          timestamp: log.timestamp
        });
      });
    });

    // 1. Separate into Critical and Other
    const criticalItems = allFlattened.filter(item => isCriticalShift(item));
    const otherItems = allFlattened.filter(item => {
      if (isCriticalShift(item)) return false;
      const oldVal = item.oldVal.toLowerCase();
      const newVal = item.newVal.toLowerCase();
      if (oldVal === newVal) return false;
      
      const competitors = ["gokwik", "shopflo", "razorpay", "fastrr", "ecom360", "cashfree"];
      return competitors.some(comp => oldVal.includes(comp));
    });

    // 2. Count Active (Unacknowledged) for badges
    const unackCriticalCount = criticalItems.filter(item => !item.acknowledged).length;
    const unackOtherCount = otherItems.filter(item => !item.acknowledged).length;

    document.getElementById("badgeCritical").textContent = unackCriticalCount;
    document.getElementById("badgeOther").textContent = unackOtherCount;

    // 3. Select items matching active tab and filter settings
    let items = activeTab === "critical" ? criticalItems : otherItems;

    // Search filter
    if (search) {
      items = items.filter(item => item.merchant.toLowerCase().includes(search));
    }

    // Acknowledged filter
    if (!showAcknowledged) {
      items = items.filter(item => !item.acknowledged);
    }

    document.getElementById("changesTotal").textContent = items.length;

    if (items.length === 0) {
      tbody.innerHTML = "";
      if (empty) {
        empty.style.display = "flex";
        document.getElementById("emptyTitle").textContent = showAcknowledged ? "No acknowledged changes" : "No active changes";
        document.getElementById("emptySub").textContent = showAcknowledged ? "Once changes are acknowledged, they will be archived here." : "All changes have been acknowledged and processed!";
      }
      return;
    }

    if (empty) empty.style.display = "none";

    tbody.innerHTML = items.map((l) => {
      const dateStr = l.timestamp ? new Date(l.timestamp).toLocaleString() : "—";
      const isSel = selectedIds.has(l.id);
      
      const typeDisplay = getShiftTag(l);

      return `
        <tr class="${l.acknowledged ? 'row-acknowledged' : ''}">
          <td style="text-align: center;">
            <input type="checkbox" class="row-check" data-id="${esc(l.id)}" ${isSel ? 'checked' : ''} ${l.acknowledged ? 'disabled' : ''}>
          </td>
          <td><strong class="change-domain-click" data-domain="${esc(l.merchant)}">${esc(l.merchant || "—")}</strong></td>
          <td>${typeDisplay}</td>
          <td><span class="val-old">${esc(l.oldVal)}</span></td>
          <td><span class="val-new">${esc(l.newVal)}</span></td>
          <td class="change-date-cell">${esc(dateStr)}</td>
          <td style="text-align: center;">
            ${l.acknowledged 
              ? '<span style="color:var(--text-muted);font-size:12px;">Acked ✓</span>'
              : `<button class="btn-ack row-ack-btn" data-id="${esc(l.id)}">Acknowledge</button>`
            }
          </td>
        </tr>
      `;
    }).join("");

    // Wire up events
    tbody.querySelectorAll(".change-domain-click").forEach((el) => {
      el.addEventListener("click", () => {
        const domain = el.dataset.domain;
        if (domain) {
          window.location.href = `index.html?focus=${domain}`;
        }
      });
    });

    tbody.querySelectorAll(".row-ack-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.id;
        acknowledgeChangesList([id]);
      });
    });

    tbody.querySelectorAll(".row-check").forEach((check) => {
      check.addEventListener("change", () => {
        const id = check.dataset.id;
        if (check.checked) {
          selectedIds.add(id);
        } else {
          selectedIds.delete(id);
        }
        updateBulkBar();
      });
    });

    updateBulkBar();
  }

  // ─── Acknowledge API calls ──────────────────────────────────────────────────
  async function acknowledgeChangesList(ids, all = false) {
    showLoader(true, "Acknowledging changes…");
    try {
      const payload = all ? { all: true } : { ids };
      const res = await api("/api/monitor/changes/acknowledge", {
        method: "POST",
        body: payload
      });
      if (res.ok) {
        toast("Changes acknowledged ✓", "success");
        await loadData();
      } else {
        toast("Failed to acknowledge changes", "error");
      }
    } catch (e) {
      console.error(e);
      toast("Error connecting to server", "error");
    } finally {
      showLoader(false);
    }
  }

  function updateBulkBar() {
    const count = selectedIds.size;
    document.getElementById("changesSelectedCount").textContent = `${count} selected`;
    document.getElementById("btnAckSelected").disabled = count === 0;
  }

  // ─── Setup Tab Listeners ────────────────────────────────────────────────────
  document.getElementById("tabCritical")?.addEventListener("click", () => {
    document.getElementById("tabCritical").classList.add("active");
    document.getElementById("tabOther").classList.remove("active");
    activeTab = "critical";
    selectedIds.clear();
    filterAndRenderChanges();
  });

  document.getElementById("tabOther")?.addEventListener("click", () => {
    document.getElementById("tabOther").classList.add("active");
    document.getElementById("tabCritical").classList.remove("active");
    activeTab = "other";
    selectedIds.clear();
    filterAndRenderChanges();
  });

  // ─── Setup Checkbox & Acknowledge Handlers ──────────────────────────────────
  document.getElementById("toggleShowAck")?.addEventListener("change", (e) => {
    showAcknowledged = e.target.checked;
    selectedIds.clear();
    filterAndRenderChanges();
  });

  document.getElementById("selectAllChanges")?.addEventListener("change", (e) => {
    const checked = e.target.checked;
    const checks = document.querySelectorAll(".row-check:not([disabled])");
    checks.forEach((chk) => {
      chk.checked = checked;
      const id = chk.dataset.id;
      if (checked) {
        selectedIds.add(id);
      } else {
        selectedIds.delete(id);
      }
    });
    updateBulkBar();
  });

  document.getElementById("btnAckSelected")?.addEventListener("click", () => {
    if (selectedIds.size === 0) return;
    acknowledgeChangesList([...selectedIds]);
  });

  document.getElementById("btnAckAll")?.addEventListener("click", () => {
    const text = activeTab === "critical" 
      ? "Are you sure you want to acknowledge all active critical shifts?" 
      : "Are you sure you want to acknowledge all active other changes?";
    if (confirm(text)) {
      acknowledgeChangesList([], true);
    }
  });

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

  document.getElementById("changesSearch")?.addEventListener("input", filterAndRenderChanges);

  // Load baseline on DOM Load
  document.addEventListener("DOMContentLoaded", loadData);
})();