#!/usr/bin/env python3
"""
Embed every game image and the background music into index.html as base64 data URIs.

GitHub Pages (or any host) then only needs the single index.html file.
The original files in rank-badges/ and logos/ are never modified: they are
read, resized / compressed (images) or copied as-is (music: compress it first, see below),
base64-encoded, and written into one static
block of index.html:

    <!-- ASSETS:BEGIN -->
    <script id="mathdemon-assets">const ASSETS = { ... };</script>
    <!-- ASSETS:END -->

Only that block is rewritten; the rest of index.html is left untouched, so the
script can be re-run whenever an image changes. Output is deterministic
(same inputs -> same bytes).

Usage (from the project root):
    python3 tools/embed-assets.py            # rewrite the block in index.html
    python3 tools/embed-assets.py --check    # only report whether it is up to date

Requires Pillow:  pip install pillow
"""
import base64
import io
import json
import re
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required:  pip install pillow")

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "index.html"
BADGES_DIR = ROOT / "rank-badges"
LOGOS_DIR = ROOT / "logos"
SONGS_DIR = ROOT / "songs"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Compression settings: sized for how large each image is actually displayed (2x for high-density screens).
BADGE = dict(max_side=120, quality=66, alpha_quality=70)       # shown at 56-78 CSS px
WORDMARK = dict(max_side=520, quality=74, alpha_quality=80)    # header logo, shown at <= 340 CSS px
LOCKUP = dict(max_side=600, quality=80, alpha_quality=85)      # full logo (embedded, available to the app)
EXTRA = dict(max_side=512, quality=78, alpha_quality=80)       # any other image found in the asset folders
PWA_BG = (20, 18, 17)                  # same as the page background (#141211)
PWA_SIDES, PWA_COLORS, PWA_FILL = (192, 512), 64, 0.66   # icon covers 66% of the canvas: safe for maskable (rounded/circle) masks
FAVICON_SIDE, FAVICON_COLORS = 48, 48
TOUCH_SIDE, TOUCH_ICON_SIDE, TOUCH_COLORS, TOUCH_BG = 180, 150, 64, (19, 19, 18)

BEGIN, END = "<!-- ASSETS:BEGIN -->", "<!-- ASSETS:END -->"


ALPHA_CUTOFF = 8   # ignore nearly invisible stray pixels (alpha < 8/255) when finding the visible content


def load(path):
    """RGBA image cropped to its visible content. Faint stray pixels are ignored, otherwise an almost
    transparent smudge at one edge would push the real artwork off-centre."""
    im = Image.open(path).convert("RGBA")
    box = im.getchannel("A").point(lambda v: 255 if v >= ALPHA_CUTOFF else 0).getbbox()
    return im.crop(box) if box else im


def data_uri(mime, data):
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def webp(im, max_side, quality, alpha_quality):
    im = im.copy()
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=quality, alpha_quality=alpha_quality, method=6)
    return data_uri("image/webp", buf.getvalue()), im.size


def png8(im, colors):
    """Palette PNG: browsers require PNG (not WebP) for favicons / home-screen icons."""
    q = im.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    q.save(buf, "PNG", optimize=True)
    return data_uri("image/png", buf.getvalue())


def badge_files():
    """rank-badges/row-R-column-C.png, ordered lowest rank (row 1, column 1) to highest."""
    pat = re.compile(r"row-(\d+)-column-(\d+)\.", re.I)
    found = []
    for p in BADGES_DIR.iterdir():
        m = pat.match(p.name)
        if m and p.suffix.lower() in IMAGE_EXT:
            found.append(((int(m.group(1)), int(m.group(2))), p))
    found.sort()
    return [p for _, p in found]


def find_music():
    """The song to embed: the "*-compressed.mp3" in songs/ (smaller is better, the data is
    inlined as base64), else the smallest .mp3 there. Compress with e.g.
        ffmpeg -i song.mp3 -map_metadata -1 -af volume=-3dB -c:a libmp3lame -b:a 64k song-compressed.mp3
    """
    if not SONGS_DIR.is_dir():
        return None
    mp3s = sorted(SONGS_DIR.glob("*.mp3"))
    preferred = [p for p in mp3s if p.stem.endswith("-compressed")]
    pool = preferred or sorted(mp3s, key=lambda p: (p.stat().st_size, p.name))
    return pool[0] if pool else None


def build_assets():
    used = set()
    assets, sizes = {}, {}

    badges = badge_files()
    if len(badges) != 36:
        sys.exit(f"expected 36 badges in {BADGES_DIR}, found {len(badges)}")
    assets["badges"] = [webp(load(p), **BADGE)[0] for p in badges]
    used.update(badges)

    wordmark = LOGOS_DIR / "text-only.png"
    uri, (w, h) = webp(load(wordmark), **WORDMARK)
    assets["wordmark"] = uri
    sizes["wordmark"] = [w, h]
    used.add(wordmark)

    lockup = LOGOS_DIR / "logo-and-text.png"
    if lockup.exists():
        uri, (w, h) = webp(load(lockup), **LOCKUP)
        assets["lockup"] = uri
        sizes["lockup"] = [w, h]
        used.add(lockup)

    icon_path = LOGOS_DIR / "icon-only.png"
    icon = load(icon_path)
    fav = icon.copy()
    fav.thumbnail((FAVICON_SIDE, FAVICON_SIDE), Image.LANCZOS)
    assets["favicon"] = png8(fav, FAVICON_COLORS)
    touch = Image.new("RGBA", (TOUCH_SIDE, TOUCH_SIDE), TOUCH_BG + (255,))
    ic = icon.copy()
    ic.thumbnail((TOUCH_ICON_SIDE, TOUCH_ICON_SIDE), Image.LANCZOS)
    touch.paste(ic, ((TOUCH_SIDE - ic.width) // 2, (TOUCH_SIDE - ic.height) // 2), ic)
    assets["touchIcon"] = png8(touch.convert("RGB"), TOUCH_COLORS)
    used.add(icon_path)

    # PWA home-screen icons (192 and 512 px): the emblem centred on the page colour, as palette PNGs.
    for side in PWA_SIDES:
        canvas = Image.new("RGBA", (side, side), PWA_BG + (255,))
        ic = icon.copy()
        ic.thumbnail((int(side * PWA_FILL), int(side * PWA_FILL)), Image.LANCZOS)
        canvas.paste(ic, ((side - ic.width) // 2, (side - ic.height) // 2), ic)
        assets[f"pwaIcon{side}"] = png8(canvas.convert("RGB"), PWA_COLORS)

    music = find_music()
    if music:
        assets["music"] = data_uri("audio/mpeg", music.read_bytes())
        print(f"music: {music.name} ({music.stat().st_size / 1024:.0f} KB)")
    else:
        print(f"note: no MP3 found in {SONGS_DIR}; the game will run without music")

    # Anything else dropped into the asset folders is embedded too, keyed by its path.
    extra = {}
    for folder in (BADGES_DIR, LOGOS_DIR):
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p.suffix.lower() in IMAGE_EXT and p not in used:
                extra[p.relative_to(ROOT).as_posix()] = webp(load(p), **EXTRA)[0]
    if extra:
        assets["extra"] = extra
    assets["sizes"] = sizes
    return assets


def render_block(assets):
    body = json.dumps(assets, indent=1, ensure_ascii=True)
    return (f"{BEGIN}\n<script id=\"mathdemon-assets\">\n"
            "/* Generated by tools/embed-assets.py from rank-badges/ and logos/. Do not edit by hand. */\n"
            f"const ASSETS = Object.freeze({body});\n</script>\n{END}")


def main():
    check = "--check" in sys.argv
    html = HTML.read_text(encoding="utf-8")
    if html.count(BEGIN) != 1 or html.count(END) != 1:
        sys.exit(f"index.html must contain exactly one {BEGIN} ... {END} block")
    block = render_block(build_assets())
    start, end = html.index(BEGIN), html.index(END) + len(END)
    new_html = html[:start] + block + html[end:]

    kb = lambda n: f"{n / 1024:.0f} KB"
    assets_bytes = len(block.encode())
    if check:
        status = "up to date" if new_html == html else "OUT OF DATE (run without --check)"
        print(f"assets block: {kb(assets_bytes)}  ->  {status}")
        sys.exit(0 if new_html == html else 1)
    HTML.write_text(new_html, encoding="utf-8")
    print(f"index.html: {kb(len(html.encode()))} -> {kb(len(new_html.encode()))}  (assets block {kb(assets_bytes)})")


if __name__ == "__main__":
    main()
