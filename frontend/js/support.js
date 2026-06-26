// frontend/js/support.js

(function () {
  window.FP_requireRole("admin", "supportteammember");
  
  const API_URL = localStorage.getItem("fp_api_url") || "http://localhost:8080";
  let churnData = [];
  
  const supportTbody = document.getElementById("supportTbody");
  const searchInput = document.getElementById("supportSearch");

  function authHeaders() {
    return {
      "Content-Type": "application/json",
      "X-User-Email": window.FP_currentUser || "admin@flexype.in"
    };
  }

  function formatDate(isoString) {
    if (!isoString) return "Unknown";
    return new Date(isoString).toLocaleString();
  }

  async function loadChurnedStores() {
    try {
      const res = await fetch(`${API_URL}/api/support/churned`, { headers: authHeaders() });
      if (!res.ok) throw new Error("Failed to load churn data");
      churnData = await res.json();
      renderTable();
    } catch (err) {
      console.error(err);
      supportTbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#f87171;">Failed to load data.</td></tr>';
    }
  }

  function renderTable() {
    const query = searchInput.value.toLowerCase().trim();
    supportTbody.innerHTML = "";

    const filtered = churnData.filter(d => {
      const storeMatch = d.domain && d.domain.toLowerCase().includes(query);
      const notesMatch = d.notes && d.notes.toLowerCase().includes(query);
      const assignedMatch = d.assigned && d.assigned.toLowerCase().includes(query);
      return storeMatch || notesMatch || assignedMatch;
    });

    if (filtered.length === 0) {
      supportTbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#888;">No churned stores found.</td></tr>';
      return;
    }

    filtered.forEach(d => {
      const tr = document.createElement("tr");
      
      const reason = `Switched from FlexyPe to ${d.new_checkout || "Unknown"}`;
      
      tr.innerHTML = `
        <td style="font-weight: 500; color: #1e293b;">${d.domain || "Unknown"}</td>
        <td style="color: #ef4444;">${reason}</td>
        <td style="color: #64748b;">${formatDate(d.timestamp)}</td>
        <td style="color: #64748b;">${d.old_checkout || "FlexyPe"}</td>
        <td>
          <input type="text" class="form-input inline-edit" data-id="${d._id}" data-field="notes" value="${d.notes || ''}" placeholder="Add notes..." style="width: 100%; padding: 4px 8px; border-radius: 4px;">
        </td>
        <td>
          <input type="text" class="form-input inline-edit" data-id="${d._id}" data-field="assigned" value="${d.assigned || ''}" placeholder="Assign to..." style="width: 100%; padding: 4px 8px; border-radius: 4px;">
        </td>
      `;
      supportTbody.appendChild(tr);
    });

    // Add event listeners for inline editing (blur/change)
    const inputs = supportTbody.querySelectorAll('.inline-edit');
    inputs.forEach(input => {
      input.addEventListener('change', async (e) => {
        const id = e.target.getAttribute('data-id');
        const field = e.target.getAttribute('data-field');
        const value = e.target.value;
        
        // Optimistically update local data
        const item = churnData.find(x => x._id === id);
        if (item) item[field] = value;
        
        try {
          const res = await fetch(`${API_URL}/api/support/churned/${id}`, {
            method: "PATCH",
            headers: authHeaders(),
            body: JSON.stringify({ [field]: value })
          });
          if (!res.ok) throw new Error("Failed to update");
          
          // Flash green on success
          e.target.style.borderColor = "#4ade80";
          setTimeout(() => e.target.style.borderColor = "rgba(255,255,255,0.1)", 1000);
        } catch (err) {
          console.error(err);
          e.target.style.borderColor = "#f87171";
        }
      });
    });
  }

  // --- INITIALIZATION ---

  searchInput.addEventListener('input', renderTable);
  
  loadChurnedStores();

  // Basic User Avatar set
  const userEmail = window.FP_currentUser;
  const userName = window.FP_currentUserName || userEmail;
  if (document.getElementById("userEmail")) {
    document.getElementById("userEmail").textContent = userName;
    document.getElementById("userAvatar").textContent = userName ? userName[0].toUpperCase() : "?";
  }

})();
