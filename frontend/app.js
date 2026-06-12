// app.js — FlexyPe Merchant Intelligence Platform

let leads = [];
let selectedLead = null;
let activeRow = null;
let watchlist = new Set();
const API_URL = localStorage.getItem("fp_api_url") || "http://localhost:8080";

// Expose to pages.js
window.leads = leads;
window.watchlist = watchlist;
window.selectedLead = selectedLead;

// ─── Email Templates ──────────────────────────────────────────────────────────

const EMAIL_TEMPLATES = {
  intro: `Hi team,

Reaching out from FlexyPe — we help Shopify brands increase checkout conversion rates, typically 20–30%, with same-day setup and zero code changes required.

Worth a 15-minute call to see if it's a fit for your store?

Best,
[Your Name]
FlexyPe`,

  competitor: `Hi team,

Noticed you're using [[PROVIDER]] for checkout. FlexyPe often outperforms [[PROVIDER]] on conversion rate, transaction fees, and load time — with a seamless migration path.

Happy to share a quick comparison or case studies if useful.

Best,
[Your Name]
FlexyPe`,

  migration: `Hi team,

If you're evaluating checkout solutions for 2026, FlexyPe is worth a look — zero-downtime migration, custom checkout flows, subscription support, and multi-currency.

Can share case studies from similar merchants.

Best,
[Your Name]
FlexyPe`,

  partnership: `Hi team,

FlexyPe is looking to partner with high-growth Shopify brands — revenue share, priority support, early access to features, and co-marketing.

Happy to jump on a quick call to explore.

Best,
[Your Name]
FlexyPe`,

  feature: `Hi team,

FlexyPe just launched one-click upsells, post-purchase offers, and an analytics dashboard. Merchants are seeing 35% AOV increase on average.

Interested in a personalised demo?

Best,
[Your Name]
FlexyPe`,
};

// ─── Utilities ────────────────────────────────────────────────────────────────

function esc(str) {
  if (!str) return "";
  return String(str).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function domainUrl(domain) {
  return /^https?:\/\//i.test(domain) ? domain : `https://${domain}`;
}

function toast(msg, type = "info") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `toast show toast-${type}`;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2800);
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
function priorityClass(p) { return `priority-${(p || "low").toLowerCase()}`; }
function priChipClass(p)  { return `pri-${(p || "low").toLowerCase()}`; }

// ─── Watchlist ─────────────────────────────────────────────────────────────

function loadWatchlist() {
  try {
    watchlist = new Set(JSON.parse(localStorage.getItem("fp_watchlist") || "[]"));
  } catch { watchlist = new Set(); }
  window.watchlist = watchlist;
}

function saveWatchlist() {
  localStorage.setItem("fp_watchlist", JSON.stringify([...watchlist]));
  window.watchlist = watchlist;
}

function toggleWatchlist(domain) {
  if (watchlist.has(domain)) {
    watchlist.delete(domain);
    toast(`Removed ${domain}`);
  } else {
    watchlist.add(domain);
    // record timestamp for "recently added" sort
    if (!localStorage.getItem(`fp_wl_added_${domain}`)) {
      localStorage.setItem(`fp_wl_added_${domain}`, String(Date.now()));
    }
    toast(`Added ${domain} to watchlist`);
  }
  saveWatchlist();
  renderTable(leads);
  if (selectedLead) renderWatchlist();
}

// ─── Filters / Stats ──────────────────────────────────────────────────────────

function populateFilters() {
  const live = new Set(), hist = new Set();
  leads.forEach((l) => {
    if (l.live_checkout) live.add(l.live_checkout);
    (l.historical_checkouts || []).forEach((p) => hist.add(p));
  });

  const liveEl = document.getElementById("providerFilter");
  liveEl.innerHTML = '<option value="">All providers</option>';
  [...live].sort().forEach((p) => {
    liveEl.insertAdjacentHTML("beforeend", `<option value="${esc(p)}">${esc(p)}</option>`);
  });

  const histEl = document.getElementById("historicalFilter");
  histEl.innerHTML = '<option value="">Historical</option>';
  [...hist].sort().forEach((p) => {
    histEl.insertAdjacentHTML("beforeend", `<option value="${esc(p)}">${esc(p)}</option>`);
  });
}

function updateStats() {
  document.getElementById("totalLeads").textContent = leads.length;
  document.getElementById("liveCount").textContent = leads.filter((l) => l.live_checkout).length;
  document.getElementById("historicalOnlyCount").textContent = leads.filter((l) => l.historical_checkouts?.length && !l.live_checkout).length;
  document.getElementById("hotLeads").textContent = leads.filter((l) => l.lead_score >= 80).length;
  document.getElementById("kwikpassCount").textContent = leads.filter((l) => l.has_kwikpass).length;
  document.getElementById("emailCount").textContent = leads.filter((l) => l.emails?.length).length;
}

// ─── Table ────────────────────────────────────────────────────────────────────

function renderTable(data) {
  const tbody = document.querySelector("#leadTable tbody");
  const search   = (document.getElementById("searchInput")?.value || "").toLowerCase();
  const liveProv = document.getElementById("providerFilter")?.value || "";
  const histProv = document.getElementById("historicalFilter")?.value || "";
  const priority = document.getElementById("priorityFilter")?.value || "";
  const sortBy   = document.getElementById("sortFilter")?.value || "score";

  const filtered = data.filter((l) =>
    l.domain.toLowerCase().includes(search) &&
    (!liveProv || l.live_checkout === liveProv) &&
    (!histProv || (l.historical_checkouts || []).includes(histProv)) &&
    (!priority || l.priority === priority)
  );

  if (sortBy === "domain") filtered.sort((a, b) => a.domain.localeCompare(b.domain));
  else filtered.sort((a, b) => b.lead_score - a.lead_score);

  tbody.innerHTML = "";
  filtered.forEach((lead) => {
    const isSel = selectedLead?.domain === lead.domain;
    const isWL = watchlist.has(lead.domain);
    const tr = document.createElement("tr");
    if (isSel) { tr.classList.add("active"); activeRow = tr; }
    if (isWL)  tr.classList.add("watchlisted");
    tr.setAttribute("data-domain", lead.domain);

    tr.innerHTML = `
      <td title="${esc(lead.domain)}">${esc(lead.domain)}${isWL ? ' <span class="watchlist-star">★</span>' : ""}</td>
      <td><span class="score-chip ${scoreClass(lead.lead_score)}">${lead.lead_score}</span></td>
      <td>${lead.live_checkout ? `<span class="live-chip">${esc(lead.live_checkout)}</span>` : '<span class="muted-chip">—</span>'}</td>
      <td>${lead.whatsapp_number ? "✅" : "—"}</td>
      <td>${lead.phone_numbers?.length ? "✅" : "—"}</td>
      <td title="${esc(lead.myshopify_domain || "")}">${lead.myshopify_domain ? `<span style="font-family:'DM Mono',monospace;font-size:10px;color:var(--t2)">${esc(lead.myshopify_domain)}</span>` : "—"}</td>
      <td><span class="pri-chip ${priChipClass(lead.priority)}">${esc(lead.priority)}</span></td>
    `;

    tr.addEventListener("click", () => {
      document.querySelectorAll("#leadTable tbody tr").forEach((r) => r.classList.remove("active"));
      tr.classList.add("active");
      activeRow = tr;
      showLead(lead);
    });

    tbody.appendChild(tr);
  });

  document.getElementById("leadCount").textContent = `${filtered.length} / ${leads.length}`;
}

// ─── Detail Panel ─────────────────────────────────────────────────────────────

function showLead(lead) {
  selectedLead = lead;
  window.selectedLead = lead;

  document.getElementById("domainTitle").textContent = lead.domain;

  const meta = document.getElementById("merchantMeta");
  meta.innerHTML = `
    ${lead.shopify ? '<span class="shopify-tag">Shopify</span>' : ""}
    <span class="meta-date">Scanned ${esc(lead.last_scan || "—")}</span>
  `;

  const pb = document.getElementById("priorityBadge");
  pb.className = `priority-badge ${priorityClass(lead.priority)}`;
  pb.textContent = lead.priority || "";

  document.getElementById("watchlistBtn").innerHTML = `
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
    ${watchlist.has(lead.domain) ? "Unwatch" : "Watch"}
  `;

  // Live Checkout
  const liveCard = document.getElementById("liveCheckoutCard");
  if (lead.live_checkout) {
    const evidence = (lead.live_evidence || []).slice(0, 3)
      .map((u) => `<span class="evidence-url">${esc(u.split("?")[0])}</span>`).join("");
    liveCard.className = "checkout-card";
    liveCard.innerHTML = `
      <div class="checkout-header">
        <span class="checkout-name">${esc(lead.live_checkout)}</span>
        <span class="checkout-conf">${lead.live_confidence || 0}% conf</span>
      </div>
      ${evidence ? `<div class="checkout-evidence"><div class="evidence-lbl">Network evidence</div>${evidence}</div>` : ""}
    `;
  } else {
    liveCard.className = "checkout-card checkout-empty";
    liveCard.textContent = "No live checkout detected";
  }

  // Historical
  const histEl = document.getElementById("historicalBadges");
  const histOnly = (lead.historical_checkouts || []).filter((p) => p !== lead.live_checkout);
  histEl.innerHTML = histOnly.length
    ? histOnly.map((p) => `<span class="badge badge-hist">${esc(p)}</span>`).join("")
    : '<span class="badge-empty">None detected</span>';

  // Kwikpass
  document.getElementById("kwikpassStatus").innerHTML = lead.has_kwikpass
    ? '<span class="kp-yes">Kwikpass detected — Login/OTP enabled</span>'
    : '<span class="kp-no">Not detected</span>';

  // Contact
  const contactEl = document.getElementById("contactInfo");
  let contactHtml = "";

  if (lead.emails?.length) {
    const emailItems = lead.emails.map((e) =>
      `<span class="contact-item"><a href="mailto:${esc(e)}" class="contact-link">${esc(e)}</a><button class="copy-mini" data-copy="${esc(e)}">copy</button></span>`
    ).join("");
    contactHtml += `<div class="contact-row"><span class="contact-icon">✉</span><div class="contact-vals">${emailItems}</div></div>`;
  }

  if (lead.phone_numbers?.length) {
    const phoneItems = lead.phone_numbers.map((p) =>
      `<span class="contact-item">${esc(p)}<button class="copy-mini" data-copy="${esc(p)}">copy</button></span>`
    ).join("");
    contactHtml += `<div class="contact-row"><span class="contact-icon">📞</span><div class="contact-vals">${phoneItems}</div></div>`;
  }

  if (lead.whatsapp_number || lead.whatsapp_link) {
    const num = lead.whatsapp_number || "";
    const link = lead.whatsapp_link || "";
    contactHtml += `<div class="contact-row"><span class="contact-icon">💬</span><div class="contact-vals">
      ${link ? `<a href="${esc(link)}" target="_blank" rel="noopener noreferrer" class="contact-link">WhatsApp</a>` : ""}
      ${num ? `<span class="contact-item">${esc(num)}<button class="copy-mini" data-copy="${esc(num)}">copy</button></span>` : ""}
    </div></div>`;
  }

  if (lead.myshopify_domain) {
    contactHtml += `<div class="contact-row"><span class="contact-icon">🏷</span><div class="contact-vals">
      <span class="contact-item" style="font-family:'DM Mono',monospace;font-size:11px">${esc(lead.myshopify_domain)}<button class="copy-mini" data-copy="${esc(lead.myshopify_domain)}">copy</button></span>
    </div></div>`;
  }

  const socials = lead.socials || {};
  const socialLinks = [
    ["linkedin", "LinkedIn"], ["instagram", "Instagram"], ["facebook", "Facebook"],
    ["twitter", "Twitter"], ["youtube", "YouTube"],
  ].filter(([k]) => socials[k])
   .map(([k, label]) => `<a href="${esc(socials[k])}" target="_blank" rel="noopener noreferrer" class="social-link">${label}</a>`)
   .join("");

  if (socialLinks) {
    contactHtml += `<div class="contact-row"><span class="contact-icon">🔗</span><div class="contact-vals">${socialLinks}</div></div>`;
  }

  contactEl.innerHTML = contactHtml || '<span class="empty-msg">No contact information found</span>';

  contactEl.querySelectorAll(".copy-mini").forEach((btn) => {
    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(btn.dataset.copy || "");
      toast("Copied");
    });
  });

  // Tech Stack
  document.getElementById("techStackBadges").innerHTML = lead.tech_stack?.length
    ? lead.tech_stack.map((t) => `<span class="badge badge-tech">${esc(t)}</span>`).join("")
    : '<span class="badge-empty">None detected</span>';

  // Merchant Info
  document.getElementById("merchantInfo").innerHTML = `
    <div class="info-row"><span class="info-key">Title</span><span class="info-val">${esc(lead.title || "—")}</span></div>
    <div class="info-row"><span class="info-key">Description</span><span class="info-val">${esc(lead.description || "—")}</span></div>
    <div class="info-row"><span class="info-key">Page Hash</span><span class="info-val info-mono">${esc(lead.page_hash || "—")}</span></div>
  `;

  // Evidence
  const evEl = document.getElementById("providerEvidence");
  evEl.innerHTML = lead.live_evidence?.length
    ? lead.live_evidence.map((u) => `<div class="evidence-item">${esc(u)}</div>`).join("")
    : '<span class="empty-msg">No evidence available</span>';

  // Notes / Status
  const notesEl = document.getElementById("leadNotes");
  if (notesEl) notesEl.value = localStorage.getItem(`fp_notes_${lead.domain}`) || lead.notes || "";
  const statusEl = document.getElementById("leadStatus");
  if (statusEl) statusEl.value = localStorage.getItem(`fp_status_${lead.domain}`) || lead.status || "Not Contacted";

  // Show contacted metadata if recorded
  const contactedMetaEl = document.getElementById("contactedMeta");
  if (contactedMetaEl) {
    const contactedBy = lead.contacted_by;
    const contactedAt = lead.contacted_at;
    if (contactedBy && contactedAt) {
      try {
        const dateStr = new Date(contactedAt).toLocaleString();
        contactedMetaEl.innerHTML = `
          <div style="margin-top: 8px; padding: 6px 10px; background: rgba(16,185,129,0.06); border: 1px solid rgba(16,185,129,0.15); border-radius: 4px; display: flex; flex-direction: column; gap: 2px;">
            <span style="color: #10b981; font-weight: 500; font-size: 11px;">Outreach Recorded</span>
            <span style="color: var(--t2); font-size: 10.5px;">By: <strong>${esc(contactedBy)}</strong></span>
            <span style="color: var(--t3); font-size: 10.5px;">At: ${esc(dateStr)}</span>
          </div>
        `;
        contactedMetaEl.style.display = "block";
      } catch (e) {
        contactedMetaEl.style.display = "none";
      }
    } else {
      contactedMetaEl.style.display = "none";
    }
  }

  generateEmail(lead);
  renderWatchlist();
}

// ─── Watchlist Render (sidebar list in detail) ───────────────────────────────

function renderWatchlist() {
  const el = document.getElementById("watchlistItems");
  if (!el) return;
  const items = leads.filter((l) => watchlist.has(l.domain)).slice(0, 5);
  if (!items.length) {
    el.innerHTML = '<span class="watchlist-empty">No merchants watchlisted</span>';
    return;
  }
  el.innerHTML = items.map((l) => `
    <div class="watchlist-item" data-domain="${esc(l.domain)}">
      <div>
        <div class="wl-domain">${esc(l.domain)}</div>
        <div class="wl-meta">Score: ${l.lead_score} ${l.live_checkout ? `· <span class="wl-live">${esc(l.live_checkout)}</span>` : ""}</div>
      </div>
      <button class="wl-remove" data-domain="${esc(l.domain)}" title="Remove">×</button>
    </div>
  `).join("");

  el.querySelectorAll(".watchlist-item").forEach((item) => {
    item.addEventListener("click", (e) => {
      if (e.target.classList.contains("wl-remove")) return;
      const lead = leads.find((l) => l.domain === item.dataset.domain);
      if (lead) { showLead(lead); highlightRow(lead.domain); }
    });
  });
  el.querySelectorAll(".wl-remove").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      watchlist.delete(btn.dataset.domain);
      saveWatchlist();
      renderTable(leads);
      renderWatchlist();
    });
  });
}

function highlightRow(domain) {
  document.querySelectorAll("#leadTable tbody tr").forEach((r) => r.classList.remove("active"));
  const row = document.querySelector(`#leadTable tbody tr[data-domain="${domain}"]`);
  if (row) { row.classList.add("active"); activeRow = row; row.scrollIntoView({ block: "nearest" }); }
}

// ─── Email ────────────────────────────────────────────────────────────────────

function generateEmail(lead) {
  const type = document.getElementById("templateType")?.value || "intro";
  let tpl = EMAIL_TEMPLATES[type] || EMAIL_TEMPLATES.intro;
  if (type === "competitor" && lead?.live_checkout) {
    tpl = tpl.replaceAll("[[PROVIDER]]", lead.live_checkout);
  }
  const el = document.getElementById("emailPreview");
  if (el) el.value = tpl;
}

// ─── Data Loading ─────────────────────────────────────────────────────────────

function generateDemoData() {
  return [
    {
      domain: "velvetaura.com", shopify: true, live_checkout: "GoKwik", live_confidence: 95,
      live_evidence: ["hits.gokwik.co/api/v1/events"], historical_checkouts: ["Shopflo", "Razorpay"],
      has_kwikpass: true, emails: ["hello@velvetaura.com", "founder@velvetaura.com"],
      phone_numbers: ["+91 98765 43210"], whatsapp_link: "https://wa.me/919876543210",
      whatsapp_number: "+919876543210", myshopify_domain: "velvetaura.myshopify.com",
      socials: { linkedin: "https://linkedin.com/company/velvetaura", instagram: "https://instagram.com/velvetaura", facebook: "", twitter: "", youtube: "" },
      tech_stack: ["Klaviyo", "Meta Pixel", "Google Analytics"],
      title: "Velvet Aura — Premium Beauty", description: "Luxury skincare and cosmetics brand",
      lead_score: 94, priority: "CRITICAL", last_scan: "2026-06-07", status: "Not Contacted", notes: "", page_hash: "abc123de",
    },
    {
      domain: "stellamart.com", shopify: true, live_checkout: "GoKwik", live_confidence: 98,
      live_evidence: ["hits.gokwik.co/api/v1/events"], historical_checkouts: ["Shopflo"],
      has_kwikpass: true, emails: ["hello@stellamart.com", "care@stellamart.com"], phone_numbers: [],
      whatsapp_link: "", whatsapp_number: "", myshopify_domain: "stellamart.myshopify.com",
      socials: { linkedin: "", instagram: "https://instagram.com/stellamart", facebook: "", twitter: "", youtube: "" },
      tech_stack: ["Klaviyo", "Intercom", "Judge.me"],
      title: "Stella Mart — Women's Fashion", description: "Trendy clothing for modern women",
      lead_score: 89, priority: "HIGH", last_scan: "2026-06-07", status: "Contacted", notes: "Interested in pricing.", page_hash: "def456gh",
    },
    {
      domain: "modernduke.com", shopify: true, live_checkout: null, live_confidence: 0,
      live_evidence: [], historical_checkouts: ["Fastrr", "Razorpay"], has_kwikpass: false,
      emails: ["care@modernduke.com"], phone_numbers: ["+91 87654 32109"],
      whatsapp_link: "https://wa.me/918765432109", whatsapp_number: "+918765432109", myshopify_domain: "",
      socials: { linkedin: "", instagram: "https://instagram.com/modernduke", facebook: "", twitter: "", youtube: "" },
      tech_stack: ["Judge.me", "Hotjar"],
      title: "Modern Duke — Men's Fashion", description: "Premium apparel for men",
      lead_score: 68, priority: "MEDIUM", last_scan: "2026-06-07", status: "Not Contacted", notes: "", page_hash: "ghi789jk",
    },
  ];
}

async function loadData() {
  showLoader(true, "Loading merchant data…");
  setProgress(10, "Fetching results.json");
  try {
    await delay(200);
    setProgress(35, "Parsing records…");
    const res = await fetch(`${API_URL}/results.json`);
    if (!res.ok) throw new Error("Not found");
    const data = await res.json();
    if (!data?.length) throw new Error("Empty");
    leads = data;
    window.leads = leads;
    setProgress(90, "Building dashboard…");
    await delay(150);
    toast(`Loaded ${leads.length} merchants`);
  } catch {
    setProgress(70, "Using demo data…");
    leads = generateDemoData();
    window.leads = leads;
    await delay(150);
    toast(`Demo mode — ${leads.length} merchants`);
  }
  setProgress(100, "Ready");
  await delay(250);
  showLoader(false);

  loadWatchlist();
  populateFilters();
  updateStats();
  renderTable(leads);
  if (leads.length) showLead(leads[0]);
}

function delay(ms) { return new Promise((r) => setTimeout(r, ms)); }

// ─── Single Domain Scan ───────────────────────────────────────────────────────

async function scanSingleDomain(domainOverride) {
  const input = document.getElementById("singleScanInput");
  const btn = document.getElementById("singleScanBtn");
  const status = document.getElementById("scanStatus");

  const raw = typeof domainOverride === "string" ? domainOverride : input?.value || "";
  const domain = raw.replace(/^https?:\/\//i, "").replace(/\/$/, "").toLowerCase().trim();

  if (!domain) { toast("Enter a domain", "error"); return; }
  const validDomain = /^([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/i;
  if (!validDomain.test(domain)) { toast("Invalid domain format", "error"); return; }

  btn.disabled = true;
  btn.textContent = "…";
  status.textContent = "Scanning";
  toast(`Scanning ${domain}`, "info");

  let result = null;
  try {
    const res = await fetch(`${API_URL}/scan-domain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain }),
    });
    if (res.ok) result = await res.json();
  } catch {}

  if (!result || result.error) {
    try {
      const res = await fetch(`results.json?t=${Date.now()}`);
      if (res.ok) {
        const all = await res.json();
        result = all.find((r) => (r.domain || "").toLowerCase() === domain) || null;
      }
    } catch {}
  }

  if (result && !result.error) {
    const idx = leads.findIndex((l) => l.domain === result.domain);
    if (idx === -1) leads.unshift(result);
    else leads[idx] = result;
    window.leads = leads;
    populateFilters();
    updateStats();
    renderTable(leads);
    showLead(result);
    setTimeout(() => highlightRow(result.domain), 80);
    status.textContent = "Done";
    if (input) input.value = "";
    toast(`Scanned ${result.domain}`, "success");
  } else {
    status.textContent = "Not found";
    toast("Not found. Start API server or run full scan.", "error");
  }

  btn.disabled = false;
  btn.textContent = "Scan";
  setTimeout(() => { if (status.textContent !== "Done") status.textContent = ""; }, 3000);
}

async function checkApi() {
  const status = document.getElementById("scanStatus");
  try {
    const res = await fetch(`${API_URL}/health`);
    if (res.ok) { status.textContent = "API Online"; return; }
  } catch {}
  if (status) status.textContent = "API Offline";
}

// ─── Export ───────────────────────────────────────────────────────────────────

function exportCSV() {
  if (!leads.length) { toast("No leads to export"); return; }
  const rows = leads.map((l) => ({
    domain: l.domain, shopify: l.shopify ? "Yes" : "No",
    live_checkout: l.live_checkout || "", live_confidence: l.live_confidence || 0,
    historical: (l.historical_checkouts || []).join("; "),
    has_kwikpass: l.has_kwikpass ? "Yes" : "No",
    emails: (l.emails || []).join("; "), phones: (l.phone_numbers || []).join("; "),
    whatsapp_number: l.whatsapp_number || "", whatsapp_link: l.whatsapp_link || "",
    myshopify: l.myshopify_domain || "",
    linkedin: l.socials?.linkedin || "", instagram: l.socials?.instagram || "",
    facebook: l.socials?.facebook || "", tech_stack: (l.tech_stack || []).join("; "),
    lead_score: l.lead_score, priority: l.priority,
    status: localStorage.getItem(`fp_status_${l.domain}`) || l.status || "",
    last_scan: l.last_scan || "",
  }));

  const headers = Object.keys(rows[0]);
  const csv = [headers.join(","), ...rows.map((r) => headers.map((h) => JSON.stringify(r[h] ?? "")).join(","))].join("\n");
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })),
    download: `flexy-outreach-${new Date().toISOString().slice(0, 10)}.csv`,
  });
  a.click();
  URL.revokeObjectURL(a.href);
  toast("CSV exported");
}

// ─── Action helpers ───────────────────────────────────────────────────────────

function copyField(val, label = "Copied") {
  if (!val) { toast("Nothing to copy", "error"); return; }
  navigator.clipboard.writeText(val);
  toast(label);
}

let watchlistFilterActive = false;
function toggleWatchlistFilter() {
  watchlistFilterActive = !watchlistFilterActive;
  const btn = document.getElementById("watchlistFilterBtn");
  btn.classList.toggle("active", watchlistFilterActive);
  renderTable(watchlistFilterActive ? leads.filter((l) => watchlist.has(l.domain)) : leads);
}

// ─── Event Bindings ───────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  ["searchInput", "providerFilter", "historicalFilter", "priorityFilter", "sortFilter"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", () => renderTable(leads));
    document.getElementById(id)?.addEventListener("change", () => renderTable(leads));
  });

  document.getElementById("searchInput")?.addEventListener("keypress", (e) => {
    if (e.key === "Enter") {
      const v = e.target.value.trim();
      if (v.includes(".") && !v.includes(" ")) scanSingleDomain(v);
    }
  });

  document.getElementById("singleScanBtn")?.addEventListener("click", () => scanSingleDomain());
  document.getElementById("singleScanInput")?.addEventListener("keypress", (e) => {
    if (e.key === "Enter") scanSingleDomain();
  });

  document.getElementById("exportCSVBtn")?.addEventListener("click", exportCSV);
  document.getElementById("watchlistFilterBtn")?.addEventListener("click", toggleWatchlistFilter);

  document.getElementById("openWebsiteBtn")?.addEventListener("click", () => {
    if (selectedLead) window.open(domainUrl(selectedLead.domain), "_blank", "noopener,noreferrer");
  });
  document.getElementById("copyDomainBtn")?.addEventListener("click", () => copyField(selectedLead?.domain, "Domain copied"));
  document.getElementById("copyMyShopifyBtn")?.addEventListener("click", () => copyField(selectedLead?.myshopify_domain, "MyShopify copied"));
  document.getElementById("copyPhoneBtn")?.addEventListener("click", () => copyField(selectedLead?.phone_numbers?.[0], "Phone copied"));
  document.getElementById("copyWhatsAppBtn")?.addEventListener("click", () => {
    copyField(selectedLead?.whatsapp_number || selectedLead?.whatsapp_link, "WhatsApp copied");
  });
  document.getElementById("watchlistBtn")?.addEventListener("click", () => {
    if (selectedLead) toggleWatchlist(selectedLead.domain);
  });

  document.getElementById("templateType")?.addEventListener("change", () => {
    if (selectedLead) generateEmail(selectedLead);
  });
  document.getElementById("copyEmailBtn")?.addEventListener("click", () => {
    copyField(document.getElementById("emailPreview")?.value, "Email copied");
  });
  document.getElementById("resetTemplateBtn")?.addEventListener("click", () => {
    if (selectedLead) generateEmail(selectedLead);
  });

  document.getElementById("saveNotesBtn")?.addEventListener("click", () => {
    if (selectedLead) {
      localStorage.setItem(`fp_notes_${selectedLead.domain}`, document.getElementById("leadNotes").value);
      toast("Notes saved");
    }
  });
  document.getElementById("saveStatusBtn")?.addEventListener("click", () => {
    if (selectedLead) {
      const val = document.getElementById("leadStatus").value;
      localStorage.setItem(`fp_status_${selectedLead.domain}`, val);
      toast(`Status: ${val}`);
    }
  });

  checkApi();
  loadData();
});

// ─── Final state exposure for pages.js ────────────────────────────────────────

window.showLead = showLead;
window.renderTable = renderTable;
window.saveWatchlist = saveWatchlist;
window.toggleWatchlist = toggleWatchlist;