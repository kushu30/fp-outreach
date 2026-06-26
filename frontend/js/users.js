// frontend/js/users.js

(function () {
  window.FP_requireRole("admin");
  const API_URL = localStorage.getItem("fp_api_url") || "http://localhost:8080";
  let usersData = [];
  let currentEditUser = null;

  // UI - Stats
  const statTotalUsers = document.getElementById("statTotalUsers");
  const statAdmins = document.getElementById("statAdmins");
  const statSales = document.getElementById("statSales");
  const statSupport = document.getElementById("statSupport");

  // UI - Table
  const usersTbody = document.getElementById("usersTbody");

  // UI - Modals
  const addUserModal = document.getElementById("addUserModal");
  const editUserModal = document.getElementById("editUserModal");

  // UI - Add Form
  const addName = document.getElementById("addName");
  const addEmail = document.getElementById("addEmail");
  const addPassword = document.getElementById("addPassword");
  const addRole = document.getElementById("addRole");

  // UI - Edit Form
  const editName = document.getElementById("editName");
  const editEmail = document.getElementById("editEmail");
  const editRole = document.getElementById("editRole");
  const editPassword = document.getElementById("editPassword");
  const editEnabledCheck = document.getElementById("editEnabledCheck");
  const editToggleActiveBtn = document.getElementById("editToggleActiveBtn");
  const edit2FaStatus = document.getElementById("edit2FaStatus");
  const editCreated = document.getElementById("editCreated");
  const editLastLogin = document.getElementById("editLastLogin");

  function authHeaders() {
    return {
      "Content-Type": "application/json",
      "X-User-Email": window.FP_currentUser || "admin@flexype.in"
    };
  }

  function formatDate(isoString) {
    if (!isoString) return "Never";
    return new Date(isoString).toLocaleString();
  }

  // --- INITIALIZATION ---

  document.getElementById("newUserBtn").addEventListener("click", () => {
    addName.value = "";
    addEmail.value = "";
    addPassword.value = "";
    addRole.value = "salesteammember";
    addUserModal.style.display = "flex";
  });

  document.getElementById("addCancel").addEventListener("click", () => {
    addUserModal.style.display = "none";
  });

  document.getElementById("editCancel").addEventListener("click", () => {
    editUserModal.style.display = "none";
    currentEditUser = null;
  });

  document.getElementById("addSubmit").addEventListener("click", createUser);
  document.getElementById("editSave").addEventListener("click", updateUser);
  document.getElementById("editResetPasswordBtn").addEventListener("click", resetPassword);
  document.getElementById("editToggleActiveBtn").addEventListener("click", () => {
    if (!currentEditUser) return;
    if (currentEditUser.active !== false) disableUser();
    else enableUser();
  });

  // --- API FUNCTIONS ---

  async function loadUsers() {
    try {
      usersTbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#888;">Loading...</td></tr>';
      const res = await fetch(`${API_URL}/api/users`, { headers: authHeaders() });
      if (!res.ok) throw new Error("Failed to load users");
      usersData = await res.json();
      renderUsers();
    } catch (err) {
      console.error(err);
      usersTbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#f87171;">Failed to load users.</td></tr>';
    }
  }

  function renderUsers() {
    usersTbody.innerHTML = "";
    
    let adminCount = 0;
    let salesCount = 0;
    let supportCount = 0;

    usersData.forEach(u => {
      // Stats calc
      if (u.role === "admin") adminCount++;
      else if (u.role === "salesteammember") salesCount++;
      else if (u.role === "supportteammember") supportCount++;

      const tr = document.createElement("tr");

      const avatarLetter = (u.name || u.email || "?")[0].toUpperCase();
      const statusHtml = u.active !== false 
        ? '<span style="color:#4ade80;">Active</span>' 
        : '<span style="color:#f87171;">Disabled</span>';
      
      const twoFaHtml = u.twoFAEnabled 
        ? '<span style="color:#4ade80;">Enabled</span>' 
        : '<span style="color:#888;">Disabled</span>';

      const roleMap = {
        "admin": "Admin",
        "salesteammember": "Sales",
        "supportteammember": "Support"
      };
      
      tr.innerHTML = `
        <td><div class="user-avatar" style="width:32px; height:32px; font-size:14px;">${avatarLetter}</div></td>
        <td style="font-weight:500; color:#fff;">${u.name || "—"}</td>
        <td style="color:#aaa;">${u.email}</td>
        <td>${roleMap[u.role] || u.role}</td>
        <td>${statusHtml}</td>
        <td>${twoFaHtml}</td>
        <td style="color:#aaa; font-size:0.85rem;">${formatDate(u.last_login)}</td>
        <td style="text-align: right; display: flex; gap: 8px; justify-content: flex-end;">
          <button class="btn-ghost action-edit" style="padding:4px 8px; font-size:0.85rem;">Edit</button>
          ${u.active !== false 
            ? '<button class="btn-ghost action-disable" style="padding:4px 8px; font-size:0.85rem; color:#f87171;">Disable</button>'
            : '<button class="btn-ghost action-enable" style="padding:4px 8px; font-size:0.85rem; color:#4ade80;">Enable</button>'}
          <button class="btn-ghost action-delete" style="padding:4px 8px; font-size:0.85rem; color:#f87171;">Delete</button>
        </td>
      `;

      // Event Listeners for actions
      tr.querySelector('.action-edit').addEventListener('click', () => openEditModal(u));
      
      const disableBtn = tr.querySelector('.action-disable');
      if (disableBtn) disableBtn.addEventListener('click', () => { currentEditUser = u; disableUser(); });
      
      const enableBtn = tr.querySelector('.action-enable');
      if (enableBtn) enableBtn.addEventListener('click', () => { currentEditUser = u; enableUser(); });

      tr.querySelector('.action-delete').addEventListener('click', () => { currentEditUser = u; deleteUser(); });

      usersTbody.appendChild(tr);
    });

    // Update Stats
    statTotalUsers.textContent = usersData.length;
    statAdmins.textContent = adminCount;
    statSales.textContent = salesCount;
    statSupport.textContent = supportCount;
  }

  function openEditModal(user) {
    currentEditUser = user;
    editName.value = user.name || "";
    editEmail.value = user.email;
    editRole.value = user.role || "salesteammember";
    editPassword.value = "";
    
    // Status visual
    if (user.active !== false) {
      editEnabledCheck.textContent = "☑";
      editEnabledCheck.style.color = "#4ade80";
      editToggleActiveBtn.textContent = "Disable";
    } else {
      editEnabledCheck.textContent = "☐";
      editEnabledCheck.style.color = "#888";
      editToggleActiveBtn.textContent = "Enable";
    }

    edit2FaStatus.textContent = user.twoFAEnabled ? "Enabled" : "Disabled";
    editCreated.textContent = formatDate(user.created_at);
    editLastLogin.textContent = formatDate(user.last_login);

    editUserModal.style.display = "flex";
  }

  async function createUser() {
    const payload = {
      name: addName.value.trim(),
      email: addEmail.value.trim(),
      password: addPassword.value.trim(),
      role: addRole.value
    };

    if (!payload.email || !payload.password) {
      alert("Email and Password are required");
      return;
    }

    const btn = document.getElementById("addSubmit");
    btn.disabled = true;
    try {
      const res = await fetch(`${API_URL}/api/users`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.error || "Failed to create user");
      }
      addUserModal.style.display = "none";
      await loadUsers();
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function updateUser() {
    if (!currentEditUser) return;
    const payload = {
      name: editName.value.trim(),
      role: editRole.value
    };

    const btn = document.getElementById("editSave");
    btn.disabled = true;
    try {
      const res = await fetch(`${API_URL}/api/users/${encodeURIComponent(currentEditUser.email)}`, {
        method: "PATCH",
        headers: authHeaders(),
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.error || "Failed to update user");
      }
      editUserModal.style.display = "none";
      await loadUsers();
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function resetPassword() {
    if (!currentEditUser) return;
    const newPass = editPassword.value.trim();
    if (newPass.length < 8) {
      alert("Password must be at least 8 characters");
      return;
    }

    const btn = document.getElementById("editResetPasswordBtn");
    btn.disabled = true;
    try {
      const res = await fetch(`${API_URL}/api/users/${encodeURIComponent(currentEditUser.email)}/reset-password`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ password: newPass })
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.error || "Failed to reset password");
      }
      alert("Password reset successfully");
      editPassword.value = "";
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function disableUser() {
    if (!currentEditUser) return;
    if (!confirm(`Disable account for ${currentEditUser.email}?`)) return;
    
    try {
      const res = await fetch(`${API_URL}/api/users/${encodeURIComponent(currentEditUser.email)}/disable`, {
        method: "POST",
        headers: authHeaders()
      });
      if (!res.ok) throw new Error("Failed to disable user");
      
      if (editUserModal.style.display === "flex") editUserModal.style.display = "none";
      await loadUsers();
    } catch (err) {
      alert(err.message);
    }
  }

  async function enableUser() {
    if (!currentEditUser) return;
    try {
      const res = await fetch(`${API_URL}/api/users/${encodeURIComponent(currentEditUser.email)}/enable`, {
        method: "POST",
        headers: authHeaders()
      });
      if (!res.ok) throw new Error("Failed to enable user");
      
      if (editUserModal.style.display === "flex") editUserModal.style.display = "none";
      await loadUsers();
    } catch (err) {
      alert(err.message);
    }
  }

  async function deleteUser() {
    if (!currentEditUser) return;
    if (!confirm(`Permanently delete ${currentEditUser.email}? This cannot be undone.`)) return;

    try {
      const res = await fetch(`${API_URL}/api/users/${encodeURIComponent(currentEditUser.email)}`, {
        method: "DELETE",
        headers: authHeaders()
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.error || "Failed to delete user");
      }
      if (editUserModal.style.display === "flex") editUserModal.style.display = "none";
      await loadUsers();
    } catch (err) {
      alert(err.message);
    }
  }

  // Load immediately
  loadUsers();

  // Basic User Avatar set
  const userEmail = window.FP_currentUser;
  const userName = window.FP_currentUserName || userEmail;
  if (document.getElementById("userEmail")) {
    document.getElementById("userEmail").textContent = userName;
    document.getElementById("userAvatar").textContent = userName ? userName[0].toUpperCase() : "?";
  }

})();
