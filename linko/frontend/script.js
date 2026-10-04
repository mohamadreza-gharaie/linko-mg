// ============================================================
// State
// ============================================================
// ============================================================
// Mobile viewport fix: keep the layout height in sync with the
// actual visible area so the input bar never slides under the
// on-screen keyboard or the browser's address bar.
// ============================================================
function setAppHeight() {
  const height = window.visualViewport ? window.visualViewport.height : window.innerHeight;
  document.documentElement.style.setProperty("--app-height", `${height}px`);
}
setAppHeight();
window.addEventListener("resize", setAppHeight);
window.addEventListener("orientationchange", () => setTimeout(setAppHeight, 200));
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => {
    setAppHeight();
    if (activeChat) scrollMessagesToBottom();
  });
}

const API = "/api";
let me = null;
let socket = null;
let chats = [];
let activeChat = null;
let activeChatRole = null;
let chatReadState = {}; // { userId: lastReadMessageId } for the chat currently open
let renderedMessages = new Map(); // messageId -> message object, for the chat currently open
let replyingToMessage = null;
let editingMessage = null;
let messageActionMenuTargetId = null;
let inSavedMessages = false; // true while the special "Saved Messages" personal notes view is open
let pinnedMessages = []; // pinned-message summaries for the currently open group/channel
let forwardMessageId = null;
let selectedNewMembers = new Map();
let selectedAddMembers = new Map();
let createChatMode = "group"; // 'group' | 'channel'
let typingTimeout = null;
let sendingMessage = false; // prevents accidental double-clicks from sending twice

// ============================================================
// Helpers
// ============================================================
function $(id) { return document.getElementById(id); }

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    method: options.method || "GET",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "خطای ناشناخته");
  return data;
}

function initials(name) {
  if (!name) return "?";
  const parts = name.trim().split(" ");
  return parts.length > 1 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2);
}

// Fills an existing .avatar element with either an uploaded photo or colored initials
function fillAvatarEl(el, { avatar_url, avatar_color, name }) {
  if (avatar_url) {
    el.style.background = "transparent";
    el.innerHTML = `<img src="${avatar_url}" alt="">`;
  } else {
    el.style.background = avatar_color || "#4e89ff";
    el.textContent = initials(name);
  }
}

// Returns an HTML string for an avatar (used when building list items dynamically)
function avatarHtml({ avatar_url, avatar_color, name, online }, extraClass = "") {
  const inner = avatar_url
    ? `<div class="avatar ${extraClass}" style="background:transparent"><img src="${avatar_url}" alt=""></div>`
    : `<div class="avatar ${extraClass}" style="background:${avatar_color || "#4e89ff"}">${initials(name)}</div>`;
  const dot = online ? `<span class="online-dot"></span>` : "";
  return `<div class="avatar-wrap">${inner}${dot}</div>`;
}

function fileIconFor(fileName) {
  const ext = (fileName || "").split(".").pop().toLowerCase();
  const map = {
    pdf: "fa-file-pdf", doc: "fa-file-word", docx: "fa-file-word",
    xls: "fa-file-excel", xlsx: "fa-file-excel", ppt: "fa-file-powerpoint", pptx: "fa-file-powerpoint",
    zip: "fa-file-zipper", rar: "fa-file-zipper", "7z": "fa-file-zipper",
    txt: "fa-file-lines", csv: "fa-file-csv", json: "fa-file-code",
  };
  return map[ext] || "fa-file";
}

function formatBytes(num) {
  if (!num && num !== 0) return "";
  const units = ["بایت", "کیلوبایت", "مگابایت", "گیگابایت"];
  let size = num, i = 0;
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
  return `${i === 0 ? Math.round(size) : size.toFixed(1)} ${units[i]}`;
}

function showToast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 2500);
}

function parseServerDate(value) {
  if (!value) return null;
  if (value instanceof Date) return value;
  const text = String(value);
  // New API responses use ISO timestamps with an explicit timezone.
  if (/([zZ]|[+-]\d{2}:?\d{2})$/.test(text)) {
    const parsed = new Date(text);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  // Backward compatibility for older saved values such as "YYYY-MM-DD HH:mm:ss".
  const legacy = new Date(text.replace(" ", "T") + "Z");
  if (!Number.isNaN(legacy.getTime())) return legacy;
  const fallback = new Date(text);
  return Number.isNaN(fallback.getTime()) ? null : fallback;
}

function formatTime(iso) {
  const d = parseServerDate(iso);
  if (!d) return "";
  return d.toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
}

function formatChatListTime(iso) {
  const d = parseServerDate(iso);
  if (!d) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return d.toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("fa-IR", { month: "short", day: "numeric" });
}

// ============================================================
// Online / offline presence + "last seen" text
// ============================================================
function relativeTimeFa(iso) {
  const d = parseServerDate(iso);
  if (!d) return null;
  const diffMs = Date.now() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "چند لحظه پیش";
  if (diffMin < 60) return `${toPersianDigits(diffMin)} دقیقه پیش`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${toPersianDigits(diffH)} ساعت پیش`;
  const diffD = Math.floor(diffH / 24);
  if (diffD === 1) return "دیروز";
  if (diffD < 7) return `${toPersianDigits(diffD)} روز پیش`;
  return d.toLocaleDateString("fa-IR");
}

function presenceText(online, lastSeen) {
  if (online) return "آنلاین";
  const rel = relativeTimeFa(lastSeen);
  return rel ? `آخرین بازدید ${rel}` : "آخرین بازدید نامشخص";
}

function updateChatHeaderSub(chat) {
  if (chat.type === "private") {
    $("chatHeaderSub").textContent = presenceText(chat.online, chat.last_seen);
    $("chatHeaderSub").classList.toggle("online-text", !!chat.online);
  } else {
    $("chatHeaderSub").classList.remove("online-text");
    $("chatHeaderSub").textContent = chat.type === "group" ? `گروه • ${chat.member_count} عضو` : `کانال • ${chat.member_count} عضو`;
  }
}

// ============================================================
// Auth
// ============================================================
document.querySelectorAll(".auth-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".auth-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".auth-form").forEach(f => f.classList.remove("active"));
    tab.classList.add("active");
    $(tab.dataset.tab + "Form").classList.add("active");
  });
});

$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("loginError").textContent = "";
  try {
    const user = await api("/login", {
      method: "POST",
      body: {
        username: $("loginUsername").value,
        password: $("loginPassword").value,
      },
    });
    onAuthed(user);
  } catch (err) {
    $("loginError").textContent = err.message;
  }
});

$("registerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("registerError").textContent = "";
  try {
    const user = await api("/register", {
      method: "POST",
      body: {
        display_name: $("regDisplayName").value,
        username: $("regUsername").value,
        password: $("regPassword").value,
      },
    });
    onAuthed(user);
  } catch (err) {
    $("registerError").textContent = err.message;
  }
});

let canPostMessages = true; // whether the current user is allowed to post in the active chat (channel restriction)

async function onAuthed(user) {
  me = user;
  applyTheme(me.theme_color, me.theme_mode);
  $("authScreen").classList.add("hidden");
  $("appScreen").classList.remove("hidden");
  fillAvatarEl($("meAvatar"), { avatar_url: me.avatar_url, avatar_color: me.avatar_color, name: me.display_name });
  $("meName").textContent = me.display_name;
  connectSocket();
  requestNotificationPermission();
  await loadChats();
}

function applyTheme(color, mode) {
  document.documentElement.dataset.themeColor = color || "blue";
  document.documentElement.dataset.themeMode = mode || "dark";
}

async function tryAutoLogin() {
  try {
    const user = await api("/me");
    onAuthed(user);
  } catch (e) {
    // not logged in, stay on auth screen
  }
}

// ============================================================
// Socket.IO
// ============================================================
function connectSocket() {
  socket = io({ withCredentials: true, reconnection: true, reconnectionAttempts: Infinity, reconnectionDelay: 500 });

  socket.on("connect", () => {
    // Re-join every chat after an initial connection or automatic reconnect.
    // This prevents a temporarily dropped Socket.IO connection from making
    // new messages invisible until the page is refreshed.
    chats.forEach(chat => socket.emit("join_chat", { chat_id: chat.id }));
  });

  socket.on("connect_error", () => {
    // Socket.IO will automatically retry; REST fallback below keeps text
    // messages functional even while the realtime connection is unavailable.
  });

  socket.on("new_message", (msg) => {
    updateChatListWithMessage(msg);
    if (activeChat && activeChat.id === msg.chat_id) {
      renderMessage(msg);
      scrollMessagesToBottom();
      refreshReadTicks();
      if (document.hasFocus()) markChatRead(activeChat.id);
    }
    notifyIncomingMessage(msg);
  });

  socket.on("typing", (data) => {
    if (activeChat && activeChat.id === data.chat_id && data.user_id !== me.id) {
      $("typingIndicator").classList.remove("hidden");
      clearTimeout(typingTimeout);
      typingTimeout = setTimeout(() => {
        $("typingIndicator").classList.add("hidden");
      }, 2400);
    }
  });

  socket.on("members_added", (data) => {
    if (activeChat && activeChat.id === data.chat_id) {
      loadChatInfo(activeChat.id);
    }
  });

  socket.on("message_deleted", (data) => {
    if (activeChat && activeChat.id === data.chat_id) {
      const row = document.querySelector(`.msg-row[data-message-id="${data.message_id}"]`);
      if (row) row.remove();
      renderedMessages.delete(data.message_id);
      removeStaleReplyQuotes(data.message_id);
    }
    loadChats();
  });

  socket.on("reaction_updated", (data) => {
    if (!activeChat || activeChat.id !== data.chat_id) return;
    const msg = renderedMessages.get(data.message_id);
    if (msg) msg.reactions = data.reactions;

    const row = document.querySelector(`.msg-row[data-message-id="${data.message_id}"]`);
    if (!row) return;
    const bubble = row.querySelector(".bubble");
    const oldReactionsEl = bubble.querySelector(".msg-reactions");
    const newHtml = buildReactionsHtml({ reactions: data.reactions });

    if (oldReactionsEl) {
      oldReactionsEl.outerHTML = newHtml || "";
    } else if (newHtml) {
      const timeEl = bubble.querySelector(".bubble-time");
      timeEl.insertAdjacentHTML("beforebegin", newHtml);
    }
    wireReactionPills(row, data.message_id);
  });

  socket.on("poll_voted", (data) => {
    if (!activeChat || activeChat.id !== data.chat_id) return;
    const msg = renderedMessages.get(data.poll.message_id);
    if (!msg || !msg.poll) return;

    // Keep whatever this client already knows about ITS OWN vote (the broadcast
    // is deliberately anonymous/objective and never carries anyone's personal pick).
    msg.poll = { ...data.poll, my_option_id: msg.poll.my_option_id };

    const row = document.querySelector(`.msg-row[data-message-id="${msg.id}"]`);
    if (!row) return;
    const bubble = row.querySelector(".bubble");
    const oldPoll = bubble.querySelector(".poll-bubble");
    if (oldPoll) oldPoll.outerHTML = buildPollHtml(msg);
    wirePollOptions(bubble, msg);
  });

  socket.on("message_edited", (msg) => {
    if (activeChat && activeChat.id === msg.chat_id) {
      renderedMessages.set(msg.id, msg);
      const row = document.querySelector(`.msg-row[data-message-id="${msg.id}"]`);
      if (row) {
        const textEl = row.querySelector(".bubble-text");
        if (textEl) { textEl.innerHTML = ""; textEl.appendChild(buildFormattedFragment(msg.content)); }
        if (!row.querySelector(".bubble-edited-tag")) {
          const timeEl = row.querySelector(".bubble-time");
          if (timeEl) timeEl.insertAdjacentHTML("afterbegin", `<span class="bubble-edited-tag">ویرایش‌شده</span>`);
        }
      }
      refreshStaleReplyQuotes(msg.id, messagePreviewText(msg));
    }
    loadChats();
  });

  socket.on("member_role_changed", (data) => {
    if (data.user_id === me.id && activeChat && activeChat.id === data.chat_id) {
      activeChatRole = data.role;
    }
    if (activeChat && activeChat.id === data.chat_id && !$("infoModal").classList.contains("hidden")) {
      loadChatInfo(activeChat.id);
    }
  });

  socket.on("member_removed", (data) => {
    if (activeChat && activeChat.id === data.chat_id) {
      loadChatInfo(activeChat.id);
    }
    loadChats();
  });

  socket.on("removed_from_chat", (data) => {
    const chat = chats.find(c => c.id === data.chat_id);
    const chatName = chat ? chat.name : "گفتگو";
    chats = chats.filter(c => c.id !== data.chat_id);
    renderChatList(chats);
    if (activeChat && activeChat.id === data.chat_id) {
      closeActiveChat();
      showToast(data.reason === "kicked" ? `شما از «${chatName}» حذف شدید` : `از «${chatName}» خارج شدید`);
    }
  });

  socket.on("chat_deleted", (data) => {
    const chat = chats.find(c => c.id === data.chat_id);
    const chatName = chat ? chat.name : "گفتگو";
    chats = chats.filter(c => c.id !== data.chat_id);
    renderChatList(chats);
    if (activeChat && activeChat.id === data.chat_id) {
      closeActiveChat();
      showToast(`«${chatName}» حذف شد`);
    }
  });

  socket.on("presence_update", (data) => {
    let listChanged = false;
    chats.forEach(c => {
      if (c.type === "private" && c.peer_id === data.user_id) {
        c.online = data.online;
        c.last_seen = data.last_seen;
        listChanged = true;
      }
    });
    if (listChanged) renderChatList(chats);

    if (activeChat && activeChat.type === "private" && activeChat.peer_id === data.user_id) {
      activeChat.online = data.online;
      activeChat.last_seen = data.last_seen;
      updateChatHeaderSub(activeChat);
      $("chatAvatarDot").classList.toggle("hidden", !data.online);
    }
  });

  socket.on("messages_read", (data) => {
    if (activeChat && activeChat.id === data.chat_id) {
      chatReadState[data.user_id] = data.last_read_message_id;
      refreshReadTicks();
    }
  });

  socket.on("chat_settings_changed", (data) => {
    const chatInList = chats.find(c => c.id === data.chat_id);
    if (chatInList) chatInList.open_chat = data.open_chat;
    if (activeChat && activeChat.id === data.chat_id) {
      activeChat.open_chat = data.open_chat;
      updateMessageInputAccess();
      if (!$("infoModal").classList.contains("hidden")) {
        $("channelOpenChatCheckbox").checked = !!data.open_chat;
      }
    }
  });

  socket.on("chat_avatar_changed", (data) => {
    const chatInList = chats.find(c => c.id === data.chat_id);
    if (chatInList) { chatInList.avatar_url = data.avatar_url; renderChatList(chats); }
    if (activeChat && activeChat.id === data.chat_id) {
      activeChat.avatar_url = data.avatar_url;
      fillAvatarEl($("chatAvatar"), { avatar_url: activeChat.avatar_url, avatar_color: activeChat.avatar_color, name: activeChat.name });
      if (!$("infoModal").classList.contains("hidden")) {
        fillAvatarEl($("infoModalAvatarPreview"), { avatar_url: activeChat.avatar_url, avatar_color: activeChat.avatar_color, name: activeChat.name });
      }
    }
  });

  socket.on("message_pinned", (data) => {
    if (!activeChat || activeChat.id !== data.chat_id) return;
    pinnedMessages = pinnedMessages.filter(p => p.message_id !== data.pin.message_id);
    pinnedMessages.unshift(data.pin);
    updatePinnedBar();
  });

  socket.on("message_unpinned", (data) => {
    if (!activeChat || activeChat.id !== data.chat_id) return;
    pinnedMessages = pinnedMessages.filter(p => p.message_id !== data.message_id);
    updatePinnedBar();
  });

  socket.on("send_error", (data) => {
    if (data.reason === "channel_restricted") {
      showToast("فقط مالک و مدیران این کانال می‌توانند پیام ارسال کنند");
      if (activeChat && activeChat.id === data.chat_id) updateMessageInputAccess();
    }
  });

  socket.on("profile_updated", (updated) => {
    // Keeps this account's other open tabs/devices in sync (theme, notifications, etc.)
    me = updated;
    applyTheme(me.theme_color, me.theme_mode);
    fillAvatarEl($("meAvatar"), { avatar_url: me.avatar_url, avatar_color: me.avatar_color, name: me.display_name });
    $("meName").textContent = me.display_name;
  });

  registerCallSocketEvents();
}

// ============================================================
// Desktop / mobile browser notifications for new messages
// ============================================================
const originalDocumentTitle = document.title;

window.addEventListener("focus", () => {
  if (activeChat) markChatRead(activeChat.id);
});

function requestNotificationPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
}

function messagePreviewText(msg) {
  switch (msg.message_type) {
    case "image": return "🖼️ عکس";
    case "video": return "🎥 ویدیو";
    case "voice": return "🎤 پیام صوتی";
    case "file": return `📎 فایل: ${msg.file_name || ""}`;
    default: return stripFormattingTokens(msg.content);
  }
}

function notifyIncomingMessage(msg) {
  if (!me || msg.sender_id === me.id) return; // never notify about our own messages

  const chatIsOpenAndFocused = activeChat && activeChat.id === msg.chat_id && document.hasFocus();
  if (chatIsOpenAndFocused) return;

  // Flash the tab title until the user comes back to this tab
  if (document.hidden || !document.hasFocus()) {
    document.title = "پیام جدید • لینکو";
    window.addEventListener("focus", function resetTitle() {
      document.title = originalDocumentTitle;
      window.removeEventListener("focus", resetTitle);
    });
  }

  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (me.notifications_enabled === false) return;

  const chat = chats.find(c => c.id === msg.chat_id);
  const title = chat ? chat.name : msg.sender_name;
  const isGroupOrChannel = chat && chat.type !== "private";
  const body = (isGroupOrChannel ? `${msg.sender_name}: ` : "") + messagePreviewText(msg);
  const icon = (msg.sender_avatar) || "/favicon.svg";

  try {
    const notif = new Notification(title, { body, icon, tag: `linko-chat-${msg.chat_id}` });
    notif.onclick = () => {
      window.focus();
      const targetChat = chats.find(c => c.id === msg.chat_id);
      if (targetChat) openChat(targetChat);
      notif.close();
    };
  } catch (e) {
    // Some browsers/OS combinations reject certain notification options; fail silently.
  }
}

// ============================================================
// Chat list
// ============================================================
async function loadChats() {
  chats = await api("/chats");
  renderChatList(chats);
}

function renderChatList(list) {
  const container = $("chatList");
  container.innerHTML = "";

  if (list.length === 0) {
    container.innerHTML = `<div style="padding:20px;color:var(--text-secondary);text-align:center;font-size:13px;">
      هنوز گفتگویی ندارید. با دکمه بالا یک گفتگو، گروه یا کانال بسازید.</div>`;
    return;
  }

  list.forEach(chat => {
    const el = document.createElement("div");
    el.className = "chat-item" + (activeChat && activeChat.id === chat.id ? " active" : "");
    el.dataset.chatId = chat.id;

    const badge = chat.type === "private" ? "" :
      `<span class="chat-type-badge">${chat.type === "group" ? "گروه" : "کانال"}</span>`;

    const preview = chat.last_message
      ? (chat.last_message.sender_id === me.id ? "شما: " : "") + escapeHtml(stripFormattingTokens(chat.last_message.content))
      : "گفتگو را شروع کنید";

    const time = chat.last_message ? formatChatListTime(chat.last_message.created_at) : "";
    const unread = chat.unread_count > 0
      ? `<span class="unread-badge">${toPersianDigits(chat.unread_count > 99 ? "99+" : chat.unread_count)}</span>`
      : "";

    el.innerHTML = `
      ${avatarHtml({ avatar_url: chat.avatar_url, avatar_color: chat.avatar_color, name: chat.name, online: chat.type === "private" && chat.online })}
      <div class="chat-item-body">
        <div class="chat-item-top">
          <span class="chat-item-name">${escapeHtml(chat.name)} ${badge}</span>
          <span class="chat-item-time">${time}</span>
        </div>
        <div class="chat-item-bottom">
          <span class="chat-item-preview">${preview}</span>
          ${unread}
        </div>
      </div>
    `;
    el.addEventListener("click", () => openChat(chat));
    container.appendChild(el);
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

// ============================================================
// Rich-text compose formatting (bold / italic / highlight / quote)
// ------------------------------------------------------------
// Formatting is stored as plain text with lightweight bracket tokens
// ([b]..[/b], [i]..[/i], [h]..[/h], [q]..[/q]) instead of raw HTML.
// Rendering always goes through buildFormattedFragment(), which only
// ever calls createElement/createTextNode — never innerHTML — so a
// message can never inject arbitrary markup into another user's screen.
// ============================================================
const FORMAT_TAG_MAP = { b: "b", i: "i", h: "mark", q: "span" };

function buildFormattedFragment(text) {
  const root = document.createDocumentFragment();
  const stack = [root];
  const tokenRe = /\[(\/?)(b|i|h|q)\]/g;
  let lastIndex = 0;
  let match;

  function appendText(str) {
    if (!str) return;
    const parts = str.split("\n");
    parts.forEach((part, idx) => {
      const current = stack[stack.length - 1];
      if (part) current.appendChild(document.createTextNode(part));
      if (idx < parts.length - 1) current.appendChild(document.createElement("br"));
    });
  }

  const openKinds = [];
  while ((match = tokenRe.exec(text)) !== null) {
    appendText(text.slice(lastIndex, match.index));
    lastIndex = tokenRe.lastIndex;
    const closing = match[1] === "/";
    const kind = match[2];
    if (!closing) {
      const el = document.createElement(FORMAT_TAG_MAP[kind]);
      if (kind === "h") el.className = "msg-highlight";
      if (kind === "q") el.className = "msg-inline-quote";
      stack[stack.length - 1].appendChild(el);
      stack.push(el);
      openKinds.push(kind);
    } else if (openKinds.length && openKinds[openKinds.length - 1] === kind) {
      openKinds.pop();
      stack.pop();
    }
    // Mismatched/unmatched closing tokens are simply ignored, never break rendering.
  }
  appendText(text.slice(lastIndex));
  return root;
}

function serializeComposeNode(node) {
  let out = "";
  node.childNodes.forEach(child => {
    if (child.nodeType === Node.TEXT_NODE) {
      out += child.textContent;
    } else if (child.nodeType === Node.ELEMENT_NODE) {
      const tag = child.tagName.toLowerCase();
      if (tag === "br") {
        out += "\n";
      } else if (tag === "div" || tag === "p") {
        if (out.length > 0 && !out.endsWith("\n")) out += "\n";
        out += serializeComposeNode(child);
      } else if (tag === "b" || tag === "strong") {
        out += "[b]" + serializeComposeNode(child) + "[/b]";
      } else if (tag === "i" || tag === "em") {
        out += "[i]" + serializeComposeNode(child) + "[/i]";
      } else if (tag === "mark") {
        out += "[h]" + serializeComposeNode(child) + "[/h]";
      } else if (child.classList && child.classList.contains("msg-inline-quote")) {
        out += "[q]" + serializeComposeNode(child) + "[/q]";
      } else {
        out += serializeComposeNode(child);
      }
    }
  });
  return out;
}

function stripFormattingTokens(text) {
  return (text || "").replace(/\[\/?(?:b|i|h|q)\]/g, "");
}

function wrapSelectionWith(tagName, className) {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
  const range = sel.getRangeAt(0);
  if (!$("messageInput").contains(range.commonAncestorContainer)) return;

  const wrapper = document.createElement(tagName);
  if (className) wrapper.className = className;
  try {
    range.surroundContents(wrapper);
  } catch (e) {
    const frag = range.extractContents();
    wrapper.appendChild(frag);
    range.insertNode(wrapper);
  }
  sel.removeAllRanges();
  const newRange = document.createRange();
  newRange.selectNodeContents(wrapper);
  sel.addRange(newRange);
  hideFormatToolbar();
  $("messageInput").focus();
}

function updateChatListWithMessage(msg) {
  const chat = chats.find(c => c.id === msg.chat_id);
  if (chat) {
    chat.last_message = {
      id: msg.id, content: messagePreviewText(msg), created_at: msg.created_at,
      sender_id: msg.sender_id, sender_name: msg.sender_name,
    };
    const chatIsOpenAndFocused = activeChat && activeChat.id === msg.chat_id && document.hasFocus();
    if (msg.sender_id !== me.id && !chatIsOpenAndFocused) {
      chat.unread_count = (chat.unread_count || 0) + 1;
    }
    chats.sort((a, b) => {
      const ta = a.last_message ? a.last_message.created_at : "0";
      const tb = b.last_message ? b.last_message.created_at : "0";
      return tb.localeCompare(ta);
    });
  } else {
    loadChats();
    return;
  }
  renderChatList(chats);
}

$("chatSearchInput").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  const filtered = chats.filter(c => c.name.toLowerCase().includes(q));
  renderChatList(filtered);
});

// ============================================================
// Open chat / messages
// ============================================================
function updateMessageInputAccess() {
  if (!activeChat) return;
  canPostMessages = activeChat.type !== "channel" || activeChat.open_chat || ["owner", "admin"].includes(activeChatRole);
  $("messageForm").classList.toggle("hidden", !canPostMessages);
  $("channelReadonlyNotice").classList.toggle("hidden", canPostMessages);
  $("pollBtn").classList.toggle("hidden", !canPostMessages || !["group", "channel"].includes(activeChat.type));
  if (!canPostMessages) cancelReplyOrEdit();
}

async function openChat(chat) {
  activeChat = chat;
  inSavedMessages = false;
  document.querySelectorAll(".chat-item").forEach(el => {
    el.classList.toggle("active", el.dataset.chatId == chat.id);
  });
  $("savedMessagesItem").classList.remove("active");

  $("emptyState").classList.add("hidden");
  $("chatWindow").classList.remove("hidden");
  $("appScreen").classList.add("chat-open");

  fillAvatarEl($("chatAvatar"), { avatar_url: chat.avatar_url, avatar_color: chat.avatar_color, name: chat.name });
  $("chatAvatar").classList.remove("saved-messages-avatar");
  $("chatAvatarDot").classList.toggle("hidden", !(chat.type === "private" && chat.online));
  $("chatHeaderName").textContent = chat.name;
  updateChatHeaderSub(chat);
  $("callBtn").classList.toggle("hidden", chat.type !== "private");
  $("chatInfoBtn").classList.remove("hidden");

  socket.emit("join_chat", { chat_id: chat.id });

  activeChatRole = null;
  try {
    const members = await api(`/chats/${chat.id}/members`);
    const meMember = members.find(m => m.id === me.id);
    activeChatRole = meMember ? meMember.role : null;
  } catch (e) { /* ignore, delete button will just be limited to own messages */ }

  updateMessageInputAccess();

  pinnedMessages = [];
  if (chat.type === "group" || chat.type === "channel") {
    try { pinnedMessages = await api(`/chats/${chat.id}/pins`); } catch (e) { /* ignore */ }
  }
  updatePinnedBar();

  cancelReplyOrEdit();
  renderedMessages = new Map();
  $("messagesContainer").innerHTML = `<div style="text-align:center;color:var(--text-secondary);padding:20px;">در حال بارگذاری...</div>`;
  const messages = await api(`/chats/${chat.id}/messages`);
  $("messagesContainer").innerHTML = "";
  let lastDay = null;
  messages.forEach(m => {
    const parsedDay = parseServerDate(m.created_at);
    const day = parsedDay ? parsedDay.toLocaleDateString("fa-IR", { year: "numeric", month: "long", day: "numeric" }) : "";
    if (day !== lastDay) {
      lastDay = day;
      const divider = document.createElement("div");
      divider.className = "day-divider";
      divider.innerHTML = `<span>${escapeHtml(day)}</span>`;
      $("messagesContainer").appendChild(divider);
    }
    renderMessage(m, false);
  });
  scrollMessagesToBottom();

  chatReadState = {};
  try {
    const readStateArr = await api(`/chats/${chat.id}/read-state`);
    readStateArr.forEach(r => { chatReadState[r.user_id] = r.last_read_message_id; });
  } catch (e) { /* ignore */ }
  refreshReadTicks();

  chat.unread_count = 0;
  renderChatList(chats);
  markChatRead(chat.id);
}

// ============================================================
// Saved Messages (personal notes-to-self — only ever visible to you)
// ============================================================
async function openSavedMessages() {
  activeChat = { id: "saved", type: "saved", name: "پیام‌های ذخیره‌شده", peer_id: null, avatar_url: null, avatar_color: null };
  activeChatRole = null;
  inSavedMessages = true;
  pinnedMessages = [];

  document.querySelectorAll(".chat-item").forEach(el => el.classList.remove("active"));
  $("savedMessagesItem").classList.add("active");

  $("emptyState").classList.add("hidden");
  $("chatWindow").classList.remove("hidden");
  $("appScreen").classList.add("chat-open");

  $("chatAvatar").style.background = "";
  $("chatAvatar").classList.add("saved-messages-avatar");
  $("chatAvatar").innerHTML = '<i class="fa-solid fa-bookmark"></i>';
  $("chatAvatarDot").classList.add("hidden");
  $("chatHeaderName").textContent = "پیام‌های ذخیره‌شده";
  $("chatHeaderSub").textContent = "فقط شما آن را می‌بینید";
  $("chatHeaderSub").classList.remove("online-text");
  $("callBtn").classList.add("hidden");
  $("chatInfoBtn").classList.add("hidden");
  $("pinnedBar").classList.add("hidden");

  updateMessageInputAccess();
  cancelReplyOrEdit();
  renderedMessages = new Map();
  $("messagesContainer").innerHTML = `<div style="text-align:center;color:var(--text-secondary);padding:20px;">در حال بارگذاری...</div>`;

  let items = [];
  try { items = await api("/saved"); } catch (e) { /* ignore */ }
  $("messagesContainer").innerHTML = "";
  let lastDay = null;
  items.forEach(item => {
    const day = item.created_at.split(" ")[0];
    if (day !== lastDay) {
      lastDay = day;
      const divider = document.createElement("div");
      divider.className = "day-divider";
      divider.innerHTML = `<span>${day}</span>`;
      $("messagesContainer").appendChild(divider);
    }
    renderSavedItem(item);
  });
  scrollMessagesToBottom();
}

function renderSavedItem(item) {
  renderMessage({
    id: item.id,
    sender_id: me.id,
    sender_name: me.display_name,
    content: item.content,
    message_type: item.message_type,
    file_url: item.file_url,
    file_name: item.file_name,
    file_size: item.file_size,
    created_at: item.created_at,
    edited_at: item.edited_at,
    forwarded_from_name: item.forwarded_from_name,
    reply_to: null,
    reactions: [],
  });
}

$("savedMessagesItem").addEventListener("click", openSavedMessages);

function updatePinnedBar() {
  const bar = $("pinnedBar");
  if (!pinnedMessages || pinnedMessages.length === 0) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  const latest = pinnedMessages[0];
  $("pinnedBarPreview").textContent = `${latest.sender_name}: ${stripFormattingTokens(latest.preview)}`;
  const extra = pinnedMessages.length - 1;
  $("pinnedBarCount").classList.toggle("hidden", extra <= 0);
  $("pinnedBarCount").textContent = extra > 0 ? `+${toPersianDigits(extra)}` : "";
  const canUnpin = ["owner", "admin"].includes(activeChatRole);
  $("unpinCurrentBtn").classList.toggle("hidden", !canUnpin);
}

$("pinnedBar").addEventListener("click", (e) => {
  if (e.target.closest("#unpinCurrentBtn")) return;
  if (pinnedMessages.length === 0) return;
  scrollToMessage(pinnedMessages[0].message_id);
});

$("unpinCurrentBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  if (pinnedMessages.length === 0 || !activeChat) return;
  togglePin(pinnedMessages[0].message_id, true);
});

async function togglePin(messageId, currentlyPinned) {
  if (!activeChat) return;
  try {
    if (currentlyPinned) {
      await api(`/chats/${activeChat.id}/pins/${messageId}`, { method: "DELETE" });
    } else {
      await api(`/chats/${activeChat.id}/pins`, { method: "POST", body: { message_id: messageId } });
    }
    // UI refresh happens via the "message_pinned"/"message_unpinned" socket broadcast
  } catch (err) {
    showToast(err.message || "خطا در تغییر وضعیت پین");
  }
}

$("backBtn").addEventListener("click", () => {
  $("appScreen").classList.remove("chat-open");
});

function closeActiveChat() {
  activeChat = null;
  activeChatRole = null;
  inSavedMessages = false;
  pinnedMessages = [];
  $("pinnedBar").classList.add("hidden");
  $("savedMessagesItem").classList.remove("active");
  cancelReplyOrEdit();
  $("chatWindow").classList.add("hidden");
  $("emptyState").classList.remove("hidden");
  $("appScreen").classList.remove("chat-open");
}

// ============================================================
// Read receipts ("seen" ticks)
// ============================================================
async function markChatRead(chatId) {
  try {
    await api(`/chats/${chatId}/read`, { method: "POST" });
    const chat = chats.find(c => c.id === chatId);
    if (chat) { chat.unread_count = 0; renderChatList(chats); }
  } catch (e) { /* ignore */ }
}

function computeIsRead(messageId) {
  const others = Object.keys(chatReadState).map(Number).filter(uid => uid !== me.id);
  if (others.length === 0) return false;
  return others.every(uid => (chatReadState[uid] || 0) >= messageId);
}

function refreshReadTicks() {
  document.querySelectorAll(".msg-tick").forEach(el => {
    const id = Number(el.dataset.messageId);
    const icon = el.querySelector("i");
    const isRead = computeIsRead(id);
    icon.className = isRead ? "fa-solid fa-check-double" : "fa-solid fa-check";
  });
}

function renderMessage(msg) {
  renderedMessages.set(msg.id, msg);

  const row = document.createElement("div");
  row.className = "msg-row " + (msg.sender_id === me.id ? "out" : "in");
  row.dataset.messageId = msg.id;

  const showSender = activeChat.type !== "private" && msg.sender_id !== me.id;
  const senderLine = showSender ? `<div class="bubble-sender">${escapeHtml(msg.sender_name)}</div>` : "";

  const forwardedLine = msg.forwarded_from_name
    ? `<div class="bubble-forwarded-tag"><i class="fa-solid fa-share"></i> فوروارد شده از ${escapeHtml(msg.forwarded_from_name)}</div>`
    : "";

  const replyQuote = msg.reply_to
    ? `<div class="bubble-reply-quote" data-scroll-to="${msg.reply_to.id}">
         <div class="bubble-reply-sender">${escapeHtml(msg.reply_to.sender_name)}</div>
         <div class="bubble-reply-preview">${escapeHtml(stripFormattingTokens(msg.reply_to.preview))}</div>
       </div>`
    : "";

  const editedTag = msg.edited_at ? `<span class="bubble-edited-tag">ویرایش‌شده</span>` : "";
  const tickHtml = msg.sender_id === me.id
    ? `<span class="msg-tick" data-message-id="${msg.id}"><i class="fa-solid fa-check"></i></span>`
    : "";
  const timeLine = `<div class="bubble-time">${editedTag}<span>${formatTime(msg.created_at)}</span>${tickHtml}</div>`;
  const reactionsHtml = buildReactionsHtml(msg);

  let bodyHtml = "";
  const type = msg.message_type || "text";

  if (type === "image") {
    bodyHtml = `
      <div class="bubble-media-image">
        <img src="${msg.file_url}" alt="عکس" loading="lazy">
      </div>
      ${msg.content ? `<div>${escapeHtml(msg.content)}</div>` : ""}
    `;
  } else if (type === "video") {
    bodyHtml = `
      <div class="bubble-media-video">
        <video src="${msg.file_url}" controls></video>
      </div>
      ${msg.content ? `<div>${escapeHtml(msg.content)}</div>` : ""}
    `;
  } else if (type === "voice") {
    bodyHtml = `
      <div class="voice-player" data-src="${msg.file_url}">
        <button type="button" class="voice-play-btn"><i class="fa-solid fa-play"></i></button>
        <div class="voice-progress-track">
          <div class="voice-progress-fill"></div>
        </div>
        <span class="voice-duration">۰:۰۰</span>
      </div>
    `;
  } else if (type === "file") {
    bodyHtml = `
      <a class="bubble-media-file" href="${msg.file_url}" download="${escapeHtml(msg.file_name || "")}" target="_blank" rel="noopener">
        <div class="bubble-file-icon"><i class="fa-solid ${fileIconFor(msg.file_name)}"></i></div>
        <div class="bubble-file-info">
          <div class="bubble-file-name">${escapeHtml(msg.file_name || "فایل")}</div>
          <div class="bubble-file-size">${formatBytes(msg.file_size)}</div>
        </div>
        <i class="fa-solid fa-download bubble-file-download"></i>
      </a>
    `;
  } else if (type === "poll") {
    bodyHtml = buildPollHtml(msg);
  } else {
    bodyHtml = `<div class="bubble-text" data-formatted="1"></div>`;
  }

  row.innerHTML = `<div class="bubble">${forwardedLine}${replyQuote}${senderLine}${bodyHtml}${reactionsHtml}${timeLine}</div>`;

  const textEl = row.querySelector(".bubble-text[data-formatted]");
  if (textEl) textEl.appendChild(buildFormattedFragment(msg.content));

  const img = row.querySelector(".bubble-media-image img");
  if (img) img.addEventListener("click", (e) => { e.stopPropagation(); openLightbox(msg.file_url); });

  const replyQuoteEl = row.querySelector(".bubble-reply-quote");
  if (replyQuoteEl) replyQuoteEl.addEventListener("click", (e) => {
    e.stopPropagation();
    scrollToMessage(Number(replyQuoteEl.dataset.scrollTo));
  });

  wireReactionPills(row, msg.id);
  if (type === "poll") wirePollOptions(row, msg);

  if (type === "voice") setupVoicePlayer(row);

  const bubbleEl = row.querySelector(".bubble");
  bubbleEl.addEventListener("click", (e) => {
    // Interactive children (links, media controls, image, reply quote, reactions, poll options) handle their own clicks.
    if (e.target.closest("a, video, audio, .voice-player, .bubble-media-image, .bubble-reply-quote, .msg-reactions, .poll-bubble")) return;
    openMessageActionMenu(e, msg.id);
  });

  $("messagesContainer").appendChild(row);
}

// ============================================================
// Reactions (Telegram/WhatsApp-style emoji reactions on messages)
// ============================================================
function buildReactionsHtml(msg) {
  const reactions = msg.reactions || [];
  if (reactions.length === 0) return "";
  const pills = reactions.map(r => {
    const mine = r.user_ids.includes(me.id);
    return `<button type="button" class="reaction-pill ${mine ? "mine" : ""}" data-emoji="${r.emoji}">
      <span class="reaction-emoji">${r.emoji}</span><span class="reaction-count">${toPersianDigits(r.count)}</span>
    </button>`;
  }).join("");
  return `<div class="msg-reactions">${pills}</div>`;
}

function wireReactionPills(row, messageId) {
  row.querySelectorAll(".reaction-pill").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      reactToMessage(messageId, btn.dataset.emoji);
    });
  });
}

async function reactToMessage(messageId, emoji) {
  try {
    await api(`/messages/${messageId}/react`, { method: "POST", body: { emoji } });
    // UI updates via the "reaction_updated" socket broadcast (also reaches the person who reacted)
  } catch (err) {
    showToast(err.message || "ثبت ری‌اکشن با خطا مواجه شد");
  }
}

function scrollToMessage(messageId) {
  const row = document.querySelector(`.msg-row[data-message-id="${messageId}"]`);
  if (!row) { showToast("پیام اصلی در این بازه بارگذاری نشده است"); return; }
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  row.classList.add("highlight-flash");
  setTimeout(() => row.classList.remove("highlight-flash"), 1200);
}

// Keeps reply-quote previews correct when the original message they quote
// gets edited or deleted after they were already rendered on screen.
function refreshStaleReplyQuotes(originalMessageId, newPreviewText) {
  renderedMessages.forEach((m) => {
    if (m.reply_to && m.reply_to.id === originalMessageId) {
      m.reply_to.preview = newPreviewText;
      const row = document.querySelector(`.msg-row[data-message-id="${m.id}"]`);
      const previewEl = row && row.querySelector(".bubble-reply-preview");
      if (previewEl) previewEl.textContent = stripFormattingTokens(newPreviewText);
    }
  });
}

function removeStaleReplyQuotes(deletedMessageId) {
  renderedMessages.forEach((m) => {
    if (m.reply_to && m.reply_to.id === deletedMessageId) {
      m.reply_to = null;
      const row = document.querySelector(`.msg-row[data-message-id="${m.id}"]`);
      const quoteEl = row && row.querySelector(".bubble-reply-quote");
      if (quoteEl) quoteEl.remove();
    }
  });
}

// ============================================================
// Custom voice message player (play/pause, seek, live duration)
// ============================================================
let currentlyPlayingAudio = null;
let currentlyPlayingIcon = null;

function formatDuration(totalSeconds) {
  if (!isFinite(totalSeconds) || totalSeconds < 0) totalSeconds = 0;
  const m = Math.floor(totalSeconds / 60);
  const s = Math.floor(totalSeconds % 60);
  return toPersianDigits(`${m}:${String(s).padStart(2, "0")}`);
}

function setupVoicePlayer(row) {
  const container = row.querySelector(".voice-player");
  if (!container) return;

  const src = container.dataset.src;
  const playBtn = container.querySelector(".voice-play-btn");
  const icon = playBtn.querySelector("i");
  const track = container.querySelector(".voice-progress-track");
  const fill = container.querySelector(".voice-progress-fill");
  const durationEl = container.querySelector(".voice-duration");

  let audio = null;

  function stopAndReset() {
    icon.className = "fa-solid fa-play";
    fill.style.width = "0%";
    if (audio) durationEl.textContent = formatDuration(audio.duration || 0);
  }

  function ensureAudio() {
    if (audio) return audio;
    audio = new Audio(src);
    audio.preload = "metadata";
    audio.addEventListener("loadedmetadata", () => {
      if (isFinite(audio.duration)) durationEl.textContent = formatDuration(audio.duration);
    });
    audio.addEventListener("timeupdate", () => {
      const pct = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
      fill.style.width = pct + "%";
      durationEl.textContent = formatDuration(Math.max(audio.duration - audio.currentTime, 0));
    });
    audio.addEventListener("ended", () => {
      stopAndReset();
      if (currentlyPlayingAudio === audio) { currentlyPlayingAudio = null; currentlyPlayingIcon = null; }
    });
    return audio;
  }

  playBtn.addEventListener("click", () => {
    const a = ensureAudio();
    if (a.paused) {
      // only one voice message plays at a time, telegram/whatsapp-style
      if (currentlyPlayingAudio && currentlyPlayingAudio !== a) {
        currentlyPlayingAudio.pause();
        if (currentlyPlayingIcon) currentlyPlayingIcon.className = "fa-solid fa-play";
      }
      a.play();
      icon.className = "fa-solid fa-pause";
      currentlyPlayingAudio = a;
      currentlyPlayingIcon = icon;
    } else {
      a.pause();
      icon.className = "fa-solid fa-play";
      currentlyPlayingAudio = null;
      currentlyPlayingIcon = null;
    }
  });

  track.addEventListener("click", (e) => {
    const a = ensureAudio();
    if (!a.duration) return;
    const rect = track.getBoundingClientRect();
    const ratio = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
    a.currentTime = ratio * a.duration;
  });
}

function confirmDeleteMessage(messageId) {
  if (!confirm("آیا از حذف این پیام مطمئن هستید؟ این عملیات قابل بازگشت نیست.")) return;
  deleteMessage(messageId);
}

async function deleteMessage(messageId) {
  try {
    if (inSavedMessages) {
      await api(`/saved/${messageId}`, { method: "DELETE" });
      const row = document.querySelector(`.msg-row[data-message-id="${messageId}"]`);
      if (row) row.remove();
      renderedMessages.delete(messageId);
    } else {
      await api(`/messages/${messageId}`, { method: "DELETE" });
      // UI removal happens via the "message_deleted" socket broadcast (also reaches the sender)
    }
  } catch (err) {
    showToast(err.message || "حذف پیام با خطا مواجه شد");
  }
}

// ============================================================
// Message action menu (reply / edit / delete) — opened by clicking a message
// ============================================================
function openMessageActionMenu(e, messageId) {
  const msg = renderedMessages.get(messageId);
  if (!msg) return;

  messageActionMenuTargetId = messageId;
  const isTextMsg = msg.message_type === "text";

  if (inSavedMessages) {
    $("quickReactionRow").classList.add("hidden");
    $("messageActionReply").classList.add("hidden");
    $("messageActionForward").classList.add("hidden");
    $("messageActionPin").classList.add("hidden");
    $("messageActionCopy").classList.toggle("hidden", !isTextMsg);
    $("messageActionEdit").classList.toggle("hidden", !isTextMsg);
    $("messageActionDelete").classList.remove("hidden");
  } else {
    const canEdit = msg.sender_id === me.id && isTextMsg;
    const canDelete = msg.sender_id === me.id || activeChatRole === "owner" || activeChatRole === "admin";
    const canPin = ["group", "channel"].includes(activeChat.type) && ["owner", "admin"].includes(activeChatRole);

    $("quickReactionRow").classList.remove("hidden");
    $("messageActionReply").classList.toggle("hidden", !canPostMessages);
    $("messageActionForward").classList.toggle("hidden", msg.message_type === "poll");
    $("messageActionCopy").classList.toggle("hidden", !isTextMsg);
    $("messageActionPin").classList.toggle("hidden", !canPin);
    if (canPin) updatePinButtonLabel(messageId);
    $("messageActionEdit").classList.toggle("hidden", !canEdit);
    $("messageActionDelete").classList.toggle("hidden", !canDelete);
  }

  const menu = $("messageActionMenu");
  menu.classList.remove("hidden");
  // Reset any previous inline sizing so offsetWidth/offsetHeight reflect this menu's
  // actual current content (button set differs between saved-mode and normal chats).
  menu.style.left = "0px";
  menu.style.top = "0px";

  // Position near the click, clamped against the REAL rendered size so it can never
  // spill off-screen — this matters most on narrow mobile widths where a hardcoded
  // width guess used to push the menu (and its 8-emoji reaction row) off the edge.
  const menuWidth = menu.offsetWidth;
  const menuHeight = menu.offsetHeight;
  let left = e.clientX;
  let top = e.clientY;
  left = Math.min(left, window.innerWidth - menuWidth - 10);
  top = Math.min(top, window.innerHeight - menuHeight - 10);
  menu.style.left = `${Math.max(10, left)}px`;
  menu.style.top = `${Math.max(10, top)}px`;
}

function updatePinButtonLabel(messageId) {
  const isPinned = pinnedMessages.some(p => p.message_id === messageId);
  const btn = $("messageActionPin");
  btn.innerHTML = isPinned
    ? '<i class="fa-solid fa-thumbtack"></i> برداشتن پین'
    : '<i class="fa-solid fa-thumbtack"></i> پین کردن';
  btn.dataset.pinned = isPinned ? "1" : "0";
}

function closeMessageActionMenu() {
  $("messageActionMenu").classList.add("hidden");
  messageActionMenuTargetId = null;
}

document.addEventListener("click", (e) => {
  if (!e.target.closest("#messageActionMenu") && !e.target.closest(".bubble")) {
    closeMessageActionMenu();
  }
});

$("messageActionMenu").addEventListener("click", (e) => {
  const emojiBtn = e.target.closest("button[data-emoji]");
  if (emojiBtn && messageActionMenuTargetId) {
    reactToMessage(messageActionMenuTargetId, emojiBtn.dataset.emoji);
    closeMessageActionMenu();
    return;
  }

  const btn = e.target.closest("button[data-action]");
  if (!btn || !messageActionMenuTargetId) return;
  const msg = renderedMessages.get(messageActionMenuTargetId);
  const action = btn.dataset.action;
  const wasPinned = btn.dataset.pinned === "1";
  closeMessageActionMenu();
  if (!msg) return;

  if (action === "reply") startReplyTo(msg);
  if (action === "edit") startEditMessage(msg);
  if (action === "delete") confirmDeleteMessage(msg.id);
  if (action === "copy") copyMessageText(msg);
  if (action === "forward") openForwardModal(msg.id);
  if (action === "pin") togglePin(msg.id, wasPinned);
});

function copyMessageText(msg) {
  const plain = stripFormattingTokens(msg.content);
  if (!plain) return;
  navigator.clipboard.writeText(plain)
    .then(() => showToast("متن پیام کپی شد"))
    .catch(() => showToast("کپی متن با خطا مواجه شد"));
}

function startReplyTo(msg) {
  editingMessage = null;
  replyingToMessage = msg;
  $("replyEditIcon").className = "fa-solid fa-reply";
  $("replyEditTitle").textContent = `پاسخ به ${msg.sender_id === me.id ? "خودتان" : msg.sender_name}`;
  $("replyEditPreview").textContent = stripFormattingTokens(messagePreviewText(msg));
  $("replyEditBar").classList.remove("hidden");
  $("messageInput").innerHTML = "";
  $("messageInput").focus();
}

function startEditMessage(msg) {
  replyingToMessage = null;
  editingMessage = msg;
  $("replyEditIcon").className = "fa-solid fa-pen";
  $("replyEditTitle").textContent = "ویرایش پیام";
  $("replyEditPreview").textContent = stripFormattingTokens(msg.content);
  $("replyEditBar").classList.remove("hidden");
  const input = $("messageInput");
  input.innerHTML = "";
  input.appendChild(buildFormattedFragment(msg.content));
  input.focus();
}

function cancelReplyOrEdit() {
  replyingToMessage = null;
  editingMessage = null;
  $("replyEditBar").classList.add("hidden");
  $("messageInput").innerHTML = "";
  hideFormatToolbar();
}

$("cancelReplyEditBtn").addEventListener("click", cancelReplyOrEdit);

async function editMessageApi(messageId, content) {
  try {
    if (inSavedMessages) {
      const updated = await api(`/saved/${messageId}`, { method: "PUT", body: { content } });
      renderedMessages.set(messageId, { ...renderedMessages.get(messageId), content: updated.content, edited_at: updated.edited_at });
      const row = document.querySelector(`.msg-row[data-message-id="${messageId}"]`);
      if (row) {
        const textEl = row.querySelector(".bubble-text");
        if (textEl) { textEl.innerHTML = ""; textEl.appendChild(buildFormattedFragment(updated.content)); }
        if (!row.querySelector(".bubble-edited-tag")) {
          const timeEl = row.querySelector(".bubble-time");
          if (timeEl) timeEl.insertAdjacentHTML("afterbegin", `<span class="bubble-edited-tag">ویرایش‌شده</span>`);
        }
      }
    } else {
      await api(`/messages/${messageId}`, { method: "PUT", body: { content } });
      // UI update happens via the "message_edited" socket broadcast (also reaches the editor)
    }
  } catch (err) {
    showToast(err.message || "ویرایش پیام با خطا مواجه شد");
  }
}

function openLightbox(url) {
  const overlay = document.createElement("div");
  overlay.className = "lightbox-overlay";
  overlay.innerHTML = `<img src="${url}" alt="">`;
  overlay.addEventListener("click", () => overlay.remove());
  document.body.appendChild(overlay);
}

function scrollMessagesToBottom() {
  const c = $("messagesContainer");
  c.scrollTop = c.scrollHeight;
}

$("messageForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("messageInput");
  const plainText = input.textContent.trim();
  if (!plainText || !activeChat || sendingMessage) return;
  const content = serializeComposeNode(input).trim();

  if (editingMessage) {
    const editId = editingMessage.id;
    cancelReplyOrEdit();
    editMessageApi(editId, content);
    return;
  }

  if (inSavedMessages) {
    input.innerHTML = "";
    try {
      const item = await api("/saved", { method: "POST", body: { content, message_type: "text" } });
      renderSavedItem(item);
      scrollMessagesToBottom();
    } catch (err) {
      showToast(err.message || "ذخیره یادداشت با خطا مواجه شد");
    }
    return;
  }

  const replyToId = replyingToMessage ? replyingToMessage.id : undefined;
  const payload = { chat_id: activeChat.id, content, message_type: "text", reply_to_id: replyToId };
  sendingMessage = true;

  // Use Socket.IO when it is connected. If it is disconnected, use REST.
  // Do NOT retry REST after a socket timeout: the server may already have
  // committed the message while the acknowledgement was delayed, which
  // would create a duplicate message.
  const sendViaRest = async () => {
    return await api(`/chats/${activeChat.id}/messages`, {
      method: "POST",
      body: { content, message_type: "text", reply_to_id: replyToId },
    });
  };

  try {
    if (!socket || !socket.connected) {
      await sendViaRest();
    } else {
      await new Promise((resolve, reject) => {
        let settled = false;
        const finish = (fn, value) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          fn(value);
        };
        // The server acknowledgement is expected quickly. If it does not
        // arrive, fail without sending the same message through REST.
        const timer = setTimeout(() => finish(reject, new Error("تأیید ارسال پیام دریافت نشد؛ لطفاً اتصال را بررسی و دوباره تلاش کنید")), 10000);
        socket.emit("send_message", payload, (result) => {
          if (result && result.ok) {
            finish(resolve, result);
          } else {
            finish(reject, new Error((result && result.error) || "ارسال پیام ناموفق بود"));
          }
        });
      });
    }
    input.innerHTML = "";
    cancelReplyOrEdit();
  } catch (err) {
    showToast(err.message || "ارسال پیام ناموفق بود");
  } finally {
    sendingMessage = false;
  }
});

$("messageInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("messageForm").requestSubmit();
  } else if (e.key === "Enter" && e.shiftKey) {
    e.preventDefault();
    const sel = window.getSelection();
    if (sel.rangeCount) {
      const range = sel.getRangeAt(0);
      range.deleteContents();
      const br = document.createElement("br");
      range.insertNode(br);
      range.setStartAfter(br);
      range.setEndAfter(br);
      sel.removeAllRanges();
      sel.addRange(range);
    }
  }
});

let lastTypingEmitAt = 0;
$("messageInput").addEventListener("input", () => {
  if (!activeChat) return;
  // Keep the :empty placeholder working even after deleting all text down to a stray <br>.
  if ($("messageInput").innerHTML === "<br>") $("messageInput").innerHTML = "";

  // Throttle: only notify the other side at most once every 2.5s while the user keeps typing,
  // instead of firing a socket event on every single keystroke.
  const now = Date.now();
  if (now - lastTypingEmitAt > 2500) {
    lastTypingEmitAt = now;
    socket.emit("typing", { chat_id: activeChat.id });
  }
});

$("messageInput").addEventListener("focus", () => {
  // give the mobile keyboard time to open before we scroll the last message into view
  setTimeout(() => { setAppHeight(); scrollMessagesToBottom(); }, 300);
});

// ------------------------------------------------------------
// Floating format toolbar (bold / italic / highlight / quote)
// ------------------------------------------------------------
function showFormatToolbar(rect) {
  const toolbar = $("formatToolbar");
  toolbar.classList.remove("hidden");
  const toolbarWidth = toolbar.offsetWidth || 170;
  const toolbarHeight = toolbar.offsetHeight || 46;
  let left = rect.left + rect.width / 2 - toolbarWidth / 2;
  let top = rect.top - toolbarHeight - 10;
  if (top < 8) top = rect.bottom + 10; // flip below the selection if too close to the top
  left = Math.max(8, Math.min(left, window.innerWidth - toolbarWidth - 8));
  toolbar.style.left = `${left}px`;
  toolbar.style.top = `${top}px`;
}

function hideFormatToolbar() {
  $("formatToolbar").classList.add("hidden");
}

document.addEventListener("selectionchange", () => {
  const sel = window.getSelection();
  const input = $("messageInput");
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) { hideFormatToolbar(); return; }
  const range = sel.getRangeAt(0);
  if (!input.contains(range.commonAncestorContainer)) { hideFormatToolbar(); return; }
  showFormatToolbar(range.getBoundingClientRect());
});

document.addEventListener("mousedown", (e) => {
  if (!e.target.closest("#formatToolbar") && !e.target.closest("#messageInput")) {
    hideFormatToolbar();
  }
});

$("formatToolbar").addEventListener("mousedown", (e) => {
  // Prevent the compose box from losing its selection when a toolbar button is pressed.
  e.preventDefault();
});

$("formatToolbar").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-format]");
  if (!btn) return;
  const format = btn.dataset.format;
  if (format === "bold") wrapSelectionWith("b");
  if (format === "italic") wrapSelectionWith("i");
  if (format === "highlight") wrapSelectionWith("mark", "msg-highlight");
  if (format === "quote") wrapSelectionWith("span", "msg-inline-quote");
});

// ============================================================
// File / image / video upload with progress
// ============================================================
$("attachBtn").addEventListener("click", () => {
  if (!activeChat) { showToast("ابتدا یک گفتگو انتخاب کنید"); return; }
  $("fileInput").click();
});

$("fileInput").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) uploadAndSend(file, { isVoice: false });
  e.target.value = "";
});

function setUploadProgress(percent, label) {
  const bar = $("uploadProgressBar");
  bar.classList.remove("hidden");
  $("uploadProgressFill").style.width = percent + "%";
  $("uploadProgressPercent").textContent = toPersianDigits(percent) + "٪";
  if (label) $("uploadProgressLabel").textContent = label;
}

function hideUploadProgress() {
  $("uploadProgressBar").classList.add("hidden");
  $("uploadProgressFill").style.width = "0%";
}

function toPersianDigits(n) {
  const fa = ["۰","۱","۲","۳","۴","۵","۶","۷","۸","۹"];
  return String(n).replace(/[0-9]/g, d => fa[d]);
}

function uploadAndSend(fileOrBlob, { isVoice, fileName } = {}) {
  if (!activeChat) return;

  const formData = new FormData();
  const name = fileName || fileOrBlob.name || (isVoice ? "voice-message.webm" : "file");
  formData.append("file", fileOrBlob, name);
  if (isVoice) formData.append("is_voice", "1");

  setUploadProgress(0, isVoice ? "در حال ارسال پیام صوتی..." : "در حال ارسال فایل...");

  const xhr = new XMLHttpRequest();
  xhr.open("POST", API + "/upload");
  xhr.withCredentials = true;

  xhr.upload.onprogress = (evt) => {
    if (evt.lengthComputable) {
      const percent = Math.round((evt.loaded / evt.total) * 100);
      setUploadProgress(percent);
    }
  };

  xhr.onload = () => {
    hideUploadProgress();
    let data;
    try { data = JSON.parse(xhr.responseText); } catch (e) { data = {}; }

    if (xhr.status !== 200) {
      showToast(data.error || "ارسال فایل با خطا مواجه شد");
      return;
    }

    if (inSavedMessages) {
      api("/saved", {
        method: "POST",
        body: {
          content: "",
          message_type: data.message_type,
          file_url: data.file_url,
          file_name: data.file_name,
          file_size: data.file_size,
        },
      }).then(item => {
        renderSavedItem(item);
        scrollMessagesToBottom();
      }).catch(err => showToast(err.message || "ذخیره فایل با خطا مواجه شد"));
      return;
    }

    socket.emit("send_message", {
      chat_id: activeChat.id,
      content: "",
      message_type: data.message_type,
      file_url: data.file_url,
      file_name: data.file_name,
      file_size: data.file_size,
      reply_to_id: replyingToMessage ? replyingToMessage.id : undefined,
    });
    cancelReplyOrEdit();
  };

  xhr.onerror = () => {
    hideUploadProgress();
    showToast("خطا در برقراری ارتباط با سرور");
  };

  xhr.send(formData);
}

// ============================================================
// Voice recording (MediaRecorder API)
// ============================================================
let mediaRecorder = null;
let recordedChunks = [];
let recordingStartedAt = null;
let recordingTimerInterval = null;
let recordingStream = null;

$("micBtn").addEventListener("click", async () => {
  if (!activeChat) { showToast("ابتدا یک گفتگو انتخاب کنید"); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showToast("مرورگر شما از ضبط صدا پشتیبانی نمی‌کند");
    return;
  }
  try {
    recordingStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    showToast("دسترسی به میکروفون رد شد");
    return;
  }
  startRecording();
});

function startRecording() {
  recordedChunks = [];
  mediaRecorder = new MediaRecorder(recordingStream);
  mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) recordedChunks.push(e.data); };
  mediaRecorder.start();

  recordingStartedAt = Date.now();
  $("voiceRecordingBar").classList.remove("hidden");
  $("messageForm").classList.add("hidden");
  updateRecordingTimer();
  recordingTimerInterval = setInterval(updateRecordingTimer, 500);
}

function updateRecordingTimer() {
  const elapsed = Math.floor((Date.now() - recordingStartedAt) / 1000);
  const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");
  $("recordingTimer").textContent = toPersianDigits(`${mm}:${ss}`);
}

function stopRecordingUI() {
  clearInterval(recordingTimerInterval);
  $("voiceRecordingBar").classList.add("hidden");
  $("messageForm").classList.remove("hidden");
  if (recordingStream) {
    recordingStream.getTracks().forEach(t => t.stop());
    recordingStream = null;
  }
}

$("cancelRecordingBtn").addEventListener("click", () => {
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.onstop = () => {};
    mediaRecorder.stop();
  }
  stopRecordingUI();
});

$("sendRecordingBtn").addEventListener("click", () => {
  if (!mediaRecorder || mediaRecorder.state === "inactive") return;
  mediaRecorder.onstop = () => {
    const blob = new Blob(recordedChunks, { type: "audio/webm" });
    if (blob.size > 0) {
      uploadAndSend(blob, { isVoice: true, fileName: "voice-message.webm" });
    }
  };
  mediaRecorder.stop();
  stopRecordingUI();
});

// ============================================================
// Profile edit
// ============================================================
let pendingAvatarFile = null;
const THEME_COLORS = ["blue", "purple", "green", "teal", "orange", "red", "pink", "indigo"];
const THEME_COLOR_HEX = {
  blue: "#5b8cff", purple: "#8b5cf6", green: "#22c55e", teal: "#14b8a6",
  orange: "#f59e0b", red: "#ef4444", pink: "#ec4899", indigo: "#6366f1",
};

function openProfileModal() {
  pendingAvatarFile = null;
  fillAvatarEl($("profileAvatarPreview"), { avatar_url: me.avatar_url, avatar_color: me.avatar_color, name: me.display_name });
  $("profileDisplayName").value = me.display_name || "";
  $("profileBio").value = me.bio || "";
  $("profileError").textContent = "";
  $("profileAvatarInput").value = "";

  $("accountUsername").value = me.username || "";
  $("usernameError").textContent = "";
  $("currentPassword").value = "";
  $("newPassword").value = "";
  $("passwordError").textContent = "";
  $("archivePassword").value = "";
  $("archivePasswordConfirm").value = "";
  $("archivePasswordError").textContent = "";

  renderThemeColorGrid();
  updateThemeModeButtons();
  $("notificationsEnabledCheckbox").checked = me.notifications_enabled !== false;

  // Always reopen on the "profile" tab for a predictable starting point.
  document.querySelectorAll(".settings-tab").forEach(t => t.classList.toggle("active", t.dataset.settingsTab === "profile"));
  document.querySelectorAll(".settings-panel").forEach(p => p.classList.toggle("active", p.id === "settingsPanelProfile"));

  $("profileModal").classList.remove("hidden");
}

$("meInfoBtn").addEventListener("click", openProfileModal);

document.querySelectorAll(".settings-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".settings-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".settings-panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    const panelId = "settingsPanel" + tab.dataset.settingsTab.charAt(0).toUpperCase() + tab.dataset.settingsTab.slice(1);
    $(panelId).classList.add("active");
  });
});

$("profileAvatarInput").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  pendingAvatarFile = file;
  const reader = new FileReader();
  reader.onload = () => {
    $("profileAvatarPreview").style.background = "transparent";
    $("profileAvatarPreview").innerHTML = `<img src="${reader.result}" alt="">`;
  };
  reader.readAsDataURL(file);
});

$("removeAvatarBtn").addEventListener("click", async () => {
  try {
    const updated = await fetch(API + "/profile/avatar", { method: "DELETE", credentials: "include" }).then(r => r.json());
    me = updated;
    pendingAvatarFile = null;
    $("profileAvatarInput").value = "";
    fillAvatarEl($("profileAvatarPreview"), { avatar_url: me.avatar_url, avatar_color: me.avatar_color, name: me.display_name });
    fillAvatarEl($("meAvatar"), { avatar_url: me.avatar_url, avatar_color: me.avatar_color, name: me.display_name });
    showToast("عکس پروفایل حذف شد");
  } catch (err) {
    showToast("خطا در حذف عکس پروفایل");
  }
});

$("submitProfile").addEventListener("click", async () => {
  $("profileError").textContent = "";
  const formData = new FormData();
  formData.append("display_name", $("profileDisplayName").value.trim());
  formData.append("bio", $("profileBio").value.trim());
  if (pendingAvatarFile) formData.append("avatar", pendingAvatarFile);

  try {
    const res = await fetch(API + "/profile", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      body: formData,
    });
    const updated = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(updated.error || "خطا در ذخیره پروفایل");
    if (!updated || !updated.id) throw new Error("سرور پاسخ معتبر برای پروفایل برنگرداند");

    me = updated;
    fillAvatarEl($("meAvatar"), { avatar_url: me.avatar_url, avatar_color: me.avatar_color, name: me.display_name });
    $("meName").textContent = me.display_name;
    closeModals();
    showToast("پروفایل با موفقیت به‌روزرسانی شد");
    await loadChats();
    if (activeChat) openChat(chats.find(c => c.id === activeChat.id) || activeChat);
  } catch (err) {
    $("profileError").textContent = err.message;
  }
});

// ------------------------------------------------------------
// Account tab: change username / change password
// ------------------------------------------------------------
$("submitUsername").addEventListener("click", async () => {
  $("usernameError").textContent = "";
  const newUsername = $("accountUsername").value.trim().toLowerCase();
  if (!newUsername || newUsername === me.username) return;
  try {
    const updated = await api("/profile/username", { method: "POST", body: { username: newUsername } });
    me = updated;
    showToast("نام کاربری با موفقیت تغییر کرد");
  } catch (err) {
    $("usernameError").textContent = err.message;
  }
});

$("submitPassword").addEventListener("click", async () => {
  $("passwordError").textContent = "";
  const currentPassword = $("currentPassword").value;
  const newPassword = $("newPassword").value;
  if (!currentPassword || !newPassword) {
    $("passwordError").textContent = "هر دو فیلد را پر کنید";
    return;
  }
  try {
    await api("/profile/password", { method: "POST", body: { current_password: currentPassword, new_password: newPassword } });
    $("currentPassword").value = "";
    $("newPassword").value = "";
    showToast("رمز عبور با موفقیت تغییر کرد");
  } catch (err) {
    $("passwordError").textContent = err.message;
  }
});

$("submitArchivePassword").addEventListener("click", async () => {
  $("archivePasswordError").textContent = "";
  const password = $("archivePassword").value;
  const confirmPassword = $("archivePasswordConfirm").value;
  if (password.length < 4) {
    $("archivePasswordError").textContent = "رمز بایگانی باید حداقل ۴ کاراکتر باشد";
    return;
  }
  if (password !== confirmPassword) {
    $("archivePasswordError").textContent = "تکرار رمز با رمز اصلی یکسان نیست";
    return;
  }
  try {
    await api("/archive/password", { method: "POST", body: { password } });
    $("archivePassword").value = "";
    $("archivePasswordConfirm").value = "";
    showToast("رمز بایگانی با موفقیت ذخیره شد");
  } catch (err) {
    $("archivePasswordError").textContent = err.message;
  }
});

// ------------------------------------------------------------
// Personalization tab: theme color, light/dark mode, notifications
// ------------------------------------------------------------
function renderThemeColorGrid() {
  const grid = $("themeColorGrid");
  grid.innerHTML = "";
  THEME_COLORS.forEach(color => {
    const swatch = document.createElement("div");
    swatch.className = "theme-color-swatch" + (me.theme_color === color ? " active" : "");
    swatch.style.background = THEME_COLOR_HEX[color];
    swatch.title = color;
    if (me.theme_color === color) swatch.innerHTML = `<i class="fa-solid fa-check"></i>`;
    swatch.addEventListener("click", () => selectThemeColor(color));
    grid.appendChild(swatch);
  });
}

async function selectThemeColor(color) {
  if (me.theme_color === color) return;
  applyTheme(color, me.theme_mode);
  try {
    const formData = new FormData();
    formData.append("theme_color", color);
    const res = await fetch(API + "/profile", { method: "POST", credentials: "include", body: formData });
    const updated = await res.json();
    if (!res.ok) throw new Error(updated.error || "ذخیره رنگ تم ناموفق بود");
    me = updated;
    renderThemeColorGrid();
  } catch (err) {
    applyTheme(me.theme_color, me.theme_mode); // revert on failure
    showToast(err.message);
  }
}

function updateThemeModeButtons() {
  document.querySelectorAll(".theme-mode-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === (me.theme_mode || "dark"));
  });
}

document.querySelectorAll(".theme-mode-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    const mode = btn.dataset.mode;
    if (me.theme_mode === mode) return;
    applyTheme(me.theme_color, mode);
    updateThemeModeButtons();
    try {
      const formData = new FormData();
      formData.append("theme_mode", mode);
      const res = await fetch(API + "/profile", { method: "POST", credentials: "include", body: formData });
      const updated = await res.json();
      if (!res.ok) throw new Error(updated.error || "ذخیره حالت نمایش ناموفق بود");
      me = updated;
    } catch (err) {
      applyTheme(me.theme_color, me.theme_mode);
      updateThemeModeButtons();
      showToast(err.message);
    }
  });
});

$("notificationsEnabledCheckbox").addEventListener("change", async (e) => {
  const enabled = e.target.checked;
  try {
    const formData = new FormData();
    formData.append("notifications_enabled", enabled ? "1" : "0");
    const res = await fetch(API + "/profile", { method: "POST", credentials: "include", body: formData });
    const updated = await res.json();
    if (!res.ok) throw new Error(updated.error || "ذخیره تنظیم نوتیفیکیشن ناموفق بود");
    me = updated;
  } catch (err) {
    e.target.checked = !enabled;
    showToast(err.message);
  }
});

// ============================================================
// Dropdown menu
// ============================================================
$("menuBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  $("dropdownMenu").classList.toggle("hidden");
});
document.addEventListener("click", () => $("dropdownMenu").classList.add("hidden"));

$("dropdownMenu").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === "profile") openProfileModal();
  if (action === "group") openCreateChatModal("group");
  if (action === "channel") openCreateChatModal("channel");
  if (action === "logout") doLogout();
});

async function doLogout() {
  await api("/logout", { method: "POST" });
  location.reload();
}

// ============================================================
// New private chat modal
// ============================================================
$("newChatBtn").addEventListener("click", () => {
  $("newChatModal").classList.remove("hidden");
  $("userSearchInput").value = "";
  $("userSearchResults").innerHTML = "";
  $("userSearchInput").focus();
  searchUsersFor("userSearchInput", "userSearchResults", async (user) => {
    try {
      const chat = await api("/chats/private", { method: "POST", body: { user_id: user.id } });
      closeModals();
      await loadChats();
      const found = chats.find(c => c.id === chat.id) || chat;
      openChat(found);
    } catch (err) {
      showToast(err.message);
    }
  });
});

function searchUsersFor(inputId, resultsId, onPick, excludeIds = new Set()) {
  const input = $(inputId);
  const doSearch = async () => {
    const q = input.value.trim();
    try {
      const users = await api(`/users/search?q=${encodeURIComponent(q)}`);
      const filtered = users.filter(u => !excludeIds.has(u.id));
      renderUserResults(resultsId, filtered, onPick);
    } catch (err) { /* ignore */ }
  };
  input.oninput = doSearch;
  doSearch();
}

function renderUserResults(resultsId, users, onPick) {
  const container = $(resultsId);
  container.innerHTML = "";
  if (users.length === 0) {
    container.innerHTML = `<div style="color:var(--text-secondary);font-size:13px;padding:8px;">کاربری یافت نشد</div>`;
    return;
  }
  users.forEach(u => {
    const el = document.createElement("div");
    el.className = "user-row";
    el.innerHTML = `
      ${avatarHtml({ avatar_url: u.avatar_url, avatar_color: u.avatar_color, name: u.display_name })}
      <div>
        <div class="user-row-name">${escapeHtml(u.display_name)}</div>
        <div class="user-row-username">@${escapeHtml(u.username)}</div>
      </div>
    `;
    el.addEventListener("click", () => onPick(u));
    container.appendChild(el);
  });
}

// ============================================================
// Create group / channel modal
// ============================================================
function openCreateChatModal(mode) {
  createChatMode = mode;
  selectedNewMembers.clear();
  $("createChatTitle").textContent = mode === "group" ? "ساخت گروه جدید" : "ساخت کانال جدید";
  $("newChatName").value = "";
  $("newChatDesc").value = "";
  $("isPublicCheckbox").checked = false;
  $("publicToggleRow").classList.toggle("hidden", mode !== "channel");
  $("isOpenChatCheckbox").checked = false;
  $("openChatToggleRow").classList.toggle("hidden", mode !== "channel");
  $("memberSearchInput").value = "";
  $("createChatError").textContent = "";
  renderSelectedMembers();
  $("createChatModal").classList.remove("hidden");

  searchUsersFor("memberSearchInput", "memberSearchResults", (user) => {
    if (!selectedNewMembers.has(user.id)) {
      selectedNewMembers.set(user.id, user);
      renderSelectedMembers();
    }
  });
}

function renderSelectedMembers() {
  const container = $("selectedMembers");
  container.innerHTML = "";
  selectedNewMembers.forEach((u, id) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.innerHTML = `<span>${escapeHtml(u.display_name)}</span><i class="fa-solid fa-xmark"></i>`;
    chip.querySelector("i").addEventListener("click", () => {
      selectedNewMembers.delete(id);
      renderSelectedMembers();
    });
    container.appendChild(chip);
  });
}

$("submitCreateChat").addEventListener("click", async () => {
  const name = $("newChatName").value.trim();
  if (!name) {
    $("createChatError").textContent = "نام را وارد کنید";
    return;
  }
  const body = {
    name,
    description: $("newChatDesc").value.trim(),
    member_ids: Array.from(selectedNewMembers.keys()),
  };
  if (createChatMode === "channel") {
    body.is_public = $("isPublicCheckbox").checked;
    body.open_chat = $("isOpenChatCheckbox").checked;
  }

  try {
    const chat = await api(`/chats/${createChatMode}`, { method: "POST", body });
    closeModals();
    await loadChats();
    const found = chats.find(c => c.id === chat.id) || chat;
    openChat(found);
    showToast(createChatMode === "group" ? "گروه با موفقیت ساخته شد" : "کانال با موفقیت ساخته شد");
  } catch (err) {
    $("createChatError").textContent = err.message;
  }
});

// ============================================================
// Chat info / members modal (+ advanced group & channel management)
// ============================================================
$("chatInfoBtn").addEventListener("click", () => {
  if (!activeChat) return;
  $("infoModalTitle").textContent = activeChat.name;
  $("infoModalDesc").textContent = activeChat.description || "بدون توضیحات";
  $("addMemberBtn").classList.toggle("hidden", activeChat.type === "private" || !["owner", "admin"].includes(activeChatRole));

  const isGroupLike = activeChat.type !== "private";
  $("leaveChatBtn").classList.toggle("hidden", !isGroupLike || activeChatRole === "owner");
  $("deleteChatBtn").classList.toggle("hidden", !isGroupLike || activeChatRole !== "owner");
  $("archiveChatBtn").innerHTML = activeChat._archived
    ? '<i class="fa-solid fa-box-open"></i> خارج کردن از بایگانی'
    : '<i class="fa-solid fa-box-archive"></i> بایگانی گفتگو';

  const showChannelToggle = activeChat.type === "channel" && activeChatRole === "owner";
  $("channelOpenChatRow").classList.toggle("hidden", !showChannelToggle);
  $("channelOpenChatCheckbox").checked = !!activeChat.open_chat;

  const canEditChatAvatar = isGroupLike && ["owner", "admin"].includes(activeChatRole);
  $("chatAvatarEditSection").classList.toggle("hidden", !canEditChatAvatar);
  if (canEditChatAvatar) {
    fillAvatarEl($("infoModalAvatarPreview"), { avatar_url: activeChat.avatar_url, avatar_color: activeChat.avatar_color, name: activeChat.name });
    $("removeChatAvatarBtn").classList.toggle("hidden", !activeChat.avatar_url);
    $("chatAvatarInput").value = "";
  }

  loadChatInfo(activeChat.id);
  $("infoModal").classList.remove("hidden");
});

$("chatAvatarInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file || !activeChat) return;

  const formData = new FormData();
  formData.append("avatar", file);
  try {
    const res = await fetch(API + `/chats/${activeChat.id}/avatar`, { method: "POST", credentials: "include", body: formData });
    const updated = await res.json();
    if (!res.ok) throw new Error(updated.error || "به‌روزرسانی عکس ناموفق بود");

    activeChat.avatar_url = updated.avatar_url;
    const chatInList = chats.find(c => c.id === activeChat.id);
    if (chatInList) chatInList.avatar_url = updated.avatar_url;
    renderChatList(chats);
    fillAvatarEl($("chatAvatar"), { avatar_url: activeChat.avatar_url, avatar_color: activeChat.avatar_color, name: activeChat.name });
    fillAvatarEl($("infoModalAvatarPreview"), { avatar_url: activeChat.avatar_url, avatar_color: activeChat.avatar_color, name: activeChat.name });
    $("removeChatAvatarBtn").classList.remove("hidden");
    showToast("عکس گفتگو به‌روزرسانی شد");
  } catch (err) {
    showToast(err.message);
  }
});

$("removeChatAvatarBtn").addEventListener("click", async () => {
  if (!activeChat) return;
  try {
    const updated = await api(`/chats/${activeChat.id}/avatar`, { method: "DELETE" });
    activeChat.avatar_url = updated.avatar_url;
    const chatInList = chats.find(c => c.id === activeChat.id);
    if (chatInList) chatInList.avatar_url = updated.avatar_url;
    renderChatList(chats);
    fillAvatarEl($("chatAvatar"), { avatar_url: activeChat.avatar_url, avatar_color: activeChat.avatar_color, name: activeChat.name });
    fillAvatarEl($("infoModalAvatarPreview"), { avatar_url: activeChat.avatar_url, avatar_color: activeChat.avatar_color, name: activeChat.name });
    $("removeChatAvatarBtn").classList.add("hidden");
    showToast("عکس گفتگو حذف شد");
  } catch (err) {
    showToast(err.message);
  }
});

$("channelOpenChatCheckbox").addEventListener("change", async (e) => {
  if (!activeChat) return;
  const openChatValue = e.target.checked;
  try {
    const updated = await api(`/chats/${activeChat.id}/settings`, { method: "POST", body: { open_chat: openChatValue } });
    activeChat.open_chat = updated.open_chat;
    const chatInList = chats.find(c => c.id === activeChat.id);
    if (chatInList) chatInList.open_chat = updated.open_chat;
    updateMessageInputAccess();
    showToast(updated.open_chat ? "چت کانال برای همه اعضا باز شد" : "چت کانال فقط برای مدیران باز است");
  } catch (err) {
    e.target.checked = !openChatValue; // revert the toggle on failure
    showToast(err.message);
  }
});

async function loadChatInfo(chatId) {
  const members = await api(`/chats/${chatId}/members`);
  const container = $("memberList");
  container.innerHTML = "";
  const roleLabel = { owner: "مالک", admin: "مدیر", member: "" };

  members.forEach(m => {
    const row = document.createElement("div");
    row.className = "member-row";

    const isMe = m.id === me.id;
    const iAmOwner = activeChatRole === "owner";
    const iAmAdmin = activeChatRole === "admin";
    const canManage = activeChat.type !== "private" && !isMe && m.role !== "owner" && (iAmOwner || iAmAdmin);

    let actionsHtml = "";
    if (canManage) {
      const promoteBtn = iAmOwner
        ? `<button type="button" class="member-action-btn promote ${m.role === "admin" ? "active" : ""}" data-user-id="${m.id}" data-role="${m.role}" title="${m.role === "admin" ? "کاهش به عضو عادی" : "ارتقا به مدیر"}">
             <i class="fa-solid ${m.role === "admin" ? "fa-user-minus" : "fa-user-shield"}"></i>
           </button>`
        : "";
      const canKick = iAmOwner || (iAmAdmin && m.role === "member");
      const kickBtn = canKick
        ? `<button type="button" class="member-action-btn kick" data-user-id="${m.id}" title="حذف از گفتگو"><i class="fa-solid fa-user-xmark"></i></button>`
        : "";
      actionsHtml = `<div class="member-row-actions">${promoteBtn}${kickBtn}</div>`;
    }

    row.innerHTML = `
      ${avatarHtml({ avatar_url: m.avatar_url, avatar_color: m.avatar_color, name: m.display_name, online: m.online })}
      <div>
        <div class="user-row-name">${escapeHtml(m.display_name)}${isMe ? " (شما)" : ""}</div>
        <div class="user-row-username">@${escapeHtml(m.username)}</div>
      </div>
      ${!canManage ? `<div class="member-role">${roleLabel[m.role] || ""}</div>` : ""}
      ${actionsHtml}
    `;
    container.appendChild(row);
  });

  container.querySelectorAll(".member-action-btn.promote").forEach(btn => {
    btn.addEventListener("click", async () => {
      const targetId = Number(btn.dataset.userId);
      const currentRole = btn.dataset.role;
      const newRole = currentRole === "admin" ? "member" : "admin";
      try {
        await api(`/chats/${chatId}/members/${targetId}/role`, { method: "POST", body: { role: newRole } });
        showToast(newRole === "admin" ? "کاربر به مدیر ارتقا یافت" : "دسترسی مدیریت از کاربر گرفته شد");
        loadChatInfo(chatId);
      } catch (err) { showToast(err.message); }
    });
  });

  container.querySelectorAll(".member-action-btn.kick").forEach(btn => {
    btn.addEventListener("click", async () => {
      const targetId = Number(btn.dataset.userId);
      if (!confirm("این عضو از گفتگو حذف شود؟")) return;
      try {
        await api(`/chats/${chatId}/members/${targetId}`, { method: "DELETE" });
        showToast("عضو از گفتگو حذف شد");
        loadChatInfo(chatId);
        loadChats();
      } catch (err) { showToast(err.message); }
    });
  });
}

async function archiveCurrentChat() {
  if (!activeChat || !activeChat.id || activeChat.type === "saved") return;
  const archived = !activeChat._archived;
  try {
    await api(`/archive/chats/${activeChat.id}`, { method: "POST", body: { archived } });
    activeChat._archived = archived;
    if (archived) {
      chats = chats.filter(c => c.id !== activeChat.id);
      closeModals();
      closeActiveChat();
      renderChatList(chats);
      showToast("گفتگو به بایگانی منتقل شد");
    } else {
      closeModals();
      await loadChats();
      const found = chats.find(c => c.id === activeChat.id);
      if (found) { found._archived = false; openChat(found); }
      showToast("گفتگو از بایگانی خارج شد");
    }
  } catch (err) {
    showToast(err.message || "تغییر وضعیت بایگانی ناموفق بود");
  }
}

$("archiveChatBtn").addEventListener("click", archiveCurrentChat);

async function renderArchiveList() {
  const list = $("archiveList");
  const empty = $("archiveEmpty");
  list.innerHTML = "";
  try {
    const archivedChats = await api("/archive/chats");
    empty.classList.toggle("hidden", archivedChats.length !== 0);
    archivedChats.forEach(chat => {
      chat._archived = true;
      const el = document.createElement("div");
      el.className = "chat-item";
      const preview = chat.last_message
        ? (chat.last_message.sender_id === me.id ? "شما: " : "") + escapeHtml(stripFormattingTokens(chat.last_message.content))
        : "گفتگو را شروع کنید";
      const badge = chat.type === "private" ? "" : `<span class="chat-type-badge">${chat.type === "group" ? "گروه" : "کانال"}</span>`;
      el.innerHTML = `
        ${avatarHtml({ avatar_url: chat.avatar_url, avatar_color: chat.avatar_color, name: chat.name, online: chat.type === "private" && chat.online })}
        <div class="chat-item-body">
          <div class="chat-item-top"><span class="chat-item-name">${escapeHtml(chat.name)} ${badge}</span></div>
          <div class="chat-item-bottom"><span class="chat-item-preview">${preview}</span></div>
        </div>
        <button type="button" class="icon-btn archive-restore-btn" title="خارج کردن از بایگانی"><i class="fa-solid fa-box-open"></i></button>`;
      el.addEventListener("click", async (e) => {
        if (e.target.closest(".archive-restore-btn")) return;
        closeModals();
        openChat(chat);
      });
      el.querySelector(".archive-restore-btn").addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          await api(`/archive/chats/${chat.id}`, { method: "POST", body: { archived: false } });
          showToast("گفتگو از بایگانی خارج شد");
          await renderArchiveList();
          await loadChats();
        } catch (err) { showToast(err.message); }
      });
      list.appendChild(el);
    });
  } catch (err) {
    empty.classList.remove("hidden");
    empty.textContent = err.message || "بارگذاری بایگانی ناموفق بود";
  }
}

async function openArchive() {
  $("archiveUnlockPassword").value = "";
  $("archiveUnlockError").textContent = "";
  $("archiveLockHint").textContent = "برای ورود به بایگانی رمز خود را وارد کنید.";
  try {
    await api("/archive/verify", { method: "POST", body: { password: "" } });
  } catch (err) {
    if (err && err.message === "هنوز رمز بایگانی تنظیم نشده است") {
      $("archiveLockHint").textContent = "ابتدا از پروفایل و تنظیمات ← حساب کاربری، یک رمز برای بایگانی تعیین کنید.";
    }
  }
  $("archiveLockModal").classList.remove("hidden");
  $("archiveUnlockPassword").focus();
}

$("unlockArchiveBtn").addEventListener("click", async () => {
  const password = $("archiveUnlockPassword").value;
  $("archiveUnlockError").textContent = "";
  if (!password) {
    $("archiveUnlockError").textContent = "رمز بایگانی را وارد کنید";
    return;
  }
  try {
    await api("/archive/verify", { method: "POST", body: { password } });
    $("archiveLockModal").classList.add("hidden");
    $("archiveModal").classList.remove("hidden");
    await renderArchiveList();
  } catch (err) {
    $("archiveUnlockError").textContent = err.message;
  }
});

$("archiveUnlockPassword").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); $("unlockArchiveBtn").click(); }
});

$("archiveItem").addEventListener("click", openArchive);

$("leaveChatBtn").addEventListener("click", async () => {
  if (!activeChat) return;
  if (!confirm(`از «${activeChat.name}» خارج شوید؟`)) return;
  try {
    await api(`/chats/${activeChat.id}/leave`, { method: "POST" });
    chats = chats.filter(c => c.id !== activeChat.id);
    closeModals();
    closeActiveChat();
    renderChatList(chats);
    showToast("از گفتگو خارج شدید");
  } catch (err) { showToast(err.message); }
});

$("deleteChatBtn").addEventListener("click", async () => {
  if (!activeChat) return;
  const label = activeChat.type === "group" ? "گروه" : "کانال";
  if (!confirm(`این ${label} برای همیشه حذف شود؟ این عملیات قابل بازگشت نیست.`)) return;
  try {
    await api(`/chats/${activeChat.id}`, { method: "DELETE" });
    chats = chats.filter(c => c.id !== activeChat.id);
    closeModals();
    closeActiveChat();
    renderChatList(chats);
    showToast(`${label} حذف شد`);
  } catch (err) { showToast(err.message); }
});

$("addMemberBtn").addEventListener("click", () => {
  selectedAddMembers.clear();
  $("addMemberSearchInput").value = "";
  $("addMemberResults").innerHTML = "";
  $("addMemberSelected").innerHTML = "";
  $("addMemberModal").classList.remove("hidden");

  const existingIds = new Set();
  api(`/chats/${activeChat.id}/members`).then(members => {
    members.forEach(m => existingIds.add(m.id));
    searchUsersFor("addMemberSearchInput", "addMemberResults", (user) => {
      if (!selectedAddMembers.has(user.id)) {
        selectedAddMembers.set(user.id, user);
        renderAddSelectedMembers();
      }
    }, existingIds);
  });
});

function renderAddSelectedMembers() {
  const container = $("addMemberSelected");
  container.innerHTML = "";
  selectedAddMembers.forEach((u, id) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.innerHTML = `<span>${escapeHtml(u.display_name)}</span><i class="fa-solid fa-xmark"></i>`;
    chip.querySelector("i").addEventListener("click", () => {
      selectedAddMembers.delete(id);
      renderAddSelectedMembers();
    });
    container.appendChild(chip);
  });
}

$("submitAddMember").addEventListener("click", async () => {
  if (selectedAddMembers.size === 0) return;
  try {
    await api(`/chats/${activeChat.id}/members`, {
      method: "POST",
      body: { member_ids: Array.from(selectedAddMembers.keys()) },
    });
    showToast("اعضا با موفقیت اضافه شدند");
    closeModals();
    await loadChats();
    loadChatInfo(activeChat.id);
  } catch (err) {
    showToast(err.message);
  }
});

// ============================================================
// Voice calls (WebRTC, signaled through Socket.IO)
// ============================================================
const ICE_SERVERS = [
  { urls: "stun:stun.l.google.com:19302" },
  { urls: "stun:stun1.l.google.com:19302" },
];

let peerConnection = null;
let localStream = null;
let currentCallPeerId = null;
let currentCallPeerInfo = null;
let pendingOfferSdp = null;
let isMuted = false;
let callTimerInterval = null;
let callStartedAt = null;

function registerCallSocketEvents() {
  socket.on("incoming_call", (data) => {
    if (currentCallPeerId) {
      // Already on a call elsewhere — automatically decline this new one.
      socket.emit("call_reject", { to_user_id: data.from_user_id });
      return;
    }
    currentCallPeerId = data.from_user_id;
    currentCallPeerInfo = { name: data.from_name, avatar_url: data.from_avatar_url, avatar_color: data.from_avatar_color };
    pendingOfferSdp = data.sdp;
    showCallOverlay("ringing");
  });

  socket.on("call_answered", async (data) => {
    if (!peerConnection || data.from_user_id !== currentCallPeerId) return;
    try {
      await peerConnection.setRemoteDescription(new RTCSessionDescription(data.sdp));
      showCallOverlay("in-call");
    } catch (e) {
      showToast("برقراری تماس با خطا مواجه شد");
      cleanupCall();
    }
  });

  socket.on("call_ice_candidate", async (data) => {
    if (!peerConnection || data.from_user_id !== currentCallPeerId) return;
    try { await peerConnection.addIceCandidate(new RTCIceCandidate(data.candidate)); } catch (e) { /* ignore */ }
  });

  socket.on("call_ended", (data) => {
    if (data.from_user_id === currentCallPeerId) {
      showToast("تماس پایان یافت");
      cleanupCall();
    }
  });

  socket.on("call_rejected", (data) => {
    if (data.from_user_id === currentCallPeerId) {
      showToast("تماس رد شد");
      cleanupCall();
    }
  });

  socket.on("call_failed", (data) => {
    showToast(data.reason === "offline" ? "کاربر مورد نظر آفلاین است" : "برقراری تماس ممکن نشد");
    cleanupCall();
  });
}

function createPeerConnection(targetUserId) {
  const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
  pc.onicecandidate = (e) => {
    if (e.candidate) socket.emit("call_ice_candidate", { to_user_id: targetUserId, candidate: e.candidate });
  };
  pc.ontrack = (e) => {
    $("remoteAudio").srcObject = e.streams[0];
  };
  return pc;
}

$("callBtn").addEventListener("click", async () => {
  if (!activeChat || activeChat.type !== "private" || !activeChat.peer_id) return;
  if (currentCallPeerId) { showToast("شما در حال حاضر در یک تماس هستید"); return; }

  try {
    localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    showToast("دسترسی به میکروفون رد شد یا در دسترس نیست");
    return;
  }

  currentCallPeerId = activeChat.peer_id;
  currentCallPeerInfo = { name: activeChat.name, avatar_url: activeChat.avatar_url, avatar_color: activeChat.avatar_color };

  peerConnection = createPeerConnection(currentCallPeerId);
  localStream.getTracks().forEach(track => peerConnection.addTrack(track, localStream));

  showCallOverlay("calling");

  try {
    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);
    socket.emit("call_offer", { to_user_id: currentCallPeerId, sdp: offer, chat_id: activeChat.id });
  } catch (err) {
    showToast("شروع تماس ممکن نشد");
    cleanupCall();
  }
});

$("callAcceptBtn").addEventListener("click", async () => {
  if (!pendingOfferSdp || !currentCallPeerId) return;
  try {
    localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    showToast("دسترسی به میکروفون رد شد یا در دسترس نیست");
    socket.emit("call_reject", { to_user_id: currentCallPeerId });
    cleanupCall();
    return;
  }

  peerConnection = createPeerConnection(currentCallPeerId);
  localStream.getTracks().forEach(track => peerConnection.addTrack(track, localStream));

  try {
    await peerConnection.setRemoteDescription(new RTCSessionDescription(pendingOfferSdp));
    const answer = await peerConnection.createAnswer();
    await peerConnection.setLocalDescription(answer);
    socket.emit("call_answer", { to_user_id: currentCallPeerId, sdp: answer });
    pendingOfferSdp = null;
    showCallOverlay("in-call");
  } catch (err) {
    showToast("پاسخ به تماس ممکن نشد");
    cleanupCall();
  }
});

$("callRejectBtn").addEventListener("click", () => {
  if (!currentCallPeerId) { hideCallOverlay(); return; }
  if (pendingOfferSdp) {
    socket.emit("call_reject", { to_user_id: currentCallPeerId });
  } else {
    socket.emit("call_end", { to_user_id: currentCallPeerId });
  }
  cleanupCall();
});

$("callMuteBtn").addEventListener("click", () => {
  if (!localStream) return;
  isMuted = !isMuted;
  localStream.getAudioTracks().forEach(t => { t.enabled = !isMuted; });
  $("callMuteBtn").classList.toggle("active", isMuted);
  $("callMuteBtn").querySelector("i").className = isMuted ? "fa-solid fa-microphone-slash" : "fa-solid fa-microphone";
});

function showCallOverlay(mode) {
  $("callOverlay").classList.remove("hidden");
  fillAvatarEl($("callAvatar"), {
    avatar_url: currentCallPeerInfo?.avatar_url,
    avatar_color: currentCallPeerInfo?.avatar_color,
    name: currentCallPeerInfo?.name,
  });
  $("callName").textContent = currentCallPeerInfo?.name || "";
  $("callAcceptBtn").classList.toggle("hidden", mode !== "ringing");
  $("callMuteBtn").classList.toggle("hidden", mode !== "in-call");
  $("callTimer").classList.toggle("hidden", mode !== "in-call");

  if (mode === "calling") $("callStatus").textContent = "در حال تماس...";
  if (mode === "ringing") $("callStatus").textContent = "تماس صوتی ورودی...";
  if (mode === "in-call") {
    $("callStatus").textContent = "در تماس";
    startCallTimer();
  }
}

function hideCallOverlay() {
  $("callOverlay").classList.add("hidden");
  stopCallTimer();
}

function startCallTimer() {
  callStartedAt = Date.now();
  updateCallTimer();
  callTimerInterval = setInterval(updateCallTimer, 1000);
}

function updateCallTimer() {
  const elapsed = Math.floor((Date.now() - callStartedAt) / 1000);
  const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");
  $("callTimer").textContent = toPersianDigits(`${mm}:${ss}`);
}

function stopCallTimer() {
  clearInterval(callTimerInterval);
  $("callTimer").textContent = "۰۰:۰۰";
}

function cleanupCall() {
  if (peerConnection) { peerConnection.close(); peerConnection = null; }
  if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
  const remoteAudio = $("remoteAudio");
  if (remoteAudio) remoteAudio.srcObject = null;
  currentCallPeerId = null;
  currentCallPeerInfo = null;
  pendingOfferSdp = null;
  isMuted = false;
  $("callMuteBtn").classList.remove("active");
  $("callMuteBtn").querySelector("i").className = "fa-solid fa-microphone";
  hideCallOverlay();
}

// ============================================================
// Forward a message to other chats and/or Saved Messages
// ============================================================
function openForwardModal(messageId) {
  forwardMessageId = messageId;
  $("forwardToSavedCheckbox").checked = false;
  $("forwardError").textContent = "";

  const container = $("forwardChatList");
  container.innerHTML = "";
  if (chats.length === 0) {
    container.innerHTML = `<div style="color:var(--text-secondary);font-size:13px;padding:8px;">گفتگویی برای فوروارد وجود ندارد</div>`;
  }
  chats.forEach(chat => {
    const row = document.createElement("label");
    row.className = "forward-target-row";
    row.innerHTML = `
      <input type="checkbox" value="${chat.id}" class="forward-chat-checkbox">
      ${avatarHtml({ avatar_url: chat.avatar_url, avatar_color: chat.avatar_color, name: chat.name })}
      <span class="forward-target-name">${escapeHtml(chat.name)}</span>
    `;
    container.appendChild(row);
  });

  $("forwardModal").classList.remove("hidden");
}

$("submitForward").addEventListener("click", async () => {
  $("forwardError").textContent = "";
  const chatIds = Array.from(document.querySelectorAll(".forward-chat-checkbox:checked")).map(cb => Number(cb.value));
  const toSaved = $("forwardToSavedCheckbox").checked;

  if (chatIds.length === 0 && !toSaved) {
    $("forwardError").textContent = "حداقل یک مقصد انتخاب کنید";
    return;
  }
  if (!forwardMessageId) return;

  try {
    await api(`/messages/${forwardMessageId}/forward`, {
      method: "POST",
      body: { chat_ids: chatIds, to_saved: toSaved },
    });
    closeModals();
    showToast("پیام فوروارد شد");
    loadChats();
  } catch (err) {
    $("forwardError").textContent = err.message;
  }
});

// ============================================================
// Polls & quizzes
// ============================================================
let pollType = "regular";
let pollOptionIdCounter = 0;
let pollCorrectRowId = null;

function addPollOptionRow(prefillText = "") {
  pollOptionIdCounter += 1;
  const rowId = `pollopt-${pollOptionIdCounter}`;
  const row = document.createElement("div");
  row.className = "poll-option-row";
  row.dataset.rowId = rowId;
  row.innerHTML = `
    <button type="button" class="poll-option-correct-radio ${pollType === "quiz" ? "" : "hidden"}" data-row-id="${rowId}" title="جواب درست"></button>
    <input type="text" class="poll-option-input" placeholder="گزینه" maxlength="120" value="${escapeHtml(prefillText)}">
    <button type="button" class="poll-option-remove-btn" title="حذف گزینه"><i class="fa-solid fa-xmark"></i></button>
  `;
  row.querySelector(".poll-option-correct-radio").addEventListener("click", () => {
    pollCorrectRowId = rowId;
    refreshPollCorrectRadios();
  });
  row.querySelector(".poll-option-remove-btn").addEventListener("click", () => {
    if ($("pollOptionsEditor").children.length <= 2) { showToast("حداقل ۲ گزینه لازم است"); return; }
    if (pollCorrectRowId === rowId) pollCorrectRowId = null;
    row.remove();
  });
  $("pollOptionsEditor").appendChild(row);
}

function refreshPollCorrectRadios() {
  document.querySelectorAll(".poll-option-correct-radio").forEach(btn => {
    btn.classList.toggle("selected", btn.dataset.rowId === pollCorrectRowId);
  });
}

function openCreatePollModal() {
  if (!activeChat || !["group", "channel"].includes(activeChat.type)) return;
  pollType = "regular";
  pollCorrectRowId = null;
  $("pollQuestionInput").value = "";
  $("pollError").textContent = "";
  $("pollOptionsEditor").innerHTML = "";
  pollOptionIdCounter = 0;
  addPollOptionRow();
  addPollOptionRow();
  document.querySelectorAll(".poll-type-btn").forEach(b => b.classList.toggle("active", b.dataset.pollType === "regular"));
  $("pollQuizHint").classList.add("hidden");
  $("createPollModal").classList.remove("hidden");
}

$("pollBtn").addEventListener("click", openCreatePollModal);

document.querySelectorAll(".poll-type-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    pollType = btn.dataset.pollType;
    document.querySelectorAll(".poll-type-btn").forEach(b => b.classList.toggle("active", b === btn));
    $("pollQuizHint").classList.toggle("hidden", pollType !== "quiz");
    document.querySelectorAll(".poll-option-correct-radio").forEach(r => r.classList.toggle("hidden", pollType !== "quiz"));
    if (pollType !== "quiz") pollCorrectRowId = null;
  });
});

$("addPollOptionBtn").addEventListener("click", () => {
  if ($("pollOptionsEditor").children.length >= 10) { showToast("حداکثر ۱۰ گزینه مجاز است"); return; }
  addPollOptionRow();
});

$("submitPoll").addEventListener("click", async () => {
  $("pollError").textContent = "";
  const question = $("pollQuestionInput").value.trim();
  const rows = Array.from($("pollOptionsEditor").children);
  const options = rows.map(r => r.querySelector(".poll-option-input").value.trim()).filter(Boolean);

  if (!question) { $("pollError").textContent = "متن سوال را وارد کنید"; return; }
  if (options.length < 2) { $("pollError").textContent = "حداقل ۲ گزینه لازم است"; return; }

  let correctIndex = null;
  if (pollType === "quiz") {
    if (!pollCorrectRowId) { $("pollError").textContent = "جواب درست آزمون را مشخص کنید"; return; }
    const nonEmptyRows = rows.filter(r => r.querySelector(".poll-option-input").value.trim());
    correctIndex = nonEmptyRows.findIndex(r => r.dataset.rowId === pollCorrectRowId);
    if (correctIndex === -1) { $("pollError").textContent = "جواب درست آزمون را مشخص کنید"; return; }
  }

  try {
    await api(`/chats/${activeChat.id}/polls`, {
      method: "POST",
      body: { question, options, poll_type: pollType, correct_option_index: correctIndex },
    });
    closeModals();
    showToast("نظرسنجی ایجاد شد");
  } catch (err) {
    $("pollError").textContent = err.message;
  }
});

function buildPollHtml(msg) {
  const poll = msg.poll;
  if (!poll) return "";
  const hasVoted = poll.my_option_id !== null && poll.my_option_id !== undefined;
  const isQuiz = poll.poll_type === "quiz";

  const optionsHtml = poll.options.map(opt => {
    const mine = hasVoted && opt.id === poll.my_option_id;
    let stateClass = "";
    if (hasVoted && isQuiz) {
      if (opt.is_correct) stateClass = "correct";
      else if (mine) stateClass = "incorrect";
    }
    const icon = hasVoted && isQuiz
      ? (opt.is_correct ? `<i class="fa-solid fa-check-circle"></i>` : (mine ? `<i class="fa-solid fa-circle-xmark"></i>` : ""))
      : (mine ? `<i class="fa-solid fa-check-circle"></i>` : "");
    const fillWidth = hasVoted ? opt.percent : 0;
    const rightSide = hasVoted ? `<span class="poll-option-percent">${toPersianDigits(opt.percent)}٪</span>` : "";
    return `
      <div class="poll-option ${mine ? "mine" : ""} ${stateClass}" data-option-id="${opt.id}" data-poll-id="${poll.id}">
        <div class="poll-option-fill" style="width:${fillWidth}%"></div>
        <div class="poll-option-content">
          <span class="poll-option-text">${icon}${escapeHtml(opt.text)}</span>
          ${rightSide}
        </div>
      </div>`;
  }).join("");

  const typeLabel = isQuiz ? "آزمون" : "نظرسنجی";
  const footerHint = hasVoted
    ? `${toPersianDigits(poll.total_votes)} رأی`
    : "برای مشاهده نتیجه رأی بدهید";

  return `
    <div class="poll-bubble" data-poll-id="${poll.id}">
      <div class="poll-question"><i class="fa-solid ${isQuiz ? "fa-graduation-cap" : "fa-square-poll-vertical"}"></i><span>${escapeHtml(poll.question)}</span></div>
      <div class="poll-type-label">${typeLabel}${isQuiz && hasVoted ? " • پاسخ داده شد" : ""}</div>
      <div class="poll-options-list">${optionsHtml}</div>
      <div class="poll-footer"><span>${footerHint}</span></div>
    </div>`;
}

function wirePollOptions(row, msg) {
  row.querySelectorAll(".poll-option").forEach(el => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const poll = msg.poll;
      if (!poll) return;
      const hasVoted = poll.my_option_id !== null && poll.my_option_id !== undefined;
      if (hasVoted && poll.poll_type === "quiz") return; // quizzes lock in your first answer
      votePoll(poll.id, Number(el.dataset.optionId));
    });
  });
}

async function votePoll(pollId, optionId) {
  try {
    const personalized = await api(`/polls/${pollId}/vote`, { method: "POST", body: { option_id: optionId } });
    const msg = renderedMessages.get(personalized.message_id);
    if (msg) {
      msg.poll = personalized;
      const row = document.querySelector(`.msg-row[data-message-id="${personalized.message_id}"]`);
      if (row) {
        const bubble = row.querySelector(".bubble");
        const oldPoll = bubble.querySelector(".poll-bubble");
        if (oldPoll) oldPoll.outerHTML = buildPollHtml(msg);
        wirePollOptions(bubble, msg);
      }
    }
  } catch (err) {
    showToast(err.message || "ثبت رأی با خطا مواجه شد");
  }
}

// ============================================================
// Modal close handlers
// ============================================================
function closeModals() {
  document.querySelectorAll(".modal-overlay").forEach(m => m.classList.add("hidden"));
}
document.querySelectorAll(".close-modal").forEach(btn => {
  btn.addEventListener("click", closeModals);
});
document.querySelectorAll(".modal-overlay").forEach(overlay => {
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeModals();
  });
});

// ============================================================
// Init
// ============================================================
tryAutoLogin();
