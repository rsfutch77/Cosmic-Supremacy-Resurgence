"""
A page for checking a run by eye: the map, planets and ships each turn, with a
slider, and each civ's credits, planets, population, ships and dominance.

    python -m sim.viewer client/SinglePlayerGalaxy.dat --turns 60 -o run.html

The page is one self-contained file. Recording is cheap: a frame keeps only
what the page draws.
"""
import argparse
import json

from . import baseline
from . import env as ev
from . import load as sl
from . import reward as rw

PALETTE = ('#2f7fd8', '#d8462f', '#2fa85a', '#c48a12', '#8a4fd0', '#c43f8f',
           '#3fa6b0', '#7a7a2a')


def frame(g):
    d = rw.dominance(g)
    civs = {}
    for c in g.civs.values():
        mine = [p for p in g.planets.values() if p.owner == c.oid]
        civs[c.oid] = {'name': c.name, 'credits': c.credits, 'planets': len(mine),
                       'pop': sum(len(p.citizens) for p in mine),
                       'ships': sum(1 for s in g.ships.values() if s.owner == c.oid),
                       'research': len(c.completed or ()), 'D': round(d.get(c.oid, 0), 4)}
    return {
        'turn': g.turn,
        'planets': [[round(p.pos[0], 1), round(p.pos[2], 1), p.owner, len(p.citizens)]
                    for p in g.planets.values()],
        'ships': [[round(s.pos[0], 1), round(s.pos[2], 1), s.owner] for s in g.ships.values()],
        'civs': civs,
    }


def record(galaxy, turns, policy=baseline.policy):
    """Frames of a run; `policy(galaxy, civs) -> {civ: [actions]}`."""
    e = ev.Env(galaxy, horizon=turns)
    e.reset()
    frames = [frame(e.g)]
    done = False
    while not done:
        _obs, _r, done, _i = e.step(policy(e.g, e.played) if policy else {})
        frames.append(frame(e.g))
    return frames


def page(frames, suns):
    data = json.dumps({'frames': frames, 'suns': [[round(p[0], 1), round(p[2], 1)]
                                                  for p in suns.values()],
                       'palette': PALETTE})
    return PAGE.replace('__DATA__', data)


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Simulator Run</title>
<style>
:root { --bg:#fbfbf9; --fg:#1d1d1b; --muted:#6b6b66; --line:#d9d9d2; --unowned:#b9b9b0; --sun:#e0b21b; }
@media (prefers-color-scheme: dark) { :root { --bg:#151514; --fg:#ececea; --muted:#9a9a94; --line:#33332f; --unowned:#55554f; --sun:#c99a12; } }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.4 system-ui, sans-serif; }
main { display:grid; grid-template-columns:minmax(0,1fr) 300px; gap:16px; padding:16px; }
@media (max-width:760px) { main { grid-template-columns:1fr; } }
svg { width:100%; height:auto; border:1px solid var(--line); border-radius:8px; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
td, th { padding:4px 6px; border-bottom:1px solid var(--line); text-align:right; }
td:first-child, th:first-child { text-align:left; }
.controls { display:flex; gap:8px; align-items:center; margin:8px 0; }
input[type=range] { flex:1; }
.muted { color:var(--muted); }
</style></head><body><main>
<section><div class="controls"><button id="play">Play</button>
<input id="turn" type="range" min="0" value="0"><span id="label" class="muted"></span></div>
<svg id="map"></svg></section>
<section><h2 style="margin:0 0 8px;font-size:16px">Civs</h2><table id="civs"></table>
<p class="muted">Dot size is population; grey planets are unowned; triangles are ships.</p></section>
</main><script>
const D = __DATA__;
const svg = document.getElementById('map'), slider = document.getElementById('turn');
const xs = [], ys = [];
D.frames[0].planets.forEach(p => { xs.push(p[0]); ys.push(p[1]); });
D.suns.forEach(s => { xs.push(s[0]); ys.push(s[1]); });
const minX = Math.min(...xs) - 20, minY = Math.min(...ys) - 20;
const w = Math.max(...xs) - minX + 20, h = Math.max(...ys) - minY + 20;
svg.setAttribute('viewBox', `${minX} ${minY} ${w} ${h}`);
const civIds = Object.keys(D.frames[0].civs);
const colour = id => id && civIds.indexOf(String(id)) >= 0 ? D.palette[civIds.indexOf(String(id)) % D.palette.length] : 'var(--unowned)';
slider.max = D.frames.length - 1;
function draw(i) {
  const f = D.frames[i], r = w / 300;
  let s = D.suns.map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="${r * 0.8}" fill="var(--sun)" opacity="0.5"/>`).join('');
  s += f.planets.map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="${r * (0.6 + Math.sqrt(p[3]) * 0.35)}" fill="${colour(p[2])}"/>`).join('');
  s += f.ships.map(p => `<path d="M${p[0]} ${p[1] - r * 1.4} l${r} ${r * 2} h${-2 * r} z" fill="none" stroke="${colour(p[2])}" stroke-width="${r * 0.4}"/>`).join('');
  svg.innerHTML = s;
  document.getElementById('label').textContent = 'turn ' + f.turn;
  const rows = civIds.map(id => { const c = f.civs[id]; return `<tr><td><span style="color:${colour(id)}">&#9632;</span> ${c.name}</td><td>${c.credits}</td><td>${c.planets}</td><td>${c.pop}</td><td>${c.ships}</td><td>${c.research}</td><td>${c.D}</td></tr>`; });
  document.getElementById('civs').innerHTML = '<tr><th>civ</th><th>credits</th><th>planets</th><th>pop</th><th>ships</th><th>techs</th><th>D</th></tr>' + rows.join('');
}
slider.oninput = () => draw(+slider.value);
let timer = null;
document.getElementById('play').onclick = e => {
  if (timer) { clearInterval(timer); timer = null; e.target.textContent = 'Play'; return; }
  e.target.textContent = 'Pause';
  timer = setInterval(() => { slider.value = (+slider.value + 1) % D.frames.length; draw(+slider.value); }, 150);
};
draw(0);
</script></body></html>
'''


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('blob')
    ap.add_argument('--turns', type=int, default=60)
    ap.add_argument('-o', '--out', default='run.html')
    args = ap.parse_args(argv)
    g = sl.load_path(args.blob)
    frames = record(g, args.turns)
    open(args.out, 'w', encoding='utf-8').write(page(frames, g.suns))
    print(f'{len(frames)} frames -> {args.out}')


if __name__ == '__main__':
    main()
