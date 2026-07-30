"""Badge the upstream Cync brand assets with a BLE marker.

Copied from cync-lan's scripts/make_brand_icons.py with one change - the
badge text - rather than diverging the two: same base art, same geometry,
same reasoning, just a different transport being called out. The one
addition over cync-lan's version is badge_logo(): cync-lan's own wordmark
(logo.png/dark_logo.png) was hand-tuned once and only the rendered PNGs were
ever committed, with no generator - badge_logo()'s geometry constants are
measured directly off that committed result, documented at their
definitions, so this repo isn't left without a way to regenerate its own.

Base art is `core_integrations/cync` from home-assistant/brands - the same
mark Home Assistant already shows for Cync devices - so this integration is
recognisable as being about Cync hardware. The badge is what distinguishes it
from the cloud integration and from cync-lan: this one talks to the devices
directly over Bluetooth.

Every asset is produced in a light and a dark variant, because the base art
ships in two: a black mark for light themes and a white one for dark. Home
Assistant, HACS, and GitHub's own dark theme all pick the dark variant
automatically, and a black-on-transparent mark on a dark background is close
to invisible without it.

Icon sizes are driven by legibility at 32px, which is what HACS renders in
its repository list.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

SS = 4  # supersample factor for the badge; the base art is already raster

ACCENT = (31, 111, 235, 255)  # #1F6FEB - carried over from the old icon
ACCENT_TEXT = (255, 255, 255, 255)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


def _font(px: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            # index 1 is usually the bold face in a .ttc; fall back to 0 for
            # single-face files, where asking for index 1 raises.
            try:
                return ImageFont.truetype(path, px, index=1)
            except (OSError, ValueError):
                return ImageFont.truetype(path, px)
        except OSError:
            continue
    raise SystemExit("no usable TrueType font found")


def badge(base: Image.Image, knockout: bool = True) -> Image.Image:
    """Composite a BLE badge onto the bottom-right of `base`."""
    size = base.size[0]
    work = size * SS
    im = base.convert("RGBA").resize((work, work), Image.LANCZOS)

    # Badge geometry: bottom-right, large enough that "BLE" stays readable,
    # small enough to leave the Cync mark dominant. Same three-letter length
    # as cync-lan's "LAN", so the geometry tuned there transfers unchanged.
    r = int(work * 0.205)
    cx = cy = work - r

    if knockout:
        # Punch a transparent ring so the badge reads as sitting *on top* of
        # the mark rather than merging into the ray it covers.
        ring = Image.new("L", (work, work), 0)
        ImageDraw.Draw(ring).ellipse(
            [cx - r - int(work * 0.035), cy - r - int(work * 0.035),
             cx + r + int(work * 0.035), cy + r + int(work * 0.035)],
            fill=255,
        )
        cleared = im.getchannel("A").point(lambda a: a)
        cleared.paste(0, (0, 0), ring)
        im.putalpha(cleared)

    d = ImageDraw.Draw(im)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT)

    text = "BLE"
    f = _font(int(r * 0.80))
    left, top, right, bottom = d.textbbox((0, 0), text, font=f)
    d.text(
        (cx - (right + left) / 2, cy - (bottom + top) / 2),
        text,
        font=f,
        fill=ACCENT_TEXT,
    )

    return im.resize((size, size), Image.LANCZOS)


# Geometry below is measured directly off cync-lan's own committed
# logo.png/dark_logo.png (a hand-tuned result with no generator of its own -
# see this script's module docstring), not re-derived from scratch: canvas
# widened by ~10% of the base wordmark's width so a pill badge fits flush
# against the new right edge, sized to ~26% of canvas height, sitting in the
# empty space below the "ync" baseline rather than over any letterform, so no
# knockout is needed here the way the icon's circular badge needs one.
LOGO_CANVAS_EXTEND = 0.10
LOGO_PILL_HEIGHT = 0.258
LOGO_PILL_BOTTOM_MARGIN = 0.086
LOGO_PILL_PAD_X = 0.55  # horizontal text padding, as a fraction of pill height


def badge_logo(base: Image.Image) -> Image.Image:
    """Composite a BLE pill badge onto the bottom-right of a wordmark,
    widening the canvas to fit it rather than overlapping any ink."""
    w, h = base.size
    work_h = h * SS
    work_w = int(w * (1 + LOGO_CANVAS_EXTEND)) * SS
    src = base.convert("RGBA").resize((w * SS, work_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (work_w, work_h), (0, 0, 0, 0))
    canvas.paste(src, (0, 0), src)
    d = ImageDraw.Draw(canvas)

    pill_h = int(work_h * LOGO_PILL_HEIGHT)
    text = "BLE"
    f = _font(int(pill_h * 0.62))
    left, top, right, bottom = d.textbbox((0, 0), text, font=f)
    text_w = right - left
    pad_x = int(pill_h * LOGO_PILL_PAD_X)
    pill_w = text_w + pad_x * 2

    x1 = work_w  # flush against the (widened) canvas's right edge
    x0 = x1 - pill_w
    y1 = work_h - int(work_h * LOGO_PILL_BOTTOM_MARGIN)
    y0 = y1 - pill_h

    d.rounded_rectangle([x0, y0, x1, y1], radius=pill_h // 2, fill=ACCENT)
    d.text(
        ((x0 + x1) / 2 - (right + left) / 2, (y0 + y1) / 2 - (bottom + top) / 2),
        text,
        font=f,
        fill=ACCENT_TEXT,
    )

    return canvas.resize((int(work_w / SS), h), Image.LANCZOS)


if __name__ == "__main__":
    import sys

    src, dest = sys.argv[1], sys.argv[2]
    for base_name, out_name in (
        ("icon.png", "icon.png"),
        ("icon@2x.png", "icon@2x.png"),
        ("dark_icon.png", "dark_icon.png"),
        ("dark_icon@2x.png", "dark_icon@2x.png"),
    ):
        out = badge(Image.open(f"{src}/{base_name}"))
        out.save(f"{dest}/{out_name}", optimize=True)
        print(f"wrote {dest}/{out_name} {out.size}")

    # The wordmark's @2x is resized from the 1x rather than rendered
    # independently - rendering both from source rounds text/pill geometry
    # separately and the two land a couple of pixels apart, which isn't what
    # @2x is supposed to mean (see cync-lan's own note on this).
    for base_name, out_1x, out_2x in (
        ("logo.png", "logo.png", "logo@2x.png"),
        ("dark_logo.png", "dark_logo.png", "dark_logo@2x.png"),
    ):
        out = badge_logo(Image.open(f"{src}/{base_name}"))
        out.save(f"{dest}/{out_1x}", optimize=True)
        print(f"wrote {dest}/{out_1x} {out.size}")
        out.resize((out.size[0] * 2, out.size[1] * 2), Image.LANCZOS).save(
            f"{dest}/{out_2x}", optimize=True
        )
        print(f"wrote {dest}/{out_2x} {(out.size[0] * 2, out.size[1] * 2)}")

    # Legibility proof at the size HACS actually renders.
    for name in ("icon.png", "dark_icon.png"):
        Image.open(f"{dest}/{name}").resize((32, 32), Image.LANCZOS).resize(
            (256, 256), Image.NEAREST
        ).save(f"{dest}/preview32_{name}")
        print(f"wrote {dest}/preview32_{name}")
