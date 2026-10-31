"use strict";
// Интерфейс Fallen Angel. Все данные дела выводятся только через textContent — HTML со скачанных
// страниц никогда не исполняется. Внешних запросов окно не делает: ссылки показываются текстом.

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : String(c));
  }
  return node;
}

const PER_PAGE = 50;
const MODULES = {
  web: "Сайты", scrape: "Страницы", infra: "Инфраструктура", media: "Фото и видео", username: "Ники",
  telegram: "Telegram", wp: "Публикации", search: "Запросы", ioc: "Индикаторы", translate: "Перевод", text: "Текст",
};
const ACTIVE = ["в очереди", "выполняется"];
const TABS = [["ai", "Ассистент"], ["records", "Записи"], ["files", "Файлы"], ["timeline", "Хронология"], ["checks", "Проверка вручную"]];
// метки статусов: цвет всегда вместе с текстом
const JOB_PILL = { "выполняется": "warn", "в очереди": "neutral", "готово": "ok", "ошибка": "err", "отменено": "neutral" };
const pill = (text, kind = "neutral", title = null) => el("span", { class: "pill " + kind, title }, text);
const findingPill = (f) => f.status ? pill("гипотеза", "warn", f.status) : pill("источник", "ok");
const freshPaste = () => ({ text: null, editing: false, draft: "", dirty: false });

const S = {
  vault: "", cases: [], tools: [], current: null, data: null, casesInfo: [],
  page: "cases", tab: "records", view: null,     // view: {kind: summary|note|finding|evidence, id}
  q: "", qDraft: "", qmode: "title", pageNo: 1, filesView: "tiles",
  jobs: [], seenDone: new Set(), paste: freshPaste(),
  route: null, ai: null, aiDraft: "", aiBusy: false, aiLast: null,
  plan: null,                                      // что запустит «Всё подходящее» для введённого запроса
  aPlan: null, assist: null,                       // ассистент: план по тексту просьбы; идущая пачка {batch, text, case}
  chat: { msgs: [], case: null, draft: "" },      // диалог на главной — только в памяти окна
  settings: null, sideFilter: "",
};
let api = null;

async function call(name, ...args) {
  try {
    return await api[name](...args);
  } catch (e) {
    toast((e && e.message) || String(e), true);
    throw e;
  }
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isErr ? 6000 : 3000);
}

// у каждой копии логотипа свои id градиентов: иначе url(#…) укажет на скрытую копию и не нарисуется
let logoCopies = 0;
function logoArt() {
  const frag = $("#logo").content.cloneNode(true);
  const n = ++logoCopies;
  const ids = new Map();
  for (const node of frag.querySelectorAll("[id]")) { ids.set(node.id, `${node.id}-${n}`); node.id = `${node.id}-${n}`; }
  for (const node of frag.querySelectorAll("[fill], [stroke]")) {
    for (const a of ["fill", "stroke"]) {
      const m = (node.getAttribute(a) || "").match(/^url\(#(.+)\)$/);
      if (m && ids.has(m[1])) node.setAttribute(a, `url(#${ids.get(m[1])})`);
    }
  }
  return el("div", { class: "hero-art" }, frag);
}

// ---------- состояние и навигация ----------
function applyState(st) {
  S.vault = st.vault;
  S.cases = st.cases || [];
  if (st.tools) S.tools = st.tools;
  if (S.vault === "закрыто") {
    // после блокировки не оставляем на экране материалы дела
    S.current = null; S.data = null; S.view = null; S.page = "cases"; S.paste = freshPaste();
    if (!S.assist?.chat) S.chat = { msgs: [], case: null, draft: "" };
    $("#page").replaceChildren();
  }
  if (S.current && !S.cases.includes(S.current)) { S.current = null; S.data = null; S.page = "cases"; }
  if (S.chat.case && !S.cases.includes(S.chat.case) && S.vault !== "закрыто") S.chat.case = null;  // дело диалога удалили
  $("#lockscreen").hidden = S.vault !== "закрыто";
  $("#app").hidden = S.vault === "закрыто";
  $("#vault").textContent = S.vault === "выключено" ? "шифрование дел выключено" : "хранилище: " + S.vault + " · AES-256";
  renderSide();
}

// ---------- боковая панель: дела всегда под рукой ----------
function renderSide() {
  const box = $("#side-cases");
  if (!box) return;
  const info = new Map(S.casesInfo.map((c) => [c.name, c]));
  const q = S.sideFilter.trim().toLowerCase();
  const names = S.cases.filter((n) => !q || n.toLowerCase().includes(q));
  box.replaceChildren(...(names.length ? names.map((name) => {
    const c = info.get(name) || {};
    const meta = c.findings == null ? "" : `${c.findings} зап. · ${c.evidence} файл.` + (c.hypotheses ? ` · ${c.hypotheses} гип.` : "");
    return el("button", { class: "side-case" + (name === S.current && S.page !== "cases" ? " active" : ""), title: name,
      onclick: () => openCase(name) }, el("span", { class: "nm" }, name), meta ? el("span", { class: "meta" }, meta) : null);
  }) : [el("div", { class: "side-empty" }, S.cases.length ? "не найдено" : "дел пока нет")]));
  for (const b of document.querySelectorAll(".side-link[data-page]")) b.classList.toggle("active", b.dataset.page === S.page);
}

async function go(page, view = null) {
  if (!(await leaveEditor())) return;
  if (page === "home") page = "case";
  S.page = page;
  S.view = view;
  S.paste = freshPaste();
  if (page === "cases" || page === "case") S.casesInfo = await call("cases_info");
  if (page === "settings") S.settings = await call("settings");
  render(true);
  $("#page").scrollTop = 0;
  if (page === "paste") loadPaste();
}

function openTab(tab) {
  S.tab = tab;
  S.page = "case";
  render(true);
}

async function openCase(name, page = "case") {
  if (!(await leaveEditor())) return;
  if (S.current !== name) Object.assign(S, { aiDraft: "", aPlan: null, aiLast: null });  // черновик и ответ ИИ — свои у каждого дела
  S.current = name;
  S.q = S.qDraft = "";
  S.pageNo = 1;
  S.tab = "ai";  // дело открывается ассистентом: попросил словами — получил отчёт
  S.data = await call("case", name);
  S.paste = freshPaste();
  go(page);
}

async function reloadCase() {
  if (!S.current) return;
  S.data = await call("case", S.current);
  if (S.page === "cases") S.casesInfo = await call("cases_info");
  if (S.page === "paste" && S.view?.kind === "summary" && !S.paste.editing) loadPaste();
  render();
}

function render(full = false) {
  renderSide();
  const pg = $("#page");
  // правку пасты не пересоздаём: курсор и введённый текст не теряются
  if (S.page === "paste" && S.paste.editing && $("#paste-edit")) return;
  // в деле строку проверки не трогаем (набранный запрос не пропадает) — обновляем шапку и вкладку
  if (S.page === "case" && $("#runform") && !full) {
    $("#case-head").replaceWith(caseHead());
    $("#case-tabs").replaceWith(caseTabs());
    $("#checks").hidden = S.tab !== "checks";
    $("#case-body").replaceChildren(caseBody());
    return renderRecent();
  }
  const pages = { case: casePage, paste: pastePage, evidence: evidencePage, cases: casesPage, queue: queuePage, settings: settingsPage };
  pg.replaceChildren((pages[S.page] || casesPage)());
  if (S.page === "case") { renderToolFields(); renderRecent(); }
}

// ---------- дело: шапка, строка проверки, вкладки ----------
function casePage() {
  if (!S.current || !S.data) return casesPage();
  // строка ручной проверки создаётся один раз: набранный запрос не пропадает при обновлении вкладок
  return el("div", { class: "wrap" }, caseHead(), caseTabs(),
    el("div", { id: "checks", class: "card runbar-card", hidden: S.tab !== "checks" }, runBar(), el("div", { id: "recent", class: "recent" })),
    el("div", { id: "case-body" }, caseBody()));
}

function caseTabs() {
  return el("nav", { id: "case-tabs", class: "tabs" }, TABS.map(([id, label]) =>
    el("button", { class: "tab" + (S.tab === id ? " active" : ""), onclick: () => openTab(id) }, label)));
}

function caseHead() {
  const f = S.data.findings, ev = S.data.evidence.filter((e) => !e.deleted);
  const hyp = f.filter((x) => x.status).length;
  const n = (S.data.notes || []).length;
  return el("div", { id: "case-head", class: "case-head" },
    el("div", { class: "top" },
      el("div", { class: "spacer" }, el("div", { class: "muted small" }, "Дело"), el("h1", { class: "title" }, S.current)),
      el("div", { class: "actions" },
        el("button", { class: "btn ghost small", onclick: exportCase }, "Экспорт"),
        el("button", { class: "btn danger small", onclick: () => deleteCase(S.current) }, "Удалить дело"))),
    el("div", { class: "row" }, pill(`${f.length} ${plural(f.length, "запись", "записи", "записей")}`, "primary"),
      pill(`${hyp} ${plural(hyp, "гипотеза", "гипотезы", "гипотез")}`, hyp ? "warn" : "neutral"),
      pill(`${ev.length} ${plural(ev.length, "файл", "файла", "файлов")}`), pill(`${n} ${plural(n, "заметка", "заметки", "заметок")}`)));
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
}

function caseBody() {
  if (S.tab === "checks") return el("div", { class: "muted small", style: "margin-top:12px" },
    "Одна проверка по выбору. «Всё подходящее» сам определит тип запроса. Результаты появятся во вкладке «Записи».");
  if (S.tab === "files") return filesPage();
  if (S.tab === "timeline") return timelinePage();
  if (S.tab === "ai") return aiPanel();
  return recordsTab();
}

function needCase() {
  return el("div", { class: "empty" }, el("button", { class: "link", onclick: () => go("cases") },
    S.cases.length ? "Выберите дело слева" : "Начните с нового поиска"));
}

// ---------- главная: логотип, поиск, закреплённые и все записи ----------
function noteRows() {
  const q = S.q.trim().toLowerCase();
  const pinned = [{ kind: "summary", id: "", name: "сводка.txt", type: "сводка", size: null, time: "авто" },
    ...(S.data.notes || []).map((n) => ({ kind: "note", id: n.name, name: n.name, type: "заметка", size: n.size, time: n.modified }))];
  return pinned.filter((p) => !q || p.name.toLowerCase().includes(q));
}

function findingRows() {
  const q = S.q.trim().toLowerCase();
  const hit = (f) => !q || [f.title, f.target, f.id, MODULES[f.module] || f.module, f.status]
    .concat(S.qmode === "content" ? [JSON.stringify(f.data)] : [])
    .some((x) => String(x ?? "").toLowerCase().includes(q));
  return S.data.findings.filter(hit).slice().reverse();
}

function recordsTab() {
  if (!S.current || !S.data) return needCase();
  const rows = findingRows();
  const pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
  S.pageNo = Math.min(S.pageNo, pages);
  const shown = rows.slice((S.pageNo - 1) * PER_PAGE, S.pageNo * PER_PAGE);
  const q = el("input", { id: "q", type: "search", placeholder: "Поиск по записям…", autocomplete: "off", spellcheck: "false" });
  q.value = S.qDraft;
  q.addEventListener("input", () => { S.qDraft = q.value; });
  q.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { S.q = S.qDraft; S.pageNo = 1; render(); } });
  const seg = (mode, label) => el("button", { class: S.qmode === mode ? "on" : "", onclick: () => { S.qmode = mode; render(); } }, label);
  const pinned = noteRows();
  return el("div", {},
    el("div", { class: "toolbar" }, q, el("div", { class: "seg" }, seg("title", "в заголовке"), seg("content", "в содержимом")),
      el("button", { class: "btn ghost", onclick: () => { S.q = S.qDraft; S.pageNo = 1; render(); } }, "Искать")),
    el("div", { class: "muted small", style: "margin:8px 0 0" }, `показано ${shown.length} из ${S.data.findings.length}`
      + (S.q ? ` · запрос «${S.q}»` : "")),
    el("h2", { class: "section" }, "Сводка и заметки"),
    el("div", { class: "notes-row" },
      pinned.map((p) => el("button", { class: "note-chip", title: p.name, onclick: () => go("paste", { kind: p.kind, id: p.id }) },
        pill(p.type, p.kind === "summary" ? "primary" : p.name.startsWith("ИИ_") ? "info" : "neutral"), el("span", { class: "nm" }, p.name))),
      el("form", { class: "inline-form", onsubmit: newNote },
        el("input", { id: "new-note-name", placeholder: "новая заметка", maxlength: "80", autocomplete: "off", spellcheck: "false" }),
        el("button", { class: "btn ghost", type: "submit" }, "Создать"))),
    el("h2", { class: "section" }, "Записи", el("span", { class: "muted" }, String(rows.length))),
    shown.length ? el("div", { class: "records" }, shown.map(recordCard))
      : el("div", { class: "empty" }, S.data.findings.length ? "по запросу ничего не найдено"
        : "пока пусто — попросите ассистента или откройте «Проверка вручную»"),
    pages > 1 ? pager(pages) : null);
}

// главные факты записи для карточки: без служебных полей
const SKIP_KEYS = new Set(["user_agent", "files", "csv", "latest", "trackers_blocked", "server_ip_note", "counts", "url", "final_url",
  "snapshots", "headers", "_маршрут"]);
function shortVal(v) {
  if (v == null) return "";
  if (Array.isArray(v)) return v.slice(0, 3).map(shortVal).join(", ") + (v.length > 3 ? ` … +${v.length - 3}` : "");
  if (typeof v === "object") return Object.entries(v).slice(0, 3).map(([k, x]) => `${k}: ${shortVal(x)}`).join(", ");
  const t = String(v).replace(/\s+/g, " ");
  return t.length > 90 ? t.slice(0, 90) + "…" : t;
}
function recordFacts(f, n = 3) {
  if (!f.data || typeof f.data !== "object" || Array.isArray(f.data)) return [];
  return Object.entries(f.data).filter(([k, v]) => !SKIP_KEYS.has(k) && v !== null && v !== "" && !(Array.isArray(v) && !v.length))
    .slice(0, n).map(([k, v]) => el("span", {}, el("i", {}, label(k) + ": "), shortVal(v)));
}

function recordCard(f) {
  return el("button", { class: "card rec", onclick: () => go("paste", { kind: "finding", id: f.id }) },
    el("div", { class: "top" }, el("span", { class: "id" }, f.id), el("span", { class: "ttl", title: f.title }, f.title),
      pill(MODULES[f.module] || f.module, "info"), findingPill(f), el("span", { class: "when" }, fmtDate(f.time))),
    el("div", { class: "facts" }, recordFacts(f)));
}

function findingsTable(rows) {
  return el("div", { class: "records" }, rows.map(recordCard));
}

function pager(pages) {
  const cur = S.pageNo;
  const nums = pages <= 7 ? [...Array(pages).keys()].map((i) => i + 1)
    : cur <= 4 ? [1, 2, 3, 4, 5, "…", pages]
      : cur >= pages - 3 ? [1, "…", pages - 4, pages - 3, pages - 2, pages - 1, pages]
        : [1, "…", cur - 1, cur, cur + 1, "…", pages];
  const to = (n) => () => { S.pageNo = n; render(); $("#page").scrollTop = 0; };
  return el("div", { class: "pager" },
    el("button", { disabled: cur <= 1, onclick: to(cur - 1) }, "<"),
    nums.map((n) => n === "…" ? el("button", { disabled: true }, "…")
      : el("button", { class: n === cur ? "cur" : "", onclick: to(n) }, n)),
    el("button", { disabled: cur >= pages, onclick: to(cur + 1) }, ">"));
}

// ---------- паста: сводка, заметка или находка на всю ширину ----------
function pastePage() {
  if (!S.data || !S.view) return needCase();
  const { kind, id } = S.view;
  const P = S.paste;
  const f = kind === "finding" ? S.data.findings.find((x) => x.id === id) : null;
  const note = kind === "note" ? (S.data.notes || []).find((n) => n.name === id) : null;
  if ((kind === "finding" && !f) || (kind === "note" && !note)) return el("div", { class: "empty" }, "запись не найдена");
  const title = kind === "summary" ? "сводка.txt" : kind === "note" ? id : f.title;
  const text = P.editing ? P.draft : P.text;
  const lines = text == null ? [] : text.split("\n");
  const meta = [
    ["Дело", S.current],
    kind === "finding" ? ["Модуль", MODULES[f.module] || f.module] : ["Тип", kind === "summary" ? "сводка (собирается сама)" : "заметка"],
    kind === "finding" ? ["Добавлено", fmtDate(f.time)] : kind === "note" ? ["Изменено", fmtDate(note.modified)] : null,
    kind === "finding" ? ["Статус", f.status || "получено из источника"] : null,
    text == null ? null : ["Строк", lines.length],
    text == null ? null : ["Размер", fmtSize(new TextEncoder().encode(text).length)],
  ].filter(Boolean);
  const actions = kind === "summary"
    ? [el("button", { class: "link", onclick: summaryToFile, disabled: text == null }, "в файл — дописать вручную"),
       el("button", { class: "link", onclick: loadPaste }, "обновить")]
    : kind === "note"
      ? (P.editing
        ? [el("button", { class: "btn", onclick: saveNote }, "Сохранить ⌘S"),
           el("button", { class: "link", onclick: cancelEdit }, "отмена"),
           el("span", { id: "dirty", class: P.dirty ? "unsaved" : "saved" }, P.dirty ? "● не сохранено" : "сохранено")]
        : [el("button", { class: "link", onclick: startEdit, disabled: text == null }, "редактировать"),
           el("button", { class: "link danger", onclick: deleteNote }, "удалить файл")])
      : [el("button", { class: "link", onclick: () => findingToFile(f), disabled: text == null }, "в файл — дописать вручную")];
  const head = [
    el("button", { class: "link back", onclick: () => go("case") }, "← " + S.current),
    el("div", { class: "head" },
      el("h1", { class: "title" }, title),
      f ? el("div", { class: "row" }, el("span", { class: "id mono muted" }, f.id), pill(MODULES[f.module] || f.module, "info"),
        findingPill(f), el("span", { class: "muted small" }, fmtDate(f.time) + " UTC"),
        f.target ? el("span", { class: "muted small", title: f.target }, "цель: " + shortVal(f.target)) : null)
        : el("div", { class: "paste-meta" }, meta.map(([k, v]) => el("span", {}, k + ": ", el("b", {}, v)))),
      f?.status ? el("div", { class: "warn-box" }, "Это гипотеза, а не установленный факт. Проверьте по первоисточнику.") : null),
    el("div", { class: "paste-actions" }, actions,
      f ? el("button", { class: "btn ghost small", onclick: () => { P.raw = !P.raw; render(); } },
        P.raw ? "Показать таблицей" : "Показать как текст (паста)") : null),
  ];
  let body;
  if (P.editing) {
    const ta = el("textarea", { id: "paste-edit", class: "paste-edit", spellcheck: "false", autocomplete: "off" });
    ta.value = P.draft;
    ta.addEventListener("input", () => {
      P.draft = ta.value;
      if (!P.dirty) { P.dirty = true; const d = $("#dirty"); if (d) { d.textContent = "● не сохранено"; d.className = "unsaved"; } }
    });
    ta.addEventListener("keydown", (ev) => {
      if (ev.key === "Tab" && !ev.metaKey && !ev.ctrlKey) {  // табуляция — два пробела, а не переход фокуса
        ev.preventDefault();
        ta.setRangeText("  ", ta.selectionStart, ta.selectionEnd, "end");
        ta.dispatchEvent(new Event("input"));
      }
    });
    setTimeout(() => ta.focus(), 0);
    body = ta;
  } else if (f && !P.raw) {
    body = dataTable(f.data);
  } else if (text == null) {
    body = el("div", { class: "paste" }, el("div", { class: "ln" }, el("span", { class: "no" }, ""), el("span", { class: "tx dim" }, "загрузка…")));
  } else {
    const q = S.q.trim().toLowerCase();
    body = el("div", { class: "paste" }, lines.map((line, i) => el("div", { class: "ln" + (q && line.toLowerCase().includes(q) ? " hit" : "") },
      el("span", { class: "no" }, i + 1), el("span", { class: "tx" }, linkify(line)))));
  }
  const files = f ? (f.evidence || []).map((eid) => S.data.evidence.find((e) => e.id === eid)).filter(Boolean) : [];
  const photos = files.filter(isPhoto);
  return el("div", { class: "wrap rec-page" }, head, photos.length ? photoStrip(photos) : null, body,
    f ? [el("h2", { class: "section" }, "Файлы-доказательства", el("span", { class: "muted" }, String(files.length))),
         files.length ? el("div", { class: "ev-grid" }, files.map((e) => evidenceCard(e, false)))
           : el("div", { class: "muted" }, "файлов нет — данные получены напрямую")] : null);
}

// данные записи: таблица «поле — значение»; вложенное — внутри, длинный текст — блоком, ссылки кликабельны
function dataValue(v) {
  if (v === null || v === undefined || v === "") return el("span", { class: "muted" }, "—");
  if (Array.isArray(v)) {
    if (!v.length) return el("span", { class: "muted" }, "—");
    if (v.every((x) => x === null || typeof x !== "object")) {
      return v.length <= 4 && v.join(", ").length < 120 ? el("span", {}, linkify(v.join(", ")))
        : el("ul", {}, v.map((x) => el("li", {}, linkify(String(x)))));
    }
    return el("div", {}, v.map((x) => dataTable(x)));
  }
  if (typeof v === "object") {
    return el("div", { class: "sub" }, Object.entries(v).flatMap(([k, x]) => [el("span", { class: "k2" }, label(k)),
      typeof x === "object" && x !== null ? dataValue(x) : el("span", {}, linkify(String(x ?? "—")))]));
  }
  return el("span", {}, String(v).split("\n").flatMap((line, i) => i ? [el("br"), linkify(line)] : [linkify(line)]));
}

// подписи служебных полей источников — по-русски (данные в деле не меняются)
const FIELD_LABELS = { url: "адрес", count: "снимков", first: "первый снимок", last: "последний снимок", latest: "последние снимки",
  title: "заголовок", status: "HTTP-статус", server_ip: "IP сервера", timestamp: "метка времени", original: "исходный адрес",
  statuscode: "код ответа", mimetype: "тип", digest: "отпечаток", date: "дата", archive_url: "в архиве", emails: "e-mail",
  phones: "телефоны", social: "соцсети", csv: "файл CSV", final_url: "итоговый адрес", user_agent: "браузер (UA)",
  files: "файлы", trackers_blocked: "заблокировано трекеров", counts: "ссылок", save_error: "сохранение в архив",
  render: "открыто" };
const label = (k) => FIELD_LABELS[k] || k;

function dataTable(data) {
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    return el("div", { class: "card" }, dataValue(data));
  }
  const entries = Object.entries(data).filter(([k]) => k !== "_маршрут");
  if (!entries.length) return el("div", { class: "empty" }, "данных нет");
  return el("div", { class: "kvt" }, entries.flatMap(([k, v]) => [el("div", { class: "k" }, label(k)), el("div", { class: "v" }, dataValue(v))]));
}

async function loadPaste() {
  if (!S.view || !["summary", "note", "finding"].includes(S.view.kind)) return;
  const { kind, id } = S.view, kase = S.current;
  const text = kind === "summary" ? await call("summary", kase)
    : kind === "note" ? (await call("note", kase, id)).text : await call("finding_paste", kase, id);
  if (S.current !== kase || S.view?.id !== id || S.view?.kind !== kind || S.paste.editing) return;  // пока грузили — ушли
  S.paste.text = text;
  if (S.page === "paste") render();
}

async function leaveEditor() {
  if (!S.paste.editing || !S.paste.dirty) return true;
  return ask({ title: `несохранённые изменения в ${S.view?.id}`, text: "Если уйти, правки пропадут.", okText: "уйти без сохранения" });
}

function startEdit() {
  Object.assign(S.paste, { editing: true, draft: S.paste.text || "", dirty: false });
  render();
}

function cancelEdit() {
  Object.assign(S.paste, { editing: false, draft: "", dirty: false });
  render();
}

async function saveNote() {
  const P = S.paste;
  if (!P.editing || S.view?.kind !== "note") return;
  await call("save_note", S.current, S.view.id, P.draft);
  Object.assign(P, { text: P.draft, editing: false, dirty: false });
  S.data = await call("case", S.current);
  render();
  toast(`сохранено: ${S.view.id}`);
}

async function createNote(name, text) {
  const meta = await call("save_note", S.current, name, text, true);
  S.data = await call("case", S.current);
  await go("paste", { kind: "note", id: meta.name });
  Object.assign(S.paste, { text, editing: true, draft: text, dirty: false });
  render();
}

async function newNote(ev) {
  ev.preventDefault();
  const name = $("#new-note-name").value.trim();
  if (name) await createNote(name, "");
}

function stamp() {
  return new Date().toISOString().slice(0, 16).replace(/[-:]/g, "").replace("T", "-");
}

async function summaryToFile() {
  if (S.paste.text == null) return;
  await createNote(`сводка_${stamp()}`, S.paste.text);
  toast("копия сводки — теперь это ваш файл, его можно править");
}

async function findingToFile(f) {
  if (S.paste.text == null) return;
  await createNote(`${f.id}_${stamp()}`, S.paste.text);
  toast(`копия ${f.id} — теперь это ваш файл, его можно править`);
}

async function deleteNote() {
  const name = S.view.id;
  if (!(await ask({ title: `удалить ${name}?`, text: "Файл заметки будет удалён без возврата.", okText: "удалить файл" }))) return;
  await call("delete_note", S.current, name);
  S.paste = freshPaste();
  S.data = await call("case", S.current);
  go("case");
  toast(`удалено: ${name}`);
}

// ---------- файлы-доказательства ----------
function evidenceCard(e, autoPreview) {
  if (e.deleted) {
    return el("div", { class: "card ev" },
      el("div", { class: "name" }, el("span", { class: "dim" }, e.id + "  "), el("span", { class: "gone" }, e.file)),
      el("div", { class: "sha" }, "sha256 " + e.sha256),
      el("div", { class: "warn-box" }, `файл удалён ${fmtDate(e.deleted)}` + (e.file_removed === false ? " · файл вне дела не трогали" : "")
        + " — запись и контрольная сумма сохранены"));
  }
  const card = el("div", { class: "card ev" },
    el("div", { class: "name" }, el("span", { class: "dim" }, e.id + "  "), e.file),
    el("div", { class: "sha" }, "sha256 " + e.sha256),
    el("div", { class: "dim small" }, `${fmtSize(e.size)} · ${fmtDate(e.time)}${e.source ? " · " + e.source : ""}`),
    e.note ? el("div", { class: "dim small" }, e.note) : null,
    el("div", { class: "row" },
      el("button", { class: "link", onclick: () => call("reveal", S.current, e.file) }, "показать в Finder"),
      el("button", { class: "link danger", onclick: () => deleteEvidence(e) }, "удалить файл")));
  if (/\.(png|jpe?g|webp|gif)$/i.test(e.file)) {
    const holder = el("div");
    card.append(holder);
    const load = () => call("preview", S.current, e.file).then((r) => {
      if (r.ok) holder.replaceChildren(el("img", { src: r.src, alt: e.file }));
    });
    if (autoPreview) load(); else holder.append(el("button", { class: "link", onclick: load }, "предпросмотр"));
  }
  return card;
}

function filesPage() {
  if (!S.current || !S.data) return needCase();
  const rows = S.data.evidence.slice().reverse();
  const live = rows.filter((e) => !e.deleted);
  const head = el("div", { class: "toolbar" },
    el("h2", { class: "section", style: "margin:0" }, "Файлы-доказательства", el("span", { class: "muted" }, String(live.length))),
    el("span", { class: "spacer" }), el("div", { class: "seg" },
      el("button", { class: S.filesView === "tiles" ? "on" : "", onclick: () => { S.filesView = "tiles"; render(); } }, "плитка"),
      el("button", { class: S.filesView === "list" ? "on" : "", onclick: () => { S.filesView = "list"; render(); } }, "список")));
  if (S.filesView === "tiles") {
    const photos = live.filter(isPhoto);
    return el("div", {}, head,
      photos.length ? [el("div", { class: "muted small", style: "margin-top:14px" }, `фото: ${photos.length} — нажмите, чтобы открыть во весь экран`),
                       photoStrip(photos, "tiles")] : null,
      el("h2", { class: "section" }, "Остальные файлы"),
      el("div", { class: "file-tiles" }, live.filter((e) => !isPhoto(e)).map((e) => el("button", {
        class: "file-tile", title: e.file, onclick: () => go("evidence", { kind: "evidence", id: e.id }) },
        el("span", {}, pill((e.file.split(".").pop() || "?").toUpperCase().slice(0, 5), "info")),
        el("span", { class: "nm" }, e.file.split("/").pop()), el("span", { class: "dim small" }, `${e.id} · ${fmtSize(e.size)}`)))),
      live.length ? null : el("div", { class: "empty" }, "файлов пока нет"));
  }
  return el("div", {}, head,
    rows.length ? el("table", { class: "list" },
      el("thead", {}, el("tr", {}, el("th", { style: "width:70px" }, "ID"), el("th", {}, "Файл"),
        el("th", { class: "num", style: "width:110px" }, "Размер"), el("th", { style: "width:28%" }, "Источник"),
        el("th", { class: "right", style: "width:200px" }, "Получено"))),
      el("tbody", {}, rows.map((e) => el("tr", { onclick: () => go("evidence", { kind: "evidence", id: e.id }) },
        el("td", { class: "dim" }, e.id),
        e.deleted ? el("td", { title: e.file }, el("span", { class: "gone" }, e.file), " ", pill("удалён", "err"))
          : el("td", { class: "title", title: e.file }, e.file),
        el("td", { class: "num" }, fmtSize(e.size)),
        el("td", { class: "dim", title: e.source || "" }, e.source || "—"),
        el("td", { class: "right" }, fmtDate(e.time))))))
      : el("div", { class: "empty" }, "файлов пока нет"));
}

function evidencePage() {
  const e = S.data?.evidence.find((x) => x.id === S.view?.id);
  if (!e) return el("div", { class: "empty" }, "файл не найден");
  const used = S.data.findings.filter((f) => (f.evidence || []).includes(e.id));
  return el("div", { class: "wrap" },
    el("button", { class: "link back", onclick: () => go("case") }, "← " + S.current), el("h1", { class: "title", style: "margin-bottom:12px" }, e.file),
    el("dl", { class: "kv" },
      el("dt", {}, "ID"), el("dd", {}, e.id), el("dt", {}, "SHA-256"), el("dd", {}, e.sha256),
      el("dt", {}, "размер"), el("dd", {}, fmtSize(e.size)), el("dt", {}, "получено"), el("dd", {}, fmtDate(e.time)),
      el("dt", {}, "источник"), el("dd", {}, e.source || "—"),
      e.imported_from ? [el("dt", {}, "импорт"), el("dd", {}, e.imported_from)] : null),
    el("div", { class: "ev-grid" }, evidenceCard(e, true)),
    el("h2", { class: "section" }, `Используется в записях (${used.length})`),
    used.length ? findingsTable(used) : el("div", { class: "dim" }, "ни одна запись на него не ссылается"));
}

async function deleteEvidence(e) {
  const ok = await ask({
    title: `удалить ${e.id}: ${e.file}?`,
    text: "Файл будет удалён с диска. В журнале останется запись — ID, SHA-256, размер и отметка об удалении, "
      + "поэтому записи, которые на него ссылаются, не сломаются.",
    okText: "удалить файл",
  });
  if (!ok) return;
  await call("delete_evidence", S.current, e.id);
  toast(`${e.id} удалён`);
  reloadCase();
}

// ---------- фото: плитка и просмотр во весь экран ----------
function isPhoto(e) {
  return !e.deleted && /\.(png|jpe?g|webp|gif)$/i.test(e.file);
}

function photoStrip(photos, cls = "strip") {
  return el("div", { class: "photos " + cls }, photos.map((e, i) => {
    const img = el("img", { alt: e.file });
    const tile = el("button", { class: "thumb", title: e.file, onclick: () => lightbox(photos, i) },
      img, el("span", { class: "cap" }, `${e.id} · ${e.file.split("/").pop()}`));
    call("thumb", S.current, e.file).then((r) => { if (r.ok) img.src = r.src; else tile.classList.add("nothumb"); });
    return tile;
  }));
}

function lightbox(photos, start) {
  let i = start;
  const img = el("img", { class: "lb-img", title: "клик — целиком / по ширине" });
  // длинный скриншот страницы целиком не прочитать — сразу «по ширине» с прокруткой
  img.addEventListener("load", () => {
    img.classList.toggle("wide", img.naturalHeight > img.naturalWidth * 1.6);
    stage.scrollTop = 0;
  });
  img.addEventListener("click", () => img.classList.toggle("wide"));
  const cap = el("div", { class: "lb-cap" });
  const counter = el("span", { class: "dim" });
  const show = async () => {
    const e = photos[i];
    cap.replaceChildren(el("b", {}, `${e.id}  ${e.file}`), "   ", el("span", { class: "dim" }, `${fmtSize(e.size)} · ${e.source || ""}`));
    counter.textContent = `${i + 1} / ${photos.length}`;
    img.removeAttribute("src");
    const r = await call("preview", S.current, e.file);
    if (photos[i] === e) {
      if (r.ok) img.src = r.src;
      else cap.append(el("div", { class: "err" }, "файл больше 8 МБ — откройте через Finder"));
    }
  };
  const step = (d) => { i = (i + d + photos.length) % photos.length; show(); };
  const onKey = (ev) => {
    if (ev.key === "Escape") { ev.stopPropagation(); close(); }
    else if (ev.key === "ArrowRight") step(1);
    else if (ev.key === "ArrowLeft") step(-1);
  };
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey, true); };
  const stage = el("div", { class: "lb-stage" },
    photos.length > 1 ? el("button", { class: "lb-nav", onclick: () => step(-1) }, "‹") : null,
    img,
    photos.length > 1 ? el("button", { class: "lb-nav", onclick: () => step(1) }, "›") : null);
  const back = el("div", { class: "lightbox", onclick: (ev) => { if (ev.target === back || ev.target.classList.contains("lb-stage")) close(); } },
    el("div", { class: "lb-bar" }, counter,
      el("button", { class: "link", onclick: () => call("reveal", S.current, photos[i].file) }, "показать в Finder"),
      el("span", { class: "spacer" }), el("span", { class: "dim small" }, "клик по фото — целиком / по ширине · ← → листать · Esc закрыть"),
      el("button", { class: "link", onclick: close }, "закрыть")),
    stage, cap);
  document.body.append(back);
  document.addEventListener("keydown", onKey, true);
  show();
}

// ---------- ссылки в тексте ----------
const URL_RE = /https?:\/\/[^\s<>"'()\[\]{}]+/gi;

function linkify(line) {
  if (!line) return " ";
  const out = [];
  let last = 0;
  for (const m of line.matchAll(URL_RE)) {
    const url = m[0].replace(/[.,;:!?]+$/, "");
    if (m.index > last) out.push(line.slice(last, m.index));
    out.push(el("span", { class: "url", title: "действия со ссылкой", onclick: () => linkMenu(url) }, url));
    last = m.index + url.length;
  }
  if (last < line.length) out.push(line.slice(last));
  return out;
}

function linkMenu(url) {
  let decoded = url;
  try { decoded = decodeURI(url); } catch (_) { /* битая кодировка — показываем как есть */ }
  const host = (() => { try { return new URL(url).hostname; } catch (_) { return ""; } })();
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey, true); };
  const onKey = (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); close(); } };
  const tor = S.route?.mode && S.route.mode !== "direct";
  const back = el("div", { class: "modal-back", onclick: (ev) => { if (ev.target === back) close(); } },
    el("div", { class: "modal" },
      el("h2", {}, "Ссылка" + (host ? " · " + host : "")),
      el("div", { class: "link-full" }, decoded),
      decoded !== url ? el("div", { class: "dim small link-full" }, url) : null,
      host.startsWith("xn--") || host.includes(".xn--") ? el("div", { class: "warn-box" }, "Домен в punycode — проверьте, не подделка ли это под известный сайт.") : null,
      el("div", { class: "menu" },
        el("button", { class: "btn", onclick: async () => { await call("copy_text", url); toast("ссылка скопирована"); close(); } }, "Скопировать"),
        el("button", { class: "btn", disabled: !S.current, onclick: async () => {
          const job = await call("run", S.current, "web_capture", url, {});
          toast(`#${job.id} снимок страницы — в очереди`); close(); pollJobs();
        } }, "Снять безопасно (скриншот + архив)"),
        el("button", { class: "btn danger", onclick: async () => { await call("open_link", url); close(); } }, "Открыть в браузере")),
      el("div", { class: "dim small" }, "«Снять безопасно» — страницу открывает сама программа"
        + (tor ? " через Tor: сайт не увидит ваш IP." : ".") + " Браузер Safari/Chrome ходит напрямую — сайт увидит ваш адрес, "
        + "если у вас нет VPN.")));
  document.body.append(back);
  document.addEventListener("keydown", onKey, true);
}

// ---------- хронология ----------
function timelinePage() {
  if (!S.current || !S.data) return needCase();
  // события: время получения записей + даты публикаций из поиска по публикациям
  const events = [];
  for (const f of S.data.findings) {
    events.push({ date: (f.time || "").slice(0, 10), time: (f.time || "").slice(11, 16), what: f.title, kind: "получено", f });
    const pubs = f.module === "wp" && f.data && Array.isArray(f.data.latest) ? f.data.latest : [];
    for (const p of pubs) {
      if (p && p.date) events.push({ date: p.date, time: "", what: p.title || "", kind: "публикация", f,
                                     places: p.places ? Object.keys(p.places).join(", ") : "" });
    }
  }
  events.sort((a, b) => (b.date + b.time).localeCompare(a.date + a.time));
  if (!events.length) return el("div", { class: "empty" }, "событий пока нет");
  const out = [];
  let day = null, body = null;
  for (const e of events) {
    if (e.date !== day) {
      day = e.date;
      body = el("tbody");
      out.push(el("h2", { class: "section", style: "margin:" + (out.length ? "22px" : "0") + " 0 10px" }, fmtDate(day) || "без даты"),
        el("table", { class: "list" }, body));
    }
    body.append(el("tr", { onclick: () => go("paste", { kind: "finding", id: e.f.id }) },
      el("td", { class: "muted", style: "width:84px" }, e.time ? e.time + " UTC" : "—"),
      el("td", { style: "width:130px" }, pill(e.kind, e.kind === "публикация" ? "info" : "neutral")),
      el("td", { class: "title", title: e.what }, e.what + (e.places ? "  · " + e.places : "")),
      el("td", { class: "right", style: "width:170px" }, el("span", { class: "mono muted" }, e.f.id + " "), findingPill(e.f))));
  }
  return el("div", {}, out);
}

// ---------- дела ----------
const EXAMPLES = ["проанализируй сайт example.com и дай отчёт", "что за IP 93.184.216.34", "кто владелец домена example.org",
  "сними страницу example.net как доказательство"];

function casesPage() {
  const fresh = !S.chat.msgs.length;
  return el("div", { class: "wrap narrow" },
    fresh ? el("div", { class: "hero" }, logoArt(), el("h1", { class: "hero-title" }, "Что проверить?"),
      el("div", { class: "hero-sub" }, "Напишите обычным текстом или перетащите фото в окно — программа сама заведёт дело, запустит проверки "
        + "и пришлёт отчёт. Перед запуском покажет, что и куда уйдёт."),
      el("div", { class: "examples" }, EXAMPLES.map((x) => el("button", { class: "example", onclick: () => {
        S.chat.draft = x; const t = $("#chat-in"); if (t) { t.value = x; t.focus(); } } }, x)))) : null,
    chatPanel(),
    fresh ? el("div", { id: "case-list" }, ...caseList()) : null);
}

function caseList() {
  const info = new Map(S.casesInfo.map((c) => [c.name, c]));
  const recent = S.cases.slice().sort((a, b) => String(info.get(b)?.updated || "").localeCompare(String(info.get(a)?.updated || ""))).slice(0, 6);
  return [
    el("h2", { class: "section" }, "Недавние дела", el("span", { class: "spacer" }),
      el("button", { class: "btn ghost small", onclick: importCase }, "Импорт дела…")),
    recent.length ? el("div", { class: "case-grid" }, recent.map((name) => {
      const c = info.get(name) || {};
      return el("button", { class: "card case-card", onclick: () => openCase(name) },
        el("span", { class: "nm", title: name }, name),
        el("div", { class: "row" }, pill(`${c.findings ?? 0} зап.`, "primary"), pill(`${c.evidence ?? 0} файл.`),
          c.hypotheses ? pill(`${c.hypotheses} гип.`, "warn") : null),
        el("span", { class: "muted small" }, c.updated ? "обновлено " + fmtDate(c.updated) : "пустое"));
    })) : el("div", { class: "empty" }, "дел пока нет — напишите, что проверить"),
    el("form", { class: "inline-form", style: "margin-top:14px", onsubmit: createCase },
      el("input", { id: "new-case-name", placeholder: "или создайте пустое дело: название", maxlength: "60", autocomplete: "off", spellcheck: "false" }),
      el("button", { class: "btn ghost", type: "submit" }, "Создать"))];
}

function renderCaseList() {
  $("#case-list")?.replaceChildren(...caseList());
  renderSide();
}

async function importCase() {
  const p = await call("pick_file");
  if (!p) return;
  const r = await call("import_case", p);
  applyState(r);
  S.casesInfo = await call("cases_info");
  toast(`импортировано в дело «${r.case}»: записей ${r.findings ?? "?"}, файлов ${r.restored ?? r.evidence ?? "?"}`);
  openCase(r.case);
}

async function createCase(ev) {
  ev.preventDefault();
  const r = await call("create_case", $("#new-case-name").value);
  applyState(r);
  S.casesInfo = await call("cases_info");
  openCase(r.name);
}

async function exportCase() {
  const r = await call("export", S.current);
  toast(`экспорт: записей ${r.findings}, файлов ${r.evidence} — открыт в Finder`);
}

async function deleteCase(name) {
  if (!(await leaveEditor())) return;
  const typed = await ask({
    title: `удалить дело ${name}?`,
    text: "Будут удалены все записи, файлы-доказательства и заметки дела. Вернуть нельзя — "
      + "если нужна копия, сначала сделайте экспорт.",
    okText: "удалить дело", requireText: name,
  });
  if (!typed) return;
  const st = await call("delete_case", name, typed);
  if (S.current === name) { S.current = null; S.data = null; S.paste = freshPaste(); S.page = "cases"; }
  S.casesInfo = await call("cases_info");
  applyState(st);
  render(true);
  toast(`дело ${name} удалено`);
}

// ---------- новая проверка ----------
// «Всё подходящее»: тип запроса определяет движок (api.plan), список проверок виден до запуска
const AUTO_TOOL = { id: "auto", file: true, placeholder: "ссылка, домен, IP, e-mail, телефон, ник, имя, файл или текст",
  hint: "все подходящие проверки сразу, до трёх одновременно; поиск по нику и перевод — только вручную" };

function currentTool() {
  const id = $("#tool")?.value;
  return id === "auto" ? AUTO_TOOL : S.tools.find((t) => t.id === id) || S.tools[0];
}

function runBar() {
  const groups = new Map();
  for (const t of S.tools) {
    if (!groups.has(t.group)) groups.set(t.group, el("optgroup", { label: t.group }));
    groups.get(t.group).append(el("option", { value: t.id, disabled: t.available === false, title: t.needs_label || null },
      t.label + (t.available === false ? " — " + t.needs_label : "")));
  }
  const auto = el("optgroup", { label: "Авто" }, el("option", { value: "auto", selected: true }, "Всё подходящее"));
  return el("div", { class: "runbar" },
    el("form", { id: "runform", class: "form-row", onsubmit: runTool },
      el("select", { id: "tool", onchange: renderToolFields }, auto, ...groups.values()),
      el("input", { id: "target", autocomplete: "off", spellcheck: "false", oninput: schedulePlan }),
      el("button", { type: "button", id: "pick", class: "btn ghost", hidden: true, onclick: pickFile }, "Файл…"),
      el("button", { type: "submit", class: "btn", disabled: !S.current }, "Запустить")),
    el("div", { id: "extras", class: "form-row" }),
    el("div", { id: "hint", class: "hint" }),
    el("div", { id: "egress" }),
    el("div", { id: "batch", class: "batch" }));
}

let planTimer = null;
function schedulePlan() {
  if (currentTool().id !== "auto") return;
  clearTimeout(planTimer);
  planTimer = setTimeout(refreshPlan, 250);
}

async function refreshPlan() {
  const target = $("#target")?.value.trim();
  S.plan = target ? await api.plan(target).catch(() => null) : null;
  if ($("#egress") && currentTool().id === "auto") renderEgress(AUTO_TOOL);
}

const ROUTE_NOTE = {
  tor: "через Tor — получатели видят адрес выхода Tor, не ваш",
  proxy: "через прокси — получатели видят адрес прокси",
  direct: "напрямую — получатели видят ваш IP",
};

function renderEgress(tool) {
  if (tool.id === "auto") {
    if (!S.plan) return $("#egress").replaceChildren();
    return $("#egress").replaceChildren(
      el("div", { class: "hint" }, "Похоже на: ", el("b", {}, S.plan.kind_label), " → ", S.plan.steps.map((st) => st.label).join(", ")),
      egressList(S.plan.egress));
  }
  if (!tool.egress) return $("#egress").replaceChildren();
  $("#egress").replaceChildren(egressList(tool.egress));
}

// куда уйдёт запрос: свёрнуто в одну строку, раскрывается по клику; при прямом подключении — предупреждение на виду
function egressList(eg) {
  if (eg.local) return el("div", { class: "ok-line small" }, "✓ Локально: ничего не отправляется");
  const mode = S.route?.mode || eg.route;  // маршрут могли переключить после загрузки списка
  return el("details", { class: "egress" },
    el("summary", {}, `Куда уйдёт запрос: ${eg.items.length} ${plural(eg.items.length, "получатель", "получателя", "получателей")} · `,
      el("span", { class: mode === "direct" ? "eg-warn" : "" }, ROUTE_NOTE[mode] || mode)),
    el("ul", {}, ...eg.items.map((i) => el("li", {}, i))));
}

function renderToolFields() {
  const tool = currentTool();
  if (!tool || !$("#target")) return;
  if (tool.id === "auto") refreshPlan();
  $("#target").placeholder = tool.placeholder || "";
  $("#hint").textContent = tool.hint || "";
  renderEgress(tool);
  $("#pick").hidden = !tool.file;
  const extras = [];
  for (const f of tool.extra || []) {
    if (f.choices) {
      extras.push(el("label", {}, f.label,
        el("select", { "data-opt": f.name }, ...f.choices.map((c) => el("option", { value: c, selected: c === f.default }, c)))));
    } else {
      extras.push(el("label", {}, f.label, el("input", { type: "text", "data-opt": f.name, value: f.default || "", spellcheck: "false" })));
    }
  }
  for (const f of tool.flags || []) {
    extras.push(el("label", {}, el("input", { type: "checkbox", "data-flag": f.name }), f.label));
  }
  $("#extras").replaceChildren(...extras);
}

async function pickFile() {
  const p = await call("pick_file");
  if (p) { $("#target").value = p; schedulePlan(); }
}

async function runTool(ev) {
  ev.preventDefault();
  if (!S.current) return toast("Сначала выберите или создайте дело", true);
  if (currentTool().id === "auto") {
    const res = await call("run_all", S.current, $("#target").value);
    toast(`общий поиск (${res.kind_label}): ${res.total} проверок в очереди`);
    $("#target").value = "";
    S.plan = null;
    renderEgress(AUTO_TOOL);
    return pollJobs();
  }
  const options = {};
  for (const n of document.querySelectorAll("#extras [data-opt]")) options[n.dataset.opt] = n.value;
  for (const n of document.querySelectorAll("#extras [data-flag]")) options[n.dataset.flag] = n.checked;
  const job = await call("run", S.current, currentTool().id, $("#target").value, options);
  toast(`#${job.id} ${job.tool}: в очереди`);
  $("#target").value = "";
  pollJobs();
}

function renderRecent() {
  const box = $("#recent");
  const mine = S.jobs.filter((j) => j.case === S.current).slice(0, 4);  // последние проверки этого дела
  if (box) box.replaceChildren(mine.length ? jobsTable(mine) : "");
  renderBatch();
}

// последний общий поиск по делу: «проверено X из Y» и «Остановить всё»
function renderBatch() {
  const box = $("#batch");
  if (!box) return;
  const mine = S.jobs.filter((j) => j.case === S.current && j.batch);
  const last = Math.max(0, ...mine.map((j) => j.batch));
  const jobs = mine.filter((j) => j.batch === last);
  if (!jobs.length) return box.replaceChildren();
  const active = jobs.filter((j) => ACTIVE.includes(j.status)).length;
  const errors = jobs.filter((j) => j.status === "ошибка").length;
  box.replaceChildren(
    pill(active ? "идёт" : "завершено", active ? "warn" : "ok"),
    el("span", {}, `«${jobs[0].target}»: проверено ${jobs.length - active} из ${jobs.length}`),
    errors ? pill(`ошибок ${errors}`, "err") : "",
    active ? el("button", { class: "btn danger small", onclick: () => call("cancel_batch", last).then(pollJobs) }, "Остановить всё") : "");
}

// ---------- очередь ----------
const STATUS_CLASS = { "выполняется": "", "в очереди": "", "готово": "ok", "ошибка": "err", "отменено": "muted" };

async function pollJobs() {
  S.jobs = await call("list_jobs");
  let reload = false;
  for (const j of S.jobs) {
    if (!ACTIVE.includes(j.status) && !S.seenDone.has(j.id)) {
      S.seenDone.add(j.id);
      if (j.case === S.current) reload = true;
      if (j.status === "ошибка") toast(`#${j.id} ${j.tool}: ${j.error || "ошибка"}`, true);
      else if (j.status === "готово") toast(`#${j.id} ${j.tool}: готово`);
    }
  }
  const active = S.jobs.filter((j) => ACTIVE.includes(j.status)).length;
  $("#q-count").textContent = active ? String(active) : "";
  $("#q-count").className = "pill q-count" + (active ? " warn" : "");
  if (S.assist && !S.aiBusy) {
    if (assistJobs().length && !assistJobs().some((j) => ACTIVE.includes(j.status))) finishAssist();
    else if ($("#assist-status")) $("#assist-status").replaceChildren(...[assistStatusView()].flat().filter(Boolean));
    if (S.assist?.chat) renderChatLog();
  }
  if (reload) reloadCase();
  else if (S.page === "queue") render();
  else if (S.page === "case") renderRecent();
}

function jobsTable(jobs) {
  return el("table", { class: "list" },
    el("thead", {}, el("tr", {}, el("th", { style: "width:60px" }, "#"), el("th", { style: "width:150px" }, "Дело"),
      el("th", { style: "width:190px" }, "Проверка"), el("th", {}, "Запрос"), el("th", { style: "width:30%" }, "Статус"),
      el("th", { class: "right", style: "width:90px" }, "Время"), el("th", { class: "right", style: "width:70px" }, ""))),
    el("tbody", {}, jobs.map((j) => el("tr", { onclick: () => { if (j.case !== S.current) openCase(j.case); else go("case"); } },
      el("td", { class: "dim" }, "#" + j.id), el("td", { title: j.case }, j.case), el("td", {}, j.tool),
      el("td", { class: "title", title: j.target }, j.target),
      el("td", { title: j.error || "" }, pill(j.status, JOB_PILL[j.status] || "neutral"), j.error ? el("span", { class: "muted small" }, "  " + j.error) : ""),
      el("td", { class: "right muted" }, j.finished || j.started || ""),
      el("td", { class: "right" }, ACTIVE.includes(j.status)
        ? el("button", { class: "link danger", onclick: (ev) => { ev.stopPropagation(); call("cancel", j.id).then(pollJobs); } }, "стоп") : "")))));
}

function queuePage() {
  const active = S.jobs.filter((j) => ACTIVE.includes(j.status)).length;
  return el("div", { class: "wrap" }, el("h1", { class: "title" }, "Очередь проверок"),
    el("div", { class: "row", style: "margin:10px 0 18px" }, pill(`активных ${active}`, active ? "warn" : "neutral"), pill(`всего ${S.jobs.length}`),
      el("span", { class: "muted small" }, "до трёх проверок одновременно · список живёт только пока открыто окно")),
    S.jobs.length ? jobsTable(S.jobs) : el("div", { class: "empty" }, "проверок ещё не было"));
}

// ---------- локальный ИИ ----------
function aiPanel() {
  if (S.ai === null) { S.ai = {}; call("ai_status").then((r) => { S.ai = r; if (S.tab === "ai") render(); }); }
  const st = S.ai || {};
  const q = el("textarea", { id: "ai-q", class: "ai-q", spellcheck: "false",
    placeholder: "Например: проанализируй сайт example.com и дай отчёт\nили: составь хронологию по записям дела" });
  q.value = S.aiDraft;
  q.addEventListener("input", () => { S.aiDraft = q.value; scheduleAssistPlan(); });
  if (S.aPlan === null && S.aiDraft.trim()) scheduleAssistPlan();
  const busy = S.aiBusy || (S.assist && S.assist.case === S.current);
  const notes = (S.data.notes || []).filter((n) => n.name.startsWith("ИИ_")).slice().reverse();
  return el("div", { class: "ai" },
    el("div", { class: "muted small" }, st.running === undefined ? "проверяю локальную модель…"
      : !st.running ? "Ollama не запущена — запустится сама при первом вопросе"
        : st.ready ? `модель ${st.model} · работает только на этом компьютере, материалы дела никуда не уходят`
          : `модель ${st.model} ещё не скачана: ollama pull ${st.model}`),
    q,
    el("div", { id: "assist-plan", class: "hint" }, assistPlanView()),
    el("div", { class: "form-row" },
      el("button", { id: "assist-btn", class: "btn", disabled: busy, onclick: assist },
        S.aiBusy ? "пишет отчёт…" : busy ? "идут проверки…" : assistLabel()),
      attachButton(),
      el("span", { class: "muted small" }, "ответ — гипотезы ИИ, не доказательство; сохраняется заметкой")),
    el("div", { id: "assist-status", class: "batch small" }, assistStatusView()),
    S.aiLast ? el("div", { class: "card ai-answer" }, answerView(S.aiLast.answer)) : null,
    notes.length ? [el("h2", { class: "section" }, "Прошлые ответы", el("span", { class: "muted" }, String(notes.length))),
      el("div", { class: "notes-row" }, notes.map((n) => el("button", { class: "note-chip", onclick: () => go("paste", { kind: "note", id: n.name }) },
        pill("ИИ", "info"), el("span", { class: "nm" }, fmtDate(n.modified)))))] : null);
}

let aPlanTimer = null;
function scheduleAssistPlan() {
  clearTimeout(aPlanTimer);
  aPlanTimer = setTimeout(async () => {
    const text = S.aiDraft.trim();
    try { S.aPlan = text ? await api.assistant_plan(text) : null; }
    catch (e) { S.aPlan = { error: (e && e.message) || String(e), targets: [] }; }
    if ($("#assist-plan")) {
      $("#assist-plan").replaceChildren(...[assistPlanView()].flat().filter(Boolean));
      if (!S.aiBusy && !S.assist) $("#assist-btn").textContent = assistLabel();  // без render: поле ввода не теряет фокус
    }
  }, 300);
}

// что ассистент сделает — видно до запуска (ТЗ: показывать, что уйдёт провайдерам)
function assistPlanView() {
  const p = S.aPlan;
  if (!p) return null;
  if (p.error) return el("div", { class: "err" }, p.error);
  if (!p.targets.length) return el("div", { class: "ok-line small" }, "✓ Целей в тексте нет — ИИ ответит по материалам дела, ничего не отправляется");
  return [el("div", {}, "Будет проверено:"),
    el("ul", {}, ...p.targets.map((t) => el("li", {}, el("b", {}, t.target), ` (${t.kind_label}) → ${t.steps.map((s) => s.label).join(", ")}`))),
    egressList(p.egress),
    el("div", {}, "Потом локальный ИИ составит отчёт по результатам.")];
}

function assistLabel() {
  return S.aPlan?.targets?.length ? "Проверить и составить отчёт" : "Спросить по материалам дела";
}

function assistJobs() {
  return S.assist ? S.jobs.filter((j) => j.batch === S.assist.batch) : [];
}

function assistStatusView() {
  if (!S.assist || S.assist.case !== S.current) return null;
  const jobs = assistJobs();
  const active = jobs.filter((j) => ACTIVE.includes(j.status)).length;
  return [pill(active ? "идут проверки" : "пишу отчёт…", "warn"), el("span", {}, `проверено ${jobs.length - active} из ${S.assist.total}`),
    active ? el("button", { class: "btn danger small", onclick: () => call("cancel_batch", S.assist.batch).then(pollJobs) }, "Остановить всё") : null];
}

async function assist() {
  const text = S.aiDraft.trim();
  if (!text || S.aiBusy || S.assist) return;
  if (!S.aPlan?.targets?.length) return askAi();
  const res = await call("assistant_run", S.current, text);
  S.assist = { batch: res.batch, total: res.total, text, case: S.current };
  render();
  pollJobs();
}

// все проверки пачки завершились — ИИ пишет отчёт по материалам дела
async function finishAssist() {
  const a = S.assist;
  const jobs = assistJobs().map((j) => ({ tool: j.tool, target: j.target, status: j.status, error: j.error }));
  S.aiBusy = true;
  if (a.chat) renderChatLog();
  else if (S.current === a.case) render();
  try {
    const res = await call("assistant_report", a.case, a.text, jobs);
    if (a.chat) S.chat.msgs.push({ role: "bot", kind: "answer", text: res.answer, case: a.case });
    else {
      S.aiLast = res;
      S.aiDraft = "";  // просьба выполнена — план больше не нужен, на виду остаётся отчёт
      S.aPlan = null;
    }
    toast("отчёт готов");
  } catch (e) {
    if (a.chat) S.chat.msgs.push({ role: "bot", kind: "error", text: "Отчёт не составлен: " + ((e && e.message) || e) });
  } finally {
    S.assist = null;
    S.aiBusy = false;
    if (a.chat) { renderChatLog(); if (S.page === "cases") { S.casesInfo = await call("cases_info"); renderCaseList(); } }
    if (S.current === a.case) {
      S.data = await call("case", a.case); S.ai = null; render();
      $(".ai-answer")?.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }
}

async function askAi() {
  const question = S.aiDraft.trim();
  if (!question || S.aiBusy) return;
  S.aiBusy = true;
  render();
  try {
    S.aiLast = await call("ai_ask", S.current, question);
    S.data = await call("case", S.current);
    S.ai = null;
  } finally {
    S.aiBusy = false;
    render();
  }
}

// ---------- диалог на главной: просьба → новое дело → проверки → отчёт ----------
const CHAT_HINT = "Например: проанализируй сайт example.com и дай отчёт";

function chatPanel() {
  const input = el("textarea", { id: "chat-in", class: "chat-in", rows: 2, spellcheck: "false",
    placeholder: S.chat.case ? `Продолжение дела ${S.chat.case}: новая цель или вопрос по материалам` : CHAT_HINT });
  input.value = S.chat.draft;
  input.addEventListener("input", () => { S.chat.draft = input.value; });
  input.addEventListener("keydown", (ev) => {  // Enter — отправить, Shift+Enter — новая строка
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); sendChat(); }
  });
  return el("div", { class: "chat" },
    S.chat.msgs.length ? el("div", { class: "row" }, el("span", { class: "muted small" },
      S.chat.case ? `Диалог по делу «${S.chat.case}»` : "Диалог: дело ещё не заведено"), el("span", { class: "spacer" }),
      el("button", { class: "btn ghost small", onclick: newChat }, "Новый диалог")) : null,
    el("div", { id: "chat-log", class: "chat-log" }, ...chatLog()),
    el("div", { class: "chat-bar" }, input, attachButton(), el("button", { class: "btn", onclick: sendChat }, "Отправить")),
    el("div", { class: "chat-foot" }, "Enter — отправить, Shift+Enter — новая строка · фото и файлы можно перетащить в окно · "
      + "ИИ работает только на этом компьютере, его ответы — гипотезы"));
}

// отчёт: разделы (КОРОТКО, ФАКТЫ…) — заголовками, ссылки кликабельны
function answerView(text) {
  return el("div", { class: "answer" }, String(text || "").split("\n").map((line) =>
    /^[А-ЯЁA-Z][А-ЯЁA-Z «»—-]{3,}(\(.*\))?:?$/.test(line.trim()) || /^#{1,4} /.test(line)
      ? el("div", { class: "h" }, line.replace(/^#{1,4} /, ""))
      : el("div", {}, linkify(line) || "\u00a0")));
}

function chatLog() {
  return S.chat.msgs.map((m, i) => m.role === "user"
    ? el("div", { class: "msg me" }, el("div", { class: "bubble" }, m.text))
    : el("div", { class: "msg bot" + (m.kind === "error" ? " err" : "") }, el("div", { class: "bubble" }, ...botBody(m, i))));
}

function renderChatLog() {
  const log = $("#chat-log");
  if (!log) return;
  log.replaceChildren(...chatLog());
  log.lastElementChild?.scrollIntoView({ block: "end" });
}

function botBody(m, i) {
  if (m.kind === "plan") {
    const p = m.plan;
    return [el("div", {}, m.case ? `Продолжу дело «${m.case}». Будет проверено:` : "Заведу новое дело. Будет проверено:"),
      el("ul", {}, ...p.targets.map((t) => el("li", {}, el("b", {}, t.target), ` (${t.kind_label}) → ${t.steps.map((st) => st.label).join(", ")}`))),
      egressList(p.egress),
      m.state === "wait" ? el("div", { class: "row" },
        el("button", { class: "btn", disabled: !!S.assist, onclick: () => startChat(i) }, S.assist ? "идёт другая проверка…" : "Начать"),
        el("button", { class: "btn ghost", onclick: () => { m.state = "cancelled"; renderChatLog(); } }, "Отмена"))
        : el("div", { class: "row" }, pill(m.state === "started" ? "запущено" : "отменено", m.state === "started" ? "ok" : "neutral"))];
  }
  if (m.kind === "progress") {
    const jobs = S.jobs.filter((j) => j.batch === m.batch);
    const active = jobs.filter((j) => ACTIVE.includes(j.status)).length;
    const live = S.assist?.batch === m.batch;
    return [el("div", { class: "row" }, pill(active ? "идут проверки" : live ? "пишу отчёт…" : "готово", active || live ? "warn" : "ok"),
        el("span", {}, `дело «${m.case}»: проверено ${jobs.length - active} из ${m.total}`)),
      el("ul", {}, ...jobs.slice().reverse().map((j) => el("li", {}, pill(j.status, JOB_PILL[j.status] || "neutral"), ` ${j.tool} · ${j.target}`,
        j.error ? el("span", { class: "muted" }, " — " + j.error) : ""))),
      el("div", { class: "row" },
        live && active ? el("button", { class: "btn danger small", onclick: () => call("cancel_batch", m.batch).then(pollJobs) }, "Остановить всё") : null,
        el("button", { class: "btn ghost small", onclick: () => openCase(m.case) }, "Открыть дело"))];
  }
  if (m.kind === "answer") {
    return [answerView(m.text),
      m.case ? el("div", { class: "row" }, el("button", { class: "btn ghost small", onclick: () => openCase(m.case) }, `Открыть дело ${m.case}`)) : null];
  }
  if (m.kind === "thinking") return [pill("думаю…", "warn")];
  return [m.text];
}

async function sendChat() {
  const text = S.chat.draft.trim();
  if (!text || S.chat.msgs.some((m) => m.kind === "thinking")) return;
  S.chat.draft = "";
  $("#chat-in").value = "";
  const first = !S.chat.msgs.length;
  S.chat.msgs.push({ role: "user", text });
  if (first) { render(); $("#chat-in")?.focus(); } else renderChatLog();  // первый ответ убирает большой логотип
  let plan;
  try { plan = await api.assistant_plan(text); }
  catch (e) { S.chat.msgs.push({ role: "bot", kind: "error", text: (e && e.message) || String(e) }); return renderChatLog(); }
  if (plan.targets.length) {
    S.chat.msgs.push({ role: "bot", kind: "plan", plan, text, case: S.chat.case, state: "wait" });
  } else if (S.chat.case) {  // без целей — вопрос по материалам начатого дела
    const wait = { role: "bot", kind: "thinking" };
    S.chat.msgs.push(wait);
    renderChatLog();
    try {
      const res = await api.ai_ask(S.chat.case, text);
      Object.assign(wait, { kind: "answer", text: res.answer, case: S.chat.case });
    } catch (e) { Object.assign(wait, { kind: "error", text: (e && e.message) || String(e) }); }
  } else {
    S.chat.msgs.push({ role: "bot", kind: "info",
      text: "Назовите, что проверить: сайт, домен, IP-адрес или e-mail — с этого начнётся новое дело. " + CHAT_HINT });
  }
  renderChatLog();
}

async function startChat(i) {
  const m = S.chat.msgs[i];
  if (S.assist || m.state !== "wait") return;
  m.state = "started";
  try {
    const res = await api.assistant_start(m.text, m.case || null);
    S.chat.case = res.case;
    S.assist = { batch: res.batch, total: res.total, text: m.text, case: res.case, chat: true };
    S.chat.msgs.push({ role: "bot", kind: "progress", batch: res.batch, total: res.total, case: res.case });
    applyState(await call("state"));
    S.casesInfo = await call("cases_info");
    renderCaseList();
    $("#chat-in").placeholder = `Продолжение дела ${res.case}: новая цель или вопрос по материалам`;
  } catch (e) {
    m.state = "wait";
    S.chat.msgs.push({ role: "bot", kind: "error", text: (e && e.message) || String(e) });
  }
  renderChatLog();
  pollJobs();
}

function newChat() {
  if (S.assist?.chat) return toast("Идут проверки диалога — дождитесь отчёта или остановите их", true);
  S.chat = { msgs: [], case: null, draft: "" };
  render();
}

// ---------- маршрут в интернет ----------
async function refreshRoute() {
  if (!api || S.vault === "закрыто") return;
  S.route = await api.route_status().catch(() => null);
  const r = S.route, b = $("#route");
  if (!r) return;
  const [text, cls] = r.mode === "direct" ? ["⚠ Напрямую — ваш IP виден", "route bad"]
    : r.up ? [r.mode === "tor" ? "● Через Tor" : "● Через прокси", "route ok"] : [r.mode === "tor" ? "● Tor выключен" : "● Прокси недоступен", "route bad"];
  b.textContent = text;
  b.className = cls;
  if ($("#egress")) renderEgress(currentTool());
}

function routeMenu() {
  const r = S.route || {};
  const out = el("div", { class: "link-full" }, r.mode === "direct" ? "Сейчас напрямую: сайты видят ваш IP."
    : r.up ? `Проверки идут через ${r.mode === "tor" ? "Tor" : "прокси"} (${r.proxy}).`
      : "Tor не запущен — проверки не выполняются, пока он не заработает.");
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey, true); };
  const onKey = (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); close(); } };
  const after = async () => { await refreshRoute(); close(); routeMenu(); };
  const back = el("div", { class: "modal-back", onclick: (ev) => { if (ev.target === back) close(); } },
    el("div", { class: "modal" },
      el("h2", {}, "Маршрут в интернет"), out,
      el("div", { class: "menu" },
        r.mode !== "direct" ? el("button", { class: "btn", onclick: async () => {
          out.textContent = "проверяю через check.torproject.org…";
          const res = await call("route_check");
          out.textContent = res["через_tor"] ? `Через Tor ✓ · сайты видят адрес ${res["видимый_ip"]}, а не ваш` : "Внимание: запросы идут НЕ через Tor";
        } }, "Проверить") : null,
        r.mode === "tor" && !r.up ? el("button", { class: "btn", onclick: async () => {
          out.textContent = "запускаю Tor (до минуты)…";
          await call("route_start"); after();
        } }, "Запустить Tor") : null,
        r.mode === "direct" ? el("button", { class: "btn", onclick: async () => { await call("route_set", "tor"); after(); } }, "Включить Tor")
          : el("button", { class: "btn danger", onclick: async () => { await call("route_set", "direct"); after(); } }, "Отключить (напрямую)")),
      el("div", { class: "dim small" }, "Tor скрывает ваш IP от сайтов. Он не защищает, если входить в свои аккаунты или "
        + "открывать ссылки в обычном браузере.")));
  document.body.append(back);
  document.addEventListener("keydown", onKey, true);
}

// ---------- настройки: маршрут, прямые повторы, ключи API, хранилище ----------
function settingsPage() {
  const st = S.settings || {};
  const r = S.route || {};
  const reload = async () => { S.settings = await call("settings"); applyState(await call("state")); render(true); };
  const keys = Object.entries(st.keys || {}).map(([name, set]) => {
    const input = el("input", { type: "password", placeholder: set ? "задан — введите новый, чтобы заменить" : "не задан",
      autocomplete: "off", spellcheck: "false" });
    return el("div", { class: "key-row" }, el("b", {}, st.key_labels?.[name] || name), pill(set ? "задан" : "нет", set ? "ok" : "neutral"), input,
      el("button", { class: "btn ghost small", onclick: async () => {
        if (!input.value.trim()) return toast("Введите ключ", true);
        await call("set_key", name, input.value); input.value = ""; toast("ключ сохранён"); reload(); } }, "Сохранить"),
      set ? el("button", { class: "link danger", onclick: async () => { await call("set_key", name, ""); toast("ключ удалён"); reload(); } }, "удалить") : el("span"));
  });
  return el("div", { class: "wrap narrow settings" }, el("h1", { class: "title" }, "Настройки"),
    el("div", { class: "card" }, el("h3", {}, "Маршрут в интернет"),
      el("div", { class: "row" }, pill(r.mode === "direct" ? "напрямую" : r.mode === "tor" ? "Tor" : "прокси", r.mode === "direct" ? "err" : "ok"),
        el("span", {}, r.mode === "direct" ? "сайты и сервисы видят ваш IP" : r.up ? "работает — сайты видят адрес выхода, а не ваш" : "не запущен — проверки не выполняются")),
      el("div", { class: "row" }, el("button", { class: "btn ghost", onclick: routeMenu }, "Проверить / переключить…"))),
    el("div", { class: "card" }, el("h3", {}, "Сервисы, которые блокируют Tor"),
      el("label", { class: "row" }, el("input", { type: "checkbox", checked: !!st.direct_fallback, onchange: async (ev) => {
        await call("set_direct_fallback", ev.target.checked); toast("сохранено"); reload(); } }),
        "Повторять запрос напрямую, если ipinfo.io или Shodan InternetDB отказали через Tor"),
      el("div", { class: "muted small" }, "Повтор видит только этот справочный сервис: ваш IP и проверяемый адрес. Сама цель проверки — никогда. "
        + "Выключите, если напрямую не должно уходить ничего.")),
    el("div", { class: "card" }, el("h3", {}, "Ключи сервисов"),
      el("div", { class: "muted small" }, "Необязательны: без ключа соответствующие проверки просто недоступны. Ключи хранятся в config.toml "
        + "(только для вашей учётной записи) и в окно не возвращаются."), ...keys),
    el("div", { class: "card" }, el("h3", {}, "Хранилище и ИИ"),
      el("div", {}, st.encrypt ? "Дела хранятся в зашифрованном образе (AES-256), пароль — в Связке ключей." : "Шифрование дел выключено."),
      el("div", {}, `Локальный ИИ: модель ${st.ai_model || "—"} (Ollama на этом компьютере).`)));
}

// ---------- окно подтверждения; requireText — нужно ввести этот текст (название дела) ----------
function ask({ title, text, okText = "удалить", requireText = null }) {
  return new Promise((resolve) => {
    const input = requireText ? el("input", { placeholder: requireText, spellcheck: "false", autocomplete: "off" }) : null;
    const ok = el("button", { class: "btn danger", disabled: !!requireText }, okText);
    const onKey = (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); close(false); } };
    const close = (v) => { back.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
    const matches = () => input.value.trim() === requireText;
    if (input) {
      input.addEventListener("input", () => { ok.disabled = !matches(); });
      input.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && matches()) close(input.value.trim()); });
    }
    ok.addEventListener("click", () => close(requireText ? input.value.trim() : true));
    const back = el("div", { class: "modal-back", onclick: (ev) => { if (ev.target === back) close(false); } },
      el("div", { class: "modal" },
        el("h2", {}, title),
        el("div", {}, text),
        input ? [el("div", { class: "dim" }, "для подтверждения введите название дела:"), input] : null,
        el("div", { class: "modal-actions" }, el("button", { class: "link", onclick: () => close(false) }, "отмена"), ok)));
    document.body.append(back);
    document.addEventListener("keydown", onKey, true);
    (input || ok).focus();
  });
}

// ---------- формат ----------
const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
function fmtDate(t) {
  const s = String(t || "");
  if (s.length < 10) return s || "—";
  const d = `${+s.slice(8, 10)} ${MONTHS[+s.slice(5, 7) - 1] || "?"} ${s.slice(0, 4)}`;
  return s.length >= 16 ? `${d}, ${s.slice(11, 16)}` : d;
}
function fmtSize(n) {
  if (n == null) return "—";
  if (n < 1024) return n + " Б";
  if (n < 1048576) return (n / 1024).toFixed(1) + " КБ";
  return (n / 1048576).toFixed(1) + " МБ";
}

// ---------- запуск ----------
// ---------- файл: перетащен в окно или выбран кнопкой ----------
// полный путь знает только Python (pywebview), страница получает его через window.fileDropped
async function useFile(path) {
  if (!path || S.vault === "закрыто") return;
  if (S.page === "case" && S.current && S.tab === "checks" && $("#target")) {
    $("#target").value = path;
    return schedulePlan();
  }
  if (S.page === "case" && S.current) {  // в деле — в просьбу ассистенту, план покажется до запуска
    S.aiDraft = S.aiDraft.trim() ? S.aiDraft.trim() + "\n" + path : path;
    S.aPlan = null;
    if (S.tab !== "ai") openTab("ai"); else { $("#ai-q").value = S.aiDraft; scheduleAssistPlan(); }
    return;
  }
  if (S.page !== "cases") await go("cases");
  S.chat.draft = path;
  sendChat();  // покажет, что будет проверено, и спросит «Начать»
}

async function attachFile() {
  useFile(await call("pick_file"));
}

function attachButton() {
  return el("button", { type: "button", class: "btn ghost", title: "фото, видео или другой файл — можно просто перетащить в окно",
    onclick: attachFile }, "Файл…");
}

let dragDepth = 0;
function dropHint(on) {
  dragDepth = on ? dragDepth + 1 : on === false ? Math.max(0, dragDepth - 1) : 0;
  document.body.classList.toggle("dropping", dragDepth > 0);
}
window.fileDropped = (path) => { dropHint(null); useFile(path); };

async function init() {
  if (api) return;
  api = window.pywebview.api;
  for (const holder of document.querySelectorAll("[data-logo]")) holder.append(logoArt());
  for (const holder of document.querySelectorAll("[data-logo-mark]")) holder.append(logoArt().firstElementChild || "");
  $("#case-filter").addEventListener("input", (ev) => { S.sideFilter = ev.target.value; renderSide(); });
  $("#unlock").addEventListener("click", async () => {
    $("#lock-error").textContent = "";
    try {
      applyState(await api.unlock());
      go("cases");
    } catch (e) { $("#lock-error").textContent = (e && e.message) || String(e); }
  });
  $("#btn-lock").addEventListener("click", async () => {
    if (await leaveEditor()) applyState(await call("lock"));
  });
  for (const b of document.querySelectorAll("[data-page]")) b.addEventListener("click", () => go(b.dataset.page));
  const files = (ev) => [...(ev.dataTransfer?.types || [])].includes("Files");
  document.addEventListener("dragenter", (ev) => { if (files(ev)) dropHint(true); });
  document.addEventListener("dragleave", (ev) => { if (files(ev)) dropHint(false); });
  document.addEventListener("dragover", (ev) => { if (files(ev)) ev.preventDefault(); });
  document.addEventListener("drop", (ev) => { ev.preventDefault(); dropHint(null); });
  document.addEventListener("keydown", (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "s" && S.paste.editing) {
      ev.preventDefault();
      saveNote();
    }
  });

  S.casesInfo = await call("cases_info");
  applyState(await call("state"));
  await go("cases");
  refreshRoute();
  setInterval(refreshRoute, 15000);
  $("#route").addEventListener("click", routeMenu);
  pollJobs();
  setInterval(() => { if (api && S.vault !== "закрыто") pollJobs(); }, 1000);
}

window.addEventListener("pywebviewready", init);
if (window.pywebview && window.pywebview.api) init();
