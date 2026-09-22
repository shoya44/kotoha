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
  diaryList: $("diaryList"),
  habitList: $("habitList"),
  diaryNote: $("diaryNote"),
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
  awaySign: $("awaySign"),
  awayWhere: $("awayWhere"),
};

// 会話の行に並べる小さな顔。立ち姿と同じ素材から切り出してある。
const AVATAR_URL = "/static/sprite/face.png";
// ことはのほうからの発言を見に行く間隔。裏の巡回が60秒なので、これで
// 取りこぼさない。画面が隠れているあいだは見に行かない。
const CATCH_UP_MS = 30000;
const MAX_INPUT_HEIGHT = 120;
const LONG_PRESS_MS = 520;

let token = localStorage.getItem("kotoha_token") || "";
let longPressTimer = null;
// 画面がすでに並べた最後のメッセージ。ここから後ろだけを取りに行く。
let lastMessageId = 0;
let catchUpTimer = null;
// 送信の往復のあいだは見に行かない。返事より先に自分の発言を拾うと二重に並ぶ。
let talking = false;

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

// 通知から開いたとき、ドットから通話へ渡されたときは、**開いた時点で呼ばれている**。
// 看板を出してから押させるのは、一手多い。
async function answerTheCall() {
  const params = new URLSearchParams(location.search);
  const called = params.has("call");
  if (!called && !params.has("remind") && !params.has("snooze")) return;
  await callHer();
  if (called) startCall();
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
  chip.textContent = `Rm. ${reminderWhen(item.due_at)} ${item.text}${repeatMark(item)}`;
  elements.log.appendChild(chip);
  chip.scrollIntoView({ block: "nearest" });
}

// 取り消した印。会話の中でことはが判断して消したものも、目で確かめられる。
function showDropped(item) {
  const chip = document.createElement("div");
  chip.className = "kept-chip";
  chip.textContent = `Rm. 取り消し ${item.text}${repeatMark(item)}`;
  elements.log.appendChild(chip);
  chip.scrollIntoView({ block: "nearest" });
}

// ===== 日記 =====
function diaryDay(day) {
  // "2026-09-21" → "9/21（月）"。年は今年なら省く。
  const [year, month, date] = day.split("-").map(Number);
  const weekday = "日月火水木金土"[new Date(year, month - 1, date).getDay()];
  const head = year === new Date().getFullYear() ? "" : `${year}/`;
  return `${head}${month}/${date}（${weekday}）`;
}

async function loadDiary() {
  const data = await fetchPanel(elements.diaryNote, "/api/diary");
  if (!data) return;
  const habits = data.habits || [];
  elements.habitList.replaceChildren(...(habits.length ? [
    Object.assign(document.createElement("div"), { className: "when", textContent: "何となく覚えていること" }),
    ...habits.map(h => Object.assign(document.createElement("div"), { className: "habit", textContent: h.text })),
  ] : []));
  const items = data.diary;
  elements.diaryList.replaceChildren(...items.map(item => {
    const row = document.createElement("article");
    row.className = "diary-item";
    const when = document.createElement("div");
    when.className = "when";
    when.textContent = diaryDay(item.day);
    const body = document.createElement("p");
    body.className = "body";
    body.textContent = item.text;
    row.append(when, body);
    return row;
  }));
  setNote(elements.diaryNote, items.length ? "" : "まだ日記はありません。日付が変わったあとに1日ぶんを書きます。");
}

// ===== 預かっているもの =====
// 繰り返し（毎日・平日）は、そう見えないと「一度きり」と区別がつかない。
function repeatMark(item) {
  return item.repeat ? `（${item.repeat}）` : "";
}

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
    body.textContent = item.text + repeatMark(item);

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

// **指で触る画面では、開いた拍子に焦点を当てない。** キーボードが立ち上がって
// 画面が縮み、ことはの顔が畳まれる。縮んだ高さの指定が残ると、端に地の黒が
// 出たままになることもある。PCでは今までどおり、開いたらすぐ打てる。
function focusInput() {
  if (window.matchMedia?.("(pointer: coarse)").matches) return;
  elements.input.focus();
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

// 返事を、文ごとの吹き出しに分けて出す。**全文が一度に出ると、書き置きに見える。**
// 人は長い文ほど打つのに時間がかかる。2つ目からは打っている印を挟み、
// 長さぶんだけ待ってから出す。分けるのは見た目だけで、脳には1つの返事のまま
// （履歴を読み直すと1つの吹き出しに戻る）。
const PIECE_MS_PER_CHAR = 45;
const PIECE_MIN_MS = 350;
const PIECE_MAX_MS = 1500;
const PIECES_MAX = 3;      // これより細かくは割らない。細切れは、かえってせわしない
const PIECE_SHORT = 6;     // これ未満の文（「明日やる。」）は前につなぐ。ひと息で打てる長さ

function splitReply(text) {
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
  let broke = false;   // 直前の文が改行で終わっていたか。つなぐときも改行のまま残す
  for (const piece of raw) {
    const part = piece.trim();
    if (!part) continue;
    const last = parts[parts.length - 1];
    const joinable = last !== undefined
      && (part.length < PIECE_SHORT || parts.length >= PIECES_MAX);
    if (joinable) parts[parts.length - 1] = last + (broke ? "\n" : "") + part;
    else parts.push(part);
    broke = piece.endsWith("\n");
  }
  return parts;
}

function typingTime(text) {
  return Math.min(PIECE_MAX_MS, Math.max(PIECE_MIN_MS, text.length * PIECE_MS_PER_CHAR));
}

async function showReply(text, iso) {
  const parts = splitReply(text);
  if (parts.length <= 1) {
    addMessage("assistant", text, iso);
    return;
  }
  addMessage("assistant", parts[0], iso);
  for (const part of parts.slice(1)) {
    const typingRow = addTypingIndicator();
    await wait(typingTime(part));
    typingRow?.remove();
    addMessage("assistant", part, false);
  }
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

// ===== 姿（実体） =====
// **どの絵を出すかは脳が決める。** ここは受け取った名前を描くだけで、
// 時間帯も機嫌も見ない。判定は talk/figure.py が1つだけ持っている。
//
// 実体（姿を出す場所）も脳が決める。ことはは1人なので、姿が出るのは
// ドットか会話画面のどちらか片方だけ。こちらに居ないあいだは「外出中」。

// この画面の名乗り。**端末ごとに覚えておく。** 裏に回ったり開き直したりしても
// 同じ器として戻れる（ことはは、そこに居たまま待っている）。
let VESSEL = localStorage.getItem("kotoha_vessel") || "";
if (!VESSEL) {
  VESSEL = "web-" + Math.random().toString(36).slice(2, 8);
  try {
    localStorage.setItem("kotoha_vessel", VESSEL);
  } catch {
    // 保存できなくても、この起動のあいだは使える。
  }
}
// 会話画面は大きく出す。iPhoneは1ポイントを3画素で描くので、小さい絵を
// 置くと引き伸ばされて眠くなる。
const SPRITE_URL = "/static/sprite/web/";

let presenceStream = null;
let blinkTimer = null;
let embodied = false;
let currentPicture = "";
// まばたきのある絵の一覧。無い絵は、まばたきしないだけ。
let blinkable = new Set();

async function loadSpriteList() {
  try {
    const response = await fetch("/static/sprite/sprites.json");
    const manifest = await response.json();
    blinkable = new Set(
      Object.entries(manifest.sprites).filter(([, s]) => s.blink).map(([name]) => name));
  } catch {
    // 読めなくても絵は出る。まばたきしないだけ。
  }
}

// 絵は、**読めたことを確かめてから**当てる。確かめずに src を替えると、
// 読めなかったときに壊れた絵の印と alt の文字が出る。外に居て脳に届かない
// ときが、まさにそれ。読めなければ投げるので、呼ぶ側で受ける。
async function readyPicture(src) {
  const preload = new Image();
  preload.src = src;
  await preload.decode();
}

// いちばん新しく頼まれた絵。読み込みを待つあいだに次が届くことがあり、
// **先に読めたほうが後から当たると、古い姿で止まる。**
let wantedPicture = "";

async function showPicture(name) {
  if (!name || name === currentPicture) return;
  wantedPicture = name;
  const src = `${SPRITE_URL}${name}.png`;
  try {
    await readyPicture(src);
  } catch {
    return;                             // 読めないなら、いまの絵のまま
  }
  if (wantedPicture !== name) return;   // 待つあいだに次が来ていた
  $("miniAvatarImage").src = src;
  currentPicture = name;
  scheduleBlink();
}

// HTMLに書いてある最初の1枚だけは、上の道を通らずに読み込まれる。そこが
// 読めなかったときのために、**壊れた絵は隠す。** 何も無い地の色のほうが、
// 壊れた絵の印よりはましなため。繋がれば脳が姿を押し出してくるので戻る。
const avatarImage = $("miniAvatarImage");
avatarImage.addEventListener("error", () => avatarImage.classList.add("unloaded"));
avatarImage.addEventListener("load", () => avatarImage.classList.remove("unloaded"));

function setEmbodied(here, where = "") {
  // **実体が移れば通話も終わる。** 向こうで話しているのに、こちらのマイクが
  // 開いたままなのはおかしい。
  if (!here && calling) {
    addMessage("system", "[通話] ほかの器に切り替わりました");
    endCall({ release: false });
  }
  embodied = here;
  document.body.classList.toggle("away", !here);
  elements.awayWhere.textContent = where === "desktop" ? "デスクトップに居る" : "外出中";
  if (!here) {
    clearTimeout(blinkTimer);
    currentPicture = "";
  }
}

// 繋がりが切れてから、立て札を出すまでの猶予。ブラウザーは短い切断でも
// 繋ぎ直しに入るので、そのたびに立て札を出すと点滅して見える。
const AWAY_GRACE_MS = 6000;

let awayTimer = null;

// **脳に繋がっていないなら、この器に実体は無い。** 実体の居場所を決めるのは
// 脳で、届かないあいだ「居る」とは言えない。繋がり直せば脳がその場で
// here / away と姿を渡してくるので、こちらの思い込みはそこで正される。
function loseTouch() {
  setStatus("接続できない", "offline");
  if (awayTimer) return;
  awayTimer = setTimeout(() => {
    awayTimer = null;
    setEmbodied(false, "");
  }, AWAY_GRACE_MS);
}

function keepTouch() {
  clearTimeout(awayTimer);
  awayTimer = null;
}

// ことはのほうから流れてくる言づて。繋ぎ直しはブラウザーがやってくれる。
function connectPresence() {
  if (!token || presenceStream) return;
  presenceStream = new EventSource(
    `/api/presence/stream?vessel=${VESSEL}&token=${encodeURIComponent(token)}`);
  presenceStream.onopen = () => {
    // 繋がった。いまの居場所は、このあと脳が教えてくる。
    keepTouch();
    setStatus(calling ? "通話中" : "いるよ", calling ? "calling" : "online");
    loadLiving();
    reportActivity(false);
  };
  presenceStream.onmessage = event => {
    keepTouch();
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (message.type === "here") setEmbodied(true);
    else if (message.type === "away") setEmbodied(false, message.where);
    else if (message.type === "act") showPicture(message.picture);
    else if (message.type === "say") {
      $("frameBubble").textContent = message.text || "";
      // 本文は履歴から取る。並べ方を1か所にしておく。
      catchUp();
      reactAvatar();
    }
    if (message.type === "here" || message.type === "away") {
      $("frameBubble").textContent = "";
      loadLiving();
    }
  };
  presenceStream.onerror = loseTouch;
}

function disconnectPresence() {
  keepTouch();
  presenceStream?.close();
  presenceStream = null;
}

// **見えなくなったことは、こちらから言う。** 切断を待つと、iPhoneでは
// 繋がりが残ったままになり、脳が「まだ居る」と思い込んでPushが鳴らなくなる。
// 居場所は動かない。**ことははこの画面に居たまま**で、見ていないあいだの
// 言葉だけがスマホの通知で届く。
function sayGoodbye() {
  if (!token) return;
  const url = `/api/presence/bye?token=${encodeURIComponent(token)}`;
  navigator.sendBeacon(url, new Blob([JSON.stringify({ vessel: VESSEL })],
                                     { type: "application/json" }));
}

// 「こっちに呼ぶ」。話しかければ来るので、押すのは用が無いときだけ。
async function callHer() {
  try {
    await api("/api/presence/here", { method: "POST", body: { vessel: VESSEL } });
  } catch {
    setStatus("接続できない", "offline");
  }
}

function scheduleBlink() {
  clearTimeout(blinkTimer);
  if (!blinkable.has(currentPicture)) return;

  blinkTimer = setTimeout(async () => {
    const image = $("miniAvatarImage");
    const open = `${SPRITE_URL}${currentPicture}.png`;
    const closed = `${SPRITE_URL}${currentPicture}-blink.png`;
    try {
      await readyPicture(closed);
      image.src = closed;
      setTimeout(() => {
        image.src = open;
        scheduleBlink();
      }, 130);
    } catch {
      scheduleBlink();
    }
  }, 4000 + Math.random() * 6000);
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

// plain を付けたぶんだけ、機嫌の乗らない素の声で返る。**どんな声にするかは
// 脳が決める**ので、器から話速や音高を送ることはない。
async function fetchVoice(text, plain = false) {
  const context = audioReady();
  if (!context) throw new Error("この端末では声を出せない");
  const response = await api("/api/speak", { method: "POST", body: { text, plain } });
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

// 届いた文から順に鳴らすための行列。**作り始めるのは受け取った瞬間**で、
// 鳴らすのは前の文が終わってから。流しながら喋る道（/api/chat/stream）では、
// 文が来るたびにここへ足していく。
let speakChain = Promise.resolve();

function enqueueSpeech(text) {
  if (!text || !text.trim()) return speakChain;
  const mine = speakGeneration;
  const pending = fetchVoice(text).catch(() => null);
  speakChain = speakChain.then(async () => {
    if (mine !== speakGeneration) return;
    const sound = await pending;
    if (!sound || mine !== speakGeneration) return;
    await playAudio(sound);
  });
  return speakChain;
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
  lastMessageId = rows.length ? rows[rows.length - 1].id : 0;
  scrollToBottom();
  return true;
}

// ことはのほうからの発言は、通知と同時に履歴へ残る。開いたままの画面はそれを
// 知らないので、**自分が持っている最後のぶんより後ろだけ**を取りに行く。
// 全部を読み直すと、そのたびに画面を組み直すことになる。
async function catchUp() {
  if (!token || !lastMessageId || document.hidden || talking) return;
  try {
    const response = await api(`/api/history?after=${lastMessageId}`);
    if (!response.ok) return;
    for (const message of await response.json()) {
      addMessage(message.role, message.text, message.created_at);
      lastMessageId = message.id;
    }
  } catch {
    // つながらないあいだは、次の番を待つだけでよい。
  }
}

function watchHistory() {
  clearInterval(catchUpTimer);
  catchUpTimer = setInterval(catchUp, CATCH_UP_MS);
  catchUp();
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
    connectPresence();
    elements.gate.style.display = "none";
    setStatus("いるよ");
    focusInput();
    setupPush();
    watchHistory();
  } catch {
    setStatus("接続できない", "offline");
    elements.gateError.textContent = "接続できませんでした";
  }
}

async function send() {
  const text = elements.input.value.trim();
  if (!text || elements.sendButton.disabled) return;

  closeContextMenu();
  talking = true;
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
    const response = await api("/api/chat",
      { method: "POST", body: { text, vessel: VESSEL } });
    typingRow?.remove();

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      addMessage("system", "[エラー] " + (error.detail || response.status));
      setStatus("いるよ");
      return;
    }

    const data = await response.json();
    if (data.last_id) lastMessageId = data.last_id;
    // 文ごとの吹き出しに分けて出す。声は分けずに、最初の吹き出しと同時に始める。
    const shown = showReply(data.reply);
    reactAvatar();
    elements.mode.textContent = data.mode || "";
    setStatus(calling ? "通話中" : "いるよ", calling ? "calling" : "online");
    // 通話中はつなぎ言葉を挟む都合があるので、読み上げは呼び出し側に任せる。
    const spoken = calling ? Promise.resolve(data.reply) : speak(data.reply);
    await shown;
    (data.kept || []).forEach(showKept);
    (data.dropped || []).forEach(showDropped);
    return spoken;
  } catch {
    typingRow?.remove();
    addMessage("system", "[エラー] 通信失敗");
    setStatus("接続できない", "offline");
  } finally {
    talking = false;
    elements.sendButton.disabled = false;
    elements.input.focus();
    loadLiving();
  }
}

// 言い終わった文から受け取る。**器は取り次ぐだけ**で、判断も記憶も脳の側。
// まとめて受け取る /api/chat と中身は同じで、違うのは届く順番だけ。
async function sendStream(text, onFirst) {
  addMessage("user", text);
  reactAvatar();
  elements.sendButton.disabled = true;
  // 往復のあいだは、履歴の見行きに自分の言葉を拾わせない。ユーザーの行は
  // サーバー側ではもう確定しているので、生成が長引いた回に見行き（30秒ごと）
  // が発火すると、吹き出しが二重に並ぶ。send() と同じ印・同じ頃合い。
  talking = true;
  setStatus("考え中…", "thinking");
  const typingRow = addTypingIndicator();
  let opened = false;

  const open = () => {
    if (opened) return;
    opened = true;
    typingRow?.remove();
    if (onFirst) onFirst();
  };

  try {
    const response = await api("/api/chat/stream",
      { method: "POST", body: { text, vessel: VESSEL } });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      open();
      addMessage("system", "[エラー] " + (error.detail || response.status));
      setStatus("いるよ");
      return;
    }

    let done = null;
    let failed = "";
    const take = line => {
      const item = JSON.parse(line);
      if (item.say) {
        open();
        enqueueSpeech(item.say);
      } else if (item.done) {
        done = item.done;
      } else if (item.error) {
        failed = item.error;
      }
    };

    // 古い端末では流れてこないことがある。そのときは届いてからまとめて読む。
    if (response.body) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        let cut = buffer.indexOf("\n");
        while (cut >= 0) {
          const line = buffer.slice(0, cut).trim();
          buffer = buffer.slice(cut + 1);
          if (line) take(line);
          cut = buffer.indexOf("\n");
        }
      }
      if (buffer.trim()) take(buffer.trim());
    } else {
      (await response.text()).split("\n").forEach(line => {
        if (line.trim()) take(line.trim());
      });
    }

    open();
    if (!done) {
      addMessage("system", "[エラー] " + (failed || "うまく言えなかった"));
      setStatus(calling ? "通話中" : "いるよ", calling ? "calling" : "online");
      return;
    }
    if (done.last_id) lastMessageId = done.last_id;
    addMessage("assistant", done.reply);
    (done.kept || []).forEach(showKept);
    (done.dropped || []).forEach(showDropped);
    reactAvatar();
    elements.mode.textContent = done.mode || "";
    setStatus(calling ? "通話中" : "いるよ", calling ? "calling" : "online");
    // 返事までのぶんは取り終えた。目印も進んだので、見行きを再開してよい。
    talking = false;
    enqueueSpeech(done.rest);       // まだ声にしていないぶん
    await speakChain;
  } catch {
    open();
    addMessage("system", "[エラー] 通信失敗");
    setStatus("接続できない", "offline");
  } finally {
    talking = false;
    elements.sendButton.disabled = false;
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

// 通話は姿のあるところでしか始まらない。**始めると実体ごとこちらへ来る**ので、
// 通話の持ち主を別に持たない。ほかの器へ移されたら away が届き、そこで切る。
// 5秒ごとの見張りは要らなくなった。


// 通話のあいだに使い回すので、開始時にまとめて作っておく。
async function prepareFillers() {
  for (const text of FILLERS) {
    if (!calling) return;
    if (fillerVoices.has(text)) continue;
    try {
      // **通話の初めに一度だけ作って、最後まで使い回す。** そのときの機嫌が
      // 終わりまで残ってしまうので、間つなぎは素の声にしておく。
      fillerVoices.set(text, await fetchVoice(text, true));
    } catch {
      return;  // 音声エンジンがなければ間つなぎなしで続ける。
    }
  }
}

function playFiller() {
  const ready = [...fillerVoices.keys()];
  if (!ready.length) return false;
  // 続けて同じ言葉にならないようにする。ただし1つしかないなら選ぶ余地はない。
  const choices = ready.length > 1 ? ready.filter(text => text !== lastFiller) : ready;
  lastFiller = choices[Math.floor(Math.random() * choices.length)];
  const sound = fillerVoices.get(lastFiller);
  const mine = speakGeneration;
  // **本文と同じ行列に並べる。** 流しながら喋ると本文のほうが先に届くことが
  // あり、別々に鳴らすと声が重なる。言い淀んだぶんの間もここで置く。
  speakChain = speakChain.then(async () => {
    if (mine !== speakGeneration) return;
    await playAudio(sound);
    await wait(FILLER_GAP_MS);
  });
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
  elements.input.value = "";
  resizeInput();
  try {
    // **最初の1文が決まった時点で「答えた」ことにする。** 生成の終わりを
    // 待つ必要はない。間つなぎを出すかどうかも、そこで決まる。
    let answered;
    const first = new Promise(resolve => { answered = resolve; });
    const finished = sendStream(text, answered);
    fillPause(first);
    await finished;
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

async function startCall() {
  if (calling) return;
  calling = true;
  recognition = createRecognition();
  updateCallButton();
  setStatus("通話中", "calling");
  prepareFillers();  // 待たない。間に合ったぶんから使う。
  listen();
  try {
    await api("/api/call", { method: "POST", body: { vessel: VESSEL } });
  } catch {
    // 名乗れなくても手元の通話は続ける。
  }
}

function endCall({ release = true } = {}) {
  if (!calling) return;
  calling = false;
  callBusy = false;
  speechEndedAt = 0;
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
    if (name === "diary") loadDiary();
    if (name === "vessel") loadVessel();
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

// 台本は、通知を使うと決まってから取りに行く。**会話はいちばん私的な画面**で、
// 使わない機能のために第三者のサーバーへ毎回つなぐ理由はない。
function loadOneSignal() {
  if (document.getElementById("onesignal-sdk")) return;
  const tag = document.createElement("script");
  tag.id = "onesignal-sdk";
  tag.src = "https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js";
  tag.defer = true;
  document.head.appendChild(tag);
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

  loadOneSignal();

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
elements.input.addEventListener("blur", () => {
  setTyping(false);
  // キーボードが引っ込んだことを、端末が知らせてこないことがある。**その場で
  // 捨てる。** 残ると画面が縮んだままで、端に地の黒が出る。まだ出ているなら、
  // 次の測り直しでまた立つ。
  document.documentElement.style.removeProperty("--vv-height");
  document.documentElement.style.removeProperty("--vv-top");
});

elements.jumpBottom.addEventListener("click", () => {
  scrollToBottom("smooth");
});

// **枠の中ならどこを触っても打ち始められる。** 空のときの入力欄は1行ぶん
// （24px）しかなく、囲みの余白を触っても何も起きないのは、見た目に反する。
// ボタンの上は渡さない。
document.querySelector(".form-inner").addEventListener("click", event => {
  if (event.target.closest("button")) return;
  elements.input.focus();
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
// これ以上縮んでいたら、キーボードが出ていると見なす。並びかえの候補が出る
// バーだけでもこのくらいにはなるので、低めに取る。
const KEYBOARD_MIN = 120;

let viewportRaf = null;

function syncVisualViewport() {
  if (viewportRaf) return;

  viewportRaf = requestAnimationFrame(() => {
    const viewport = window.visualViewport;
    const shell = document.documentElement.style;
    const height = viewport?.height ?? window.innerHeight;

    // **キーボードが出ていないときは何も指定しない。** 100dvh、つまり画面
    // いっぱいに任せる。端末の言う値をそのまま貼ると、読み取った頃合いに
    // よっては一回り小さい値が返り、端に地の黒が覗いたまま固まる。
    if (window.innerHeight - height < KEYBOARD_MIN) {
      shell.removeProperty("--vv-height");
      shell.removeProperty("--vv-top");
    } else {
      shell.setProperty("--vv-height", `${Math.round(height)}px`);
      shell.setProperty("--vv-top", `${Math.round(viewport.offsetTop)}px`);
    }

    viewportRaf = null;
    // キーボードが出ると会話欄がその場で縮む。誰も戻さないと、縮んだぶん
    // だけ最新が下へはみ出して見えなくなる。
    keepBottomInView();
  });
}

syncVisualViewport();
elements.awaySign.addEventListener("click", callHer);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) catchUp();
});

window.visualViewport?.addEventListener("resize", syncVisualViewport);
window.visualViewport?.addEventListener("scroll", syncVisualViewport);

// ===== Initialize =====
let vesselProfile = null;
async function livingApi(path, options) {
  const response = await api(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "保存できませんでした");
  return data;
}
async function loadVessel() {
  try {
    vesselProfile = await livingApi(`/api/vessel?vessel=${encodeURIComponent(VESSEL)}`);
    $("vesselKind").value = vesselProfile.kind;
    $("vesselLabel").value = vesselProfile.label;
    $("vesselFrame").checked = vesselProfile.frame;
    syncFrameChoice();
  } catch (error) { $("vesselNote").textContent = error.message; }
}
function syncFrameChoice() {
  $("vesselFrame").disabled = $("vesselKind").value !== "tablet";
  if ($("vesselFrame").disabled) $("vesselFrame").checked = false;
}
$("vesselKind").addEventListener("change", syncFrameChoice);
$("vesselSave").addEventListener("click", async () => {
  try {
    vesselProfile = await livingApi("/api/vessel", { method: "POST", body: {
      vessel: VESSEL, kind: $("vesselKind").value, label: $("vesselLabel").value,
      frame: $("vesselFrame").checked,
    } });
    $("vesselNote").textContent = "保存しました。次の会話から反映されます。";
    framePaused = false;
    loadLiving();
  } catch (error) { $("vesselNote").textContent = error.message; }
});
let lastActivitySent = 0;
function reportActivity(active = false) {
  if (!token || document.hidden || !presenceStream) return;
  if (active && Date.now() - lastActivitySent < 15000) return;
  if (active) lastActivitySent = Date.now();
  api("/api/presence/activity", { method: "POST", body: { vessel: VESSEL, active } }).catch(() => {});
}
document.addEventListener("pointerdown", () => reportActivity(true), { passive: true });
document.addEventListener("keydown", () => reportActivity(true));
setInterval(() => { reportActivity(false); if (!document.hidden) loadLiving(); }, 30000);

let framePaused = false;
let quietNow = false;
function renderFrame() {
  const framed = Boolean(vesselProfile?.frame && !framePaused);
  document.body.classList.toggle("frame-mode", framed);
  $("framePanel").hidden = !framed;
  $("frameReturn").hidden = !(vesselProfile?.frame && framePaused);
  $("frameClock").textContent = new Date().toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" });
  $("frameChat").textContent = framePaused ? "額縁に戻る" : "話しかける";
}
async function loadLiving() {
  if (!token || document.hidden) return;
  try {
    const state = await livingApi(`/api/living?vessel=${encodeURIComponent(VESSEL)}`);
    vesselProfile = state.profile;
    quietNow = state.quiet;
    renderGrowth(state.growth);
    $("quietValue").textContent = quietNow ? "終了" : "開始";
    if (!embodied && state.where) elements.awayWhere.textContent = `${state.where}に居る`;
    $("frameLetter").textContent = state.note ? `${new Date(state.note.at).toLocaleString("ja-JP")} の置き手紙\n${state.note.text}` : "";
    renderFrame();
  } catch { /* 接続状態の表示は既存の接続処理に任せる。 */ }
}
$("quietToggle").addEventListener("click", async () => {
  try {
    await livingApi("/api/living/quiet", { method: "POST", body: { enabled: !quietNow } });
    await loadLiving();
  } catch (error) { setStatus(error.message, "offline"); }
});
$("frameChat").addEventListener("click", () => {
  framePaused = !framePaused;
  renderFrame();
  if (framePaused) { callHer(); elements.input.focus(); }
});
$("frameReturn").addEventListener("click", () => { framePaused = false; elements.input.blur(); renderFrame(); });
setInterval(renderFrame, 60000);

function renderGrowth(proposal) {
  $("growthCard").hidden = !proposal;
  if (!proposal) return;
  $("growthText").textContent = proposal.text;
  $("growthEvidence").replaceChildren(...proposal.evidence.map(source => {
    const line = document.createElement("p");
    line.textContent = `${new Date(source.at).toLocaleDateString("ja-JP")}「${source.quote}」`;
    return line;
  }));
  const labels = { try: "一週間試す", skip: "見送る", keep: "これからも続ける", undo: "元に戻す", reset: "元に戻す" };
  $("growthActions").replaceChildren(...proposal.actions.map(action => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sheet-button";
    button.textContent = labels[action];
    button.addEventListener("click", async () => {
      for (const item of $("growthActions").children) item.disabled = true;
      try {
        await livingApi("/api/living/growth", { method: "POST", body: { id: proposal.id, action } });
        await catchUp();
        await loadLiving();
      } catch (error) {
        $("growthText").textContent = error.message;
        for (const item of $("growthActions").children) item.disabled = false;
      }
    });
    return button;
  }));
}

applyPreferences();
loadSpriteList();
// 音声入力に対応しない環境では通話ボタンを出さない。
document.body.classList.toggle("no-call", !SpeechRecognition);

// 姿が変わるのは脳が決めたときだけ。こちらから見に行かない。
elements.awaySign.addEventListener("click", callHer);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    sayGoodbye();
    disconnectPresence();
  } else {
    connectPresence();
  }
});
window.addEventListener("pagehide", sayGoodbye);

(async () => {
  if (!token) {
    elements.tokenInput.focus();
    return;
  }
  try {
    if (await loadHistory()) {
      elements.gate.style.display = "none";
      setStatus("いるよ");
      focusInput();
      setupPush();
      watchHistory();
      connectPresence();
      answerTheCall();         // 通知やドットから開かれたとき
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
