"""Build the animated WebP walkthroughs of the two projects.

Each clip is made from the real screens the portfolio publishes (the Solar
frames exported from Figma with synthetic data, the OrderDi captures from the
live app). A cursor glides to the real navigation control, clicks, and the next
screen cross-fades in while it settles from 1.03 to 1.0 around its focus point.
Holds are still, so the animated WebP encoder merges them and the files stay small.

Frames are composed at 1920x1200 (2x) and piped to ffmpeg, which scales them to
960x600 with Lanczos and encodes with libwebp_anim.

Run from the repository root:
  python tools/build_demos.py --portfolio ../kittiphatduangmanee.github.io
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "tools" / "fonts" / "PlusJakartaSans[wght].ttf"
MAX_BYTES = int(1.8 * 1024 * 1024)

# Output is 960x600; everything here is drawn at 2x.
S = 2
W, H = 960 * S, 600 * S
BAR = 40 * S
CONTENT_H = H - BAR
RADIUS = 12 * S

# Site tokens (design/reference/spec.md via src/styles/tokens.css).
CANVAS = (250, 249, 246)
SURFACE = (255, 255, 255)
WELL = (243, 241, 236)
HAIRLINE = (228, 226, 221)
HAIRLINE_STRONG = (197, 192, 184)
INK = (20, 21, 23)
INK_2 = (82, 88, 102)
TEAL = (15, 118, 110)


def bezier_ease(x1: float, y1: float, x2: float, y2: float):
    """CSS cubic-bezier timing function."""

    def curve(t: float, a: float, b: float) -> float:
        return 3 * a * (1 - t) ** 2 * t + 3 * b * (1 - t) * t ** 2 + t ** 3

    def ease(x: float) -> float:
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if curve(mid, x1, x2) < x:
                lo = mid
            else:
                hi = mid
        return curve((lo + hi) / 2, y1, y2)

    return ease


EASE = bezier_ease(0.22, 1, 0.36, 1)  # the site's one curve
EASE_IN_OUT = bezier_ease(0.42, 0, 0.58, 1)


def font(size: float, weight: int = 500) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT), int(round(size)))
    f.set_variation_by_axes([weight])
    return f


def supersampled(size: tuple[int, int], draw, factor: int = 4) -> Image.Image:
    """Draw on a larger RGBA canvas and scale down, for smooth edges."""
    big = Image.new("RGBA", (size[0] * factor, size[1] * factor), (0, 0, 0, 0))
    draw(ImageDraw.Draw(big), factor)
    return big.resize(size, Image.LANCZOS)


# --------------------------------------------------------------------------
# Static chrome
# --------------------------------------------------------------------------


def title_bar(title: str) -> Image.Image:
    """The site's window chrome: well colour, three quiet dots, centred title."""

    def draw(d: ImageDraw.ImageDraw, k: int):
        d.rectangle((0, 0, W * k, BAR * k), fill=WELL + (255,))
        d.rectangle((0, BAR * k - S * k, W * k, BAR * k), fill=HAIRLINE + (255,))
        for i in range(3):
            cx = (16 + 5 + i * 16) * S * k
            cy = BAR * k / 2
            r = 5 * S * k
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=HAIRLINE_STRONG + (255,))

    bar = supersampled((W, BAR), draw)
    d = ImageDraw.Draw(bar)
    f = font(15 * S, 500)
    d.text((W / 2, BAR / 2 - S / 2), title, font=f, fill=INK_2, anchor="mm")
    return bar


def badge(label: str) -> tuple[Image.Image, tuple[int, int], tuple[int, int]]:
    """Pill on the right of the title bar. Returns image, its position and the dot centre."""
    f = font(12.5 * S, 500)
    text_w = f.getlength(label)
    pad, dot_gap, dot_d, h = 10 * S, 7 * S, 7 * S, 24 * S
    w = int(pad + dot_d + dot_gap + text_w + pad)

    def draw(d: ImageDraw.ImageDraw, k: int):
        d.rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=h * k / 2, fill=SURFACE + (255,),
                            outline=HAIRLINE + (255,), width=S * k)
        cx, cy, r = (pad + dot_d / 2) * k, h * k / 2, dot_d / 2 * k
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=TEAL + (255,))

    pill = supersampled((w, h), draw)
    ImageDraw.Draw(pill).text((pad + dot_d + dot_gap, h / 2), label, font=f, fill=INK_2, anchor="lm")
    x = W - 16 * S - w
    y = (BAR - h) // 2
    return pill, (x, y), (int(x + pad + dot_d / 2), int(y + h / 2))


def window_mask_and_border() -> tuple[Image.Image, Image.Image]:
    def mask_draw(d: ImageDraw.ImageDraw, k: int):
        d.rounded_rectangle((0, 0, W * k - 1, H * k - 1), radius=RADIUS * k, fill=(255, 255, 255, 255))

    def border_draw(d: ImageDraw.ImageDraw, k: int):
        d.rounded_rectangle((0, 0, W * k - 1, H * k - 1), radius=RADIUS * k, outline=HAIRLINE + (255,), width=S * k)

    mask = supersampled((W, H), mask_draw).getchannel("A")
    border = supersampled((W, H), border_draw)
    return mask, border


def cursor_sprite() -> tuple[Image.Image, tuple[int, int]]:
    """Arrow cursor, white with an ink outline and a soft shadow. Returns sprite and tip offset."""
    pts = [(0, 0), (0, 21), (5.3, 16), (8.8, 24), (11.9, 22.7), (8.5, 15), (15.4, 15)]
    scale = 1.15 * S  # 1x arrow about 28px tall
    k = 8
    margin = 6 * S
    size = (int(17 * scale + 2 * margin), int(26 * scale + 2 * margin))

    def draw(d: ImageDraw.ImageDraw, kk: int):
        poly = [((margin + x * scale) * kk, (margin + y * scale) * kk) for x, y in pts]
        d.polygon(poly, fill=SURFACE + (255,), outline=INK + (255,), width=int(1.5 * S * kk))

    arrow = supersampled(size, draw, factor=k)
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    alpha = arrow.getchannel("A").point(lambda a: int(a * 0.28))
    shadow.putalpha(alpha)
    shadow = ImageChops.offset(shadow, 0, int(1.5 * S)).filter(ImageFilter.GaussianBlur(2 * S))
    out = Image.alpha_composite(shadow, arrow)
    return out, (margin, margin)


# --------------------------------------------------------------------------
# Screens and timeline
# --------------------------------------------------------------------------


@dataclass
class Screen:
    file: str
    title: str
    focus: tuple[float, float]  # in 1440 wide screen coordinates


@dataclass
class Clip:
    name: str
    project: str
    fps: int
    seg: int  # frames per screen
    hold: int  # frames before the cursor moves
    glide: int
    fade: int
    quality: int
    badge: str
    title: str | None  # fixed window title, or None to show each screen's title
    screens: list[Screen]
    targets: list[tuple[float, float]]  # nav control clicked on screen i, 1440 coordinates


CLIPS = [
    Clip(
        name="solar-monitoring-demo.webp",
        project="solar",
        fps=15, seg=27, hold=12, glide=7, fade=7, quality=80,
        badge="Synthetic data",
        title="Solar String Monitoring Dashboard · Telemetry & Alarm View",
        screens=[
            Screen("screen-1.webp", "Plant overview", (860, 270)),
            Screen("screen-3.webp", "String performance", (860, 640)),
            Screen("screen-5.webp", "Event history", (860, 300)),
        ],
        # All-site on the overview, Alarm on the heatmap, Dashboard on the history.
        targets=[(110, 346), (104, 510), (136, 252)],
    ),
    Clip(
        name="orderdi-pos-demo.webp",
        project="orderdi",
        fps=12, seg=22, hold=9, glide=6, fade=6, quality=78,
        badge="Live in production",
        title=None,
        screens=[
            Screen("screen-1.webp", "Table floor plan", (540, 600)),
            Screen("screen-2.webp", "Counter sale", (480, 400)),
            Screen("screen-4.webp", "Sales report", (720, 400)),
        ],
        # The counter sale button, the report tab, the floor plan tab.
        targets=[(1094, 106), (878, 32), (488, 32)],
    ),
]


def to_canvas(p: tuple[float, float]) -> tuple[float, float]:
    """1440 wide screen coordinates to 2x canvas pixels."""
    k = W / 1440
    return p[0] * k, BAR + p[1] * k


class Renderer:
    def __init__(self, clip: Clip, portfolio: Path):
        self.clip = clip
        src = portfolio / "public" / "images" / "projects" / clip.project
        self.sources = []
        for screen in clip.screens:
            path = src / screen.file
            if not path.exists():
                sys.exit(f"Missing {path}")
            self.sources.append(Image.open(path).convert("RGB"))
        self.bars = [title_bar(clip.title or s.title) for s in clip.screens]
        self.pill, self.pill_xy, self.dot = badge(clip.badge)
        self.mask, self.border = window_mask_and_border()
        self.cursor, self.tip = cursor_sprite()
        self.rest = [self._content(i, 1.0) for i in range(len(clip.screens))]

    def _content(self, i: int, zoom: float) -> Image.Image:
        """Screen i scaled to the window width, zoomed about its focus point."""
        src = self.sources[i]
        k = src.width / W  # source pixels per canvas pixel
        view_w, view_h = W * k / zoom, CONTENT_H * k / zoom
        fx, fy = (c * src.width / 1440 for c in self.clip.screens[i].focus)
        x0 = fx - (fx / zoom)
        y0 = fy - (fy / zoom)
        box = (x0, y0, x0 + view_w, y0 + view_h)
        return src.resize((W, CONTENT_H), Image.LANCZOS, box=box)

    def _frame_base(self, i: int, content: Image.Image) -> Image.Image:
        frame = Image.new("RGB", (W, H), CANVAS)
        frame.paste(content, (0, BAR))
        frame.paste(self.bars[i], (0, 0), self.bars[i])
        return frame

    def frame(self, n: int) -> Image.Image:
        c = self.clip
        count = len(c.screens)
        seg, local = divmod(n, c.seg)
        cur, nxt = seg % count, (seg + 1) % count
        press_at = c.hold + c.glide
        fade_at = press_at + 1

        base = self._frame_base(cur, self.rest[cur])
        if local >= fade_at:
            t = (local - fade_at + 1) / c.fade
            alpha = EASE_IN_OUT(t)
            zoom = 1 + 0.03 * (1 - EASE(t))
            incoming = self._frame_base(nxt, self._content(nxt, zoom) if t < 1 else self.rest[nxt])
            base = Image.blend(base, incoming, alpha)

        frame = base.convert("RGBA")
        frame.alpha_composite(self.pill, self.pill_xy)
        self._badge_pulse(frame, n)

        # Cursor: rests where it last clicked, glides to this screen's target, presses.
        start = to_canvas(c.targets[(seg - 1) % count])
        end = to_canvas(c.targets[cur])
        if local < c.hold:
            pos, pressed = start, False
        elif local < press_at:
            t = EASE((local - c.hold + 1) / c.glide)
            pos, pressed = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t), False
        else:
            pos, pressed = end, local in (press_at, press_at + 1)
        if local >= press_at:
            self._ripple(frame, end, (local - press_at) / (c.fps * 0.45))
        self._cursor(frame, pos, pressed)

        frame.alpha_composite(self.border)
        # Outside the rounded corners: transparent, with the border colour
        # underneath so the downscale never pulls a dark fringe in.
        out = Image.new("RGBA", (W, H), HAIRLINE + (0,))
        out.paste(frame, (0, 0), self.mask)
        out.putalpha(self.mask)
        return out

    def _badge_pulse(self, frame: Image.Image, n: int):
        period = self.clip.seg  # one pulse per screen, so the loop is seamless
        t = (n % period) / period
        r = (3.5 + 7 * EASE(t)) * S
        a = int(110 * (1 - t))
        if a <= 0:
            return
        size = int(12 * S * 2)
        cx, cy = self.dot

        def draw(d: ImageDraw.ImageDraw, k: int):
            c = size * k / 2
            d.ellipse((c - r * k, c - r * k, c + r * k, c + r * k), outline=TEAL + (a,), width=int(1.5 * S * k))

        ring = supersampled((size, size), draw)
        frame.alpha_composite(ring, (cx - size // 2, cy - size // 2))

    def _ripple(self, frame: Image.Image, at: tuple[float, float], t: float):
        if t >= 1:
            return
        r = (6 + 20 * EASE(t)) * S
        a = int(150 * (1 - t))
        size = int(56 * S)

        def draw(d: ImageDraw.ImageDraw, k: int):
            c = size * k / 2
            d.ellipse((c - r * k, c - r * k, c + r * k, c + r * k), fill=TEAL + (a // 4,),
                      outline=TEAL + (a,), width=int(2 * S * k))

        ring = supersampled((size, size), draw)
        frame.alpha_composite(ring, (int(at[0] - size / 2), int(at[1] - size / 2)))

    def _cursor(self, frame: Image.Image, pos: tuple[float, float], pressed: bool):
        sprite = self.cursor
        tip = self.tip
        if pressed:
            w, h = sprite.size
            sprite = sprite.resize((int(w * 0.9), int(h * 0.9)), Image.LANCZOS)
            tip = (int(tip[0] * 0.9), int(tip[1] * 0.9))
        frame.alpha_composite(sprite, (int(round(pos[0] - tip[0])), int(round(pos[1] - tip[1]))))


def encode(clip: Clip, renderer: Renderer, out: Path, quality: int, stills: Path | None) -> int:
    total = clip.seg * len(clip.screens)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-framerate", str(clip.fps), "-i", "-",
        "-vf", "scale=960:600:flags=lanczos,format=yuva420p",
        "-c:v", "libwebp_anim", "-lossless", "0", "-compression_level", "4",
        "-q:v", str(quality), "-preset", "picture", "-loop", "0", "-an", str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    keep = {0, clip.hold + clip.glide, clip.hold + clip.glide + 1 + clip.fade // 2}
    for n in range(total):
        img = renderer.frame(n)
        if stills is not None and n in keep:
            img.resize((960, 600), Image.LANCZOS).save(stills / f"{out.stem}-{n:03d}.png")
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit(f"ffmpeg failed for {out.name}")
    return out.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--portfolio", type=Path, default=ROOT.parent / "kittiphatduangmanee.github.io")
    ap.add_argument("--stills", type=Path, help="folder for a few PNG stills to review")
    ap.add_argument("--only", help="build one clip by file name")
    args = ap.parse_args()
    if not FONT.exists():
        sys.exit(f"Missing {FONT}. See tools/build_svgs.py for the font setup.")
    if args.stills:
        args.stills.mkdir(parents=True, exist_ok=True)

    for clip in CLIPS:
        if args.only and clip.name != args.only:
            continue
        renderer = Renderer(clip, args.portfolio)
        out = ROOT / "assets" / clip.name
        quality = clip.quality
        while True:
            size = encode(clip, renderer, out, quality, args.stills)
            if size < MAX_BYTES or quality <= 70:
                break
            print(f"{clip.name}: {size / 1048576:.2f} MB at q {quality}, trying q {quality - 4}")
            quality -= 4
        frames = clip.seg * len(clip.screens)
        status = "ok" if size < MAX_BYTES else "OVER BUDGET"
        print(f"{clip.name:28} {size / 1048576:5.2f} MB  q {quality}  {frames} frames  "
              f"{clip.fps} fps  {frames / clip.fps:.2f} s  {status}")


if __name__ == "__main__":
    main()
