// ===== DOM / State =====
const $ = id => document.getElementById(id);

const elements = {
  gate: $("gate"),
  gateButton: $("gatebtn"),
  gateError: $("gateerr"),
  tokenInput: $("token"),
  face: $("face"),
  status: $("status"),
  statusText: $("statusText"),
  sheetStatus: $("sheetStatus"),
  mode: $("mode"),
  log: $("log"),
  form: $("form"),
  input: $("input"),
  sendButton: $("send"),
  callButton: $("call"),
  jumpBottom: $("jumpBottom"),
  settingsOverlay: $("settingsOverlay"),
  toggleTime: $("toggleTime"),
  timeValue: $("timeValue"),
  toggleVoice: $("toggleVoice"),
  voiceValue: $("voiceValue"),
  changeToken: $("changeToken"),
  promptTabs: $("promptTabs"),
  promptText: $("promptText"),
  promptNote: $("promptNote"),
  promptSave: $("promptSave"),
  promptRevert: $("promptRevert"),
  settingsList: $("settingsList"),
  settingsNote: $("settingsNote"),
  settingsSave: $("settingsSave"),
  endRemoteCall: $("endRemoteCall"),
  restartApp: $("restartApp"),
  remoteCallValue: $("remoteCallValue"),
  memoryTabs: $("memoryTabs"),
  memoryList: $("memoryList"),
  memoryNote: $("memoryNote"),
  memoryText: $("memoryText"),
  memoryFacts: $("memoryFacts"),
  memoryDetailNote: $("memoryDetailNote"),
  memoryPin: $("memoryPin"),
  memorySave: $("memorySave"),
  memoryDelete: $("memoryDelete"),
  contextMenu: $("contextMenu"),
};

const AVATAR_URL = "/static/kotoha.png";
const MAX_INPUT_HEIGHT = 120;
const LONG_PRESS_MS = 520;

let token = localStorage.getItem("kotoha_token") || "";
let longPressTimer = null;

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

const preferences = {
  showTime: localStorage.getItem("kotoha_show_time") !== "0",
  voice: localStorage.getItem("kotoha_voice") === "1",
};

// ===== Settings sheet =====
// シートの中で画面を差し替える。iPhoneから片手で戻れるよう階層は1段までにする。
function showSheetPage(name) {
  for (const page of document.querySelectorAll(".sheet-page")) {
    page.classList.toggle("active", page.dataset.page === name);
  }
  document.querySelector(".sheet").scrollTop = 0;
}

function setNote(element, text, bad = false) {
  element.textContent = text;
  element.classList.toggle("bad", bad);
}

// 押し間違いで走らせたくない操作は、もう一度押させる。
function armOnce(button, label, confirmLabel, run) {
  let armed = false;
  let timer = null;
  const reset = () => {
    armed = false;
    clearTimeout(timer);
    button.textContent = label;
    button.classList.remove("danger");
  };
  button.addEventListener("click", () => {
    if (!armed) {
      armed = true;
      button.textContent = confirmLabel;
      button.classList.add("danger");
      timer = setTimeout(reset, 5000);
      return;
    }
    reset();
    run();
  });
  return reset;
}

// ===== ことばを直す =====
let prompts = [];
let promptName = "";

async function loadPrompts() {
  setNote(elements.promptNote, "読み込んでいます…");
  try {
    const response = await api("/api/prompts");
    if (!response.ok) throw new Error();
    prompts = (await response.json()).prompts;
  } catch {
    setNote(elements.promptNote, "読み込めませんでした。", true);
    return;
  }
  elements.promptTabs.replaceChildren(...prompts.map(item => {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.textContent = item.label;
    tab.setAttribute("role", "tab");
    tab.addEventListener("click", () => selectPrompt(item.name));
    return tab;
  }));
  selectPrompt(prompts[0]?.name || "");
}

function selectPrompt(name) {
  promptName = name;
  const item = prompts.find(p => p.name === name);
  prompts.forEach((p, index) => {
    elements.promptTabs.children[index]
      ?.setAttribute("aria-selected", p.name === name ? "true" : "false");
  });
  elements.promptText.value = item ? item.text : "";
  elements.promptRevert.disabled = !item?.has_backup;
  setNote(elements.promptNote, "保存すると次の発言から変わります。");
}

async function savePrompt() {
  const text = elements.promptText.value;
  elements.promptSave.disabled = true;
  try {
    const response = await api(`/api/prompts/${promptName}`, { method: "PUT", body: { text } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setNote(elements.promptNote, data.detail || "保存できませんでした。", true);
      return;
    }
    const item = prompts.find(p => p.name === promptName);
    if (item) {
      item.text = text;
      item.has_backup = true;
    }
    elements.promptRevert.disabled = false;
    setNote(elements.promptNote, "保存しました。次の発言から変わります。");
  } catch {
    setNote(elements.promptNote, "保存できませんでした。", true);
  } finally {
    elements.promptSave.disabled = false;
  }
}

async function revertPrompt() {
  try {
    const response = await api(`/api/prompts/${promptName}/revert`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setNote(elements.promptNote, data.detail || "戻せませんでした。", true);
      return;
    }
    elements.promptText.value = data.text;
    const item = prompts.find(p => p.name === promptName);
    if (item) item.text = data.text;
    setNote(elements.promptNote, "ひとつ前に戻しました。もう一度押すと元に戻ります。");
  } catch {
    setNote(elements.promptNote, "戻せませんでした。", true);
  }
}

// ===== 設定を変える =====
async function loadSettings() {
  setNote(elements.settingsNote, "読み込んでいます…");
  let items;
  try {
    const response = await api("/api/settings");
    if (!response.ok) throw new Error();
    items = (await response.json()).settings;
  } catch {
    setNote(elements.settingsNote, "読み込めませんでした。", true);
    return;
  }
  elements.settingsList.replaceChildren(...items.map(item => {
    const row = document.createElement("div");
    row.className = "setting-row";

    const label = document.createElement("div");
    label.className = "setting-label";
    const name = document.createElement("b");
    name.textContent = item.label;
    const note = document.createElement("small");
    note.textContent = item.note;
    label.append(name, note);

    const input = document.createElement("input");
    input.type = "number";
    input.inputMode = "decimal";
    input.dataset.key = item.key;
    input.value = item.value;
    input.min = item.min;
    input.max = item.max;
    if (item.step) input.step = item.step;
    input.setAttribute("aria-label", item.label);

    row.append(label, input);
    return row;
  }));
  setNote(elements.settingsNote, "保存すると再起動なしで反映されます。");
}

async function saveSettings() {
  const values = {};
  for (const input of elements.settingsList.querySelectorAll("input[data-key]")) {
    values[input.dataset.key] = input.value;
  }
  elements.settingsSave.disabled = true;
  try {
    const response = await api("/api/settings", { method: "PUT", body: { values } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setNote(elements.settingsNote, data.detail || "保存できませんでした。", true);
      return;
    }
    setNote(elements.settingsNote, "保存しました。次のやりとりから反映されます。");
  } catch {
    setNote(elements.settingsNote, "保存できませんでした。", true);
  } finally {
    elements.settingsSave.disabled = false;
  }
}

// ===== 記憶を見る =====
const MEMORY_KIND_LABEL = { event: "できごと", fact: "事実", preference: "好み",
                            open_topic: "続いている話", procedure: "やりかた" };
let memoryKind = "semantic";
let openMemory = null;

async function loadMemories(kind = memoryKind) {
  memoryKind = kind;
  for (const tab of elements.memoryTabs.children) {
    tab.setAttribute("aria-selected", tab.dataset.kind === kind ? "true" : "false");
  }
  setNote(elements.memoryNote, "読み込んでいます…");
  let items;
  try {
    const response = await api(`/api/memories?kind=${kind}`);
    if (!response.ok) throw new Error();
    items = (await response.json()).memories;
  } catch {
    setNote(elements.memoryNote, "読み込めませんでした。", true);
    return;
  }
  elements.memoryList.replaceChildren(...items.map(item => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "memory-item";

    const body = document.createElement("span");
    body.className = "body";
    body.textContent = item.text;

    const meta = document.createElement("span");
    meta.className = "meta";
    const stamp = (item.layer === "episode" ? item.occurred_at : item.confirmed_at) || "";
    meta.textContent = stamp.slice(0, 10);

    row.append(body, meta);
    if (item.pinned) {
      const pin = document.createElement("span");
      pin.className = "pin";
      pin.textContent = "保護";
      row.append(pin);
    }
    row.addEventListener("click", () => showMemory(item.id));
    return row;
  }));
  setNote(elements.memoryNote, items.length ? `${items.length}件` : "まだありません。");
}

async function showMemory(id) {
  showSheetPage("memory");
  elements.memoryText.value = "";
  elements.memoryFacts.replaceChildren();
  setNote(elements.memoryDetailNote, "読み込んでいます…");
  try {
    const response = await api(`/api/memories/${id}`);
    if (!response.ok) throw new Error();
    openMemory = await response.json();
  } catch {
    setNote(elements.memoryDetailNote, "読み込めませんでした。", true);
    return;
  }
  const m = openMemory.memory;
  elements.memoryText.value = m.text;
  const facts = [
    ["種類", MEMORY_KIND_LABEL[m.kind] || m.kind],
    [m.layer === "episode" ? "あった日" : "確かめた日",
     (m.layer === "episode" ? m.occurred_at : m.confirmed_at || "").slice(0, 10)],
    ["消える日", (m.expires_at || "").slice(0, 10) || "期限なし"],
    ["ことば", openMemory.tags.join("、") || "なし"],
    ["もとの発言", openMemory.sources.length ? openMemory.sources.join("、") : "なし"],
  ];
  elements.memoryFacts.replaceChildren(...facts.flatMap(([key, value]) => {
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = value;
    return [dt, dd];
  }));
  elements.memoryPin.textContent = m.pinned ? "保護をやめる" : "保護する";
  resetMemoryDelete();
  setNote(elements.memoryDetailNote, m.pinned ? "保護中は自動で消えません。" : "");
}

async function saveMemory() {
  if (!openMemory) return;
  elements.memorySave.disabled = true;
  try {
    const response = await api(`/api/memories/${openMemory.memory.id}`, {
      method: "PUT", body: { text: elements.memoryText.value },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setNote(elements.memoryDetailNote, data.detail || "保存できませんでした。", true);
      return;
    }
    openMemory.memory.text = elements.memoryText.value.trim();
    setNote(elements.memoryDetailNote, "保存しました。");
  } catch {
    setNote(elements.memoryDetailNote, "保存できませんでした。", true);
  } finally {
    elements.memorySave.disabled = false;
  }
}

async function toggleMemoryPin() {
  if (!openMemory) return;
  const next = !openMemory.memory.pinned;
  try {
    const response = await api(`/api/memories/${openMemory.memory.id}/pinned`, {
      method: "PUT", body: { pinned: next },
    });
    if (!response.ok) throw new Error();
    openMemory.memory.pinned = next ? 1 : 0;
    elements.memoryPin.textContent = next ? "保護をやめる" : "保護する";
    setNote(elements.memoryDetailNote, next ? "保護しました。自動では消えません。" : "保護をやめました。");
  } catch {
    setNote(elements.memoryDetailNote, "切り替えられませんでした。", true);
  }
}

async function deleteMemory() {
  if (!openMemory) return;
  try {
    const response = await api(`/api/memories/${openMemory.memory.id}`, { method: "DELETE" });
    if (!response.ok) throw new Error();
    openMemory = null;
    showSheetPage("memories");
    loadMemories();
  } catch {
    setNote(elements.memoryDetailNote, "消せませんでした。", true);
  }
}

// ===== ほかの端末の通話を切る =====
async function endRemoteCall() {
  const value = elements.remoteCallValue;
  try {
    const response = await api("/api/call", { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    value.textContent = data.released ? "切りました" : "通話中の端末なし";
  } catch {
    value.textContent = "つながらない";
  }
  if (calling) endCall({ release: false });
  setTimeout(() => { value.textContent = ""; }, 4000);
}

// ===== 再起動 =====
async function restartApp() {
  setStatus("再起動しています…", "offline");
  closeSettings();
  addMessage("system", "[再起動] 立ち上がるまで少し待ってください");
  if (calling) endCall();
  try {
    await api("/api/restart", { method: "POST" });
  } catch {
    // 落ちる側なので、応答が途切れても想定どおり。
  }
  await waitForServer();
}

async function waitForServer() {
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    await wait(2000);
    try {
      const response = await api("/api/history?limit=0");
      if (response.ok) {
        elements.log.replaceChildren();
        lastDateKey = null;
        await loadHistory();
        setStatus("いるよ");
        addMessage("system", "[再起動] 戻りました");
        return;
      }
    } catch {
      // まだ起きていない。
    }
  }
  setStatus("接続できない", "offline");
  addMessage("system", "[再起動] 戻ってこないので、PCの画面を確認してください");
}

// ===== Date / Time =====
let lastDateKey = null;

function parseMessageDate(value) {
  if (!value) return new Date();

  const normalized = String(value)
    .trim()
    .replace(" ", "T")
    .replace(/(\.\d{3})\d+/, "$1");

  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function getDateKey(date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

function formatDateLabel(date) {
  const now = new Date();

  const today = new Date(
    now.getFullYear(), now.getMonth(), now.getDate()
  );

  const target = new Date(
    date.getFullYear(), date.getMonth(), date.getDate()
  );

  const days = Math.round((today - target) / 86400000);

  if (days === 0) return "今日";
  if (days === 1) return "昨日";

  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}月${date.getDate()}日`;
  }

  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

function addDateSeparator(date) {
  const key = getDateKey(date);
  if (key === lastDateKey) return;

  const separator = document.createElement("div");
  separator.className = "date-separator";
  separator.textContent = formatDateLabel(date);

  elements.log.appendChild(separator);
  lastDateKey = key;
}

function formatTime(value) {
  const raw = value ? String(value).trim() : "";
  if (!raw) return "";

  const normalized = raw
    .replace(" ", "T")
    .replace(/(\.\d{3})\d+/, "$1");

  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return "";

  const now = new Date();

  const startToday = new Date(
    now.getFullYear(), now.getMonth(), now.getDate()
  );

  const startTarget = new Date(
    date.getFullYear(), date.getMonth(), date.getDate()
  );

  const days =
    Math.round((startToday - startTarget) / 86400000);

  const time = date.toLocaleTimeString("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

  if (days === 0) return time;
  if (days === 1) return `昨日 ${time}`;

  if (days > 1 && days < 7) {
    const weekday = date.toLocaleDateString("ja-JP", {
      weekday: "short",
    });
    return `${weekday} ${time}`;
  }

  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}/${date.getDate()} ${time}`;
  }

  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()} ${time}`;
}

// ===== API =====
function api(path, options = {}) {
  const headers = {
    ...(options.headers || {}),
    "X-Kotoha-Token": token,
  };
  const fetchOptions = { ...options, headers };

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    fetchOptions.body = JSON.stringify(options.body);
  }
  return fetch(path, fetchOptions);
}

// ===== Chat UI =====
function setStatus(label, state = "online") {
  elements.status.classList.remove("thinking", "offline", "calling");
  if (state !== "online") elements.status.classList.add(state);
  elements.statusText.textContent = label;
  elements.sheetStatus.textContent = label;
}

function applyPreferences() {
  document.body.classList.toggle("hide-time", !preferences.showTime);
  elements.timeValue.textContent = preferences.showTime ? "表示" : "非表示";
  elements.voiceValue.textContent = preferences.voice ? "オン" : "オフ";
}

function isNearBottom(threshold = 80) {
  const distance =
    elements.log.scrollHeight -
    elements.log.scrollTop -
    elements.log.clientHeight;

  return distance <= threshold;
}

function updateJumpButton() {
  elements.jumpBottom.classList.toggle("show", !isNearBottom());
}

function scrollToBottom(behavior = "auto") {
  elements.log.scrollTo({
    top: elements.log.scrollHeight,
    behavior,
  });

  requestAnimationFrame(updateJumpButton);
}

function createAvatar() {
  const avatar = document.createElement("img");
  avatar.className = "av";
  avatar.src = AVATAR_URL;
  avatar.alt = "";
  return avatar;
}

function createMessageBubble(role, text, iso) {
  const bubble = document.createElement("div");
  bubble.className = "msg";
  bubble.dataset.role = role;
  bubble.dataset.text = text;
  bubble.textContent = text;

  if (iso !== false && role !== "system" && role !== "sys") {
    bubble.dataset.time = formatTime(iso);
  }

  bindMessageInteractions(bubble);
  return bubble;
}

function addMessage(role, text, iso) {
  const shouldFollow = isNearBottom();

if (iso !== false) {
  const date = parseMessageDate(iso);
  addDateSeparator(date);
}

  let element;
  let bubble;

  if (role === "assistant") {
    element = document.createElement("div");
    element.className = "ai-row";
    bubble = createMessageBubble(role, text, iso);
    element.append(createAvatar(), bubble);
  } else {
    bubble = createMessageBubble(role, text, role === "user" ? iso : false);
    bubble.classList.add(role === "user" ? "user" : "sys");
    element = bubble;
  }

  bubble.classList.add("msg-enter");
  elements.log.appendChild(element);

  if (shouldFollow) {
    scrollToBottom();
  } else {
    updateJumpButton();
  }

  return bubble;
}

function addTypingIndicator() {
  const bubble = addMessage("assistant", "", false);
  bubble.classList.add("typing");
  bubble.removeAttribute("data-text");
  bubble.replaceChildren(
    document.createElement("i"),
    document.createElement("i"),
    document.createElement("i"),
  );
  return bubble.closest(".ai-row");
}

function resizeInput() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, MAX_INPUT_HEIGHT)}px`;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const helper = document.createElement("textarea");
    helper.value = text;
    helper.style.position = "fixed";
    helper.style.opacity = "0";
    document.body.appendChild(helper);
    helper.select();
    document.execCommand("copy");
    helper.remove();
  }
}

function markReaction(bubble, reaction) {
  bubble.querySelector(".reaction-mark")?.remove();
  const mark = document.createElement("div");
  mark.className = "reaction-mark";
  mark.textContent = reaction;
  bubble.appendChild(mark);
}

function closeContextMenu() {
  elements.contextMenu.classList.remove("open");
  elements.contextMenu.setAttribute("aria-hidden", "true");
  elements.contextMenu.replaceChildren();
}

function addContextAction(label, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.role = "menuitem";
  button.textContent = label;
  button.addEventListener("click", async () => {
    reactAvatar("avatar-tap");
    await action();
    closeContextMenu();
  });
  elements.contextMenu.appendChild(button);
}

function openContextMenu(bubble, x, y) {
  if (bubble.classList.contains("typing")) return;
  closeContextMenu();

  const role = bubble.dataset.role;
  const text = bubble.dataset.text || "";
  addContextAction("コピー", () => copyText(text));

  if (role === "assistant") {
    addContextAction("👍 良い", () => markReaction(bubble, "👍"));
    addContextAction("👎 微妙", () => markReaction(bubble, "👎"));
  }

  if (role === "user") {
    addContextAction("編集して再送", () => {
      elements.input.value = text;
      resizeInput();
      elements.input.focus();
    });
  }

  elements.contextMenu.classList.add("open");
  elements.contextMenu.setAttribute("aria-hidden", "false");
  const rect = elements.contextMenu.getBoundingClientRect();
  const pad = 10;
  const left = Math.min(Math.max(pad, x - rect.width / 2), window.innerWidth - rect.width - pad);
  const top = Math.min(Math.max(pad, y - rect.height - 12), window.innerHeight - rect.height - pad);
  elements.contextMenu.style.left = `${left}px`;
  elements.contextMenu.style.top = `${top}px`;
}

function bindMessageInteractions(bubble) {
  bubble.addEventListener("contextmenu", event => {
    event.preventDefault();
    openContextMenu(bubble, event.clientX, event.clientY);
  });

  bubble.addEventListener("pointerdown", event => {
    if (event.pointerType !== "touch") return;
    clearTimeout(longPressTimer);
    const x = event.clientX;
    const y = event.clientY;
    longPressTimer = setTimeout(() => openContextMenu(bubble, x, y), LONG_PRESS_MS);
  });

  const cancel = () => {
    clearTimeout(longPressTimer);
    longPressTimer = null;
  };
  bubble.addEventListener("pointerup", cancel);
  bubble.addEventListener("pointercancel", cancel);
  bubble.addEventListener("pointermove", cancel);
}

function openSettings() {
  closeContextMenu();
  showSheetPage("main");
  elements.settingsOverlay.classList.add("open");
  elements.settingsOverlay.setAttribute("aria-hidden", "false");
}

function closeSettings() {
  elements.settingsOverlay.classList.remove("open");
  elements.settingsOverlay.setAttribute("aria-hidden", "true");
}

// ===== Mini avatar =====
const avatarByTime = {
  morning: [
    "/static/avatar/kotoha_morning_sleepy.png",
    "/static/avatar/kotoha_morning_coffee.png",
  ],
  day: [
    "/static/avatar/kotoha_day_phone.png",
    "/static/avatar/kotoha_day_idle.png",
  ],
  afternoon: [
    "/static/avatar/kotoha_afternoon_nap.png",
    "/static/avatar/kotoha_snack.png",
  ],
  evening: [
    "/static/avatar/kotoha_evening_bath.png",
  ],
  night: [
    "/static/avatar/kotoha_night_game.png",
    "/static/avatar/kotoha_night_phone.png",
  ],
  sleep: [
    "/static/avatar/kotoha_sleep.png",
  ],
};

function getAvatarGroup() {
  const hour = new Date().getHours();

  if (hour >= 6 && hour < 11) return "morning";
  if (hour >= 11 && hour < 14) return "day";
  if (hour >= 14 && hour < 17) return "afternoon";
  if (hour >= 17 && hour < 21) return "evening";
  if (hour >= 21 || hour < 2) return "night";

  return "sleep";
}

function updateMiniAvatar() {
  const groupName = getAvatarGroup();
  const images = avatarByTime[groupName];

  // 日付 + 時間帯で1枚固定
  const now = new Date();
  const seed = now.getDate() + groupName.length;
  const index = seed % images.length;

  $("miniAvatarImage").src = images[index];
  scheduleBlink();
}

let blinkTimer = null;

function getBlinkSrc(src) {
  return src.replace(".png", "_blink.png");
}

function scheduleBlink() {
  clearTimeout(blinkTimer);

  const delay = 8000 + Math.random() * 7000;

  blinkTimer = setTimeout(async () => {
    const image = $("miniAvatarImage");
    const normalSrc = image.src;
    const blinkSrc = getBlinkSrc(normalSrc);

    const preload = new Image();
    preload.src = blinkSrc;

    try {
      await preload.decode();

      image.src = blinkSrc;

      setTimeout(() => {
        image.src = normalSrc;
        scheduleBlink();
      }, 130);

    } catch {
      // blink画像が無い場合はそのまま
      scheduleBlink();
    }
  }, delay);
}

function reactAvatar(className = "avatar-react") {
  const image = $("miniAvatarImage");

  image.classList.remove(className);
  void image.offsetWidth;
  image.classList.add(className);
}

// ===== Voice =====
// 読み上げは会話とは独立させる。エンジンが止まっていても会話は続ける。
let calling = false;

// iPhoneは、画面に触れた流れの中でしか音を鳴らさせてくれない。返事の声が届くのは
// 通信を終えたあとで、そのときにはもう指の流れが切れている。そこで、通話や送信の
// 指の動きで音の出口を開けておき、以後はそこへ流し込む。鳴らすたびに Audio を
// 作る書き方だと、作ったものが毎回「触れていない」ものとして黙って弾かれる。
let audioOut = null;
let currentSource = null;

function audioReady() {
  const Sound = window.AudioContext || window.webkitAudioContext;
  if (!Sound) return null;
  if (!audioOut) audioOut = new Sound();
  if (audioOut.state === "suspended") audioOut.resume().catch(() => {});
  return audioOut;
}

// 画面に触れた瞬間に呼ぶ。無音をひとつ鳴らして、音を出す許可を取っておく。
function openAudio() {
  const context = audioReady();
  if (!context) return;
  const source = context.createBufferSource();
  source.buffer = context.createBuffer(1, 1, 22050);
  source.connect(context.destination);
  source.start(0);
  try {
    // iOS 16.4から。着信スイッチを消音にしていても、ことはの声は鳴らす。
    if (navigator.audioSession) navigator.audioSession.type = "playback";
  } catch {
    // 使えない端末では、今までどおり消音スイッチに従う。
  }
}

// 読み上げは文ごとに分けて鳴らすので、途中で打ち切れるよう世代で見分ける。
let speakGeneration = 0;

function stopSpeaking() {
  speakGeneration += 1;
  if (!currentSource) return;
  try {
    currentSource.stop();
  } catch {
    // 鳴り終わったあとのstopは無視してよい。
  }
  currentSource = null;
}

// 鳴らし終えるまで待つ。音は呼び出し側が持つ（つなぎ言葉は使い回すため）。
function playAudio(sound) {
  const context = audioReady();
  if (!context || !sound) return Promise.resolve();
  return new Promise(resolve => {
    const source = context.createBufferSource();
    source.buffer = sound;
    source.connect(context.destination);
    // stopで止めたときも鳴り終わりとして届くので、待ち続けることはない。
    source.addEventListener("ended", () => {
      if (currentSource === source) currentSource = null;
      resolve();
    }, { once: true });
    currentSource = source;
    source.start();
  });
}

async function fetchVoice(text) {
  const context = audioReady();
  if (!context) throw new Error("この端末では声を出せない");
  const response = await api("/api/speak", { method: "POST", body: { text } });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "声を出せない");
  }
  // 受け取った wav をここで音に変える。以後は鳴らすだけなので、間が空かない。
  return await context.decodeAudioData(await response.arrayBuffer());
}

const SPEAK_MAX_CHARS = 300;  // serve/voice.py の MAX_CHARS と揃える
const SENTENCE_END = "。．！？!?\n";
const SHORT_PART = 10;  // これ未満の断片は前につなぐ。細切れに合成すると間延びする。

// 文の切れ目で分ける。全部を1回で合成するより、最初の声が早く出る。
function splitSentences(text) {
  const raw = [];
  let buffer = "";
  for (const ch of text) {
    buffer += ch;
    if (SENTENCE_END.includes(ch)) {
      raw.push(buffer);
      buffer = "";
    }
  }
  if (buffer) raw.push(buffer);

  const parts = [];
  let used = 0;
  for (const piece of raw) {
    const part = piece.trim();
    if (!part) continue;
    if (used + part.length > SPEAK_MAX_CHARS) {
      // 切れ目のない長文でも、入るぶんまでは読む。
      if (!parts.length) parts.push(part.slice(0, SPEAK_MAX_CHARS));
      break;
    }
    used += part.length;
    const last = parts[parts.length - 1];
    if (last && last.length < SHORT_PART) parts[parts.length - 1] = last + part;
    else parts.push(part);
  }
  return parts;
}

// 通話中は設定に関わらず声を出す。再生し終えるまで待てるように解決を返す。
async function speak(text) {
  if ((!preferences.voice && !calling) || !text) return;
  stopSpeaking();
  const mine = speakGeneration;
  const parts = splitSentences(text);
  if (!parts.length) return;

  let pending = fetchVoice(parts[0]);
  for (let i = 0; i < parts.length; i += 1) {
    let sound;
    try {
      sound = await pending;
    } catch (error) {
      setStatus(error.message || "声を出せない", calling ? "calling" : "online");
      return;
    }
    if (mine !== speakGeneration) return;
    // 次の文は、いま鳴らしているあいだに作っておく。
    pending = i + 1 < parts.length ? fetchVoice(parts[i + 1]) : null;
    await playAudio(sound);
    if (mine !== speakGeneration) break;
  }
  // 途中でやめたぶんは、鳴らさなければそのまま消える。
  if (pending) pending.catch(() => {});
}

// ===== Session / Chat =====
async function loadHistory() {
  const response = await api("/api/history");
  if (response.status === 401) return false;
  if (!response.ok) throw new Error(`history:${response.status}`);

  const rows = await response.json();

  elements.log.replaceChildren();
  lastDateKey = null;

  rows.forEach(message => addMessage(message.role, message.text, message.created_at));
  scrollToBottom();
  return true;
}

async function unlock() {
  token = elements.tokenInput.value.trim();
  elements.gateError.textContent = "";
  try {
    if (!await loadHistory()) {
      elements.gateError.textContent = "トークンが違います";
      return;
    }
    localStorage.setItem("kotoha_token", token);
    elements.gate.style.display = "none";
    setStatus("いるよ");
    elements.input.focus();
  } catch {
    setStatus("接続できない", "offline");
    elements.gateError.textContent = "接続できませんでした";
  }
}

async function send() {
  const text = elements.input.value.trim();
  if (!text || elements.sendButton.disabled) return;

  closeContextMenu();
  elements.input.value = "";
  resizeInput();
  addMessage("user", text);
  reactAvatar();
  elements.sendButton.disabled = true;
  setStatus("考え中…", "thinking");
  const typingRow = addTypingIndicator();

  try {
    const response = await api("/api/chat", { method: "POST", body: { text } });
    typingRow?.remove();

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      addMessage("system", "[エラー] " + (error.detail || response.status));
      setStatus("いるよ");
      return;
    }

    const data = await response.json();
    // ことは側から始まる会話も、通常のAIメッセージとして同じ見た目で扱う。
    addMessage("assistant", data.reply);
    reactAvatar();
    elements.mode.textContent = data.mode || "";
    setStatus(calling ? "通話中" : "いるよ", calling ? "calling" : "online");
    // 通話中はつなぎ言葉を挟む都合があるので、読み上げは呼び出し側に任せる。
    if (calling) return data.reply;
    return speak(data.reply);
  } catch {
    typingRow?.remove();
    addMessage("system", "[エラー] 通信失敗");
    setStatus("接続できない", "offline");
  } finally {
    elements.sendButton.disabled = false;
    elements.input.focus();
  }
}

// ===== Call =====
// 方針: 読み上げ中はマイクを開かない。スピーカーでも自分の声を拾わない。
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let callBusy = false;

// 返答を待たせるあいだの間つなぎ。ことはの話し方に合わせて増減してよい。
const FILLERS = ["あっ…", "えっと…", "んー…", "うーん…"];
// 話し終わりからこれだけ待って、まだ返答がなければ言い淀む。
// 発言の内容では変えない。プロンプト1900字のうち発言は14字ほどで、何を話しても
// ことはが読む量はほぼ同じだから、重い問いかけという見分けがつかない。
const FILLER_AFTER_MS = 400;
// つなぎ言葉のあとに置く間。すぐ本文へ移ると畳みかけるように聞こえる。
const FILLER_GAP_MS = 450;
const fillerVoices = new Map();
let lastFiller = null;
// 話し終わりを検知した時刻。認識が確定するのはこの数百ms後になる。
let speechEndedAt = 0;

// 通話はサーバー側で1台だけにする。あとから始めた端末が持ち主になり、
// 前の端末は次の見張りで気づいて切る。置いてきた端末を外から切るためでもある。
const CALL_WATCH_MS = 5000;
let deviceId = localStorage.getItem("kotoha_device") || "";
if (!deviceId) {
  deviceId = Math.random().toString(36).slice(2) + Date.now().toString(36);
  try {
    localStorage.setItem("kotoha_device", deviceId);
  } catch {
    // 保存できなくてもこの起動のあいだは使える。
  }
}
let callWatch = null;


// 通話のあいだに使い回すので、開始時にまとめて作っておく。
async function prepareFillers() {
  for (const text of FILLERS) {
    if (!calling) return;
    if (fillerVoices.has(text)) continue;
    try {
      fillerVoices.set(text, await fetchVoice(text));
    } catch {
      return;  // 音声エンジンがなければ間つなぎなしで続ける。
    }
  }
}

async function playFiller() {
  const ready = [...fillerVoices.keys()];
  if (!ready.length) return false;
  // 続けて同じ言葉にならないようにする。ただし1つしかないなら選ぶ余地はない。
  const choices = ready.length > 1 ? ready.filter(text => text !== lastFiller) : ready;
  lastFiller = choices[Math.floor(Math.random() * choices.length)];
  await playAudio(fillerVoices.get(lastFiller));
  return true;
}

// 返答が間に合わなければ間をつなぐ。鳴らしたかどうかを返す。
function fillPause(pending) {
  // 話し終わりから数える。認識が確定するまでの間も、相手にとっては無言の待ち時間。
  const since = speechEndedAt ? Date.now() - speechEndedAt : 0;
  speechEndedAt = 0;
  let answered = false;
  pending.then(() => { answered = true; }, () => { answered = true; });
  return wait(Math.max(0, FILLER_AFTER_MS - since))
    .then(() => (answered || !calling ? false : playFiller()));
}

function listen() {
  if (!calling || callBusy || !recognition) return;
  try {
    recognition.start();
  } catch {
    // すでに聞いている場合の二重startは無視してよい。
  }
}

async function onHeard(text) {
  if (!calling || !text) return;
  callBusy = true;
  elements.input.value = text;
  try {
    const pending = send();
    // 間つなぎを鳴らし切ってから本文に移る。声が重ならないようにする。
    const filling = fillPause(pending);
    const reply = await pending;
    if (await filling) await wait(FILLER_GAP_MS);  // 言いよどんだ分の間を置く
    if (calling && reply) await speak(reply);
  } finally {
    callBusy = false;
  }
  if (calling) {
    setStatus("通話中", "calling");
    listen();
  }
}

function createRecognition() {
  const engine = new SpeechRecognition();
  engine.lang = "ja-JP";
  engine.continuous = false;
  engine.interimResults = false;
  engine.maxAlternatives = 1;
  engine.addEventListener("speechend", () => {
    speechEndedAt = Date.now();
  });
  engine.addEventListener("result", event => {
    onHeard((event.results[0][0].transcript || "").trim());
  });
  engine.addEventListener("error", event => {
    // 無音や中断は通話の終わりではない。聞き直す。
    if (event.error === "no-speech" || event.error === "aborted") return;
    if (event.error === "not-allowed" || event.error === "service-not-allowed") {
      addMessage("system", "[エラー] マイクの使用が許可されていません");
      endCall();
      return;
    }
    addMessage("system", "[エラー] 音声入力: " + event.error);
  });
  // 発話が切れるたびに終わるので、話していないあいだは開き直す。
  engine.addEventListener("end", () => listen());
  return engine;
}

function updateCallButton() {
  document.body.classList.toggle("calling", calling);
  elements.callButton.setAttribute("aria-pressed", calling ? "true" : "false");
  elements.callButton.setAttribute(
    "aria-label", calling ? "通話を終わる" : "通話をはじめる"
  );
}

// 5秒ごとに、まだ自分が持ち主か確かめる。入れ替わっていたら黙って切る。
function watchCall() {
  clearInterval(callWatch);
  callWatch = setInterval(async () => {
    if (!calling) return;
    try {
      const response = await api(`/api/call?device=${encodeURIComponent(deviceId)}`);
      if (!response.ok) return;   // 通信の不調で勝手に切らない
      const state = await response.json();
      if (!state.mine) {
        addMessage("system", "[通話] ほかの端末に切り替わりました");
        endCall({ release: false });
      }
    } catch {
      // つながらないあいだは様子を見る。
    }
  }, CALL_WATCH_MS);
}

async function startCall() {
  if (calling) return;
  calling = true;
  recognition = createRecognition();
  updateCallButton();
  setStatus("通話中", "calling");
  prepareFillers();  // 待たない。間に合ったぶんから使う。
  listen();
  watchCall();
  try {
    await api("/api/call", { method: "POST", body: { device: deviceId } });
  } catch {
    // 名乗れなくても手元の通話は続ける。
  }
}

function endCall({ release = true } = {}) {
  if (!calling) return;
  calling = false;
  callBusy = false;
  speechEndedAt = 0;
  clearInterval(callWatch);
  callWatch = null;
  stopSpeaking();
  if (recognition) {
    recognition.abort();
    recognition = null;
  }
  updateCallButton();
  setStatus("いるよ");
  // 取り上げられた側は消さない。新しい持ち主の記録まで消してしまう。
  if (release) api("/api/call", { method: "DELETE" }).catch(() => {});
}

// ===== Events =====
elements.gateButton.addEventListener("click", unlock);
elements.tokenInput.addEventListener("keydown", event => {
  if (event.key === "Enter") { event.preventDefault(); unlock(); }
});

elements.face.addEventListener("click", openSettings);
elements.settingsOverlay.addEventListener("click", event => {
  if (event.target === elements.settingsOverlay) closeSettings();
});

elements.toggleTime.addEventListener("click", () => {
  preferences.showTime = !preferences.showTime;
  localStorage.setItem("kotoha_show_time", preferences.showTime ? "1" : "0");
applyPreferences();
  updateJumpButton();
});

elements.callButton.addEventListener("click", () => {
  openAudio();  // 指が触れているいまのうちに、音の出口を開けておく
  if (calling) endCall(); else startCall();
});

for (const row of document.querySelectorAll("[data-open]")) {
  row.addEventListener("click", () => {
    const name = row.dataset.open;
    showSheetPage(name);
    if (name === "prompts") loadPrompts();
    if (name === "settings") loadSettings();
    if (name === "memories") loadMemories();
  });
}

for (const back of document.querySelectorAll("[data-back]")) {
  back.addEventListener("click", () => showSheetPage("main"));
}

for (const back of document.querySelectorAll("[data-back-to]")) {
  back.addEventListener("click", () => showSheetPage(back.dataset.backTo));
}

for (const tab of elements.memoryTabs.children) {
  tab.addEventListener("click", () => loadMemories(tab.dataset.kind));
}

elements.memorySave.addEventListener("click", saveMemory);
elements.memoryPin.addEventListener("click", toggleMemoryPin);
const resetMemoryDelete = armOnce(
  elements.memoryDelete, "この記憶を消す", "もう一度押すと消える", deleteMemory
);

elements.promptSave.addEventListener("click", savePrompt);
elements.promptRevert.addEventListener("click", revertPrompt);
elements.settingsSave.addEventListener("click", saveSettings);
elements.endRemoteCall.addEventListener("click", endRemoteCall);
armOnce(elements.restartApp, "再起動する", "もう一度押すと再起動", restartApp);

elements.toggleVoice.addEventListener("click", () => {
  preferences.voice = !preferences.voice;
  if (preferences.voice) openAudio();
  localStorage.setItem("kotoha_voice", preferences.voice ? "1" : "0");
  if (!preferences.voice) stopSpeaking();
  applyPreferences();
});

elements.changeToken.addEventListener("click", () => {
  localStorage.removeItem("kotoha_token");
  token = "";
  closeSettings();
  elements.tokenInput.value = "";
  elements.gate.style.display = "flex";
  elements.tokenInput.focus();
});

elements.log.addEventListener("scroll", updateJumpButton, { passive: true });

elements.jumpBottom.addEventListener("click", () => {
  scrollToBottom("smooth");
});

elements.input.addEventListener("input", resizeInput);
elements.input.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
});
elements.form.addEventListener("submit", event => {
  event.preventDefault();
  if (preferences.voice) openAudio();
  send();
});

document.addEventListener("pointerdown", event => {
  if (elements.contextMenu.classList.contains("open") && !elements.contextMenu.contains(event.target)) {
    closeContextMenu();
  }
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    closeContextMenu();
    closeSettings();
  }
});
window.addEventListener("resize", closeContextMenu);

// ===== iOS Visual Viewport =====
let viewportRaf = null;

function syncVisualViewport() {
  if (viewportRaf) return;

  viewportRaf = requestAnimationFrame(() => {
    const viewport = window.visualViewport;

    const height = viewport?.height ?? window.innerHeight;
    const top = viewport?.offsetTop ?? 0;

    document.documentElement.style.setProperty(
      "--vv-height",
      `${Math.round(height)}px`
    );

    document.documentElement.style.setProperty(
      "--vv-top",
      `${Math.round(top)}px`
    );

    viewportRaf = null;
  });
}

syncVisualViewport();
window.visualViewport?.addEventListener("resize", syncVisualViewport);
window.visualViewport?.addEventListener("scroll", syncVisualViewport);

// ===== Initialize =====
applyPreferences();
updateMiniAvatar();
// 音声入力に対応しない環境では通話ボタンを出さない。
document.body.classList.toggle("no-call", !SpeechRecognition);

setInterval(updateMiniAvatar, 30 * 60 * 1000);

(async () => {
  if (!token) {
    elements.tokenInput.focus();
    return;
  }
  try {
    if (await loadHistory()) {
      elements.gate.style.display = "none";
      setStatus("いるよ");
      elements.input.focus();
      return;
    }
  } catch {
    setStatus("接続できない", "offline");
  }
  token = "";
  elements.tokenInput.focus();
})();
