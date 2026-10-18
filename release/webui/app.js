// The launcher page. Python owns every decision (launcher.Launcher, through
// web_view.WebView); this file draws what it is told through window.ui and
// hands back what the player presses through window.pywebview.api.
"use strict";

const $ = (id) => document.getElementById(id);

// ── the game's 9-slice sets, joined into one image each for border-image ──

function loadImage(src) {
  return new Promise((ok, fail) => {
    const im = new Image();
    im.onload = () => ok(im);
    im.onerror = () => fail(new Error("missing " + src));
    im.src = src;
  });
}

// rows: [[name, ...], ...] laid edge to edge, left to right and top to bottom.
// A name without an extension is a PNG.
async function joinPieces(rows) {
  const file = (n) => "art/" + (n.includes(".") ? n : n + ".png");
  const ims = await Promise.all(rows.map((r) => Promise.all(r.map((n) => loadImage(file(n))))));
  const w = ims[0].reduce((s, im) => s + im.width, 0);
  const h = ims.reduce((s, r) => s + r[0].height, 0);
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const g = c.getContext("2d");
  let y = 0;
  for (const r of ims) {
    let x = 0;
    for (const im of r) { g.drawImage(im, x, y); x += im.width; }
    y += r[0].height;
  }
  return `url(${c.toDataURL()})`;
}

async function buildSkin() {
  const root = document.documentElement.style;
  const button = (state) => ["UPPER", "MIDDLE", "LOWER"].map(
    (row) => ["LEFT", "MIDDLE", "RIGHT"].map((col) => `BUTTON${col}${row}${state}`));
  for (const [name, state] of [["normal", ""], ["hover", "HIGH"], ["pressed", "SEL"], ["disabled", "DISABLED"]]) {
    root.setProperty(`--btn-${name}`, await joinPieces(button(state)));
  }
  const knob = (state) => ["UPPER", "MIDDLE", "LOWER"].map((row) => [`SCROLLBARVERTKNOB${row}${state}`]);
  for (const [name, state] of [["normal", ""], ["hover", "HIGHLIGHTED"], ["pressed", "SELECTED"]]) {
    root.setProperty(`--knob-${name}`, await joinPieces(knob(state)));
  }
  root.setProperty("--hdr", await joinPieces([["NEWHEADERLEFT.bmp", "NEWHEADERMIDDLE.bmp", "NEWHEADERRIGHT.bmp"]]));
}

// ── calls to Python ──

function call(name, ...args) {
  const api = window.pywebview && window.pywebview.api;
  if (api && api[name]) return api[name](...args);
  return Promise.resolve(null);
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function gbtn(label, onClick, extra) {
  const b = el("button", "gbtn" + (extra ? " " + extra : ""), label);
  b.addEventListener("click", onClick);
  return b;
}

// ── state the page keeps between pushes ──

let columns = [];
let sort = { key: null, desc: false };
let modesDisabled = false;
const controlBtns = {};

function showPage(name) {
  for (const b of document.querySelectorAll("[data-page]")) b.classList.toggle("sel", b.dataset.page === name);
  for (const p of document.querySelectorAll(".page")) p.classList.toggle("on", p.id === "page-" + name);
}

function drawModes(modes) {
  const box = $("modes");
  box.replaceChildren();
  for (const m of modes) {
    const row = el("div", "row");
    const b = gbtn(m.title, () => call("play", m.id), "wide");
    b.disabled = !m.ready || modesDisabled;
    b.dataset.ready = m.ready ? "1" : "";
    row.append(b, el("div", "blurb", m.blurb));
    if (m.continues) {
      const fresh = gbtn("New Game", () => call("new_game", m.id), "small newgame");
      fresh.disabled = b.disabled;
      row.append(fresh);
    }
    box.appendChild(row);
  }
}

function drawHeadings() {
  const head = $("colhead");
  head.replaceChildren();
  for (const c of columns) {
    const here = c.key === sort.key;
    const cell = el("div", here ? "sorted" : "", c.heading + (here ? (sort.desc ? " ▾" : " ▴") : ""));
    cell.addEventListener("click", () => call("sort", c.key));
    head.appendChild(cell);
  }
  head.appendChild(el("div"));
}

function drawRows(galaxies) {
  const box = $("grows");
  box.replaceChildren();
  if (!galaxies.rows) {
    box.appendChild(el("div", "note", galaxies.note || ""));
    return;
  }
  galaxies.rows.forEach((row, r) => {
    const line = el("div", "grid grow");
    row.cells.forEach(([text, colour], i) => {
      const cell = el("div", "", text);
      cell.style.color = colour;
      cell.id = `cell-${r}-${i}`;
      line.appendChild(cell);
    });
    const act = el("div");
    if (row.action) act.appendChild(gbtn(row.action, () => call("row", r), "small"));
    line.appendChild(act);
    box.appendChild(line);
  });
}

// ── prompts: one on screen, the rest wait their turn ──

const prompts = [];
let current = null;

function show(id, on) { $(id).hidden = !on; }

function nextPrompt() {
  current = prompts.shift() || null;
  $("scrim").classList.toggle("on", !!current);
  if (!current) return;
  const p = current;
  $("dlg").classList.toggle("wide", p.kind === "notice");
  $("dlg-title").textContent = p.title || "";
  $("dlg-text").textContent = p.text || "";
  show("dlg-text", !!p.text);
  $("dlg-scroll").textContent = p.body || "";
  $("dlg-scroll").scrollTop = 0;
  show("dlg-scroll", !!p.body);
  $("dlg-error").textContent = p.error || "";
  show("dlg-error", !!p.error);
  $("dlg-note").textContent = p.note || "";
  show("dlg-note", !!p.note);

  show("dlg-field", !!p.input);
  if (p.input) {
    $("dlg-label").textContent = p.input.label || "";
    $("dlg-input").value = p.input.value || "";
    $("dlg-input").maxLength = p.input.maxlength || 200;
  }

  const refs = $("dlg-refs");
  refs.replaceChildren();
  show("dlg-refs", !!(p.refs && p.refs.length));
  for (const ref of p.refs || []) {
    const box = el("input");
    box.readOnly = true;
    box.value = ref.id;
    const copy = gbtn(p.copy || "Copy", () => {
      navigator.clipboard.writeText(ref.id).then(() => {
        copy.textContent = p.copied || "Copied";
        setTimeout(() => { copy.textContent = p.copy || "Copy"; }, 1500);
      });
    }, "small");
    refs.append(box, el("span", "", ref.when || ""), copy);
  }

  const btns = $("dlg-btns");
  btns.replaceChildren();
  for (const b of p.buttons) btns.appendChild(gbtn(b.label, () => answer(b.value)));
  if (p.input) { $("dlg-input").focus(); $("dlg-input").select(); }
  else if (btns.lastChild) btns.lastChild.focus();
}

function answer(value) {
  if (!current) return;
  const p = current;
  call("resolve", p.id, value === undefined ? null : value, p.input ? $("dlg-input").value : null);
  nextPrompt();
}

document.addEventListener("keydown", (e) => {
  if (!current) return;
  if (e.key === "Escape") { e.preventDefault(); answer(current.cancel); }
  if (e.key === "Enter" && current.default !== undefined && current.default !== null) {
    e.preventDefault();
    answer(current.default);
  }
});
$("dlg-x").addEventListener("click", () => answer(current && current.cancel));

// ── what Python calls ──

window.ui = {
  set(s) {
    if (s.product !== undefined) { $("product").textContent = s.product; document.title = s.product; }
    if (s.modesDisabled) modesDisabled = true;
    if (s.modes) drawModes(s.modes);
    if (s.modesDisabled) for (const b of $("modes").querySelectorAll("button")) b.disabled = true;
    if (s.columns) { columns = s.columns; drawHeadings(); }
    if (s.controls) {
      const box = $("control-btns");
      box.replaceChildren();
      for (const c of s.controls) {
        controlBtns[c.key] = gbtn(c.label, () => call("control", c.key));
        box.appendChild(controlBtns[c.key]);
      }
    }
    if (s.page) showPage(s.page);
    if (s.name !== undefined) $("name-btn").textContent = s.name || "set name";
    if (s.build !== undefined) $("st-build").textContent = s.build;
    if (s.status) {
      $("st-text").textContent = s.status.text;
      $("st-dot").style.background = s.status.colour;
    }
    if (s.capture) {
      $("cap-text").textContent = s.capture.text;
      $("cap-dot").style.background = s.capture.colour;
      show("cap", !!s.capture.text);
    }
    if (s.controlsShown !== undefined) show("controls", s.controlsShown);
    if (s.control) {
      const b = controlBtns[s.control.key];
      if (b) {
        if (s.control.enabled !== undefined) b.disabled = !s.control.enabled;
        if (s.control.text !== undefined) b.textContent = s.control.text;
      }
    }
    if (s.turnLabel !== undefined) $("turn-label").textContent = s.turnLabel;
    if (s.sort) { sort = s.sort; drawHeadings(); }
    if (s.gamesStatus) {
      $("games-status").textContent = s.gamesStatus.text;
      $("games-status").style.color = s.gamesStatus.colour;
    }
    if (s.galaxies) drawRows(s.galaxies);
    if (s.cell) {
      const c = $(`cell-${s.cell.row}-${s.cell.column}`);
      if (c) { c.textContent = s.cell.text; c.style.color = s.cell.colour; }
    }
    if (s.hint !== undefined) { $("games-hint").textContent = s.hint; show("games-hint", !!s.hint); }
    if (s.logRefs) show("log-refs-btn", true);
  },
  log(lines) {
    const box = $("log");
    const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
    box.textContent += lines.map((l) => l + "\n").join("");
    if (stick) box.scrollTop = box.scrollHeight;
  },
  prompt(p) {
    prompts.push(p);
    if (!current) nextPrompt();
  },
};

// ── wiring ──

for (const b of document.querySelectorAll("[data-page]")) b.addEventListener("click", () => call("page", b.dataset.page));
for (const b of document.querySelectorAll("[data-act]")) b.addEventListener("click", () => call(b.dataset.act));

const skinReady = buildSkin().catch((e) => console.error(e));
window.addEventListener("pywebviewready", () => skinReady.then(() => call("ready")));
