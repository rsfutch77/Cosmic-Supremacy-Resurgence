// The game's 9-slice sets, joined into one image each for border-image and
// published as custom properties on the page. Shared by the launcher page and
// the scan window.
"use strict";

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
