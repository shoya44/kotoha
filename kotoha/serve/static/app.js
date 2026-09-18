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
  volumeRange: $("volumeRange"),
  jumpBottom: $("jumpBottom"),
  settingsOverlay: $("settingsOverlay"),
  toggleTime: $("toggleTime"),
  timeValue: $("timeValue"),
  toggleVoice: $("toggleVoice"),
  voiceValue: $("voiceValue"),
  togglePush: $("togglePush"),
  pushValue: $("pushValue"),
  changeToken: $("changeToken"),
  promptTabs: $("promptTabs"),
  promptText: $("promptText"),
  promptNote: $("promptNote"),
  promptSave: $("promptSave"),
  promptRevert: $("promptRevert"),
  reminderList: $("reminderList"),
  reminderNote: $("reminderNote"),
  machineList: $("machineList"),
  machineNote: $("machineNote"),
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
  // 声の大きさは端末ごと。イヤホンの日と部屋で鳴らす日とで違う。
  volume: clampVolume(localStorage.getItem("kotoha_volume")),
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

// 画面を開いたときの読み込み。4つとも「読む → だめなら一言 → 並べる」で同じ形をしている。
async function fetchPanel(note, path, waiting = "読み込んでいます…") {
  setNote(note, waiting);
  try {
    const response = await api(path);
    if (!response.ok) throw new Error();
    return await response.json();
  } catch {
    setNote(note, "読み込めませんでした。", true);
    return null;
  }
}

// ===== ことばを直す =====
let prompts = [];
let promptName = "";

async function loadPrompts() {
  const data = await fetchPanel(elements.promptNote, "/api/prompts");
  if (!data) return;
  prompts = data.prompts;
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
  const data = await fetchPanel(elements.settingsNote, "/api/settings");
  if (!data) return;
  const items = data.settings;
  // 見出しごとに区切る。9つが平らに並ぶと、どれがどれだか分からなくなる。
  let group = "";
  const rows = items.flatMap(item => {
    const made = [];
    if (item.group && item.group !== group) {
      group = item.group;
      const head = document.createElement("div");
      head.className = "setting-group";
      head.textContent = group;
      made.push(head);
    }
    made.push(makeSettingRow(item));
    return made;
  });
  elements.settingsList.replaceChildren(...rows);
  setNote(elements.settingsNote, "保存すると再起動なしで反映されます。");
}

function makeSettingRow(item) {
  return ((item) => {
    const row = document.createElement(item.type === "bool" ? "button" : "div");
    row.className = "setting-row";

    const label = document.createElement("div");
    label.className = "setting-label";
    const name = document.createElement("b");
    name.textContent = item.label;
    const note = document.createElement("small");
    note.textContent = item.note;
    label.append(name, note);

    if (item.type === "bool") {
      // 同じ「切り替え」なので、いちばん上の「声で話す」と同じ見た目にする。
      row.type = "button";
      const value = document.createElement("span");
      value.className = "sheet-value";
      const paint = () => { value.textContent = row.dataset.on === "true" ? "オン" : "オフ"; };
      row.dataset.key = item.key;
      row.dataset.on = String(item.value).trim().toLowerCase() === "true";
      paint();
      row.addEventListener("click", () => {
        row.dataset.on = row.dataset.on === "true" ? "false" : "true";
        paint();
      });
      row.append(label, value);
      return row;
    }

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
  })(item);
}

async function saveSettings() {
  const values = {};
  for (const input of elements.settingsList.querySelectorAll("input[data-key]")) {
    values[input.dataset.key] = input.value;
  }
  for (const row of elements.settingsList.querySelectorAll("button[data-key]")) {
    values[row.dataset.key] = row.dataset.on;
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

// ===== 通知の「あとで」 =====
function readIds(name) {
  const asked = new URLSearchParams(location.search).get(name);
  if (!asked) return [];
  // 押した跡はURLから消す。読み込み直すたびに効いては困る。
  history.replaceState(null, "", location.pathname);
  return asked.split(",").map(one => parseInt(one, 10)).filter(Number.isInteger);
}

async function snoozeNow(ids) {
  try {
    const response = await api("/api/reminders/snooze", { method: "POST", body: { ids } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      addMessage("system", "[あとで] 置き直せませんでした");
      return false;
    }
    addMessage("system", `[あとで] ${data.due_at} にもう一度言ってもらいます`);
    return true;
  } catch {
    addMessage("system", "[あとで] 置き直せませんでした");
    return false;
  }
}

// iPhoneは通知にボタンを出せない（Safariが対応していない）ので、
// 通知から開いたこの画面に、一度だけ押せる形で出す。
function offerSnooze() {
  const ids = readIds("remind");
  if (!ids.length) return;
  const bubble = addMessage("system", "");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "snooze-offer";
  button.textContent = "あとでもう一度言ってもらう";
  button.addEventListener("click", async () => {
    button.disabled = true;
    if (!await snoozeNow(ids)) button.disabled = false;
    else button.remove();
  });
  bubble.append(button);
}

// Chrome の通知ボタンから来たときは、開いた時点でもう押されている。
async function handleSnoozeLink() {
  const ids = readIds("snooze");
  if (ids.length) await snoozeNow(ids);
}

// 預かった印。ことはの言葉は変えないので、伝わったかどうかを目で確かめられる。
function showKept(item) {
  const chip = document.createElement("div");
  chip.className = "kept-chip";
  chip.textContent = `Rm. ${reminderWhen(item.due_at)} ${item.text}`;
  elements.log.appendChild(chip);
  chip.scrollIntoView({ block: "nearest" });
}

// ===== 預かっているもの =====
function reminderWhen(due) {
  // "2026-09-18 09:00" → "9/18 09:00"。今年の予定に年は要らない。
  const [date, time] = due.split(" ");
  const [year, month, day] = date.split("-");
  const head = year === String(new Date().getFullYear()) ? "" : `${year}/`;
  return `${head}${Number(month)}/${Number(day)} ${time}`;
}

async function loadReminders() {
  const data = await fetchPanel(elements.reminderNote, "/api/reminders");
  if (!data) return;
  const items = data.reminders;
  elements.reminderList.replaceChildren(...items.map(item => {
    const row = document.createElement("div");
    row.className = "reminder-item";

    const when = document.createElement("span");
    when.className = "when";
    when.textContent = reminderWhen(item.due_at);

    const body = document.createElement("span");
    body.className = "body";
    body.textContent = item.text;

    // 取り消しは2度押し。押し間違いで預けたものが消えるほうが困る。
    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "reminder-drop";
    drop.textContent = "取り消す";
    let armed = false;
    drop.addEventListener("click", async () => {
      if (!armed) {
        armed = true;
        drop.textContent = "もう一度";
        drop.classList.add("armed");
        setTimeout(() => {
          armed = false;
          drop.textContent = "取り消す";
          drop.classList.remove("armed");
        }, 4000);
        return;
      }
      drop.disabled = true;
      try {
        const response = await api(`/api/reminders/${item.id}`, { method: "DELETE" });
        if (!response.ok) throw new Error();
        loadReminders();
      } catch {
        drop.disabled = false;
        setNote(elements.reminderNote, "取り消せませんでした。", true);
      }
    });

    row.append(when, body, drop);
    return row;
  }));
  setNote(elements.reminderNote,
          items.length ? `${items.length}件。時刻が来たら言います。`
                       : "いまは何も預かっていません。");
}

// ===== PCの様子 =====
async function loadMachine() {
  const data = await fetchPanel(elements.machineNote, "/api/machine", "調べています…");
  if (!data) return;
  const rows = data.rows;
  elements.machineList.replaceChildren(...rows.map(row => {
    const line = document.createElement("div");
    line.className = "setting-row";
    const label = document.createElement("div");
    label.className = "setting-label";
    const name = document.createElement("b");
    name.textContent = row.label;
    label.append(name);
    const value = document.createElement("div");
    value.className = "machine-value";
    value.textContent = row.value;
    line.append(label, value);
    return line;
  }));
  setNote(elements.machineNote, "開いたときの様子です。見るだけで、何も変わりません。");
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
  const data = await fetchPanel(elements.memoryNote, `/api/memories?kind=${kind}`);
  if (!data) return;
  const items = data.memories;
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

// 一番下に居たかどうか。**高さが変わる前の気持ちを覚えておく。** キーボードが
// 出たあとに測っても、もう「下に居ない」ことになってしまう。
let stickToBottom = true;

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
  stickToBottom = true;
  elements.log.scrollTo({
    top: elements.log.scrollHeight,
    behavior,
  });

  requestAnimationFrame(updateJumpButton);
}

// 会話欄の高さが変わったあとに呼ぶ。下に居た人だけを、下に置いたままにする。
// 上を読み返している最中に引きずり下ろされるほうが、よほど使いづらい。
function keepBottomInView() {
  if (stickToBottom) requestAnimationFrame(() => scrollToBottom());
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
let volumeKnob = null;
let currentSource = null;

function audioReady() {
  const Sound = window.AudioContext || window.webkitAudioContext;
  if (!Sound) return null;
  if (!audioOut) {
    audioOut = new Sound();
    // 音の元と出口のあいだに、つまみを1つだけ挟む。つなぎ言葉も返事も
    // ここを通るので、大きさを変えるのはこの1か所で足りる。
    volumeKnob = audioOut.createGain();
    volumeKnob.gain.value = volumeGain(preferences.volume);
    volumeKnob.connect(audioOut.destination);
  }
  if (audioOut.state === "suspended") audioOut.resume().catch(() => {});
  return audioOut;
}

function clampVolume(saved) {
  const value = Number(saved);
  return Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 100;
}

// つまみの位置をそのまま倍率にすると、真ん中がもう十分に大きく聞こえる。
// 耳の感じ方に近づけるため二乗する。1.0より上は音が割れるので上げない。
const volumeGain = percent => (percent / 100) ** 2;

function setVolume(percent) {
  preferences.volume = clampVolume(percent);
  localStorage.setItem("kotoha_volume", String(preferences.volume));
  // つまみより左を色で埋める。線の描き方はCSS側に任せ、割合だけ渡す。
  elements.volumeRange.style.setProperty("--volume-fill", preferences.volume + "%");
  if (!volumeKnob) return;
  // 鳴っている最中でも変えられる。急に切り替えるとブツッと鳴るので、少しなまらせる。
  volumeKnob.gain.setTargetAtTime(
    volumeGain(preferences.volume), audioOut.currentTime, 0.02
  );
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
    source.connect(volumeKnob || context.destination);
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
    setupPush();
  } catch {
    setStatus("接続できない", "offline");
    elements.gateError.textContent = "接続できませんでした";
  }
}

async function send() {
  const text = elements.input.value.trim();
  if (!text || elements.sendButton.disabled) return;

  closeContextMenu();
  // iPhoneでは、送信ボタンを押した拍子に焦点が外れてキーボードが閉じる。
  // **指の動きが続いているこの瞬間なら戻せる。** 返事を待ってから戻しても、
  // そのときにはもう指が離れていて、キーボードは開かない（下の finally）。
  elements.input.focus();
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
    (data.kept || []).forEach(showKept);
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

elements.volumeRange.value = String(preferences.volume);
setVolume(preferences.volume);
elements.volumeRange.addEventListener("input", event => {
  setVolume(event.target.value);
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
    if (name === "machine") loadMachine();
    if (name === "reminders") loadReminders();
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

// ===== 通知 =====
// 受け取りの面倒（鍵・購読の保存・iOSの作法）はOneSignalに任せている。
// iPhoneは iOS 16.4 以降で、ホーム画面に追加したときだけ受け取れる。
// 押しても何も起きない、が一番困るので、だめなときは理由を行に出す。
let pushReady = null;
let pushNote = "";   // 準備でつまずいた理由

function whenOneSignal(run) {
  window.OneSignalDeferred = window.OneSignalDeferred || [];
  window.OneSignalDeferred.push(run);
}

function homeScreen() {
  return window.matchMedia("(display-mode: standalone)").matches
    || navigator.standalone === true;
}

// 通知を受け取れない理由。受け取れるなら空。
function pushBlocked() {
  const apple = /iPhone|iPad|iPod/.test(navigator.userAgent);
  if (apple && !homeScreen()) return "ホーム画面で";
  if (!("serviceWorker" in navigator)) return "使えない";
  if (!("Notification" in window) || !("PushManager" in window)) {
    return apple ? "iOSが古い" : "使えない";
  }
  if (Notification.permission === "denied") return "拒否ずみ";
  return "";
}

function showPushState() {
  const blocked = pushBlocked();
  if (blocked) { elements.pushValue.textContent = blocked; return; }
  if (!pushReady) { elements.pushValue.textContent = pushNote || "準備中"; return; }
  elements.pushValue.textContent = pushReady.User.PushSubscription.optedIn ? "オン" : "オフ";
}

async function setupPush() {
  let appId = "";
  try {
    const response = await api("/api/push");
    if (response.ok) appId = (await response.json()).appId || "";
  } catch {
    return;   // つながらないだけ。会話には関係ない。
  }
  if (!appId) return;

  elements.togglePush.hidden = false;
  showPushState();
  if (pushBlocked()) return;

  // 入らないことがある。黙って準備中のままにしない。
  const late = setTimeout(() => {
    if (!pushReady) { pushNote = facts(); showPushState(); }
  }, 8000);
  whenOneSignal(async OneSignal => {
    try {
      await OneSignal.init({ appId });
      pushReady = OneSignal;
      pushNote = "";
      OneSignal.User.PushSubscription.addEventListener("change", () => showPushState());
    } catch (error) {
      console.error("OneSignal init:", error);
      pushNote = short(error);
    } finally {
      clearTimeout(late);
    }
    showPushState();
  });
}

// 整わないときに見たい事実。行はせまいので短く詰める。
// 例 "SDK無/default" = 台本が届いていない, "SDK有/granted" = 届いたが止まっている
function facts() {
  const sdk = window.OneSignal ? "SDK有" : "SDK無";
  const allowed = ("Notification" in window) ? Notification.permission : "無";
  return `${sdk}/${allowed}`;
}

// 行に収まる長さの理由。何が起きたか分からないのが一番困る。
function short(error) {
  const text = (error && (error.message || error.name)) || "つながらない";
  return text.length > 24 ? text.slice(0, 24) + "…" : text;
}

async function togglePush() {
  const blocked = pushBlocked();
  if (blocked) { showPushState(); return; }
  if (!pushReady) {
    pushNote = pushNote || facts();
    showPushState();
    return;
  }
  const subscription = pushReady.User.PushSubscription;
  try {
    if (subscription.optedIn) {
      await subscription.optOut();
    } else {
      // 端末に許可を聞く。触れた流れの中でないと、iOSは出してくれない。
      await pushReady.Notifications.requestPermission();
      await subscription.optIn();
    }
  } catch (error) {
    // ここは pushReady があるので、理由を直に出す。
    console.error("OneSignal:", error);
    elements.pushValue.textContent = short(error);
    return;
  }
  showPushState();
}

elements.togglePush.addEventListener("click", togglePush);

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

elements.log.addEventListener("scroll", () => {
  stickToBottom = isNearBottom();
  updateJumpButton();
}, { passive: true });

// 入力中はことはの顔を畳む。キーボードで縮むぶんを、会話欄だけがかぶらない
// ようにする。畳んだ直後は高さが変わるので、最新を置き直す。
function setTyping(on) {
  document.body.classList.toggle("typing", on);
  keepBottomInView();
}

elements.input.addEventListener("focus", () => setTyping(true));
elements.input.addEventListener("blur", () => setTyping(false));

elements.jumpBottom.addEventListener("click", () => {
  scrollToBottom("smooth");
});

elements.input.addEventListener("input", resizeInput);
// 指で触る画面では、確定キーは改行にする。送信はボタンだけ。**物理キーボードの
// あるPCでは今までどおり Enter で送る。** マウスに手を伸ばさないと送れないのは
// 遅い。分けるのは端末の見分けであって、画面の幅ではない。
const BY_FINGER = window.matchMedia?.("(pointer: coarse)").matches ?? false;

elements.input.addEventListener("keydown", event => {
  if (BY_FINGER) return;
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

    // 高さだけでなく幅も見る。キーボードを出すとき、iPhoneは画面を少し引いて
    // 見せることがある。そのとき画面の幅で置くと、左右に地の黒が覗く。
    const height = viewport?.height ?? window.innerHeight;
    const width = viewport?.width ?? window.innerWidth;
    const top = viewport?.offsetTop ?? 0;
    const left = viewport?.offsetLeft ?? 0;

    const shell = document.documentElement.style;
    shell.setProperty("--vv-height", `${Math.round(height)}px`);
    shell.setProperty("--vv-width", `${Math.round(width)}px`);
    shell.setProperty("--vv-top", `${Math.round(top)}px`);
    shell.setProperty("--vv-left", `${Math.round(left)}px`);

    viewportRaf = null;
    // キーボードが出ると会話欄がその場で縮む。誰も戻さないと、縮んだぶん
    // だけ最新が下へはみ出して見えなくなる。
    keepBottomInView();
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
      setupPush();
      handleSnoozeLink();      // Chromeの通知ボタンから開かれたとき
      offerSnooze();           // 頼まれごとの通知から開かれたとき
      return;
    }
  } catch {
    setStatus("接続できない", "offline");
  }
  token = "";
  elements.tokenInput.focus();
})();
