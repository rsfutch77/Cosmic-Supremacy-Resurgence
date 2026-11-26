"""Builds the mobile design's images from the client's own art.

    py -3.14 mobile\\build_art.py [client exe] [output folder] [--raw]

The exe defaults to client\\CosmicSupremacy_Resurgence.exe and the output to
mobile\\art, which is not committed. --raw also writes every PNG, bitmap and
texture resource under raw\\ for browsing. The images are the ones uploaded to
the "CS Mobile Interface" design canvas.
"""
import io
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'release'))
import extract_ui_art as x  # noqa: E402

args = [a for a in sys.argv[1:] if a != '--raw']
RAW = '--raw' in sys.argv
EXE = args[0] if len(args) > 0 else os.path.join(ROOT, 'client', 'CosmicSupremacy_Resurgence.exe')
OUT = args[1] if len(args) > 1 else os.path.join(ROOT, 'mobile', 'art')
os.makedirs(OUT, exist_ok=True)

data = open(EXE, 'rb').read()
sections, res_rva = x.parse_sections(data)
res_off = x.rva_to_off(sections, res_rva)
PNG = x.collect_named(data, sections, res_off, 'PNG')
BMP = {n: x.bmp_file(b) for n, b in x.collect_named(data, sections, res_off, x.RT_BITMAP).items()}
TEX = x.collect_named(data, sections, res_off, 'TEXTURES')

if RAW:
    raw = os.path.join(OUT, 'raw')
    os.makedirs(raw, exist_ok=True)
    for n, b in PNG.items():
        open(os.path.join(raw, n + '.png'), 'wb').write(b)
    for n, b in BMP.items():
        open(os.path.join(raw, n + '.bmp'), 'wb').write(b)
    for n, b in TEX.items():
        ext = '.png' if b[:4] == b'\x89PNG' else '.jpg' if b[:2] == b'\xff\xd8' else '.bmp' if b[:2] == b'BM' else '.tga'
        open(os.path.join(raw, 'TEXTURES_' + n + ext), 'wb').write(b)


def art(name):
    """A UI piece by resource name; magenta in bitmaps is transparent."""
    if name in PNG:
        return Image.open(io.BytesIO(PNG[name])).convert('RGBA')
    im = Image.open(io.BytesIO(BMP[name])).convert('RGBA')
    px = im.load()
    for y in range(im.height):
        for xx in range(im.width):
            if px[xx, y][:3] == (255, 0, 255):
                px[xx, y] = (0, 0, 0, 0)
    return im


def tex(name):
    return Image.open(io.BytesIO(TEX[name])).convert('RGBA')


def save(im, name):
    im.save(os.path.join(OUT, name))


# Map buttons: every state cropped to one square around the sphere, at 2x.
def sphere_box():
    a = tex('MAPHELPNORMAL').getchannel('A')
    x0, y0, x1, y1 = a.point(lambda v: 255 if v > 200 else 0).getbbox()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = max(x1 - x0, y1 - y0) / 2 + 6
    return (round(cx - r), round(cy - r), round(cx + r), round(cy + r)), (cx, cy), (x1 - x0) / 2


BOX, CENTER, RAD = sphere_box()
SIDE = BOX[2] - BOX[0]
SCALE = 4  # glyphs are drawn at 4x, then scaled down


def crop_btn(im):
    c = Image.new('RGBA', (SIDE, SIDE), (0, 0, 0, 0))
    c.alpha_composite(im.crop(BOX))
    return c


for btn in ('RADAR', 'OPTIONS', 'TERRITORY', 'HELP'):
    for state in ('NORMAL', 'SELECTED'):
        im = crop_btn(tex('MAP' + btn + state)).resize((SIDE * 2, SIDE * 2), Image.LANCZOS)
        save(im, f'btn_{btn.lower()}_{state.lower()}.png')


# A blank sphere: the help button with its "?" painted over by its own radial profile.
def blank(state):
    im = crop_btn(tex('MAPHELP' + state))
    px = im.load()
    cx, cy = CENTER[0] - BOX[0], CENTER[1] - BOX[1]
    inner = RAD - 3.2
    prof = {}
    for y in range(SIDE):
        for xx in range(SIDE):
            d = math.hypot(xx + 0.5 - cx, y + 0.5 - cy)
            if d < inner:
                prof.setdefault(int(d * 2), []).append(px[xx, y])
    lum = lambda c: c[0] * 0.3 + c[1] * 0.59 + c[2] * 0.11
    pick = {}
    for k, cols in prof.items():
        cols.sort(key=lum)
        pick[k] = cols[int(len(cols) * 0.85)]
    for y in range(SIDE):
        for xx in range(SIDE):
            d = math.hypot(xx + 0.5 - cx, y + 0.5 - cy)
            if d < inner:
                px[xx, y] = pick[int(d * 2)]
    big = im.resize((SIDE * SCALE, SIDE * SCALE), Image.LANCZOS)
    return big, (cx * SCALE, cy * SCALE), inner * SCALE


def glyph_btn(name, draw_fn):
    for state, ink in (('NORMAL', (0, 0, 0, 255)), ('SELECTED', (112, 66, 0, 255))):
        big, (cx, cy), r = blank(state)
        layer = Image.new('RGBA', big.size, (0, 0, 0, 0))
        draw_fn(ImageDraw.Draw(layer), cx, cy, r, ink)
        big.alpha_composite(layer)
        save(big.resize((SIDE * 2, SIDE * 2), Image.LANCZOS), f'btn_{name}_{state.lower()}.png')


def g_plus(d, cx, cy, r, ink):
    w, l = r * 0.2, r * 0.58
    d.rectangle((cx - l, cy - w, cx + l, cy + w), fill=ink)
    d.rectangle((cx - w, cy - l, cx + w, cy + l), fill=ink)


def g_minus(d, cx, cy, r, ink):
    w, l = r * 0.2, r * 0.58
    d.rectangle((cx - l, cy - w, cx + l, cy + w), fill=ink)


def g_center(d, cx, cy, r, ink):
    w = r * 0.12
    rr = r * 0.4
    d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=ink, width=round(w * 1.3))
    l0, l1 = r * 0.22, r * 0.7
    for sx, sy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        if sx:
            d.rectangle((cx + sx * l0 if sx > 0 else cx - l1, cy - w, cx + l1 if sx > 0 else cx - l0, cy + w), fill=ink)
        else:
            d.rectangle((cx - w, cy + l0 if sy > 0 else cy - l1, cx + w, cy + l1 if sy > 0 else cy - l0), fill=ink)
    d.ellipse((cx - w * 1.2, cy - w * 1.2, cx + w * 1.2, cy + w * 1.2), fill=ink)


def g_info(d, cx, cy, r, ink):
    w = r * 0.19
    d.ellipse((cx - w * 1.15, cy - r * 0.62 - w * 1.15, cx + w * 1.15, cy - r * 0.62 + w * 1.15), fill=ink)
    d.rectangle((cx - w, cy - r * 0.28, cx + w, cy + r * 0.5), fill=ink)
    d.rectangle((cx - w * 2.2, cy - r * 0.28, cx + w, cy - r * 0.28 + w * 1.1), fill=ink)
    d.rectangle((cx - w * 2.2, cy + r * 0.5 - w * 1.1, cx + w * 2.2, cy + r * 0.5), fill=ink)


def g_menu(d, cx, cy, r, ink):
    w, l = r * 0.13, r * 0.56
    for dy in (-0.36, 0, 0.36):
        d.rectangle((cx - l, cy + dy * r - w, cx + l, cy + dy * r + w), fill=ink)


glyph_btn('zoomin', g_plus)
glyph_btn('zoomout', g_minus)
glyph_btn('center', g_center)
glyph_btn('info', g_info)
glyph_btn('menu', g_menu)


# Planets: a texture wrapped on a sphere lit from the sun's side.
def planet(texname, size, light, tint=None, ring=None, rim=None):
    t = tex(texname).convert('RGB')
    if tint:
        g = t.convert('L')
        t = Image.merge('RGB', [g.point(lambda v, c=c: min(255, int(v * c / 255 * 1.25))) for c in tint])
    tw, th = t.size
    tp = t.load()
    ss = 3  # supersampling
    N = size * ss
    im = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    px = im.load()
    lx, ly, lz = light
    ln = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ln, ly / ln, lz / ln
    R = N / 2
    for y in range(N):
        for xx in range(N):
            nx, ny = (xx + 0.5 - R) / R, (y + 0.5 - R) / R
            q = nx * nx + ny * ny
            if q >= 1:
                continue
            nz = math.sqrt(1 - q)
            lon = math.atan2(nx, nz) + 0.6
            lat = math.asin(-ny)
            u = int(((lon / (2 * math.pi)) % 1) * tw) % tw
            v = min(th - 1, int((0.5 - lat / math.pi) * th))
            r, g, b = tp[u, v]
            s = 0.16 + 0.95 * max(0.0, nx * lx + ny * ly + nz * lz)  # ambient plus diffuse
            px[xx, y] = (min(255, int(r * s)), min(255, int(g * s)), min(255, int(b * s)), 255)
    im = im.resize((size, size), Image.LANCZOS)
    if rim:
        glow = Image.new('RGBA', (size + 8, size + 8), (0, 0, 0, 0))
        dd = ImageDraw.Draw(glow)
        dd.ellipse((3, 3, size + 4, size + 4), outline=rim, width=2)
        glow = glow.filter(ImageFilter.GaussianBlur(1.5))
        out = Image.new('RGBA', (size + 8, size + 8), (0, 0, 0, 0))
        out.alpha_composite(glow)
        out.alpha_composite(im, (4, 4))
        im = out
    if ring:
        rw, rh = ring
        W = int(size * rw)
        H = max(int(size * rh), 4)
        canvas = Image.new('RGBA', (W, max(W, im.height)), (0, 0, 0, 0))
        cxy = ((W - im.width) // 2, (canvas.height - im.height) // 2)
        band = Image.new('RGBA', (W * 3, canvas.height * 3), (0, 0, 0, 0))
        bd = ImageDraw.Draw(band)
        cy3 = canvas.height * 3 / 2
        for i, col in enumerate(((150, 128, 96, 120), (214, 190, 150, 210), (120, 104, 82, 160), (230, 214, 182, 220))):
            pad = i * 3 * 2
            bd.ellipse((pad, cy3 - H * 3 / 2 + pad / 3, W * 3 - pad, cy3 + H * 3 / 2 - pad / 3), outline=col, width=5)
        band = band.resize((W, canvas.height), Image.LANCZOS)
        back = band.crop((0, 0, W, canvas.height // 2))
        front = band.crop((0, canvas.height // 2, W, canvas.height))
        canvas.alpha_composite(back, (0, 0))
        canvas.alpha_composite(im, cxy)
        canvas.alpha_composite(front, (0, canvas.height // 2))
        im = canvas
    return im


SUN = (430, 200)  # the sun's place on the 844x390 map


def light_for(p):
    return (SUN[0] - p[0], SUN[1] - p[1], 140)


save(planet('GROUNDLAYER_MAGNET', 32, light_for((370, 123)), tint=(214, 150, 210)), 'planet_22.png')
save(planet('GROUNDLAYER_ROCK', 22, light_for((345, 184)), tint=(176, 190, 150)), 'planet_9.png')
save(planet('GROUNDLAYER_ROCK3', 22, light_for((560, 172)), tint=(200, 202, 206)), 'planet_7.png')
save(planet('GROUNDLAYER_ROCK5', 26, light_for((450, 280)), tint=(222, 160, 104), ring=(2.1, 0.5)), 'planet_21.png')
save(planet('EARTH', 28, light_for((660, 254)), rim=(120, 180, 255, 200)), 'planet_45.png')
save(planet('GROUNDLAYER_ICE', 14, light_for((600, 90)), tint=(190, 205, 220)), 'planet_free.png')

# Sun, flare, starfield, nebula.
save(tex('SUN').resize((96, 96), Image.LANCZOS), 'sun.png')
tex('SUNFLARE1').convert('RGB').save(os.path.join(OUT, 'sunflare.jpg'), quality=92)
tex('BACKGROUND').convert('RGB').save(os.path.join(OUT, 'stars.jpg'), quality=92)
save(tex('GALAXYNEBULA'), 'nebula.png')

# Planet tag frame: the plain pill from the tag texture.
pill = tex('PLANETTAG').crop((0, 60, 128, 110))
pill = pill.crop(pill.getbbox())
save(pill, 'tag_pill.png')

# Selection brackets, ship and fleet markers, tag symbols.
for n in ('BRACKETUPPERLEFT', 'BRACKETUPPERRIGHT', 'BRACKETLOWERLEFT', 'BRACKETLOWERRIGHT'):
    save(tex(n).resize((16, 16), Image.NEAREST), n.lower() + '.png')
save(tex('SHIP').resize((16, 16), Image.NEAREST), 'mk_ship.png')
save(tex('FLEET').resize((32, 32), Image.NEAREST), 'mk_fleet.png')
save(tex('SHIPYARDSYMBOL').resize((32, 32), Image.NEAREST), 'tag_shipyard.png')
save(tex('CENTRALDEFENSEAGENCYSYMBOL').resize((32, 32), Image.NEAREST), 'tag_cda.png')

# Menu and list icons at 2x.
for n in ('COMMANDMOVETO', 'COMMANDMOVENEAR', 'COMMANDATTACK', 'COMMANDSCOUT', 'COMMANDCOLONIZE',
          'COMMANDCONQUER', 'COMMANDBIOBOMB', 'COMMANDCREATEWORMHOLE', 'FIREPOWER', 'SHIELD', 'SPEED',
          'CREW', 'UNITS', 'SPACE', 'PLANET', 'SUN', 'SHIP', 'RECON', 'TREATY', 'NEWSMESSAGE', 'WORMHOLE',
          'PRODUCTION', 'SCIENCE', 'GOLD', 'FOOD', 'FLEET', 'MENUTICKED', 'PLANETARYDEFENSESYMBOL'):
    im = art(n)
    save(im.resize((im.width * 2, im.height * 2), Image.NEAREST), 'ic_' + n.lower() + '.png')

# Tickboxes and slider handle at 2x.
for n in ('TICKBOX', 'TICKBOXTICKED', 'TICKBOXDISABLED', 'TICKBOXTICKEDDISABLED', 'SLIDERHANDLENORMAL', 'SLIDERHANDLESELECTED'):
    im = art(n)
    save(im.resize((im.width * 2, im.height * 2), Image.NEAREST), 'ui_' + n.lower() + '.png')

# Header bar: one 9-slice image from the three NEWHEADER pieces.
l, m, r = art('NEWHEADERLEFT'), art('NEWHEADERMIDDLE'), art('NEWHEADERRIGHT')
hdr = Image.new('RGBA', (l.width + m.width + r.width, l.height))
hdr.paste(l, (0, 0))
hdr.paste(m, (l.width, 0))
hdr.paste(r, (l.width + m.width, 0))
save(hdr.resize((hdr.width * 2, hdr.height * 2), Image.NEAREST), 'ui_header.png')

# Game button: one 9-slice image per state from the 3x3 pieces.
btn_img = {}
for state, suffix in (('normal', ''), ('sel', 'SEL'), ('disabled', 'DISABLED')):
    btn = Image.new('RGBA', (33, 33))
    for j, row in enumerate(('UPPER', 'MIDDLE', 'LOWER')):
        for i, col in enumerate(('LEFT', 'MIDDLE', 'RIGHT')):
            p = art('BUTTON' + col + row + suffix)
            btn.paste(p.resize((11, 11), Image.NEAREST), (i * 11, j * 11))
    btn_img[state] = btn.resize((66, 66), Image.NEAREST)
    save(btn_img[state], f'ui_btn_{state}.png')

# Map background: tiled stars, a blue glow and a faint nebula, 844x390.
W, H = 844, 390
bg = Image.new('RGBA', (W, H), (0, 20, 40, 255))
tile = Image.open(os.path.join(OUT, 'stars.jpg')).convert('RGBA')
for y in range(0, H, 128):
    for xx in range(0, W, 128):
        bg.paste(tile, (xx, y))
glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
for i in range(60):
    t = i / 60
    rx, ry = 520 * (1 - t), 300 * (1 - t)
    gd.ellipse((520 - rx, 175 - ry, 520 + rx, 175 + ry), fill=(40, 95, 160, int(3 + 3 * t)))
bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(20)))
neb = tex('GALAXYNEBULA').resize((700, 700), Image.LANCZOS)
nr, ng, nb, na = neb.split()
neb = Image.merge('RGBA', (nr.point(lambda v: v * 0.45), ng.point(lambda v: v * 0.7), nb, na.point(lambda v: int(v * 0.28))))
bg.alpha_composite(neb, (330, -200))
bg.convert('RGB').save(os.path.join(OUT, 'map_bg.jpg'), quality=90)


# Tag frames: the pill stretched to each tag width, ends kept.
def nine_h(img, w, h, left, right):
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    out.paste(img.crop((0, 0, left, img.height)).resize((left, h)), (0, 0))
    out.paste(img.crop((img.width - right, 0, img.width, img.height)).resize((right, h)), (w - right, 0))
    out.paste(img.crop((left, 0, img.width - right, img.height)).resize((w - left - right, h)), (left, 0))
    return out


for w in (146, 136):
    nine_h(pill, w, 32, 18, 18).save(os.path.join(OUT, f'tag_{w}.png'))


# Markers in the owner colours: cyan for yours, red for enemies.
def tint(name, col, out):
    im = Image.open(os.path.join(OUT, name)).convert('RGBA')
    t = Image.new('RGBA', im.size, col + (255,))
    t.putalpha(im.getchannel('A'))
    t.save(os.path.join(OUT, out))


tint('mk_fleet.png', (0, 255, 255), 'mk_fleet_cyan.png')
tint('mk_ship.png', (255, 64, 48), 'mk_ship_red.png')


# The Orders button on the selection bar, baked at 192x72.
def nine(img, w, h, s):
    out = Image.new('RGBA', (w, h))
    xs = [(0, s, 0, s), (s, img.width - s, s, w - s), (img.width - s, img.width, w - s, w)]
    ys = [(0, s, 0, s), (s, img.height - s, s, h - s), (img.height - s, img.height, h - s, h)]
    for sx0, sx1, dx0, dx1 in xs:
        for sy0, sy1, dy0, dy1 in ys:
            out.paste(img.crop((sx0, sy0, sx1, sy1)).resize((dx1 - dx0, dy1 - dy0), Image.NEAREST), (dx0, dy0))
    return out


nine(btn_img['normal'], 192, 72, 22).save(os.path.join(OUT, 'btn_orders.png'))

print(len([f for f in os.listdir(OUT) if os.path.isfile(os.path.join(OUT, f))]), 'images in', OUT)
