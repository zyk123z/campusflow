const AUTH_KEY = "campusflow.auth";

const state = {
  sessionId: null,
  sending: false,
  profile: null,
  modelName: "mock"
};

const els = {
  serviceState: document.querySelector("#serviceState"),
  modelState: document.querySelector("#modelState"),
  activeAccount: document.querySelector("#activeAccount"),
  switchAccount: document.querySelector("#switchAccount"),
  messages: document.querySelector("#messages"),
  chatForm: document.querySelector("#chatForm"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  newSession: document.querySelector("#newSession"),
  sessionBadge: document.querySelector("#sessionBadge"),
  studentGreeting: document.querySelector("#studentGreeting"),
  charCount: document.querySelector("#charCount")
};

function readAuth() {
  try {
    return JSON.parse(sessionStorage.getItem(AUTH_KEY) || "null");
  } catch {
    return null;
  }
}

function clearAuth() {
  sessionStorage.removeItem(AUTH_KEY);
}

function authHeader() {
  const auth = readAuth();
  if (!auth?.token) {
    window.location.replace("/");
    return "";
  }
  return `Basic ${auth.token}`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), Authorization: authHeader() };
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response;
}

function setPill(el, text, tone = "ok") {
  el.textContent = text;
  el.className = `pill ${tone}`;
}

function isAdmin(profile) {
  return profile.roles?.some((role) => role.authority === "ROLE_ADMIN");
}

function displayModel(model) {
  return model || "mock";
}

function studentDisplayName(profile) {
  return profile.displayName === "Demo Student" ? "Student" : (profile.displayName || profile.username || "Student");
}

function greetingForNow() {
  const hour = new Date().getHours();
  if (hour < 6) return "夜深了";
  if (hour < 11) return "早上好";
  if (hour < 14) return "中午好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

function updateCharCount() {
  els.charCount.textContent = `${els.messageInput.value.length} / 1000`;
}

async function checkHealth() {
  try {
    const response = await fetch("/actuator/health");
    const body = await response.json();
    setPill(els.serviceState, body.status === "UP" ? "服务正常" : `服务 ${body.status}`, body.status === "UP" ? "ok" : "danger");
  } catch {
    setPill(els.serviceState, "服务 DOWN", "danger");
  }
}

async function loadProfile() {
  try {
    const response = await api("/api/profile");
    const profile = await response.json();
    if (isAdmin(profile)) {
      window.location.replace("/admin.html");
      return null;
    }
    state.profile = profile;
    const displayName = studentDisplayName(profile);
    els.activeAccount.textContent = displayName;
    els.studentGreeting.textContent = `${greetingForNow()}，${displayName}`;
    return profile;
  } catch {
    clearAuth();
    window.location.replace("/");
    return null;
  }
}

async function loadAgentStatus() {
  const response = await api("/api/agent/status");
  const status = await response.json();
  state.modelName = status.model || "mock";
  if (status.realModelEnabled) {
    setPill(els.modelState, `${status.provider} / ${displayModel(state.modelName)}`, "ok");
  } else {
    setPill(els.modelState, "mock 演示", "warn");
  }
}

function clearWelcome() {
  const empty = els.messages.querySelector(".empty");
  if (empty) empty.remove();
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderInlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
}

function renderMarkdown(text) {
  const lines = String(text).replace(/\r\n/g, "\n").split("\n");
  const output = [];
  let paragraph = [];
  let listType = null;

  const closeParagraph = () => {
    if (!paragraph.length) return;
    output.push(`<p>${paragraph.join("<br>")}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (!listType) return;
    output.push(`</${listType}>`);
    listType = null;
  };
  const openList = (type) => {
    closeParagraph();
    if (listType === type) return;
    closeList();
    listType = type;
    output.push(`<${type}>`);
  };

  for (const line of lines) {
    if (!line.trim()) {
      closeParagraph();
      closeList();
      continue;
    }
    const heading = line.match(/^\s*(#{1,3})\s+(.+)$/);
    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (heading) {
      closeParagraph();
      closeList();
      const level = heading[1].length + 2;
      output.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
    } else if (unordered) {
      openList("ul");
      output.push(`<li>${renderInlineMarkdown(unordered[1])}</li>`);
    } else if (ordered) {
      openList("ol");
      output.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`);
    } else {
      closeList();
      paragraph.push(renderInlineMarkdown(line));
    }
  }
  closeParagraph();
  closeList();
  return output.join("");
}

function renderAssistantMessage(element, content) {
  element.innerHTML = renderMarkdown(content);
}

function addMessage(role, content) {
  clearWelcome();
  const row = document.createElement("article");
  row.className = `message ${role}`;
  row.innerHTML = `
    <div class="message-role">${role === "user" ? "我" : "CampusFlow"}</div>
    <div class="bubble"></div>
  `;
  row.querySelector(".bubble").textContent = content;
  els.messages.append(row);
  els.messages.scrollTop = els.messages.scrollHeight;
  return row.querySelector(".bubble");
}

function parseSse(buffer, onEvent) {
  const parts = buffer.split("\n\n");
  const rest = parts.pop();
  for (const part of parts) {
    const dataLine = part.split("\n").find((line) => line.startsWith("data: "));
    if (!dataLine) continue;
    onEvent(JSON.parse(dataLine.slice(6)));
  }
  return rest;
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.sending) return;
  const message = els.messageInput.value.trim();
  if (!message) return;
  state.sending = true;
  els.sendButton.disabled = true;
  setPill(els.sessionBadge, "THINKING", "warn");
  els.messageInput.value = "";
  updateCharCount();
  addMessage("user", message);
  const assistant = addMessage("assistant", "");
  let raw = "";

  try {
    const response = await api("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: state.sessionId, message })
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let streamFailed = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = parseSse(buffer, (eventData) => {
        if (eventData.type === "meta") state.sessionId = eventData.sessionId;
        if (eventData.type === "token") {
          raw += eventData.content || "";
          assistant.textContent = raw;
          els.messages.scrollTop = els.messages.scrollHeight;
        }
        if (eventData.type === "error") {
          streamFailed = true;
          if (!raw) assistant.textContent = eventData.message || "MCP 工具调用失败";
          setPill(els.sessionBadge, "ERROR", "danger");
        }
      });
    }
    if (raw) renderAssistantMessage(assistant, raw);
    if (!streamFailed) setPill(els.sessionBadge, "DONE", "ok");
  } catch (error) {
    assistant.textContent = `发送失败：${error.message}`;
    setPill(els.sessionBadge, "ERROR", "danger");
  } finally {
    state.sending = false;
    els.sendButton.disabled = false;
  }
}

function resetSession() {
  state.sessionId = null;
  els.messages.innerHTML = `
    <div class="empty student-welcome">
      <span class="welcome-kicker">A FRESH START</span>
      <strong>新会话已经准备好</strong>
      <p>不用延续刚才的话题，你可以从此刻最想说的一件事重新开始。</p>
    </div>
  `;
  els.messageInput.value = "";
  updateCharCount();
  setPill(els.sessionBadge, "READY");
}

function logout() {
  clearAuth();
  window.location.assign("/");
}

document.querySelectorAll("[data-quick]").forEach((button) => {
  button.addEventListener("click", () => {
    els.messageInput.value = button.dataset.quick;
    updateCharCount();
    els.messageInput.focus();
  });
});
els.messageInput.addEventListener("input", updateCharCount);
els.chatForm.addEventListener("submit", sendMessage);
els.newSession.addEventListener("click", resetSession);
els.switchAccount.addEventListener("click", logout);

checkHealth();
loadProfile().then((profile) => {
  if (profile) loadAgentStatus();
});
updateCharCount();
