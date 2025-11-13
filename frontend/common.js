let API = localStorage.getItem("API") || "http://localhost:8000";

function setApi(url) {
  API = url.trim();
  localStorage.setItem("API", API);
}

function getApi() {
  return API;
}

function saveSession(token, userId, isAdmin, kycStatus, email) {
  localStorage.setItem("token", token);
  localStorage.setItem("user", userId);
  localStorage.setItem("is_admin", isAdmin ? "1" : "0");
  localStorage.setItem("kyc_status", kycStatus || "pending");
  if (email !== undefined) localStorage.setItem("email", email || "");
}

function getSession() {
  return {
    token: localStorage.getItem("token"),
    user: localStorage.getItem("user"),
    is_admin: localStorage.getItem("is_admin") === "1",
    kyc_status: localStorage.getItem("kyc_status") || "pending",
    email: localStorage.getItem("email") || "",
  };
}

function authHeaders() {
  const { token } = getSession();
  return token ? { Authorization: "Bearer " + token } : {};
}

async function pingHealth(labelId) {
  const el = document.getElementById(labelId);
  try {
    const r = await fetch(getApi() + "/health");
    const j = await r.json();
    el.textContent = j.ok ? "✓ API OK (" + j.symbol + ")" : "API?";
    el.style.color = j.ok ? "green" : "red";
  } catch (e) {
    el.textContent = "API not reachable";
    el.style.color = "red";
  }
}
