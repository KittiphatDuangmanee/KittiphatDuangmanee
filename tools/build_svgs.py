"""Build the banner and system flow SVGs in light and dark.

Text is set in the portfolio's own fonts (Plus Jakarta Sans, JetBrains Mono)
and converted to outlines, because an SVG shown through <img> on GitHub cannot
load web fonts. Both themes come from one layout, so they never drift apart.

Setup (once):
  1. Put PlusJakartaSans[wght].ttf and JetBrainsMono[wght].ttf from
     https://github.com/google/fonts (ofl/plusjakartasans, ofl/jetbrainsmono)
     into tools/fonts/ (gitignored).
  2. pip install -r tools/requirements.txt

Run from the repository root:
  python tools/build_svgs.py
"""

from __future__ import annotations

import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "tools" / "fonts"
ASSETS = ROOT / "assets"

# Exact tokens from the brief. "muted" is the tagline: secondary ink at 82%,
# which keeps it at or above 4.5:1 on the canvas in both themes.
PALETTES = {
    "light": {
        "canvas": "#FAF9F6",
        "border": "#E4E2DD",
        "ink": "#141517",
        "ink2": "#525866",
        "dot": "#D6D2C9",
        "teal": "#0F766E",
        "amber": "#F59E0B",
    },
    "dark": {
        "canvas": "#0D1117",
        "border": "#30363D",
        "ink": "#F0F6FC",
        "ink2": "#8B949E",
        "dot": "#21262D",
        "teal": "#2DD4BF",
        "amber": "#FBBF24",
    },
}
MUTED_OPACITY = 0.82
RAISED_OPACITY = 0.28  # the site's emboss.raisedOpacity
EASE = "cubic-bezier(0.22, 1, 0.36, 1)"  # the site's one easing curve
EASE_IN_OUT = "0.42 0 0.58 1"  # CSS ease-in-out, for SMIL keySplines

ALLOWED = {
    "svg", "defs", "pattern", "linearGradient", "radialGradient", "stop",
    "rect", "circle", "path", "line", "g", "text", "style", "animate",
    "animateMotion",
}


def num(v: float) -> str:
    s = f"{v:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return "0" if s == "-0" else s


class Font:
    """One weight of a variable font: shapes with HarfBuzz, outlines with fontTools."""

    def __init__(self, file: str, weight: int):
        path = FONTS / file
        if not path.exists():
            sys.exit(f"Missing font {path}. See the setup notes at the top of this file.")
        self.tt = TTFont(path)
        self.glyphs = self.tt.getGlyphSet(location={"wght": weight})
        self.order = self.tt.getGlyphOrder()
        self.upem = self.tt["head"].unitsPerEm
        self.hb = hb.Font(hb.Face(path.read_bytes()))
        self.hb.set_variations({"wght": weight})

    def layout(self, text: str, size: float, tracking: float):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb, buf, {"kern": True, "liga": True})
        scale = size / self.upem
        placed, x = [], 0.0
        count = len(buf.glyph_infos)
        for i, (info, pos) in enumerate(zip(buf.glyph_infos, buf.glyph_positions)):
            placed.append((self.order[info.codepoint], x + pos.x_offset * scale, pos.y_offset * scale))
            x += pos.x_advance * scale
            if i < count - 1:
                x += tracking
        return placed, x

    def width(self, text: str, size: float, tracking: float = 0.0) -> float:
        return self.layout(text, size, tracking)[1]

    def path(self, text: str, x: float, y: float, size: float, tracking: float = 0.0, anchor: str = "start"):
        """Outline of one line of text with its baseline at y. Returns (d, left, width)."""
        placed, width = self.layout(text, size, tracking)
        if anchor == "middle":
            x -= width / 2
        elif anchor == "end":
            x -= width
        pen = SVGPathPen(self.glyphs, ntos=num)
        scale = size / self.upem
        for name, gx, gy in placed:
            self.glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x + gx, y - gy)))
        return pen.getCommands(), x, width


class Fonts:
    def __init__(self):
        self.bold = Font("PlusJakartaSans[wght].ttf", 700)
        self.medium = Font("PlusJakartaSans[wght].ttf", 500)
        self.regular = Font("PlusJakartaSans[wght].ttf", 400)
        self.mono = Font("JetBrainsMono[wght].ttf", 500)


class Slots:
    """Collects every text line with the box it must fit in, then checks them."""

    def __init__(self, name: str):
        self.name = name
        self.problems: list[str] = []

    def fit(self, label: str, left: float, width: float, lo: float, hi: float):
        if left < lo - 0.01 or left + width > hi + 0.01:
            self.problems.append(f"{self.name}: '{label}' spans {left:.1f}..{left + width:.1f}, slot {lo}..{hi}")


def text(font: Font, slots: Slots, label: str, x: float, y: float, size: float, fill: str,
         lo: float, hi: float, tracking: float = 0.0, anchor: str = "start", extra: str = "") -> str:
    d, left, width = font.path(label, x, y, size, tracking, anchor)
    slots.fit(label, left, width, lo, hi)
    return f'<path fill="{fill}"{extra} d="{d}"/>'


def smooth(points: list[tuple[float, float]]) -> str:
    """Catmull-Rom through the points, written as cubic Beziers."""
    d = [f"M{num(points[0][0])} {num(points[0][1])}"]
    for i in range(len(points) - 1):
        p0 = points[max(i - 1, 0)]
        p1, p2 = points[i], points[i + 1]
        p3 = points[min(i + 2, len(points) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{num(c1[0])} {num(c1[1])} {num(c2[0])} {num(c2[1])} {num(p2[0])} {num(p2[1])}")
    return "".join(d)


def dot_pattern(p: dict, y_offset: float) -> str:
    # 24px cells, dot in the middle: 50 columns fill 1200 exactly, so the
    # grid has the same margin on both sides.
    return (
        f'<pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse" y="{num(y_offset)}">'
        f'<circle cx="12" cy="12" r="1.15" fill="{p["dot"]}"/></pattern>'
    )


def card(width: int, height: int, p: dict, layer: str) -> str:
    w, h = width - 1, height - 1
    if layer == "fill":
        return (
            f'<rect x=".5" y=".5" width="{w}" height="{h}" rx="12" fill="{p["canvas"]}"/>'
            f'<rect x=".5" y=".5" width="{w}" height="{h}" rx="12" fill="url(#dots)"/>'
        )
    return f'<rect x=".5" y=".5" width="{w}" height="{h}" rx="12" fill="none" stroke="{p["border"]}"/>'


# --------------------------------------------------------------------------
# Banner
# --------------------------------------------------------------------------

BANNER_W, BANNER_H = 1200, 260
DOT_Y0 = -2  # dots at y = 10, 34, ... 250: 10px from top and bottom

# Spotlight A carries the relief. It drifts through the open band between the
# text and the telemetry frame. B is a warm light low behind the tagline.
SPOT_A = {"cx": 640, "cy": 104, "w": 440, "h": 300, "dx": 84, "dy": 60}
SPOT_B = {"cx": 330, "cy": 246, "w": 560, "h": 240, "dx": 190, "dy": -18}
RELIEF_R = 210
RELIEF_X = (420, 960)


def banner(p: dict, f: Fonts, theme: str) -> tuple[str, Slots]:
    s = Slots(f"banner-{theme}")
    a, b = SPOT_A, SPOT_B

    raised = []
    for col in range(50):
        x = 12 + col * 24
        if not RELIEF_X[0] <= x <= RELIEF_X[1]:
            continue
        for row in range(11):
            y = 10 + row * 24
            raised.append(f"M{x - 2} {y}a2 2 0 1 0 4 0a2 2 0 1 0-4 0")

    ax2, ay2 = a["cx"] + a["dx"], a["cy"] + a["dy"]
    spline = f"{EASE_IN_OUT};{EASE_IN_OUT}"

    css = f"""
.t{{animation:rise .7s {EASE} both}}
.t1{{animation-delay:.1s}}.t2{{animation-delay:.19s}}.t3{{animation-delay:.28s}}.t4{{animation-delay:.37s}}.t5{{animation-delay:.46s}}
@keyframes rise{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:translateY(0)}}}}
.sa{{animation:da 14s ease-in-out infinite alternate}}
.sb{{animation:db 14s ease-in-out -5s infinite alternate}}
@keyframes da{{to{{transform:translate({a["dx"]}px,{a["dy"]}px)}}}}
@keyframes db{{to{{transform:translate({b["dx"]}px,{b["dy"]}px)}}}}
@media (prefers-reduced-motion:reduce){{.t,.sa,.sb{{animation:none}}.motion{{display:none}}}}
"""

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BANNER_W} {BANNER_H}" width="100%" '
        f'role="img" aria-label="Kittiphat Duangmanee (Mark), Full-Stack Developer in Bangkok, Thailand">',
        f"<style>{css.strip()}</style>",
        "<defs>",
        dot_pattern(p, DOT_Y0),
        f'<radialGradient id="ga"><stop offset="0" stop-color="{p["teal"]}"/>'
        f'<stop offset=".55" stop-color="{p["teal"]}" stop-opacity=".35"/>'
        f'<stop offset="1" stop-color="{p["teal"]}" stop-opacity="0"/></radialGradient>',
        f'<radialGradient id="gb"><stop offset="0" stop-color="{p["amber"]}"/>'
        f'<stop offset=".55" stop-color="{p["amber"]}" stop-opacity=".3"/>'
        f'<stop offset="1" stop-color="{p["amber"]}" stop-opacity="0"/></radialGradient>',
        # The relief: raised dots show through this gradient, which follows
        # spotlight A on the same 14s ease-in-out alternate clock.
        f'<radialGradient id="relief" gradientUnits="userSpaceOnUse" cx="{a["cx"]}" cy="{a["cy"]}" r="{RELIEF_R}">'
        f'<animate attributeName="cx" values="{a["cx"]};{ax2};{a["cx"]}" keyTimes="0;.5;1" calcMode="spline" keySplines="{spline}" dur="28s" repeatCount="indefinite"/>'
        f'<animate attributeName="cy" values="{a["cy"]};{ay2};{a["cy"]}" keyTimes="0;.5;1" calcMode="spline" keySplines="{spline}" dur="28s" repeatCount="indefinite"/>'
        f'<stop offset="0" stop-color="{p["ink"]}"/>'
        f'<stop offset=".45" stop-color="{p["ink"]}" stop-opacity=".45"/>'
        f'<stop offset=".8" stop-color="{p["ink"]}" stop-opacity="0"/></radialGradient>',
        f'<radialGradient id="halo"><stop offset="0" stop-color="{p["teal"]}" stop-opacity=".55"/>'
        f'<stop offset="1" stop-color="{p["teal"]}" stop-opacity="0"/></radialGradient>',
        "</defs>",
        card(BANNER_W, BANNER_H, p, "fill"),
        f'<rect class="sa" x="{a["cx"] - a["w"] // 2}" y="{a["cy"] - a["h"] // 2}" width="{a["w"]}" height="{a["h"]}" fill="url(#ga)" opacity=".14"/>',
        f'<rect class="sb" x="{b["cx"] - b["w"] // 2}" y="{b["cy"] - b["h"] // 2}" width="{b["w"]}" height="{b["h"]}" fill="url(#gb)" opacity=".14"/>',
        f'<path class="motion" fill="url(#relief)" opacity="{RAISED_OPACITY}" d="{"".join(raised)}"/>',
    ]

    # Left text block. Every line must end before the telemetry frame.
    text_hi = 760
    out.append('<g class="t t1">' + text(
        f.mono, s, "FULL-STACK DEVELOPER  •  BANGKOK, THAILAND", 56, 72, 12, p["ink2"], 56, text_hi, tracking=2) + "</g>")
    out.append('<g class="t t2">' + text(
        f.bold, s, "Kittiphat Duangmanee (Mark)", 56, 128, 38, p["ink"], 56, text_hi, tracking=-0.8) + "</g>")
    out.append('<g class="t t3">' + text(
        f.medium, s, "B.Eng. in Computer Engineering, Kasetsart University (2026)", 56, 166, 16, p["ink2"], 56, text_hi) + "</g>")
    out.append('<g class="t t4">' + text(
        f.regular, s, "Scalable Backend APIs • Time-Series Telemetry • Real-Time Web Dashboards", 56, 202, 14,
        p["ink2"], 56, text_hi, extra=f' fill-opacity="{MUTED_OPACITY}"') + "</g>")

    out.append('<g class="t t5">' + telemetry_frame(p, f, s) + "</g>")
    out.append(card(BANNER_W, BANNER_H, p, "stroke"))
    out.append("</svg>")
    return "".join(out), s


def telemetry_frame(p: dict, f: Fonts, s: Slots) -> str:
    x0, x1, y0, y1 = 780, 1144, 40, 220
    px0, px1 = 800, 1124  # plot area
    out = [
        f'<rect x="{x0 + .5}" y="{y0 + .5}" width="{x1 - x0 - 1}" height="{y1 - y0 - 1}" rx="10" '
        f'fill="{p["canvas"]}" fill-opacity=".92" stroke="{p["border"]}"/>',
        text(f.mono, s, "STRING TELEMETRY", px0, 64, 10.5, p["ink2"], px0, px1, tracking=1.5),
        text(f.mono, s, "5 MIN", px1, 64, 10.5, p["ink2"], px0, px1, tracking=1.5, anchor="end",
             extra=' fill-opacity=".72"'),
        f'<path d="M{x0 + 1} 76.5H{x1 - 1}" stroke="{p["border"]}"/>',
    ]
    for gy in (96, 120, 144):
        out.append(f'<path d="M{px0} {gy}H{px1}" stroke="{p["border"]}" stroke-dasharray="1 5" stroke-linecap="round"/>')

    def trace(base: float, amp: float, phase: float, freq: float, dip_at: float | None = None):
        pts = []
        steps = 27
        for i in range(steps + 1):
            x = px0 + (px1 - px0) * i / steps
            t = i / steps
            y = base + amp * math.sin(t * freq * math.tau + phase) + amp * 0.35 * math.sin(t * 13.0 + phase * 2)
            if dip_at is not None:
                y += 15 * math.exp(-((x - dip_at) ** 2) / (2 * 16 ** 2))
            pts.append((x, y))
        return pts

    main = trace(106, 7, 0.4, 1.6)
    second = trace(121, 6, 1.9, 1.3)
    third = trace(134, 5, 3.1, 1.1, dip_at=1012)
    dip = [pt for pt in third if 966 <= pt[0] <= 1060]

    out.append(f'<path d="{smooth(second)}" fill="none" stroke="{p["ink2"]}" stroke-opacity=".5" stroke-width="1.5" stroke-linecap="round"/>')
    out.append(f'<path d="{smooth(third)}" fill="none" stroke="{p["ink2"]}" stroke-opacity=".34" stroke-width="1.5" stroke-linecap="round"/>')
    out.append(f'<path d="{smooth(dip)}" fill="none" stroke="{p["amber"]}" stroke-width="1.75" stroke-linecap="round"/>')
    main_d = smooth(main)
    out.append(f'<path d="{main_d}" fill="none" stroke="{p["teal"]}" stroke-width="2" stroke-linecap="round"/>')

    # Pulse along the teal string. It fades at both ends so the loop never jumps.
    out.append(
        '<g class="motion" opacity="0">'
        f'<circle r="11" fill="url(#halo)"/><circle r="3.5" fill="{p["teal"]}"/>'
        f'<animateMotion dur="5s" repeatCount="indefinite" path="{main_d}"/>'
        '<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.08;.92;1" dur="5s" repeatCount="indefinite"/>'
        "</g>"
    )

    out.append(f'<path d="M{x0 + 1} 166.5H{x1 - 1}" stroke="{p["border"]}"/>')

    # Pipeline status: each node lifts as the pulse crosses its third of the trace.
    labels = [("INGEST", p["teal"]), ("EVALUATE", p["teal"]), ("DISPATCH", p["amber"])]
    size, track = 10, 1.2
    widths = [8 + 8 + f.mono.width(lbl, size, track) for lbl, _ in labels]
    gap = (px1 - px0 - sum(widths)) / 2
    cy = 193
    x = px0
    peaks = [(0, 1 / 6, 1 / 3), (1 / 3, 1 / 2, 2 / 3), (2 / 3, 5 / 6, 1)]
    for i, ((lbl, color), w) in enumerate(zip(labels, widths)):
        cx = x + 4
        lo, peak, hi = peaks[i]
        if i == 0:
            values, times = ".2;1;.2;.2", f"0;{peak:.3f};{hi:.3f};1"
            splines = ";".join([EASE_IN_OUT] * 3)
        elif i == 2:
            values, times = ".2;.2;1;.2", f"0;{lo:.3f};{peak:.3f};1"
            splines = ";".join([EASE_IN_OUT] * 3)
        else:
            values, times = ".2;.2;1;.2;.2", f"0;{lo:.3f};{peak:.3f};{hi:.3f};1"
            splines = ";".join([EASE_IN_OUT] * 4)
        out.append(
            f'<circle class="motion" cx="{num(cx)}" cy="{cy}" r="7.5" fill="{color}" opacity=".2">'
            f'<animate attributeName="opacity" values="{values}" keyTimes="{times}" calcMode="spline" '
            f'keySplines="{splines}" dur="5s" repeatCount="indefinite"/></circle>'
        )
        out.append(f'<circle cx="{num(cx)}" cy="{cy}" r="4" fill="{color}"/>')
        out.append(text(f.mono, s, lbl, x + 16, cy + 3.6, size, p["ink2"], px0, px1, tracking=track))
        if i < 2:
            start = x + w + 8
            end = x + w + gap - 8
            out.append(f'<path d="M{num(start)} {cy}H{num(end)}" stroke="{p["border"]}" stroke-width="1.25"/>')
        x += w + gap
    return "".join(out)


# --------------------------------------------------------------------------
# System flow strip
# --------------------------------------------------------------------------

FLOW_W, FLOW_H = 1200, 170
NODE_XS = (40, 345, 650, 955)
NODE_W, NODE_Y, NODE_H = 205, 14, 142
EDGE_Y = NODE_Y + NODE_H / 2
HOP = 0.8  # seconds per connector
CYCLE = HOP * 3

NODES = [
    {"title": ("Client &", "Telemetry Edge"), "stack": ("React • TypeScript", "IoT Strings"), "icon": "client"},
    {"title": ("API &", "Event Layer"), "stack": ("FastAPI • Spring Boot", "RabbitMQ"), "icon": "server"},
    {"title": ("Storage &", "Low-Latency Cache"), "stack": ("TimescaleDB • PostgreSQL", "Redis"), "icon": "database"},
    {"title": ("Real-Time UI &", "Alerting"), "stack": ("Live Dashboards", "LINE / SMTP"), "icon": "alerting"},
]
EDGES = ["REST / JSON", "SQL / Cache", "Stream / Alert"]


def icon(kind: str, p: dict) -> str:
    """32 unit glyphs in the style of the site's DiagramIcon."""
    ink2, teal, amber, canvas = p["ink2"], p["teal"], p["amber"], p["canvas"]
    line = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
    if kind == "client":
        return (
            f'<rect x="3" y="5" width="26" height="22" rx="4" fill="{canvas}" stroke="{ink2}" stroke-width="2"/>'
            f'<path d="M3 11.5h26" stroke="{ink2}" stroke-width="2"/>'
            f'<circle cx="7.4" cy="8.3" r="1.1" fill="{ink2}"/><circle cx="10.9" cy="8.3" r="1.1" fill="{ink2}"/>'
            f'<circle cx="14.4" cy="8.3" r="1.1" fill="{ink2}"/>'
            f'<path d="M7 20h3l2.4-4.2 3.4 7.4 2.6-5.2 1.8 2H25" {line} stroke="{teal}" stroke-width="2"/>'
        )
    if kind == "server":
        unit = []
        for y in (4.5, 17.5):
            unit.append(
                f'<rect x="4" y="{y}" width="24" height="10" rx="2.5" fill="{canvas}" stroke="{ink2}" stroke-width="2"/>'
                f'<circle cx="9" cy="{y + 5}" r="1.6" fill="{teal}"/>'
                f'<path d="M15 {y + 5}h8.5" {line} stroke="{ink2}" stroke-width="2"/>'
            )
        return "".join(unit) + f'<path d="M16 14.5v3" stroke="{ink2}" stroke-width="2"/>'
    if kind == "database":
        return (
            f'<path d="M3.5 8v15.5c0 1.9 4 3.5 9 3.5s9-1.6 9-3.5V8" fill="{canvas}" stroke="{ink2}" stroke-width="2"/>'
            f'<path d="M3.5 8c0-1.9 4-3.5 9-3.5s9 1.6 9 3.5-4 3.5-9 3.5-9-1.6-9-3.5z" fill="{canvas}" stroke="{ink2}" stroke-width="2"/>'
            f'<path d="M3.5 15.8c0 1.9 4 3.5 9 3.5s9-1.6 9-3.5" fill="none" stroke="{ink2}" stroke-width="2"/>'
            f'<path d="M26.5 12.5 21.8 20h3.6l-2 7.5 6.1-8.6h-3.7l2.2-6.4z" fill="{amber}"/>'
        )
    if kind == "alerting":
        return (
            f'<rect x="3" y="9" width="20" height="19" rx="3.5" fill="{canvas}" stroke="{ink2}" stroke-width="2"/>'
            f'<rect x="7" y="19.5" width="3" height="4.5" rx=".8" fill="{teal}" opacity=".5"/>'
            f'<rect x="11.5" y="16.5" width="3" height="7.5" rx=".8" fill="{teal}" opacity=".75"/>'
            f'<rect x="16" y="13.5" width="3" height="10.5" rx=".8" fill="{teal}"/>'
            # The site's bell glyph, scaled into the top right corner.
            f'<g transform="translate(16.2 1.4) scale(.48)">'
            f'<circle cx="16" cy="15" r="13" fill="{canvas}"/>'
            f'<path d="M16 5a7 7 0 0 0-7 7v4.1c0 1.3-.5 2.5-1.3 3.4l-1 1.1c-.8.9-.2 2.4 1 2.4h16.6c1.2 0 1.8-1.5 1-2.4l-1-1.1a5.1 5.1 0 0 1-1.3-3.4V12a7 7 0 0 0-7-7z" fill="{amber}"/>'
            f'<path d="M12.8 25a3.2 3.2 0 0 0 6.4 0z" fill="{amber}"/></g>'
        )
    raise ValueError(kind)


def system_flow(p: dict, f: Fonts, theme: str) -> tuple[str, Slots]:
    s = Slots(f"system-flow-{theme}")
    css = "@media (prefers-reduced-motion:reduce){.motion{display:none}}"
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {FLOW_W} {FLOW_H}" width="100%" role="img" '
        f'aria-label="System flow: client and telemetry edge, API and event layer, storage and low-latency cache, real-time UI and alerting">',
        f"<style>{css}</style>",
        "<defs>",
        dot_pattern(p, 1),  # dots at y = 13 ... 157
        "</defs>",
        card(FLOW_W, FLOW_H, p, "fill"),
    ]

    for i, (node, x) in enumerate(zip(NODES, NODE_XS)):
        pad = 16
        lo, hi = x + pad, x + NODE_W - pad
        out.append(
            f'<rect x="{x + .5}" y="{NODE_Y + .5}" width="{NODE_W - 1}" height="{NODE_H - 1}" rx="10" '
            f'fill="{p["canvas"]}" stroke="{p["border"]}"/>'
        )
        out.append(f'<g transform="translate({x + pad} {NODE_Y + pad})">{icon(node["icon"], p)}</g>')
        out.append(text(f.mono, s, f"0{i + 1}", hi, NODE_Y + pad + 11, 10.5, p["ink2"], lo, hi,
                        tracking=1, anchor="end", extra=' fill-opacity=".75"'))
        ty = NODE_Y + pad + 32 + 22
        out.append(text(f.bold, s, node["title"][0], lo, ty, 15, p["ink"], lo, hi, tracking=-0.2))
        out.append(text(f.bold, s, node["title"][1], lo, ty + 19, 15, p["ink"], lo, hi, tracking=-0.2))
        out.append(text(f.medium, s, node["stack"][0], lo, ty + 39, 12, p["ink2"], lo, hi))
        out.append(text(f.medium, s, node["stack"][1], lo, ty + 55, 12, p["ink2"], lo, hi))

    # Connectors, edge labels, arrival flashes and packets.
    for i, label in enumerate(EDGES):
        gap_lo = NODE_XS[i] + NODE_W
        gap_hi = NODE_XS[i + 1]
        start, tip = gap_lo + 6, gap_hi - 5
        out.append(f'<path d="M{start} {num(EDGE_Y)}H{tip}" stroke="{p["ink2"]}" stroke-opacity=".55" stroke-width="1.5"/>')
        out.append(
            f'<path d="M{tip - 6} {num(EDGE_Y - 4.5)} {tip} {num(EDGE_Y)} {tip - 6} {num(EDGE_Y + 4.5)}" fill="none" '
            f'stroke="{p["ink2"]}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        out.append(text(f.mono, s, label, (gap_lo + gap_hi) / 2, EDGE_Y - 9, 10.5, p["ink2"],
                        gap_lo + 4, gap_hi - 4, anchor="middle"))

        color = p["amber"] if i == 2 else p["teal"]
        begin = f"{HOP * i:.1f}s"
        third = 1 / 3
        out.append(
            f'<circle class="motion" r="4" fill="{color}" opacity="0">'
            f'<animateMotion path="M{start + 2} {num(EDGE_Y)}H{tip - 4}" dur="{CYCLE:.1f}s" begin="{begin}" '
            f'repeatCount="indefinite" calcMode="spline" keyPoints="0;1;1" keyTimes="0;{third:.3f};1" '
            f'keySplines="0.45 0 0.55 1;0 0 1 1"/>'
            f'<animate attributeName="opacity" values="0;1;1;0;0" keyTimes="0;.05;.29;{third:.3f};1" '
            f'dur="{CYCLE:.1f}s" begin="{begin}" repeatCount="indefinite"/>'
            "</circle>"
        )

    # The receiving node's border lights briefly as a packet arrives.
    for i, x in enumerate(NODE_XS):
        arrive = (HOP * i) % CYCLE
        color = p["amber"] if i == 3 else p["teal"]
        begin = (arrive - 0.2) % CYCLE
        out.append(
            f'<rect class="motion" x="{x + .5}" y="{NODE_Y + .5}" width="{NODE_W - 1}" height="{NODE_H - 1}" rx="10" '
            f'fill="none" stroke="{color}" stroke-width="1.5" opacity="0">'
            f'<animate attributeName="opacity" values="0;.7;0;0" keyTimes="0;.083;.25;1" dur="{CYCLE:.1f}s" '
            f'begin="{begin:.1f}s" repeatCount="indefinite"/></rect>'
        )

    out.append(card(FLOW_W, FLOW_H, p, "stroke"))
    out.append("</svg>")
    return "".join(out), s


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

FORBIDDEN = [
    (re.compile(r"<\s*script", re.I), "<script>"),
    (re.compile(r"foreignObject", re.I), "<foreignObject>"),
    (re.compile(r"\son[a-z]+\s*=", re.I), "event handler attribute"),
    (re.compile(r"href", re.I), "href"),
    (re.compile(r"<\s*image", re.I), "<image>"),
    (re.compile(r"url\((?!#)", re.I), "external url()"),
]


def check(name: str, svg: str, viewbox: str) -> list[str]:
    problems = []
    for pattern, label in FORBIDDEN:
        if pattern.search(svg):
            problems.append(f"{name}: contains {label}")
    root = ET.fromstring(svg)
    if root.get("viewBox") != viewbox:
        problems.append(f"{name}: viewBox is {root.get('viewBox')}")
    if root.get("width") != "100%":
        problems.append(f"{name}: width is {root.get('width')}")
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ALLOWED:
            problems.append(f"{name}: element <{tag}> is not on the allowed list")
    return problems


def main() -> None:
    fonts = Fonts()
    ASSETS.mkdir(exist_ok=True)
    problems: list[str] = []
    for theme, palette in PALETTES.items():
        for name, build, viewbox in (
            (f"banner-{theme}.svg", banner, f"0 0 {BANNER_W} {BANNER_H}"),
            (f"system-flow-{theme}.svg", system_flow, f"0 0 {FLOW_W} {FLOW_H}"),
        ):
            svg, slots = build(palette, fonts, theme)
            problems += slots.problems + check(name, svg, viewbox)
            (ASSETS / name).write_text(svg, encoding="utf-8", newline="\n")
            print(f"{name:28} {len(svg.encode()) / 1024:6.1f} KB")
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
