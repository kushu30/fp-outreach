// app.js - Merchant Intelligence Platform v2

let leads = [];
let selectedLead = null;
let activeRow = null;
let watchlist = new Set();

const emailTemplates = {
  intro: `Hello Team,

I am reaching out from FlexyPe Outreach. We help Shopify brands optimize their checkout experience for higher conversion rates.

A few things our merchants appreciate:
- Fast implementation (under 2 hours)
- 20-30% increase in checkout completion
- Dedicated support team
- No code changes required

Would you be open to a 15-minute call to discuss your current setup?

Best regards,
[Your Name]
FlexyPe Outreach`,

  competitor: `Hello Team,

I noticed you are currently using [[PROVIDER]] for checkout.

FlexyPe Outreach offers a modern alternative that typically outperforms [[PROVIDER]] on:
- Conversion rates (25% higher)
- Transaction fees (lower)
- Load time (2x faster)
- Migration support (seamless)

Would you be open to a quick comparison demo?

Best regards,
[Your Name]
FlexyPe Outreach`,

  migration: `Hello Team,

If your team is evaluating checkout solutions for 2026, FlexyPe Outreach should be on your shortlist.

We specialize in:
- Zero-downtime migration
- Custom checkout flows
- Subscription support
- Multi-currency

Happy to share case studies from similar merchants.

Best regards,
[Your Name]
FlexyPe Outreach`,

  partnership: `Hello Team,

FlexyPe Outreach is looking to partner with high-growth Shopify brands.

Benefits include:
- Revenue share program
- Priority support
- Early access to features
- Co-marketing opportunities

Let's schedule a quick call to explore.

Best regards,
[Your Name]
FlexyPe Outreach`,

  feature: `Hello Team,

FlexyPe Outreach has launched new features that could benefit your store:
- One-click upsells
- Post-purchase offers
- Abandoned cart recovery
- Analytics dashboard

Average AOV increase: 35%

Interested in a personalized demo?

Best regards,
[Your Name]
FlexyPe Outreach`,
};

function getDomainUrl(domain) {
  if (!domain) return "";
  if (domain.startsWith("http://") || domain.startsWith("https://")) {
    return domain;
  }
  return `https://${domain}`;
}

function showToast(message, type = "info") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = `toast toast-${type}`;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 3000);
}

function showLoader(show = true, text = "Loading...") {
  const overlay = document.getElementById("loaderOverlay");
  const loaderText = document.getElementById("loaderText");
  if (overlay) {
    overlay.style.display = show ? "flex" : "none";
  }
  if (loaderText && text) {
    loaderText.textContent = text;
  }
}

function updateLoaderProgress(percent, status) {
  const progressBar = document.getElementById("loaderProgressBar");
  const loaderStatus = document.getElementById("loaderStatus");
  if (progressBar) progressBar.style.width = `${percent}%`;
  if (loaderStatus && status) loaderStatus.textContent = status;
}

function loadWatchlist() {
  const saved = localStorage.getItem("flexype_watchlist");
  if (saved) {
    watchlist = new Set(JSON.parse(saved));
  }
  updateWatchlistUI();
}

function saveWatchlist() {
  localStorage.setItem("flexype_watchlist", JSON.stringify([...watchlist]));
  updateWatchlistUI();
}

function toggleWatchlist(domain) {
  if (watchlist.has(domain)) {
    watchlist.delete(domain);
    showToast(`Removed ${domain} from watchlist`);
  } else {
    watchlist.add(domain);
    showToast(`Added ${domain} to watchlist`);
  }
  saveWatchlist();
  renderTable(leads);
}

function updateWatchlistUI() {
  const container = document.getElementById("watchlistItems");
  if (!container) return;

  const watchlistLeads = leads.filter((lead) => watchlist.has(lead.domain));
  if (watchlistLeads.length === 0) {
    container.innerHTML =
      '<div class="watchlist-empty">No merchants in watchlist</div>';
    return;
  }

  container.innerHTML = watchlistLeads
    .map(
      (lead) => `
    <div class="watchlist-item" data-domain="${lead.domain}">
      <div class="watchlist-info">
        <div class="watchlist-domain">${lead.domain}</div>
        <div class="watchlist-meta">
          <span class="watchlist-score">Score: ${lead.lead_score}</span>
          ${lead.live_checkout ? `<span class="watchlist-live">${lead.live_checkout}</span>` : ""}
        </div>
      </div>
      <button class="watchlist-remove" data-domain="${lead.domain}" aria-label="Remove from watchlist">×</button>
    </div>
  `,
    )
    .join("");

  document.querySelectorAll(".watchlist-item").forEach((item) => {
    item.addEventListener("click", (e) => {
      if (!e.target.classList.contains("watchlist-remove")) {
        const domain = item.dataset.domain;
        const lead = leads.find((l) => l.domain === domain);
        if (lead) showLead(lead);
        const row = document.querySelector(
          `#leadTable tbody tr[data-domain="${domain}"]`,
        );
        if (row) {
          document
            .querySelectorAll("#leadTable tbody tr")
            .forEach((r) => r.classList.remove("active"));
          row.classList.add("active");
          activeRow = row;
        }
      }
    });
  });

  document.querySelectorAll(".watchlist-remove").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const domain = btn.dataset.domain;
      watchlist.delete(domain);
      saveWatchlist();
      renderTable(leads);
      if (selectedLead && selectedLead.domain === domain) {
        const newSelected = leads.find((l) => l.domain !== domain);
        if (newSelected) showLead(newSelected);
      }
    });
  });
}

async function scanSingleDomain() {
  const input = document.getElementById("singleScanInput");
  const btn = document.getElementById("singleScanBtn");
  const statusEl = document.getElementById("scanStatus");
  const domain = input.value.trim().toLowerCase();

  if (!domain) {
    showToast("Please enter a domain", "error");
    return;
  }

  // Validate domain
  const domainRegex =
    /^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$/;
  const cleanDomain = domain.replace(/^https?:\/\//, "");
  if (!domainRegex.test(cleanDomain)) {
    showToast("Invalid domain format", "error");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Scanning...";
  statusEl.textContent = "Running scanner...";
  showToast(`Scanning ${cleanDomain}...`, "info");

  try {
    const response = await fetch("http://localhost:8080/scan-domain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: cleanDomain }),
    });

    if (!response.ok) throw new Error("Server error");

    const result = await response.json();

    if (result && !result.error) {
      const existingIndex = leads.findIndex((l) => l.domain === result.domain);
      if (existingIndex === -1) {
        leads.unshift(result);
        showToast(`Added ${result.domain}`, "success");
      } else {
        leads[existingIndex] = result;
        showToast(`Updated ${result.domain}`, "success");
      }

      populateFilters();
      updateStats();
      renderTable(leads);
      showLead(result);

      statusEl.textContent = "Complete";
      input.value = "";

      setTimeout(() => {
        const rows = document.querySelectorAll("#leadTable tbody tr");
        rows.forEach((row) => {
          if (row.textContent.includes(result.domain)) {
            row.scrollIntoView({ behavior: "smooth", block: "center" });
            row.click();
          }
        });
      }, 100);
    } else {
      throw new Error("No data received");
    }
  } catch (error) {
    console.error("Scan failed:", error);
    showToast(
      `Scan failed. Make sure server is running: python server.py`,
      "error",
    );
    statusEl.textContent = "Failed";
  } finally {
    btn.disabled = false;
    btn.textContent = "Scan Domain";
    setTimeout(() => {
      if (statusEl.textContent !== "Complete") {
        statusEl.textContent = "";
      }
    }, 3000);
  }
}

async function checkApiServer() {
  try {
    const response = await fetch("http://localhost:8080/health", {
      method: "GET",
      headers: { "Content-Type": "application/json" },
    });
    if (response.ok) {
      document.getElementById("scanStatus").textContent = "Ready";
      return true;
    }
  } catch (e) {
    document.getElementById("scanStatus").textContent = "Server offline";
  }
  return false;
}

async function loadData() {
  showLoader(true, "Loading merchant data...");
  updateLoaderProgress(10, "Reading results.json");

  try {
    await new Promise((resolve) => setTimeout(resolve, 300));
    updateLoaderProgress(30, "Fetching merchant data...");

    const response = await fetch("results.json");
    if (!response.ok) throw new Error("results.json not found");

    updateLoaderProgress(60, "Processing merchant records...");
    const data = await response.json();

    if (data && data.length > 0) {
      leads = data;
      updateLoaderProgress(90, "Building dashboard...");
      await new Promise((resolve) => setTimeout(resolve, 200));
      showToast(`Loaded ${leads.length} merchants`);
    } else {
      throw new Error("Empty data");
    }
  } catch (err) {
    console.warn("Using demo data:", err.message);
    updateLoaderProgress(70, "Using demonstration data...");
    leads = generateDemoData();
    await new Promise((resolve) => setTimeout(resolve, 200));
    showToast(`Demo mode: ${leads.length} merchants`);
  }

  updateLoaderProgress(100, "Ready");
  await new Promise((resolve) => setTimeout(resolve, 300));
  showLoader(false);

  loadWatchlist();
  populateFilters();
  updateStats();
  renderTable(leads);

  if (leads.length) {
    showLead(leads[0]);
  }
}

function generateDemoData() {
  return [
    {
      domain: "velvetaura.com",
      shopify: true,
      live_checkout: "GoKwik",
      live_confidence: 95,
      historical_checkouts: ["Shopflo", "Razorpay"],
      has_kwikpass: true,
      emails: ["hello@velvetaura.com", "founder@velvetaura.com"],
      socials: {
        linkedin: "https://linkedin.com/company/velvetaura",
        instagram: "https://instagram.com/velvetaura",
        facebook: "https://facebook.com/velvetaura",
        twitter: "",
        youtube: "",
      },
      tech_stack: ["Klaviyo", "Meta Pixel", "Google Analytics"],
      title: "Velvet Aura - Premium Beauty Products",
      description: "Luxury skincare and cosmetics brand",
      lead_score: 94,
      priority: "CRITICAL",
      last_scan: "2026-06-07",
      status: "Not Contacted",
      notes: "",
    },
    {
      domain: "stellamart.com",
      shopify: true,
      live_checkout: "GoKwik",
      live_confidence: 98,
      historical_checkouts: ["Shopflo"],
      has_kwikpass: true,
      emails: ["hello@stellamart.com", "care@stellamart.com"],
      socials: {
        linkedin: "https://linkedin.com/company/stellamart",
        instagram: "https://instagram.com/stellamart",
        facebook: "https://facebook.com/stellamart",
        twitter: "",
        youtube: "",
      },
      tech_stack: ["Klaviyo", "Intercom", "Judge.me"],
      title: "Stella Mart - Women's Fashion",
      description: "Trendy clothing and accessories for modern women",
      lead_score: 89,
      priority: "HIGH",
      last_scan: "2026-06-07",
      status: "Contacted",
      notes: "Spoke with marketing team. Interested in pricing.",
    },
    {
      domain: "modernduke.com",
      shopify: true,
      live_checkout: null,
      live_confidence: 0,
      historical_checkouts: ["Fastrr", "Razorpay", "ecom360"],
      has_kwikpass: false,
      emails: ["care@modernduke.com", "support@modernduke.com"],
      socials: {
        linkedin: "",
        instagram: "https://instagram.com/modernduke",
        facebook: "https://facebook.com/modernduke",
        twitter: "",
        youtube: "",
      },
      tech_stack: ["Judge.me", "Hotjar", "Microsoft Clarity"],
      title: "Modern Duke - Men's Fashion",
      description: "Premium apparel and accessories for men",
      lead_score: 68,
      priority: "MEDIUM",
      last_scan: "2026-06-07",
      status: "Not Contacted",
      notes: "",
    },
  ];
}

function populateFilters() {
  const liveProviders = new Set();
  const historicalProviders = new Set();

  leads.forEach((lead) => {
    if (lead.live_checkout) liveProviders.add(lead.live_checkout);
    if (lead.historical_checkouts) {
      lead.historical_checkouts.forEach((p) => historicalProviders.add(p));
    }
  });

  const liveFilter = document.getElementById("providerFilter");
  liveFilter.innerHTML = '<option value="">All Live Providers</option>';
  [...liveProviders].sort().forEach((p) => {
    const option = document.createElement("option");
    option.value = p;
    option.textContent = p;
    liveFilter.appendChild(option);
  });

  const historicalFilter = document.getElementById("historicalFilter");
  historicalFilter.innerHTML = '<option value="">Historical Checkouts</option>';
  [...historicalProviders].sort().forEach((p) => {
    const option = document.createElement("option");
    option.value = p;
    option.textContent = p;
    historicalFilter.appendChild(option);
  });
}

function updateStats() {
  document.getElementById("totalLeads").innerText = leads.length;
  document.getElementById("liveCount").innerText = leads.filter(
    (l) => l.live_checkout,
  ).length;
  document.getElementById("historicalOnlyCount").innerText = leads.filter(
    (l) =>
      l.historical_checkouts &&
      l.historical_checkouts.length > 0 &&
      !l.live_checkout,
  ).length;
  document.getElementById("hotLeads").innerText = leads.filter(
    (l) => l.lead_score >= 80,
  ).length;
  document.getElementById("kwikpassCount").innerText = leads.filter(
    (l) => l.has_kwikpass,
  ).length;
  document.getElementById("emailCount").innerText = leads.filter(
    (l) => l.emails && l.emails.length > 0,
  ).length;
}

function updateLeadCount() {
  const visibleRows = document.querySelectorAll(
    "#leadTable tbody tr:not(.hidden)",
  ).length;
  document.getElementById("leadCount").innerText =
    `${visibleRows} / ${leads.length}`;
}

function getScoreClass(score) {
  if (score >= 80) return "score-critical";
  if (score >= 65) return "score-high";
  if (score >= 50) return "score-medium";
  return "score-low";
}

function renderTable(data) {
  const tbody = document.querySelector("#leadTable tbody");
  tbody.innerHTML = "";

  const search = document.getElementById("searchInput").value.toLowerCase();
  const liveProvider = document.getElementById("providerFilter").value;
  const historicalProvider = document.getElementById("historicalFilter").value;
  const priority = document.getElementById("priorityFilter").value;
  const sortBy = document.getElementById("sortFilter")?.value || "score";

  const filtered = data.filter((lead) => {
    const matchesSearch = lead.domain.toLowerCase().includes(search);
    const matchesLive = !liveProvider || lead.live_checkout === liveProvider;
    const matchesHistorical =
      !historicalProvider ||
      (lead.historical_checkouts &&
        lead.historical_checkouts.includes(historicalProvider));
    const matchesPriority = !priority || lead.priority === priority;
    return matchesSearch && matchesLive && matchesHistorical && matchesPriority;
  });

  if (sortBy === "domain") {
    filtered.sort((a, b) => a.domain.localeCompare(b.domain));
  } else {
    filtered.sort((a, b) => b.lead_score - a.lead_score);
  }

  filtered.forEach((lead) => {
    const row = document.createElement("tr");
    row.setAttribute("data-domain", lead.domain);
    if (selectedLead && selectedLead.domain === lead.domain) {
      row.classList.add("active");
      activeRow = row;
    }
    if (watchlist.has(lead.domain)) {
      row.classList.add("watchlisted");
    }

    const historicalCount =
      lead.historical_checkouts ? lead.historical_checkouts.length : 0;

    row.innerHTML = `
      <td><strong>${escapeHtml(lead.domain)}</strong>${watchlist.has(lead.domain) ? ' <span class="watchlist-star">⭐</span>' : ""}</td>
      <td>${lead.live_checkout ? `<span class="badge badge-live">${escapeHtml(lead.live_checkout)}</span>` : '<span class="badge badge-muted">—</span>'}</td>
      <td>${historicalCount > 0 ? `<span class="badge badge-historical">${historicalCount}</span>` : "—"}</td>
      <td><span class="score-badge ${getScoreClass(lead.lead_score)}">${lead.lead_score}</span></td>
      <td><span class="priority-badge priority-${lead.priority.toLowerCase()}">${lead.priority}</span></td>
    `;

    row.addEventListener("click", () => {
      if (activeRow) activeRow.classList.remove("active");
      row.classList.add("active");
      activeRow = row;
      showLead(lead);
    });

    tbody.appendChild(row);
  });

  updateLeadCount();
  updateWatchlistUI();
}

function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/[&<>]/g, function (m) {
    if (m === "&") return "&amp;";
    if (m === "<") return "&lt;";
    if (m === ">") return "&gt;";
    return m;
  });
}

function showLead(lead) {
  selectedLead = lead;

  document.getElementById("domainTitle").innerText = lead.domain;
  document.getElementById("merchantMeta").innerHTML = `
    ${lead.shopify ? '<span class="shopify-badge">Shopify</span>' : '<span class="custom-badge">Custom</span>'}
    <span class="meta-text">Last scan: ${lead.last_scan || "Unknown"}</span>
  `;

  const priority = lead.priority;
  const badge = document.getElementById("priorityBadge");
  badge.className = `priority-badge priority-${priority.toLowerCase()}`;
  badge.innerText = priority;

  const isWatchlisted = watchlist.has(lead.domain);
  document.getElementById("watchlistBtn").innerHTML =
    isWatchlisted ? "Remove from Watchlist" : "Add to Watchlist";

  // Live Checkout Card
  const liveCard = document.getElementById("liveCheckoutCard");
  if (lead.live_checkout) {
    liveCard.innerHTML = `
      <div class="live-checkout-header">
        <span class="live-checkout-name">${escapeHtml(lead.live_checkout)}</span>
        <span class="live-confidence-badge">${lead.live_confidence}% confidence</span>
      </div>
      <div class="live-checkout-evidence">
        <div class="evidence-label">Network evidence:</div>
        <div class="evidence-list">
          ${
            lead.live_evidence && lead.live_evidence.length ?
              lead.live_evidence
                .slice(0, 3)
                .map(
                  (e) =>
                    `<code class="evidence-code">${escapeHtml(e.split("?")[0])}</code>`,
                )
                .join("")
            : '<code class="evidence-code">Network request detected</code>'
          }
        </div>
      </div>
    `;
  } else {
    liveCard.innerHTML =
      '<div class="live-checkout-empty">No live checkout detected</div>';
  }

  // Historical Checkouts
  const historicalContainer = document.getElementById("historicalBadges");
  if (lead.historical_checkouts && lead.historical_checkouts.length > 0) {
    historicalContainer.innerHTML = lead.historical_checkouts
      .map(
        (p) => `<span class="badge badge-historical">${escapeHtml(p)}</span>`,
      )
      .join("");
  } else {
    historicalContainer.innerHTML =
      '<div class="empty-message">No historical checkouts found</div>';
  }

  // Kwikpass
  const kwikpassDiv = document.getElementById("kwikpassStatus");
  if (lead.has_kwikpass) {
    kwikpassDiv.innerHTML =
      '<span class="kwikpass-badge kwikpass-yes">Kwikpass detected (Login/OTP enabled)</span>';
  } else {
    kwikpassDiv.innerHTML =
      '<span class="kwikpass-badge kwikpass-no">Not detected</span>';
  }

  // Contact Information
  const contactDiv = document.getElementById("contactInfo");
  let contactHtml = "";
  if (lead.emails && lead.emails.length) {
    contactHtml += `<div class="contact-row">
      <span class="contact-icon">📧</span>
      <div class="contact-values">${lead.emails.map((e) => `<a href="mailto:${escapeHtml(e)}" class="contact-link">${escapeHtml(e)}</a>`).join(", ")}</div>
    </div>`;
  }
  if (lead.socials) {
    const socialLinks = [];
    if (lead.socials.linkedin)
      socialLinks.push(
        `<a href="${escapeHtml(lead.socials.linkedin)}" target="_blank" class="social-link" rel="noopener noreferrer">LinkedIn</a>`,
      );
    if (lead.socials.instagram)
      socialLinks.push(
        `<a href="${escapeHtml(lead.socials.instagram)}" target="_blank" class="social-link" rel="noopener noreferrer">Instagram</a>`,
      );
    if (lead.socials.facebook)
      socialLinks.push(
        `<a href="${escapeHtml(lead.socials.facebook)}" target="_blank" class="social-link" rel="noopener noreferrer">Facebook</a>`,
      );
    if (lead.socials.twitter)
      socialLinks.push(
        `<a href="${escapeHtml(lead.socials.twitter)}" target="_blank" class="social-link" rel="noopener noreferrer">Twitter</a>`,
      );
    if (socialLinks.length) {
      contactHtml += `<div class="contact-row">
        <span class="contact-icon">🌐</span>
        <div class="contact-values">${socialLinks.join(" · ")}</div>
      </div>`;
    }
  }
  if (!contactHtml)
    contactHtml =
      '<div class="empty-message">No contact information available</div>';
  contactDiv.innerHTML = contactHtml;

  // Technology Stack
  const techDiv = document.getElementById("techStackBadges");
  if (lead.tech_stack && lead.tech_stack.length) {
    techDiv.innerHTML = lead.tech_stack
      .map((t) => `<span class="badge badge-tech">${escapeHtml(t)}</span>`)
      .join("");
  } else {
    techDiv.innerHTML =
      '<div class="empty-message">No technology detected</div>';
  }

  // Merchant Info
  const infoDiv = document.getElementById("merchantInfo");
  infoDiv.innerHTML = `
    <div class="info-row">
      <span class="info-label">Title</span>
      <span class="info-value">${escapeHtml(lead.title || "N/A")}</span>
    </div>
    <div class="info-row">
      <span class="info-label">Description</span>
      <span class="info-value">${escapeHtml(lead.description || "N/A")}</span>
    </div>
    <div class="info-row">
      <span class="info-label">Page Hash</span>
      <span class="info-value mono">${escapeHtml(lead.page_hash || "N/A")}</span>
    </div>
  `;

  // Provider Evidence
  const evidenceDiv = document.getElementById("providerEvidence");
  if (lead.live_evidence && lead.live_evidence.length) {
    evidenceDiv.innerHTML = lead.live_evidence
      .map(
        (url) =>
          `<div class="evidence-item"><code class="evidence-code">${escapeHtml(url)}</code></div>`,
      )
      .join("");
  } else {
    evidenceDiv.innerHTML =
      '<div class="empty-message">No evidence available</div>';
  }

  // Notes and Status
  const notesEl = document.getElementById("leadNotes");
  const statusEl = document.getElementById("leadStatus");
  if (notesEl)
    notesEl.value =
      localStorage.getItem(`flexype_notes_${lead.domain}`) || lead.notes || "";
  if (statusEl)
    statusEl.value =
      localStorage.getItem(`flexype_status_${lead.domain}`) ||
      lead.status ||
      "Not Contacted";

  // Generate email preview
  generateEmailPreview(lead);
}

function generateEmailPreview(lead) {
  const type = document.getElementById("templateType").value;
  let email = emailTemplates[type] || emailTemplates.intro;

  if (type === "competitor" && lead.live_checkout) {
    email = email.replace("[[PROVIDER]]", lead.live_checkout);
  }

  document.getElementById("emailPreview").value = email;
}

function updateEmailTemplate() {
  if (selectedLead) {
    generateEmailPreview(selectedLead);
  }
}

function copyEmail() {
  const emailContent = document.getElementById("emailPreview").value;
  navigator.clipboard.writeText(emailContent);
  showToast("Email copied to clipboard");
}

function resetEmailTemplate() {
  if (selectedLead) {
    generateEmailPreview(selectedLead);
    showToast("Template reset");
  }
}

function exportCSV() {
  if (!leads.length) {
    showToast("No leads to export");
    return;
  }

  const rows = leads.map((lead) => ({
    domain: lead.domain,
    shopify: lead.shopify ? "Yes" : "No",
    live_checkout: lead.live_checkout || "",
    live_confidence: lead.live_confidence || 0,
    historical_count: lead.historical_checkouts?.length || 0,
    historical_checkouts: (lead.historical_checkouts || []).join(", "),
    has_kwikpass: lead.has_kwikpass ? "Yes" : "No",
    emails: (lead.emails || []).join("; "),
    linkedin: lead.socials?.linkedin || "",
    instagram: lead.socials?.instagram || "",
    facebook: lead.socials?.facebook || "",
    lead_score: lead.lead_score,
    priority: lead.priority,
    status:
      localStorage.getItem(`flexype_status_${lead.domain}`) ||
      lead.status ||
      "Not Contacted",
    last_scan: lead.last_scan,
  }));

  const headers = Object.keys(rows[0]);
  const csv = [
    headers.join(","),
    ...rows.map((r) =>
      headers.map((h) => JSON.stringify(r[h] || "")).join(","),
    ),
  ].join("\n");

  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  const url = URL.createObjectURL(blob);
  a.href = url;
  a.download = `flexype_outreach-export-${new Date().toISOString().slice(0, 19)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  showToast("CSV exported");
}

function saveNotes() {
  if (selectedLead) {
    const notes = document.getElementById("leadNotes").value;
    localStorage.setItem(`flexype_notes_${selectedLead.domain}`, notes);
    showToast("Notes saved");
  }
}

function saveStatus() {
  if (selectedLead) {
    const status = document.getElementById("leadStatus").value;
    localStorage.setItem(`flexype_status_${selectedLead.domain}`, status);
    showToast(`Status updated to "${status}"`);
  }
}

function openWebsite() {
  if (selectedLead) {
    const url = getDomainUrl(selectedLead.domain);
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

function copyDomain() {
  if (selectedLead) {
    navigator.clipboard.writeText(selectedLead.domain);
    showToast("Domain copied");
  }
}

function toggleWatchlistFilter() {
  const btn = document.getElementById("watchlistFilterBtn");
  const isActive = btn.classList.toggle("active");
  if (isActive) {
    renderTable(leads.filter((l) => watchlist.has(l.domain)));
    btn.textContent = "Show All";
  } else {
    renderTable(leads);
    btn.textContent = "Watchlist Only";
  }
}

// --- On-demand single domain scan helpers ---
async function scanSingleDomain(domainOverride) {
  const input = document.getElementById("singleScanInput");
  const btn = document.getElementById("singleScanBtn");
  const statusEl = document.getElementById("scanStatus");
  const domainRaw = (typeof domainOverride === 'string' && domainOverride.length) ? domainOverride : (input?.value?.trim() || "");
  const domain = domainRaw.replace(/^https?:\/\//, "").replace(/\/$/, "").toLowerCase();

  if (!domain) {
    showToast("Please enter a domain", "error");
    return;
  }

  const domainRegex = /^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$/;
  if (!domainRegex.test(domain)) {
    showToast("Invalid domain format", "error");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Scanning...";
  statusEl.textContent = "Scanning...";
  showToast(`Scanning ${domain}...`, "info");

  let result = null;
  // Try contacting local API server first (port 8080 then 5000)
  try {
    let resp = null;
    try {
      resp = await fetch("http://localhost:8080/scan-domain", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain }),
      });
    } catch (e) {
      // fallback to older endpoint
      resp = await fetch("http://localhost:5000/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain }),
      });
    }

    if (resp && resp.ok) {
      result = await resp.json();
    } else if (resp) {
      // non-OK response from API: read body for diagnostics
      try {
        const body = await resp.text();
        console.error('API error', resp.status, body);
        statusEl.textContent = `API error ${resp.status}: ${body.slice(0,200)}`;
      } catch (e) {
        console.error('API error and failed to read body', e);
        statusEl.textContent = `API error ${resp.status}`;
      }
    }
  } catch (e) {
    console.error("API server not available, falling back to local results.json", e);
    statusEl.textContent = `API unreachable: ${e.message}`;
  }

  // Fallback: reload results.json and search
  if (!result) {
    statusEl.textContent = "Loading results.json...";
    try {
      const resp = await fetch(`results.json?t=${Date.now()}`);
      if (resp.ok) {
        const all = await resp.json();
        result = all.find((r) => (r.domain || "").toLowerCase() === domain);
      }
    } catch (e) {
      console.error("Failed to load results.json", e);
      statusEl.textContent = `Failed to load results.json: ${e.message}`;
    }
  }

  try {
    if (result && !result.error) {
      const existingIndex = leads.findIndex((l) => l.domain === result.domain);
      if (existingIndex === -1) {
        leads.unshift(result);
        showToast(`Added ${result.domain} to database`, "success");
      } else {
        leads[existingIndex] = result;
        showToast(`Updated ${result.domain}`, "success");
      }

      populateFilters();
      updateStats();
      renderTable(leads);
      showLead(result);

      // Highlight the row
      const rows = document.querySelectorAll('#leadTable tbody tr');
      rows.forEach((row) => {
        if (row.textContent.includes(result.domain)) {
          row.scrollIntoView({ behavior: 'smooth', block: 'center' });
          row.click();
        }
      });

      statusEl.textContent = 'Complete';
      input.value = '';
    } else {
      showToast('Domain not found. Run full scan or start API server.', 'error');
      statusEl.textContent = 'Not found';
    }
  } catch (err) {
    console.error('Scan failed:', err);
    showToast(`Scan failed: ${err.message || err}`, 'error');
    statusEl.textContent = `Failed: ${err.message || err}`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Scan Now';
    setTimeout(() => {
      if (statusEl.textContent !== 'Complete') statusEl.textContent = '';
    }, 3000);
  }
}

async function checkApiServer() {
  try {
    const res = await fetch('http://localhost:8080/health');
    if (res.ok) {
      document.getElementById('scanStatus').textContent = 'API ready (8080)';
      return true;
    } else {
      const body = await res.text().catch(() => '');
      document.getElementById('scanStatus').textContent = `API responded ${res.status}: ${body.slice(0,80)}`;
      console.error('Health check non-OK', res.status, body);
      return false;
    }
  } catch (e) {
    console.error('Health check failed on 8080:', e);
    try {
      const res2 = await fetch('http://localhost:5000/health');
      if (res2.ok) {
        document.getElementById('scanStatus').textContent = 'API ready (5000)';
        return true;
      } else {
        const body2 = await res2.text().catch(() => '');
        document.getElementById('scanStatus').textContent = `API responded ${res2.status}: ${body2.slice(0,80)}`;
        console.error('Health check non-OK on 5000', res2.status, body2);
        return false;
      }
    } catch (e2) {
      console.error('Health check failed on 5000:', e2);
      document.getElementById('scanStatus').textContent = `API offline: ${e.message || e2.message}`;
    }
  }
  return false;
}

// Event Listeners
document
  .getElementById("searchInput")
  ?.addEventListener("input", () => renderTable(leads));

// If user presses Enter in the main search input and it looks like a domain, trigger single scan
document.getElementById("searchInput")?.addEventListener("keypress", (e) => {
  if (e.key === 'Enter') {
    const val = e.target.value?.trim() || '';
    if (val && val.includes('.') && !val.includes(' ')) {
      scanSingleDomain(val);
    }
  }
});
document
  .getElementById("providerFilter")
  ?.addEventListener("change", () => renderTable(leads));
document
  .getElementById("historicalFilter")
  ?.addEventListener("change", () => renderTable(leads));
document
  .getElementById("priorityFilter")
  ?.addEventListener("change", () => renderTable(leads));
document
  .getElementById("sortFilter")
  ?.addEventListener("change", () => renderTable(leads));
document.getElementById("exportCSVBtn")?.addEventListener("click", exportCSV);
document
  .getElementById("watchlistFilterBtn")
  ?.addEventListener("click", toggleWatchlistFilter);
document
  .getElementById("openWebsiteBtn")
  ?.addEventListener("click", openWebsite);
document.getElementById("copyDomainBtn")?.addEventListener("click", copyDomain);
document.getElementById("watchlistBtn")?.addEventListener("click", () => {
  if (selectedLead) {
    toggleWatchlist(selectedLead.domain);
    renderTable(leads);
    if (selectedLead) showLead(selectedLead);
  }
});
document.getElementById("saveNotesBtn")?.addEventListener("click", saveNotes);
document.getElementById("saveStatusBtn")?.addEventListener("click", saveStatus);
document
  .getElementById("templateType")
  ?.addEventListener("change", () => updateEmailTemplate());
document.getElementById("copyEmailBtn")?.addEventListener("click", copyEmail);
document
  .getElementById("resetTemplateBtn")
  ?.addEventListener("click", resetEmailTemplate);

// Single-scan UI bindings
document.getElementById('singleScanBtn')?.addEventListener('click', scanSingleDomain);
document.getElementById('singleScanInput')?.addEventListener('keypress', (e) => {
  if (e.key === 'Enter') scanSingleDomain();
});

// Check if local API server is available
checkApiServer();

// Initialize
loadData();
