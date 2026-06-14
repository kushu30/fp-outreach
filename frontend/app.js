// app.js — FlexyPe Merchant Intelligence Platform

let leads = [];
let selectedLead = null;
let activeRow = null;
let watchlist = new Set();
let currentPage = 1;
let pageSize = 25;
let sortField = "score";
let sortOrder = "desc";
const API_URL = localStorage.getItem("fp_api_url") || "http://localhost:8080";
let attachmentBase64 = "";
let attachmentFileName = "";
let gmailStatus = { connected: false };

// Expose to pages.js
window.leads = leads;
window.watchlist = watchlist;
window.selectedLead = selectedLead;

// ─── Lucide Icon Snippets ─────────────────────────────────────────────────────
// Reused across the rendering pipeline. Defined once at module scope.

const ICONS = {
  check: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`,
  checkSmall: `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`,
  star: `<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`,
  starOutline: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`,
  starFilled: `<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`,
  mail: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>`,
  mailSmall: `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>`,
  phone: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>`,
  chat: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>`,
  tag: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>`,
  link: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
  alert: `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
};

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
  return String(str).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
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
function priorityClass(p) {
  return `priority-${(p || "low").toLowerCase()}`;
}
function priChipClass(p) {
  return `pri-${(p || "low").toLowerCase()}`;
}

// ─── Watchlist ─────────────────────────────────────────────────────────────

function loadWatchlist() {
  try {
    watchlist = new Set(
      JSON.parse(localStorage.getItem("fp_watchlist") || "[]"),
    );
  } catch {
    watchlist = new Set();
  }
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
  const live = new Set(),
    hist = new Set();
  leads.forEach((l) => {
    if (l.live_checkout) live.add(l.live_checkout);
    (l.historical_checkouts || []).forEach((p) => hist.add(p));
  });

  const liveEl = document.getElementById("providerFilter");
  liveEl.innerHTML = '<option value="">All providers</option>';
  [...live].sort().forEach((p) => {
    liveEl.insertAdjacentHTML(
      "beforeend",
      `<option value="${esc(p)}">${esc(p)}</option>`,
    );
  });

  const histEl = document.getElementById("historicalFilter");
  histEl.innerHTML = '<option value="">Historical</option>';
  [...hist].sort().forEach((p) => {
    histEl.insertAdjacentHTML(
      "beforeend",
      `<option value="${esc(p)}">${esc(p)}</option>`,
    );
  });
}

function updateStats() {
  document.getElementById("totalLeads").textContent = leads.length;
  document.getElementById("liveCount").textContent = leads.filter(
    (l) => l.live_checkout,
  ).length;
  document.getElementById("historicalOnlyCount").textContent = leads.filter(
    (l) => l.historical_checkouts?.length && !l.live_checkout,
  ).length;
  document.getElementById("hotLeads").textContent = leads.filter(
    (l) => l.lead_score >= 80,
  ).length;
  document.getElementById("kwikpassCount").textContent = leads.filter(
    (l) => l.has_kwikpass,
  ).length;
  document.getElementById("emailCount").textContent = leads.filter(
    (l) => l.emails?.length,
  ).length;
}

// ─── Table ────────────────────────────────────────────────────────────────────

function renderTable(data) {
  const tbody = document.querySelector("#leadTable tbody");
  const search = (
    document.getElementById("searchInput")?.value || ""
  ).toLowerCase();
  const liveProv = document.getElementById("providerFilter")?.value || "";
  const histProv = document.getElementById("historicalFilter")?.value || "";
  const priority = document.getElementById("priorityFilter")?.value || "";

  const filtered = data.filter(
    (l) =>
      l.domain.toLowerCase().includes(search) &&
      (!liveProv || l.live_checkout === liveProv) &&
      (!histProv || (l.historical_checkouts || []).includes(histProv)) &&
      (!priority || l.priority === priority),
  );

  // Apply sorting
  filtered.sort((a, b) => {
    // If one has an active unacknowledged competitor shift, prioritize it to the top!
    const hasShiftA = a.latest_change && a.latest_change.changes && !a.latest_change.acknowledged && 
      (a.latest_change.changes.checkout_providers || a.latest_change.changes.live_checkout);
    const hasShiftB = b.latest_change && b.latest_change.changes && !b.latest_change.acknowledged && 
      (b.latest_change.changes.checkout_providers || b.latest_change.changes.live_checkout);

    const isCompShiftA = hasShiftA && (function() {
      const p = a.latest_change.changes.checkout_providers || a.latest_change.changes.live_checkout;
      let o = Array.isArray(p.old) ? p.old.join(",") : String(p.old || "");
      let n = Array.isArray(p.new) ? p.new.join(",") : String(p.new || "");
      return ["gokwik", "shopflo", "razorpay", "fastrr", "ecom360", "cashfree", "flexype"].some(c => o.toLowerCase().includes(c)) && o.toLowerCase() !== n.toLowerCase();
    })();
    const isCompShiftB = hasShiftB && (function() {
      const p = b.latest_change.changes.checkout_providers || b.latest_change.changes.live_checkout;
      let o = Array.isArray(p.old) ? p.old.join(",") : String(p.old || "");
      let n = Array.isArray(p.new) ? p.new.join(",") : String(p.new || "");
      return ["gokwik", "shopflo", "razorpay", "fastrr", "ecom360", "cashfree", "flexype"].some(c => o.toLowerCase().includes(c)) && o.toLowerCase() !== n.toLowerCase();
    })();

    if (isCompShiftA && !isCompShiftB) return -1;
    if (!isCompShiftA && isCompShiftB) return 1;

    let valA = a[sortField];
    let valB = b[sortField];

    if (sortField === "priority") {
      const priorityMap = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1 };
      valA = priorityMap[a.priority] || 0;
      valB = priorityMap[b.priority] || 0;
    }

    if (typeof valA === "string") {
      return sortOrder === "asc" ?
          valA.localeCompare(valB)
        : valB.localeCompare(valA);
    } else {
      valA = valA || 0;
      valB = valB || 0;
      return sortOrder === "asc" ? valA - valB : valB - valA;
    }
  });

  // Pagination calculations
  const totalItems = filtered.length;
  const sizeNum = pageSize === "all" ? totalItems : parseInt(pageSize, 10);
  const totalPages = Math.ceil(totalItems / sizeNum) || 1;

  if (currentPage > totalPages) currentPage = totalPages;
  if (currentPage < 1) currentPage = 1;

  const startIdx = (currentPage - 1) * sizeNum;
  const endIdx = Math.min(startIdx + sizeNum, totalItems);

  const pageItems = filtered.slice(startIdx, endIdx);

  tbody.innerHTML = "";
  const dashSpan = `<span class="cell-dash">—</span>`;

  pageItems.forEach((lead) => {
    const isSel = selectedLead?.domain === lead.domain;
    const isWL = watchlist.has(lead.domain);
    const tr = document.createElement("tr");
    if (isSel) {
      tr.classList.add("active");
      activeRow = tr;
    }
    if (isWL) tr.classList.add("watchlisted");
    tr.setAttribute("data-domain", lead.domain);

    let changeAlert = "";
    if (lead.latest_change && lead.latest_change.changes) {
      const keys = Object.keys(lead.latest_change.changes);
      if (keys.includes("theme_id") || keys.includes("theme_family")) {
        changeAlert = `<span class="change-alert-text" title="Theme changed recently">${ICONS.alert}<span>Theme</span></span>`;
      } else if (
        keys.includes("checkout_providers") ||
        keys.includes("checkout_scripts") ||
        keys.includes("live_checkout")
      ) {
        changeAlert = `<span class="change-alert-text" title="Checkout changed recently">${ICONS.alert}<span>Checkout</span></span>`;
      } else {
        changeAlert = `<span class="change-alert-text" title="Fingerprint updated recently">${ICONS.alert}<span>Updated</span></span>`;
      }
    }

    let shiftTag = "";
    if (lead.latest_change && lead.latest_change.changes && !lead.latest_change.acknowledged) {
      const providerChange = lead.latest_change.changes.checkout_providers || lead.latest_change.changes.live_checkout;
      if (providerChange) {
        let oldVal = Array.isArray(providerChange.old) ? providerChange.old.join(", ") : String(providerChange.old || "");
        let newVal = Array.isArray(providerChange.new) ? providerChange.new.join(", ") : String(providerChange.new || "");
        
        const oldLower = oldVal.toLowerCase();
        const newLower = newVal.toLowerCase();
        
        const competitors = ["gokwik", "shopflo", "razorpay", "fastrr", "ecom360", "cashfree", "flexype"];
        const leftCompetitor = competitors.some(c => oldLower.includes(c));
        
        if (leftCompetitor && oldLower !== newLower) {
          const oldName = oldVal || "None";
          const newName = newVal || "None";
          shiftTag = `<span class="change-alert-text" style="background:var(--warning-soft);color:var(--warning-hover);border-color:var(--warning-border);" title="recently shifted from ${esc(oldName)} to ${esc(newName)}">recently shifted from ${esc(oldName)} to ${esc(newName)}</span>`;
        }
      }
    }

    const themeDisplay =
      lead.theme_family && lead.theme_family !== "unknown" ?
        lead.theme_family
      : "";

    const status =
      localStorage.getItem(`fp_status_${lead.domain}`) ||
      lead.status ||
      "Not Contacted";

    tr.innerHTML = `
      <td title="${esc(lead.domain)}">
        <div class="domain-cell-wrap">
          <div class="domain-main-name">
            <span class="domain-name-text">${esc(lead.domain)}</span>
            ${isWL ? `<span class="watchlist-star" aria-label="Watchlisted">${ICONS.star}</span>` : ""}
            ${
              status === "Contacted" || status === "Replied" ?
                `
              <span class="email-thread-shortcut" title="View email thread">
                ${ICONS.mailSmall}<span>Thread</span>
              </span>
            `
              : ""
            }
          </div>
          <div class="domain-sub-text">
            ${themeDisplay ? `<span>Theme: ${esc(themeDisplay)}</span>` : ""}
            ${changeAlert}
            ${shiftTag}
          </div>
        </div>
      </td>
      <td><span class="score-chip ${scoreClass(lead.lead_score)}">${lead.lead_score}</span></td>
      <td>${lead.live_checkout ? `<span class="live-chip">${esc(lead.live_checkout)}</span>` : dashSpan}</td>
      <td class="cell-icon">${lead.whatsapp_number ? ICONS.check : dashSpan}</td>
      <td class="cell-icon">${lead.phone_numbers?.length ? ICONS.check : dashSpan}</td>
      <td title="${esc(lead.myshopify_domain || "")}">${lead.myshopify_domain ? `<span class="cell-mono">${esc(lead.myshopify_domain)}</span>` : dashSpan}</td>
      <td><span class="pri-chip ${priChipClass(lead.priority)}">${esc(lead.priority)}</span></td>
    `;

    tr.querySelector(".email-thread-shortcut")?.addEventListener(
      "click",
      (e) => {
        e.stopPropagation();
        openEmailThreadModal({ domain: lead.domain, status: status });
      },
    );

    tr.addEventListener("click", () => {
      document
        .querySelectorAll("#leadTable tbody tr")
        .forEach((r) => r.classList.remove("active"));
      tr.classList.add("active");
      activeRow = tr;
      showLead(lead);
    });

    tbody.appendChild(tr);
  });

  // Update pagination footer UI
  document.getElementById("paginationInfoLabel").textContent =
    `Showing ${totalItems ? startIdx + 1 : 0}–${endIdx} of ${totalItems}`;
  document.getElementById("currentPageLabel").textContent = currentPage;
  document.getElementById("prevPageBtn").disabled = currentPage === 1;
  document.getElementById("nextPageBtn").disabled = currentPage === totalPages;

  document.getElementById("leadCount").textContent =
    `${filtered.length} / ${leads.length}`;
}

function getEmailTrustScore(email) {
  const e = email.toLowerCase();
  if (e.includes("noreply") || e.includes("no-reply") || e.includes("donotreply")) return -100;
  if (e.includes("privacy") || e.includes("abuse") || e.includes("jobs") || e.includes("careers") || e.includes("hr@")) return -50;
  if (e.startsWith("founder@") || e.startsWith("ceo@") || e.startsWith("coo@") || e.startsWith("owner@")) return 100;
  if (e.startsWith("hello@") || e.startsWith("contact@") || e.startsWith("info@")) return 50;
  if (e.startsWith("support@") || e.startsWith("care@") || e.startsWith("help@")) return 40;
  return 0;
}

// ─── Detail Panel ─────────────────────────────────────────────────────────────

function showLead(lead) {
  selectedLead = lead;
  window.selectedLead = lead;

  const detailPanel = document.getElementById("detailPanel");
  const resizer = document.getElementById("resizer");
  const leftPanel = document.getElementById("leftPanel");

  if (detailPanel) detailPanel.style.display = "flex";
  if (resizer) resizer.style.display = "block";
  if (
    leftPanel &&
    (leftPanel.style.width === "100%" || !leftPanel.style.width)
  ) {
    leftPanel.style.width = "48%";
  }

  document.getElementById("domainTitle").textContent = lead.domain;

  const meta = document.getElementById("merchantMeta");
  meta.innerHTML = `
    ${lead.shopify ? '<span class="shopify-tag">Shopify</span>' : ""}
    <span class="meta-date">Scanned ${esc(lead.last_scan || "—")}</span>
  `;

  const pb = document.getElementById("priorityBadge");
  pb.className = `priority-badge ${priorityClass(lead.priority)}`;
  pb.textContent = lead.priority || "";

  const isWatched = watchlist.has(lead.domain);
  const watchBtn = document.getElementById("watchlistBtn");
  watchBtn.classList.toggle("active", isWatched);
  watchBtn.innerHTML = `
    ${isWatched ? ICONS.starFilled : ICONS.starOutline}
    <span>${isWatched ? "Unwatch" : "Watch"}</span>
  `;

  const status =
    localStorage.getItem(`fp_status_${lead.domain}`) ||
    lead.status ||
    "Not Contacted";
  const viewThreadBtn = document.getElementById("viewThreadBtn");
  if (viewThreadBtn) {
    viewThreadBtn.hidden = !(status === "Contacted" || status === "Replied");
  }

  // Live Checkout
  const liveCard = document.getElementById("liveCheckoutCard");
  if (lead.live_checkout) {
    const evidence = (lead.live_evidence || [])
      .slice(0, 3)
      .map((u) => `<span class="evidence-url">${esc(u.split("?")[0])}</span>`)
      .join("");
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
  const histOnly = (lead.historical_checkouts || []).filter(
    (p) => p !== lead.live_checkout,
  );
  histEl.innerHTML =
    histOnly.length ?
      histOnly
        .map((p) => `<span class="badge badge-hist">${esc(p)}</span>`)
        .join("")
    : '<span class="badge-empty">None detected</span>';

  // Kwikpass
  document.getElementById("kwikpassStatus").innerHTML =
    lead.has_kwikpass ?
      '<span class="kp-yes">Kwikpass detected — Login/OTP enabled</span>'
    : '<span class="kp-no">Not detected</span>';

  // Contact
  const contactEl = document.getElementById("contactInfo");
  let contactHtml = "";

  if (lead.emails?.length) {
    const emailItems = lead.emails
      .map(
        (e) =>
          `<span class="contact-item"><a href="mailto:${esc(e)}" class="contact-link">${esc(e)}</a><button class="copy-mini" data-copy="${esc(e)}">Copy</button></span>`,
      )
      .join("");
    contactHtml += `<div class="contact-row"><span class="contact-icon">${ICONS.mail}</span><div class="contact-vals">${emailItems}</div></div>`;
  }

  if (lead.phone_numbers?.length) {
    const phoneItems = lead.phone_numbers
      .map(
        (p) =>
          `<span class="contact-item">${esc(p)}<button class="copy-mini" data-copy="${esc(p)}">Copy</button></span>`,
      )
      .join("");
    contactHtml += `<div class="contact-row"><span class="contact-icon">${ICONS.phone}</span><div class="contact-vals">${phoneItems}</div></div>`;
  }

  if (lead.whatsapp_number || lead.whatsapp_link) {
    const num = lead.whatsapp_number || "";
    const link = lead.whatsapp_link || "";
    contactHtml += `<div class="contact-row"><span class="contact-icon">${ICONS.chat}</span><div class="contact-vals">
      ${link ? `<a href="${esc(link)}" target="_blank" rel="noopener noreferrer" class="contact-link">WhatsApp</a>` : ""}
      ${num ? `<span class="contact-item">${esc(num)}<button class="copy-mini" data-copy="${esc(num)}">Copy</button></span>` : ""}
    </div></div>`;
  }

  if (lead.myshopify_domain) {
    contactHtml += `<div class="contact-row"><span class="contact-icon">${ICONS.tag}</span><div class="contact-vals">
      <span class="contact-item contact-item-mono">${esc(lead.myshopify_domain)}<button class="copy-mini" data-copy="${esc(lead.myshopify_domain)}">Copy</button></span>
    </div></div>`;
  }

  const socials = lead.socials || {};
  const socialLinks = [
    ["linkedin", "LinkedIn"],
    ["instagram", "Instagram"],
    ["facebook", "Facebook"],
    ["twitter", "Twitter"],
    ["youtube", "YouTube"],
  ]
    .filter(([k]) => socials[k])
    .map(
      ([k, label]) =>
        `<a href="${esc(socials[k])}" target="_blank" rel="noopener noreferrer" class="social-link">${label}</a>`,
    )
    .join("");

  if (socialLinks) {
    contactHtml += `<div class="contact-row"><span class="contact-icon">${ICONS.link}</span><div class="contact-vals">${socialLinks}</div></div>`;
  }

  contactEl.innerHTML =
    contactHtml ||
    '<span class="empty-msg">No contact information found</span>';

  contactEl.querySelectorAll(".copy-mini").forEach((btn) => {
    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(btn.dataset.copy || "");
      toast("Copied");
    });
  });

  // Tech Stack
  document.getElementById("techStackBadges").innerHTML =
    lead.tech_stack?.length ?
      lead.tech_stack
        .map((t) => `<span class="badge badge-tech">${esc(t)}</span>`)
        .join("")
    : '<span class="badge-empty">None detected</span>';

  // Merchant Info
  document.getElementById("merchantInfo").innerHTML = `
    <div class="info-row"><span class="info-key">Title</span><span class="info-val">${esc(lead.title || "—")}</span></div>
    <div class="info-row"><span class="info-key">Description</span><span class="info-val">${esc(lead.description || "—")}</span></div>
    <div class="info-row"><span class="info-key">Theme Family</span><span class="info-val">${esc(lead.theme_family || "—")}</span></div>
    <div class="info-row"><span class="info-key">Theme ID</span><span class="info-val info-mono">${esc(lead.theme_id || "—")}</span></div>
    <div class="info-row"><span class="info-key">Page Hash</span><span class="info-val info-mono">${esc(lead.page_hash || "—")}</span></div>
  `;

  // Evidence
  const evEl = document.getElementById("providerEvidence");
  evEl.innerHTML =
    lead.live_evidence?.length ?
      lead.live_evidence
        .map((u) => `<div class="evidence-item">${esc(u)}</div>`)
        .join("")
    : '<span class="empty-msg">No evidence available</span>';

  // Notes / Status
  const notesEl = document.getElementById("leadNotes");
  if (notesEl)
    notesEl.value =
      localStorage.getItem(`fp_notes_${lead.domain}`) || lead.notes || "";
  const statusEl = document.getElementById("leadStatus");
  if (statusEl)
    statusEl.value =
      localStorage.getItem(`fp_status_${lead.domain}`) ||
      lead.status ||
      "Not Contacted";

  // Show contacted metadata if recorded
  const contactedMetaEl = document.getElementById("contactedMeta");
  if (contactedMetaEl) {
    const contactedBy = lead.contacted_by;
    const contactedAt = lead.contacted_at;
    if (contactedBy && contactedAt) {
      try {
        const dateStr = new Date(contactedAt).toLocaleString();
        contactedMetaEl.innerHTML = `
          <div class="contacted-card">
            <span class="contacted-card-title">Outreach recorded</span>
            <span class="contacted-card-row">By <strong>${esc(contactedBy)}</strong></span>
            <span class="contacted-card-row contacted-card-time">${esc(dateStr)}</span>
          </div>
        `;
        contactedMetaEl.hidden = false;
      } catch (e) {
        contactedMetaEl.hidden = true;
      }
    } else {
      contactedMetaEl.hidden = true;
    }
  }

  // Populate TO recipient email and selector suggestions
  const emailToInput = document.getElementById("emailTo");
  const emailToSelect = document.getElementById("emailToSelect");
  if (emailToInput && emailToSelect) {
    emailToInput.value = "";
    emailToSelect.innerHTML = "";
    emailToSelect.style.display = "none";

    const emails = lead.emails || [];
    if (emails.length > 0) {
      // Sort by trust score (highest first)
      const sortedEmails = [...emails].sort((a, b) => getEmailTrustScore(b) - getEmailTrustScore(a));
      
      emailToInput.value = sortedEmails[0];

      if (emails.length > 1) {
        emailToSelect.style.display = "block";
        emailToSelect.innerHTML = `<option value="">Suggestions...</option>` + sortedEmails.map(
          (e) => `<option value="${esc(e)}">${esc(e)}</option>`
        ).join("");
      }
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
    el.innerHTML =
      '<span class="watchlist-empty">No merchants watchlisted</span>';
    return;
  }
  el.innerHTML = items
    .map(
      (l) => `
    <div class="watchlist-item" data-domain="${esc(l.domain)}">
      <div>
        <div class="wl-domain">${esc(l.domain)}</div>
        <div class="wl-meta">Score: ${l.lead_score} ${l.live_checkout ? `· <span class="wl-live">${esc(l.live_checkout)}</span>` : ""}</div>
      </div>
      <button class="wl-remove" data-domain="${esc(l.domain)}" title="Remove">×</button>
    </div>
  `,
    )
    .join("");

  el.querySelectorAll(".watchlist-item").forEach((item) => {
    item.addEventListener("click", (e) => {
      if (e.target.classList.contains("wl-remove")) return;
      const lead = leads.find((l) => l.domain === item.dataset.domain);
      if (lead) {
        showLead(lead);
        highlightRow(lead.domain);
      }
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
  document
    .querySelectorAll("#leadTable tbody tr")
    .forEach((r) => r.classList.remove("active"));
  const row = document.querySelector(
    `#leadTable tbody tr[data-domain="${domain}"]`,
  );
  if (row) {
    row.classList.add("active");
    activeRow = row;
    row.scrollIntoView({ block: "nearest" });
  }
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
      domain: "velvetaura.com",
      shopify: true,
      live_checkout: "GoKwik",
      live_confidence: 95,
      live_evidence: ["hits.gokwik.co/api/v1/events"],
      historical_checkouts: ["Shopflo", "Razorpay"],
      has_kwikpass: true,
      emails: ["hello@velvetaura.com", "founder@velvetaura.com"],
      phone_numbers: ["+91 98765 43210"],
      whatsapp_link: "https://wa.me/919876543210",
      whatsapp_number: "+919876543210",
      myshopify_domain: "velvetaura.myshopify.com",
      socials: {
        linkedin: "https://linkedin.com/company/velvetaura",
        instagram: "https://instagram.com/velvetaura",
        facebook: "",
        twitter: "",
        youtube: "",
      },
      tech_stack: ["Klaviyo", "Meta Pixel", "Google Analytics"],
      title: "Velvet Aura — Premium Beauty",
      description: "Luxury skincare and cosmetics brand",
      lead_score: 94,
      priority: "CRITICAL",
      last_scan: "2026-06-07",
      status: "Not Contacted",
      notes: "",
      page_hash: "abc123de",
    },
    {
      domain: "stellamart.com",
      shopify: true,
      live_checkout: "GoKwik",
      live_confidence: 98,
      live_evidence: ["hits.gokwik.co/api/v1/events"],
      historical_checkouts: ["Shopflo"],
      has_kwikpass: true,
      emails: ["hello@stellamart.com", "care@stellamart.com"],
      phone_numbers: [],
      whatsapp_link: "",
      whatsapp_number: "",
      myshopify_domain: "stellamart.myshopify.com",
      socials: {
        linkedin: "",
        instagram: "https://instagram.com/stellamart",
        facebook: "",
        twitter: "",
        youtube: "",
      },
      tech_stack: ["Klaviyo", "Intercom", "Judge.me"],
      title: "Stella Mart — Women's Fashion",
      description: "Trendy clothing for modern women",
      lead_score: 89,
      priority: "HIGH",
      last_scan: "2026-06-07",
      status: "Contacted",
      notes: "Interested in pricing.",
      page_hash: "def456gh",
    },
    {
      domain: "modernduke.com",
      shopify: true,
      live_checkout: null,
      live_confidence: 0,
      live_evidence: [],
      historical_checkouts: ["Fastrr", "Razorpay"],
      has_kwikpass: false,
      emails: ["care@modernduke.com"],
      phone_numbers: ["+91 87654 32109"],
      whatsapp_link: "https://wa.me/918765432109",
      whatsapp_number: "+918765432109",
      myshopify_domain: "",
      socials: {
        linkedin: "",
        instagram: "https://instagram.com/modernduke",
        facebook: "",
        twitter: "",
        youtube: "",
      },
      tech_stack: ["Judge.me", "Hotjar"],
      title: "Modern Duke — Men's Fashion",
      description: "Premium apparel for men",
      lead_score: 68,
      priority: "MEDIUM",
      last_scan: "2026-06-07",
      status: "Not Contacted",
      notes: "",
      page_hash: "ghi789jk",
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

  // Set default initial select or parameter-focused domain on load
  const params = new URLSearchParams(window.location.search);
  const focusDomain = params.get("focus");
  if (focusDomain) {
    const lead = leads.find(
      (l) => l.domain.toLowerCase() === focusDomain.toLowerCase(),
    );
    if (lead) {
      showLead(lead);
      setTimeout(() => {
        highlightRow(lead.domain);
        if (params.get("email") === "true") {
          const emailArea = document.getElementById("emailPreview");
          if (emailArea) {
            emailArea.focus();
            emailArea.scrollIntoView({ behavior: "smooth" });
          }
        }
      }, 150);
    }
  }
}

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ─── Single Domain Scan ───────────────────────────────────────────────────────

async function scanSingleDomain(domainOverride) {
  const input = document.getElementById("singleScanInput");
  const btn = document.getElementById("singleScanBtn");
  const status = document.getElementById("scanStatus");

  const raw =
    typeof domainOverride === "string" ? domainOverride : input?.value || "";
  const domain = raw
    .replace(/^https?:\/\//i, "")
    .replace(/\/$/, "")
    .toLowerCase()
    .trim();

  if (!domain) {
    toast("Enter a domain", "error");
    return;
  }
  const validDomain = /^([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/i;
  if (!validDomain.test(domain)) {
    toast("Invalid domain format", "error");
    return;
  }

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
        result =
          all.find((r) => (r.domain || "").toLowerCase() === domain) || null;
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
  setTimeout(() => {
    if (status.textContent !== "Done") status.textContent = "";
  }, 3000);
}

async function checkApi() {
  const status = document.getElementById("scanStatus");
  try {
    const res = await fetch(`${API_URL}/health`);
    if (res.ok) {
      status.textContent = "API Online";
      return;
    }
  } catch {}
  if (status) status.textContent = "API Offline";
}

// ─── Export ───────────────────────────────────────────────────────────────────

function exportCSV() {
  if (!leads.length) {
    toast("No leads to export");
    return;
  }
  const rows = leads.map((l) => ({
    domain: l.domain,
    shopify: l.shopify ? "Yes" : "No",
    live_checkout: l.live_checkout || "",
    live_confidence: l.live_confidence || 0,
    historical: (l.historical_checkouts || []).join("; "),
    has_kwikpass: l.has_kwikpass ? "Yes" : "No",
    emails: (l.emails || []).join("; "),
    phones: (l.phone_numbers || []).join("; "),
    whatsapp_number: l.whatsapp_number || "",
    whatsapp_link: l.whatsapp_link || "",
    myshopify: l.myshopify_domain || "",
    linkedin: l.socials?.linkedin || "",
    instagram: l.socials?.instagram || "",
    facebook: l.socials?.facebook || "",
    tech_stack: (l.tech_stack || []).join("; "),
    lead_score: l.lead_score,
    priority: l.priority,
    status: localStorage.getItem(`fp_status_${l.domain}`) || l.status || "",
    last_scan: l.last_scan || "",
  }));

  const headers = Object.keys(rows[0]);
  const csv = [
    headers.join(","),
    ...rows.map((r) =>
      headers.map((h) => JSON.stringify(r[h] ?? "")).join(","),
    ),
  ].join("\n");
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
  if (!val) {
    toast("Nothing to copy", "error");
    return;
  }
  navigator.clipboard.writeText(val);
  toast(label);
}

let watchlistFilterActive = false;
function toggleWatchlistFilter() {
  watchlistFilterActive = !watchlistFilterActive;
  const btn = document.getElementById("watchlistFilterBtn");
  btn.classList.toggle("active", watchlistFilterActive);
  renderTable(
    watchlistFilterActive ?
      leads.filter((l) => watchlist.has(l.domain))
    : leads,
  );
}

// ─── Drag & Drop Resizing Layouts ──────────────────────────────────────────────

function initResizer() {
  const resizer = document.getElementById("resizer");
  const leftPanel = document.getElementById("leftPanel");
  const splitContainer = resizer?.parentElement;

  if (!resizer || !leftPanel || !splitContainer) return;

  let isDragging = false;

  resizer.addEventListener("mousedown", (e) => {
    isDragging = true;
    resizer.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  document.addEventListener("mousemove", (e) => {
    if (!isDragging) return;

    const containerRect = splitContainer.getBoundingClientRect();
    const relativeX = e.clientX - containerRect.left;

    const minWidth = 250;
    const maxWidth = containerRect.width - minWidth;
    let newWidth = Math.max(minWidth, Math.min(relativeX, maxWidth));

    leftPanel.style.width = `${newWidth}px`;
  });

  document.addEventListener("mouseup", () => {
    if (isDragging) {
      isDragging = false;
      resizer.classList.remove("dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }
  });
}

function initColumnResizer(tableId) {
  const table =
    typeof tableId === "string" ? document.getElementById(tableId) : tableId;
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

    // Stop propagation of click events so that column sorting isn't triggered on dragging
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

      // Fix current widths for all th headers before starting resize
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

async function checkGmailStatus() {
  try {
    const email = window.FP_currentUser || "admin@flexype.in";
    const res = await fetch(`${API_URL}/api/auth/google/status`, {
      headers: { "X-User-Email": email },
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
      headers: { "X-User-Email": email },
    });
    if (res.ok) {
      const data = await res.json();
      if (data.authorize_url) {
        window.location.href = data.authorize_url;
      }
    }
  } catch (e) {
    toast("OAuth connection failed", "error");
  }
}

async function sendEmailViaGmail(lead) {
  const to = (document.getElementById("emailTo")?.value || "").trim();
  if (!to) {
    toast("Please specify a recipient email address", "error");
    return;
  }

  await checkGmailStatus();
  if (!gmailStatus.connected) {
    const ok = confirm("Gmail is not connected yet. Connect now?");
    if (ok) startGmailConnect();
    return;
  }
  const cc = document.getElementById("emailCc")?.value || "";
  const bcc = document.getElementById("emailBcc")?.value || "";
  const subject = `FlexyPe × ${lead.domain} — Quick chat?`;
  const rawBody = document.getElementById("emailPreview")?.value || "";

  const email = window.FP_currentUser || "admin@flexype.in";
  const userName = email.split("@")[0];
  const formattedUserName =
    userName.charAt(0).toUpperCase() + userName.slice(1);
  const body = rawBody
    .replaceAll("[Your Name]", formattedUserName)
    .replaceAll("[[PROVIDER]]", lead.live_checkout || "native checkout");

  const btn = document.getElementById("sendGmailBtn");
  if (btn) {
    btn.disabled = true;
    btn.dataset.orig = btn.innerHTML;
    btn.textContent = "Sending…";
  }

  try {
    const payload = {
      to,
      cc,
      bcc,
      subject,
      body,
      domain: lead.domain,
      attachment_data: attachmentBase64,
      attachment_name: attachmentFileName,
    };

    const res = await fetch(`${API_URL}/api/send-email`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Email": email,
      },
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));

    if (res.ok && data.ok) {
      toast(`Email sent to ${to}`, "success");
      lead.status = "Contacted";
      lead.contacted_by = data.contacted_by;
      lead.contacted_at = data.contacted_at;
      lead.contacted_to = data.contacted_to;
      lead.contacted_subject = data.contacted_subject;

      localStorage.setItem(`fp_status_${lead.domain}`, "Contacted");

      // Reset attachment state after successful send
      const attachmentInput = document.getElementById("emailAttachment");
      if (attachmentInput) attachmentInput.value = "";
      attachmentBase64 = "";
      attachmentFileName = "";
      const label = document.getElementById("attachmentName");
      if (label) label.textContent = "No file selected";
      const clearBtn = document.getElementById("clearAttachmentBtn");
      if (clearBtn) clearBtn.hidden = true;

      if (selectedLead?.domain === lead.domain) {
        showLead(lead);
      }
      renderTable(leads);
    } else if (data.code === "not_connected") {
      const ok = confirm("Gmail authorization expired. Reconnect now?");
      if (ok) startGmailConnect();
    } else {
      toast(data.error || "Send failed", "error");
    }
  } catch (e) {
    toast("Backend unreachable. Falling back to mail client…", "error");
    const mailto = `mailto:${encodeURIComponent(to)}?cc=${encodeURIComponent(cc)}&bcc=${encodeURIComponent(bcc)}&subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    window.location.href = mailto;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = btn.dataset.orig || "Send via Gmail";
    }
  }
}

async function openEmailThreadModal(emailLog) {
  const email = window.FP_currentUser || "admin@flexype.in";

  // Set default placeholders
  document.getElementById("modalEmailSubject").textContent =
    emailLog.subject || `Outreach Thread — ${emailLog.domain}`;
  document.getElementById("modalEmailTo").textContent = emailLog.to || "—";

  const ccRow = document.getElementById("modalCcRow");
  if (emailLog.cc) {
    document.getElementById("modalEmailCc").textContent = emailLog.cc;
    ccRow.style.display = "flex";
  } else {
    ccRow.style.display = "none";
  }

  document.getElementById("modalEmailFrom").textContent =
    emailLog.flexype_user || "—";
  document.getElementById("modalEmailDate").textContent =
    emailLog.sent_at ? new Date(emailLog.sent_at).toLocaleString() : "—";

  const status =
    localStorage.getItem(`fp_status_${emailLog.domain}`) ||
    emailLog.status ||
    "Not Contacted";
  const isReplied = status.toLowerCase() === "replied";
  document.getElementById("modalEmailStatus").innerHTML =
    isReplied ?
      `<span class="status-pill status-pill-replied">${ICONS.checkSmall}Replied</span>`
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
    const threadId = emailLog.gmail_thread_id || "";
    const domain = emailLog.domain || "";
    const gmailUserIndex = localStorage.getItem("fp_gmail_user_index") || "4";
    openInGmailBtn.onclick = () => {
      const url =
        threadId ?
          `https://mail.google.com/mail/u/${gmailUserIndex}/#sent/${threadId}`
        : `https://mail.google.com/mail/u/${gmailUserIndex}/#search/${encodeURIComponent(domain)}`;
      window.open(url, "_blank", "noopener,noreferrer");
    };
  }

  const modal = document.getElementById("emailViewModal");
  if (modal) {
    modal.hidden = false;
    modal.style.display = "flex";
    requestAnimationFrame(() => modal.classList.add("show"));
  }

  // Save the domain viewed for the Select Merchant action button
  const backToDashBtn = document.getElementById("modalEmailBackToDashboard");
  if (backToDashBtn) {
    backToDashBtn.dataset.domain = emailLog.domain;
  }

  try {
    const threadId = emailLog.gmail_thread_id || "";
    const res = await fetch(
      `${API_URL}/api/outreach/thread?thread_id=${threadId}&domain=${emailLog.domain}`,
      {
        headers: { "X-User-Email": email },
      },
    );
    if (res.ok) {
      const data = await res.json();
      if (data.ok && data.messages && data.messages.length) {
        if (data.thread_id && openInGmailBtn) {
          const gmailUserIndex =
            localStorage.getItem("fp_gmail_user_index") || "4";
          openInGmailBtn.onclick = () => {
            window.open(
              `https://mail.google.com/mail/u/${gmailUserIndex}/#sent/${data.thread_id}`,
              "_blank",
              "noopener,noreferrer",
            );
          };
        }
        const firstMsg = data.messages[0];
        document.getElementById("modalEmailSubject").textContent =
          firstMsg.subject ||
          emailLog.subject ||
          `Outreach Thread — ${emailLog.domain}`;
        document.getElementById("modalEmailTo").textContent =
          firstMsg.to || emailLog.to || "—";
        document.getElementById("modalEmailFrom").textContent =
          firstMsg.from || emailLog.flexype_user || "—";
        document.getElementById("modalEmailDate").textContent =
          firstMsg.date ||
          (emailLog.sent_at ?
            new Date(emailLog.sent_at).toLocaleString()
          : "—");

        let html = '<div class="email-thread-wrap">';
        data.messages.forEach((msg) => {
          const fromLower = (msg.from || "").toLowerCase();
          const isOutgoing =
            fromLower.includes("flexype.in") ||
            fromLower.includes("myselfkushu");
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
        html += "</div>";
        bodyContainer.innerHTML = html;
        return;
      }
    }
  } catch (e) {
    console.warn("Failed to load thread:", e);
  }

  bodyContainer.innerHTML = `<div class="modal-empty">No conversation details found. Send an email to initiate the thread.</div>`;
}

// ─── Event Bindings ───────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Authentication header sync and Sign out Modal Wiring
  const email = window.FP_currentUser || "admin@flexype.in";
  document.getElementById("userEmail").textContent = email;
  document.getElementById("userAvatar").textContent = email[0].toUpperCase();

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

  // Close details panel and return to Full View (100% width)
  document.getElementById("closeDetailBtn")?.addEventListener("click", () => {
    const detailPanel = document.getElementById("detailPanel");
    const resizer = document.getElementById("resizer");
    const leftPanel = document.getElementById("leftPanel");

    if (detailPanel) detailPanel.style.display = "none";
    if (resizer) resizer.style.display = "none";
    if (leftPanel) leftPanel.style.width = "100%";

    if (activeRow) {
      activeRow.classList.remove("active");
      activeRow = null;
    }
    selectedLead = null;
    window.selectedLead = null;
  });

  // Event bindings for filters
  [
    "searchInput",
    "providerFilter",
    "historicalFilter",
    "priorityFilter",
  ].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", () => {
      currentPage = 1;
      renderTable(leads);
    });
    document.getElementById(id)?.addEventListener("change", () => {
      currentPage = 1;
      renderTable(leads);
    });
  });

  // Table Sorter wiring
  document.querySelectorAll("th.sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const field = th.dataset.sort;
      if (sortField === field) {
        sortOrder = sortOrder === "asc" ? "desc" : "asc";
      } else {
        sortField = field;
        sortOrder = "desc";
      }

      // Update Arrow visuals in Headers
      document.querySelectorAll("th.sortable").forEach((el) => {
        const icon = el.querySelector(".sort-icon");
        if (el.dataset.sort === sortField) {
          icon.textContent = sortOrder === "asc" ? "↑" : "↓";
        } else {
          icon.textContent = "↕";
        }
      });
      renderTable(leads);
    });
  });

  // Pagination Footer wiring
  document.getElementById("pageSizeSelect")?.addEventListener("change", (e) => {
    pageSize = e.target.value;
    currentPage = 1;
    renderTable(leads);
  });
  document.getElementById("prevPageBtn")?.addEventListener("click", () => {
    if (currentPage > 1) {
      currentPage--;
      renderTable(leads);
    }
  });
  document.getElementById("nextPageBtn")?.addEventListener("click", () => {
    const sizeNum = pageSize === "all" ? leads.length : parseInt(pageSize, 10);
    const totalPages = Math.ceil(leads.length / sizeNum) || 1;
    if (currentPage < totalPages) {
      currentPage++;
      renderTable(leads);
    }
  });

  document.getElementById("searchInput")?.addEventListener("keypress", (e) => {
    if (e.key === "Enter") {
      const v = e.target.value.trim();
      if (v.includes(".") && !v.includes(" ")) scanSingleDomain(v);
    }
  });

  document
    .getElementById("singleScanBtn")
    ?.addEventListener("click", () => scanSingleDomain());
  document
    .getElementById("singleScanInput")
    ?.addEventListener("keypress", (e) => {
      if (e.key === "Enter") scanSingleDomain();
    });

  document.getElementById("exportCSVBtn")?.addEventListener("click", exportCSV);
  document
    .getElementById("watchlistFilterBtn")
    ?.addEventListener("click", toggleWatchlistFilter);

  document.getElementById("openWebsiteBtn")?.addEventListener("click", () => {
    if (selectedLead)
      window.open(
        domainUrl(selectedLead.domain),
        "_blank",
        "noopener,noreferrer",
      );
  });
  document
    .getElementById("copyDomainBtn")
    ?.addEventListener("click", () =>
      copyField(selectedLead?.domain, "Domain copied"),
    );
  document
    .getElementById("copyMyShopifyBtn")
    ?.addEventListener("click", () =>
      copyField(selectedLead?.myshopify_domain, "MyShopify copied"),
    );
  document
    .getElementById("copyPhoneBtn")
    ?.addEventListener("click", () =>
      copyField(selectedLead?.phone_numbers?.[0], "Phone copied"),
    );
  document.getElementById("copyWhatsAppBtn")?.addEventListener("click", () => {
    copyField(
      selectedLead?.whatsapp_number || selectedLead?.whatsapp_link,
      "WhatsApp copied",
    );
  });
  document.getElementById("watchlistBtn")?.addEventListener("click", () => {
    if (selectedLead) toggleWatchlist(selectedLead.domain);
  });

  document.getElementById("templateType")?.addEventListener("change", () => {
    if (selectedLead) generateEmail(selectedLead);
  });
  document.getElementById("emailToSelect")?.addEventListener("change", (e) => {
    const val = e.target.value;
    if (val) {
      const input = document.getElementById("emailTo");
      if (input) {
        const currentVal = input.value.trim();
        if (currentVal) {
          const emailsList = currentVal.split(",").map(item => item.trim());
          if (!emailsList.includes(val)) {
            input.value = currentVal + ", " + val;
          }
        } else {
          input.value = val;
        }
      }
      e.target.value = "";
    }
  });
  document.getElementById("copyEmailBtn")?.addEventListener("click", () => {
    copyField(document.getElementById("emailPreview")?.value, "Email copied");
  });
  document.getElementById("resetTemplateBtn")?.addEventListener("click", () => {
    if (selectedLead) generateEmail(selectedLead);
  });

  document.getElementById("saveNotesBtn")?.addEventListener("click", () => {
    if (selectedLead) {
      localStorage.setItem(
        `fp_notes_${selectedLead.domain}`,
        document.getElementById("leadNotes").value,
      );
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

  document.getElementById("viewThreadBtn")?.addEventListener("click", () => {
    if (selectedLead) {
      openEmailThreadModal({ domain: selectedLead.domain });
    }
  });

  const closeEmailModal = () => {
    const modal = document.getElementById("emailViewModal");
    if (modal) {
      modal.classList.remove("show");
      setTimeout(() => {
        modal.style.display = "none";
        modal.hidden = true;
      }, 180);
    }
  };
  document
    .getElementById("modalCloseEmailView")
    ?.addEventListener("click", closeEmailModal);
  document
    .getElementById("modalCloseEmailViewBtn")
    ?.addEventListener("click", closeEmailModal);
  document.getElementById("emailViewModal")?.addEventListener("click", (e) => {
    if (e.target.id === "emailViewModal") closeEmailModal();
  });

  document
    .getElementById("modalEmailBackToDashboard")
    ?.addEventListener("click", (e) => {
      const domain = e.target.dataset.domain;
      closeEmailModal();
      if (domain) {
        const lead = leads.find(
          (l) => l.domain.toLowerCase() === domain.toLowerCase(),
        );
        if (lead) {
          showLead(lead);
          highlightRow(lead.domain);
        }
      }
    });

  document.getElementById("sendGmailBtn")?.addEventListener("click", () => {
    if (selectedLead) sendEmailViaGmail(selectedLead);
    else toast("Select a merchant first", "error");
  });

  document
    .getElementById("emailAttachment")
    ?.addEventListener("change", (e) => {
      const file = e.target.files[0];
      const label = document.getElementById("attachmentName");
      const clearBtn = document.getElementById("clearAttachmentBtn");

      if (!file) {
        attachmentBase64 = "";
        attachmentFileName = "";
        if (label) label.textContent = "No file selected";
        if (clearBtn) clearBtn.hidden = true;
        return;
      }

      attachmentFileName = file.name;
      if (label) label.textContent = file.name;
      if (clearBtn) clearBtn.hidden = false;

      const reader = new FileReader();
      reader.onload = function (evt) {
        const dataUrl = evt.target.result;
        attachmentBase64 = dataUrl.split(",")[1];
      };
      reader.readAsDataURL(file);
    });

  document
    .getElementById("clearAttachmentBtn")
    ?.addEventListener("click", () => {
      const input = document.getElementById("emailAttachment");
      if (input) input.value = "";
      attachmentBase64 = "";
      attachmentFileName = "";

      const label = document.getElementById("attachmentName");
      if (label) label.textContent = "No file selected";

      const clearBtn = document.getElementById("clearAttachmentBtn");
      if (clearBtn) clearBtn.hidden = true;
    });

  // ─── CC Chip Quick-fill ─────────────────────────────────────────────────────
  document.querySelectorAll(".cc-chip:not(.cc-chip-clear)").forEach((chip) => {
    chip.addEventListener("click", () => {
      const ccInput = document.getElementById("emailCc");
      if (!ccInput) return;
      const email = chip.dataset.email;
      const current = ccInput.value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (current.includes(email)) {
        // Remove if already added
        ccInput.value = current.filter((e) => e !== email).join(", ");
        chip.classList.remove("active");
      } else {
        current.push(email);
        ccInput.value = current.join(", ");
        chip.classList.add("active");
      }
    });
  });

  document.getElementById("clearCcBtn")?.addEventListener("click", () => {
    const ccInput = document.getElementById("emailCc");
    if (ccInput) ccInput.value = "";
    document
      .querySelectorAll(".cc-chip:not(.cc-chip-clear)")
      .forEach((c) => c.classList.remove("active"));
  });

  initResizer();
  initColumnResizer("leadTable");

  checkApi();
  loadData();
});

// ─── Final state exposure for pages.js ────────────────────────────────────────

window.showLead = showLead;
window.renderTable = renderTable;
window.saveWatchlist = saveWatchlist;
window.toggleWatchlist = toggleWatchlist;
window.initColumnResizer = initColumnResizer;
