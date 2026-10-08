// The launcher page. Python owns every decision; this file draws what it is told
// (ui.set, ui.log) and hands back what the player does (window.pywebview.api).
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

function api() {
  return (window.pywebview && window.pywebview.api) || null;
}

function call(name, ...args) {
  const a = api();
  if (a && a[name]) return a[name](...args);
  return Promise.resolve(null);
}

// ── pages ──

function showPage(name) {
  for (const b of document.querySelectorAll("[data-page]")) b.classList.toggle("sel", b.dataset.page === name);
  for (const p of document.querySelectorAll(".page")) p.classList.toggle("on", p.id === "page-" + name);
}

// ── prompts: one on screen, the rest wait their turn ──

const prompts = [];
let current = null;

function nextPrompt() {
  current = prompts.shift() || null;
  $("scrim").classList.toggle("on", !!current);
  if (!current) return;
  const p = current;
  $("dlg-title").textContent = p.title || "";
  $("dlg-text").textContent = p.text || "";
  $("dlg-error").textContent = p.error || "";
  $("dlg-error").style.display = p.error ? "" : "none";
  $("dlg-note").textContent = p.note || "";
  $("dlg-note").style.display = p.note ? "" : "none";
  const field = $("dlg-field");
  field.style.display = p.input ? "" : "none";
  if (p.input) {
    $("dlg-label").textContent = p.input.label || "";
    const inp = $("dlg-input");
    inp.value = p.input.value || "";
    inp.maxLength = p.input.maxlength || 200;
  }
  const btns = $("dlg-btns");
  btns.replaceChildren();
  for (const b of p.buttons) {
    const el = document.createElement("button");
    el.className = "gbtn";
    el.textContent = b.label;
    el.addEventListener("click", () => answer(b.value));
    btns.appendChild(el);
  }
  if (p.input) $("dlg-input").focus();
  else btns.lastChild && btns.lastChild.focus();
}

function answer(value) {
  if (!current) return;
  const p = current;
  const text = p.input ? $("dlg-input").value : null;
  call("resolve", p.id, value, text);
  nextPrompt();
}

document.addEventListener("keydown", (e) => {
  if (!current) return;
  if (e.key === "Escape") { e.preventDefault(); answer(current.cancel); }
  if (e.key === "Enter" && current.default !== undefined) { e.preventDefault(); answer(current.default); }
});
$("dlg-x").addEventListener("click", () => answer(current && current.cancel));

// ── what Python calls ──

window.ui = {
  set(s) {
    if (s.product !== undefined) { $("product").textContent = s.product; document.title = s.product; }
    if (s.name !== undefined) {
      $("st-name").textContent = s.name || "";
      $("name-btn").textContent = s.name || "set name";
    }
    if (s.build !== undefined) $("st-build").textContent = s.build;
    if (s.status) {
      $("st-text").textContent = s.status.text;
      $("st-dot").style.background = s.status.colour;
    }
    if (s.modes) {
      const box = $("modes");
      box.replaceChildren();
      for (const m of s.modes) {
        const row = document.createElement("div");
        row.className = "row";
        const b = document.createElement("button");
        b.className = "gbtn wide";
        b.textContent = m.title;
        b.disabled = !m.ready;
        b.addEventListener("click", () => call("play", m.id));
        const t = document.createElement("div");
        t.className = "blurb";
        t.textContent = m.blurb;
        row.append(b, t);
        box.appendChild(row);
      }
    }
    if (s.page) showPage(s.page);
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

for (const b of document.querySelectorAll("[data-page]")) b.addEventListener("click", () => showPage(b.dataset.page));
for (const b of document.querySelectorAll("[data-act]")) b.addEventListener("click", () => call(b.dataset.act));

const skinReady = buildSkin().catch((e) => console.error(e));
window.addEventListener("pywebviewready", () => skinReady.then(() => call("ready")));
