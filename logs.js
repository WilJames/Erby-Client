// ===============================
// Erby Logs UI (logs.js)
// ===============================

/* ---------- DOM ---------- */
const log = document.getElementById("log");

const pauseBtn = document.getElementById("pauseBtn");
const autoscroll = document.getElementById("autoscroll");
const q = document.getElementById("q");
const maxLinesInp = document.getElementById("max");
const clearBtn = document.getElementById("clearBtn");

const lvlbar = document.getElementById("lvlbar");
const lvlAll = document.getElementById("lvlAll");
const levelButtons = Array.from(lvlbar.querySelectorAll(".lvlbtn[data-lvl]"));

const serverLvlWrap = document.getElementById("serverLvlWrap");
const serverBtns = Array.from(serverLvlWrap.querySelectorAll("button[data-srv]"));

const sbConn = document.getElementById("sbConn");
const sbTail = document.getElementById("sbTail");
const sbAuto = document.getElementById("sbAuto");
const sbLines = document.getElementById("sbLines");
const sbCounts = document.getElementById("sbCounts");
const sbFont = document.getElementById("sbFont");

/* ---------- Const / RegExp ---------- */
const RE = /^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2},\d{3})\s+([A-Z]+)\s+(\[[^\]]+\])\s*(.*)$/;
const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const LEVELS_FALLBACK_SCAN = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"];
const FONT_DEFAULT_PT = 10;
const FONT_MIN_PT = 7;
const FONT_MAX_PT = 24;
const LS_FONT_KEY = "erby_log_font_pt";

/* ---------- State ---------- */
let selectedLevels = new Set(); // пусто => ALL (все уровни)
let paused = false;
let followTail = true; // автоскролл "держит хвост" только если юзер у низа
let lines = [];
let es = null;

let counters = { DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0, CRITICAL: 0 };
let logFontPt = FONT_DEFAULT_PT;


/* =========================================================
   Хелперы
   ========================================================= */


/* =========================================================
   Фильтр по уровням (UI) + текстовый поиск
   ========================================================= */
function updateLevelUI() {
  const allActive = selectedLevels.size === 0;
  lvlAll.classList.toggle("active", allActive);

  for (const btn of levelButtons) {
    const lvl = btn.dataset.lvl;
    if (lvl === "ALL") continue;
    btn.classList.toggle("active", selectedLevels.has(lvl));
  }
}

function setOnlyLevel(lvl) {
  selectedLevels = new Set([lvl]);
  updateLevelUI();
  render();
}

function toggleLevel(lvl) {
  if (selectedLevels.has(lvl)) selectedLevels.delete(lvl);
  else selectedLevels.add(lvl);

  updateLevelUI();
  render();
}

function clearLevels() {
  selectedLevels.clear();
  updateLevelUI();
  render();
}

lvlbar.addEventListener("click", (ev) => {
  const btn = ev.target.closest(".lvlbtn");
  if (!btn) return;

  const lvl = btn.dataset.lvl;
  if (lvl === "ALL") return clearLevels();

  if (ev.shiftKey) toggleLevel(lvl);
  else setOnlyLevel(lvl);
});

function passesFilters(it) {
  const lvl = it && it.ok ? it.lvl : "";

  if (selectedLevels.size > 0) {
    if (!selectedLevels.has(lvl)) return false;
  }

  const query = q.value.trim().toLowerCase();
  if (query) {
    const hay = (it?.raw || "").toLowerCase();
    if (!hay.includes(query)) return false;
  }

  return true;
}


/* =========================================================
   Управление уровнем лога сервера (API)
   ========================================================= */
function setServerUI(level) {
  for (const b of serverBtns) {
    b.classList.toggle("srv-active", b.dataset.srv === level);
  }
}

async function fetchServerLevel() {
  try {
    const r = await fetch("/api/loglevel");
    const j = await r.json();
    if (j.level) setServerUI(j.level);
  } catch (_) {}
}

async function setServerLevel(level) {
  try {
    const r = await fetch("/api/loglevel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level }),
    });
    const j = await r.json();
    if (j.level) setServerUI(j.level);
  } catch (_) {}
}

serverLvlWrap.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-srv]");
  if (!btn) return;
  setServerLevel(btn.dataset.srv);
});

/* =========================================================
   Счётчики и статус-бар
   ========================================================= */
function updateCountersFromLines() {
  counters = { DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0, CRITICAL: 0 };

  for (const it of lines) {
    const lvl = it && it.ok ? it.lvl : "";
    if (counters[lvl] !== undefined) counters[lvl]++;
  }
}

function setConnStatus(ok) {
  if (ok) {
    sbConn.textContent = "SSE: подключён";
    sbConn.classList.remove("bad", "warn");
    sbConn.classList.add("good");
  } else {
    sbConn.textContent = "SSE: отключён";
    sbConn.classList.remove("good", "warn");
    sbConn.classList.add("bad");
  }
}

function updateStatusBar() {
  sbTail.textContent = "Хвост: " + (followTail ? "ON" : "OFF");
  sbTail.classList.toggle("dim", !followTail);

  // sbAuto — label с input, текст в HTML уже есть
  sbAuto.classList.toggle("dim", !autoscroll.checked);

  sbLines.textContent = "Строк: " + lines.length;

  sbCounts.textContent =
    `D:${counters.DEBUG} I:${counters.INFO} W:${counters.WARNING} E:${counters.ERROR} C:${counters.CRITICAL}`;

  sbFont.textContent = `Шрифт: ${logFontPt}pt`;
}

/* =========================================================
   Шрифт (Ctrl+колесо) + сброс кликом
   ========================================================= */
function applyFontPt(pt) {
  pt = Math.max(FONT_MIN_PT, Math.min(FONT_MAX_PT, pt));
  logFontPt = pt;

  document.documentElement.style.setProperty("--log-font-pt", String(pt));
  localStorage.setItem(LS_FONT_KEY, String(pt));

  updateStatusBar();
}

window.addEventListener(
  "wheel",
  (ev) => {
    if (!ev.ctrlKey) return;

    // чтобы браузер не делал zoom страницы
    ev.preventDefault();

    if (ev.deltaY < 0) applyFontPt(logFontPt + 1);
    else applyFontPt(logFontPt - 1);
  },
  { passive: false }
);

sbFont.addEventListener("click", () => applyFontPt(FONT_DEFAULT_PT));

/* =========================================================
   Follow tail (автоуправление по скроллу)
   ========================================================= */
function isNearBottom(px = 60) {
  const scrollPos = window.scrollY + window.innerHeight;
  const bottom = document.documentElement.scrollHeight;
  return bottom - scrollPos <= px;
}

window.addEventListener("scroll", () => {
  followTail = isNearBottom();
  updateStatusBar();
});

autoscroll.addEventListener("change", () => {
  if (!autoscroll.checked) followTail = false;
  updateStatusBar();
});

/* =========================================================
   Рендер строк (подсветка токенов)
   ========================================================= */
function span(cls, text) {
  if (cls === "t-url") {
    const a = document.createElement("a");
    a.className = cls;
    a.textContent = text;
    a.href = text;
    a.target = "_blank";
    return a;
  }

  const el = document.createElement("span");
  el.className = cls;
  el.textContent = text;
  return el;
}

function renderModule(moduleText) {
  // [server | server.py:88]
  // части текста — фиолетовые, разделители — белые
  const frag = document.createDocumentFragment();

  const inner = moduleText.slice(1, -1);
  const parts = inner.split("|").map((x) => x.trim());

  frag.appendChild(span("t-br", "["));

  for (let i = 0; i < parts.length; i++) {
    // часть целиком фиолетовая (без выделения чисел отдельно)
    frag.appendChild(span("t-dot", parts[i]));

    if (i < parts.length - 1) {
      frag.appendChild(span("t-br", " "));
      frag.appendChild(span("t-br", "|"));
      frag.appendChild(span("t-br", " "));
    }
  }

  frag.appendChild(span("t-br", "]"));
  return frag;
}

function tokenizeText(text, opts = {}) {
  const baseClass = opts.baseClass || "t-msg";
  const frag = document.createDocumentFragment();

  // Порядок важен:
  // 1) '...'
  // 2) url
  // 3) маркеры направления в начале сообщения: < > %
  // 4) числа
  const re =
    /'([^'\\]*(?:\\.[^'\\]*)*)'|((?:https?|wss?):\/\/[^\s]+)|(^\s*[<>%])|(\b\d+\b)/g;

  let last = 0;
  let m;

  while ((m = re.exec(text)) !== null) {
    const start = m.index;

    if (start > last) {
      frag.appendChild(span(baseClass, text.slice(last, start)));
    }

    // --- quoted string (всё жёлтое, внутри не красим) ---
    if (m[1] !== undefined) {
      frag.appendChild(span("t-quote", "'" + m[1] + "'"));
    }

    // --- URL (фиолетовый как числа) ---
    else if (m[2] !== undefined) {
      frag.appendChild(span("t-url", m[2]));
    }

    // --- direction marker (только в начале строки/сообщения) ---
    else if (m[3] !== undefined) {
      const marker = m[3]; // например " <" или ">"
      const ch = marker.trim();

      let cls = "t-msg";
      if (ch === ">") cls = "t-dir-out";
      else if (ch === "<") cls = "t-dir-in";
      else if (ch === "%") cls = "t-dir-sys";

      frag.appendChild(span(baseClass, marker.replace(/[<>%]/g, ""))); // пробелы до
      frag.appendChild(span(cls, ch));
    }

    // --- number (вне кавычек) ---
    else if (m[4] !== undefined) {
      frag.appendChild(span("t-num", m[4]));
    }

    last = re.lastIndex;
  }

  if (last < text.length) {
    frag.appendChild(span(baseClass, text.slice(last)));
  }

  return frag;
}

function renderLine(it) {
  const el = document.createElement("div");
  el.className = "line";

  if (!it || !it.ok) {
    el.appendChild(tokenizeText(it?.raw ?? "", { baseClass: "t-msg" }));
    return el;
  }

  el.appendChild(span("t-dt", it.date + " " + it.time + " "));
  el.appendChild(span("t-lvl-" + it.lvl, it.lvl));
  el.appendChild(span("t-msg", " "));

  el.appendChild(renderModule(it.module));

  if (it.msg) {
    el.appendChild(span("t-msg", " "));
    el.appendChild(tokenizeText(it.msg, { baseClass: "t-msg" }));
  }

  return el;
}


function render() {
  log.textContent = "";

  const maxLines = Math.max(100, parseInt(maxLinesInp.value || "2000", 10));
  const start = Math.max(0, lines.length - maxLines);

  const frag = document.createDocumentFragment();
  for (let i = start; i < lines.length; i++) {
    const it = lines[i];
    if (!passesFilters(it)) continue;
    frag.appendChild(renderLine(it));
  }
  log.appendChild(frag);

  if (autoscroll.checked && followTail) {
    window.scrollTo(0, document.body.scrollHeight);
  }
}

/* =========================================================
   SSE подключение
   ========================================================= */
function connect() {
  // ----------------------------
  // Persistent reconnection state
  // ----------------------------
  if (!connect._state) {
    connect._state = {
      retryMs: 500,
      retryMaxMs: 10000,
      retryTimer: null,
      closedByUs: false,
    };
  }
  const st = connect._state;

  function clearRetryTimer() {
    if (st.retryTimer) {
      clearTimeout(st.retryTimer);
      st.retryTimer = null;
    }
  }

  function scheduleReconnect() {
    if (st.closedByUs) return;   // если закрыли сами — не реконнектимся
    if (st.retryTimer) return;   // уже запланировано

    const delay = st.retryMs;
    st.retryMs = Math.min(st.retryMaxMs, Math.floor(st.retryMs * 1.7));

    st.retryTimer = setTimeout(() => {
      st.retryTimer = null;
      connect();
    }, delay);
  }

  // ----------------------------
  // Close previous connection (if any)
  // ----------------------------
  if (es) {
    st.closedByUs = true;
    try { es.close(); } catch (_) {}
    st.closedByUs = false;
    es = null;
  }
  clearRetryTimer();

  // ----------------------------
  // Create EventSource
  // ----------------------------
  es = new EventSource("/logs/stream");

  // ----------------------------
  // Batch render state
  // ----------------------------
  let pending = [];
  let flushTimer = null;

  function lvlOf(it) {
    return it && it.ok ? it.lvl : "";
  }

  function countersAddBatch(batch) {
    for (const it of batch) {
      const lvl = lvlOf(it);
      if (counters[lvl] !== undefined) counters[lvl]++;
    }
  }

  function scheduleFlush() {
    if (flushTimer) return;

    flushTimer = setTimeout(() => {
      flushTimer = null;

      if (paused) {
        pending.length = 0; // не копим в паузе
        return;
      }

      if (pending.length === 0) return;

      const batch = pending;
      pending = [];

      // добавить новые строки
      lines.push(...batch);

      // инкрементальные счётчики
      countersAddBatch(batch);

      // ограничение буфера
      const hardMax = Math.max(2000, parseInt(maxLinesInp.value || "2000", 10) * 3);
      let trimmed = false;
      if (lines.length > hardMax) {
        lines = lines.slice(lines.length - hardMax);
        trimmed = true;
      }

      // если резали — проще пересчитать (редко)
      if (trimmed) updateCountersFromLines();

      updateStatusBar();
      render();
    }, 50);
  }

  // ----------------------------
  // Events
  // ----------------------------
  es.onopen = () => {
    setConnStatus(true);

    // подключились успешно — сброс backoff
    st.retryMs = 500;

    updateStatusBar();
  };

  // Сервер шлёт: event: init, data: [ ... ]
  es.addEventListener("init", (e) => {
    let arr = [];
    try {
      const parsed = JSON.parse(e.data);
      if (Array.isArray(parsed)) arr = parsed;
    } catch (_) {
      arr = [];
    }

    // заменить буфер на init-снимок
    lines = arr;

    // сброс очереди
    pending.length = 0;
    if (flushTimer) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }

    // полный пересчёт на init
    updateCountersFromLines();
    updateStatusBar();
    render();
  });

  // Сервер шлёт: event: log, data: { ... }
  es.addEventListener("log", (e) => {
    if (paused) return;

    let obj;
    try {
      obj = JSON.parse(e.data);
      if (!obj || typeof obj !== "object") obj = null;
    } catch (_) {
      obj = null;
    }

    if (!obj) obj = { ok: false, raw: String(e.data), lvl: "" };

    // лёгкая нормализация
    if (typeof obj.raw !== "string") obj.raw = String(e.data);
    if (typeof obj.ok !== "boolean") obj.ok = !!obj.ok;
    if (typeof obj.lvl !== "string") obj.lvl = "";

    pending.push(obj);
    scheduleFlush();
  });

  es.onerror = () => {
    setConnStatus(false);
    updateStatusBar();

    // Закрываем "полудохлый" ES и реконнектимся сами по backoff
    try {
      st.closedByUs = true;
      es.close();
    } catch (_) {}
    finally {
      st.closedByUs = false;
      es = null;
    }

    scheduleReconnect();
  };
}


/* =========================================================
   Кнопки / события
   ========================================================= */
pauseBtn.onclick = () => {
  paused = !paused;
  pauseBtn.textContent = paused ? "Продолжить" : "Пауза";
  pauseBtn.classList.toggle("pause-active", paused);
};

clearBtn.onclick = () => {
  lines = [];
  updateCountersFromLines();
  updateStatusBar();
  render();
};

q.oninput = render;
maxLinesInp.onchange = render;

/* =========================================================
   init
   ========================================================= */
(function init() {
  const saved = parseInt(localStorage.getItem(LS_FONT_KEY) || String(FONT_DEFAULT_PT), 10);
  applyFontPt(Number.isFinite(saved) ? saved : FONT_DEFAULT_PT);

  updateLevelUI();
  fetchServerLevel();

  updateCountersFromLines();
  setConnStatus(false);
  updateStatusBar();

  connect();
})();
