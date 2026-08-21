#!/usr/bin/env python3
"""Draw the Opus app icon and write it in every format the builds need.

Run from anywhere:  python3 packaging/make_icons.py

Writes, next to this file:
    opus.png   1024px master, used for the macOS .icns and anywhere else
    opus.ico   multi-resolution Windows icon
    opus.icns  macOS icon -- via iconutil on macOS, or Pillow if it can

The drawing is deliberately literal: a sheet of music with a licence bar
stamped across the top, which is the one thing the tool does.
"""

import subprocess
import shutil
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Missing dependency 'Pillow'. Install it with: pip3 install Pillow")

HERE = Path(__file__).resolve().parent

INK = (20, 22, 42, 255)        # --ink from the design system
PAPER = (255, 255, 255, 255)
STAMP = (156, 43, 58, 255)     # --stamp
RULE = (150, 152, 170, 255)

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def draw(px):
    """Render the icon at px by px."""
    s = px / 256.0

    def u(v):
        return int(round(v * s))

    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([u(34), u(16), u(222), u(240)], radius=u(12),
                        fill=PAPER, outline=INK, width=max(1, u(7)))
    d.rectangle([u(34), u(52), u(222), u(84)], fill=STAMP)

    for top in (u(116), u(180)):
        for i in range(5):
            y = top + i * u(11)
            d.line([u(62), y, u(194), y], fill=RULE, width=max(1, u(3)))

    for cx, cy in ((u(84), u(149)), (u(120), u(138)), (u(156), u(160)),
                   (u(84), u(213)), (u(128), u(202)), (u(168), u(224))):
        r = u(11)
        d.ellipse([cx - r, cy - int(r * .78), cx + r, cy + int(r * .78)], fill=INK)
        d.line([cx + r - u(2), cy, cx + r - u(2), cy - u(34)],
               fill=INK, width=max(1, u(4)))
    return img


def write_icns(master):
    """Write opus.icns. Returns True if a file was produced."""
    target = HERE / "opus.icns"

    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "opus.iconset"
            iconset.mkdir()
            for size in ICNS_SIZES:
                draw(size).save(iconset / "icon_{0}x{0}.png".format(size))
                draw(size * 2).save(iconset / "icon_{0}x{0}@2x.png".format(size))
            subprocess.run(["iconutil", "-c", "icns", str(iconset),
                            "-o", str(target)], check=True)
        return True

    try:                                    # Pillow can do it on some builds
        master.save(target, format="ICNS")
        return True
    except Exception:
        return False


def main():
    master = draw(1024)
    master.save(HERE / "opus.png")

    imgs = [draw(p) for p in ICO_SIZES]
    imgs[-1].save(HERE / "opus.ico",
                  sizes=[(p, p) for p in ICO_SIZES],
                  append_images=imgs[:-1])

    made_icns = write_icns(master)

    print("wrote {}".format(HERE / "opus.png"))
    print("wrote {}".format(HERE / "opus.ico"))
    if made_icns:
        print("wrote {}".format(HERE / "opus.icns"))
    else:
        print("skipped opus.icns -- needs macOS (iconutil). The macOS CI job "
              "regenerates icons before it builds, so this is only a problem "
              "if you are building a .app by hand off-platform.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
