const authView = document.querySelector("#auth-view");
const appView = document.querySelector("#app-view");
const registerForm = document.querySelector("#register-form");
const authError = document.querySelector("#auth-error");
const toast = document.querySelector("#toast");

let authToken = localStorage.getItem("objectlens-token");
let conversationId = null;
let pollTimer = null;

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.setTimeout(() => toast.classList.add("hidden"), 4500);
}

function setAuthenticated(token) {
  authToken = token;
  localStorage.setItem("objectlens-token", token);
  authView.classList.add("hidden");
  appView.classList.remove("hidden");
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem("objectlens-token");
      authToken = null;
      appView.classList.add("hidden");
      authView.classList.remove("hidden");
    }
    throw new Error(data.detail || "Something went wrong. Please try again.");
  }
  return data;
}

registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authError.textContent = "";
  const button = registerForm.querySelector("button");
  button.disabled = true;
  button.textContent = "Setting up your profile…";
  try {
    const data = await api("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: registerForm.username.value,
        phone: registerForm.phone.value,
      }),
    });
    setAuthenticated(data.session_token);
  } catch (error) {
    authError.textContent = error.message;
  } finally {
    button.disabled = false;
    button.innerHTML = 'Start exploring <span>→</span>';
  }
});

async function checkLinkStatus(code) {
  try {
    const result = await fetch(`/api/auth/link-status/${encodeURIComponent(code)}`);
    const data = await result.json();
    if (result.ok && data.linked) {
      window.clearInterval(pollTimer);
      document.querySelector("#link-status").textContent = "Verified! Your conversation summary has been sent to Telegram.";
      document.querySelector("#telegram-link").classList.add("hidden");
      document.querySelector("#retry-link").classList.add("hidden");
    } else if (!result.ok) {
      window.clearInterval(pollTimer);
      document.querySelector("#link-status").textContent = data.detail || "This link expired.";
      document.querySelector("#retry-link").classList.remove("hidden");
    }
  } catch {
    document.querySelector("#link-status").textContent = "Connection interrupted. Still waiting for Telegram…";
  }
}

function appendMessage(role, text) {
  const message = document.createElement("div");
  message.className = `message ${role}`;
  message.textContent = text;
  const messages = document.querySelector("#messages");
  messages.append(message);
  messages.scrollTo({ top: messages.scrollHeight, behavior: "smooth" });
}

async function startConversation() {
  const data = await api("/api/conversations", { method: "POST" });
  conversationId = data.conversation_id;
}

async function uploadPhoto(file) {
  if (!file) return;
  const photoInput = document.querySelector("#photo-input");
  document.querySelector("#empty-state").classList.add("hidden");
  document.querySelector("#chat-content").classList.remove("hidden");
  document.querySelector("#typing").classList.remove("hidden");
  try {
    if (!conversationId) await startConversation();
    const body = new FormData();
    body.append("photo", file);
    const data = await api(`/api/conversations/${conversationId}/photo`, {
      method: "POST",
      body,
    });
    document.querySelector("#typing").classList.add("hidden");
    document.querySelector("#object-banner").textContent = `Exploring: ${data.object_name}`;
    document.querySelector("#object-banner").classList.remove("hidden");
    document.querySelector("#message-form").classList.remove("hidden");
    appendMessage("assistant", data.introduction);
    document.querySelector("#aspects").classList.remove("hidden");
    const buttons = document.querySelector("#aspect-buttons");
    buttons.replaceChildren();
    data.aspects.forEach((aspect) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "aspect-button";
      button.textContent = aspect;
      button.addEventListener("click", () => sendMessage(`Tell me about the ${aspect} of this ${data.object_name}.`));
      buttons.append(button);
    });
  } catch (error) {
    document.querySelector("#typing").classList.add("hidden");
    document.querySelector("#empty-state").classList.remove("hidden");
    document.querySelector("#chat-content").classList.add("hidden");
    showToast(error.message);
  } finally {
    photoInput.value = "";
  }
}

document.querySelector("#photo-input").addEventListener("change", (event) => uploadPhoto(event.target.files[0]));

async function sendMessage(text) {
  if (!conversationId || !text.trim()) return;
  const input = document.querySelector("#message-input");
  const cleanText = text.trim();
  appendMessage("user", cleanText);
  input.value = "";
  document.querySelector("#typing").classList.remove("hidden");
  document.querySelectorAll(".aspect-button").forEach((button) => button.disabled = true);
  try {
    const data = await api(`/api/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: cleanText }),
    });
    appendMessage("assistant", data.answer);
  } catch (error) {
    showToast(error.message);
  } finally {
    document.querySelector("#typing").classList.add("hidden");
    document.querySelectorAll(".aspect-button").forEach((button) => button.disabled = false);
  }
}

document.querySelector("#message-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(document.querySelector("#message-input").value);
});

document.querySelector("#end-button").addEventListener("click", async () => {
  if (!conversationId) {
    showToast("Upload and identify an object before ending the conversation.");
    return;
  }
  const button = document.querySelector("#end-button");
  button.disabled = true;
  button.textContent = "Preparing summary…";
  try {
    const data = await api(`/api/conversations/${conversationId}/end`, { method: "POST" });
    if (data.delivery === "web") {
      document.querySelector("#completion-view h1").textContent = "Your summary is ready.";
      document.querySelector("#telegram-link").classList.add("hidden");
      document.querySelector("#link-status").classList.add("hidden");
      document.querySelector("#summary-text").textContent = data.summary;
      document.querySelector("#summary-text").classList.remove("hidden");
    } else if (data.sent) {
      document.querySelector("#completion-view h1").textContent = "Your summary was sent.";
      document.querySelector("#completion-view .muted").textContent = "Your ObjectLens conversation summary has been delivered to your linked Telegram account.";
      document.querySelector("#telegram-link").classList.add("hidden");
      document.querySelector("#link-status").textContent = "You can close this page or start another discovery.";
    } else {
      document.querySelector("#telegram-link").href = data.telegram_link;
      document.querySelector("#link-status").textContent = "Waiting for you to tap Start in Telegram…";
      document.querySelector("#retry-link").classList.add("hidden");
      window.clearInterval(pollTimer);
      pollTimer = window.setInterval(() => checkLinkStatus(data.link_code), 2500);
      checkLinkStatus(data.link_code);
    }
    document.querySelector(".chat-main").classList.add("hidden");
    document.querySelector("#message-form").classList.add("hidden");
    document.querySelector("#completion-view").classList.remove("hidden");
    button.classList.add("hidden");
  } catch (error) {
    showToast(error.message);
  } finally {
    if (conversationId) {
      button.disabled = false;
      button.innerHTML = 'End conversation <span>↗</span>';
    }
  }
});

document.querySelector("#retry-link").addEventListener("click", async () => {
  const button = document.querySelector("#retry-link");
  button.disabled = true;
  try {
    const data = await api(`/api/conversations/${conversationId}/end`, { method: "POST" });
    document.querySelector("#telegram-link").href = data.telegram_link;
    document.querySelector("#telegram-link").classList.remove("hidden");
    document.querySelector("#link-status").textContent = "Waiting for you to tap Start in Telegram…";
    button.classList.add("hidden");
    window.clearInterval(pollTimer);
    pollTimer = window.setInterval(() => checkLinkStatus(data.link_code), 2500);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  const button = document.querySelector("#logout-button");
  button.disabled = true;
  try {
    if (conversationId) {
      const confirmed = window.confirm("Log out and leave this conversation? It will not be summarized.");
      if (!confirmed) return;
    }
    await api("/api/auth/logout", { method: "POST" });
    localStorage.removeItem("objectlens-token");
    authToken = null;
    conversationId = null;
    window.location.reload();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

async function initialize() {
  if (authToken) {
    try {
      await api("/api/auth/me");
      setAuthenticated(authToken);
      return;
    } catch (error) {
      if (!authToken) return;
      showToast(error.message);
      localStorage.removeItem("objectlens-token");
      authToken = null;
    }
  }
}

initialize();
