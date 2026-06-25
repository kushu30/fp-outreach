(function () {
  const API_URL = localStorage.getItem("fp_api_url") || "http://localhost:8080";
  let scanQueue = [];
  let isScanning = false;

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

  // Session Init
  const userEmail = window.FP_currentUser;
  const userName = window.FP_currentUserName || userEmail;

  document.getElementById("userEmail").textContent = userName;
  document.getElementById("userAvatar").textContent = userName[0].toUpperCase();

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

  // --- CSV / Paste File Handling ---
  const dragDropArea = document.getElementById("dragDropArea");
  const fileInput = document.getElementById("batchFileInput");
  const fileLabel = document.getElementById("fileUploadLabel");
  let selectedFileContent = "";

  dragDropArea?.addEventListener("click", () => fileInput.click());
  dragDropArea?.addEventListener("dragover", (e) => {
    e.preventDefault();
    dragDropArea.style.borderColor = "var(--primary)";
  });
  dragDropArea?.addEventListener("dragleave", () => {
    dragDropArea.style.borderColor = "var(--border-color)";
  });
  dragDropArea?.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDropArea.style.borderColor = "var(--border-color)";
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  });

  fileInput?.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) handleFile(file);
  });

  function handleFile(file) {
    const reader = new FileReader();
    reader.onload = (event) => {
      selectedFileContent = event.target.result;
      fileLabel.textContent = `File loaded: ${file.name}`;
      toast("File loaded successfully", "success");
    };
    reader.readAsText(file);
  }

  function extractDomains(text) {
    // Matches standard domain structures, ignoring spaces, scheme prefixes
    const raw = text.split(/[\n\r,;\s]+/).map(item => {
      let d = item.trim().toLowerCase();
      if (d.startsWith("http://") || d.startsWith("https://")) {
        d = d.split("://")[1];
      }
      return d.split("/")[0].trim();
    }).filter(d => d.length > 3 && d.includes("."));
    return raw;
  }

  // --- Scanning Logic ---
  const startBtn = document.getElementById("startBatchBtn");
  const textarea = document.getElementById("batchTextarea");
  const tableBody = document.getElementById("batchTableBody");
  const progressPanel = document.getElementById("progressPanel");
  const progressBar = document.getElementById("batchProgressBar");
  const progressText = document.getElementById("progressText");
  const activeText = document.getElementById("activeDomainText");

  startBtn?.addEventListener("click", async () => {
    if (isScanning) return;

    // 1. Gather all input sources
    const pastedText = textarea.value;
    let domains = [];

    if (pastedText) {
      domains = domains.concat(extractDomains(pastedText));
    }
    if (selectedFileContent) {
      domains = domains.concat(extractDomains(selectedFileContent));
    }

    if (domains.length === 0) {
      toast("Please paste domains or upload a CSV file first", "error");
      return;
    }

    // 2. Remove duplicates if checked
    const deduplicate = document.getElementById("deduplicateCheckbox").checked;
    if (deduplicate) {
      domains = Array.from(new Set(domains));
    }

    const excludeMaster = document.getElementById("excludeMasterCheckbox").checked;

    scanQueue = domains;
    isScanning = true;
    startBtn.disabled = true;
    startBtn.textContent = "Scanning Batch...";
    tableBody.innerHTML = "";
    progressPanel.style.display = "block";

    let completed = 0;
    const total = scanQueue.length;

    for (const domain of scanQueue) {
      activeText.textContent = `Active: ${domain}`;
      progressText.textContent = `Scanned ${completed} of ${total} domains (${Math.round((completed/total)*100)}%)`;
      progressBar.style.width = `${(completed/total)*100}%`;

      try {
        const res = await api("/scan-domain", {
          method: "POST",
          body: { domain, exclude_master: excludeMaster }
        });
        const dateStr = new Date().toLocaleString();

        if (res.ok) {
          const data = await res.json();
          appendRow(data, dateStr);
        } else {
          appendErrorRow(domain, dateStr, "Scan failed (Server Error)");
        }
      } catch (e) {
        const dateStr = new Date().toLocaleString();
        appendErrorRow(domain, dateStr, `Failed to reach server: ${e.message}`);
      }

      completed++;
    }

    // Finished
    progressBar.style.width = "100%";
    progressText.textContent = `Completed scanning all ${total} domains!`;
    activeText.textContent = "Active: Done";
    isScanning = false;
    startBtn.disabled = false;
    startBtn.textContent = "Start Batch Scan";
    toast("Batch scan finished", "success");
  });

  function appendRow(data, dateStr) {
    const row = document.createElement("tr");
    const emails = (data.emails || []).join(", ") || "—";
    const phones = (data.phone_numbers || []).join(", ") || "—";
    const shopifyTag = data.shopify ? '<span class="live-chip" style="animation:none;background:var(--green-dim);color:var(--green);border:1px solid rgba(52,211,153,0.3)">Shopify</span>' : '<span style="color:var(--text-muted)">No</span>';
    const checkout = data.live_checkout ? `<span class="live-chip">${esc(data.live_checkout)}</span>` : '<span style="color:var(--text-muted)">—</span>';

    row.innerHTML = `
      <td><strong>${esc(data.domain)}</strong></td>
      <td style="font-size:11.5px;color:var(--text-muted);">${esc(dateStr)}</td>
      <td>${shopifyTag}</td>
      <td>${checkout}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(emails)}">${esc(emails)}</td>
      <td>${esc(phones)}</td>
      <td><strong>${data.lead_score || 0}</strong></td>
    `;
    tableBody.appendChild(row);
  }

  function appendErrorRow(domain, dateStr, errorMsg) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${esc(domain)}</strong></td>
      <td style="font-size:11.5px;color:var(--text-muted);">${esc(dateStr)}</td>
      <td colspan="5" style="color:var(--red);font-style:italic;">${esc(errorMsg)}</td>
    `;
    tableBody.appendChild(row);
  }
})();
