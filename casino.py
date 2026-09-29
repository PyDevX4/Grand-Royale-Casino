"""
Grand Royale Casino - a just-for-fun casino game.

PLAY MONEY ONLY. Chips are an in-game currency with no cash value and
can never be bought, sold, or transferred to real money.

Run:  python casino.py
"""
import json
import math
import os
import random
import sys
import threading
import time
from array import array
from collections import deque

import pygame

# The browser version (built with pygbag) runs under Emscripten. A browser tab can't open network
# connections to friends or replace its own program, so multiplayer and auto-updates are switched off
# there - the web page always serves the newest version anyway.
WEB = sys.platform == "emscripten"
try:
    import socket
except ImportError:
    socket = None
try:
    from version import VERSION
except ImportError:         # someone was given just casino.py on its own
    VERSION = "dev"
updater = None
if not WEB:
    try:                    # auto-updates (only the built .exe actually updates itself)
        import updater
    except ImportError:
        updater = None

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
W, H = 1280, 720
FPS = 60
if getattr(sys, "frozen", False):      # the built .exe: keep the save next to it, not in its temporary folder
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_FILE = os.path.join(BASE_DIR, "save.json")
WEB_SAVE_KEY = "grand_royale_save"     # the browser keeps the save in its local storage
WEB_DEBUG = False                      # print keys and clicks to the browser console (for fixing web problems)
START_BALANCE = 500

CW, CH = 90, 126          # card size
SS = 3                    # supersampling factor for smooth pre-rendered art

GOLD = (224, 184, 84)
GOLD_DARK = (150, 115, 40)
WHITE = (245, 245, 240)
GREY = (150, 150, 150)
RED_SUIT = (200, 25, 35)
BLACK_SUIT = (20, 20, 25)

SUITS = "SHDC"
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

CHIP_VALUES = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 500000, 1000000]
CHIP_STYLE = {  # base colour, text colour, label
    10: ((40, 100, 210), WHITE, "10"),
    25: ((30, 150, 70), WHITE, "25"),
    50: ((230, 120, 30), WHITE, "50"),
    100: ((30, 30, 34), WHITE, "100"),
    250: ((225, 80, 160), WHITE, "250"),
    500: ((120, 50, 180), WHITE, "500"),
    1000: ((235, 195, 50), (45, 32, 10), "1K"),
    2500: ((190, 30, 40), WHITE, "2.5K"),
    5000: ((0, 145, 150), WHITE, "5K"),
    10000: ((25, 45, 125), (255, 215, 90), "10K"),
    25000: ((105, 165, 40), WHITE, "25K"),
    50000: ((205, 70, 25), (255, 235, 160), "50K"),
    100000: ((18, 18, 20), (255, 210, 80), "100K"),
    500000: ((165, 172, 188), (60, 20, 30), "500K"),
    1000000: ((200, 20, 120), (255, 225, 90), "1M"),
}

# Layout (blackjack table)
DEALER_Y = 100
PLAYER_Y = 352
BET_Y = 552
CARD_STEP = 40
SHOE_POS = (1095, 72)
DISCARD_POS = (80, 80)
CHIP_TRAY_Y = 680


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
_fonts = {}


def font(size, bold=False, serif=False):
    key = (int(size), bold, serif)
    if key not in _fonts:
        name = "georgia,timesnewroman" if serif else "segoeui,arial"
        _fonts[key] = pygame.font.SysFont(name, int(size), bold=bold)
    return _fonts[key]


def money(n):
    return f"${n:,}"


def lighten(c, amt):
    return tuple(min(255, int(v + amt)) for v in c[:3])


def darken(c, amt):
    return tuple(max(0, int(v - amt)) for v in c[:3])


def lerp_col(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_text(surf, text, f, color, pos, anchor="center", shadow=None):
    img = f.render(text, True, color)
    rect = img.get_rect(**{anchor: pos})
    if shadow:
        surf.blit(f.render(text, True, shadow), rect.move(2, 2))
    surf.blit(img, rect)
    return rect


def toggle_fullscreen():
    try:
        pygame.display.toggle_fullscreen()
    except Exception:              # not every browser allows it - F11 in the browser works anyway
        pass


def fit_font(text, size, width, bold=True):
    """The biggest font (up to `size`) that fits `text` in `width` pixels - for long player names."""
    while size > 9 and font(size, bold=bold).size(text)[0] > width:
        size -= 1
    return font(size, bold=bold)


def draw_pill(surf, text, f, center, fg, bg, border=None, pad=(18, 6)):
    img = f.render(text, True, fg)
    rect = img.get_rect(center=center).inflate(pad[0] * 2, pad[1] * 2)
    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel, bg, panel.get_rect(), border_radius=rect.h // 2)
    if border:
        pygame.draw.rect(panel, border, panel.get_rect(), width=2, border_radius=rect.h // 2)
    surf.blit(panel, rect)
    surf.blit(img, img.get_rect(center=rect.center))
    return rect


def draw_arc_text(surf, text, f, color, center, radius, spacing=2):
    """Write text along the bottom of a circle (a 'smile' curve) like on a felt."""
    cx, cy = center
    imgs = [f.render(ch, True, color) for ch in text]
    total = sum(i.get_width() for i in imgs) + spacing * (len(imgs) - 1)
    acc = -total / 2
    for img in imgs:
        w = img.get_width()
        th = (acc + w / 2) / radius
        x = cx + radius * math.sin(th)
        y = cy + radius * math.cos(th)
        rot = pygame.transform.rotozoom(img, math.degrees(th), 1)
        surf.blit(rot, rot.get_rect(center=(x, y)))
        acc += w + spacing


# --------------------------------------------------------------------------
# Procedural art: suits, cards, chips
# --------------------------------------------------------------------------
def suit_color(suit):
    return RED_SUIT if suit in "HD" else BLACK_SUIT


def draw_suit(surf, suit, cx, cy, s, color):
    """Draw a suit symbol of height s centred on (cx, cy)."""
    if suit == "D":
        pygame.draw.polygon(surf, color, [(cx, cy - 0.5 * s), (cx + 0.36 * s, cy),
                                          (cx, cy + 0.5 * s), (cx - 0.36 * s, cy)])
    elif suit == "H":
        r = s * 0.26
        pygame.draw.circle(surf, color, (cx - r * 0.92, cy - s * 0.18), r)
        pygame.draw.circle(surf, color, (cx + r * 0.92, cy - s * 0.18), r)
        pygame.draw.polygon(surf, color, [(cx - r * 1.88, cy - s * 0.18 + r * 0.3),
                                          (cx + r * 1.88, cy - s * 0.18 + r * 0.3),
                                          (cx, cy + s * 0.48)])
    elif suit == "S":
        r = s * 0.26
        pygame.draw.circle(surf, color, (cx - r * 0.92, cy + s * 0.06), r)
        pygame.draw.circle(surf, color, (cx + r * 0.92, cy + s * 0.06), r)
        pygame.draw.polygon(surf, color, [(cx - r * 1.88, cy + s * 0.06 - r * 0.3),
                                          (cx + r * 1.88, cy + s * 0.06 - r * 0.3),
                                          (cx, cy - s * 0.5)])
        pygame.draw.polygon(surf, color, [(cx, cy + s * 0.05), (cx - s * 0.16, cy + s * 0.5),
                                          (cx + s * 0.16, cy + s * 0.5)])
    elif suit == "C":
        r = s * 0.2
        pygame.draw.circle(surf, color, (cx, cy - s * 0.26), r)
        pygame.draw.circle(surf, color, (cx - s * 0.22, cy + s * 0.06), r)
        pygame.draw.circle(surf, color, (cx + s * 0.22, cy + s * 0.06), r)
        pygame.draw.circle(surf, color, (cx, cy), r * 0.8)
        pygame.draw.polygon(surf, color, [(cx, cy), (cx - s * 0.16, cy + s * 0.5),
                                          (cx + s * 0.16, cy + s * 0.5)])


def pip_surface(suit, size, color=None):
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    draw_suit(s, suit, size / 2, size / 2, size * 0.96, color or suit_color(suit))
    return s


L, C, R = "L", "C", "R"
PIP_LAYOUT = {
    2: [(C, 0), (C, 1)],
    3: [(C, 0), (C, .5), (C, 1)],
    4: [(L, 0), (R, 0), (L, 1), (R, 1)],
    5: [(L, 0), (R, 0), (C, .5), (L, 1), (R, 1)],
    6: [(L, 0), (R, 0), (L, .5), (R, .5), (L, 1), (R, 1)],
    7: [(L, 0), (R, 0), (C, .25), (L, .5), (R, .5), (L, 1), (R, 1)],
    8: [(L, 0), (R, 0), (C, .25), (L, .5), (R, .5), (C, .75), (L, 1), (R, 1)],
    9: [(L, 0), (R, 0), (L, 1 / 3), (R, 1 / 3), (C, .5), (L, 2 / 3), (R, 2 / 3), (L, 1), (R, 1)],
    10: [(L, 0), (R, 0), (C, 1 / 6), (L, 1 / 3), (R, 1 / 3), (L, 2 / 3), (R, 2 / 3), (C, 5 / 6),
         (L, 1), (R, 1)],
}


def make_card_face(rank, suit):
    k = SS
    w, h = CW * k, CH * k
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(s, (252, 251, 246), (0, 0, w, h), border_radius=8 * k)
    pygame.draw.rect(s, (170, 170, 165), (0, 0, w, h), width=k, border_radius=8 * k)
    col = suit_color(suit)

    # corner index (rank + small suit), mirrored in the opposite corner
    corner = pygame.Surface((22 * k, 42 * k), pygame.SRCALPHA)
    f = font(19 * k if rank != "10" else 16 * k, bold=True, serif=True)
    txt = f.render(rank, True, col)
    corner.blit(txt, txt.get_rect(midtop=(11 * k, 0)))
    pip = pip_surface(suit, 12 * k)
    corner.blit(pip, pip.get_rect(midtop=(11 * k, 23 * k)))
    s.blit(corner, (3 * k, 4 * k))
    s.blit(pygame.transform.rotate(corner, 180), (w - 25 * k, h - 46 * k))

    if rank == "A":
        big = pip_surface(suit, (54 if suit == "S" else 44) * k)
        s.blit(big, big.get_rect(center=(w / 2, h / 2)))
    elif rank in ("J", "Q", "K"):
        fr = pygame.Rect(24 * k, 17 * k, 42 * k, 92 * k)
        pygame.draw.rect(s, (250, 238, 210), fr, border_radius=3 * k)
        accent = (200, 40, 50) if col == RED_SUIT else (40, 60, 150)
        pygame.draw.rect(s, accent, fr.inflate(-6 * k, -6 * k), width=k, border_radius=2 * k)
        pygame.draw.rect(s, GOLD_DARK, fr, width=2 * k, border_radius=3 * k)
        big = font(34 * k, bold=True, serif=True).render(rank, True, col)
        s.blit(big, big.get_rect(center=(w / 2, h / 2 + k)))
        sp = pip_surface(suit, 14 * k)
        s.blit(sp, sp.get_rect(center=(w / 2, fr.top + 14 * k)))
        sp = pygame.transform.rotate(sp, 180)
        s.blit(sp, sp.get_rect(center=(w / 2, fr.bottom - 14 * k)))
    else:
        cols = {L: 31, C: 45, R: 59}
        top, bot = 22, 104
        pip = pip_surface(suit, 16 * k)
        flipped = pygame.transform.rotate(pip, 180)
        for c, frac in PIP_LAYOUT[int(rank)]:
            img = flipped if frac > 0.5 else pip
            s.blit(img, img.get_rect(center=(cols[c] * k, (top + (bot - top) * frac) * k)))
    return pygame.transform.smoothscale(s, (CW, CH))


def make_card_back():
    k = SS
    w, h = CW * k, CH * k
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(s, (250, 250, 246), (0, 0, w, h), border_radius=8 * k)
    pygame.draw.rect(s, (170, 170, 165), (0, 0, w, h), width=k, border_radius=8 * k)
    m = 5 * k
    iw, ih = w - 2 * m, h - 2 * m
    pat = pygame.Surface((iw, ih), pygame.SRCALPHA)
    pat.fill((150, 18, 32, 255))
    step = 7 * k
    for i in range(-ih, iw + ih, step):
        pygame.draw.line(pat, (185, 45, 60), (i, 0), (i + ih, ih), k)
        pygame.draw.line(pat, (185, 45, 60), (i, ih), (i + ih, 0), k)
    trim = (225, 190, 120)
    pygame.draw.rect(pat, trim, pat.get_rect().inflate(-6 * k, -6 * k), width=k, border_radius=4 * k)
    pygame.draw.circle(pat, (150, 18, 32), (iw / 2, ih / 2), 15 * k)
    pygame.draw.circle(pat, trim, (iw / 2, ih / 2), 15 * k, width=k)
    draw_suit(pat, "S", iw / 2, ih / 2, 15 * k, trim)
    mask = pygame.Surface((iw, ih), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=5 * k)
    pat.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    s.blit(pat, (m, m))
    return pygame.transform.smoothscale(s, (CW, CH))


def make_chip(value, radius):
    k = SS
    R = radius * k
    s = pygame.Surface((2 * R, 2 * R), pygame.SRCALPHA)
    base, text_col, label = CHIP_STYLE[value]
    c = (R, R)
    pygame.draw.circle(s, darken(base, 70), c, R)
    pygame.draw.circle(s, base, c, R - 1.5 * k)
    # edge inserts
    for i in range(6):
        a0 = math.radians(i * 60 + 12)
        a1 = a0 + math.radians(22)
        pts = [(R + (R - 1.5 * k) * math.cos(a0 + (a1 - a0) * t / 6),
                R + (R - 1.5 * k) * math.sin(a0 + (a1 - a0) * t / 6)) for t in range(7)]
        pts += [(R + R * 0.76 * math.cos(a1 - (a1 - a0) * t / 6),
                 R + R * 0.76 * math.sin(a1 - (a1 - a0) * t / 6)) for t in range(7)]
        pygame.draw.polygon(s, (245, 245, 245), pts)
    # inner inlay
    pygame.draw.circle(s, (240, 240, 235), c, R * 0.70)
    pygame.draw.circle(s, lighten(base, 18), c, R * 0.64)
    for i in range(24):
        a = math.radians(i * 15)
        pygame.draw.circle(s, darken(base, 40), (R + R * 0.58 * math.cos(a), R + R * 0.58 * math.sin(a)),
                           R * 0.025)
    f = font(R * (0.5 if len(label) <= 2 else 0.42 if len(label) == 3 else 0.36), bold=True)
    txt = f.render(label, True, text_col)
    s.blit(txt, txt.get_rect(center=(R, R + k)))
    return pygame.transform.smoothscale(s, (2 * radius, 2 * radius))


def chip_breakdown(amount):
    chips = []
    for v in sorted(CHIP_VALUES, reverse=True):
        while amount >= v:
            chips.append(v)
            amount -= v
    return chips


CHIP_SHOWN = 7          # chips the tray shows at once (the ALL IN button sits at the right end)
CHIP_GAP = 64


class ChipWindow:
    """Every game's chip tray shows 8 chips at a time. The arrows (or the mouse wheel) slide along to
    the bigger or smaller chips. All the trays share this, so it stays put when you switch games."""

    def __init__(self):
        self.start = 0
        self.frame = 0
        self.seen = -1          # the last frame a tray was drawn (so the arrows only show with a tray)

    def values(self):
        return CHIP_VALUES[self.start:self.start + CHIP_SHOWN]

    @staticmethod
    def pos_of(i):
        return (410 + i * CHIP_GAP, CHIP_TRAY_Y)

    def items(self):
        self.seen = self.frame
        return [(v, self.pos_of(i)) for i, v in enumerate(self.values())]

    def __getitem__(self, v):
        vals = self.values()
        if v in vals:
            return self.pos_of(vals.index(v))
        return self.pos_of(0 if v < vals[0] else len(vals) - 1)

    def max_start(self):
        return max(0, len(CHIP_VALUES) - CHIP_SHOWN)

    def shift(self, d):
        self.start = max(0, min(self.max_start(), self.start + d))

    def auto(self, balance):
        """Show the chips that make sense for how much you have."""
        top = max([i for i, v in enumerate(CHIP_VALUES) if v <= balance], default=0)
        self.start = max(0, min(self.max_start(), top - CHIP_SHOWN + 1))

    def visible(self):
        return self.seen == self.frame

    @staticmethod
    def arrows():
        return pygame.Rect(349, CHIP_TRAY_Y - 16, 26, 32), pygame.Rect(829, CHIP_TRAY_Y - 16, 26, 32)

    @staticmethod
    def all_in_rect():
        return pygame.Rect(861, CHIP_TRAY_Y - 27, 68, 54)


CHIP_WIN = ChipWindow()


# --------------------------------------------------------------------------
# Sound (synthesised so no asset files are needed)
# --------------------------------------------------------------------------
def synth(duration, parts, vol=0.35):
    sr, size, channels = pygame.mixer.get_init() or (22050, -16, 1)
    floats = size == 32
    n = int(sr * duration)
    buf = array("f" if floats else "h")
    for i in range(n):
        t = i / sr
        v = 0.0
        for kind, freq, start, decay in parts:
            if t < start:
                continue
            tt = t - start
            env = math.exp(-tt * decay)
            if kind == "noise":
                v += random.uniform(-1, 1) * env
            else:
                v += math.sin(2 * math.pi * freq * tt) * env
        sample = max(-1.0, min(1.0, v * vol))
        sample = sample if floats else int(sample * 32767)
        for _ in range(channels):
            buf.append(sample)
    return pygame.mixer.Sound(buffer=buf.tobytes())


def make_sounds():
    try:
        return {
            "card": synth(0.09, [("noise", 0, 0, 55)], 0.25),
            "chip": synth(0.12, [("sine", 3200, 0, 70), ("noise", 0, 0, 90),
                                 ("sine", 2700, 0.04, 70), ("noise", 0, 0.04, 90)], 0.2),
            "win": synth(0.6, [("sine", 523, 0, 6), ("sine", 659, 0.12, 6), ("sine", 784, 0.24, 5),
                               ("sine", 1046, 0.36, 5)], 0.18),
            "lose": synth(0.45, [("sine", 330, 0, 5), ("sine", 262, 0.15, 5)], 0.15),
            "boom": synth(1.0, [("noise", 0, 0, 5), ("sine", 55, 0, 4)], 0.35),
            "launch": synth(0.6, [("noise", 0, 0, 4)], 0.12),
            "alert": synth(0.7, [("sine", 988, 0, 7), ("sine", 740, 0.16, 7), ("sine", 988, 0.32, 6)], 0.16),
        }
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Shared assets
# --------------------------------------------------------------------------
class Assets:
    def __init__(self):
        self.faces = {(r, s): make_card_face(r, s) for s in SUITS for r in RANKS}
        self._back = make_card_back()
        self.shadow = pygame.Surface((CW, CH), pygame.SRCALPHA)
        pygame.draw.rect(self.shadow, (0, 0, 0, 70), (0, 0, CW, CH), border_radius=8)
        self._chips = {}

    @property
    def back(self):
        """The card back - in the season's design when a theme is on."""
        return themed(("back",), lambda: make_themed_back(CURRENT_THEME)) if CURRENT_THEME else self._back

    def chip(self, value, radius):
        key = (value, radius)
        if key not in self._chips:
            self._chips[key] = make_chip(value, radius)
        return self._chips[key]


class Button:
    def __init__(self, rect, text, color=(55, 55, 65), size=22, hint=None):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.color = color
        self.size = size
        self.hint = hint

    def draw(self, surf, mouse, enabled=True):
        r = self.rect
        hover = enabled and r.collidepoint(mouse)
        base = self.color if enabled else (70, 70, 72)
        if hover:
            base = lighten(base, 28)
        pygame.draw.rect(surf, darken(base, 45), r.move(0, 4), border_radius=10)
        pygame.draw.rect(surf, base, r, border_radius=10)
        pygame.draw.rect(surf, lighten(base, 22), (r.x + 3, r.y + 3, r.w - 6, r.h // 2 - 3), border_radius=8)
        pygame.draw.rect(surf, GOLD if enabled else (105, 105, 105), r, width=2, border_radius=10)
        tc = WHITE if enabled else (150, 150, 150)
        cy = r.centery - (6 if self.hint else 0)
        draw_text(surf, self.text, font(self.size, bold=True), tc, (r.centerx, cy), shadow=(0, 0, 0))
        if self.hint:
            draw_text(surf, self.hint, font(12), (220, 220, 210) if enabled else (130, 130, 130),
                      (r.centerx, r.bottom - 11))

    def clicked(self, pos, enabled=True):
        return enabled and self.rect.collidepoint(pos)


# --------------------------------------------------------------------------
# Blackjack
# --------------------------------------------------------------------------
def rank_value(rank):
    if rank == "A":
        return 11
    if rank in ("J", "Q", "K"):
        return 10
    return int(rank)


def hand_value(ranks):
    total = sum(rank_value(r) for r in ranks)
    aces = sum(1 for r in ranks if r == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0


class Shoe:
    def __init__(self, decks=6):
        self.decks = decks
        self.shuffle()

    def shuffle(self):
        self.cards = [(r, s) for _ in range(self.decks) for s in SUITS for r in RANKS]
        random.shuffle(self.cards)

    def needs_shuffle(self):
        return len(self.cards) < self.decks * 52 * 0.25

    def draw(self):
        if not self.cards:
            self.shuffle()
        return self.cards.pop()


class CardSprite:
    def __init__(self, rank, suit, pos, face_up=True):
        self.rank, self.suit = rank, suit
        self.x, self.y = pos
        self.tx, self.ty = pos
        self.face_up = face_up
        self.flip_t = None

    def reveal(self):
        if not self.face_up and self.flip_t is None:
            self.flip_t = 0.0

    def update(self, dt):
        k = 1 - math.exp(-dt * 13)
        self.x += (self.tx - self.x) * k
        self.y += (self.ty - self.y) * k
        if self.flip_t is not None:
            self.flip_t += dt * 3.5
            if self.flip_t >= 0.5:
                self.face_up = True
            if self.flip_t >= 1:
                self.flip_t = None

    def arrived(self):
        return abs(self.tx - self.x) < 2 and abs(self.ty - self.y) < 2

    def draw(self, surf, assets):
        img = assets.faces[(self.rank, self.suit)] if self.face_up else assets.back
        if self.flip_t is not None:
            w = max(2, int(CW * abs(math.cos(math.pi * self.flip_t))))
            img = pygame.transform.smoothscale(img, (w, CH))
            surf.blit(img, (self.x + (CW - w) / 2, self.y))
            return
        surf.blit(assets.shadow, (self.x + 3, self.y + 4))
        surf.blit(img, (self.x, self.y))


class Hand:
    def __init__(self, bet=0):
        self.cards = []
        self.bet = bet
        self.done = False
        self.result = None      # 'bust', 'win', 'lose', 'push', 'blackjack'
        self.payout = 0
        self.from_split = False

    def value(self, visible_only=False):
        cards = [c for c in self.cards if c.face_up] if visible_only else self.cards
        return hand_value([c.rank for c in cards])

    @property
    def total(self):
        return self.value()[0]

    @property
    def blackjack(self):
        return len(self.cards) == 2 and self.total == 21 and not self.from_split


def make_wood():
    surf = pygame.Surface((W, H))
    rng = random.Random(7)
    for y in range(H):
        shade = 58 + 12 * math.sin(y * 0.31 + math.sin(y * 0.047) * 3) + rng.randint(-3, 3)
        pygame.draw.line(surf, (int(shade + 10), int(shade * 0.6), int(shade * 0.33)), (0, y), (W, y))
    return surf


def make_table_bg(assets):
    surf = make_wood()
    rng = random.Random(7)

    felt_rect = pygame.Rect(-150, -560, W + 300, 1160)       # bottom edge at y=600
    rail_rect = felt_rect.inflate(60, 70).move(0, 0)
    pygame.draw.ellipse(surf, (28, 16, 11), rail_rect.move(0, 6))
    pygame.draw.ellipse(surf, (48, 28, 19), rail_rect)
    pygame.draw.ellipse(surf, (78, 48, 32), rail_rect.inflate(-14, -14), width=3)
    pygame.draw.ellipse(surf, (35, 20, 13), felt_rect.inflate(8, 8))

    # felt with radial gradient + subtle texture
    felt = pygame.Surface((W, H), pygame.SRCALPHA)
    dark, light = (8, 62, 36), (24, 122, 68)
    felt.fill((*dark, 255))
    steps = 48
    for i in range(steps):
        t = i / (steps - 1)
        col = lerp_col(dark, light, t ** 0.8)
        rw, rh = 1900 * (1 - t) + 160, 1400 * (1 - t) + 100
        pygame.draw.ellipse(felt, col, (W / 2 - rw / 2, 290 - rh / 2, rw, rh))
    for _ in range(30000):
        x, y = rng.randrange(W), rng.randrange(H)
        r, g, b, a = felt.get_at((x, y))
        d = rng.choice((-7, -4, 4, 6))
        felt.set_at((x, y), (max(0, r + d), max(0, min(255, g + d)), max(0, b + d), a))
    mask = pygame.Surface((W, H), pygame.SRCALPHA)
    pygame.draw.ellipse(mask, (255, 255, 255, 255), felt_rect)
    felt.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(felt, (0, 0))

    # felt printing
    draw_arc_text(surf, "BLACKJACK PAYS 3 TO 2", font(27, bold=True, serif=True), GOLD, (W / 2, -300), 560)
    draw_arc_text(surf, "DEALER MUST STAND ON ALL 17s", font(15, bold=True), (200, 225, 205), (W / 2, -300), 596)
    pts = [(W / 2 + 630 * math.sin(math.radians(a / 2)), -300 + 630 * math.cos(math.radians(a / 2)))
           for a in range(-44, 45)]
    pygame.draw.aalines(surf, (190, 160, 80), False, pts)

    # dealer shoe (top right)
    back = pygame.transform.rotate(assets.back, 0)
    surf.blit(back, SHOE_POS)
    shoe = [(1122, 60), (1236, 60), (1236, 206), (1106, 206)]
    pygame.draw.polygon(surf, (20, 20, 24), [(x + 4, y + 5) for x, y in shoe])
    pygame.draw.polygon(surf, (38, 38, 44), shoe)
    pygame.draw.polygon(surf, (62, 62, 70), [(1122, 60), (1236, 60), (1236, 76), (1119, 76)])
    pygame.draw.polygon(surf, GOLD_DARK, shoe, width=2)
    draw_text(surf, "SHOE", font(13, bold=True), (150, 150, 150), (1175, 190))

    # betting circle guide
    return surf


class Blackjack:
    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.table = make_table_bg(self.assets)
        self.shoe = Shoe(6)
        self.hands = []
        self.dealer = Hand()
        self.active = 0
        self.state = "betting"          # betting, dealing, player, dealer, result
        self.bet_chips = []
        self.last_bet = []
        self.queue = []
        self.leaving = []
        self.discards = 0
        self.flying = []
        self.message = "PLACE YOUR BET"

        x0 = W / 2 - 3.5 * 70
        self.chip_pos = CHIP_WIN
        self.btn_clear = Button((40, 650, 135, 54), "CLEAR", (120, 35, 40))
        self.btn_rebet = Button((190, 650, 135, 54), "REBET", (40, 70, 130))
        self.btn_deal = Button((965, 646, 270, 62), "DEAL", (25, 120, 60), size=28, hint="SPACE")
        bw, gap = 160, 18
        x = W / 2 - (4 * bw + 3 * gap) / 2
        self.btn_hit = Button((x, 648, bw, 58), "HIT", (25, 120, 60), 26, "H")
        self.btn_stand = Button((x + (bw + gap), 648, bw, 58), "STAND", (150, 35, 40), 26, "S")
        self.btn_double = Button((x + 2 * (bw + gap), 648, bw, 58), "DOUBLE", (170, 120, 20), 26, "D")
        self.btn_split = Button((x + 3 * (bw + gap), 648, bw, 58), "SPLIT", (95, 45, 150), 26, "P")
        self.rc = 0                  # Hi-Lo running count of every card seen since the last shuffle
        self.show_count = False
        self.btn_count = Button((1104, 214, 134, 34), "COUNT: OFF", (50, 60, 90), 14)

        self.dim_chip = pygame.Surface((64, 64), pygame.SRCALPHA)
        pygame.draw.circle(self.dim_chip, (0, 0, 0, 150), (32, 32), 31)

    # ---- helpers ---------------------------------------------------------
    @property
    def bet(self):
        return sum(self.bet_chips)

    @property
    def cur(self):
        return self.hands[self.active]

    def busy(self):
        return bool(self.queue)

    def schedule(self, delay, fn):
        self.queue.append([delay, fn])

    def can_leave(self):
        return self.state in ("betting", "result") and not self.busy()

    def outstanding_bets(self):
        """Money on the table that hasn't been settled yet (for refunds on quit)."""
        total = self.bet
        if self.state in ("dealing", "player", "dealer"):
            total += sum(h.bet for h in self.hands)
        return total

    def leave(self):
        self.app.balance += self.bet
        self.bet_chips = []
        self.flying = []
        self.discards += sum(len(h.cards) for h in self.hands) + len(self.dealer.cards)
        self.hands, self.dealer = [], Hand()
        self.leaving = []
        self.state = "betting"
        self.message = "PLACE YOUR BET"

    # ---- betting ---------------------------------------------------------
    def clear_table(self):
        for sp in [c for h in self.hands for c in h.cards] + self.dealer.cards:
            sp.tx, sp.ty = DISCARD_POS
            self.leaving.append(sp)
        self.hands, self.dealer = [], Hand()
        self.state = "betting"
        self.message = "PLACE YOUR BET"

    def add_chip(self, v):
        if self.state == "result":
            self.clear_table()
        if self.app.balance < v:
            self.message = "NOT ENOUGH CHIPS"
            return
        self.app.balance -= v
        self.bet_chips.append(v)
        sx, sy = self.chip_pos[v]
        self.flying.append({"v": v, "x": sx - 26, "y": sy - 26, "tx": W / 2 - 26,
                            "ty": BET_Y - 26 - 5 * (len(chip_breakdown(self.bet)) - 1)})
        self.message = ""
        self.app.sfx("chip")

    def all_in(self):
        """ALL IN: every chip you have goes on the table."""
        for v in chip_breakdown(self.app.balance):
            before = self.app.balance
            self.add_chip(v)
            if self.app.balance == before:
                break
        if self.bet:
            self.message = f"ALL IN!  BET {money(self.bet)}"

    def clear_bet(self):
        if self.state == "result":
            self.clear_table()
        self.app.balance += self.bet
        self.bet_chips = []
        self.flying = []
        self.message = "PLACE YOUR BET"

    def undo_chip(self):
        if self.bet_chips:
            v = self.bet_chips.pop()
            self.app.balance += v
            self.flying = []
            self.app.sfx("chip")

    def rebet(self):
        if self.state == "result":
            self.clear_table()
        if not self.last_bet:
            return
        self.clear_bet()
        if self.app.balance < sum(self.last_bet):
            self.message = "NOT ENOUGH CHIPS TO REBET"
            return
        for v in self.last_bet:
            self.add_chip(v)

    def deal(self):
        if self.state == "result":
            self.clear_table()
            if not self.bet_chips:
                self.rebet()
        if self.bet == 0:
            self.message = "PLACE A BET FIRST"
            return
        self.last_bet = list(self.bet_chips)
        self.hands = [Hand(self.bet)]
        self.bet_chips = []
        self.flying = []
        self.dealer = Hand()
        self.active = 0
        self.state = "dealing"
        self.message = ""
        first = 0.25
        if self.shoe.needs_shuffle():
            self.shoe.shuffle()
            self.discards = 0
            self.rc = 0
            self.message = "SHUFFLING A FRESH SHOE..."
            first = 1.2
            self.schedule(first, lambda: setattr(self, "message", ""))
            first = 0.1
        h = self.hands[0]
        self.schedule(first, lambda: self.deal_card(h))
        self.schedule(0.35, lambda: self.deal_card(self.dealer))
        self.schedule(0.35, lambda: self.deal_card(h))
        self.schedule(0.35, lambda: self.deal_card(self.dealer, face_up=False))
        self.schedule(0.55, self.after_initial_deal)

    # ---- play ------------------------------------------------------------
    def deal_card(self, hand, face_up=True):
        r, s = self.shoe.draw()
        hand.cards.append(CardSprite(r, s, SHOE_POS, face_up))
        if face_up:
            self.rc += hilo(r)
        self.app.sfx("card")

    def after_initial_deal(self):
        h, d = self.hands[0], self.dealer
        if h.blackjack or d.blackjack:
            self.state = "dealer"
            self.schedule(0.2, self.reveal_hole)
            self.schedule(0.8, self.settle)
        else:
            self.state = "player"

    def reveal_hole(self):
        for c in self.dealer.cards:
            if not c.face_up and c.flip_t is None:
                c.reveal()
                self.rc += hilo(c.rank)
                self.app.sfx("card")

    def can_double(self):
        h = self.cur
        return len(h.cards) == 2 and self.app.balance >= h.bet

    def can_split(self):
        h = self.cur
        return (len(h.cards) == 2 and rank_value(h.cards[0].rank) == rank_value(h.cards[1].rank)
                and self.app.balance >= h.bet and len(self.hands) < 4
                and not (h.from_split and h.cards[0].rank == "A"))

    def hit(self):
        h = self.cur
        self.deal_card(h)
        self.schedule(0.3, lambda: self.after_card(h))

    def after_card(self, h):
        v = h.total
        if v > 21:
            h.result = "bust"
            h.done = True
            self.app.sfx("lose")
            self.schedule(0.6, self.next_hand)
        elif v == 21:
            h.done = True
            self.schedule(0.35, self.next_hand)

    def stand(self):
        self.cur.done = True
        self.next_hand()

    def double(self):
        h = self.cur
        self.app.balance -= h.bet
        h.bet *= 2
        self.app.sfx("chip")
        self.deal_card(h)
        self.schedule(0.4, lambda: self.finish_double(h))

    def finish_double(self, h):
        if h.total > 21:
            h.result = "bust"
            self.app.sfx("lose")
        h.done = True
        self.schedule(0.5, self.next_hand)

    def split(self):
        h = self.cur
        self.app.balance -= h.bet
        self.app.sfx("chip")
        nh = Hand(h.bet)
        nh.from_split = h.from_split = True
        nh.cards.append(h.cards.pop())
        self.hands.insert(self.active + 1, nh)
        if h.cards[0].rank == "A":
            # split aces get one card each and no more
            h.done = nh.done = True
            self.schedule(0.35, lambda: self.deal_card(h))
            self.schedule(0.35, lambda: self.deal_card(nh))
            self.schedule(0.5, lambda: self.skip_to_after(nh))
        else:
            self.schedule(0.35, lambda: self.deal_card(h))
            self.schedule(0.3, lambda: self.after_card(h))

    def skip_to_after(self, hand):
        self.active = self.hands.index(hand)
        self.next_hand()

    def next_hand(self):
        self.active += 1
        if self.active < len(self.hands):
            h = self.hands[self.active]
            if len(h.cards) < 2:
                self.schedule(0.35, lambda: self.deal_card(h))
                self.schedule(0.3, lambda: self.after_card(h))
        else:
            self.active = len(self.hands) - 1
            self.dealer_turn()

    def dealer_turn(self):
        self.state = "dealer"
        self.schedule(0.35, self.reveal_hole)
        if all(h.result == "bust" for h in self.hands):
            self.schedule(0.8, self.settle)
        else:
            self.schedule(0.8, self.dealer_step)

    def dealer_step(self):
        if self.dealer.total < 17:
            self.deal_card(self.dealer)
            self.schedule(0.65, self.dealer_step)
        else:
            self.schedule(0.3, self.settle)

    def settle(self):
        d = self.dealer
        dv, dbj = d.total, d.blackjack
        total_bet = total_return = 0
        for h in self.hands:
            total_bet += h.bet
            pv = h.total
            if h.result == "bust":
                h.payout = 0
            elif h.blackjack and not dbj:
                h.result, h.payout = "blackjack", h.bet * 5 // 2
            elif h.blackjack and dbj:
                h.result, h.payout = "push", h.bet
            elif dbj:
                h.result, h.payout = "lose", 0
            elif dv > 21 or pv > dv:
                h.result, h.payout = "win", h.bet * 2
            elif pv == dv:
                h.result, h.payout = "push", h.bet
            else:
                h.result, h.payout = "lose", 0
            total_return += h.payout
        self.app.balance += total_return
        self.app.record("blackjack", total_bet, total_return)
        if any(h.result == "blackjack" for h in self.hands):
            self.app.unlock("bj_natural")
        if any(h.result == "win" and len(h.cards) >= 5 for h in self.hands):
            self.app.unlock("five_card")
        net = total_return - total_bet
        if dbj:
            lead = "DEALER HAS BLACKJACK.  "
        elif dv > 21:
            lead = "DEALER BUSTS!  "
        else:
            lead = ""
        if net > 0:
            self.message = lead + f"YOU WIN {money(net)}!"
            self.app.float_text(f"+{money(net)}", (80, 230, 110))
            self.app.sfx("win")
        elif net < 0:
            self.message = lead + f"YOU LOSE {money(-net)}"
            self.app.float_text(f"-{money(-net)}", (240, 90, 90))
            self.app.sfx("lose")
        else:
            self.message = lead + "PUSH - BET RETURNED"
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        self.state = "result"
        self.app.save()

    # ---- events ----------------------------------------------------------
    def handle(self, e):
        if (e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.btn_count.clicked(e.pos)) or \
                (e.type == pygame.KEYDOWN and e.key == pygame.K_c):
            self.show_count = not self.show_count
            self.app.sfx("chip")
            return
        if self.busy():
            return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self.click(e.pos)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3 and self.state == "betting":
            self.undo_chip()
        elif e.type == pygame.KEYDOWN:
            if self.state in ("betting", "result") and e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.deal()
            elif self.state == "player":
                if e.key == pygame.K_h:
                    self.hit()
                elif e.key == pygame.K_s:
                    self.stand()
                elif e.key == pygame.K_d and self.can_double():
                    self.double()
                elif e.key == pygame.K_p and self.can_split():
                    self.split()

    def click(self, pos):
        if self.state in ("betting", "result"):
            for v, (cx, cy) in self.chip_pos.items():
                if math.hypot(pos[0] - cx, pos[1] - cy) <= 31:
                    self.add_chip(v)
                    return
            if self.btn_clear.clicked(pos):
                self.clear_bet()
            elif self.btn_rebet.clicked(pos, bool(self.last_bet)):
                self.rebet()
            elif self.btn_deal.clicked(pos):
                self.deal()
            elif self.state == "betting" and math.hypot(pos[0] - W / 2, pos[1] - BET_Y) < 48:
                self.undo_chip()
        elif self.state == "player":
            if self.btn_hit.clicked(pos):
                self.hit()
            elif self.btn_stand.clicked(pos):
                self.stand()
            elif self.btn_double.clicked(pos, self.can_double()):
                self.double()
            elif self.btn_split.clicked(pos, self.can_split()):
                self.split()

    # ---- update / draw ---------------------------------------------------
    def hand_centers(self):
        n = len(self.hands)
        return [W / 2 + (i - (n - 1) / 2) * 250 for i in range(n)]

    def update(self, dt):
        if self.queue:
            self.queue[0][0] -= dt
            if self.queue[0][0] <= 0:
                _, fn = self.queue.pop(0)
                fn()

        def layout(hand, cx, y):
            n = len(hand.cards)
            left = cx - (CW + CARD_STEP * (n - 1)) / 2
            for i, c in enumerate(hand.cards):
                c.tx, c.ty = left + i * CARD_STEP, y

        layout(self.dealer, W / 2, DEALER_Y)
        for h, cx in zip(self.hands, self.hand_centers()):
            layout(h, cx, PLAYER_Y)
        for c in [c for h in self.hands for c in h.cards] + self.dealer.cards + self.leaving:
            c.update(dt)
        still = []
        for c in self.leaving:
            if c.arrived():
                self.discards += 1
            else:
                still.append(c)
        self.leaving = still

        k = 1 - math.exp(-dt * 12)
        for f in self.flying:
            f["x"] += (f["tx"] - f["x"]) * k
            f["y"] += (f["ty"] - f["y"]) * k
        self.flying = [f for f in self.flying if abs(f["tx"] - f["x"]) + abs(f["ty"] - f["y"]) > 3]

    def draw_stack(self, surf, amount, cx, cy, label=True):
        chips = chip_breakdown(amount)[:18]
        for i, v in enumerate(chips):
            img = self.assets.chip(v, 26)
            y = cy - i * 6
            pygame.draw.circle(surf, darken(CHIP_STYLE[v][0], 80), (cx, y + 3), 26)
            surf.blit(img, img.get_rect(center=(cx, y)))
        if label and amount:
            draw_pill(surf, money(amount), font(17, bold=True), (cx, cy + 44), WHITE, (0, 0, 0, 170), GOLD_DARK,
                      pad=(10, 3))

    def draw_badge(self, surf, hand, x, y, visible_only=False):
        total, soft = hand.value(visible_only)
        if not hand.cards or total == 0:
            return
        if hand.blackjack and all(c.face_up for c in hand.cards):
            txt, bg = "BJ", (180, 140, 30, 235)
        else:
            txt = f"{total - 10}/{total}" if soft and total < 21 else str(total)
            bg = (170, 30, 35, 235) if total > 21 else (15, 15, 20, 225)
        draw_pill(surf, txt, font(18, bold=True), (x, y), WHITE, bg, GOLD_DARK, pad=(9, 3))

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.table, (0, 0))

        # discard pile
        if self.discards:
            layers = min(12, 1 + self.discards // 8)
            for i in range(layers):
                surf.blit(self.assets.back, (DISCARD_POS[0], DISCARD_POS[1] - i * 1.5))

        # bet circles and chips
        centers = self.hand_centers() if self.hands else [W / 2]
        for cx in centers:
            pygame.draw.circle(surf, (170, 205, 180), (cx, BET_Y), 46, width=2)
            pygame.draw.circle(surf, (120, 170, 135), (cx, BET_Y), 40, width=1)
        if self.hands:
            for h, cx in zip(self.hands, centers):
                if h.result in ("bust", "lose") and self.state == "result":
                    continue
                shown = h.payout if self.state == "result" else h.bet
                self.draw_stack(surf, shown, cx, BET_Y)
        else:
            landed = self.bet - sum(f["v"] for f in self.flying)
            if landed > 0:
                self.draw_stack(surf, landed, W / 2, BET_Y, label=False)
            if self.bet:
                draw_pill(surf, money(self.bet), font(17, bold=True), (W / 2, BET_Y + 44), WHITE, (0, 0, 0, 170),
                          GOLD_DARK, pad=(10, 3))
            else:
                draw_text(surf, "BET", font(20, bold=True, serif=True), (150, 195, 165), (W / 2, BET_Y))
        for f in self.flying:
            surf.blit(self.assets.chip(f["v"], 26), (f["x"], f["y"]))

        # cards
        for c in self.leaving:
            c.draw(surf, self.assets)
        for c in self.dealer.cards:
            c.draw(surf, self.assets)
        for h in self.hands:
            for c in h.cards:
                c.draw(surf, self.assets)

        # value badges
        if self.dealer.cards:
            left = self.dealer.cards[0].tx
            self.draw_badge(surf, self.dealer, left - 32, DEALER_Y + CH / 2, visible_only=True)
        for h, cx in zip(self.hands, centers):
            if h.cards:
                self.draw_badge(surf, h, h.cards[0].tx - 32, PLAYER_Y + CH / 2)

        # active hand marker (when split)
        if self.state == "player" and len(self.hands) > 1:
            cx = centers[self.active]
            y = PLAYER_Y + CH + 12
            pygame.draw.polygon(surf, GOLD, [(cx, y - 8), (cx - 10, y + 6), (cx + 10, y + 6)])

        # per-hand result labels
        styles = {"win": ("WIN", (30, 140, 60, 235)), "blackjack": ("BLACKJACK!", (190, 145, 25, 240)),
                  "push": ("PUSH", (90, 90, 100, 235)), "lose": ("LOSE", (160, 30, 35, 235)),
                  "bust": ("BUST", (160, 30, 35, 235))}
        for h, cx in zip(self.hands, centers):
            if h.result and (self.state == "result" or h.result == "bust"):
                txt, bg = styles[h.result]
                if h.result in ("win", "blackjack"):
                    txt += f"  +{money(h.payout - h.bet)}"
                draw_pill(surf, txt, font(20, bold=True), (cx, PLAYER_Y + CH / 2), WHITE, bg, GOLD, pad=(14, 6))

        # centre message
        if self.message:
            draw_pill(surf, self.message, font(20, bold=True), (W / 2, 322), GOLD, (0, 0, 0, 175), GOLD_DARK)

        # bottom controls
        if self.state in ("betting", "result"):
            tray = pygame.Rect(0, 0, 590, 80)
            tray.center = (W / 2, CHIP_TRAY_Y + 1)
            pygame.draw.rect(surf, (22, 13, 8), tray, border_radius=16)
            pygame.draw.rect(surf, (90, 60, 35), tray, width=2, border_radius=16)
            for v, (cx, cy) in self.chip_pos.items():
                hover = math.hypot(mouse[0] - cx, mouse[1] - cy) <= 31 and not self.busy()
                y = cy - (7 if hover else 0)
                pygame.draw.circle(surf, (0, 0, 0), (cx + 2, cy + 4), 31)
                img = self.assets.chip(v, 31)
                surf.blit(img, img.get_rect(center=(cx, y)))
                if v > self.app.balance:
                    surf.blit(self.dim_chip, self.dim_chip.get_rect(center=(cx, y)))
            self.btn_clear.draw(surf, mouse, self.bet > 0 or self.state == "result")
            self.btn_rebet.draw(surf, mouse, bool(self.last_bet))
            self.btn_deal.text = "DEAL" if self.state == "betting" or self.bet else "DEAL AGAIN"
            self.btn_deal.draw(surf, mouse, self.bet > 0 or (self.state == "result" and bool(self.last_bet)))
        else:
            playing = self.state == "player" and not self.busy()
            self.btn_hit.draw(surf, mouse, playing)
            self.btn_stand.draw(surf, mouse, playing)
            self.btn_double.draw(surf, mouse, playing and self.can_double())
            self.btn_split.draw(surf, mouse, playing and self.can_split())

        self.btn_count.text = "COUNT: ON" if self.show_count else "COUNT: OFF"
        self.btn_count.color = (30, 110, 70) if self.show_count else (50, 60, 90)
        self.btn_count.hint = None
        self.btn_count.draw(surf, mouse)
        if self.show_count:
            decks = len(self.shoe.cards) / 52
            tc = self.rc / max(0.5, decks)
            box = pygame.Rect(1084, 256, 174, 86)
            p = pygame.Surface(box.size, pygame.SRCALPHA)
            pygame.draw.rect(p, (0, 0, 0, 190), p.get_rect(), border_radius=10)
            surf.blit(p, box)
            pygame.draw.rect(surf, GOLD_DARK, box, width=2, border_radius=10)
            col = VALUE_COL[(self.rc > 0) - (self.rc < 0)]
            draw_text(surf, "RUNNING", font(11, bold=True), (190, 200, 190), (box.x + 44, box.y + 16))
            draw_text(surf, fmt_count(self.rc), font(24, bold=True), col, (box.x + 44, box.y + 42))
            draw_text(surf, "TRUE", font(11, bold=True), (190, 200, 190), (box.right - 44, box.y + 16))
            draw_text(surf, f"{tc:+.1f}", font(24, bold=True), col, (box.right - 44, box.y + 42))
            draw_text(surf, f"{decks:.1f} decks left", font(12), (180, 190, 185), (box.centerx, box.bottom - 13))
        self.app.draw_top_bar(surf, "BLACKJACK", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Roulette (European single-zero wheel)
# --------------------------------------------------------------------------
WHEEL_ORDER = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20,
               14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26]
RED_NUMS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
POCKET = 2 * math.pi / 37
WHEEL_C = (300, 345)
WHEEL_R = 205
GX, GY, CELL_W, CELL_H = 626, 130, 46, 62
ZERO_RECT = pygame.Rect(580, GY, 46, 3 * CELL_H)
GRID_RECT = pygame.Rect(GX, GY, 12 * CELL_W, 3 * CELL_H)
SPIN_TIME = 6.0


def num_color(n):
    if n == 0:
        return (20, 130, 60)
    return (185, 25, 32) if n in RED_NUMS else (22, 22, 26)


def cell_rect(n):
    if n == 0:
        return ZERO_RECT
    c, r = (n - 1) // 3, 2 - (n - 1) % 3
    return pygame.Rect(GX + c * CELL_W, GY + r * CELL_H, CELL_W, CELL_H)


def wheel_point(center, angle, radius):
    """Angle is measured clockwise from 12 o'clock."""
    return center[0] + radius * math.sin(angle), center[1] - radius * math.cos(angle)


def make_wheel_rotor(R):
    """The spinning part of the wheel: numbered pockets, frets, cone and turret."""
    k = 2
    Rr = int(R * 0.86)
    size = 2 * Rr * k
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    c = (size / 2, size / 2)
    Rk = R * k
    for i, n in enumerate(WHEEL_ORDER):
        a0, a1 = i * POCKET - POCKET / 2, i * POCKET + POCKET / 2
        col = num_color(n)
        band = [wheel_point(c, a0 + (a1 - a0) * t / 4, Rk * 0.86) for t in range(5)]
        band += [wheel_point(c, a1 - (a1 - a0) * t / 4, Rk * 0.72) for t in range(5)]
        pygame.draw.polygon(s, col, band)
        pocket = [wheel_point(c, a0 + (a1 - a0) * t / 4, Rk * 0.72) for t in range(5)]
        pocket += [wheel_point(c, a1 - (a1 - a0) * t / 4, Rk * 0.60) for t in range(5)]
        pygame.draw.polygon(s, darken(col, 12) if n else lighten(col, 10), pocket)
    for i in range(37):
        a = i * POCKET - POCKET / 2
        pygame.draw.line(s, (205, 205, 210), wheel_point(c, a, Rk * 0.60), wheel_point(c, a, Rk * 0.86), 2 * k)
    pygame.draw.circle(s, (215, 185, 110), c, Rk * 0.86, width=2 * k)
    pygame.draw.circle(s, (205, 205, 210), c, Rk * 0.72, width=2 * k)
    for i, n in enumerate(WHEEL_ORDER):
        img = font(14 * k, bold=True).render(str(n), True, WHITE)
        rot = pygame.transform.rotozoom(img, -math.degrees(i * POCKET), 1)
        s.blit(rot, rot.get_rect(center=wheel_point(c, i * POCKET, Rk * 0.79)))
    # wooden cone
    for j in range(40):
        t = j / 39
        pygame.draw.circle(s, lerp_col((85, 48, 22), (170, 112, 58), t), c, Rk * 0.60 * (1 - t * 0.82))
    pygame.draw.circle(s, GOLD_DARK, c, Rk * 0.60, width=3 * k)
    pygame.draw.circle(s, GOLD_DARK, c, Rk * 0.38, width=k)
    # turret
    for i in range(4):
        a = i * math.pi / 2 + math.pi / 4
        end = wheel_point(c, a, Rk * 0.3)
        pygame.draw.line(s, (150, 115, 45), c, end, 5 * k)
        pygame.draw.line(s, GOLD, c, end, 3 * k)
        pygame.draw.circle(s, GOLD, end, Rk * 0.035)
    pygame.draw.circle(s, (150, 115, 45), c, Rk * 0.085)
    pygame.draw.circle(s, GOLD, c, Rk * 0.07)
    pygame.draw.circle(s, (255, 240, 190), (c[0] - Rk * 0.02, c[1] - Rk * 0.02), Rk * 0.025)
    return pygame.transform.smoothscale(s, (2 * Rr, 2 * Rr))


def make_roulette_bg():
    surf = make_wood()
    rng = random.Random(11)
    felt_rect = pygame.Rect(12, 66, W - 24, 556)
    pygame.draw.rect(surf, (28, 16, 11), felt_rect.inflate(26, 26).move(0, 5), border_radius=48)
    pygame.draw.rect(surf, (48, 28, 19), felt_rect.inflate(26, 26), border_radius=48)
    pygame.draw.rect(surf, (78, 48, 32), felt_rect.inflate(14, 14), width=3, border_radius=44)

    felt = pygame.Surface((W, H), pygame.SRCALPHA)
    dark, light = (8, 62, 36), (24, 122, 68)
    felt.fill((*dark, 255))
    for i in range(48):
        t = i / 47
        rw, rh = 1900 * (1 - t) + 160, 1100 * (1 - t) + 100
        pygame.draw.ellipse(felt, lerp_col(dark, light, t ** 0.8), (W / 2 - rw / 2, 340 - rh / 2, rw, rh))
    for _ in range(25000):
        x, y = rng.randrange(W), rng.randrange(H)
        r, g, b, a = felt.get_at((x, y))
        d = rng.choice((-7, -4, 4, 6))
        felt.set_at((x, y), (max(0, r + d), max(0, min(255, g + d)), max(0, b + d), a))
    mask = pygame.Surface((W, H), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), felt_rect, border_radius=36)
    felt.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(felt, (0, 0))

    # wheel bowl (static part)
    cx, cy = WHEEL_C
    R = WHEEL_R
    pygame.draw.circle(surf, (5, 30, 16), (cx + 6, cy + 10), R + 22)
    for j in range(20):
        t = j / 19
        pygame.draw.circle(surf, lerp_col((55, 30, 14), (130, 80, 40), math.sin(t * math.pi)), (cx, cy),
                           R + 20 - j)
    pygame.draw.circle(surf, GOLD_DARK, (cx, cy), R + 1, width=2)
    for j in range(12):
        t = j / 11
        pygame.draw.circle(surf, lerp_col((70, 44, 26), (40, 25, 16), t), (cx, cy), R - 2 - j * (R * 0.13 / 11))
    pygame.draw.circle(surf, (95, 65, 40), (cx, cy), R * 0.965, width=1)
    for i in range(8):  # ball deflectors
        a = i * math.pi / 4 + math.pi / 8
        p = wheel_point((cx, cy), a, R * 0.895)
        pts = [wheel_point(p, a, 7), wheel_point(p, a + math.pi / 2, 3),
               wheel_point(p, a + math.pi, 7), wheel_point(p, a - math.pi / 2, 3)]
        pygame.draw.polygon(surf, (215, 190, 120), pts)

    # betting layout
    line = (235, 235, 225)
    for n in range(1, 37):
        r = cell_rect(n)
        pygame.draw.rect(surf, num_color(n), r)
        draw_text(surf, str(n), font(22, bold=True, serif=True), WHITE, r.center)
    pygame.draw.rect(surf, num_color(0), ZERO_RECT, border_top_left_radius=23, border_bottom_left_radius=23)
    pygame.draw.rect(surf, line, ZERO_RECT, width=2, border_top_left_radius=23, border_bottom_left_radius=23)
    draw_text(surf, "0", font(26, bold=True, serif=True), WHITE, ZERO_RECT.center)
    for c in range(13):
        pygame.draw.line(surf, line, (GX + c * CELL_W, GY), (GX + c * CELL_W, GY + 3 * CELL_H), 2)
    for r in range(4):
        pygame.draw.line(surf, line, (GX, GY + r * CELL_H), (GX + 12 * CELL_W, GY + r * CELL_H), 2)
    return surf


class Roulette:
    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.bg = make_roulette_bg()
        self.rotor = make_wheel_rotor(WHEEL_R)
        self.wheel_angle = 0.0
        self.wheel_speed = 0.35
        self.bets = {}            # key -> {"nums", "pos", "amount", "won"}
        self.history = []         # placement order for undo
        self.last_bets = {}
        self.results = []
        self.selected = 10
        self.state = "betting"    # betting, spinning, result
        self.message = "PLACE YOUR BETS"
        self.win_number = None
        self.ball_rel = None
        self.ball_r = 0
        self.spin_t = 0
        self.clicks = []
        self.pulse = 0

        self.outside = self.build_outside()
        for key, rect, nums, label in self.outside:
            pygame.draw.rect(self.bg, (235, 235, 225), rect, width=2)
            if key in ("red", "black"):
                cx, cy = rect.center
                pts = [(cx, cy - 16), (cx + 28, cy), (cx, cy + 16), (cx - 28, cy)]
                pygame.draw.polygon(self.bg, num_color(1 if key == "red" else 2), pts)
                pygame.draw.polygon(self.bg, (235, 235, 225), pts, width=2)
            else:
                draw_text(self.bg, label, font(17, bold=True), WHITE, rect.center)

        x0 = W / 2 - 3.5 * 70
        self.chip_pos = CHIP_WIN
        self.btn_clear = Button((22, 652, 100, 52), "CLEAR", (120, 35, 40), 18)
        self.btn_undo = Button((130, 652, 100, 52), "UNDO", (80, 80, 90), 18)
        self.btn_rebet = Button((238, 652, 100, 52), "REBET", (40, 70, 130), 18)
        self.btn_spin = Button((965, 646, 270, 62), "SPIN", (25, 120, 60), 28, "SPACE")
        self.dim_chip = pygame.Surface((64, 64), pygame.SRCALPHA)
        pygame.draw.circle(self.dim_chip, (0, 0, 0, 150), (32, 32), 31)

    @staticmethod
    def build_outside():
        regions = []
        for r in range(3):      # top row of "2 to 1" is the column holding 3, 6, 9...
            nums = frozenset(n for n in range(1, 37) if n % 3 == (0, 2, 1)[r])
            regions.append((f"col{3 - r}", pygame.Rect(GX + 12 * CELL_W, GY + r * CELL_H, 60, CELL_H), nums,
                            "2 TO 1"))
        for d in range(3):
            regions.append((f"dozen{d + 1}", pygame.Rect(GX + d * 4 * CELL_W, GY + 3 * CELL_H, 4 * CELL_W, 50),
                            frozenset(range(d * 12 + 1, d * 12 + 13)), ["1st 12", "2nd 12", "3rd 12"][d]))
        evens = [
            ("low", range(1, 19), "1 - 18"), ("even", range(2, 37, 2), "EVEN"), ("red", RED_NUMS, "RED"),
            ("black", set(range(1, 37)) - RED_NUMS, "BLACK"), ("odd", range(1, 37, 2), "ODD"),
            ("high", range(19, 37), "19 - 36"),
        ]
        for i, (key, nums, label) in enumerate(evens):
            regions.append((key, pygame.Rect(GX + i * 2 * CELL_W, GY + 3 * CELL_H + 50, 2 * CELL_W, 50),
                            frozenset(nums), label))
        return regions

    # ---- helpers ---------------------------------------------------------
    @property
    def total_bet(self):
        return sum(b["amount"] for b in self.bets.values())

    def can_leave(self):
        return self.state != "spinning"

    def outstanding_bets(self):
        return self.total_bet if self.state != "result" else 0

    def leave(self):
        self.app.balance += self.outstanding_bets()
        self.bets, self.history = {}, []
        self.state = "betting"
        self.message = "PLACE YOUR BETS"

    def target_at(self, pos):
        """What bet a click at pos would make: (key, numbers, chip position)."""
        x, y = pos
        if ZERO_RECT.collidepoint(pos):
            return (0,), frozenset({0}), ZERO_RECT.center
        if GRID_RECT.collidepoint(pos):
            gx, gy = (x - GX) / CELL_W, (y - GY) / CELL_H
            c, r = int(gx), int(gy)
            fx, fy = gx - c, gy - r
            dx = -1 if fx < 0.25 and c > 0 else 1 if fx > 0.75 and c < 11 else 0
            dy = -1 if fy < 0.25 and r > 0 else 1 if fy > 0.75 and r < 2 else 0
            cells = {(c, r), (c + dx, r), (c, r + dy), (c + dx, r + dy)}
            nums = frozenset(3 * cc + 3 - rr for cc, rr in cells)
            px = GX + (c + (0.5 if dx == 0 else 1 if dx > 0 else 0)) * CELL_W
            py = GY + (r + (0.5 if dy == 0 else 1 if dy > 0 else 0)) * CELL_H
            return tuple(sorted(nums)), nums, (px, py)
        for key, rect, nums, _ in self.outside:
            if rect.collidepoint(pos):
                return key, nums, rect.center
        return None

    def new_round_if_needed(self):
        if self.state == "result":
            self.bets, self.history = {}, []
            self.state = "betting"
            self.win_number = None
            self.message = "PLACE YOUR BETS"

    # ---- betting ---------------------------------------------------------
    def place(self, pos, value=None):
        t = self.target_at(pos)
        if not t:
            return False
        self.new_round_if_needed()
        v = value or self.selected
        if self.app.balance < v:
            self.message = "NOT ENOUGH CHIPS"
            return False
        key, nums, cpos = t
        self.app.balance -= v
        bet = self.bets.setdefault(key, {"nums": nums, "pos": cpos, "amount": 0, "won": 0})
        bet["amount"] += v
        self.history.append((key, v))
        self.message = ""
        self.app.sfx("chip")
        return True

    def remove_at(self, pos):
        t = self.target_at(pos)
        if self.state == "betting" and t and t[0] in self.bets:
            self.app.balance += self.bets.pop(t[0])["amount"]
            self.history = [h for h in self.history if h[0] != t[0]]
            self.app.sfx("chip")

    def clear(self):
        self.new_round_if_needed()
        self.app.balance += self.total_bet
        self.bets, self.history = {}, []
        self.message = "PLACE YOUR BETS"

    def undo(self):
        if self.state == "betting" and self.history:
            key, v = self.history.pop()
            self.bets[key]["amount"] -= v
            if self.bets[key]["amount"] <= 0:
                del self.bets[key]
            self.app.balance += v
            self.app.sfx("chip")

    def rebet(self):
        self.clear()
        need = sum(b["amount"] for b in self.last_bets.values())
        if not need:
            return
        if self.app.balance < need:
            self.message = "NOT ENOUGH CHIPS TO REBET"
            return
        for key, b in self.last_bets.items():
            self.app.balance -= b["amount"]
            self.bets[key] = dict(b, won=0)
            self.history.append((key, b["amount"]))
        self.message = ""
        self.app.sfx("chip")

    def spin(self):
        if self.state == "result" and self.last_bets:
            self.rebet()
        self.new_round_if_needed()
        if not self.bets:
            self.message = "PLACE A BET FIRST"
            return
        self.last_bets = {k: dict(b) for k, b in self.bets.items()}
        self.state = "spinning"
        self.message = "NO MORE BETS"
        self.target_idx = random.randrange(37)
        self.rel_target = self.target_idx * POCKET
        self.rel0 = self.rel_target + 2 * math.pi * 5 + random.uniform(0, 2 * math.pi)
        self.spin_t = 0
        self.clicks = [0.64, 0.70, 0.77, 0.85]
        self.app.sfx("card")

    def settle(self):
        n = WHEEL_ORDER[self.target_idx]
        self.win_number = n
        self.results = ([n] + self.results)[:16]
        total_bet = self.total_bet
        total_return = 0
        for b in self.bets.values():
            b["won"] = b["amount"] * 36 // len(b["nums"]) if n in b["nums"] else 0
            total_return += b["won"]
        self.bets = {k: b for k, b in self.bets.items() if b["won"]}
        self.app.balance += total_return
        self.app.record("roulette", total_bet, total_return)
        if any(len(b["nums"]) == 1 for b in self.bets.values()):
            self.app.unlock("straight_up")
        net = total_return - total_bet
        colour = "GREEN" if n == 0 else "RED" if n in RED_NUMS else "BLACK"
        if net > 0:
            self.message = f"{n} {colour}  -  YOU WIN {money(net)}!"
            self.app.float_text(f"+{money(net)}", (80, 230, 110))
            self.app.sfx("win")
        elif net < 0:
            self.message = f"{n} {colour}  -  YOU LOSE {money(-net)}"
            self.app.float_text(f"-{money(-net)}", (240, 90, 90))
            self.app.sfx("lose")
        else:
            self.message = f"{n} {colour}  -  BREAK EVEN"
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        self.state = "result"
        self.app.save()

    # ---- events ----------------------------------------------------------
    def handle(self, e):
        if self.state == "spinning":
            return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for v, (cx, cy) in self.chip_pos.items():
                if math.hypot(e.pos[0] - cx, e.pos[1] - cy) <= 31:
                    self.selected = v
                    self.app.sfx("chip")
                    return
            if self.btn_clear.clicked(e.pos):
                self.clear()
            elif self.btn_undo.clicked(e.pos, bool(self.history) and self.state == "betting"):
                self.undo()
            elif self.btn_rebet.clicked(e.pos, bool(self.last_bets)):
                self.rebet()
            elif self.btn_spin.clicked(e.pos):
                self.spin()
            else:
                self.place(e.pos)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            self.remove_at(e.pos)
        elif e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.spin()
            elif e.key == pygame.K_z and e.mod & pygame.KMOD_CTRL or e.key == pygame.K_BACKSPACE:
                self.undo()

    # ---- update / draw ---------------------------------------------------
    def update(self, dt):
        self.pulse += dt
        if self.state == "spinning":
            self.spin_t += dt
            p = min(1.0, self.spin_t / SPIN_TIME)
            self.wheel_speed = 0.35 + 0.9 * (1 - p)
            ease = 1 - (1 - p) ** 3
            rel = self.rel0 + (self.rel_target - self.rel0) * ease
            track, pocket = WHEEL_R * 0.935, WHEEL_R * 0.66
            if p < 0.62:
                r = track
            else:
                q = (p - 0.62) / 0.38
                drop = min(1.0, q * 1.6)
                r = track + (pocket - track) * drop + abs(math.sin(q * math.pi * 4)) * (1 - q) * WHEEL_R * 0.05
                rel += 0.12 * math.sin(q * math.pi * 3) * (1 - q)
            self.ball_rel, self.ball_r = rel, r
            while self.clicks and p >= self.clicks[0]:
                self.clicks.pop(0)
                self.app.sfx("card")
            if self.spin_t >= SPIN_TIME + 0.4:
                self.settle()
        self.wheel_angle = (self.wheel_angle + self.wheel_speed * dt) % (2 * math.pi)

    def draw_stack(self, surf, amount, pos, radius=16):
        cx, cy = pos
        for i, v in enumerate(chip_breakdown(amount)[:10]):
            y = cy - i * 4
            pygame.draw.circle(surf, darken(CHIP_STYLE[v][0], 80), (cx, y + 2), radius)
            img = self.assets.chip(v, radius)
            surf.blit(img, img.get_rect(center=(cx, y)))

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))

        # wheel + ball
        rot = pygame.transform.rotozoom(self.rotor, -math.degrees(self.wheel_angle), 1)
        surf.blit(rot, rot.get_rect(center=WHEEL_C))
        if self.ball_rel is not None:
            bx, by = wheel_point(WHEEL_C, self.wheel_angle + self.ball_rel, self.ball_r)
            pygame.draw.circle(surf, (0, 0, 0), (bx + 2, by + 3), 7)
            if not draw_theme_ball(surf, bx, by, 8):
                pygame.draw.circle(surf, (235, 235, 230), (bx, by), 7)
                pygame.draw.circle(surf, WHITE, (bx - 2, by - 2), 3)
        if self.state == "result":
            n = self.win_number
            draw_pill(surf, f"  {n}  ", font(30, bold=True, serif=True), (WHEEL_C[0], 600), WHITE,
                      (*num_color(n), 255), GOLD, pad=(16, 2))

        # hover preview of which numbers a click would cover
        if self.state != "spinning":
            t = self.target_at(mouse)
            if t:
                key, nums, _ = t
                regions = [r for k, r, _, _ in self.outside if k == key] or [cell_rect(n) for n in nums]
                for r in regions:
                    hl = pygame.Surface(r.size, pygame.SRCALPHA)
                    hl.fill((255, 255, 255, 70))
                    surf.blit(hl, r)

        # winning number marker (the "dolly")
        if self.state == "result":
            r = cell_rect(self.win_number)
            glow = 3 + int(2 * math.sin(self.pulse * 6))
            pygame.draw.rect(surf, GOLD, r.inflate(glow * 2, glow * 2), width=3, border_radius=4)
            pygame.draw.circle(surf, (40, 40, 40), (r.centerx + 1, r.bottom - 12), 9)
            pygame.draw.circle(surf, (250, 250, 250), (r.centerx, r.bottom - 14), 9)
            pygame.draw.circle(surf, GOLD, (r.centerx, r.bottom - 14), 9, width=2)

        # chips on the layout
        for b in self.bets.values():
            self.draw_stack(surf, b["won"] or b["amount"], b["pos"])

        # history of results
        draw_text(surf, "LAST NUMBERS", font(14, bold=True), (200, 225, 205), (580, 452), anchor="midleft")
        for i, n in enumerate(self.results):
            c = (720 + i * 34, 452)
            pygame.draw.circle(surf, num_color(n), c, 15)
            pygame.draw.circle(surf, GOLD if i == 0 else (200, 200, 200), c, 15, width=2)
            draw_text(surf, str(n), font(14, bold=True), WHITE, c)

        info = "TOTAL WIN" if self.state == "result" else "TOTAL BET"
        amount = sum(b["won"] for b in self.bets.values()) if self.state == "result" else self.total_bet
        draw_text(surf, f"{info}:  {money(amount)}", font(20, bold=True), GOLD, (909, 505))
        if self.message:
            draw_pill(surf, self.message, font(20, bold=True), (909, 560), GOLD, (0, 0, 0, 175), GOLD_DARK)
        elif self.state == "betting":
            draw_text(surf, "Click the layout to place chips  -  right-click a bet to remove it",
                      font(15), (200, 225, 205), (909, 560))

        # chip tray + buttons
        tray = pygame.Rect(0, 0, 590, 80)
        tray.center = (W / 2, CHIP_TRAY_Y + 1)
        pygame.draw.rect(surf, (22, 13, 8), tray, border_radius=16)
        pygame.draw.rect(surf, (90, 60, 35), tray, width=2, border_radius=16)
        spinning = self.state == "spinning"
        for v, (cx, cy) in self.chip_pos.items():
            sel = v == self.selected
            hover = math.hypot(mouse[0] - cx, mouse[1] - cy) <= 31 and not spinning
            y = cy - (9 if sel else 5 if hover else 0)
            pygame.draw.circle(surf, (0, 0, 0), (cx + 2, cy + 4), 31)
            if sel:
                pygame.draw.circle(surf, GOLD, (cx, y), 35, width=3)
            img = self.assets.chip(v, 31)
            surf.blit(img, img.get_rect(center=(cx, y)))
            if v > self.app.balance or spinning:
                surf.blit(self.dim_chip, self.dim_chip.get_rect(center=(cx, y)))
        self.btn_clear.draw(surf, mouse, not spinning and (self.total_bet > 0 or self.state == "result"))
        self.btn_undo.draw(surf, mouse, self.state == "betting" and bool(self.history))
        self.btn_rebet.draw(surf, mouse, not spinning and bool(self.last_bets))
        self.btn_spin.text = "SPIN AGAIN" if self.state == "result" else "SPIN"
        self.btn_spin.draw(surf, mouse, not spinning and (self.total_bet > 0 or
                                                          (self.state == "result" and bool(self.last_bets))))

        self.app.draw_top_bar(surf, "ROULETTE", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Rocket (crash game)
# --------------------------------------------------------------------------
ROCKET_RATE = 0.085                       # multiplier = e^(rate * seconds): 2x at ~8s, 10x at ~27s
GRAPH = pygame.Rect(60, 104, 1160, 490)
AUTO_OPTIONS = [None, 1.5, 2.0, 3.0, 5.0, 10.0]


def make_rocket_sprite():
    k = 3
    w, h = 84 * k, 40 * k
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    cy = h / 2
    red, dred = (210, 35, 45), (140, 20, 28)
    pygame.draw.polygon(s, dred, [(10 * k, cy - 6 * k), (2 * k, cy - 19 * k), (16 * k, cy - 19 * k),
                                  (30 * k, cy - 7 * k)])
    pygame.draw.polygon(s, dred, [(10 * k, cy + 6 * k), (2 * k, cy + 19 * k), (16 * k, cy + 19 * k),
                                  (30 * k, cy + 7 * k)])
    pygame.draw.rect(s, (90, 90, 100), (4 * k, cy - 6 * k, 8 * k, 12 * k), border_radius=2 * k)
    pygame.draw.ellipse(s, red, (38 * k, cy - 9 * k, 44 * k, 18 * k))
    pygame.draw.rect(s, (236, 236, 242), (10 * k, cy - 9 * k, 50 * k, 18 * k), border_radius=6 * k)
    pygame.draw.rect(s, (195, 195, 205), (10 * k, cy + 3 * k, 50 * k, 6 * k),
                     border_bottom_left_radius=6 * k, border_bottom_right_radius=6 * k)
    pygame.draw.rect(s, red, (18 * k, cy - 9 * k, 5 * k, 18 * k))
    pygame.draw.circle(s, (200, 200, 210), (46 * k, cy - k), 6 * k)
    pygame.draw.circle(s, (60, 140, 220), (46 * k, cy - k), 4.5 * k)
    pygame.draw.circle(s, (210, 235, 255), (44.5 * k, cy - 2.5 * k), 1.5 * k)
    return pygame.transform.smoothscale(s, (84, 40))


def make_space_bg():
    s = pygame.Surface((W, H))
    for y in range(H):
        pygame.draw.line(s, lerp_col((6, 8, 24), (30, 10, 46), y / H), (0, y), (W, y))
    rng = random.Random(9)
    glow = pygame.Surface((W, H), pygame.SRCALPHA)
    for _ in range(14):
        x, y, r = rng.uniform(0, W), rng.uniform(80, H), rng.uniform(120, 300)
        col = rng.choice([(120, 60, 200), (40, 90, 200), (200, 60, 140)])
        for i in range(10):
            rr = r * (1 - i / 10)
            pygame.draw.ellipse(glow, (*col, 3), (x - rr, y - rr * 0.6, rr * 2, rr * 1.2))
    s.blit(glow, (0, 0))
    for i in range(40):  # planet, bottom right
        t = i / 39
        pygame.draw.circle(s, lerp_col((25, 35, 80), (70, 110, 190), t ** 1.5),
                           (1150 - t * 60, 760 - t * 60), 260 * (1 - t * 0.55))
    for _ in range(300):
        c = rng.randint(60, 150)
        s.set_at((rng.randrange(W), rng.randrange(56, H)), (c, c, c + 20))
    return s


class Rocket:
    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.bg = make_space_bg()
        self.sprite = make_rocket_sprite()
        rng = random.Random(5)
        self.stars = [[rng.uniform(0, W), rng.uniform(56, H), rng.choice((0.25, 0.5, 1.0))] for _ in range(160)]
        self.canvas = pygame.Surface((W, H))
        self.fx = pygame.Surface((W, H), pygame.SRCALPHA)
        self.panel = pygame.Surface(GRAPH.size, pygame.SRCALPHA)
        pygame.draw.rect(self.panel, (0, 0, 0, 95), self.panel.get_rect(), border_radius=18)
        pygame.draw.rect(self.panel, (*GOLD_DARK, 255), self.panel.get_rect(), width=2, border_radius=18)

        self.bet_chips = []
        self.last_bet = []
        self.stake = 0
        self.state = "betting"          # betting, flying, crashed
        self.t = 0.0
        self.mult = 1.0
        self.crash_at = 1.0
        self.cashed = None
        self.cashed_t = 0.0
        self.cashed_win = 0
        self.history = []
        self.auto_idx = 0
        self.particles = []
        self.shake = 0.0
        self.flash = 0.0
        self.message = "PLACE YOUR BET, THEN LAUNCH"

        x0 = W / 2 - 3.5 * 70
        self.chip_pos = CHIP_WIN
        self.btn_clear = Button((22, 652, 100, 52), "CLEAR", (120, 35, 40), 18)
        self.btn_rebet = Button((130, 652, 100, 52), "REBET", (40, 70, 130), 18)
        self.btn_auto = Button((238, 652, 100, 52), "AUTO", (80, 80, 90), 17, "OFF")
        self.btn_main = Button((965, 646, 270, 62), "LAUNCH", (25, 120, 60), 26, "SPACE")
        self.dim_chip = pygame.Surface((64, 64), pygame.SRCALPHA)
        pygame.draw.circle(self.dim_chip, (0, 0, 0, 150), (32, 32), 31)

    # ---- helpers ---------------------------------------------------------
    @property
    def bet(self):
        return sum(self.bet_chips)

    def can_leave(self):
        return self.state != "flying"

    def outstanding_bets(self):
        return self.bet + (self.stake if self.state == "flying" and self.cashed is None else 0)

    def leave(self):
        self.app.balance += self.bet
        self.bet_chips = []
        self.new_round()

    def new_round(self):
        if self.state == "crashed":
            self.state = "betting"
            self.t, self.mult, self.cashed = 0.0, 1.0, None
            self.message = "PLACE YOUR BET, THEN LAUNCH"

    def scale(self):
        return max(8.0, self.t * 1.18), max(2.0, self.mult * 1.25)

    @staticmethod
    def to_screen(t, m, tmax, mmax):
        return (GRAPH.x + 24 + t / tmax * (GRAPH.w - 48),
                GRAPH.bottom - 24 - (m - 1) / (mmax - 1) * (GRAPH.h - 48))

    def geometry(self):
        """Rocket position (tip of the curve) and heading in degrees."""
        tmax, mmax = self.scale()
        x, y = self.to_screen(self.t, self.mult, tmax, mmax)
        dx = (GRAPH.w - 48) / tmax
        dy = ROCKET_RATE * self.mult * (GRAPH.h - 48) / (mmax - 1)
        return x, y, max(12.0, min(60.0, math.degrees(math.atan2(dy, dx))))

    # ---- betting / actions -----------------------------------------------
    def add_chip(self, v):
        self.new_round()
        if self.state != "betting":
            return
        if self.app.balance < v:
            self.message = "NOT ENOUGH CHIPS"
            return
        self.app.balance -= v
        self.bet_chips.append(v)
        self.message = ""
        self.app.sfx("chip")

    def all_in(self):
        """ALL IN: every chip you have goes on the table."""
        for v in chip_breakdown(self.app.balance):
            before = self.app.balance
            self.add_chip(v)
            if self.app.balance == before:
                break
        if self.bet:
            self.message = f"ALL IN!  BET {money(self.bet)}"

    def clear(self):
        self.new_round()
        if self.state == "betting":
            self.app.balance += self.bet
            self.bet_chips = []

    def rebet(self):
        self.clear()
        if not self.last_bet or self.state != "betting":
            return
        if self.app.balance < sum(self.last_bet):
            self.message = "NOT ENOUGH CHIPS TO REBET"
            return
        for v in self.last_bet:
            self.add_chip(v)

    def launch(self):
        if self.state == "crashed":
            self.new_round()
            if not self.bet_chips:
                self.rebet()
        if self.state != "betting":
            return
        if not self.bet:
            self.message = "PLACE A BET FIRST"
            return
        self.last_bet = list(self.bet_chips)
        self.stake = self.bet
        self.bet_chips = []
        # 97% payback: P(reaching multiplier m) = 0.97 / m
        u = random.random()
        self.crash_at = max(1.0, math.floor(0.97 / (1 - u) * 100) / 100)
        self.state = "flying"
        self.t, self.mult, self.cashed = 0.0, 1.0, None
        self.message = ""
        self.app.sfx("launch")

    def cash_out(self, at=None):
        if self.state != "flying" or self.cashed is not None:
            return
        self.cashed = at or self.mult
        self.cashed_t = self.t
        self.cashed_win = self.stake * int(round(self.cashed * 100)) // 100
        self.app.balance += self.cashed_win
        self.app.record("rocket", self.stake, self.cashed_win)
        if self.cashed >= 10:
            self.app.unlock("to_the_moon")
        self.app.float_text(f"+{money(self.cashed_win)}", (80, 230, 110))
        self.message = f"CASHED OUT AT {self.cashed:.2f}x  -  YOU WON {money(self.cashed_win - self.stake)}"
        self.app.sfx("win")
        self.app.save()

    def crash(self):
        self.state = "crashed"
        self.mult = self.crash_at
        self.history = ([self.crash_at] + self.history)[:14]
        x, y, _ = self.geometry()
        for _ in range(120):
            a = random.uniform(0, 2 * math.pi)
            sp = random.uniform(40, 430)
            life = random.uniform(0.5, 1.4)
            self.particles.append({"x": x, "y": y, "vx": math.cos(a) * sp, "vy": math.sin(a) * sp,
                                   "life": life, "max": life, "size": random.uniform(3, 9), "grow": False,
                                   "col": random.choice([(255, 230, 120), (255, 160, 40), (240, 80, 30),
                                                         (130, 130, 140)])})
        self.shake, self.flash = 0.45, 1.0
        self.app.sfx("boom")
        if self.cashed is None:
            self.app.record("rocket", self.stake, 0)
            self.message = f"CRASHED AT {self.crash_at:.2f}x  -  YOU LOST {money(self.stake)}"
            self.app.float_text(f"-{money(self.stake)}", (240, 90, 90))
        else:
            self.message = f"CRASHED AT {self.crash_at:.2f}x  -  YOU CASHED OUT AT {self.cashed:.2f}x"
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        self.app.save()

    # ---- events ----------------------------------------------------------
    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN):
            if self.state == "flying":
                self.cash_out()
            else:
                self.launch()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.btn_main.clicked(e.pos):
                if self.state == "flying":
                    self.cash_out()
                else:
                    self.launch()
                return
            if self.btn_auto.clicked(e.pos):
                self.auto_idx = (self.auto_idx + 1) % len(AUTO_OPTIONS)
                self.app.sfx("chip")
                return
            if self.state == "flying":
                return
            for v, (cx, cy) in self.chip_pos.items():
                if math.hypot(e.pos[0] - cx, e.pos[1] - cy) <= 31:
                    self.add_chip(v)
                    return
            if self.btn_clear.clicked(e.pos):
                self.clear()
            elif self.btn_rebet.clicked(e.pos, bool(self.last_bet)):
                self.rebet()

    # ---- update / draw ---------------------------------------------------
    def update(self, dt):
        if self.state == "flying":
            self.t += dt
            m = math.exp(ROCKET_RATE * self.t)
            auto = AUTO_OPTIONS[self.auto_idx]
            if auto and self.cashed is None and m >= auto and auto <= self.crash_at:
                self.cash_out(auto)
            if m >= self.crash_at:
                self.crash()
            else:
                self.mult = math.floor(m * 100) / 100
                x, y, ang = self.geometry()
                dx, dy = math.cos(math.radians(ang)), -math.sin(math.radians(ang))
                for _ in range(2):
                    self.particles.append({"x": x - dx * 44, "y": y - dy * 44,
                                           "vx": -dx * 80 + random.uniform(-25, 25),
                                           "vy": -dy * 80 + random.uniform(-25, 25),
                                           "life": 0.9, "max": 0.9, "size": random.uniform(4, 7), "grow": True,
                                           "col": (190, 190, 205)})
        speed = 60 + 110 * math.log(self.mult) if self.state == "flying" else 8
        for s in self.stars:
            s[0] -= s[2] * speed * dt
            s[1] += s[2] * speed * 0.35 * dt
            if s[0] < 0:
                s[0] += W
                s[1] = random.uniform(56, H)
            if s[1] > H:
                s[1] -= H - 56
        for p in self.particles:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["vx"] *= 1 - dt * 1.8
            p["vy"] *= 1 - dt * 1.8
            p["life"] -= dt
        self.particles = [p for p in self.particles if p["life"] > 0]
        self.shake = max(0.0, self.shake - dt)
        self.flash = max(0.0, self.flash - dt * 2.5)

    def draw_grid(self, c, tmax, mmax):
        f = font(13, bold=True)
        step = next(s for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 5000)
                    if (mmax - 1) / s <= 5)
        v = 1.0
        while v < mmax:
            _, y = self.to_screen(0, v, tmax, mmax)
            pygame.draw.line(c, (60, 60, 90), (GRAPH.x + 24, y), (GRAPH.right - 24, y), 1)
            draw_text(c, f"{v:.1f}x" if step < 1 else f"{v:.0f}x", f, (150, 150, 180), (GRAPH.x + 30, y - 9),
                      anchor="midleft")
            v += step
        tstep = next(s for s in (2, 5, 10, 20, 30, 60, 120, 300) if tmax / s <= 8)
        t = tstep
        while t < tmax:
            x, _ = self.to_screen(t, 1, tmax, mmax)
            pygame.draw.line(c, (45, 45, 70), (x, GRAPH.y + 24), (x, GRAPH.bottom - 24), 1)
            draw_text(c, f"{t}s", f, (130, 130, 160), (x, GRAPH.bottom - 12))
            t += tstep

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        c = self.canvas
        c.blit(self.bg, (0, 0))
        for x, y, z in self.stars:
            b = int(120 + 135 * z)
            pygame.draw.circle(c, (b, b, min(255, b + 20)), (x, y), 1 if z < 1 else 2)
        c.blit(self.panel, GRAPH)
        tmax, mmax = self.scale()
        self.draw_grid(c, tmax, mmax)

        self.fx.fill((0, 0, 0, 0))
        col = (240, 70, 60) if self.state == "crashed" else (80, 230, 110) if self.cashed else (255, 200, 60)
        if self.t > 0:
            pts = [self.to_screen(self.t * i / 60, min(self.mult, math.exp(ROCKET_RATE * self.t * i / 60)),
                                  tmax, mmax) for i in range(61)]
            pygame.draw.polygon(self.fx, (*col, 45), pts + [(pts[-1][0], GRAPH.bottom - 24),
                                                          (pts[0][0], GRAPH.bottom - 24)])
        for p in self.particles:
            k = p["life"] / p["max"]
            size = p["size"] * (1 + (1 - k) * 2) if p["grow"] else p["size"] * (0.4 + 0.6 * k)
            pygame.draw.circle(self.fx, (*p["col"], int(200 * k)), (p["x"], p["y"]), size)
        c.blit(self.fx, (0, 0))
        if self.t > 0:
            pygame.draw.lines(c, col, False, pts, 4)
        if self.cashed:
            cx, cy = self.to_screen(self.cashed_t, self.cashed, tmax, mmax)
            pygame.draw.circle(c, (80, 230, 110), (cx, cy), 7)
            pygame.draw.circle(c, WHITE, (cx, cy), 7, width=2)
            draw_pill(c, f"{self.cashed:.2f}x  +{money(self.cashed_win)}", font(15, bold=True), (cx, cy - 24),
                      WHITE, (20, 110, 50, 230), None, pad=(10, 3))

        # the rocket
        if self.state != "crashed":
            x, y, ang = self.geometry()
            art = pygame.Surface((150, 60), pygame.SRCALPHA)
            if self.state == "flying":
                fl = random.uniform(26, 44)
                pygame.draw.polygon(art, (255, 120, 30), [(66, 22), (66 - fl, 30), (66, 38)])
                pygame.draw.polygon(art, (255, 230, 120), [(66, 26), (66 - fl * 0.55, 30), (66, 34)])
            art.blit(self.sprite, (60, 10))
            rot = pygame.transform.rotozoom(art, ang, 1)
            a = math.radians(ang)
            c.blit(rot, rot.get_rect(center=(x - math.cos(a) * 27, y + math.sin(a) * 27)))

        # multiplier readout
        mcol = (240, 70, 60) if self.state == "crashed" else (80, 230, 110) if self.cashed else \
            WHITE if self.state == "flying" else (150, 150, 170)
        draw_text(c, f"{self.mult:.2f}x", font(96, bold=True), mcol, (GRAPH.centerx, GRAPH.y + 120),
                  shadow=(0, 0, 0))
        if self.state == "flying" and self.cashed is None:
            draw_text(c, f"CASH OUT NOW FOR {money(self.stake * int(round(self.mult * 100)) // 100)}",
                      font(20, bold=True), GOLD, (GRAPH.centerx, GRAPH.y + 190))
        if self.message:
            draw_pill(c, self.message, font(20, bold=True), (GRAPH.centerx, GRAPH.y + 190 +
                      (0 if self.state != "flying" or self.cashed else 40)), GOLD, (0, 0, 0, 185), GOLD_DARK)

        # bet info + history
        stake = self.stake if self.state == "flying" or (self.state == "crashed" and not self.bet) else self.bet
        draw_text(c, f"BET  {money(stake)}", font(22, bold=True), WHITE, (GRAPH.x + 22, GRAPH.y + 24),
                  anchor="midleft")
        auto = AUTO_OPTIONS[self.auto_idx]
        if auto:
            draw_text(c, f"AUTO CASH OUT AT {auto:.2f}x", font(14, bold=True), (80, 230, 110),
                      (GRAPH.x + 22, GRAPH.y + 50), anchor="midleft")
        for i, v in enumerate(self.history):
            hc = (230, 190, 40) if v >= 10 else (40, 160, 80) if v >= 2 else (180, 40, 45)
            draw_pill(c, f"{v:.2f}x", font(14, bold=True), (GRAPH.right - 48 - i * 72, GRAPH.y + 24), WHITE,
                      (*hc, 230), None, pad=(8, 3))

        ox = oy = 0
        if self.shake:
            ox, oy = random.randint(-8, 8), random.randint(-8, 8)
            surf.blit(self.bg, (0, 0))
        surf.blit(c, (ox, oy))
        if self.flash:
            fl = pygame.Surface((W, H), pygame.SRCALPHA)
            fl.fill((255, 200, 120, int(90 * self.flash)))
            surf.blit(fl, (0, 0))

        # bottom controls
        pygame.draw.rect(surf, (14, 12, 26), (0, 630, W, 90))
        pygame.draw.line(surf, GOLD_DARK, (0, 630), (W, 630), 2)
        tray = pygame.Rect(0, 0, 590, 80)
        tray.center = (W / 2, CHIP_TRAY_Y + 1)
        pygame.draw.rect(surf, (6, 6, 14), tray, border_radius=16)
        pygame.draw.rect(surf, (70, 60, 100), tray, width=2, border_radius=16)
        can_bet = self.state != "flying"
        for v, (cx, cy) in self.chip_pos.items():
            hover = math.hypot(mouse[0] - cx, mouse[1] - cy) <= 31 and can_bet
            y = cy - (7 if hover else 0)
            pygame.draw.circle(surf, (0, 0, 0), (cx + 2, cy + 4), 31)
            img = self.assets.chip(v, 31)
            surf.blit(img, img.get_rect(center=(cx, y)))
            if v > self.app.balance or not can_bet:
                surf.blit(self.dim_chip, self.dim_chip.get_rect(center=(cx, y)))
        self.btn_clear.draw(surf, mouse, can_bet and self.bet > 0)
        self.btn_rebet.draw(surf, mouse, can_bet and bool(self.last_bet))
        self.btn_auto.hint = f"{auto:.2f}x" if auto else "OFF"
        self.btn_auto.draw(surf, mouse)
        if self.state == "flying" and self.cashed is None:
            self.btn_main.text = f"CASH OUT {money(self.stake * int(round(self.mult * 100)) // 100)}"
            self.btn_main.color = (215, 120, 15)
            enabled = True
        elif self.state == "flying":
            self.btn_main.text, self.btn_main.color, enabled = "CASHED OUT", (25, 120, 60), False
        else:
            self.btn_main.text = "LAUNCH AGAIN" if self.state == "crashed" else "LAUNCH"
            self.btn_main.color = (25, 120, 60)
            enabled = self.bet > 0 or (self.state == "crashed" and bool(self.last_bet))
        self.btn_main.size = 24 if len(self.btn_main.text) > 12 else 28
        self.btn_main.draw(surf, mouse, enabled)

        self.app.draw_top_bar(surf, "ROCKET", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Slots (3 reels, 5 paylines)
# --------------------------------------------------------------------------
SLOT_SYMBOLS = ["cherry", "lemon", "orange", "plum", "bell", "bar", "seven", "diamond"]
SLOT_WEIGHTS = {"cherry": 7, "lemon": 6, "orange": 5, "plum": 5, "bell": 4, "bar": 3, "seven": 1, "diamond": 1}
SLOT_PAYS = {"diamond": 300, "seven": 150, "bar": 60, "bell": 35, "plum": 22, "orange": 18, "lemon": 15,
             "cherry": 8}          # three of a kind, times the line bet (~96% payback)
CHERRY_TWO, CHERRY_ONE = 4, 2      # cherries counted from the left reel
SLOT_LINES = [(1, 1, 1), (0, 0, 0), (2, 2, 2), (0, 1, 2), (2, 1, 0)]
LINE_COLORS = [(255, 215, 0), (80, 200, 255), (255, 90, 90), (110, 240, 110), (255, 120, 255)]
REEL_X, REEL_Y, REEL_W, ROW_H = 395, 168, 164, 116
SYM_W, SYM_H = 130, 100


def make_symbol(name):
    k = 2
    w, h = SYM_W * k, SYM_H * k
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    if name == "cherry":
        g = (40, 140, 50)
        pygame.draw.line(s, g, (140, 30), (95, 125), 8)
        pygame.draw.line(s, g, (140, 30), (180, 132), 8)
        pygame.draw.ellipse(s, (60, 170, 60), (140, 14, 62, 28))
        for cx, cy in ((90, 148), (180, 152)):
            pygame.draw.circle(s, (140, 10, 20), (cx, cy), 42)
            pygame.draw.circle(s, (220, 30, 40), (cx - 3, cy - 3), 37)
            pygame.draw.circle(s, (255, 190, 190), (cx - 16, cy - 17), 9)
    elif name == "lemon":
        pygame.draw.ellipse(s, (215, 175, 25), (22, 84, 36, 32))
        pygame.draw.ellipse(s, (215, 175, 25), (202, 84, 36, 32))
        pygame.draw.ellipse(s, (215, 175, 25), (38, 40, 184, 122))
        pygame.draw.ellipse(s, (250, 222, 55), (44, 46, 172, 110))
        pygame.draw.ellipse(s, (255, 245, 170), (80, 62, 62, 26))
    elif name == "orange":
        pygame.draw.circle(s, (200, 100, 20), (130, 112), 80)
        pygame.draw.circle(s, (245, 145, 35), (128, 110), 74)
        rng = random.Random(3)
        for _ in range(40):
            a, r = rng.uniform(0, 6.3), rng.uniform(0, 66)
            pygame.draw.circle(s, (225, 125, 25), (128 + r * math.cos(a), 110 + r * math.sin(a)), 3)
        pygame.draw.circle(s, (255, 205, 130), (98, 80), 16)
        pygame.draw.line(s, (100, 70, 30), (130, 38), (134, 20), 6)
        pygame.draw.ellipse(s, (60, 160, 60), (134, 10, 56, 24))
    elif name == "plum":
        pygame.draw.line(s, (100, 70, 30), (130, 40), (146, 12), 7)
        pygame.draw.ellipse(s, (75, 20, 95), (64, 34, 134, 160))
        pygame.draw.ellipse(s, (120, 45, 150), (70, 38, 122, 150))
        pygame.draw.arc(s, (80, 25, 100), (100, 45, 60, 140), 1.2, 2.2, 4)
        pygame.draw.ellipse(s, (180, 120, 210), (92, 64, 26, 52))
    elif name == "bell":
        gold, dark = (235, 185, 45), (170, 120, 20)
        pygame.draw.circle(s, dark, (130, 26), 11)
        pygame.draw.circle(s, gold, (130, 84), 54)
        pygame.draw.polygon(s, gold, [(77, 88), (183, 88), (218, 160), (42, 160)])
        pygame.draw.rect(s, dark, (34, 152, 192, 24), border_radius=12)
        pygame.draw.circle(s, (120, 80, 15), (130, 184), 14)
        pygame.draw.ellipse(s, (255, 232, 150), (92, 62, 20, 80))
    elif name == "bar":
        pygame.draw.rect(s, (20, 20, 22), (22, 52, 216, 96), border_radius=16)
        pygame.draw.rect(s, GOLD, (22, 52, 216, 96), width=6, border_radius=16)
        draw_text(s, "BAR", font(70, bold=True, serif=True), WHITE, (130, 101), shadow=(120, 20, 30))
    elif name == "seven":
        f = font(200, bold=True, serif=True)
        gold = f.render("7", True, GOLD)
        red = f.render("7", True, (215, 20, 30))
        r = red.get_rect(center=(130, 104))
        for dx in (-5, 0, 5):
            for dy in (-5, 0, 5):
                s.blit(gold, r.move(dx, dy))
        s.blit(red, r)
    elif name == "diamond":
        top, mid, bot = 34, 88, 188
        pygame.draw.polygon(s, (60, 160, 210), [(70, top), (190, top), (240, mid), (130, bot), (20, mid)])
        pygame.draw.polygon(s, (170, 235, 255), [(95, top), (165, top), (188, mid), (72, mid)])
        pygame.draw.polygon(s, (60, 160, 215), [(20, mid), (72, mid), (130, bot)])
        pygame.draw.polygon(s, (40, 125, 190), [(188, mid), (240, mid), (130, bot)])
        pygame.draw.polygon(s, (110, 210, 245), [(72, mid), (188, mid), (130, bot)])
        pygame.draw.polygon(s, (230, 250, 255), [(70, top), (190, top), (240, mid), (130, bot), (20, mid)], 3)
        pygame.draw.line(s, (230, 250, 255), (20, mid), (240, mid), 3)
        for cx, cy, r in ((200, 30, 12), (60, 150, 8)):
            pygame.draw.polygon(s, WHITE, [(cx, cy - r), (cx + r / 4, cy), (cx, cy + r), (cx - r / 4, cy)])
            pygame.draw.polygon(s, WHITE, [(cx - r, cy), (cx, cy - r / 4), (cx + r, cy), (cx, cy + r / 4)])
    return pygame.transform.smoothscale(s, (SYM_W, SYM_H))


def line_points(line, pad=14):
    pts = [(REEL_X + r * REEL_W + REEL_W / 2, REEL_Y + line[r] * ROW_H + ROW_H / 2) for r in range(3)]
    return [(REEL_X - pad, pts[0][1])] + pts + [(REEL_X + 3 * REEL_W + pad, pts[2][1])]


def make_slots_bg(icons):
    s = pygame.Surface((W, H))
    for y in range(H):
        pygame.draw.line(s, lerp_col((40, 8, 44), (12, 4, 18), y / H), (0, y), (W, y))
    for y in range(40, H, 48):          # carpet pattern
        for x in range(0, W + 48, 48):
            ox = 24 if (y // 48) % 2 else 0
            cx, cy = x + ox, y
            pygame.draw.polygon(s, (60, 16, 60), [(cx, cy - 10), (cx + 7, cy), (cx, cy + 10), (cx - 7, cy)])

    # side panels
    for rect in (pygame.Rect(16, 72, 300, 548), pygame.Rect(964, 72, 300, 548)):
        p = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(p, (0, 0, 0, 140), p.get_rect(), border_radius=16)
        pygame.draw.rect(p, (*GOLD_DARK, 255), p.get_rect(), width=2, border_radius=16)
        s.blit(p, rect)

    # paytable
    draw_text(s, "PAYTABLE", font(24, bold=True, serif=True), GOLD, (1114, 98))
    draw_text(s, "wins are multiplied by your line bet", font(12), (200, 190, 170), (1114, 122))
    rows = [(["diamond"] * 3, SLOT_PAYS["diamond"]), (["seven"] * 3, SLOT_PAYS["seven"]),
            (["bar"] * 3, SLOT_PAYS["bar"]), (["bell"] * 3, SLOT_PAYS["bell"]), (["plum"] * 3, SLOT_PAYS["plum"]),
            (["orange"] * 3, SLOT_PAYS["orange"]), (["lemon"] * 3, SLOT_PAYS["lemon"]),
            (["cherry"] * 3, SLOT_PAYS["cherry"]), (["cherry", "cherry", None], CHERRY_TWO),
            (["cherry", None, None], CHERRY_ONE)]
    for i, (syms, pay) in enumerate(rows):
        y = 162 + i * 45
        for j, name in enumerate(syms):
            x = 1000 + j * 50
            if name:
                s.blit(icons[name], icons[name].get_rect(center=(x, y)))
            else:
                draw_text(s, "ANY", font(12, bold=True), (170, 160, 150), (x, y))
        draw_text(s, f"{pay}x", font(22, bold=True), WHITE, (1240, y), anchor="midright")
        if i < len(rows) - 1:
            pygame.draw.line(s, (70, 55, 40), (982, y + 22), (1246, y + 22))

    # payline diagrams
    draw_text(s, "PAYLINES", font(24, bold=True, serif=True), GOLD, (166, 98))
    for i, line in enumerate(SLOT_LINES):
        y0 = 132 + i * 66
        draw_text(s, f"LINE {i + 1}", font(16, bold=True), LINE_COLORS[i], (44, y0 + 27), anchor="midleft")
        gx = 150
        for r in range(3):
            for c in range(3):
                pygame.draw.rect(s, (60, 50, 60), (gx + c * 42, y0 + r * 18, 40, 16), border_radius=3)
        pts = [(gx + c * 42 + 20, y0 + line[c] * 18 + 8) for c in range(3)]
        for c in range(3):
            pygame.draw.rect(s, LINE_COLORS[i], (gx + c * 42, y0 + line[c] * 18, 40, 16), border_radius=3)
        pygame.draw.lines(s, (20, 20, 20), False, pts, 3)
    draw_text(s, "Your bet is split evenly", font(14), (200, 190, 170), (166, 480))
    draw_text(s, "across all 5 lines.", font(14), (200, 190, 170), (166, 500))

    # cabinet
    cab = pygame.Rect(345, 64, 590, 566)
    pygame.draw.rect(s, (8, 2, 8), cab.move(6, 8), border_radius=26)
    body = pygame.Surface(cab.size, pygame.SRCALPHA)
    for x in range(cab.w):
        t = abs(x / cab.w - 0.35)
        pygame.draw.line(body, lerp_col((175, 28, 38), (90, 10, 18), min(1, t * 1.6)), (x, 0), (x, cab.h))
    m = pygame.Surface(cab.size, pygame.SRCALPHA)
    pygame.draw.rect(m, (255, 255, 255, 255), m.get_rect(), border_radius=26)
    body.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    s.blit(body, cab)
    pygame.draw.rect(s, GOLD, cab, width=4, border_radius=26)
    marquee = pygame.Rect(368, 76, 544, 70)
    pygame.draw.rect(s, (50, 4, 10), marquee, border_radius=14)
    pygame.draw.rect(s, GOLD_DARK, marquee, width=3, border_radius=14)
    draw_text(s, "LUCKY SEVENS", font(42, bold=True, serif=True), GOLD, marquee.center, shadow=(20, 0, 0))

    window = pygame.Rect(REEL_X, REEL_Y, 3 * REEL_W, 3 * ROW_H)
    for i, col in enumerate([(90, 90, 95), (220, 220, 225), (160, 160, 170), (240, 240, 245), (70, 70, 75)]):
        pygame.draw.rect(s, col, window.inflate(30 - i * 4, 30 - i * 4), border_radius=12 - i)
    pygame.draw.rect(s, (10, 10, 12), window.inflate(8, 8), border_radius=6)

    # payline number tabs on both sides of the window
    for side in (0, 2):
        used = {}
        for i, line in enumerate(SLOT_LINES):
            row = line[side]
            n = used.get(row, 0)
            used[row] = n + 1
            y = REEL_Y + row * ROW_H + ROW_H / 2 + (0 if row == 1 else (-22 if n == 0 else 22))
            x = REEL_X - 24 if side == 0 else REEL_X + 3 * REEL_W + 24
            pygame.draw.circle(s, (20, 20, 20), (x, y), 11)
            pygame.draw.circle(s, LINE_COLORS[i], (x, y), 9)
            draw_text(s, str(i + 1), font(12, bold=True), (20, 20, 20), (x, y))

    # LED display frames
    for label, rect in (("BET", pygame.Rect(395, 530, 152, 52)), ("LINES", pygame.Rect(557, 530, 168, 52)),
                        ("WIN", pygame.Rect(735, 530, 152, 52))):
        pygame.draw.rect(s, (10, 8, 8), rect, border_radius=8)
        pygame.draw.rect(s, GOLD_DARK, rect, width=2, border_radius=8)
        draw_text(s, label, font(11, bold=True), (200, 170, 120), (rect.centerx, rect.y + 10))
    return s


class Slots:
    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.sym = {n: make_symbol(n) for n in SLOT_SYMBOLS}
        self.blur = {n: pygame.transform.smoothscale(pygame.transform.smoothscale(img, (SYM_W, SYM_H // 5)),
                                                     (SYM_W, int(SYM_H * 1.3)))
                     for n, img in self.sym.items()}
        self.icons = {n: pygame.transform.smoothscale(img, (42, 32)) for n, img in self.sym.items()}
        self.bg = make_slots_bg(self.icons)
        rng = random.Random(21)
        base = [n for n in SLOT_SYMBOLS for _ in range(SLOT_WEIGHTS[n])]
        self.strips = []
        for _ in range(3):
            strip = base[:]
            rng.shuffle(strip)
            self.strips.append(strip)
        self.L = len(base)
        self.pos = [float(random.randrange(self.L)) for _ in range(3)]

        rw = REEL_W - 8
        self.reel_bg = pygame.Surface((rw, 3 * ROW_H))
        for x in range(rw):
            t = abs(x / rw - 0.5) * 2
            pygame.draw.line(self.reel_bg, lerp_col((250, 248, 240), (205, 200, 190), t ** 2), (x, 0),
                             (x, 3 * ROW_H))
        self.shade = pygame.Surface((rw, 3 * ROW_H), pygame.SRCALPHA)
        for y in range(3 * ROW_H):
            a = int(170 * (abs(y / (3 * ROW_H) - 0.5) * 2) ** 3)
            pygame.draw.line(self.shade, (0, 0, 0, a), (0, y), (rw, y))
        self.bulbs = [(368 + i * 544 / 17, y) for i in range(18) for y in (70, 152)]

        self.bet = 0
        self.stake = 0
        self.state = "idle"          # idle, spinning, result
        self.anim = []
        self.stopped = [True] * 3
        self.spin_t = 0.0
        self.wins = []
        self.win_total = 0
        self.shown_win = 0.0
        self.big = False
        self.coins = []
        self.t = 0.0
        self.message = "ADD CHIPS TO SET YOUR BET, THEN SPIN"

        x0 = W / 2 - 3.5 * 70
        self.chip_pos = CHIP_WIN
        self.btn_clear = Button((40, 652, 150, 52), "CLEAR BET", (120, 35, 40), 18)
        self.btn_spin = Button((965, 646, 270, 62), "SPIN", (25, 120, 60), 28, "SPACE")
        self.dim_chip = pygame.Surface((64, 64), pygame.SRCALPHA)
        pygame.draw.circle(self.dim_chip, (0, 0, 0, 150), (32, 32), 31)

    def can_leave(self):
        return self.state != "spinning"

    def outstanding_bets(self):
        return self.stake if self.state == "spinning" else 0

    def leave(self):
        pass

    def symbols(self, blurred):
        """The reel pictures - three of the fruit become seasonal when a theme is on (same payouts)."""
        if not CURRENT_THEME:
            return self.blur if blurred else self.sym

        def make():
            swap = THEME_SLOTS[CURRENT_THEME]
            sym = {n: make_theme_symbol(swap[n]) if n in swap else img for n, img in self.sym.items()}
            blur = {n: pygame.transform.smoothscale(pygame.transform.smoothscale(img, (SYM_W, SYM_H // 5)),
                                                    (SYM_W, int(SYM_H * 1.3))) for n, img in sym.items()}
            return sym, blur
        sym, blur = themed(("slot_symbols",), make)
        return blur if blurred else sym

    def theme_bg(self):
        if not CURRENT_THEME:
            return self.bg
        return themed(("slots_bg",), lambda: make_slots_bg(
            {n: pygame.transform.smoothscale(img, (42, 32)) for n, img in self.symbols(False).items()}))

    def add_chip(self, v):
        if self.bet + v > self.app.balance:
            self.message = "NOT ENOUGH CHIPS FOR THAT BET"
            return
        self.bet += v
        self.message = ""
        self.app.sfx("chip")

    def all_in(self):
        """ALL IN: bet everything (in steps of $5, since the bet is split over 5 lines)."""
        if self.state == "spinning":
            return
        self.bet = self.app.balance - self.app.balance % 5
        self.message = f"ALL IN!  BET {money(self.bet)}" if self.bet else "YOU HAVE NO CHIPS TO BET"
        self.app.sfx("chip")

    def spin(self):
        if self.state == "spinning":
            return
        if not self.bet:
            self.message = "ADD CHIPS TO SET YOUR BET FIRST"
            return
        if self.app.balance < self.bet:
            self.message = "NOT ENOUGH CHIPS - LOWER YOUR BET"
            return
        self.app.balance -= self.bet
        self.stake = self.bet
        self.anim = []
        forced = None
        ev = getattr(self.app, "events", None)
        if ev and ev.active("jackpot") and random.random() < JACKPOT_CHANCE:
            forced = []                                  # JACKPOT event: land three diamonds on the middle line
            for r in range(3):
                spots = [k for k, sym in enumerate(self.strips[r]) if sym == "diamond"]
                forced.append((random.choice(spots) - 1) % self.L)
        for i in range(3):
            stop = forced[i] if forced else random.randrange(self.L)
            start = self.pos[i]
            travel = (start - stop) % self.L + self.L * (2 + i)
            self.anim.append((start, travel, 1.1 + 0.45 * i, stop))
        self.stopped = [False] * 3
        self.spin_t = 0.0
        self.state = "spinning"
        self.wins, self.win_total, self.shown_win, self.big = [], 0, 0.0, False
        self.message = ""
        self.app.sfx("launch")

    def evaluate(self):
        grid = [[self.strips[r][(int(self.pos[r]) + row) % self.L] for r in range(3)] for row in range(3)]
        self.wins = []
        for li, line in enumerate(SLOT_LINES):
            a, b, c = (grid[line[r]][r] for r in range(3))
            if a == b == c:
                self.wins.append((li, SLOT_PAYS[a], 3))
            elif a == b == "cherry":
                self.wins.append((li, CHERRY_TWO, 2))
            elif a == "cherry":
                self.wins.append((li, CHERRY_ONE, 1))
        self.win_total = self.stake // 5 * sum(m for _, m, _ in self.wins)
        ev = getattr(self.app, "events", None)
        self.jackpot_hit = bool(ev and ev.active("jackpot") and [grid[1][r] for r in range(3)] == ["diamond"] * 3)
        if self.jackpot_hit:
            self.win_total = max(self.win_total, self.stake * JACKPOT_PAY)
        self.app.balance += self.win_total
        self.app.record("slots", self.stake, self.win_total)
        if any(m in (SLOT_PAYS["seven"], SLOT_PAYS["diamond"]) for _, m, _ in self.wins):
            self.app.unlock("jackpot")
        self.state = "result"
        if self.win_total:
            self.big = self.win_total >= self.stake * 10
            self.message = ("BIG WIN!  " if self.big else "") + f"YOU WIN {money(self.win_total)}"
            if self.jackpot_hit:
                self.message = f"JACKPOT!!!  {JACKPOT_PAY}x  -  YOU WIN {money(self.win_total)}"
                self.app.effects.chip_rain(120)
            self.app.float_text(f"+{money(self.win_total)}", (80, 230, 110))
            self.app.sfx("win")
            if self.big:
                for _ in range(140):
                    self.coins.append([random.uniform(345, 935), random.uniform(-400, 0),
                                       random.uniform(-60, 60), random.uniform(0, 120), random.uniform(0, 6)])
        else:
            self.message = "NO WIN THIS TIME"
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        if self.bet > self.app.balance:
            self.bet = 0
        self.app.save()

    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN):
            self.spin()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.state != "spinning":
            for v, (cx, cy) in self.chip_pos.items():
                if math.hypot(e.pos[0] - cx, e.pos[1] - cy) <= 31:
                    self.add_chip(v)
                    return
            if self.btn_clear.clicked(e.pos):
                self.bet = 0
            elif self.btn_spin.clicked(e.pos):
                self.spin()

    def update(self, dt):
        self.t += dt
        if self.state == "spinning":
            self.spin_t += dt
            for i, (start, travel, dur, stop) in enumerate(self.anim):
                if self.stopped[i]:
                    continue
                p = min(1.0, self.spin_t / dur)
                ease = 1 - (1 - p) ** 3
                dip = 0.3 * math.sin(math.pi * min(1.0, max(0.0, (p - 0.86) / 0.14)))
                self.pos[i] = (start - travel * ease - dip) % self.L
                if p >= 1:
                    self.stopped[i] = True
                    self.pos[i] = float(stop)
                    self.app.sfx("card")
            if all(self.stopped):
                self.evaluate()
        self.shown_win += (self.win_total - self.shown_win) * min(1, dt * 5)
        for c in self.coins:
            c[3] += 700 * dt
            c[0] += c[2] * dt
            c[1] += c[3] * dt
            c[4] += dt * 8
        self.coins = [c for c in self.coins if c[1] < H + 20]

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.theme_bg(), (0, 0))

        # marquee bulbs
        speed = 14 if self.state == "spinning" or self.big else 5
        for i, (x, y) in enumerate(self.bulbs):
            lit = (i + int(self.t * speed)) % 3 == 0
            pygame.draw.circle(surf, (255, 240, 150) if lit else (120, 90, 40), (x, y), 5)

        # reels
        for i in range(3):
            rect = pygame.Rect(REEL_X + i * REEL_W + 4, REEL_Y, REEL_W - 8, 3 * ROW_H)
            surf.blit(self.reel_bg, rect)
            surf.set_clip(rect)
            pos = self.pos[i]
            base = math.floor(pos)
            frac = pos - base
            blurred = False
            if self.state == "spinning" and not self.stopped[i]:
                blurred = self.spin_t / self.anim[i][2] < 0.75
            for j in range(-1, 4):
                name = self.strips[i][(base + j) % self.L]
                img = self.symbols(blurred)[name]
                cy = REEL_Y + (j - frac) * ROW_H + ROW_H / 2
                surf.blit(img, img.get_rect(center=(rect.centerx, cy)))
            surf.set_clip(None)
            surf.blit(self.shade, rect)
        for i in range(1, 3):
            x = REEL_X + i * REEL_W
            pygame.draw.line(surf, (40, 40, 45), (x, REEL_Y), (x, REEL_Y + 3 * ROW_H), 4)

        # winning lines
        if self.state == "result":
            pulse = 0.5 + 0.5 * math.sin(self.t * 6)
            for li, _, n in self.wins:
                line = SLOT_LINES[li]
                for r in range(n):
                    cell = pygame.Rect(REEL_X + r * REEL_W + 8, REEL_Y + line[r] * ROW_H + 4, REEL_W - 16,
                                       ROW_H - 8)
                    pygame.draw.rect(surf, lerp_col(LINE_COLORS[li], WHITE, pulse), cell, width=4,
                                     border_radius=8)
                pts = line_points(line)
                pygame.draw.lines(surf, (20, 20, 20), False, pts, 9)
                pygame.draw.lines(surf, LINE_COLORS[li], False, pts, 5)

        # LED displays
        led = (255, 70, 50)
        line_bet = self.bet // 5
        draw_text(surf, money(self.bet), font(24, bold=True), led, (471, 562))
        draw_text(surf, f"5 x {money(line_bet)}", font(20, bold=True), led, (641, 562))
        draw_text(surf, money(int(round(self.shown_win))), font(24, bold=True),
                  (90, 255, 110) if self.win_total else led, (811, 562))
        if self.message:
            big = self.big and self.state == "result"
            draw_pill(surf, self.message, font(24 if big else 18, bold=True), (641, 606), GOLD,
                      (0, 0, 0, 200), GOLD if big else GOLD_DARK, pad=(18, 4))

        for x, y, _, _, rot in self.coins:
            w = max(2, abs(math.cos(rot)) * 16)
            pygame.draw.ellipse(surf, (150, 110, 20), (x - w / 2, y - 7, w, 16))
            pygame.draw.ellipse(surf, GOLD, (x - w / 2 + 1, y - 8, max(1, w - 2), 14))

        # chip tray + buttons
        tray = pygame.Rect(0, 0, 590, 80)
        tray.center = (W / 2, CHIP_TRAY_Y + 1)
        pygame.draw.rect(surf, (14, 4, 16), tray, border_radius=16)
        pygame.draw.rect(surf, (100, 60, 90), tray, width=2, border_radius=16)
        spinning = self.state == "spinning"
        for v, (cx, cy) in self.chip_pos.items():
            hover = math.hypot(mouse[0] - cx, mouse[1] - cy) <= 31 and not spinning
            y = cy - (7 if hover else 0)
            pygame.draw.circle(surf, (0, 0, 0), (cx + 2, cy + 4), 31)
            img = self.assets.chip(v, 31)
            surf.blit(img, img.get_rect(center=(cx, y)))
            if spinning or self.bet + v > self.app.balance:
                surf.blit(self.dim_chip, self.dim_chip.get_rect(center=(cx, y)))
        self.btn_clear.draw(surf, mouse, not spinning and self.bet > 0)
        self.btn_spin.draw(surf, mouse, not spinning and 0 < self.bet <= self.app.balance)

        ev = getattr(self.app, "events", None)
        if ev and ev.active("jackpot"):
            left = int(ev.left("jackpot"))
            glow = 0.5 + 0.5 * math.sin(self.t * 6)
            draw_pill(surf, f"JACKPOT EVENT  {left // 60}:{left % 60:02d}  -  3 DIAMONDS IN THE MIDDLE PAYS {JACKPOT_PAY}x!",
                      font(17, bold=True), (641, 78), (30, 20, 0), (*lerp_col(GOLD, (255, 255, 255), glow * 0.4), 240),
                      (255, 255, 255), pad=(16, 4))
        self.app.draw_top_bar(surf, "SLOTS", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Stock market (fake companies; ticks once a second for the whole session)
# --------------------------------------------------------------------------
STOCK_DEFS = [
    ("ROKT", "Rocket Rides Inc.", 42.0, 0.010, (255, 140, 40)),
    ("BNNA", "Big Banana Farms", 18.0, 0.008, (240, 210, 60)),
    ("PIXL", "Pixel Pals Studios", 118.0, 0.007, (90, 170, 255)),
    ("MOON", "Moonbeam Mining", 9.5, 0.013, (190, 140, 255)),
    ("TACO", "Taco Galaxy", 27.0, 0.005, (255, 110, 90)),
    ("ROBO", "Robo Butler Co.", 210.0, 0.006, (120, 230, 200)),
    ("GLDN", "Golden Goose Bank", 64.0, 0.004, (235, 195, 60)),
    ("DINO", "Dino Theme Parks", 33.0, 0.009, (120, 200, 90)),
    ("ZOOM", "Zoomster Scooters", 12.5, 0.012, (255, 90, 160)),
    ("CHOC", "Choco Cloud Sweets", 22.0, 0.006, (180, 120, 80)),
    ("NEON", "Neon Nights Arcades", 48.0, 0.008, (80, 255, 230)),
    ("AQUA", "Aqua Farms Co.", 16.0, 0.007, (60, 160, 255)),
    ("WZRD", "Wizard Games Ltd.", 140.0, 0.009, (150, 90, 255)),
    ("SNAK", "Snack Attack Foods", 8.5, 0.010, (255, 170, 40)),
    ("VOLT", "Volt Motors", 260.0, 0.011, (250, 240, 80)),
    ("PETS", "Happy Paws Pet Co.", 38.0, 0.005, (230, 140, 110)),
    ("SKYH", "Skyhook Airlines", 55.0, 0.008, (140, 200, 255)),
    ("GEMZ", "Gem Mountain Mining", 5.2, 0.015, (255, 110, 110)),
    ("BOTZ", "Botz Robotics", 94.0, 0.007, (170, 170, 190)),
    ("SURF", "Surf's Up Resorts", 27.5, 0.006, (70, 220, 200)),
]
GOOD_NEWS = ["{name} smashes earnings expectations", "{name} unveils a hit new product",
             "Analysts upgrade {sym} to BUY", "{name} signs a huge partnership deal",
             "{sym} added to the Galaxy 500 index", "{name} reports record sales"]
BAD_NEWS = ["{name} misses earnings targets", "Analysts downgrade {sym} to SELL",
            "{name} announces a product recall", "{name} CEO steps down unexpectedly",
            "Regulators open a probe into {name}", "{name} reports a weak quarter"]
MARKET_EVENTS = [      # (headline, details, {symbol or "ALL": (smallest, biggest) change})
    ("MARKET CRASH!", "Panic selling hits every stock on the exchange", {"ALL": (-0.30, -0.15)}),
    ("MARKET BOOM!", "Aliens make first contact and investors go wild", {"ALL": (0.15, 0.30)}),
    ("SOLAR FLARE!", "A giant solar flare fries tech companies", {"PIXL": (-0.35, -0.2), "ROBO": (-0.35, -0.2),
                                                                  "ROKT": (-0.3, -0.15)}),
    ("SPACE RACE!", "Governments pour billions into space companies", {"ROKT": (0.25, 0.45), "MOON": (0.25, 0.45)}),
    ("MOON MADE OF CHEESE!", "Moonbeam Mining discovers cheese on the Moon", {"MOON": (0.5, 0.9)}),
    ("MINE COLLAPSE!", "Moonbeam Mining's biggest mine caves in", {"MOON": (-0.55, -0.35)}),
    ("BANANA BLIGHT!", "A fungus wipes out banana farms worldwide", {"BNNA": (-0.5, -0.3)}),
    ("BANANA CRAZE!", "Banana smoothies become the hottest trend on Earth", {"BNNA": (0.35, 0.6)}),
    ("ROCKET EXPLODES!", "A Rocket Rides launch blows up on the pad", {"ROKT": (-0.45, -0.25)}),
    ("MARS LANDING!", "Rocket Rides lands the first tourists on Mars", {"ROKT": (0.4, 0.7)}),
    ("TACO HOLIDAY!", "Taco Tuesday is declared a national holiday", {"TACO": (0.3, 0.55)}),
    ("HOT SAUCE SHORTAGE!", "Taco Galaxy runs out of hot sauce", {"TACO": (-0.4, -0.25)}),
    ("ROBOTS GO ROGUE!", "Robo Butlers refuse to do any more chores", {"ROBO": (-0.45, -0.25)}),
    ("A ROBOT IN EVERY HOME!", "Robo Butler signs a deal to sell a billion robots", {"ROBO": (0.35, 0.6)}),
    ("VIRAL HIT!", "Pixel Pals' new game breaks every download record", {"PIXL": (0.4, 0.7)}),
    ("FOOD FIGHT!", "Tacos beat bananas in the great food fight", {"TACO": (0.2, 0.35), "BNNA": (-0.35, -0.2)}),
]
GENERIC_BOOMS = [("{sym} ROCKETS!", "{name} wins a giant government contract"),
                 ("{sym} GOES VIRAL!", "Everyone on the internet suddenly wants {name}"),
                 ("{sym} SMASHES RECORDS!", "{name} posts its best year ever")]
GENERIC_BUSTS = [("{sym} PLUNGES!", "{name} loses its biggest customer"),
                 ("{sym} SCANDAL!", "{name} caught cooking the books"),
                 ("{sym} MELTDOWN!", "A disaster shuts down {name} for a month")]
# Rare, huge moves: (headline, details, "one" = a random company / "all" = every company, (smallest, biggest))
MEGA_EVENTS = [
    ("MEGA MOONSHOT!", "{name} is being bought by a trillion-dollar giant", "one", (2.0, 5.0)),
    ("MIRACLE BREAKTHROUGH!", "{name} invents something that changes the world", "one", (1.5, 4.0)),
    ("BANKRUPTCY SCARE!", "{name} might not survive the week", "one", (-0.85, -0.65)),
    ("FRAUD EXPOSED!", "{name}'s bosses are arrested", "one", (-0.80, -0.60)),
    ("THE GREAT CRASH!", "The whole market collapses in a single afternoon", "all", (-0.50, -0.35)),
    ("GOLDEN AGE!", "Record profits everywhere - every stock explodes", "all", (0.40, 0.80)),
]
MEGA_EVERY = (30 * 60, 75 * 60)
STOCK_ANCHOR = math.log(2) / (8 * 3600)   # fair value drifts half-way back to normal in about 8 hours
STOCK_LIMIT = 30                            # no stock goes below 1/30 or above 30x its normal price   # seconds between mega events (random)
STOCK_BET_TIME = 300              # a higher/lower bet is decided 5 minutes after you place it
STOCK_BET_PAY = 1.9
STOCK_BET_MAX = 10                # bets you can have running at once
MAX_OFFLINE = 6 * 3600 if not WEB else 2 * 3600          # market catches up for time the game was closed, up to 6 hours
SPREAD = 0.002                  # buy at the ask (0.1% above the price), sell at the bid (0.1% below)
UP_COL, DOWN_COL = (60, 210, 120), (235, 80, 80)


class Market:
    HIST = 3600                 # one hour of 1-second prices per stock

    def __init__(self, state=None):
        self.stocks = [{"sym": sym, "name": name, "vol": vol, "color": col, "price": p, "fair": p, "base": p,
                        "hist": deque([p], maxlen=self.HIST)} for sym, name, p, vol, col in STOCK_DEFS]
        self.holdings = {}
        self.trades = []            # most recent first
        self.realized = 0.0         # profit locked in by selling
        self.news = []
        self.acc = 0.0
        self.news_in = random.uniform(25, 60)
        self.event_in = random.uniform(150, 420)
        self.alerts = []            # big events waiting to be shown as notifications
        self.mega_in = random.uniform(*MEGA_EVERY)
        self.bets = []              # 5-minute higher/lower bets
        self.settled = []           # finished bets waiting for the app to pay out
        self.bet_id = 0
        loaded = False
        if state:
            try:
                self.load_state(state)
                loaded = True
            except Exception:
                pass
        if not loaded:
            now = time.time()
            for k in range(900):
                self.tick(now - 900 + k)
            self.news = self.news[:3]
            self.alerts = []

    # ---- simulation ------------------------------------------------------
    def update(self, dt):
        self.acc += dt
        while self.acc >= 1.0:
            self.acc -= 1.0
            self.tick(time.time())

    @staticmethod
    def step(st):
        """One second of one stock: a slowly wandering (slightly rising) fair value, the price jittering around it."""
        # ...which is also slowly pulled back towards the company's normal price (half-way in about 8 hours),
        # so crashes and booms swing things wildly for hours but prices can't drift away forever
        pull = STOCK_ANCHOR * (math.log(st["base"]) - math.log(st["fair"]))
        b = st["base"]
        st["fair"] = min(b * STOCK_LIMIT / 2, max(b / STOCK_LIMIT * 2, st["fair"] * math.exp(pull + 0.0008 * random.gauss(0, 1))))
        lp, lf = math.log(st["price"]), math.log(st["fair"])
        lp += st["vol"] * 0.45 * random.gauss(0, 1) + 0.003 * (lf - lp)
        push = st.get("push", 0.0)
        if push:                                  # a big event keeps pushing the price for a while
            step = push * 0.15 if abs(push) > 0.002 else push
            lp += step
            st["push"] = push - step
        st["price"] = min(b * STOCK_LIMIT, max(b / STOCK_LIMIT, math.exp(lp)))
        st["hist"].append(round(st["price"], 2))

    def tick(self, when):
        for st in self.stocks:
            self.step(st)
        if self.bets:
            self.settle_bets(when)
        self.mega_in -= 1
        if self.mega_in <= 0:
            self.mega_in = random.uniform(*MEGA_EVERY)
            self.mega_event(when)
        self.event_in -= 1
        if self.event_in <= 0:
            self.event_in = random.uniform(360, 840)
            self.big_event(when)
        self.news_in -= 1
        if self.news_in <= 0:
            self.news_in = random.uniform(90, 200)
            self.headline(when)

    def headline(self, when):
        st = random.choice(self.stocks)
        good = random.random() < 0.5
        jump = random.uniform(0.03, 0.10) * (1 if good else -1)
        st["price"] = max(0.5, st["price"] * math.exp(jump))
        st["fair"] *= math.exp(jump * 0.3)
        text = random.choice(GOOD_NEWS if good else BAD_NEWS).format(name=st["name"], sym=st["sym"])
        self.news.insert(0, {"text": text, "good": good, "time": time.strftime("%H:%M", time.localtime(when)),
                             "sym": st["sym"], "pct": round(jump * 100, 1)})
        del self.news[8:]

    def big_event(self, when):
        """Something huge happens - one or more stocks jump or crash by 15-90%."""
        syms = {st["sym"] for st in self.stocks}
        if random.random() < 0.5:                 # a made-up headline about any company
            st = random.choice(self.stocks)
            good = random.random() < 0.5
            title, sub = random.choice(GENERIC_BOOMS if good else GENERIC_BUSTS)
            title, sub = title.format(sym=st["sym"]), sub.format(name=st["name"])
            fx = {st["sym"]: (0.25, 0.60) if good else (-0.40, -0.20)}
        else:
            title, sub, fx = random.choice([e for e in MARKET_EVENTS if "ALL" in e[2] or set(e[2]) <= syms])
        self.apply_event(when, title, sub, fx)

    def mega_event(self, when):
        """Rare and enormous: one company multiplies or collapses, or the whole market swings."""
        title, sub, scope, rng = random.choice(MEGA_EVENTS)
        if scope == "all":
            fx = {"ALL": rng}
        else:
            st = random.choice(self.stocks)
            sub = sub.format(name=st["name"])
            title = f"{title} ({st['sym']})"
            fx = {st["sym"]: rng}
        self.apply_event(when, title, sub, fx, mega=True)

    def apply_event(self, when, title, sub, fx, mega=False):
        moves = []
        for st in self.stocks:
            rng = fx.get(st["sym"], fx.get("ALL"))
            if not rng:
                continue
            pct = random.uniform(*rng)
            jump = math.log(1 + pct)
            st["price"] = max(0.5, st["price"] * math.exp(jump * 0.4))   # 40% right away, the rest over ~20 s
            st["push"] = st.get("push", 0.0) + jump * 0.6
            st["fair"] = min(st["base"] * STOCK_LIMIT / 2, max(st["base"] / STOCK_LIMIT * 2, st["fair"] * math.exp(jump * 0.7)))
            moves.append((st["sym"], pct))
        avg = sum(m for _, m in moves) / len(moves)
        detail = "EVERY STOCK" if "ALL" in fx else "  ".join(f"{sym} {pct * 100:+.0f}%" for sym, pct in moves)
        if "ALL" in fx:
            detail += f" {avg * 100:+.0f}%"
        self.news.insert(0, {"text": f"{title} {sub}", "good": avg > 0, "big": True, "sym": "ALL" if "ALL" in fx else
                             moves[0][0], "time": time.strftime("%H:%M", time.localtime(when)), "pct": round(avg * 100, 1)})
        del self.news[8:]
        away = time.time() - when > 5
        self.alerts.append({"title": ("WHILE YOU WERE AWAY: " if away else "") + ("MEGA EVENT: " if mega else "") + title,
                            "sub": f"{sub}  ({detail})", "kind": "up" if avg > 0 else "down"})
        del self.alerts[:-3]

    # ---- 5-minute higher / lower bets ------------------------------------------
    def place_bet(self, sym, direction, amount, now=None):
        now = time.time() if now is None else now
        self.bet_id += 1
        bet = {"id": self.bet_id, "sym": sym, "dir": direction, "amount": int(amount),
               "start": self.stock(sym)["price"], "t0": now, "t1": now + STOCK_BET_TIME}
        self.bets.append(bet)
        return bet

    def bet_winning(self, bet):
        price = self.stock(bet["sym"])["price"]
        return price > bet["start"] if bet["dir"] == "up" else price < bet["start"]

    def settle_bets(self, when):
        for bet in [b for b in self.bets if b["t1"] <= when]:
            self.bets.remove(bet)
            price = self.stock(bet["sym"])["price"]
            if price == bet["start"]:
                payout, result = bet["amount"], "push"
            elif self.bet_winning(bet):
                payout, result = int(bet["amount"] * STOCK_BET_PAY), "win"
            else:
                payout, result = 0, "lose"
            self.settled.append(dict(bet, end=price, payout=payout, result=result))

    def pop_settled(self):
        out, self.settled = self.settled, []
        return out

    def pop_alerts(self):
        out, self.alerts = self.alerts, []
        return out

    # ---- portfolio -------------------------------------------------------
    def stock(self, sym):
        return next(s for s in self.stocks if s["sym"] == sym)

    def position_value(self, sym):
        h = self.holdings.get(sym)
        return h["shares"] * self.stock(sym)["price"] if h else 0.0

    def total_value(self):
        return sum(self.position_value(sym) for sym in self.holdings)

    def total_cost(self):
        return sum(h["cost"] for h in self.holdings.values())

    def ask(self, sym):
        return self.stock(sym)["price"] * (1 + SPREAD / 2)

    def bid(self, sym):
        return self.stock(sym)["price"] * (1 - SPREAD / 2)

    def shares(self, sym):
        h = self.holdings.get(sym)
        return h["shares"] if h else 0.0

    def buy_cost(self, sym, qty):
        return math.ceil(qty * self.ask(sym) - 1e-9)

    def sell_proceeds(self, sym, qty):
        return math.floor(qty * self.bid(sym) + 1e-9)

    def max_affordable(self, sym, cash):
        q = int(cash // self.ask(sym))
        while q > 0 and self.buy_cost(sym, q) > cash:
            q -= 1
        return q

    def log(self, side, sym, qty, price, total):
        self.trades.insert(0, {"side": side, "sym": sym, "qty": qty, "price": round(price, 2), "total": total,
                               "time": time.strftime("%H:%M")})
        del self.trades[30:]

    def buy(self, sym, qty):
        """Buy whole shares at the ask. Returns the cash it cost."""
        cost = self.buy_cost(sym, qty)
        h = self.holdings.setdefault(sym, {"shares": 0.0, "cost": 0.0})
        h["shares"] += qty
        h["cost"] += cost
        self.log("BUY", sym, qty, self.ask(sym), cost)
        return cost

    def sell(self, sym, qty=None):
        """Sell `qty` shares (or all of them) at the bid. Returns the cash received."""
        h = self.holdings.get(sym)
        if not h:
            return 0
        qty = h["shares"] if qty is None else min(qty, h["shares"])
        frac = qty / h["shares"]
        proceeds = self.sell_proceeds(sym, qty)
        cost_part = h["cost"] * frac
        self.last_cost = cost_part
        self.realized += proceeds - cost_part
        h["shares"] -= qty
        h["cost"] -= cost_part
        if h["shares"] < 1e-6:
            del self.holdings[sym]
        shown = int(qty) if abs(qty - round(qty)) < 1e-6 else round(qty, 4)
        self.log("SELL", sym, shown, self.bid(sym), proceeds)
        return proceeds

    # ---- persistence -----------------------------------------------------
    def to_state(self):
        return {"t": time.time(), "news": self.news, "holdings": self.holdings, "trades": self.trades,
                "realized": self.realized, "bets": self.bets, "bet_id": self.bet_id,
                "unpaid": self.settled,
                "stocks": {s["sym"]: {"price": s["price"], "fair": s["fair"],
                                      "hist": list(s["hist"])[-900:] if WEB else list(s["hist"])}
                           for s in self.stocks}}

    def load_state(self, state):
        for s in self.stocks:
            d = state["stocks"].get(s["sym"])
            if d:
                s["price"], s["fair"] = float(d["price"]), float(d["fair"])
                s["hist"] = deque(d["hist"], maxlen=self.HIST)
        self.holdings = {k: {"shares": float(v["shares"]), "cost": float(v["cost"])}
                         for k, v in state.get("holdings", {}).items()}
        self.news = list(state.get("news", []))[:8]
        self.trades = list(state.get("trades", []))[:30]
        self.realized = float(state.get("realized", 0.0))
        self.bets = [dict(b) for b in state.get("bets", [])]
        self.bet_id = int(state.get("bet_id", 0))
        self.settled = [dict(b) for b in state.get("unpaid", [])]
        for st in self.stocks:                    # companies added in an update get a little made-up history
            if st["sym"] not in state["stocks"]:
                for _ in range(900):
                    self.step(st)
        saved = float(state.get("t", time.time()))
        for k in range(int(min(MAX_OFFLINE, max(0, time.time() - saved)))):
            self.tick(saved + k)


TIMEFRAMES = [("1M", 60), ("5M", 300), ("30M", 1800), ("1H", 3600)]


def draw_arrow(surf, up, center, size, color):
    x, y = center
    if up:
        pts = [(x, y - size * 0.6), (x + size * 0.6, y + size * 0.4), (x - size * 0.6, y + size * 0.4)]
    else:
        pts = [(x, y + size * 0.6), (x + size * 0.6, y - size * 0.4), (x - size * 0.6, y - size * 0.4)]
    pygame.draw.polygon(surf, color, pts)


def pct_change(hist, n):
    data = list(hist)[-n:]
    return (data[-1] / data[0] - 1) * 100 if len(data) > 1 and data[0] else 0.0


class Stocks:
    def __init__(self, app):
        self.app = app
        self.m = app.market
        self.assets = app.assets
        self.sel = 0
        self.tf = 1
        self.qty = 1                # shares in the order ticket
        self.news_tab = 0           # 0 = market news, 1 = my trades
        self.message = ""
        self.msg_t = 0.0
        self.list_rect = pygame.Rect(16, 66, 324, 560)     # the company list scrolls (there are 20)
        self.scroll = 0.0
        self.scroll_to = 0.0
        self.pos_tab = 0            # 0 = my position, 1 = 5-minute bets
        self.bet_amt = 0
        self.chart = pygame.Rect(352, 150, 912, 300)
        self.pos_box = pygame.Rect(352, 462, 446, 160)
        self.news_box = pygame.Rect(810, 462, 454, 160)
        self.tf_rects = [pygame.Rect(1264 - (4 - i) * 58, 88, 52, 32) for i in range(4)]

        self.bg = pygame.Surface((W, H))
        for y in range(H):
            pygame.draw.line(self.bg, lerp_col((10, 14, 28), (6, 8, 16), y / H), (0, y), (W, y))
        for x in range(0, W, 40):
            pygame.draw.line(self.bg, (14, 20, 38), (x, 56), (x, H))
        for y in range(56, H, 40):
            pygame.draw.line(self.bg, (14, 20, 38), (0, y), (W, y))
        self.panel_img = {}

        # order ticket: how many shares to buy or sell
        self.qty_btns = [Button((22 + i * 60, 660, 54, 46), label, (50, 60, 90), 17)
                         for i, label in enumerate(["-10", "-1"])]
        self.qty_rect = pygame.Rect(142, 660, 130, 46)
        self.qty_btns += [Button((278 + i * 60, 660, 54, 46), label, (50, 60, 90), 17)
                          for i, label in enumerate(["+1", "+10", "+100"])]
        self.qty_steps = [-10, -1, 1, 10, 100]
        self.btn_max = Button((458, 660, 70, 46), "MAX", (80, 70, 30), 17)
        self.btn_sell_all = Button((830, 652, 118, 52), "SELL ALL", (150, 60, 30), 17)
        self.btn_buy = Button((960, 646, 134, 62), "BUY", (25, 120, 60), 24)
        self.btn_sell = Button((1104, 646, 134, 62), "SELL", (160, 40, 45), 24)
        self.news_tabs = [pygame.Rect(self.news_box.x + 10, self.news_box.y + 6, 130, 26),
                          pygame.Rect(self.news_box.x + 146, self.news_box.y + 6, 110, 26)]
        self.pos_tabs = [pygame.Rect(self.pos_box.x + 10, self.pos_box.y + 6, 150, 26),
                         pygame.Rect(self.pos_box.x + 166, self.pos_box.y + 6, 170, 26)]
        # 5-minute bet controls (they replace the share buttons while the BETS tab is open)
        self.bet_steps = [10, 100, 1000, 10000]
        self.bet_btns = [Button((22 + i * 64, 660, 58, 46), label, (50, 60, 90), 16)
                         for i, label in enumerate(["+10", "+100", "+1K", "+10K"])]
        self.bet_rect = pygame.Rect(280, 660, 150, 46)
        self.btn_bet_all = Button((436, 660, 60, 46), "ALL", (150, 40, 40), 16)
        self.btn_bet_clear = Button((502, 660, 76, 46), "CLEAR", (80, 70, 30), 16)
        self.btn_higher = Button((960, 646, 134, 62), "HIGHER", (25, 120, 60), 22, "x1.9 IN 5 MIN")
        self.btn_lower = Button((1104, 646, 134, 62), "LOWER", (160, 40, 45), 22, "x1.9 IN 5 MIN")

    def can_leave(self):
        return True

    def outstanding_bets(self):
        return 0

    def leave(self):
        pass

    @property
    def stock(self):
        return self.m.stocks[self.sel]

    ROW_H = 60

    def row_rect(self, i):
        return pygame.Rect(self.list_rect.x, self.list_rect.y + i * self.ROW_H - int(self.scroll), 306, self.ROW_H - 4)

    def max_scroll(self):
        return max(0, len(self.m.stocks) * self.ROW_H - 4 - self.list_rect.h)

    def show_selected(self):
        top = self.sel * self.ROW_H
        if top < self.scroll_to:
            self.scroll_to = top
        elif top + self.ROW_H > self.scroll_to + self.list_rect.h:
            self.scroll_to = top + self.ROW_H - self.list_rect.h
        self.scroll_to = max(0, min(self.max_scroll(), self.scroll_to))

    def place_bet(self, direction):
        sym = self.stock["sym"]
        amt = self.bet_amt
        if amt <= 0:
            self.say("CHOOSE HOW MUCH TO BET")
        elif amt > self.app.balance:
            self.say("NOT ENOUGH CHIPS FOR THAT BET")
        elif len(self.m.bets) >= STOCK_BET_MAX:
            self.say(f"YOU CAN ONLY HAVE {STOCK_BET_MAX} BETS RUNNING AT ONCE")
        else:
            self.app.balance -= amt
            bet = self.m.place_bet(sym, direction, amt)
            word = "HIGHER" if direction == "up" else "LOWER"
            self.say(f"{money(amt)} ON {sym} {word} THAN ${bet['start']:,.2f} IN 5 MINUTES")
            self.app.sfx("chip")
            self.app.save()

    def say(self, text):
        self.message, self.msg_t = text, 3.5

    def panel(self, surf, rect, alpha=150, border=GOLD_DARK):
        key = (rect.size, alpha, border)
        if key not in self.panel_img:
            p = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(p, (0, 0, 0, alpha), p.get_rect(), border_radius=14)
            pygame.draw.rect(p, (*border, 255), p.get_rect(), width=2, border_radius=14)
            self.panel_img[key] = p
        surf.blit(self.panel_img[key], rect)

    def buy(self):
        sym = self.stock["sym"]
        if self.qty < 1:
            self.say("CHOOSE HOW MANY SHARES TO BUY")
            return
        cost = self.m.buy_cost(sym, self.qty)
        if cost > self.app.balance:
            most = self.m.max_affordable(sym, self.app.balance)
            self.say(f"NOT ENOUGH CASH - YOU CAN AFFORD {most} SHARE{'S' if most != 1 else ''}")
            return
        self.app.balance -= self.m.buy(sym, self.qty)
        self.say(f"BOUGHT {self.qty:,} {sym} @ ${self.m.ask(sym):,.2f}  =  {money(cost)}")
        self.app.sfx("chip")
        self.app.save()

    def sell(self, everything=False):
        sym = self.stock["sym"]
        owned = self.m.shares(sym)
        if owned <= 0:
            self.say(f"YOU DON'T OWN ANY {sym}")
            return
        if not everything and self.qty < 1:
            self.say("CHOOSE HOW MANY SHARES TO SELL")
            return
        if not everything and self.qty > owned + 1e-6:
            self.say(f"YOU ONLY OWN {owned:,.4g} {sym}")
            return
        qty = None if everything else self.qty
        cash = self.m.sell(sym, qty)
        self.app.balance += cash
        self.app.record("stocks", int(round(self.m.last_cost)), cash)
        if self.m.realized >= 500:
            self.app.unlock("buy_low")
        self.app.float_text(f"+{money(cash)}", (80, 230, 110))
        self.say(f"SOLD {'ALL ' if everything else f'{self.qty:,} '}{sym} @ ${self.m.bid(sym):,.2f}  =  {money(cash)}")
        self.app.sfx("win")
        self.app.save()

    def handle(self, e):
        if e.type == pygame.MOUSEWHEEL:
            if self.list_rect.collidepoint(pygame.mouse.get_pos()):
                self.scroll_to = max(0, min(self.max_scroll(), self.scroll_to - e.y * self.ROW_H))
            return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.list_rect.collidepoint(e.pos):
                for i in range(len(self.m.stocks)):
                    if self.row_rect(i).collidepoint(e.pos):
                        self.sel = i
                        return
                return
            for i, r in enumerate(self.pos_tabs):
                if r.collidepoint(e.pos):
                    self.pos_tab = i
                    return
            if self.pos_tab == 1:
                for b, step in zip(self.bet_btns, self.bet_steps):
                    if b.clicked(e.pos):
                        self.bet_amt = min(self.app.balance, self.bet_amt + step)
                        self.app.sfx("chip")
                        return
                if self.btn_bet_all.clicked(e.pos):
                    self.bet_amt = self.app.balance
                elif self.btn_bet_clear.clicked(e.pos):
                    self.bet_amt = 0
                elif self.btn_higher.clicked(e.pos):
                    self.place_bet("up")
                elif self.btn_lower.clicked(e.pos):
                    self.place_bet("down")
                for i, r in enumerate(self.tf_rects + self.news_tabs):
                    if r.collidepoint(e.pos):
                        if i < len(self.tf_rects):
                            self.tf = i
                        else:
                            self.news_tab = i - len(self.tf_rects)
                return
            for i, r in enumerate(self.tf_rects):
                if r.collidepoint(e.pos):
                    self.tf = i
                    return
            for i, r in enumerate(self.news_tabs):
                if r.collidepoint(e.pos):
                    self.news_tab = i
                    return
            for b, step in zip(self.qty_btns, self.qty_steps):
                if b.clicked(e.pos):
                    self.qty = max(0, min(999999, self.qty + step))
                    return
            if self.btn_max.clicked(e.pos):
                self.qty = self.m.max_affordable(self.stock["sym"], self.app.balance)
            elif self.btn_sell_all.clicked(e.pos):
                self.sell(everything=True)
            elif self.btn_buy.clicked(e.pos):
                self.buy()
            elif self.btn_sell.clicked(e.pos):
                self.sell()
        elif e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_UP, pygame.K_w):
                self.sel = (self.sel - 1) % len(self.m.stocks)
                self.show_selected()
            elif e.key in (pygame.K_DOWN, pygame.K_s):
                self.sel = (self.sel + 1) % len(self.m.stocks)
                self.show_selected()
            elif self.pos_tab == 1:
                if e.unicode and e.unicode.isdigit():        # type a bet amount
                    self.bet_amt = min(10 ** 12, self.bet_amt * 10 + int(e.unicode))
                elif e.key == pygame.K_BACKSPACE:
                    self.bet_amt //= 10
            elif e.unicode and e.unicode.isdigit():          # type a share count
                self.qty = min(999999, self.qty * 10 + int(e.unicode))
            elif e.key == pygame.K_BACKSPACE:
                self.qty //= 10
            elif e.key == pygame.K_b:
                self.buy()

    def update(self, dt):
        self.msg_t = max(0.0, self.msg_t - dt)
        self.scroll += (self.scroll_to - self.scroll) * min(1.0, dt * 14)

    def draw_chart(self, surf, mouse):
        st = self.stock
        r = self.chart
        self.panel(surf, r, 120, (40, 50, 80))
        n = TIMEFRAMES[self.tf][1]
        data = list(st["hist"])[-n:]
        if len(data) < 2:
            return
        step = max(1, len(data) // 500)
        pts_data = data[::step]
        if pts_data[-1] is not data[-1]:
            pts_data.append(data[-1])
        lo, hi = min(data), max(data)
        pad = max((hi - lo) * 0.15, hi * 0.005)
        lo, hi = lo - pad, hi + pad
        inner = r.inflate(-90, -50).move(-30, 0)

        def to_xy(i, v, count):
            return (inner.x + i / (count - 1) * inner.w, inner.bottom - (v - lo) / (hi - lo) * inner.h)

        f = font(13, bold=True)
        for k in range(5):
            v = lo + (hi - lo) * k / 4
            y = inner.bottom - inner.h * k / 4
            pygame.draw.line(surf, (35, 45, 70), (inner.x, y), (inner.right, y))
            draw_text(surf, f"${v:,.2f}", f, (140, 150, 180), (inner.right + 10, y), anchor="midleft")
        label = TIMEFRAMES[self.tf][0]
        draw_text(surf, f"-{label.lower()}", f, (140, 150, 180), (inner.x, inner.bottom + 16), anchor="midleft")
        draw_text(surf, "now", f, (140, 150, 180), (inner.right, inner.bottom + 16), anchor="midright")

        up = data[-1] >= data[0]
        col = UP_COL if up else DOWN_COL
        pts = [to_xy(i, v, len(pts_data)) for i, v in enumerate(pts_data)]
        fill = pygame.Surface(r.size, pygame.SRCALPHA)
        local = [(x - r.x, y - r.y) for x, y in pts]
        pygame.draw.polygon(fill, (*col, 40), local + [(local[-1][0], inner.bottom - r.y), (local[0][0], inner.bottom - r.y)])
        surf.blit(fill, r)
        pygame.draw.lines(surf, col, False, pts, 3)
        pygame.draw.circle(surf, col, pts[-1], 5)
        pygame.draw.circle(surf, WHITE, pts[-1], 5, width=1)

        h = self.m.holdings.get(st["sym"])
        if h and h["shares"] > 0:
            avg = h["cost"] / h["shares"]
            if lo < avg < hi:
                y = inner.bottom - (avg - lo) / (hi - lo) * inner.h
                for x in range(inner.x, inner.right, 14):
                    pygame.draw.line(surf, GOLD, (x, y), (min(x + 7, inner.right), y), 2)
                draw_pill(surf, f"YOUR AVG ${avg:,.2f}", font(12, bold=True), (inner.x + 70, y - 12), (20, 20, 20),
                          (*GOLD, 230), None, pad=(8, 2))

        for bet in self.m.bets:
            if bet["sym"] == st["sym"] and lo < bet["start"] < hi:
                y = inner.bottom - (bet["start"] - lo) / (hi - lo) * inner.h
                c = (120, 190, 255)
                for x in range(inner.x, inner.right, 10):
                    pygame.draw.line(surf, c, (x, y), (min(x + 5, inner.right), y), 2)
                left = max(0, int(bet["t1"] - time.time()))
                draw_pill(surf, f"BET {'HIGHER' if bet['dir'] == 'up' else 'LOWER'} {money(bet['amount'])}  "
                                f"{left // 60}:{left % 60:02d}", font(12, bold=True), (inner.right - 110, y - 12),
                          (10, 20, 40), (*c, 230), None, pad=(8, 2))

        if inner.collidepoint(mouse):
            i = round((mouse[0] - inner.x) / inner.w * (len(data) - 1))
            v = data[i]
            x, y = to_xy(i, v, len(data))
            pygame.draw.line(surf, (120, 130, 160), (x, inner.y), (x, inner.bottom))
            pygame.draw.circle(surf, WHITE, (x, y), 4)
            ago = len(data) - 1 - i
            when = "now" if ago == 0 else f"{ago // 60}m {ago % 60}s ago" if ago >= 60 else f"{ago}s ago"
            draw_pill(surf, f"${v:,.2f}  ·  {when}", font(13, bold=True), (x, inner.y + 12), WHITE,
                      (20, 25, 45, 235), (80, 90, 130), pad=(8, 3))

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        n = TIMEFRAMES[self.tf][1]

        # market list (scrolls - mouse wheel, or the up / down keys)
        surf.set_clip(self.list_rect)
        betting = {b["sym"] for b in self.m.bets}
        for i, st in enumerate(self.m.stocks):
            r = self.row_rect(i)
            if r.bottom < self.list_rect.y or r.y > self.list_rect.bottom:
                continue
            sel = i == self.sel
            self.panel(surf, r, 190 if sel else 120, GOLD if sel else (40, 50, 80))
            pygame.draw.rect(surf, st["color"], (r.x + 6, r.y + 10, 5, r.h - 20), border_radius=3)
            draw_text(surf, st["sym"], font(18, bold=True), WHITE, (r.x + 20, r.y + 18), anchor="midleft")
            draw_text(surf, st["name"], font(11), (150, 150, 170), (r.x + 20, r.y + 39), anchor="midleft")
            ch = pct_change(st["hist"], 3600)
            col = UP_COL if ch >= 0 else DOWN_COL
            draw_text(surf, f"${st['price']:,.2f}", font(16, bold=True), WHITE, (r.right - 10, r.y + 18),
                      anchor="midright")
            draw_text(surf, f"{ch:+.2f}%", font(13, bold=True), col, (r.right - 10, r.y + 39), anchor="midright")
            spark = list(st["hist"])[-300:][::10]
            if len(spark) > 1:
                lo, hi = min(spark), max(spark)
                rng = (hi - lo) or 1
                pts = [(r.x + 150 + j / (len(spark) - 1) * 60, r.y + 30 - (v - lo) / rng * 12 + 6)
                       for j, v in enumerate(spark)]
                pygame.draw.lines(surf, UP_COL if spark[-1] >= spark[0] else DOWN_COL, False, pts, 2)
            tags = (["OWNED"] if st["sym"] in self.m.holdings else []) + (["BET"] if st["sym"] in betting else [])
            for k, tag in enumerate(tags):
                draw_pill(surf, tag, font(9, bold=True), (r.x + 160 + k * 38, r.y + 47), (20, 20, 20),
                          (*(GOLD if tag == "OWNED" else (120, 190, 255)), 255), None, pad=(5, 1))
        surf.set_clip(None)
        if self.max_scroll():
            track = pygame.Rect(self.list_rect.right - 12, self.list_rect.y, 6, self.list_rect.h)
            pygame.draw.rect(surf, (25, 30, 50), track, border_radius=3)
            frac = self.list_rect.h / (self.list_rect.h + self.max_scroll())
            thumb_h = max(30, track.h * frac)
            ty = track.y + (track.h - thumb_h) * (self.scroll / self.max_scroll())
            pygame.draw.rect(surf, (110, 120, 160), (track.x, ty, 6, thumb_h), border_radius=3)

        # header
        st = self.stock
        draw_text(surf, st["sym"], font(34, bold=True), st["color"], (352, 94), anchor="midleft")
        draw_text(surf, st["name"], font(16), (170, 170, 190), (352, 126), anchor="midleft")
        pr = draw_text(surf, f"${st['price']:,.2f}", font(34, bold=True), WHITE, (560, 94), anchor="midleft")
        ch = pct_change(st["hist"], n)
        data = list(st["hist"])[-n:]
        diff = data[-1] - data[0]
        col = UP_COL if ch >= 0 else DOWN_COL
        draw_arrow(surf, ch >= 0, (pr.right + 22, 95), 16, col)
        draw_text(surf, f"{diff:+,.2f}  ({ch:+.2f}%)", font(20, bold=True), col, (pr.right + 38, 95),
                  anchor="midleft")
        draw_text(surf, f"past {TIMEFRAMES[self.tf][0].lower().replace('m', ' min').replace('h', ' hour')}",
                  font(13), (140, 140, 160), (560, 126), anchor="midleft")
        draw_text(surf, f"BID ${self.m.bid(st['sym']):,.2f}   ASK ${self.m.ask(st['sym']):,.2f}", font(13, bold=True),
                  (170, 180, 210), (680, 126), anchor="midleft")
        for i, r in enumerate(self.tf_rects):
            on = i == self.tf
            pygame.draw.rect(surf, (60, 70, 110) if on else (22, 26, 44), r, border_radius=8)
            pygame.draw.rect(surf, GOLD if on else (50, 60, 90), r, width=2, border_radius=8)
            draw_text(surf, TIMEFRAMES[i][0], font(14, bold=True), WHITE, r.center)
        live = int(time.time() * 2) % 2
        pygame.draw.circle(surf, (255, 70, 70) if live else (120, 40, 40), (1000, 104), 5)
        draw_text(surf, "LIVE", font(12, bold=True), (220, 220, 230), (1010, 104), anchor="midleft")

        self.draw_chart(surf, mouse)
        if self.msg_t:
            draw_pill(surf, self.message, font(16, bold=True), (self.chart.centerx, self.chart.y + 20), GOLD,
                      (0, 0, 0, 220), GOLD_DARK, pad=(14, 4))

        # position box - or your 5-minute bets
        b = self.pos_box
        self.panel(surf, b, 150, (40, 50, 80))
        for i, (r, label) in enumerate(zip(self.pos_tabs, [f"MY {st['sym']} SHARES", f"5-MIN BETS  ({len(self.m.bets)})"])):
            on = i == self.pos_tab
            if on:
                pygame.draw.rect(surf, (40, 50, 80), r, border_radius=8)
            draw_text(surf, label, font(14, bold=True), GOLD if on else (130, 130, 150), r.center)
        h = self.m.holdings.get(st["sym"])
        f, fb = font(15), font(15, bold=True)
        if self.pos_tab == 1:
            now = time.time()
            bets = sorted(self.m.bets, key=lambda x: x["t1"])
            for j, bet in enumerate(bets[:4]):
                y = b.y + 50 + j * 23
                left = max(0, int(bet["t1"] - now))
                win = self.m.bet_winning(bet)
                draw_arrow(surf, bet["dir"] == "up", (b.x + 22, y), 11, UP_COL if bet["dir"] == "up" else DOWN_COL)
                draw_text(surf, f"{bet['sym']}  {money(bet['amount'])}", font(14, bold=True), WHITE, (b.x + 34, y),
                          anchor="midleft")
                draw_text(surf, f"from ${bet['start']:,.2f}", font(13), (160, 160, 180), (b.x + 170, y),
                          anchor="midleft")
                draw_text(surf, f"{left // 60}:{left % 60:02d}", font(14, bold=True), (200, 200, 220),
                          (b.x + 316, y), anchor="midleft")
                draw_text(surf, "WINNING" if win else "LOSING", font(13, bold=True), UP_COL if win else DOWN_COL,
                          (b.right - 14, y), anchor="midright")
            if len(bets) > 4:
                draw_text(surf, f"+ {len(bets) - 4} more", font(12), (150, 150, 170), (b.x + 34, b.y + 142),
                          anchor="midleft")
            if not bets:
                draw_text(surf, f"Will {st['sym']} be HIGHER or LOWER in 5 minutes?", f, (190, 190, 210),
                          (b.x + 16, b.y + 56), anchor="midleft")
                draw_text(surf, "Pick an amount below, then press HIGHER or LOWER.", f, (150, 150, 170),
                          (b.x + 16, b.y + 82), anchor="midleft")
                draw_text(surf, "Right pays x1.9. Bets finish even if the game is closed.", f, (150, 150, 170),
                          (b.x + 16, b.y + 106), anchor="midleft")
            else:
                total = sum(x["amount"] for x in bets)
                draw_text(surf, f"ON THE LINE {money(total)}", fb, WHITE, (b.right - 16, b.y + 143), anchor="midright")
        elif h:
            value = self.m.position_value(st["sym"])
            pnl = int(round(value - h["cost"]))
            pc = pnl / h["cost"] * 100 if h["cost"] else 0
            sh = h["shares"]
            rows = [("Shares", f"{int(round(sh)):,}" if abs(sh - round(sh)) < 1e-6 else f"{sh:,.4f}"),
                    ("Avg cost", f"${h['cost'] / sh:,.2f}"),
                    ("Cost basis", money(int(round(h["cost"])))), ("Market value", money(int(round(value))))]
            for j, (k, v) in enumerate(rows):
                x = b.x + 16 + (j % 2) * 215
                y = b.y + 48 + (j // 2) * 26
                draw_text(surf, k, f, (150, 150, 170), (x, y), anchor="midleft")
                draw_text(surf, v, fb, WHITE, (x + 200, y), anchor="midright")
            draw_text(surf, f"{'+' if pnl >= 0 else '-'}{money(abs(pnl))}  ({pc:+.1f}%)",
                      font(20, bold=True), UP_COL if pnl >= 0 else DOWN_COL, (b.x + 16, b.y + 106),
                      anchor="midleft")
        else:
            draw_text(surf, f"You don't own any {st['sym']} yet.", f, (150, 150, 170), (b.x + 16, b.y + 56),
                      anchor="midleft")
            draw_text(surf, "Choose a number of shares below and press BUY.", f, (150, 150, 170),
                      (b.x + 16, b.y + 80), anchor="midleft")
        tv, tc = self.m.total_value(), self.m.total_cost()
        tp = int(round(tv - tc))
        if self.pos_tab == 0:
            pygame.draw.line(surf, (40, 50, 80), (b.x + 12, b.y + 126), (b.right - 12, b.y + 126))
            draw_text(surf, f"PORTFOLIO  {money(int(round(tv)))}", fb, WHITE, (b.x + 16, b.y + 143), anchor="midleft")
        if self.m.holdings and self.pos_tab == 0:
            draw_text(surf, f"{'+' if tp >= 0 else '-'}{money(abs(tp))} unrealized", fb,
                      UP_COL if tp >= 0 else DOWN_COL, (b.right - 16, b.y + 143), anchor="midright")

        # news
        b = self.news_box
        self.panel(surf, b, 150, (40, 50, 80))
        for i, (r, label) in enumerate(zip(self.news_tabs, ["MARKET NEWS", "MY TRADES"])):
            on = i == self.news_tab
            if on:
                pygame.draw.rect(surf, (40, 50, 80), r, border_radius=8)
            draw_text(surf, label, font(14, bold=True), GOLD if on else (130, 130, 150), r.center)
        if self.news_tab == 1:
            rz = int(round(self.m.realized))
            draw_text(surf, f"Realized: {'+' if rz >= 0 else '-'}{money(abs(rz))}", font(13, bold=True),
                      UP_COL if rz >= 0 else DOWN_COL, (b.right - 14, b.y + 19), anchor="midright")
            for j, tr in enumerate(self.m.trades[:5]):
                y = b.y + 44 + j * 24
                draw_text(surf, tr["time"], font(12), (130, 130, 150), (b.x + 16, y), anchor="midleft")
                draw_text(surf, tr["side"], font(13, bold=True), UP_COL if tr["side"] == "BUY" else DOWN_COL,
                          (b.x + 60, y), anchor="midleft")
                draw_text(surf, f"{tr['qty']:,} {tr['sym']} @ ${tr['price']:,.2f}", font(14), (220, 220, 230),
                          (b.x + 108, y), anchor="midleft")
                draw_text(surf, money(tr["total"]), font(13, bold=True), WHITE, (b.right - 14, y), anchor="midright")
            if not self.m.trades:
                draw_text(surf, "No trades yet.", font(14), (150, 150, 170), (b.x + 16, b.y + 50), anchor="midleft")
        for j, item in enumerate(self.m.news[:5] if self.news_tab == 0 else []):
            y = b.y + 44 + j * 24
            draw_text(surf, item["time"], font(12), (130, 130, 150), (b.x + 16, y), anchor="midleft")
            draw_arrow(surf, item["good"], (b.x + 64, y), 11, UP_COL if item["good"] else DOWN_COL)
            text = item["text"]
            big = item.get("big")
            f = font(14, bold=big)
            while f.size(text)[0] > b.w - 140 and len(text) > 4:
                text = text[:-4] + "..."
            draw_text(surf, text, f, GOLD if big else (220, 220, 230), (b.x + 76, y), anchor="midleft")
            draw_text(surf, f"{item['pct']:+.0f}%", font(12, bold=True), UP_COL if item["good"] else DOWN_COL,
                      (b.right - 14, y), anchor="midright")
        if not self.m.news and self.news_tab == 0:
            draw_text(surf, "No news yet - check back soon.", font(14), (150, 150, 170), (b.x + 16, b.y + 50),
                      anchor="midleft")

        # bottom bar
        pygame.draw.rect(surf, (8, 10, 20), (0, 630, W, 90))
        pygame.draw.line(surf, GOLD_DARK, (0, 630), (W, 630), 2)
        if self.pos_tab == 1:                      # 5-minute bet ticket
            self.bet_amt = min(self.bet_amt, self.app.balance)
            draw_text(surf, "BET AMOUNT", font(11, bold=True), (150, 160, 190), (22, 646), anchor="midleft")
            draw_text(surf, "type a number or use the buttons", font(11), (110, 120, 150), (110, 646), anchor="midleft")
            for b in self.bet_btns:
                b.draw(surf, mouse)
            pygame.draw.rect(surf, (4, 6, 14), self.bet_rect, border_radius=10)
            pygame.draw.rect(surf, GOLD, self.bet_rect, width=2, border_radius=10)
            draw_text(surf, money(self.bet_amt), fit_font(money(self.bet_amt), 22, 136), WHITE, self.bet_rect.center)
            self.btn_bet_all.draw(surf, mouse, self.app.balance > 0)
            self.btn_bet_clear.draw(surf, mouse, self.bet_amt > 0)
            ok = 0 < self.bet_amt <= self.app.balance and len(self.m.bets) < STOCK_BET_MAX
            draw_text(surf, f"Will {st['sym']} be above or below ${st['price']:,.2f} in 5 minutes?", font(14, bold=True),
                      (210, 215, 235), (592, 666), anchor="midleft")
            draw_text(surf, f"Right pays {money(int(self.bet_amt * STOCK_BET_PAY))}" if self.bet_amt else
                      "Right pays x1.9 your bet", font(13), (150, 200, 160), (592, 690), anchor="midleft")
            self.btn_higher.draw(surf, mouse, ok)
            self.btn_lower.draw(surf, mouse, ok)
            self.app.draw_top_bar(surf, "STOCKS", lobby=True)
            return
        sym = st["sym"]
        draw_text(surf, "SHARES", font(11, bold=True), (150, 160, 190), (22, 646), anchor="midleft")
        draw_text(surf, "type a number or use the buttons", font(11), (110, 120, 150), (142, 646), anchor="midleft")
        for b in self.qty_btns:
            b.draw(surf, mouse)
        pygame.draw.rect(surf, (4, 6, 14), self.qty_rect, border_radius=10)
        pygame.draw.rect(surf, GOLD, self.qty_rect, width=2, border_radius=10)
        draw_text(surf, f"{self.qty:,}", font(24, bold=True), WHITE, self.qty_rect.center)
        self.btn_max.draw(surf, mouse)
        owned = self.m.shares(sym)
        cost, proceeds = self.m.buy_cost(sym, self.qty), self.m.sell_proceeds(sym, self.qty)
        can_buy = self.qty > 0 and cost <= self.app.balance
        can_sell = self.qty > 0 and self.qty <= owned + 1e-6
        f14 = font(14, bold=True)
        draw_text(surf, f"BUY {self.qty:,} x ${self.m.ask(sym):,.2f} = {money(cost)}", f14,
                  UP_COL if can_buy else (120, 120, 130), (544, 655), anchor="midleft")
        draw_text(surf, f"SELL {self.qty:,} x ${self.m.bid(sym):,.2f} = {money(proceeds)}", f14,
                  DOWN_COL if can_sell else (120, 120, 130), (544, 679), anchor="midleft")
        own_txt = f"{int(owned):,}" if abs(owned - round(owned)) < 1e-6 else f"{owned:,.4f}"
        draw_text(surf, f"You own {own_txt} {sym}  -  cash {money(self.app.balance)}", font(13), (170, 175, 200),
                  (544, 703), anchor="midleft")
        self.btn_sell_all.draw(surf, mouse, owned > 0)
        self.btn_buy.hint = money(cost) if self.qty else None
        self.btn_sell.hint = money(proceeds) if self.qty and can_sell else None
        self.btn_buy.draw(surf, mouse, can_buy)
        self.btn_sell.draw(surf, mouse, can_sell)

        self.app.draw_top_bar(surf, "STOCKS", lobby=True)


# --------------------------------------------------------------------------
# Poker (No-Limit Texas Hold'em vs 3 computer players)
# --------------------------------------------------------------------------
POKER_SB, POKER_BB = 5, 10
BUYIN_MIN, BUYIN_MAX = 50, 2000
BOT_NAMES = ["Mike", "Sara", "Tony", "Lena", "Victor", "Rosa", "Jin", "Omar", "Nina", "Carl", "Ivy", "Dmitri",
             "Priya", "Hank", "Mei", "Lars"]
HAND_NAMES = ["High Card", "Pair", "Two Pair", "Three of a Kind", "Straight", "Flush", "Full House",
              "Four of a Kind", "Straight Flush"]
RANK_NUM = {r: i + 2 for i, r in enumerate(["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"])}
FULL_DECK = [(RANK_NUM[r], s) for s in SUITS for r in RANKS]
SEAT_PANEL = [(640, 604), (170, 350), (640, 96), (1110, 350)]
SEAT_CARDS = [(640, 490), (170, 283), (640, 164), (1110, 283)]
SEAT_BET = [(640, 404), (310, 350), (515, 212), (970, 350)]
SEAT_BTN = [(745, 440), (270, 292), (725, 124), (1010, 292)]
SEAT_BUBBLE = [(440, 604), (170, 398), (812, 96), (1110, 398)]
BOARD_Y = 237
DECK_POS = (595, 120)


def straight_high(ranks):
    u = set(ranks)
    if 14 in u:
        u.add(1)
    for hi in range(14, 4, -1):
        if all(hi - i in u for i in range(5)):
            return hi
    return 0


def eval_hand(cards):
    """Score the best 5-card poker hand from up to 7 (rank, suit) cards. Higher tuple wins."""
    ranks = sorted((r for r, _ in cards), reverse=True)
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    suits = {}
    for r, s in cards:
        suits.setdefault(s, []).append(r)
    flush = next((sorted(rs, reverse=True) for rs in suits.values() if len(rs) >= 5), None)
    if flush:
        sf = straight_high(flush)
        if sf:
            return (8, sf)
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    top, n = groups[0]
    if n == 4:
        return (7, top, max((r for r in ranks if r != top), default=0))
    if n == 3 and len(groups) > 1 and groups[1][1] >= 2:
        return (6, top, groups[1][0])
    if flush:
        return (5, *flush[:5])
    st = straight_high(ranks)
    if st:
        return (4, st)
    if n == 3:
        return (3, top, *[r for r in ranks if r != top][:2])
    if n == 2 and len(groups) > 1 and groups[1][1] == 2:
        p2 = groups[1][0]
        return (2, top, p2, max((r for r in ranks if r not in (top, p2)), default=0))
    if n == 2:
        return (1, top, *[r for r in ranks if r != top][:3])
    return (0, *ranks[:5])


def equity(hole, board, n_opp, sims=160):
    """Monte-Carlo chance of winning the pot against n_opp random hands."""
    known = set(hole + board)
    deck = [c for c in FULL_DECK if c not in known]
    need = 5 - len(board)
    wins = 0.0
    for _ in range(sims):
        draw = random.sample(deck, need + 2 * n_opp)
        b = board + draw[:need]
        mine = eval_hand(hole + b)
        best = max(eval_hand(draw[need + 2 * i: need + 2 * i + 2] + b) for i in range(n_opp))
        if mine > best:
            wins += 1
        elif mine == best:
            wins += 0.5
    return wins / sims


class Seat:
    def __init__(self, name, stack, bot=True):
        self.name, self.stack, self.bot = name, stack, bot
        self.color = random.choice([(200, 70, 70), (70, 120, 200), (80, 160, 90), (170, 90, 190), (210, 150, 50),
                                    (60, 170, 170)]) if bot else (200, 160, 60)
        self.aggr = random.uniform(0.85, 1.2)
        self.reset()

    def reset(self):
        self.cards = []
        self.bet = self.total = self.won = 0
        self.folded = self.allin = self.acted = False
        self.bubble, self.bubble_t = "", 0.0
        self.best = None

    @property
    def live(self):
        return bool(self.cards) and not self.folded

    @property
    def can_act(self):
        return self.live and not self.allin


def make_poker_bg():
    s = Menu.make_bg()
    table = pygame.Rect(120, 64, 1040, 572)
    pygame.draw.ellipse(s, (8, 4, 6), table.move(0, 10))
    pygame.draw.ellipse(s, (48, 28, 19), table)
    pygame.draw.ellipse(s, (78, 48, 32), table.inflate(-14, -14), width=3)
    felt_rect = table.inflate(-54, -54)
    felt = pygame.Surface((W, H), pygame.SRCALPHA)
    dark, light = (8, 62, 36), (24, 122, 68)
    felt.fill((*dark, 255))
    for i in range(40):
        t = i / 39
        rw, rh = 1100 * (1 - t) + 100, 620 * (1 - t) + 60
        pygame.draw.ellipse(felt, lerp_col(dark, light, t ** 0.8), (W / 2 - rw / 2, 330 - rh / 2, rw, rh))
    mask = pygame.Surface((W, H), pygame.SRCALPHA)
    pygame.draw.ellipse(mask, (255, 255, 255, 255), felt_rect)
    felt.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    s.blit(felt, (0, 0))
    pygame.draw.ellipse(s, (200, 170, 90), felt_rect.inflate(-70, -70), width=1)
    for i in range(5):
        x = 640 - 2 * 98 - 45 + i * 98
        pygame.draw.rect(s, (30, 110, 62), (x, BOARD_Y, CW, CH), width=2, border_radius=8)
    return s


class Poker:
    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.bg = make_poker_bg()
        self.small_faces = {}
        self.you = Seat("YOU", 0, bot=False)
        self.seats = [self.you]
        for _ in range(3):
            self.seats.append(self.new_bot())
        self.dealer = random.randrange(4)
        self.phase = "idle"          # idle (not seated), hand, between
        self.board = []
        self.my_sprites = []
        self.deck = []
        self.queue = []
        self.to_act = 0
        self.your_turn = False
        self.current_bet = 0
        self.min_raise = POKER_BB
        self.raises = 0
        self.street = 0
        self.reveal = False
        self.buyin = 0
        self.between_t = 0.0
        self.t = 0.0
        self.message = f"CHOOSE YOUR BUY-IN ({money(BUYIN_MIN)} - {money(BUYIN_MAX)}), THEN SIT DOWN"

        x0 = W / 2 - 3.5 * 70
        self.chip_pos = CHIP_WIN
        self.btn_clear = Button((40, 652, 150, 52), "CLEAR", (120, 35, 40), 18)
        self.btn_sit = Button((965, 646, 270, 62), "SIT DOWN", (25, 120, 60), 24)
        self.btn_next = Button((965, 646, 270, 62), "NEXT HAND", (25, 120, 60), 24, "SPACE")
        self.btn_stand = Button((40, 652, 200, 52), "STAND UP", (120, 35, 40), 18, "CASH OUT")
        bw, gap = 190, 12
        x = (W - (6 * bw + 5 * gap)) / 2
        self.btn_act = [Button((x + i * (bw + gap), 648, bw, 58), "", c, 20, h) for i, (c, h) in enumerate(
            [((150, 35, 40), "F"), ((40, 90, 160), "C"), ((25, 120, 60), "MIN RAISE"), ((25, 120, 60), "1/2 POT"),
             ((25, 120, 60), "POT"), ((170, 120, 20), "A")])]
        self.dim_chip = pygame.Surface((64, 64), pygame.SRCALPHA)
        pygame.draw.circle(self.dim_chip, (0, 0, 0, 150), (32, 32), 31)

    # ---- table management ------------------------------------------------
    def new_bot(self):
        taken = {s.name for s in getattr(self, "seats", [])}
        name = random.choice([n for n in BOT_NAMES if n not in taken])
        return Seat(name, random.choice([300, 400, 500, 600, 800, 1000]))

    def all_in(self):
        """ALL IN: buy in for as much as the table allows."""
        if self.phase != "idle" or getattr(self, "sitting", 0):
            return
        self.buyin = min(BUYIN_MAX, self.app.balance)
        self.message = f"BUY-IN SET TO {money(self.buyin)} - PRESS SIT DOWN" if self.buyin else "YOU HAVE NO CHIPS"
        self.app.sfx("chip")

    def seated(self):
        return self.phase != "idle"

    def in_hand(self):
        return self.phase == "hand" and self.you.live

    def can_leave(self):
        return not self.in_hand()

    def outstanding_bets(self):
        return self.you.stack + (self.you.total if self.in_hand() else 0) if self.seated() else 0

    def leave(self):
        if self.seated():
            self.app.balance += self.you.stack
        self.you.stack = 0
        self.you.reset()
        self.queue = []
        self.phase = "idle"
        self.board, self.my_sprites = [], []
        for s in self.seats:
            s.reset()
        self.buyin = 0
        self.message = f"CHOOSE YOUR BUY-IN ({money(BUYIN_MIN)} - {money(BUYIN_MAX)}), THEN SIT DOWN"

    def schedule(self, delay, fn):
        self.queue.append([delay, fn])

    def sit(self):
        if self.buyin < min(BUYIN_MIN, self.app.balance) or self.buyin <= 0:
            self.message = f"MINIMUM BUY-IN IS {money(BUYIN_MIN)}"
            return
        self.app.balance -= self.buyin
        self.you.stack = self.buyin
        self.buyin = 0
        self.phase = "between"
        self.app.sfx("chip")
        self.start_hand()

    # ---- hand flow -------------------------------------------------------
    def post(self, s, amount):
        a = min(amount, s.stack)
        s.stack -= a
        s.bet += a
        s.total += a
        if s.stack == 0:
            s.allin = True
        return a

    def start_hand(self):
        self.queue = []
        notice = ""
        for i in range(1, 4):
            if self.seats[i].stack < POKER_BB:
                old = self.seats[i].name
                self.seats[i] = self.new_bot()
                notice = f"{old.upper()} IS OUT OF CHIPS.  {self.seats[i].name.upper()} SITS DOWN."
        if self.you.stack <= 0:
            self.leave()
            self.message = "YOU'RE OUT OF CHIPS AT THIS TABLE - BUY IN AGAIN TO KEEP PLAYING"
            return
        for s in self.seats:
            s.reset()
        self.deck = [(r, s) for s in SUITS for r in RANKS]
        random.shuffle(self.deck)
        self.board, self.my_sprites = [], []
        self.dealer = (self.dealer + 1) % 4
        sb, bb = (self.dealer + 1) % 4, (self.dealer + 2) % 4
        for i in range(4):
            for _ in range(2):
                self.seats[i].cards.append(self.deck.pop())
        self.post(self.seats[sb], POKER_SB)
        self.post(self.seats[bb], POKER_BB)
        self.seats[sb].bubble, self.seats[sb].bubble_t = "SMALL BLIND", 1.5
        self.seats[bb].bubble, self.seats[bb].bubble_t = "BIG BLIND", 1.5
        self.current_bet, self.min_raise, self.raises, self.street = POKER_BB, POKER_BB, 0, 0
        self.reveal = False
        self.phase = "hand"
        self.your_turn = False
        self.message = notice
        cx, cy = SEAT_CARDS[0]
        for j, (r, s) in enumerate(self.you.cards):
            sp = CardSprite(r, s, DECK_POS)
            sp.tx, sp.ty = cx - 70 + j * 50, cy - CH / 2
            self.my_sprites.append(sp)
        self.app.sfx("card")
        self.to_act = (bb + 1) % 4
        self.schedule(0.8, self.begin_turn)

    def pot(self):
        return sum(s.total for s in self.seats)

    def round_done(self):
        actors = [s for s in self.seats if s.can_act]
        if not actors:
            return True
        if len(actors) == 1 and actors[0].bet >= self.current_bet:
            return True
        return all(s.acted and s.bet == self.current_bet for s in actors)

    def begin_turn(self):
        live = [s for s in self.seats if s.live]
        if len(live) == 1:
            self.finish_uncontested(live[0])
            return
        if self.round_done():
            self.end_street()
            return
        for _ in range(4):
            if self.seats[self.to_act].can_act:
                break
            self.to_act = (self.to_act + 1) % 4
        s = self.seats[self.to_act]
        if s.bot:
            i = self.to_act
            self.schedule(random.uniform(0.6, 1.3), lambda: self.bot_act(i))
        else:
            self.your_turn = True

    def act(self, i, kind, raise_to=0):
        s = self.seats[i]
        to_call = self.current_bet - s.bet
        if kind == "raise" and (self.raises >= 4 or s.stack <= to_call):
            kind = "call"
        if kind == "fold":
            s.folded = True
            s.bubble = "FOLD"
            self.app.sfx("card")
        elif kind == "call":
            paid = self.post(s, to_call)
            s.bubble = "CHECK" if paid == 0 else "ALL IN" if s.allin else f"CALL {money(paid)}"
            if paid:
                self.app.sfx("chip")
        else:
            raise_to = min(max(raise_to, self.current_bet + self.min_raise), s.bet + s.stack)
            if raise_to - self.current_bet >= self.min_raise:
                self.min_raise = raise_to - self.current_bet
            was_bet = self.current_bet == 0
            self.post(s, raise_to - s.bet)
            self.current_bet = max(self.current_bet, s.bet)
            self.raises += 1
            for o in self.seats:
                if o is not s and o.can_act:
                    o.acted = False
            s.bubble = "ALL IN" if s.allin else f"{'BET' if was_bet else 'RAISE TO'} {money(s.bet)}"
            self.app.sfx("chip")
        s.acted = True
        s.bubble_t = 2.2
        self.your_turn = False
        self.to_act = (i + 1) % 4
        self.schedule(0.35, self.begin_turn)

    def end_street(self):
        for s in self.seats:
            s.bet = 0
            s.acted = False
        self.current_bet, self.min_raise, self.raises = 0, POKER_BB, 0
        if self.street == 3:
            self.schedule(0.6, self.showdown)
            return
        self.street += 1
        n = 3 if self.street == 1 else 1
        self.schedule(0.45, lambda: self.deal_board(n))
        if sum(1 for s in self.seats if s.can_act) <= 1:
            self.reveal = True            # everyone's all in: flip the cards and run out the board
            self.schedule(1.1, self.end_street)
        else:
            self.to_act = (self.dealer + 1) % 4
            self.schedule(0.8, self.begin_turn)

    def deal_board(self, n):
        for _ in range(n):
            r, s = self.deck.pop()
            sp = CardSprite(r, s, DECK_POS)
            sp.tx, sp.ty = 640 - 2 * 98 - 45 + len(self.board) * 98, BOARD_Y
            self.board.append(sp)
        self.app.sfx("card")

    def board_cards(self):
        return [(RANK_NUM[c.rank], c.suit) for c in self.board]

    @staticmethod
    def hole(s):
        return [(RANK_NUM[r], su) for r, su in s.cards]

    def award(self, winnings, text):
        for s, amt in winnings.items():
            s.stack += amt
            s.won += amt
        if self.you.total:
            self.app.record("poker", self.you.total, self.you.won)
        for s in self.seats:
            if s.won:
                s.bubble = f"WINS {money(s.won)}"
                s.bubble_t = 6.0
        self.message = text
        if self.you.won:
            self.app.sfx("win")
        elif self.you.total:
            self.app.sfx("lose")
        self.phase = "between"
        self.between_t = 5.0
        self.your_turn = False

    def finish_uncontested(self, winner):
        self.award({winner: self.pot()}, f"{'YOU WIN' if winner is self.you else winner.name + ' WINS'} "
                                         f"{money(self.pot())} - EVERYONE ELSE FOLDED")

    def showdown(self):
        self.reveal = True
        board = self.board_cards()
        live = [s for s in self.seats if s.live]
        for s in live:
            s.best = eval_hand(self.hole(s) + board)
            s.bubble, s.bubble_t = HAND_NAMES[s.best[0]], 6.0
        winnings = {}
        levels = sorted({s.total for s in self.seats if s.total})
        prev = 0
        main_winners = None
        for lvl in levels:                       # main pot + side pots
            amount = sum(min(s.total, lvl) - min(s.total, prev) for s in self.seats)
            eligible = [s for s in live if s.total >= lvl] or live
            best = max(s.best for s in eligible)
            winners = [s for s in eligible if s.best == best]
            share, extra = divmod(amount, len(winners))
            for k, s in enumerate(winners):
                winnings[s] = winnings.get(s, 0) + share + (extra if k == 0 else 0)
            if main_winners is None:
                main_winners = winners
            prev = lvl
        if winnings.get(self.you) and self.you.best and self.you.best[0] >= 5:
            self.app.unlock("flush_cash")
        names = " & ".join("YOU" if s is self.you else s.name for s in main_winners)
        verb = "WIN" if len(main_winners) > 1 or main_winners[0] is self.you else "WINS"
        self.award(winnings, f"{names} {verb} WITH {HAND_NAMES[main_winners[0].best[0]].upper()}")

    # ---- computer players ------------------------------------------------
    def bot_act(self, i):
        if self.phase != "hand" or self.to_act != i or not self.seats[i].can_act:
            return
        s = self.seats[i]
        opp = sum(1 for o in self.seats if o is not s and o.live)
        eq = equity(self.hole(s), self.board_cards(), opp)
        strength = min(1.0, eq * (opp + 1) / 2) * s.aggr
        to_call = self.current_bet - s.bet
        pot = self.pot()
        odds = to_call / (pot + to_call) if to_call else 0
        r = random.random()
        if strength > 0.85 and r < 0.75:
            target = self.current_bet + max(self.min_raise, pot + to_call)
            if strength > 0.95 and r < 0.2:
                target = s.bet + s.stack
            self.act(i, "raise", target)
        elif strength > 0.62 and r < 0.4:
            self.act(i, "raise", self.current_bet + max(self.min_raise, (pot + to_call) // 2))
        elif to_call == 0:
            if r < 0.08 * s.aggr:
                self.act(i, "raise", self.current_bet + max(self.min_raise, pot // 2))
            else:
                self.act(i, "call")
        elif eq >= odds * 0.95 or (to_call <= POKER_BB and eq > 1 / (opp + 2)) or r < 0.04:
            self.act(i, "call")
        else:
            self.act(i, "fold")

    # ---- your actions ----------------------------------------------------
    def raise_targets(self):
        to_call = self.current_bet - self.you.bet
        pot = self.pot()
        return [self.current_bet + self.min_raise,
                self.current_bet + max(self.min_raise, (pot + to_call) // 2),
                self.current_bet + max(self.min_raise, pot + to_call)]

    def press(self, idx):
        if not self.your_turn:
            return
        to_call = self.current_bet - self.you.bet
        max_to = self.you.bet + self.you.stack
        if idx == 0 and to_call > 0:
            self.act(0, "fold")
        elif idx == 1:
            self.act(0, "call")
        elif idx in (2, 3, 4):
            target = self.raise_targets()[idx - 2]
            if target < max_to and self.you.stack > to_call and self.raises < 4:
                self.act(0, "raise", target)
        elif idx == 5 and self.you.stack > to_call:
            self.act(0, "raise", max_to)

    def handle(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.phase == "idle":
                for v, (cx, cy) in self.chip_pos.items():
                    if math.hypot(e.pos[0] - cx, e.pos[1] - cy) <= 31:
                        room = min(BUYIN_MAX, self.app.balance) - self.buyin
                        if room <= 0:
                            self.message = "THAT'S THE MOST YOU CAN BUY IN FOR"
                        else:
                            self.buyin += min(v, room)
                            self.app.sfx("chip")
                        return
                if self.btn_clear.clicked(e.pos):
                    self.buyin = 0
                elif self.btn_sit.clicked(e.pos):
                    self.sit()
            elif self.phase == "between":
                if self.btn_next.clicked(e.pos):
                    self.start_hand()
                elif self.btn_stand.clicked(e.pos):
                    self.leave()
            elif self.your_turn:
                for i, b in enumerate(self.btn_act):
                    if b.clicked(e.pos):
                        self.press(i)
        elif e.type == pygame.KEYDOWN:
            if self.phase == "between" and e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.start_hand()
            elif self.your_turn:
                keys = {pygame.K_f: 0, pygame.K_c: 1, pygame.K_a: 5}
                if e.key in keys:
                    self.press(keys[e.key])

    def update(self, dt):
        self.t += dt
        if self.queue:
            self.queue[0][0] -= dt
            if self.queue[0][0] <= 0:
                _, fn = self.queue.pop(0)
                fn()
        for sp in self.board + self.my_sprites:
            sp.update(dt)
        for s in self.seats:
            s.bubble_t = max(0.0, s.bubble_t - dt)
        if self.phase == "between" and not self.queue:
            self.between_t -= dt
            if self.between_t <= 0:
                self.start_hand()

    # ---- drawing ---------------------------------------------------------
    def small_face(self, card):
        if card not in self.small_faces:
            self.small_faces[card] = pygame.transform.smoothscale(self.assets.faces[card], (54, 76))
        return self.small_faces[card]

    def draw_seat(self, surf, i, s):
        px, py = SEAT_PANEL[i]
        rect = pygame.Rect(0, 0, 196, 54)
        rect.center = (px, py)
        if i == 0 and not self.seated():
            p = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(p, (0, 0, 0, 140), p.get_rect(), border_radius=27)
            surf.blit(p, rect)
            pygame.draw.rect(surf, GOLD_DARK, rect, width=2, border_radius=27)
            draw_text(surf, "YOUR SEAT", font(16, bold=True), (190, 180, 150), rect.center)
            return
        # cards
        cx, cy = SEAT_CARDS[i]
        if s.bot and s.cards and not s.folded:
            for j, card in enumerate(s.cards):
                img = self.small_face(card) if self.reveal else themed(
                    ("small_back",), lambda: pygame.transform.smoothscale(self.assets.back, (54, 76)))
                surf.blit(img, img.get_rect(center=(cx - 16 + j * 32, cy)))
        # panel
        turn = self.phase == "hand" and self.to_act == i and s.can_act
        p = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(p, (10, 10, 14, 225), p.get_rect(), border_radius=27)
        surf.blit(p, rect)
        if s.won and self.phase == "between":
            border, width = (80, 230, 110), 3
        elif turn:
            border, width = lerp_col(GOLD, WHITE, 0.5 + 0.5 * math.sin(self.t * 7)), 3
        else:
            border, width = GOLD_DARK, 2
        pygame.draw.rect(surf, border, rect, width=width, border_radius=27)
        pygame.draw.circle(surf, s.color, (rect.x + 27, py), 20)
        pygame.draw.circle(surf, WHITE, (rect.x + 27, py), 20, width=2)
        draw_text(surf, s.name[0], font(20, bold=True), WHITE, (rect.x + 27, py))
        draw_text(surf, s.name, fit_font(s.name, 16, 132), WHITE, (rect.x + 56, py - 11), anchor="midleft")
        draw_text(surf, money(s.stack), font(16, bold=True), GOLD, (rect.x + 56, py + 11), anchor="midleft")
        if s.folded or (self.phase == "hand" and not s.cards):
            p.fill((0, 0, 0, 0))
            pygame.draw.rect(p, (0, 0, 0, 130), p.get_rect(), border_radius=27)
            surf.blit(p, rect)
        # bet in front of the seat
        if s.bet:
            bx, by = SEAT_BET[i]
            for k, v in enumerate(chip_breakdown(s.bet)[:8]):
                pygame.draw.circle(surf, darken(CHIP_STYLE[v][0], 80), (bx - 22, by + 2 - k * 4), 14)
                img = self.assets.chip(v, 14)
                surf.blit(img, img.get_rect(center=(bx - 22, by - k * 4)))
            draw_pill(surf, money(s.bet), font(14, bold=True), (bx + 18, by), WHITE, (0, 0, 0, 180), None,
                      pad=(8, 2))
        if self.dealer == i and self.phase != "idle":
            bx, by = SEAT_BTN[i]
            pygame.draw.circle(surf, (30, 30, 30), (bx + 1, by + 2), 13)
            pygame.draw.circle(surf, WHITE, (bx, by), 13)
            draw_text(surf, "D", font(14, bold=True), (20, 20, 20), (bx, by))
        if s.bubble_t > 0 and s.bubble:
            good = s.bubble.startswith("WINS")
            draw_pill(surf, s.bubble, font(15, bold=True), SEAT_BUBBLE[i], WHITE,
                      (30, 140, 60, 235) if good else (20, 20, 28, 235), GOLD if good else None, pad=(12, 4))

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        for i, s in enumerate(self.seats):
            self.draw_seat(surf, i, s)
        for sp in self.board:
            sp.draw(surf, self.assets)
        for sp in self.my_sprites:
            sp.draw(surf, self.assets)
            if self.you.folded:
                d = pygame.Surface((CW, CH), pygame.SRCALPHA)
                pygame.draw.rect(d, (0, 0, 0, 140), d.get_rect(), border_radius=8)
                surf.blit(d, (sp.x, sp.y))
        if self.phase in ("hand", "between") and self.pot():
            draw_pill(surf, f"POT  {money(self.pot())}", font(20, bold=True), (640, 212), GOLD, (0, 0, 0, 190),
                      GOLD_DARK, pad=(16, 4))
        if self.phase in ("hand", "between") and self.you.cards and not self.you.folded:
            best = eval_hand(self.hole(self.you) + self.board_cards())
            draw_pill(surf, HAND_NAMES[best[0]], font(15, bold=True), (800, 490), WHITE, (0, 0, 0, 170),
                      GOLD_DARK, pad=(10, 4))
        if self.message:
            draw_pill(surf, self.message, font(17, bold=True), (640, 560 if self.phase == "idle" else 385),
                      GOLD, (0, 0, 0, 200), GOLD_DARK, pad=(16, 5))
        if not self.message or self.phase == "idle":
            draw_text(surf, "NO LIMIT HOLD'EM  -  BLINDS $5 / $10", font(13, bold=True), (150, 200, 165), (640, 385))

        # bottom bar
        pygame.draw.rect(surf, (10, 6, 10), (0, 636, W, 84))
        pygame.draw.line(surf, GOLD_DARK, (0, 636), (W, 636), 2)
        if self.phase == "idle":
            tray = pygame.Rect(0, 0, 590, 80)
            tray.center = (W / 2, CHIP_TRAY_Y + 1)
            pygame.draw.rect(surf, (22, 13, 8), tray, border_radius=16)
            pygame.draw.rect(surf, (90, 60, 35), tray, width=2, border_radius=16)
            for v, (cx, cy) in self.chip_pos.items():
                hover = math.hypot(mouse[0] - cx, mouse[1] - cy) <= 31
                y = cy - (7 if hover else 0)
                pygame.draw.circle(surf, (0, 0, 0), (cx + 2, cy + 4), 31)
                img = self.assets.chip(v, 31)
                surf.blit(img, img.get_rect(center=(cx, y)))
                if v > self.app.balance - self.buyin:
                    surf.blit(self.dim_chip, self.dim_chip.get_rect(center=(cx, y)))
            self.btn_clear.draw(surf, mouse, self.buyin > 0)
            self.btn_sit.text = f"SIT DOWN {money(self.buyin)}" if self.buyin else "SIT DOWN"
            self.btn_sit.draw(surf, mouse, self.buyin >= min(BUYIN_MIN, self.app.balance) and self.buyin > 0)
        elif self.phase == "between":
            self.btn_stand.hint = f"CASH OUT {money(self.you.stack)}"
            self.btn_stand.draw(surf, mouse)
            self.btn_next.text = f"NEXT HAND  ({max(0, math.ceil(self.between_t))})"
            self.btn_next.draw(surf, mouse)
        else:
            to_call = self.current_bet - self.you.bet
            max_to = self.you.bet + self.you.stack
            on = self.your_turn
            can_raise = on and self.you.stack > to_call and self.raises < 4
            labels = ["FOLD", "CHECK" if to_call == 0 else f"CALL {money(min(to_call, self.you.stack))}"]
            word = "BET" if self.current_bet == 0 else "RAISE"
            targets = self.raise_targets()
            labels += [f"{word} {money(t)}" for t in targets] + [f"ALL IN {money(max_to)}"]
            enabled = [on and to_call > 0, on] + [can_raise and t < max_to for t in targets] + [
                on and self.you.stack > to_call]
            for b, label, en in zip(self.btn_act, labels, enabled):
                b.text = label
                b.size = 18 if len(label) > 11 else 20
                b.draw(surf, mouse, en)
            if on:
                draw_text(surf, "YOUR TURN", font(14, bold=True), GOLD, (640, 626))

        self.app.draw_top_bar(surf, "POKER", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Horse racing
# --------------------------------------------------------------------------
HORSE_NAMES = ["Thunder Hoof", "Lucky Clover", "Midnight Dash", "Golden Gallop", "Silver Streak", "Hay Now",
               "Neigh Sayer", "Pony Express", "Dark Horse", "Sir Trots-a-Lot", "Mane Event", "Buttercup",
               "Canter Bury", "Galloping Ghost", "Rocket Mane", "Oat Cuisine", "Photo Finish", "Blaze Runner",
               "Last Minute", "Maple Sprint", "Apple Crumble", "Storm Chaser", "Little Legs", "Mister Zoom"]
HORSE_COATS = [(120, 70, 35), (70, 45, 28), (160, 95, 45), (38, 32, 32), (175, 170, 165), (205, 165, 110)]
HORSE_SILKS = [(220, 40, 45), (40, 100, 220), (240, 200, 40), (40, 160, 70), (150, 60, 190), (245, 130, 30)]
RACE_LEN = 1200                   # metres
PX_PER_M = 5
LANE_TOP, LANE_H = 178, 68
BET_KINDS = [("WIN", 1), ("PLACE", 2), ("SHOW", 3)]   # must finish in the top N
HORSE_PAYBACK = 0.92


def draw_horse(surf, x, y, phase, coat, silk, number, s=1.0):
    """Side view of a galloping horse and jockey facing right. (x, y) is the ground under its middle."""
    dark = darken(coat, 40)
    by = y - 40 * s - abs(math.sin(phase)) * 3 * s

    def leg(px, off, col):
        a = math.sin(phase + off) * 0.7
        bend = 0.9 * max(0.0, math.sin(phase + off + 0.9))
        hip = (x + px * s, by + 5 * s)
        knee = (hip[0] + math.sin(a) * 16 * s, hip[1] + math.cos(a) * 16 * s)
        la = a - bend if px < 0 else a + bend
        hoof = (knee[0] + math.sin(la) * 16 * s, knee[1] + math.cos(la) * 16 * s)
        pygame.draw.line(surf, col, hip, knee, max(2, int(5 * s)))
        pygame.draw.line(surf, col, knee, hoof, max(2, int(4 * s)))
        pygame.draw.circle(surf, (30, 25, 20), hoof, max(1, 2.5 * s))

    leg(-20, 0.0, dark)
    leg(20, math.pi * 0.9, dark)
    tail = [(x - 30 * s, by - 5 * s), (x - 44 * s, by + (2 + math.sin(phase * 2) * 3) * s), (x - 50 * s, by + 14 * s)]
    pygame.draw.lines(surf, dark, False, tail, max(2, int(5 * s)))
    pygame.draw.ellipse(surf, coat, (x - 32 * s, by - 12 * s, 64 * s, 26 * s))
    pygame.draw.polygon(surf, coat, [(x + 18 * s, by - 8 * s), (x + 32 * s, by - 30 * s),
                                     (x + 42 * s, by - 26 * s), (x + 30 * s, by + 4 * s)])
    pygame.draw.ellipse(surf, coat, (x + 30 * s, by - 37 * s, 26 * s, 13 * s))
    pygame.draw.ellipse(surf, dark, (x + 48 * s, by - 34 * s, 9 * s, 8 * s))
    pygame.draw.polygon(surf, coat, [(x + 33 * s, by - 34 * s), (x + 35 * s, by - 43 * s), (x + 39 * s, by - 34 * s)])
    pygame.draw.line(surf, dark, (x + 20 * s, by - 12 * s), (x + 33 * s, by - 33 * s), max(2, int(3 * s)))
    pygame.draw.circle(surf, (15, 10, 10), (x + 44 * s, by - 32 * s), max(1, 1.6 * s))
    leg(-17, 0.5, coat)
    leg(23, math.pi * 0.9 + 0.5, coat)
    # saddle cloth with the race number
    cloth = pygame.Rect(x - 12 * s, by - 11 * s, 20 * s, 15 * s)
    pygame.draw.rect(surf, WHITE, cloth, border_radius=max(1, int(2 * s)))
    draw_text(surf, str(number), font(max(8, 12 * s), bold=True), (20, 20, 20), cloth.center)
    # jockey
    pygame.draw.line(surf, (235, 235, 230), (x - 4 * s, by - 12 * s), (x + 4 * s, by - 1 * s), max(2, int(5 * s)))
    pygame.draw.polygon(surf, silk, [(x - 12 * s, by - 14 * s), (x + 6 * s, by - 31 * s),
                                     (x + 15 * s, by - 25 * s), (x - 2 * s, by - 9 * s)])
    pygame.draw.line(surf, silk, (x + 8 * s, by - 26 * s), (x + 27 * s, by - 20 * s), max(2, int(3 * s)))
    pygame.draw.circle(surf, (230, 190, 160), (x + 13 * s, by - 34 * s), 5 * s)
    pygame.draw.circle(surf, lighten(silk, 30), (x + 12 * s, by - 36 * s), 5 * s,
                       draw_top_left=True, draw_top_right=True)


def make_crowd_tile():
    rng = random.Random(8)
    s = pygame.Surface((600, 112))
    for y in range(112):
        pygame.draw.line(s, lerp_col((90, 90, 110), (60, 60, 75), y / 112), (0, y), (600, y))
    pygame.draw.rect(s, (40, 40, 50), (0, 0, 600, 18))
    for x in range(0, 600, 60):
        pygame.draw.rect(s, (70, 70, 85), (x, 0, 6, 112))
    for row in range(7):
        for i in range(60):
            x = i * 10 + (5 if row % 2 else 0) + rng.randint(-2, 2)
            y = 30 + row * 12 + rng.randint(-1, 1)
            col = rng.choice([(220, 60, 60), (60, 120, 220), (240, 220, 80), (240, 240, 240), (60, 180, 90),
                              (200, 120, 200), (230, 150, 60), (40, 40, 40)])
            pygame.draw.circle(s, (230, 190, 150), (x, y - 5), 3)
            pygame.draw.rect(s, col, (x - 3, y - 2, 7, 7), border_radius=2)
    return s


def make_dirt_tile():
    rng = random.Random(6)
    s = pygame.Surface((400, 6 * LANE_H))
    s.fill((164, 120, 78))
    for i in range(6):
        if i % 2:
            pygame.draw.rect(s, (158, 115, 74), (0, i * LANE_H, 400, LANE_H))
        pygame.draw.line(s, (180, 140, 100), (0, i * LANE_H), (400, i * LANE_H))
    for _ in range(900):
        c = rng.choice([(140, 100, 64), (185, 145, 105), (150, 108, 70)])
        pygame.draw.circle(s, c, (rng.randrange(400), rng.randrange(6 * LANE_H)), rng.choice((1, 1, 2)))
    return s


class HorseRace:
    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.crowd = make_crowd_tile()
        self.dirt = make_dirt_tile()
        self.board_bg = pygame.Surface((W, H))
        for y in range(H):
            pygame.draw.line(self.board_bg, lerp_col((14, 48, 30), (6, 20, 12), y / H), (0, y), (W, y))
        self.sky = pygame.Surface((W, 30))
        for y in range(30):
            pygame.draw.line(self.sky, lerp_col((110, 170, 230), (170, 210, 240), y / 30), (0, y), (W, y))
        self.race_no = 0
        self.winners = []
        self.selected = 10
        self.last_bets = {}
        x0 = W / 2 - 3.5 * 70
        self.chip_pos = CHIP_WIN
        self.btn_clear = Button((22, 652, 100, 52), "CLEAR", (120, 35, 40), 18)
        self.btn_undo = Button((130, 652, 100, 52), "UNDO", (80, 80, 90), 18)
        self.btn_rebet = Button((238, 652, 100, 52), "REBET", (40, 70, 130), 18)
        self.btn_start = Button((965, 646, 270, 62), "START RACE", (25, 120, 60), 26, "SPACE")
        self.btn_next = Button((965, 646, 270, 62), "NEXT RACE", (25, 120, 60), 26, "SPACE")
        self.new_race()

    # ---- setup -----------------------------------------------------------
    def new_race(self):
        self.race_no += 1
        names = random.sample(HORSE_NAMES, 6)
        w = [random.lognormvariate(0, 0.55) for _ in range(6)]
        total = sum(w)
        p1 = [x / total for x in w]
        p2, p3 = [0.0] * 6, [0.0] * 6
        for a in range(6):                      # exact top-2 / top-3 chances
            pa = w[a] / total
            for b in range(6):
                if b == a:
                    continue
                pab = pa * w[b] / (total - w[a])
                p2[a] += pab
                p2[b] += pab
                for c in range(6):
                    if c in (a, b):
                        continue
                    pabc = pab * w[c] / (total - w[a] - w[b])
                    for h in (a, b, c):
                        p3[h] += pabc

        def mult(p):
            return max(1.05, math.floor(HORSE_PAYBACK / p * 10) / 10)

        fav = max(range(6), key=lambda i: w[i])
        coats = random.sample(HORSE_COATS, 6)
        self.horses = [{"name": names[i], "num": i + 1, "w": w[i], "p": p1[i], "fav": i == fav,
                        "coat": coats[i], "silk": HORSE_SILKS[i],
                        "odds": {"WIN": mult(p1[i]), "PLACE": mult(p2[i]), "SHOW": mult(p3[i])},
                        "x": 0.0, "phase": random.uniform(0, 6.3), "T": 1.0, "b": []}
                       for i in range(6)]
        self.bets = {}
        self.history = []
        self.state = "betting"          # betting, racing, result
        self.t = 0.0
        self.cam = 0.0
        self.order = []
        self.comment = ""
        self.comment_t = 0.0
        self.leader = None
        self.message = ""

    def can_leave(self):
        return self.state != "racing"

    def outstanding_bets(self):
        return self.total_bet() if self.state != "result" else 0

    def leave(self):
        if self.state == "betting":
            self.app.balance += self.total_bet()
            self.bets, self.history = {}, []
        elif self.state == "result":
            self.new_race()

    def total_bet(self):
        return sum(b["amount"] for b in self.bets.values())

    # ---- betting ---------------------------------------------------------
    def bet_rect(self, i, k):
        return pygame.Rect(24 + 470 + k * 134, 112 + i * 84 + 12, 124, 52)

    def bet_at(self, pos):
        for i in range(6):
            for k, (kind, _) in enumerate(BET_KINDS):
                if self.bet_rect(i, k).collidepoint(pos):
                    return i, kind
        return None

    def add_bet(self, key, v):
        if self.app.balance < v:
            self.message = "NOT ENOUGH CHIPS"
            return
        i, kind = key
        self.app.balance -= v
        b = self.bets.setdefault(key, {"amount": 0, "mult": self.horses[i]["odds"][kind]})
        b["amount"] += v
        self.history.append((key, v))
        self.message = ""
        self.app.sfx("chip")

    def clear(self):
        self.app.balance += self.total_bet()
        self.bets, self.history = {}, []

    def undo(self):
        if self.history:
            key, v = self.history.pop()
            self.bets[key]["amount"] -= v
            if self.bets[key]["amount"] <= 0:
                del self.bets[key]
            self.app.balance += v
            self.app.sfx("chip")

    def rebet(self):
        """Same amounts on the same bet types and lane numbers as last race."""
        self.clear()
        need = sum(self.last_bets.values())
        if need > self.app.balance:
            self.message = "NOT ENOUGH CHIPS TO REBET"
            return
        for key, amount in self.last_bets.items():
            self.add_bet(key, amount)

    # ---- the race --------------------------------------------------------
    def start_race(self):
        self.last_bets = {k: b["amount"] for k, b in self.bets.items()}
        remaining = list(range(6))
        self.order = []
        while remaining:                          # finishing order, weighted by ability
            r = random.uniform(0, sum(self.horses[i]["w"] for i in remaining))
            for i in remaining:
                r -= self.horses[i]["w"]
                if r <= 0:
                    break
            self.order.append(i)
            remaining.remove(i)
        t = random.uniform(15.5, 17.0)
        for i in self.order:
            h = self.horses[i]
            h["T"] = t
            t += random.uniform(0.06, 0.55)
            # random surges and fades: f(u) = u + sum b_k sin(pi k u) stays monotonic and hits 1 exactly at u = 1
            b = [random.uniform(-1, 1) for _ in range(3)]
            norm = sum(abs(x) * math.pi * (k + 1) for k, x in enumerate(b)) or 1
            amp = random.uniform(0.25, 0.6) / norm
            h["b"] = [x * amp for x in b]
        self.state = "racing"
        self.t = -1.2                             # short pause in the gate
        self.say("THE HORSES ARE IN THE GATE...")
        self.app.sfx("launch")

    def say(self, text, dur=2.5):
        self.comment, self.comment_t = text, dur

    def pos_at(self, h, t):
        if t <= 0:
            return 0.0
        u = t / h["T"]
        if u >= 1:
            return RACE_LEN + (t - h["T"]) * RACE_LEN / h["T"] * 0.5
        return RACE_LEN * (u + sum(b * math.sin(math.pi * (k + 1) * u) for k, b in enumerate(h["b"])))

    def settle(self):
        rank = {h: r for r, h in enumerate(self.order)}
        need = dict(BET_KINDS)
        total_ret = 0
        for (i, kind), b in self.bets.items():
            b["won"] = int(b["amount"] * b["mult"]) if rank[i] < need[kind] else 0
            total_ret += b["won"]
        total_bet = self.total_bet()
        self.app.balance += total_ret
        if self.bets:
            self.app.record("horses", total_bet, total_ret)
        if any(kind == "WIN" and b["won"] and self.horses[i]["p"] < 0.10 for (i, kind), b in self.bets.items()):
            self.app.unlock("long_shot")
        net = total_ret - total_bet
        winner = self.horses[self.order[0]]
        self.winners = ([(winner["num"], winner["name"], winner["silk"])] + self.winners)[:6]
        if not self.bets:
            self.message = "YOU WATCHED THIS ONE FROM THE STANDS"
        elif net > 0:
            self.message = f"YOU WIN {money(net)}!"
            self.app.float_text(f"+{money(net)}", (80, 230, 110))
            self.app.sfx("win")
        elif net < 0:
            self.message = f"YOU LOSE {money(-net)}" if total_ret == 0 else \
                f"YOU GET BACK {money(total_ret)} ({money(net).replace('$-', '-$')})"
            self.app.float_text(f"-{money(-net)}", (240, 90, 90))
            self.app.sfx("lose")
        else:
            self.message = "YOU BROKE EVEN"
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        self.state = "result"
        self.app.save()

    # ---- events ----------------------------------------------------------
    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN):
            if self.state == "betting":
                self.start_race()
            elif self.state == "result":
                self.new_race()
            return
        if self.state == "result" and e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.btn_next.clicked(e.pos):
                self.new_race()
            return
        if self.state != "betting":
            return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for v, (cx, cy) in self.chip_pos.items():
                if math.hypot(e.pos[0] - cx, e.pos[1] - cy) <= 31:
                    self.selected = v
                    self.app.sfx("chip")
                    return
            key = self.bet_at(e.pos)
            if key:
                self.add_bet(key, self.selected)
            elif self.btn_clear.clicked(e.pos):
                self.clear()
            elif self.btn_undo.clicked(e.pos):
                self.undo()
            elif self.btn_rebet.clicked(e.pos, bool(self.last_bets)):
                self.rebet()
            elif self.btn_start.clicked(e.pos):
                self.start_race()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            key = self.bet_at(e.pos)
            if key in self.bets:
                self.app.balance += self.bets.pop(key)["amount"]
                self.history = [h for h in self.history if h[0] != key]
                self.app.sfx("chip")

    def update(self, dt):
        self.comment_t = max(0.0, self.comment_t - dt)
        if self.state != "racing":
            for h in self.horses:
                h["phase"] += dt * 1.5
            return
        prev_t = self.t
        self.t += dt
        if prev_t < 0 <= self.t:
            self.say("AND THEY'RE OFF!")
            self.app.sfx("card")
        for h in self.horses:
            nx = self.pos_at(h, self.t)
            speed = (nx - h["x"]) / dt if dt else 0
            h["x"] = nx
            h["phase"] += dt * (4 + speed * 0.14)
        running = [h for h in self.horses if h["x"] < RACE_LEN]
        lead = max(self.horses, key=lambda h: h["x"])
        if self.t > 1.5 and running and lead is not self.leader and self.comment_t < 1.0:
            self.leader = lead
            far = lead["x"] / RACE_LEN
            text = f"{lead['name'].upper()} TAKES THE LEAD!" if far < 0.8 else \
                f"{lead['name'].upper()} IN FRONT AS THEY COME TO THE LINE!"
            self.say(text, 2.0)
        first = self.horses[self.order[0]]
        if prev_t < first["T"] <= self.t:
            self.say(f"{first['name'].upper()} WINS RACE {self.race_no}!", 5)
            self.app.sfx("card")
        cam_target = min(max(0.0, lead["x"] * PX_PER_M - 820), RACE_LEN * PX_PER_M + 260 - W)
        self.cam += (cam_target - self.cam) * min(1, dt * 6)
        if self.t > max(h["T"] for h in self.horses) + 1.2:
            self.settle()

    # ---- drawing ---------------------------------------------------------
    def draw_track(self, surf):
        cam = self.cam
        surf.blit(self.sky, (0, 56))
        off = -(cam * 0.35) % 600
        for k in range(-1, 3):
            surf.blit(self.crowd, (off + k * 600, 62))
        off = -cam % 400
        for k in range(-1, 5):
            surf.blit(self.dirt, (off + (k - 1) * 400 + 400, LANE_TOP))
        pygame.draw.rect(surf, (60, 140, 60), (0, LANE_TOP + 6 * LANE_H, W, 60))
        # rails and distance posts
        for y in (LANE_TOP - 4, LANE_TOP + 6 * LANE_H + 2):
            pygame.draw.rect(surf, (245, 245, 245), (0, y - 2, W, 5))
            first = int(cam // 50) * 50
            for wx in range(first, int(cam) + W + 50, 50):
                pygame.draw.rect(surf, (225, 225, 225), (wx - cam + 60, y - 2, 3, 12))
        for m in range(200, RACE_LEN, 200):
            sx = m * PX_PER_M - cam + 60
            if -40 < sx < W + 40:
                pygame.draw.rect(surf, (200, 30, 30), (sx - 2, LANE_TOP - 34, 4, 32))
                draw_pill(surf, f"{RACE_LEN - m}m", font(12, bold=True), (sx, LANE_TOP - 40), (20, 20, 20),
                          (250, 250, 250, 255), None, pad=(6, 1))
        # starting gate & finish line
        gx = -cam + 60
        if gx > -200 and self.t < 1.0:
            for i in range(6):
                y = LANE_TOP + i * LANE_H
                pygame.draw.rect(surf, (120, 120, 130), (gx + 58, y + 2, 10, LANE_H - 4))
        fx = RACE_LEN * PX_PER_M - cam + 60
        if -20 < fx < W + 20:
            for i in range(int(6 * LANE_H / 10) + 1):
                for j in range(2):
                    col = (20, 20, 20) if (i + j) % 2 else (245, 245, 245)
                    pygame.draw.rect(surf, col, (fx + j * 6, LANE_TOP + i * 10, 6, 10))
            pygame.draw.rect(surf, (230, 230, 230), (fx + 3, LANE_TOP - 70, 6, 70))
            draw_pill(surf, "FINISH", font(16, bold=True), (fx + 6, LANE_TOP - 74), WHITE, (200, 30, 30, 255),
                      None, pad=(10, 3))
        # horses, top lane first so lower lanes overlap correctly
        for i, h in enumerate(self.horses):
            sx = h["x"] * PX_PER_M - cam + 60
            gy = LANE_TOP + (i + 1) * LANE_H - 8
            pygame.draw.ellipse(surf, (120, 86, 55), (sx - 36, gy - 5, 72, 10))
            draw_horse(surf, sx, gy, h["phase"] if self.state != "betting" else 0.4, h["coat"], h["silk"], h["num"])

    def draw_board(self, surf, mouse):
        surf.blit(self.board_bg, (0, 0))
        draw_text(surf, f"RACE {self.race_no}  -  PLACE YOUR BETS", font(24, bold=True, serif=True), GOLD,
                  (28, 86), anchor="midleft")
        draw_text(surf, "Pick a chip, then click WIN / PLACE / SHOW.  Right-click removes a bet.",
                  font(14), (180, 210, 190), (430, 86), anchor="midleft")
        hover = self.bet_at(mouse)
        for i, h in enumerate(self.horses):
            r = pygame.Rect(24, 112 + i * 84, 876, 76)
            p = pygame.Surface(r.size, pygame.SRCALPHA)
            pygame.draw.rect(p, (0, 0, 0, 120), p.get_rect(), border_radius=12)
            surf.blit(p, r)
            pygame.draw.rect(surf, (50, 90, 60), r, width=2, border_radius=12)
            badge = pygame.Rect(r.x + 12, r.y + 14, 48, 48)
            pygame.draw.rect(surf, h["silk"], badge, border_radius=8)
            pygame.draw.rect(surf, WHITE, badge, width=2, border_radius=8)
            draw_text(surf, str(h["num"]), font(26, bold=True), WHITE, badge.center, shadow=(0, 0, 0))
            draw_horse(surf, r.x + 118, r.bottom - 8, 0.4, h["coat"], h["silk"], h["num"], 0.72)
            draw_text(surf, h["name"], font(20, bold=True), WHITE, (r.x + 178, r.y + 26), anchor="midleft")
            draw_text(surf, f"Win chance about {h['p'] * 100:.0f}%", font(13), (170, 200, 180),
                      (r.x + 178, r.y + 52), anchor="midleft")
            if h["fav"]:
                draw_pill(surf, "FAVORITE", font(10, bold=True), (r.x + 420, r.y + 26), (20, 20, 20),
                          (*GOLD, 255), None, pad=(6, 2))
            for k, (kind, _) in enumerate(BET_KINDS):
                br = self.bet_rect(i, k)
                on = (i, kind) == hover
                base = [(30, 110, 60), (35, 90, 140), (120, 70, 150)][k]
                pygame.draw.rect(surf, lighten(base, 25) if on else base, br, border_radius=10)
                pygame.draw.rect(surf, GOLD if on else darken(base, 30), br, width=2, border_radius=10)
                draw_text(surf, kind, font(11, bold=True), (220, 230, 220), (br.centerx, br.y + 11))
                draw_text(surf, f"x{h['odds'][kind]:g}", font(20, bold=True), WHITE, (br.centerx, br.y + 33))
                b = self.bets.get((i, kind))
                if b:
                    chip = self.assets.chip(chip_breakdown(b["amount"])[0], 13)
                    surf.blit(chip, chip.get_rect(center=(br.right - 4, br.y + 4)))
                    draw_pill(surf, money(b["amount"]), font(11, bold=True), (br.right - 4, br.bottom - 2),
                              (20, 20, 20), (*GOLD, 255), None, pad=(5, 1))

        # side panel
        side = pygame.Rect(916, 72, 348, 548)
        p = pygame.Surface(side.size, pygame.SRCALPHA)
        pygame.draw.rect(p, (0, 0, 0, 140), p.get_rect(), border_radius=14)
        surf.blit(p, side)
        pygame.draw.rect(surf, GOLD_DARK, side, width=2, border_radius=14)
        draw_text(surf, "YOUR BETS", font(20, bold=True, serif=True), GOLD, (side.centerx, side.y + 24))
        y = side.y + 56
        for (i, kind), b in list(self.bets.items())[:9]:
            h = self.horses[i]
            pygame.draw.circle(surf, h["silk"], (side.x + 24, y), 10)
            draw_text(surf, str(h["num"]), font(12, bold=True), WHITE, (side.x + 24, y))
            draw_text(surf, f"{kind}  x{b['mult']:g}", font(14, bold=True), WHITE, (side.x + 42, y), anchor="midleft")
            draw_text(surf, money(b["amount"]), font(14, bold=True), GOLD, (side.right - 16, y), anchor="midright")
            y += 26
        if not self.bets:
            draw_text(surf, "No bets yet - or just watch the race!", font(14), (170, 170, 170),
                      (side.centerx, y + 4))
        tb = self.total_bet()
        best = sum(int(b["amount"] * b["mult"]) for b in self.bets.values())
        draw_text(surf, f"TOTAL BET  {money(tb)}", font(16, bold=True), WHITE, (side.x + 18, side.y + 318),
                  anchor="midleft")
        if tb:
            draw_text(surf, f"If everything hits: {money(best)}", font(13), (170, 210, 180),
                      (side.x + 18, side.y + 340), anchor="midleft")
        pygame.draw.line(surf, (70, 60, 40), (side.x + 14, side.y + 360), (side.right - 14, side.y + 360))
        for j, line in enumerate(["WIN - horse must finish 1st", "PLACE - finish 1st or 2nd",
                                  "SHOW - finish in the top 3", "Payout = bet x the number shown"]):
            draw_text(surf, line, font(13), (200, 200, 190), (side.x + 18, side.y + 380 + j * 20), anchor="midleft")
        draw_text(surf, "RECENT WINNERS", font(13, bold=True), GOLD, (side.x + 18, side.y + 470), anchor="midleft")
        for j, (num, name, silk) in enumerate(self.winners[:3]):
            yy = side.y + 494 + j * 18
            pygame.draw.circle(surf, silk, (side.x + 26, yy), 7)
            draw_text(surf, name, font(13), (220, 220, 220), (side.x + 40, yy), anchor="midleft")
        if self.message:
            draw_pill(surf, self.message, font(16, bold=True), (462, 630), GOLD, (0, 0, 0, 210), GOLD_DARK,
                      pad=(14, 3))

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        if self.state == "betting":
            self.draw_board(surf, mouse)
        else:
            self.draw_track(surf)
            if self.comment_t:
                draw_pill(surf, self.comment, font(20, bold=True), (W / 2, 88), WHITE, (10, 10, 20, 220), GOLD,
                          pad=(18, 5))

        # bottom bar
        pygame.draw.rect(surf, (10, 16, 12), (0, 636, W, 84))
        pygame.draw.line(surf, GOLD_DARK, (0, 636), (W, 636), 2)
        if self.state == "betting":
            tray = pygame.Rect(0, 0, 590, 80)
            tray.center = (W / 2, CHIP_TRAY_Y + 1)
            pygame.draw.rect(surf, (6, 14, 8), tray, border_radius=16)
            pygame.draw.rect(surf, (60, 100, 70), tray, width=2, border_radius=16)
            for v, (cx, cy) in self.chip_pos.items():
                sel = v == self.selected
                hover = math.hypot(mouse[0] - cx, mouse[1] - cy) <= 31
                y = cy - (9 if sel else 5 if hover else 0)
                pygame.draw.circle(surf, (0, 0, 0), (cx + 2, cy + 4), 31)
                if sel:
                    pygame.draw.circle(surf, GOLD, (cx, y), 35, width=3)
                img = self.assets.chip(v, 31)
                surf.blit(img, img.get_rect(center=(cx, y)))
            self.btn_clear.draw(surf, mouse, bool(self.bets))
            self.btn_undo.draw(surf, mouse, bool(self.history))
            self.btn_rebet.draw(surf, mouse, bool(self.last_bets))
            self.btn_start.text = "START RACE" if self.bets else "WATCH RACE"
            self.btn_start.draw(surf, mouse)
        else:
            # live standings strip
            bar = pygame.Rect(40, 668, 760, 8)
            pygame.draw.rect(surf, (60, 70, 60), bar, border_radius=4)
            pygame.draw.rect(surf, WHITE, (bar.right - 2, bar.y - 8, 4, 24))
            for h in sorted(self.horses, key=lambda h: h["x"]):
                x = bar.x + min(1.0, h["x"] / RACE_LEN) * bar.w
                pygame.draw.circle(surf, h["silk"], (x, bar.centery), 11)
                pygame.draw.circle(surf, WHITE, (x, bar.centery), 11, width=2)
                draw_text(surf, str(h["num"]), font(11, bold=True), WHITE, (x, bar.centery))
            draw_text(surf, "START", font(11, bold=True), (150, 170, 150), (bar.x, bar.y - 14), anchor="midleft")
            draw_text(surf, "FINISH", font(11, bold=True), (150, 170, 150), (bar.right, bar.y - 14), anchor="midright")
            if self.state == "result":
                self.btn_next.draw(surf, mouse)
            else:
                standing = sorted(self.horses, key=lambda h: -h["x"])
                for k, h in enumerate(standing[:3]):
                    x = 860 + k * 130
                    draw_text(surf, ["1st", "2nd", "3rd"][k], font(13, bold=True), GOLD, (x, 666), anchor="midleft")
                    pygame.draw.circle(surf, h["silk"], (x + 44, 666), 12)
                    draw_text(surf, str(h["num"]), font(13, bold=True), WHITE, (x + 44, 666))
                draw_text(surf, f"{self.total_bet() and money(self.total_bet()) + ' riding on this race' or 'Just watching'}",
                          font(13), (180, 200, 180), (1050, 700))

        if self.state == "result":
            panel = pygame.Rect(0, 0, 560, 330)
            panel.center = (W / 2, 390)
            p = pygame.Surface(panel.size, pygame.SRCALPHA)
            pygame.draw.rect(p, (8, 10, 16, 235), p.get_rect(), border_radius=18)
            surf.blit(p, panel)
            pygame.draw.rect(surf, GOLD, panel, width=3, border_radius=18)
            draw_text(surf, f"RACE {self.race_no} RESULTS", font(26, bold=True, serif=True), GOLD,
                      (panel.centerx, panel.y + 32))
            for k, i in enumerate(self.order[:3]):
                h = self.horses[i]
                y = panel.y + 80 + k * 46
                draw_text(surf, ["1ST", "2ND", "3RD"][k], font(20, bold=True), [GOLD, (200, 200, 210), (205, 130, 60)][k],
                          (panel.x + 40, y), anchor="midleft")
                badge = pygame.Rect(panel.x + 100, y - 17, 34, 34)
                pygame.draw.rect(surf, h["silk"], badge, border_radius=6)
                draw_text(surf, str(h["num"]), font(18, bold=True), WHITE, badge.center)
                draw_text(surf, h["name"], font(20, bold=True), WHITE, (panel.x + 148, y), anchor="midleft")
                draw_text(surf, f"{h['T']:.2f}s", font(15), (170, 170, 180), (panel.right - 30, y), anchor="midright")
            won = [(k, b) for k, b in self.bets.items() if b.get("won")]
            y = panel.y + 228
            for (i, kind), b in won[:2]:
                draw_text(surf, f"#{i + 1} {kind} paid {money(b['won'])}", font(15, bold=True), (80, 230, 110),
                          (panel.centerx, y))
                y += 22
            draw_pill(surf, self.message, font(18, bold=True), (panel.centerx, panel.bottom - 34), GOLD,
                      (0, 0, 0, 255), GOLD_DARK)

        self.app.draw_top_bar(surf, "HORSE RACING", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Multiplier wheel
# --------------------------------------------------------------------------
# 24 equal slices: about a 1 in 3 chance to win, 96.7% payback overall
MWHEEL = [0.5, 1.2, 0.1, 2, 0.3, 0.8, 1.5, 0.1, 0.5, 5, 0.1, 0.8, 1.2, 0.3, 0.5, 3, 0.1, 0.8, 1.5, 0.3, 0.5, 1.2,
          0.1, 0.8]
MSLICE = 2 * math.pi / len(MWHEEL)
MWHEEL_C = (420, 372)
MWHEEL_R = 248
MULT_COLORS = {0.1: (110, 18, 28), 0.3: (160, 32, 42), 0.5: (200, 70, 48), 0.8: (220, 125, 45),
               1.2: (55, 160, 80), 1.5: (30, 140, 120), 2: (40, 110, 205), 3: (130, 60, 205), 5: (235, 185, 30)}


def make_mult_wheel(R):
    k = 2
    Rk = R * k
    size = 2 * Rk
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    c = (Rk, Rk)
    pygame.draw.circle(s, (60, 40, 20), c, Rk)
    pygame.draw.circle(s, GOLD_DARK, c, Rk * 0.99)
    pygame.draw.circle(s, (80, 55, 25), c, Rk * 0.95)
    for i, m in enumerate(MWHEEL):
        a0, a1 = i * MSLICE - MSLICE / 2, i * MSLICE + MSLICE / 2
        pts = [c] + [wheel_point(c, a0 + (a1 - a0) * t / 8, Rk * 0.93) for t in range(9)]
        col = MULT_COLORS[m]
        pygame.draw.polygon(s, col, pts)
        inner = [c] + [wheel_point(c, a0 + (a1 - a0) * t / 8, Rk * 0.5) for t in range(9)]
        pygame.draw.polygon(s, darken(col, 18), inner)
    for i in range(len(MWHEEL)):
        a = i * MSLICE - MSLICE / 2
        pygame.draw.line(s, (240, 225, 180), c, wheel_point(c, a, Rk * 0.93), 2 * k)
        pygame.draw.circle(s, (250, 240, 200), wheel_point(c, a, Rk * 0.965), 5 * k)
        pygame.draw.circle(s, GOLD_DARK, wheel_point(c, a, Rk * 0.965), 5 * k, width=k)
    for i, m in enumerate(MWHEEL):
        a = i * MSLICE
        label = f"x{m:g}"
        img = font(int(Rk * 0.085), bold=True).render(label, True, (40, 25, 5) if m == 5 else WHITE)
        flip = 180 if a > math.pi else 0          # keep labels on the left half the right way up
        rot = pygame.transform.rotozoom(img, 90 - math.degrees(a) + flip, 1)
        s.blit(rot, rot.get_rect(center=wheel_point(c, a, Rk * 0.70)))
    return pygame.transform.smoothscale(s, (2 * R, 2 * R))


class MultWheel:
    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.wheel = make_mult_wheel(MWHEEL_R)
        self.bg = Menu.make_bg()
        self.rot = random.uniform(0, 2 * math.pi)
        self.bet = 0
        self.stake = 0
        self.state = "idle"             # idle, spinning, result
        self.t = 0.0
        self.dur = 5.5
        self.rot0 = self.rot_end = self.rot
        self.result = None
        self.history = []
        self.kick = 0.0
        self.tick_cd = 0.0
        self.last_slice = self.slice_under()
        self.message = "ADD CHIPS TO SET YOUR BET, THEN SPIN"
        self.clock = 0.0
        x0 = W / 2 - 3.5 * 70
        self.chip_pos = CHIP_WIN
        self.btn_clear = Button((40, 652, 150, 52), "CLEAR BET", (120, 35, 40), 18)
        self.btn_spin = Button((965, 646, 270, 62), "SPIN", (25, 120, 60), 28, "SPACE")
        self.dim_chip = pygame.Surface((64, 64), pygame.SRCALPHA)
        pygame.draw.circle(self.dim_chip, (0, 0, 0, 150), (32, 32), 31)
        counts = {}
        for m in MWHEEL:
            counts[m] = counts.get(m, 0) + 1
        self.legend = sorted(counts.items())

    def can_leave(self):
        return self.state != "spinning"

    def outstanding_bets(self):
        return self.stake if self.state == "spinning" else 0

    def leave(self):
        pass

    def slice_under(self):
        return round(-self.rot / MSLICE) % len(MWHEEL)

    def all_in(self):
        """ALL IN: your whole balance becomes the bet."""
        if self.state == "spinning":
            return
        self.bet = self.app.balance
        self.message = f"ALL IN!  BET {money(self.bet)}" if self.bet else "YOU HAVE NO CHIPS TO BET"
        self.app.sfx("chip")

    def spin(self):
        if self.state == "spinning":
            return
        if not self.bet:
            self.message = "ADD CHIPS TO SET YOUR BET FIRST"
            return
        if self.bet > self.app.balance:
            self.message = "NOT ENOUGH CHIPS - LOWER YOUR BET"
            return
        self.app.balance -= self.bet
        self.stake = self.bet
        target = random.randrange(len(MWHEEL))
        wobble = random.uniform(-0.38, 0.38) * MSLICE
        self.rot0 = self.rot
        delta = (-(target * MSLICE) - wobble - self.rot0) % (2 * math.pi)
        self.rot_end = self.rot0 + delta + 2 * math.pi * random.randint(4, 6)
        self.dur = random.uniform(5.0, 6.0)
        self.t = 0.0
        self.target = target
        self.state = "spinning"
        self.message = ""
        self.app.sfx("launch")

    def settle(self):
        m = MWHEEL[self.target]
        self.result = m
        back = int(self.stake * m)
        net = back - self.stake
        self.app.balance += back
        self.app.record("wheel", self.stake, back)
        if m == 5:
            self.app.unlock("top_wheel")
        self.history = ([m] + self.history)[:12]
        if net > 0:
            self.message = f"x{m:g}!  YOU WIN {money(back)}  (+{money(net)})"
            self.app.float_text(f"+{money(net)}", (80, 230, 110))
            self.app.sfx("win")
        else:
            self.message = f"x{m:g}  -  YOU GET BACK {money(back)}  (-{money(-net)})"
            self.app.float_text(f"-{money(-net)}", (240, 90, 90))
            self.app.sfx("lose")
        if self.bet > self.app.balance:
            self.bet = 0
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        self.state = "result"
        self.app.save()

    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN):
            self.spin()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.state != "spinning":
            for v, (cx, cy) in self.chip_pos.items():
                if math.hypot(e.pos[0] - cx, e.pos[1] - cy) <= 31:
                    if self.bet + v > self.app.balance:
                        self.message = "NOT ENOUGH CHIPS FOR THAT BET"
                    else:
                        self.bet += v
                        self.message = ""
                        self.app.sfx("chip")
                    return
            if self.btn_clear.clicked(e.pos):
                self.bet = 0
            elif self.btn_spin.clicked(e.pos):
                self.spin()

    def update(self, dt):
        self.clock += dt
        self.tick_cd = max(0.0, self.tick_cd - dt)
        self.kick *= math.exp(-dt * 10)
        if self.state == "spinning":
            self.t += dt
            p = min(1.0, self.t / self.dur)
            self.rot = self.rot0 + (self.rot_end - self.rot0) * (1 - (1 - p) ** 4)
            if p >= 1:
                self.settle()
        sl = self.slice_under()
        if sl != self.last_slice:
            self.last_slice = sl
            self.kick = 22.0
            if self.tick_cd <= 0:
                self.app.sfx("card")
                self.tick_cd = 0.045

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        cx, cy = MWHEEL_C
        R = MWHEEL_R
        pygame.draw.circle(surf, (5, 3, 6), (cx + 6, cy + 10), R + 14)
        pygame.draw.circle(surf, (40, 28, 16), (cx, cy), R + 12)
        for i in range(32):          # marquee lights around the wheel
            a = i * 2 * math.pi / 32
            lit = (i + int(self.clock * (16 if self.state == "spinning" else 4))) % 2 == 0
            pygame.draw.circle(surf, (255, 235, 150) if lit else (110, 80, 40), wheel_point((cx, cy), a, R + 6), 4)
        rot = pygame.transform.rotozoom(self.wheel, -math.degrees(self.rot), 1)
        surf.blit(rot, rot.get_rect(center=(cx, cy)))
        # hub shows the multiplier under the pointer
        m = MWHEEL[self.slice_under()]
        pygame.draw.circle(surf, (20, 14, 10), (cx, cy), 64)
        pygame.draw.circle(surf, GOLD, (cx, cy), 64, width=4)
        pygame.draw.circle(surf, MULT_COLORS[m], (cx, cy), 52)
        draw_text(surf, f"x{m:g}", font(34, bold=True), (40, 25, 5) if m == 5 else WHITE, (cx, cy), shadow=(0, 0, 0))
        # pointer at the top, flicking as it hits the pegs
        a = math.radians(self.kick)
        tip = (cx, cy - R + 26)
        base_y = cy - R - 30

        def rp(px, py):
            dx, dy = px - cx, py - base_y
            return (cx + dx * math.cos(a) - dy * math.sin(a), base_y + dx * math.sin(a) + dy * math.cos(a))
        pts = [rp(cx - 22, base_y), rp(cx + 22, base_y), rp(*tip)]
        pygame.draw.polygon(surf, (40, 10, 10), [(x + 3, y + 3) for x, y in pts])
        pygame.draw.polygon(surf, (225, 35, 45), pts)
        pygame.draw.polygon(surf, GOLD, pts, width=3)
        pygame.draw.circle(surf, GOLD, (cx, base_y), 9)

        # right panel
        panel = pygame.Rect(720, 72, 544, 548)
        p = pygame.Surface(panel.size, pygame.SRCALPHA)
        pygame.draw.rect(p, (0, 0, 0, 150), p.get_rect(), border_radius=16)
        surf.blit(p, panel)
        pygame.draw.rect(surf, GOLD_DARK, panel, width=2, border_radius=16)
        draw_text(surf, "MULTIPLIER WHEEL", font(28, bold=True, serif=True), GOLD, (panel.centerx, panel.y + 34))
        draw_text(surf, "Your bet is multiplied by wherever the pointer stops.", font(14), (210, 200, 180),
                  (panel.centerx, panel.y + 66))
        draw_text(surf, "Anything under x1 means you lose part of your bet!", font(14), (240, 150, 140),
                  (panel.centerx, panel.y + 86))
        for j, (mult, n) in enumerate(self.legend):
            col, row = j % 3, j // 3
            x = panel.x + 34 + col * 170
            y = panel.y + 130 + row * 52
            sw = pygame.Rect(x, y - 18, 64, 36)
            pygame.draw.rect(surf, MULT_COLORS[mult], sw, border_radius=8)
            pygame.draw.rect(surf, (240, 225, 180), sw, width=2, border_radius=8)
            draw_text(surf, f"x{mult:g}", font(17, bold=True), (40, 25, 5) if mult == 5 else WHITE, sw.center)
            draw_text(surf, f"{n / len(MWHEEL) * 100:.0f}%", font(16, bold=True), WHITE, (x + 76, y - 7),
                      anchor="midleft")
            draw_text(surf, "chance", font(11), (160, 160, 160), (x + 76, y + 10), anchor="midleft")
        # bet + win displays
        for label, value, rect, col in (
                ("BET", money(self.bet), pygame.Rect(panel.x + 34, panel.y + 300, 220, 64), (255, 70, 50)),
                ("LAST WIN", money(int(self.stake * self.result)) if self.result is not None else "-",
                 pygame.Rect(panel.x + 290, panel.y + 300, 220, 64),
                 (90, 255, 110) if self.result and self.result > 1 else (255, 70, 50))):
            pygame.draw.rect(surf, (10, 8, 8), rect, border_radius=10)
            pygame.draw.rect(surf, GOLD_DARK, rect, width=2, border_radius=10)
            draw_text(surf, label, font(12, bold=True), (200, 170, 120), (rect.centerx, rect.y + 13))
            draw_text(surf, value, font(28, bold=True), col, (rect.centerx, rect.y + 40))
        if self.message:
            draw_pill(surf, self.message, font(17, bold=True), (panel.centerx, panel.y + 400), GOLD,
                      (0, 0, 0, 220), GOLD_DARK, pad=(14, 5))
        draw_text(surf, "LAST SPINS", font(13, bold=True), GOLD, (panel.x + 34, panel.y + 452), anchor="midleft")
        for j, mult in enumerate(self.history):
            x = panel.x + 54 + j * 41
            pygame.draw.circle(surf, MULT_COLORS[mult], (x, panel.y + 490), 18)
            pygame.draw.circle(surf, GOLD if j == 0 else (200, 200, 200), (x, panel.y + 490), 18, width=2)
            draw_text(surf, f"{mult:g}", font(13, bold=True), (40, 25, 5) if mult == 5 else WHITE, (x, panel.y + 490))

        # chip tray
        pygame.draw.rect(surf, (10, 6, 10), (0, 636, W, 84))
        pygame.draw.line(surf, GOLD_DARK, (0, 636), (W, 636), 2)
        tray = pygame.Rect(0, 0, 590, 80)
        tray.center = (W / 2, CHIP_TRAY_Y + 1)
        pygame.draw.rect(surf, (22, 13, 8), tray, border_radius=16)
        pygame.draw.rect(surf, (90, 60, 35), tray, width=2, border_radius=16)
        spinning = self.state == "spinning"
        for v, (x, y0) in self.chip_pos.items():
            hover = math.hypot(mouse[0] - x, mouse[1] - y0) <= 31 and not spinning
            y = y0 - (7 if hover else 0)
            pygame.draw.circle(surf, (0, 0, 0), (x + 2, y0 + 4), 31)
            img = self.assets.chip(v, 31)
            surf.blit(img, img.get_rect(center=(x, y)))
            if spinning or self.bet + v > self.app.balance:
                surf.blit(self.dim_chip, self.dim_chip.get_rect(center=(x, y)))
        self.btn_clear.draw(surf, mouse, not spinning and self.bet > 0)
        self.btn_spin.draw(surf, mouse, not spinning and 0 < self.bet <= self.app.balance)
        self.app.draw_top_bar(surf, "WHEEL", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Ride the Bus
# --------------------------------------------------------------------------
BUS_MULTS = [1.9, 2.6, 4.0, 20.0]        # cash-out value after each correct round
BUS_ROUNDS = ["RED OR BLACK?", "HIGHER OR LOWER?", "INSIDE OR OUTSIDE?", "PICK THE SUIT!"]
BUS_SHORT = ["RED / BLACK", "HIGH / LOW", "IN / OUT", "SUIT"]
BUS_CARD_Y = 200
BUS_DECK = (1112, 84)
SUIT_NAMES = {"H": "HEARTS", "D": "DIAMONDS", "S": "SPADES", "C": "CLUBS"}


def bus_x(i):
    return W / 2 + (i - 1.5) * 160 - CW / 2


def make_bus_bg():
    surf = make_wood()
    felt_rect = pygame.Rect(12, 66, W - 24, 556)
    pygame.draw.rect(surf, (28, 16, 11), felt_rect.inflate(26, 26).move(0, 5), border_radius=48)
    pygame.draw.rect(surf, (48, 28, 19), felt_rect.inflate(26, 26), border_radius=48)
    pygame.draw.rect(surf, (78, 48, 32), felt_rect.inflate(14, 14), width=3, border_radius=44)
    felt = pygame.Surface((W, H), pygame.SRCALPHA)
    dark, light = (10, 40, 70), (28, 90, 130)
    felt.fill((*dark, 255))
    for i in range(48):
        t = i / 47
        rw, rh = 1900 * (1 - t) + 160, 1100 * (1 - t) + 100
        pygame.draw.ellipse(felt, lerp_col(dark, light, t ** 0.8), (W / 2 - rw / 2, 300 - rh / 2, rw, rh))
    rng = random.Random(4)
    for _ in range(20000):
        x, y = rng.randrange(W), rng.randrange(H)
        r, g, b, a = felt.get_at((x, y))
        d = rng.choice((-6, -3, 3, 5))
        felt.set_at((x, y), (max(0, r + d), max(0, g + d), max(0, min(255, b + d)), a))
    mask = pygame.Surface((W, H), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), felt_rect, border_radius=36)
    felt.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(felt, (0, 0))
    draw_arc_text(surf, "RIDE THE BUS", font(30, bold=True, serif=True), GOLD, (W / 2, -700), 790)
    draw_arc_text(surf, "GET ALL FOUR RIGHT FOR 20x YOUR BET", font(14, bold=True), (190, 215, 235), (W / 2, -700), 826)
    for i in range(4):
        pygame.draw.rect(surf, (40, 110, 150), (bus_x(i), BUS_CARD_Y, CW, CH), width=2, border_radius=8)
    return surf


class RideTheBus:
    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.bg = make_bus_bg()
        self.bet_chips = []
        self.last_bet = []
        self.stake = 0
        self.state = "betting"          # betting, playing, revealing, result
        self.round = 0
        self.results = [None] * 4
        self.cards = []
        self.leaving = []
        self.queue = []
        self.t = 0.0
        self.message = "PLACE YOUR BET, THEN RIDE THE BUS"
        x0 = W / 2 - 3.5 * 70
        self.chip_pos = CHIP_WIN
        self.dim_chip = pygame.Surface((64, 64), pygame.SRCALPHA)
        pygame.draw.circle(self.dim_chip, (0, 0, 0, 150), (32, 32), 31)
        self.btn_clear = Button((40, 650, 135, 54), "CLEAR", (120, 35, 40))
        self.btn_rebet = Button((190, 650, 135, 54), "REBET", (40, 70, 130))
        self.btn_start = Button((965, 646, 270, 62), "RIDE THE BUS", (25, 120, 60), 26, "SPACE")
        self.btn_cash = Button((965, 646, 270, 62), "CASH OUT", (215, 120, 15), 24, "C")
        two = [pygame.Rect(W / 2 - 250, 648, 240, 58), pygame.Rect(W / 2 + 10, 648, 240, 58)]
        four = [pygame.Rect(250 + i * 172, 648, 160, 58) for i in range(4)]
        self.choices = [
            [("red", "RED", (175, 30, 40), "R", two[0]), ("black", "BLACK", (30, 30, 36), "B", two[1])],
            [("higher", "HIGHER", (25, 120, 60), "H", two[0]), ("lower", "LOWER", (150, 35, 40), "L", two[1])],
            [("inside", "INSIDE", (40, 90, 160), "I", two[0]), ("outside", "OUTSIDE", (120, 60, 150), "O", two[1])],
            [(su, SUIT_NAMES[su], (175, 30, 40) if su in "HD" else (30, 30, 36), str(k + 1), four[k])
             for k, su in enumerate("HDSC")],
        ]
        self.buttons = [[Button(r, label, col, 22 if len(label) < 8 else 19, key) for _, label, col, key, r in row]
                        for row in self.choices]

    # ---- helpers ---------------------------------------------------------
    @property
    def bet(self):
        return sum(self.bet_chips)

    def schedule(self, delay, fn):
        self.queue.append([delay, fn])

    def can_leave(self):
        return self.state in ("betting", "result") and not self.queue

    def outstanding_bets(self):
        return self.bet + (self.stake if self.state in ("playing", "revealing") else 0)

    def leave(self):
        self.app.balance += self.bet
        self.bet_chips = []
        self.clear_table(instant=True)

    def clear_table(self, instant=False):
        if not instant:
            for c in self.cards:
                c.tx, c.ty = BUS_DECK
                self.leaving.append(c)
        self.cards = []
        self.results = [None] * 4
        self.round = 0
        self.state = "betting"
        self.message = "PLACE YOUR BET, THEN RIDE THE BUS"

    def cash_value(self):
        return int(self.stake * BUS_MULTS[self.round - 1]) if self.round else 0

    # ---- betting ---------------------------------------------------------
    def add_chip(self, v):
        if self.state == "result":
            self.clear_table()
        if self.state != "betting":
            return
        if self.app.balance < v:
            self.message = "NOT ENOUGH CHIPS"
            return
        self.app.balance -= v
        self.bet_chips.append(v)
        self.message = ""
        self.app.sfx("chip")

    def all_in(self):
        """ALL IN: every chip you have goes on the table."""
        for v in chip_breakdown(self.app.balance):
            before = self.app.balance
            self.add_chip(v)
            if self.app.balance == before:
                break
        if self.bet:
            self.message = f"ALL IN!  BET {money(self.bet)}"

    def clear_bet(self):
        if self.state == "result":
            self.clear_table()
        self.app.balance += self.bet
        self.bet_chips = []

    def rebet(self):
        self.clear_bet()
        if self.app.balance < sum(self.last_bet):
            self.message = "NOT ENOUGH CHIPS TO REBET"
            return
        for v in self.last_bet:
            self.add_chip(v)

    def start(self):
        if self.state == "result":
            self.clear_table()
            if not self.bet_chips and self.last_bet:
                self.rebet()
        if self.state != "betting":
            return
        if not self.bet:
            self.message = "PLACE A BET FIRST"
            return
        self.last_bet = list(self.bet_chips)
        self.stake = self.bet
        self.bet_chips = []
        deck = [(r, s) for s in SUITS for r in RANKS]
        random.shuffle(deck)
        self.cards = []
        for i in range(4):
            r, s = deck.pop()
            sp = CardSprite(r, s, BUS_DECK, face_up=False)
            self.cards.append(sp)
            self.schedule(0.12 if i else 0.0, lambda sp=sp, i=i: self.deal(sp, i))
        self.round = 0
        self.results = [None] * 4
        self.state = "revealing"
        self.message = ""
        self.schedule(0.4, self.ready)

    def deal(self, sp, i):
        sp.tx, sp.ty = bus_x(i), BUS_CARD_Y
        self.app.sfx("card")

    def ready(self):
        self.state = "playing"

    # ---- playing ---------------------------------------------------------
    def guess(self, choice):
        if self.state != "playing":
            return
        self.cards[self.round].reveal()
        self.app.sfx("card")
        self.state = "revealing"
        self.schedule(0.5, lambda: self.judge(choice))

    def judge(self, choice):
        r = self.round
        c = self.cards[r]
        v = RANK_NUM[c.rank]
        if r == 0:
            ok = (c.suit in "HD") == (choice == "red")
        elif r == 1:
            first = RANK_NUM[self.cards[0].rank]
            ok = v != first and (v > first) == (choice == "higher")
        elif r == 2:
            lo, hi = sorted((RANK_NUM[self.cards[0].rank], RANK_NUM[self.cards[1].rank]))
            ok = v not in (lo, hi) and (lo < v < hi) == (choice == "inside")
        else:
            ok = c.suit == choice
        self.results[r] = ok
        if not ok:
            self.state = "result"
            why = " (SAME RANK - TIES LOSE)" if (r == 1 and v == RANK_NUM[self.cards[0].rank]) or \
                (r == 2 and v in (RANK_NUM[self.cards[0].rank], RANK_NUM[self.cards[1].rank])) else ""
            self.message = f"WRONG{why}!  THE BUS LEFT WITHOUT YOU  -  YOU LOSE {money(self.stake)}"
            self.app.float_text(f"-{money(self.stake)}", (240, 90, 90))
            self.app.sfx("lose")
            self.app.record("bus", self.stake, 0)
            self.reveal_rest()
            self.after_round()
            return
        self.round += 1
        self.app.sfx("chip")
        if self.round == 4:
            self.cash_out(auto=True)
        else:
            self.state = "playing"
            self.message = f"CORRECT!  CASH OUT {money(self.cash_value())} OR KEEP GOING FOR " \
                           f"{money(int(self.stake * BUS_MULTS[self.round]))}"

    def cash_out(self, auto=False):
        if self.round == 0 or self.state not in ("playing", "revealing"):
            return
        win = self.cash_value()
        self.app.balance += win
        self.app.record("bus", self.stake, win)
        if self.round == 4:
            self.app.unlock("rode_bus")
        self.state = "result"
        self.app.float_text(f"+{money(win)}", (80, 230, 110))
        self.app.sfx("win")
        if auto:
            self.message = f"ALL FOUR RIGHT!  YOU RODE THE BUS FOR {money(win)}!"
        else:
            self.message = f"CASHED OUT AFTER ROUND {self.round}  -  YOU WIN {money(win)}"
            self.reveal_rest()
        self.after_round()

    def reveal_rest(self):
        for k, c in enumerate(self.cards):
            if not c.face_up:
                self.schedule(0.25, lambda c=c: (c.reveal(), self.app.sfx("card")))

    def after_round(self):
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        self.app.save()

    # ---- events ----------------------------------------------------------
    def handle(self, e):
        if self.queue:
            return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.state in ("betting", "result"):
                for v, (cx, cy) in self.chip_pos.items():
                    if math.hypot(e.pos[0] - cx, e.pos[1] - cy) <= 31:
                        self.add_chip(v)
                        return
                if self.btn_clear.clicked(e.pos):
                    self.clear_bet()
                elif self.btn_rebet.clicked(e.pos, bool(self.last_bet)):
                    self.rebet()
                elif self.btn_start.clicked(e.pos):
                    self.start()
            elif self.state == "playing":
                for b, (choice, *_rest) in zip(self.buttons[self.round], self.choices[self.round]):
                    if b.clicked(e.pos):
                        self.guess(choice)
                        return
                if self.btn_cash.clicked(e.pos, self.round > 0):
                    self.cash_out()
        elif e.type == pygame.KEYDOWN:
            if self.state in ("betting", "result") and e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.start()
            elif self.state == "playing":
                if e.key == pygame.K_c and self.round > 0:
                    self.cash_out()
                    return
                for choice, _, _, key, _ in self.choices[self.round]:
                    if e.unicode and e.unicode.lower() == key.lower():
                        self.guess(choice)

    def update(self, dt):
        self.t += dt
        if self.queue:
            self.queue[0][0] -= dt
            if self.queue[0][0] <= 0:
                _, fn = self.queue.pop(0)
                fn()
        for c in self.cards + self.leaving:
            c.update(dt)
        self.leaving = [c for c in self.leaving if not c.arrived()]

    # ---- drawing ---------------------------------------------------------
    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        for i in range(8):                                   # the deck
            surf.blit(self.assets.back, (BUS_DECK[0], BUS_DECK[1] - i * 1.5))
        playing = self.state in ("playing", "revealing")
        # round labels + multipliers under each card
        for i in range(4):
            x = bus_x(i) + CW / 2
            res = self.results[i]
            current = playing and i == self.round and res is None
            if current:
                glow = 3 + int(2 * math.sin(self.t * 6))
                pygame.draw.rect(surf, GOLD, (bus_x(i) - glow - 2, BUS_CARD_Y - glow - 2, CW + 2 * glow + 4,
                                              CH + 2 * glow + 4), width=3, border_radius=10)
            col = (80, 230, 110) if res else (240, 90, 90) if res is False else GOLD if current else (170, 190, 210)
            draw_text(surf, f"ROUND {i + 1}", font(13, bold=True), col, (x, BUS_CARD_Y + CH + 22))
            draw_text(surf, BUS_SHORT[i], font(15, bold=True), WHITE if current else (200, 210, 225),
                      (x, BUS_CARD_Y + CH + 42))
            draw_pill(surf, f"x{BUS_MULTS[i]:g}", font(16, bold=True), (x, BUS_CARD_Y + CH + 70),
                      (20, 20, 20) if res or current else WHITE,
                      (80, 230, 110, 255) if res else (*GOLD, 255) if current else (0, 0, 0, 150), None, pad=(12, 3))
        for c in self.leaving + self.cards:
            c.draw(surf, self.assets)
        for i, res in enumerate(self.results):
            if res is not None and i < len(self.cards):
                cx, cy = bus_x(i) + CW / 2, BUS_CARD_Y - 16
                pygame.draw.circle(surf, (30, 140, 60) if res else (170, 30, 35), (cx, cy), 14)
                pygame.draw.circle(surf, WHITE, (cx, cy), 14, width=2)
                if res:
                    pygame.draw.lines(surf, WHITE, False, [(cx - 6, cy), (cx - 1, cy + 5), (cx + 7, cy - 5)], 3)
                else:
                    pygame.draw.line(surf, WHITE, (cx - 5, cy - 5), (cx + 5, cy + 5), 3)
                    pygame.draw.line(surf, WHITE, (cx + 5, cy - 5), (cx - 5, cy + 5), 3)

        # headline, money and message
        if playing:
            draw_text(surf, f"ROUND {self.round + 1}:  {BUS_ROUNDS[min(self.round, 3)]}", font(30, bold=True, serif=True),
                      GOLD, (W / 2, 150), shadow=(0, 0, 0))
        stake = self.stake if self.state != "betting" else self.bet
        info = f"BET  {money(stake)}"
        if playing and self.round:
            info += f"     CASH OUT NOW:  {money(self.cash_value())}"
        if playing and self.round < 4:
            info += f"     NEXT PRIZE:  {money(int(stake * BUS_MULTS[self.round]))}"
        draw_text(surf, info, font(20, bold=True), WHITE, (W / 2, 470))
        if self.message:
            draw_pill(surf, self.message, font(18, bold=True), (W / 2, 520), GOLD, (0, 0, 0, 190), GOLD_DARK)
        if self.state == "betting":
            if self.bet:
                for k, v in enumerate(chip_breakdown(self.bet)[:14]):
                    pygame.draw.circle(surf, darken(CHIP_STYLE[v][0], 80), (W / 2, 578 - k * 5), 24)
                    img = self.assets.chip(v, 24)
                    surf.blit(img, img.get_rect(center=(W / 2, 575 - k * 5)))

        # bottom bar
        pygame.draw.rect(surf, (8, 14, 22), (0, 636, W, 84))
        pygame.draw.line(surf, GOLD_DARK, (0, 636), (W, 636), 2)
        if self.state in ("betting", "result"):
            tray = pygame.Rect(0, 0, 590, 80)
            tray.center = (W / 2, CHIP_TRAY_Y + 1)
            pygame.draw.rect(surf, (4, 10, 18), tray, border_radius=16)
            pygame.draw.rect(surf, (50, 80, 110), tray, width=2, border_radius=16)
            for v, (cx, cy) in self.chip_pos.items():
                hover = math.hypot(mouse[0] - cx, mouse[1] - cy) <= 31 and not self.queue
                y = cy - (7 if hover else 0)
                pygame.draw.circle(surf, (0, 0, 0), (cx + 2, cy + 4), 31)
                img = self.assets.chip(v, 31)
                surf.blit(img, img.get_rect(center=(cx, y)))
                if v > self.app.balance:
                    surf.blit(self.dim_chip, self.dim_chip.get_rect(center=(cx, y)))
            self.btn_clear.draw(surf, mouse, self.bet > 0)
            self.btn_rebet.draw(surf, mouse, bool(self.last_bet))
            self.btn_start.text = "RIDE AGAIN" if self.state == "result" and not self.bet else "RIDE THE BUS"
            self.btn_start.draw(surf, mouse, not self.queue and (self.bet > 0 or
                                                                  (self.state == "result" and bool(self.last_bet))))
        else:
            rnd = min(self.round, 3)
            on = self.state == "playing" and not self.queue
            for b, (choice, _, _, key, r) in zip(self.buttons[rnd], self.choices[rnd]):
                b.hint = key
                b.draw(surf, mouse, on)
                if rnd == 3:
                    draw_suit(surf, choice, r.x + 22, r.centery - 6, 20, WHITE if on else (150, 150, 150))
            self.btn_cash.text = f"CASH OUT {money(self.cash_value())}" if self.round else "CASH OUT"
            self.btn_cash.draw(surf, mouse, on and self.round > 0)
            draw_text(surf, "Ace is high  -  a tie counts as a loss", font(14, bold=True), (170, 195, 220), (W / 2, 580))

        self.app.draw_top_bar(surf, "RIDE THE BUS", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Craps
# --------------------------------------------------------------------------
CRAPS_POINTS = [4, 5, 6, 8, 9, 10]
ODDS_PAY = {4: (2, 1), 10: (2, 1), 5: (3, 2), 9: (3, 2), 6: (6, 5), 8: (6, 5)}     # true odds
PIP_SPOTS = {1: [(0, 0)], 2: [(-1, -1), (1, 1)], 3: [(-1, -1), (0, 0), (1, 1)],
             4: [(-1, -1), (1, -1), (-1, 1), (1, 1)], 5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
             6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)]}
DICE_REST = [(590, 104), (668, 112)]
_die_cache = {}


def make_die(value, size):
    key = (value, size, CURRENT_THEME)
    if key not in _die_cache:
        edge, face, shine, pip = DIE_THEME.get(CURRENT_THEME, ((130, 10, 20), (215, 30, 40), (235, 80, 85),
                                                                (250, 250, 250)))
        k = 3
        S = size * k
        s = pygame.Surface((S, S), pygame.SRCALPHA)
        pygame.draw.rect(s, edge, (0, 0, S, S), border_radius=int(S * 0.2))
        pygame.draw.rect(s, face, (k, k, S - 2 * k, S - 2 * k), border_radius=int(S * 0.18))
        pygame.draw.rect(s, shine, (S * 0.12, S * 0.08, S * 0.5, S * 0.18), border_radius=int(S * 0.08))
        for px, py in PIP_SPOTS[value]:
            c = (S / 2 + px * S * 0.27, S / 2 + py * S * 0.27)
            if CURRENT_THEME == "valentines":                    # little heart pips
                draw_heart(s, c[0], c[1], S * 0.085, pip)
                continue
            pygame.draw.circle(s, darken(edge, 20), (c[0] + k * 0.6, c[1] + k * 0.6), S * 0.085)
            pygame.draw.circle(s, pip, c, S * 0.085)
        _die_cache[key] = pygame.transform.smoothscale(s, (size, size))
    return _die_cache[key]


def craps_regions():
    r = {}
    for i, n in enumerate(CRAPS_POINTS):
        r[f"box{n}"] = pygame.Rect(200 + i * 126, 160, 118, 84)
    r["place6"], r["place8"] = r["box6"], r["box8"]
    r["field"] = pygame.Rect(200, 254, 748, 66)
    r["dp"] = pygame.Rect(200, 330, 748, 50)
    r["pass"] = pygame.Rect(200, 390, 600, 80)
    r["odds"] = pygame.Rect(812, 390, 136, 80)
    r["any7"] = pygame.Rect(980, 190, 268, 80)
    r["anycraps"] = pygame.Rect(980, 280, 268, 80)
    r["yo"] = pygame.Rect(980, 370, 268, 100)
    return r


def make_craps_bg(regions):
    surf = make_wood()
    felt_rect = pygame.Rect(12, 60, W - 24, 566)
    pygame.draw.rect(surf, (28, 16, 11), felt_rect.inflate(26, 26).move(0, 5), border_radius=40)
    pygame.draw.rect(surf, (48, 28, 19), felt_rect.inflate(26, 26), border_radius=40)
    felt = pygame.Surface((W, H), pygame.SRCALPHA)
    dark, light = (8, 62, 36), (24, 122, 68)
    felt.fill((*dark, 255))
    for i in range(48):
        t = i / 47
        rw, rh = 1900 * (1 - t) + 160, 1100 * (1 - t) + 100
        pygame.draw.ellipse(felt, lerp_col(dark, light, t ** 0.8), (W / 2 - rw / 2, 330 - rh / 2, rw, rh))
    mask = pygame.Surface((W, H), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), felt_rect, border_radius=30)
    felt.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(felt, (0, 0))
    # back wall of the dice area (the bumpy rubber)
    for x in range(20, W - 20, 14):
        for yy in (66, 76):
            pygame.draw.polygon(surf, (20, 70, 40), [(x, yy), (x + 7, yy + 8), (x + 14, yy)])
    line = (235, 235, 225)
    serif = lambda n: font(n, bold=True, serif=True)
    names = {4: "4", 5: "5", 6: "SIX", 8: "8", 9: "NINE", 10: "10"}
    for n in CRAPS_POINTS:
        b = regions[f"box{n}"]
        pygame.draw.rect(surf, line, b, width=2, border_radius=8)
        draw_text(surf, names[n], serif(30), (240, 220, 150) if n in (6, 8) else WHITE, (b.centerx, b.y + 30))
        draw_text(surf, "PLACE 7:6" if n in (6, 8) else f"ODDS {ODDS_PAY[n][0]}:{ODDS_PAY[n][1]}", font(11, bold=True),
                  (190, 215, 195), (b.centerx, b.bottom - 14))
    f = regions["field"]
    pygame.draw.rect(surf, line, f, width=2, border_radius=10)
    draw_text(surf, "FIELD", serif(26), (240, 220, 150), (f.x + 70, f.centery))
    draw_text(surf, "2   3   4   9   10   11   12", serif(22), WHITE, (f.centerx + 40, f.y + 24))
    draw_text(surf, "2 PAYS DOUBLE   -   12 PAYS TRIPLE   -   OTHERS PAY 1:1", font(12, bold=True), (190, 215, 195),
              (f.centerx + 40, f.bottom - 13))
    d = regions["dp"]
    pygame.draw.rect(surf, line, d, width=2, border_radius=10)
    draw_text(surf, "DON'T PASS BAR", serif(22), WHITE, (d.centerx - 60, d.centery))
    draw_text(surf, "12 PUSHES", font(12, bold=True), (190, 215, 195), (d.right - 90, d.centery))
    p = regions["pass"]
    pygame.draw.rect(surf, line, p, width=3, border_radius=12)
    draw_text(surf, "PASS LINE", serif(34), WHITE, (p.centerx, p.centery - 6))
    draw_text(surf, "PAYS 1:1", font(12, bold=True), (190, 215, 195), (p.centerx, p.bottom - 13))
    o = regions["odds"]
    pygame.draw.rect(surf, line, o, width=2, border_radius=12)
    draw_text(surf, "ODDS", serif(22), (240, 220, 150), (o.centerx, o.y + 24))
    draw_text(surf, "TRUE ODDS", font(11, bold=True), (190, 215, 195), (o.centerx, o.bottom - 14))
    props = pygame.Rect(968, 160, 292, 318)
    pygame.draw.rect(surf, (10, 50, 30), props, border_radius=12)
    pygame.draw.rect(surf, (200, 170, 90), props, width=2, border_radius=12)
    draw_text(surf, "ONE ROLL BETS", font(14, bold=True), GOLD, (props.centerx, props.y + 15))
    for key, title, sub in (("any7", "ANY SEVEN", "PAYS 4 TO 1"), ("anycraps", "ANY CRAPS  (2, 3, 12)", "PAYS 7 TO 1"),
                            ("yo", "YO  ELEVEN", "PAYS 15 TO 1")):
        b = regions[key]
        pygame.draw.rect(surf, (170, 30, 40) if key == "any7" else (20, 70, 45), b, border_radius=10)
        pygame.draw.rect(surf, line, b, width=2, border_radius=10)
        draw_text(surf, title, font(19, bold=True), WHITE, (b.centerx, b.y + 24))
        draw_text(surf, sub, font(12, bold=True), (230, 220, 190), (b.centerx, b.y + 46))
    side = pygame.Rect(22, 160, 164, 318)
    pygame.draw.rect(surf, (6, 40, 24), side, border_radius=12)
    pygame.draw.rect(surf, (60, 110, 80), side, width=2, border_radius=12)
    draw_text(surf, "LAST ROLLS", font(13, bold=True), GOLD, (side.centerx, side.y + 16))
    return surf


class Craps:
    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.regions = craps_regions()
        self.bg = make_craps_bg(self.regions)
        self.bets = {}
        self.history = []
        self.last_bets = {}
        self.point = None
        self.state = "betting"
        self.selected = 10
        self.rolls = []
        self.dice = [3, 4]
        self.show = [3, 4]
        self.anim = None
        self.flash = {}
        self.call = ""
        self.call_t = 0.0
        self.t = 0.0
        self.message = "PLACE YOUR BETS, THEN ROLL THE DICE"
        x0 = W / 2 - 3.5 * 70
        self.chip_pos = CHIP_WIN
        self.dim_chip = pygame.Surface((64, 64), pygame.SRCALPHA)
        pygame.draw.circle(self.dim_chip, (0, 0, 0, 150), (32, 32), 31)
        self.btn_clear = Button((22, 652, 100, 52), "CLEAR", (120, 35, 40), 18)
        self.btn_undo = Button((130, 652, 100, 52), "UNDO", (80, 80, 90), 18)
        self.btn_rebet = Button((238, 652, 100, 52), "REBET", (40, 70, 130), 18)
        self.btn_roll = Button((965, 646, 270, 62), "ROLL DICE", (25, 120, 60), 26, "SPACE")

    # ---- helpers ---------------------------------------------------------
    @property
    def total_bet(self):
        return sum(self.bets.values())

    def can_leave(self):
        return self.state != "rolling" and not self.locked_total()

    def locked_total(self):
        return sum(self.bets.get(k, 0) for k in ("pass", "dp")) if self.point else 0

    def outstanding_bets(self):
        return self.total_bet

    def leave(self):
        self.app.balance += self.total_bet
        self.bets, self.history = {}, []

    def removable(self, key):
        return not (self.point and key in ("pass", "dp"))

    def region_at(self, pos):
        for key in ("place6", "place8", "field", "dp", "pass", "odds", "any7", "anycraps", "yo"):
            if self.regions[key].collidepoint(pos):
                return key
        return None

    def why_not(self, key):
        if key in ("pass", "dp") and self.point:
            return "PASS / DON'T PASS BETS CAN ONLY BE MADE BEFORE A POINT IS SET"
        if key == "odds":
            if not self.point:
                return "ODDS CAN ONLY BE ADDED AFTER A POINT IS SET"
            if not self.bets.get("pass"):
                return "YOU NEED A PASS LINE BET TO TAKE ODDS"
        return None

    # ---- betting ---------------------------------------------------------
    def place(self, key, v):
        reason = self.why_not(key)
        if reason:
            self.message = reason
            return
        if key == "odds":
            room = 3 * self.bets["pass"] - self.bets.get("odds", 0)
            if room <= 0:
                self.message = "ODDS ARE LIMITED TO 3x YOUR PASS LINE BET"
                return
            v = min(v, room)
        if self.app.balance < v:
            self.message = "NOT ENOUGH CHIPS"
            return
        self.app.balance -= v
        self.bets[key] = self.bets.get(key, 0) + v
        self.history.append((key, v))
        self.message = ""
        self.app.sfx("chip")

    def remove(self, key):
        if key in self.bets and self.removable(key):
            self.app.balance += self.bets.pop(key)
            self.history = [h for h in self.history if h[0] != key]
            self.app.sfx("chip")

    def clear(self):
        for key in list(self.bets):
            if self.removable(key):
                self.app.balance += self.bets.pop(key)
        self.history = [h for h in self.history if h[0] in self.bets]

    def undo(self):
        while self.history:
            key, v = self.history.pop()
            if key in self.bets and self.removable(key):
                self.bets[key] -= v
                if self.bets[key] <= 0:
                    del self.bets[key]
                self.app.balance += v
                self.app.sfx("chip")
                return

    def rebet(self):
        for key, amt in self.last_bets.items():
            if key in self.bets or self.why_not(key):
                continue
            if self.app.balance < amt:
                self.message = "NOT ENOUGH CHIPS TO REBET"
                return
            self.place(key, amt)

    # ---- rolling ---------------------------------------------------------
    def roll(self):
        if self.state == "rolling":
            return
        if not self.bets:
            self.message = "PLACE A BET FIRST"
            return
        self.last_bets = {k: v for k, v in self.bets.items() if k != "odds"}
        self.dice = [random.randint(1, 6), random.randint(1, 6)]
        self.anim = {"t": 0.0, "dur": 1.25, "starts": [(1190, random.uniform(90, 130)), (1215, random.uniform(90, 130))],
                     "spins": [random.uniform(540, 900), random.uniform(-900, -540)], "next_face": 0.0}
        self.state = "rolling"
        self.message = ""
        self.flash = {}
        self.app.sfx("launch")

    def pay(self, key, profit, keep=True):
        """Winning bet: pay the profit; the bet stays up unless keep=False (then the stake comes back too)."""
        amt = self.bets[key]
        self.app.balance += profit + (0 if keep else amt)
        if not keep:
            del self.bets[key]
        self.flash[key] = (f"WIN +{money(profit)}", (40, 170, 80), 2.6)
        return profit

    def lose(self, key):
        amt = self.bets.pop(key)
        self.flash[key] = (f"LOSE {money(amt)}", (180, 40, 45), 2.6)
        return amt

    def resolve(self):
        d1, d2 = self.dice
        total = d1 + d2
        self.rolls = ([(d1, d2)] + self.rolls)[:12]
        won = lost = 0
        b = self.bets
        # one-roll bets
        if "field" in b:
            mult = 2 if total == 2 else 3 if total == 12 else 1 if total in (3, 4, 9, 10, 11) else 0
            won, lost = (won + self.pay("field", b["field"] * mult), lost) if mult else (won, lost + self.lose("field"))
        if "any7" in b:
            won, lost = (won + self.pay("any7", b["any7"] * 4), lost) if total == 7 else (won, lost + self.lose("any7"))
        if "anycraps" in b:
            hit = total in (2, 3, 12)
            won, lost = (won + self.pay("anycraps", b["anycraps"] * 7), lost) if hit else (won, lost + self.lose("anycraps"))
        if "yo" in b:
            won, lost = (won + self.pay("yo", b["yo"] * 15), lost) if total == 11 else (won, lost + self.lose("yo"))
        # place bets only work once a point is on
        if self.point:
            for n in (6, 8):
                key = f"place{n}"
                if key in b:
                    if total == n:
                        won += self.pay(key, b[key] * 7 // 6)
                    elif total == 7:
                        lost += self.lose(key)
        # line bets
        call = f"{total}"
        if self.point is None:
            if total in (7, 11):
                call = f"{total} - FRONT LINE WINNER!"
                if "pass" in b:
                    won += self.pay("pass", b["pass"])
                if "dp" in b:
                    lost += self.lose("dp")
            elif total in (2, 3, 12):
                call = f"{total} - CRAPS!"
                if "pass" in b:
                    lost += self.lose("pass")
                if "dp" in b:
                    if total == 12:
                        self.flash["dp"] = ("PUSH", (90, 90, 100), 2.6)
                    else:
                        won += self.pay("dp", b["dp"])
            else:
                self.point = total
                call = f"THE POINT IS {total}"
        else:
            if total == self.point:
                call = f"{total} - WINNER! THE POINT IS MADE"
                if "pass" in b:
                    self.app.unlock("point_maker")
                if "pass" in b:
                    won += self.pay("pass", b["pass"])
                if "odds" in b:
                    num, den = ODDS_PAY[self.point]
                    won += self.pay("odds", b["odds"] * num // den, keep=False)
                if "dp" in b:
                    lost += self.lose("dp")
                self.point = None
            elif total == 7:
                call = "7 - SEVEN OUT!"
                for key in ("pass", "odds"):
                    if key in b:
                        lost += self.lose(key)
                if "dp" in b:
                    won += self.pay("dp", b["dp"])
                self.point = None
        self.call, self.call_t = call, 3.0
        if won or lost:
            self.app.record("craps", lost, won)
        self.history = [h for h in self.history if h[0] in self.bets]
        net = won - lost
        if won and net >= 0:
            self.message = f"{call}   -   YOU WIN {money(won)}" + (f" (LOST {money(lost)})" if lost else "")
            self.app.float_text(f"+{money(won)}", (80, 230, 110))
            self.app.sfx("win")
        elif lost:
            self.message = f"{call}   -   YOU LOSE {money(lost)}" + (f" (WON {money(won)})" if won else "")
            self.app.float_text(f"-{money(lost)}", (240, 90, 90))
            self.app.sfx("lose")
        elif self.point and call == str(total):
            self.message = f"{total}   -   NO DECISION, THE POINT IS STILL {self.point}"
        else:
            self.message = call
        if self.app.balance < min(CHIP_VALUES) and not self.bets:
            self.message = self.app.broke_message()
        self.state = "betting"
        self.app.save()

    # ---- events ----------------------------------------------------------
    def handle(self, e):
        if self.state == "rolling":
            return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for v, (cx, cy) in self.chip_pos.items():
                if math.hypot(e.pos[0] - cx, e.pos[1] - cy) <= 31:
                    self.selected = v
                    self.app.sfx("chip")
                    return
            if self.btn_clear.clicked(e.pos):
                self.clear()
            elif self.btn_undo.clicked(e.pos):
                self.undo()
            elif self.btn_rebet.clicked(e.pos, bool(self.last_bets)):
                self.rebet()
            elif self.btn_roll.clicked(e.pos):
                self.roll()
            else:
                key = self.region_at(e.pos)
                if key:
                    self.place(key, self.selected)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            key = self.region_at(e.pos)
            if key:
                if not self.removable(key) and key in self.bets:
                    self.message = "LINE BETS STAY UP UNTIL THE POINT IS DECIDED"
                self.remove(key)
        elif e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.roll()
            elif e.key == pygame.K_BACKSPACE:
                self.undo()

    def update(self, dt):
        self.t += dt
        self.call_t = max(0.0, self.call_t - dt)
        for k in list(self.flash):
            text, col, life = self.flash[k]
            if life - dt <= 0:
                del self.flash[k]
            else:
                self.flash[k] = (text, col, life - dt)
        if self.anim:
            a = self.anim
            a["t"] += dt
            a["next_face"] -= dt
            p = a["t"] / a["dur"]
            if p < 0.8 and a["next_face"] <= 0:
                self.show = [random.randint(1, 6), random.randint(1, 6)]
                a["next_face"] = 0.07
                if random.random() < 0.3:
                    self.app.sfx("card")
            if p >= 0.8:
                self.show = list(self.dice)
            if p >= 1:
                self.anim = None
                self.app.sfx("chip")
                self.resolve()

    def dice_pose(self, i):
        if not self.anim:
            return DICE_REST[i], 18 * (1 if i else -1)
        a = self.anim
        p = min(1.0, a["t"] / a["dur"])
        e = 1 - (1 - p) ** 3
        sx, sy = a["starts"][i]
        ex, ey = DICE_REST[i]
        x = sx + (ex - sx) * e
        y = sy + (ey - sy) * e - abs(math.sin(p * math.pi * 3)) * (1 - p) * 40
        return (x, y), a["spins"][i] * (1 - e) + 18 * (1 if i else -1)

    # ---- drawing ---------------------------------------------------------
    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        # puck
        if self.point:
            b = self.regions[f"box{self.point}"]
            pygame.draw.circle(surf, (20, 20, 20), (b.centerx + 2, b.y + 2), 18)
            pygame.draw.circle(surf, WHITE, (b.centerx, b.y), 18)
            draw_text(surf, "ON", font(13, bold=True), (20, 20, 20), (b.centerx, b.y))
        else:
            c = (self.regions["dp"].right - 24, self.regions["dp"].centery)
            pygame.draw.circle(surf, (230, 230, 230), c, 18)
            pygame.draw.circle(surf, (20, 20, 20), c, 16)
            draw_text(surf, "OFF", font(11, bold=True), WHITE, c)
        # hover highlight
        if self.state != "rolling":
            key = self.region_at(mouse)
            if key:
                r = self.regions[key]
                hl = pygame.Surface(r.size, pygame.SRCALPHA)
                pygame.draw.rect(hl, (255, 255, 255, 40 if not self.why_not(key) else 12), hl.get_rect(),
                                 border_radius=10)
                surf.blit(hl, r)
        # bets on the layout
        for key, amt in self.bets.items():
            r = self.regions[key]
            pos = (r.centerx + (38 if key in ("place6", "place8") else 0), r.bottom - (24 if key != "pass" else 22))
            if key in ("any7", "anycraps", "yo"):
                pos = (r.right - 34, r.centery + 6)
            if key == "field":
                pos = (r.x + 150, r.centery)
            if key == "dp":
                pos = (r.x + 60, r.centery)
            if key == "pass":
                pos = (r.x + 90, r.centery + 2)
            if key in ("place6", "place8"):
                pos = (r.right - 22, r.y + 26)
            for k, v in enumerate(chip_breakdown(amt)[:8]):
                pygame.draw.circle(surf, darken(CHIP_STYLE[v][0], 80), (pos[0], pos[1] + 2 - k * 4), 16)
                img = self.assets.chip(v, 16)
                surf.blit(img, img.get_rect(center=(pos[0], pos[1] - k * 4)))
            draw_pill(surf, money(amt), font(11, bold=True), (pos[0], pos[1] + 22), WHITE, (0, 0, 0, 190), None,
                      pad=(6, 1))
        for key, (text, col, life) in self.flash.items():
            r = self.regions[key]
            draw_pill(surf, text, font(14, bold=True), (r.centerx, r.y + 14), WHITE, (*col, 240), GOLD, pad=(8, 2))
        # roll history
        for i, (a, b) in enumerate(self.rolls[:9]):
            y = 196 + i * 31
            for j, v in enumerate((a, b)):
                img = make_die(v, 22)
                surf.blit(img, (48 + j * 26, y - 11))
            draw_text(surf, str(a + b), font(16, bold=True), GOLD if i == 0 else (200, 220, 205), (126, y),
                      anchor="midleft")
        # dice
        for i in range(2):
            (x, y), ang = self.dice_pose(i)
            pygame.draw.ellipse(surf, (5, 40, 20), (x - 22, y + 18, 46, 12))
            img = pygame.transform.rotozoom(make_die(self.show[i], 44), ang, 1)
            surf.blit(img, img.get_rect(center=(x, y)))
        if self.call_t and not self.anim:
            draw_pill(surf, self.call, font(20, bold=True), (W / 2 - 180, 112), WHITE, (10, 10, 20, 220), GOLD,
                      pad=(14, 4))
        point_txt = f"POINT: {self.point}" if self.point else "COME-OUT ROLL"
        draw_text(surf, point_txt, font(16, bold=True), GOLD, (W / 2 + 330, 112))
        draw_text(surf, f"ON THE TABLE: {money(self.total_bet)}", font(16, bold=True), WHITE, (W / 2 - 60, 500))
        if self.message:
            draw_pill(surf, self.message, font(16, bold=True), (W / 2, 540), GOLD, (0, 0, 0, 200), GOLD_DARK,
                      pad=(14, 4))
        else:
            draw_text(surf, "Pick a chip, click the table to bet.  Right-click a bet to take it back.", font(14),
                      (190, 215, 195), (W / 2, 540))
        # chip tray + buttons
        pygame.draw.rect(surf, (10, 16, 12), (0, 636, W, 84))
        pygame.draw.line(surf, GOLD_DARK, (0, 636), (W, 636), 2)
        tray = pygame.Rect(0, 0, 590, 80)
        tray.center = (W / 2, CHIP_TRAY_Y + 1)
        pygame.draw.rect(surf, (6, 14, 8), tray, border_radius=16)
        pygame.draw.rect(surf, (60, 100, 70), tray, width=2, border_radius=16)
        rolling = self.state == "rolling"
        for v, (cx, cy) in self.chip_pos.items():
            sel = v == self.selected
            y = cy - (9 if sel else 0)
            pygame.draw.circle(surf, (0, 0, 0), (cx + 2, cy + 4), 31)
            if sel:
                pygame.draw.circle(surf, GOLD, (cx, y), 35, width=3)
            img = self.assets.chip(v, 31)
            surf.blit(img, img.get_rect(center=(cx, y)))
            if rolling or v > self.app.balance:
                surf.blit(self.dim_chip, self.dim_chip.get_rect(center=(cx, y)))
        self.btn_clear.draw(surf, mouse, not rolling and any(self.removable(k) for k in self.bets))
        self.btn_undo.draw(surf, mouse, not rolling and bool(self.history))
        self.btn_rebet.draw(surf, mouse, not rolling and bool(self.last_bets))
        self.btn_roll.draw(surf, mouse, not rolling and bool(self.bets))
        self.app.draw_top_bar(surf, "CRAPS", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Baccarat (Punto Banco)
# --------------------------------------------------------------------------
BAC_P_X, BAC_B_X, BAC_CARD_Y = 330, 950, 150


def bac_value(rank):
    return 1 if rank == "A" else 0 if rank in ("10", "J", "Q", "K") else int(rank)


def bac_total(cards):
    return sum(bac_value(c.rank) for c in cards) % 10


def bac_regions():
    widths = [("ppair", 150), ("player", 230), ("tie", 170), ("banker", 230), ("bpair", 150)]
    x = (W - (sum(w for _, w in widths) + 4 * 12)) / 2
    r = {}
    for key, w in widths:
        r[key] = pygame.Rect(x, 380, w, 110)
        x += w + 12
    return r


BAC_LABELS = {"ppair": ("PLAYER PAIR", "PAYS 11:1", (40, 90, 170)), "player": ("PLAYER", "PAYS 1:1", (30, 80, 170)),
              "tie": ("TIE", "PAYS 8:1", (30, 130, 60)), "banker": ("BANKER", "PAYS 0.95:1", (170, 35, 45)),
              "bpair": ("BANKER PAIR", "PAYS 11:1", (170, 50, 60))}


def make_baccarat_bg(regions):
    surf = make_wood()
    felt_rect = pygame.Rect(12, 60, W - 24, 566)
    pygame.draw.rect(surf, (28, 16, 11), felt_rect.inflate(26, 26).move(0, 5), border_radius=40)
    pygame.draw.rect(surf, (48, 28, 19), felt_rect.inflate(26, 26), border_radius=40)
    felt = pygame.Surface((W, H), pygame.SRCALPHA)
    dark, light = (60, 8, 20), (130, 25, 45)
    felt.fill((*dark, 255))
    for i in range(48):
        t = i / 47
        rw, rh = 1900 * (1 - t) + 160, 1100 * (1 - t) + 100
        pygame.draw.ellipse(felt, lerp_col(dark, light, t ** 0.8), (W / 2 - rw / 2, 300 - rh / 2, rw, rh))
    mask = pygame.Surface((W, H), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), felt_rect, border_radius=30)
    felt.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(felt, (0, 0))
    draw_text(surf, "PLAYER", font(30, bold=True, serif=True), (140, 180, 255), (BAC_P_X, 100), shadow=(0, 0, 0))
    draw_text(surf, "BANKER", font(30, bold=True, serif=True), (255, 150, 150), (BAC_B_X, 100), shadow=(0, 0, 0))
    for cx in (BAC_P_X, BAC_B_X):
        for i in range(3):
            pygame.draw.rect(surf, (150, 60, 80), (cx - 1.5 * CW - 10 + i * (CW + 10), BAC_CARD_Y, CW, CH),
                             width=2, border_radius=8)
    for key, r in regions.items():
        title, sub, col = BAC_LABELS[key]
        pygame.draw.rect(surf, darken(col, 40), r, border_radius=16)
        pygame.draw.rect(surf, (235, 215, 160), r, width=2, border_radius=16)
        draw_text(surf, title, font(22 if key in ("player", "banker", "tie") else 15, bold=True, serif=True), WHITE,
                  (r.centerx, r.y + 26))
        draw_text(surf, sub, font(12, bold=True), (235, 215, 170), (r.centerx, r.y + 50))
    draw_text(surf, "BANKER DRAWS BY THE HOUSE RULES   -   TIE PAYS 8 TO 1", font(13, bold=True), (230, 190, 190),
              (W / 2, 506))
    return surf


class Baccarat:
    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.regions = bac_regions()
        self.bg = make_baccarat_bg(self.regions)
        self.shoe = Shoe(8)
        self.player, self.banker = [], []
        self.leaving = []
        self.bets = {}
        self.history = []
        self.last_bets = {}
        self.won = {}
        self.results = []                  # "P", "B" or "T" for the scoreboard
        self.state = "betting"             # betting, dealing, result
        self.queue = []
        self.selected = 10
        self.winner = None
        self.message = "PLACE YOUR BETS, THEN DEAL"
        x0 = W / 2 - 3.5 * 70
        self.chip_pos = CHIP_WIN
        self.dim_chip = pygame.Surface((64, 64), pygame.SRCALPHA)
        pygame.draw.circle(self.dim_chip, (0, 0, 0, 150), (32, 32), 31)
        self.btn_clear = Button((22, 652, 100, 52), "CLEAR", (120, 35, 40), 18)
        self.btn_undo = Button((130, 652, 100, 52), "UNDO", (80, 80, 90), 18)
        self.btn_rebet = Button((238, 652, 100, 52), "REBET", (40, 70, 130), 18)
        self.btn_deal = Button((965, 646, 270, 62), "DEAL", (25, 120, 60), 28, "SPACE")

    @property
    def total_bet(self):
        return sum(self.bets.values())

    def schedule(self, d, fn):
        self.queue.append([d, fn])

    def can_leave(self):
        return self.state != "dealing"

    def outstanding_bets(self):
        return self.total_bet if self.state != "result" else 0

    def leave(self):
        if self.state == "betting":
            self.app.balance += self.total_bet
        self.new_round(instant=True)

    def new_round(self, instant=False):
        if not instant:
            for c in self.player + self.banker:
                c.tx, c.ty = (1150, 70)
                self.leaving.append(c)
        self.player, self.banker = [], []
        if self.state == "result" or instant:
            self.bets, self.history, self.won = {}, [], {}
        self.winner = None
        self.state = "betting"
        self.message = "PLACE YOUR BETS, THEN DEAL"

    def region_at(self, pos):
        for key, r in self.regions.items():
            if r.collidepoint(pos):
                return key
        return None

    def place(self, key, v):
        if self.state == "result":
            self.new_round()
        if self.app.balance < v:
            self.message = "NOT ENOUGH CHIPS"
            return
        self.app.balance -= v
        self.bets[key] = self.bets.get(key, 0) + v
        self.history.append((key, v))
        self.message = ""
        self.app.sfx("chip")

    def clear(self):
        if self.state == "result":
            self.new_round()
        self.app.balance += self.total_bet
        self.bets, self.history = {}, []

    def undo(self):
        if self.state == "betting" and self.history:
            key, v = self.history.pop()
            self.bets[key] -= v
            if self.bets[key] <= 0:
                del self.bets[key]
            self.app.balance += v
            self.app.sfx("chip")

    def rebet(self):
        self.clear()
        if sum(self.last_bets.values()) > self.app.balance:
            self.message = "NOT ENOUGH CHIPS TO REBET"
            return
        for key, amt in self.last_bets.items():
            self.place(key, amt)

    def deal(self):
        if self.state == "result":
            self.new_round()
            if not self.bets and self.last_bets:
                self.rebet()
        if self.state != "betting":
            return
        if not self.bets:
            self.message = "PLACE A BET FIRST"
            return
        self.last_bets = dict(self.bets)
        if self.shoe.needs_shuffle():
            self.shoe.shuffle()
        self.state = "dealing"
        self.message = ""
        self.schedule(0.2, lambda: self.draw_card(self.player))
        self.schedule(0.4, lambda: self.draw_card(self.banker))
        self.schedule(0.4, lambda: self.draw_card(self.player))
        self.schedule(0.4, lambda: self.draw_card(self.banker))
        self.schedule(0.7, self.third_cards)

    def draw_card(self, hand):
        r, s = self.shoe.draw()
        sp = CardSprite(r, s, (1150, 70))
        cx = BAC_P_X if hand is self.player else BAC_B_X
        sp.tx, sp.ty = cx - 1.5 * CW - 10 + len(hand) * (CW + 10), BAC_CARD_Y
        hand.append(sp)
        self.app.sfx("card")

    def third_cards(self):
        p, b = bac_total(self.player), bac_total(self.banker)
        if p >= 8 or b >= 8:                         # a "natural" - nobody draws
            self.message = "NATURAL!"
            self.schedule(0.6, self.settle)
            return
        player_third = None
        if p <= 5:
            self.draw_card(self.player)
            player_third = bac_value(self.player[2].rank)
        # banker's drawing rules
        if player_third is None:
            banker_draws = b <= 5
        else:
            banker_draws = (b <= 2 or (b == 3 and player_third != 8) or (b == 4 and 2 <= player_third <= 7) or
                            (b == 5 and 4 <= player_third <= 7) or (b == 6 and player_third in (6, 7)))
        if banker_draws:
            self.schedule(0.6, lambda: self.draw_card(self.banker))
        self.schedule(0.8, self.settle)

    def settle(self):
        p, b = bac_total(self.player), bac_total(self.banker)
        self.winner = "P" if p > b else "B" if b > p else "T"
        self.results = (self.results + [self.winner])[-66:]
        ppair = self.player[0].rank == self.player[1].rank
        bpair = self.banker[0].rank == self.banker[1].rank
        total_ret = 0
        self.won = {}
        for key, amt in self.bets.items():
            if key == "player":
                ret = amt * 2 if self.winner == "P" else amt if self.winner == "T" else 0
            elif key == "banker":
                ret = amt + amt * 95 // 100 if self.winner == "B" else amt if self.winner == "T" else 0
            elif key == "tie":
                ret = amt * 9 if self.winner == "T" else 0
            elif key == "ppair":
                ret = amt * 12 if ppair else 0
            else:
                ret = amt * 12 if bpair else 0
            self.won[key] = ret
            total_ret += ret
        self.app.balance += total_ret
        self.app.record("baccarat", self.total_bet, total_ret)
        st = self.app.stats["baccarat"]
        if total_ret > self.total_bet:
            st["streak"] = st.get("streak", 0) + 1
            st["best_streak"] = max(st.get("best_streak", 0), st["streak"])
            if st["streak"] >= 25:
                self.app.unlock("mikki_mase")
        elif total_ret < self.total_bet:
            st["streak"] = 0
        if self.winner == "T" and self.won.get("tie"):
            self.app.unlock("dead_heat")
        net = total_ret - self.total_bet
        who = {"P": f"PLAYER WINS {p} TO {b}", "B": f"BANKER WINS {b} TO {p}", "T": f"TIE AT {p}"}[self.winner]
        if net > 0:
            self.message = f"{who}   -   YOU WIN {money(net)}!"
            self.app.float_text(f"+{money(net)}", (80, 230, 110))
            self.app.sfx("win")
        elif net < 0:
            self.message = f"{who}   -   YOU LOSE {money(-net)}"
            self.app.float_text(f"-{money(-net)}", (240, 90, 90))
            self.app.sfx("lose")
        else:
            self.message = f"{who}   -   YOUR BET IS RETURNED"
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        self.state = "result"
        self.app.save()

    def handle(self, e):
        if self.queue:
            return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for v, (cx, cy) in self.chip_pos.items():
                if math.hypot(e.pos[0] - cx, e.pos[1] - cy) <= 31:
                    self.selected = v
                    self.app.sfx("chip")
                    return
            if self.btn_clear.clicked(e.pos):
                self.clear()
            elif self.btn_undo.clicked(e.pos):
                self.undo()
            elif self.btn_rebet.clicked(e.pos, bool(self.last_bets)):
                self.rebet()
            elif self.btn_deal.clicked(e.pos):
                self.deal()
            else:
                key = self.region_at(e.pos)
                if key:
                    self.place(key, self.selected)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3 and self.state == "betting":
            key = self.region_at(e.pos)
            if key in self.bets:
                self.app.balance += self.bets.pop(key)
                self.history = [h for h in self.history if h[0] != key]
                self.app.sfx("chip")
        elif e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.deal()
            elif e.key == pygame.K_BACKSPACE:
                self.undo()

    def update(self, dt):
        if self.queue:
            self.queue[0][0] -= dt
            if self.queue[0][0] <= 0:
                _, fn = self.queue.pop(0)
                fn()
        for c in self.player + self.banker + self.leaving:
            c.update(dt)
        self.leaving = [c for c in self.leaving if not c.arrived()]

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        # scoreboard (bead plate): 11 columns x 6 rows, filled top to bottom
        board = pygame.Rect(W / 2 - 132, 96, 264, 150)
        pygame.draw.rect(surf, (245, 240, 230), board, border_radius=8)
        pygame.draw.rect(surf, GOLD_DARK, board, width=2, border_radius=8)
        for i, r in enumerate(self.results[-66:]):
            col, row = divmod(i, 6)
            c = (board.x + 16 + col * 23.2, board.y + 14 + row * 24)
            color = {"P": (40, 90, 200), "B": (200, 40, 50), "T": (40, 150, 70)}[r]
            pygame.draw.circle(surf, color, c, 10)
            draw_text(surf, r, font(11, bold=True), WHITE, c)
        cnt = {k: self.results.count(k) for k in "PBT"}
        draw_text(surf, f"P {cnt['P']}    B {cnt['B']}    T {cnt['T']}", font(14, bold=True), (240, 220, 200),
                  (W / 2, 262))
        st = self.app.stats.get("baccarat", {})
        streak = st.get("streak", 0)
        draw_pill(surf, f"WIN STREAK {streak} / 25   -   BEST {st.get('best_streak', 0)}", font(13, bold=True),
                  (W / 2, 288), (255, 230, 150) if streak else (200, 180, 180), (0, 0, 0, 170),
                  GOLD if streak >= 10 else None, pad=(10, 2))
        for c in self.leaving + self.player + self.banker:
            c.draw(surf, self.assets)
        for hand, cx, col in ((self.player, BAC_P_X, (40, 90, 200)), (self.banker, BAC_B_X, (200, 40, 50))):
            if hand:
                shown = sum(bac_value(c.rank) for c in hand if c.arrived()) % 10
                draw_pill(surf, str(shown), font(24, bold=True), (cx, BAC_CARD_Y + CH + 30), WHITE, (*col, 240), GOLD,
                          pad=(16, 2))
        if self.winner:
            wx = {"P": BAC_P_X, "B": BAC_B_X, "T": W / 2}[self.winner]
            draw_pill(surf, "WINNER" if self.winner != "T" else "TIE!", font(18, bold=True),
                      (wx, BAC_CARD_Y - 12 if self.winner != "T" else 290), (20, 20, 20), (*GOLD, 255), None,
                      pad=(14, 3))
        # bets
        hover = self.region_at(mouse) if not self.queue else None
        for key, r in self.regions.items():
            if key == hover:
                hl = pygame.Surface(r.size, pygame.SRCALPHA)
                pygame.draw.rect(hl, (255, 255, 255, 45), hl.get_rect(), border_radius=16)
                surf.blit(hl, r)
            amt = self.won.get(key) if self.state == "result" else self.bets.get(key)
            if amt:
                pos = (r.centerx, r.bottom - 24)
                for k, v in enumerate(chip_breakdown(amt)[:8]):
                    pygame.draw.circle(surf, darken(CHIP_STYLE[v][0], 80), (pos[0], pos[1] + 2 - k * 4), 17)
                    img = self.assets.chip(v, 17)
                    surf.blit(img, img.get_rect(center=(pos[0], pos[1] - k * 4)))
                draw_pill(surf, money(amt), font(12, bold=True), (pos[0] + 48, pos[1]), WHITE, (0, 0, 0, 190), None,
                          pad=(6, 2))
        if self.message:
            draw_pill(surf, self.message, font(18, bold=True), (W / 2, 330), GOLD, (0, 0, 0, 200), GOLD_DARK)
        draw_text(surf, f"TOTAL BET: {money(self.total_bet)}", font(15, bold=True), WHITE, (W / 2, 600))
        # tray
        pygame.draw.rect(surf, (18, 6, 10), (0, 636, W, 84))
        pygame.draw.line(surf, GOLD_DARK, (0, 636), (W, 636), 2)
        tray = pygame.Rect(0, 0, 590, 80)
        tray.center = (W / 2, CHIP_TRAY_Y + 1)
        pygame.draw.rect(surf, (10, 4, 6), tray, border_radius=16)
        pygame.draw.rect(surf, (110, 60, 70), tray, width=2, border_radius=16)
        busy = bool(self.queue)
        for v, (cx, cy) in self.chip_pos.items():
            sel = v == self.selected
            y = cy - (9 if sel else 0)
            pygame.draw.circle(surf, (0, 0, 0), (cx + 2, cy + 4), 31)
            if sel:
                pygame.draw.circle(surf, GOLD, (cx, y), 35, width=3)
            img = self.assets.chip(v, 31)
            surf.blit(img, img.get_rect(center=(cx, y)))
            if busy or v > self.app.balance:
                surf.blit(self.dim_chip, self.dim_chip.get_rect(center=(cx, y)))
        self.btn_clear.draw(surf, mouse, not busy and (bool(self.bets) or self.state == "result"))
        self.btn_undo.draw(surf, mouse, self.state == "betting" and bool(self.history))
        self.btn_rebet.draw(surf, mouse, not busy and bool(self.last_bets))
        self.btn_deal.text = "DEAL AGAIN" if self.state == "result" else "DEAL"
        self.btn_deal.draw(surf, mouse, not busy and (bool(self.bets) or (self.state == "result" and
                                                                           bool(self.last_bets))))
        self.app.draw_top_bar(surf, "BACCARAT", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Shared pieces for the newer games
# --------------------------------------------------------------------------
class ChipTray:
    def __init__(self, bg=(22, 13, 8), border=(90, 60, 35)):
        x0 = W / 2 - 3.5 * 70
        self.pos = CHIP_WIN
        self.bg, self.border = bg, border
        self.dim = pygame.Surface((64, 64), pygame.SRCALPHA)
        pygame.draw.circle(self.dim, (0, 0, 0, 150), (32, 32), 31)

    def hit(self, pos):
        for v, (cx, cy) in self.pos.items():
            if math.hypot(pos[0] - cx, pos[1] - cy) <= 31:
                return v
        return None

    def draw(self, surf, assets, mouse, selected=None, dim=None, disabled=False):
        tray = pygame.Rect(0, 0, 590, 80)
        tray.center = (W / 2, CHIP_TRAY_Y + 1)
        pygame.draw.rect(surf, self.bg, tray, border_radius=16)
        pygame.draw.rect(surf, self.border, tray, width=2, border_radius=16)
        for v, (cx, cy) in self.pos.items():
            sel = v == selected
            hover = not disabled and math.hypot(mouse[0] - cx, mouse[1] - cy) <= 31
            y = cy - (9 if sel else 6 if hover else 0)
            pygame.draw.circle(surf, (0, 0, 0), (cx + 2, cy + 4), 31)
            if sel:
                pygame.draw.circle(surf, GOLD, (cx, y), 35, width=3)
            img = assets.chip(v, 31)
            surf.blit(img, img.get_rect(center=(cx, y)))
            if disabled or (dim and dim(v)):
                surf.blit(self.dim, self.dim.get_rect(center=(cx, y)))


def bottom_bar(surf, color=(10, 8, 12)):
    pygame.draw.rect(surf, color, (0, 636, W, 84))
    pygame.draw.line(surf, GOLD_DARK, (0, 636), (W, 636), 2)


def felt_table(dark, light, center_y=330, seed=4):
    """Wooden rail around a rounded felt, like the other tables."""
    surf = make_wood()
    felt_rect = pygame.Rect(12, 60, W - 24, 566)
    pygame.draw.rect(surf, (28, 16, 11), felt_rect.inflate(26, 26).move(0, 5), border_radius=40)
    pygame.draw.rect(surf, (48, 28, 19), felt_rect.inflate(26, 26), border_radius=40)
    pygame.draw.rect(surf, (78, 48, 32), felt_rect.inflate(14, 14), width=3, border_radius=36)
    felt = pygame.Surface((W, H), pygame.SRCALPHA)
    felt.fill((*dark, 255))
    for i in range(48):
        t = i / 47
        rw, rh = 1900 * (1 - t) + 160, 1100 * (1 - t) + 100
        pygame.draw.ellipse(felt, lerp_col(dark, light, t ** 0.8), (W / 2 - rw / 2, center_y - rh / 2, rw, rh))
    rng = random.Random(seed)
    for _ in range(15000):
        x, y = rng.randrange(W), rng.randrange(H)
        r, g, b, a = felt.get_at((x, y))
        d = rng.choice((-6, -3, 3, 5))
        felt.set_at((x, y), (max(0, min(255, r + d)), max(0, min(255, g + d)), max(0, min(255, b + d)), a))
    mask = pygame.Surface((W, H), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), felt_rect, border_radius=30)
    felt.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(felt, (0, 0))
    return surf


def gradient_bg(top, bottom):
    s = pygame.Surface((W, H))
    for y in range(H):
        pygame.draw.line(s, lerp_col(top, bottom, y / H), (0, y), (W, y))
    return s


def soft_panel(surf, rect, alpha=150, border=GOLD_DARK, radius=14):
    p = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(p, (0, 0, 0, alpha), p.get_rect(), border_radius=radius)
    surf.blit(p, rect)
    pygame.draw.rect(surf, border, rect, width=2, border_radius=radius)


class StakeGame:
    """Chips set a bet that stays the same between rounds (like the slot machine)."""
    key = ""

    def __init__(self, app, tray_colors=((22, 13, 8), (90, 60, 35))):
        self.app = app
        self.assets = app.assets
        self.tray = ChipTray(*tray_colors)
        self.bet = 0
        self.message = ""
        self.btn_clear = Button((40, 652, 150, 52), "CLEAR BET", (120, 35, 40), 18)

    def busy(self):
        return False

    def can_leave(self):
        return not self.busy()

    def outstanding_bets(self):
        return 0

    def leave(self):
        pass

    def chip_click(self, pos):
        v = self.tray.hit(pos)
        if v is None:
            return False
        if self.busy():
            return True
        if self.bet + v > self.app.balance:
            self.message = "NOT ENOUGH CHIPS FOR THAT BET"
        else:
            self.bet += v
            self.message = ""
            self.app.sfx("chip")
        return True

    def all_in(self):
        """ALL IN: your whole balance becomes the bet."""
        if self.busy():
            return
        if self.app.balance <= 0:
            self.message = "YOU HAVE NO CHIPS TO BET"
            return
        self.bet = self.app.balance
        self.message = f"ALL IN!  BET {money(self.bet)}"
        self.app.sfx("chip")

    def take_bet(self):
        if not self.bet:
            self.message = "ADD CHIPS TO SET YOUR BET FIRST"
            return 0
        if self.bet > self.app.balance:
            self.message = "NOT ENOUGH CHIPS - LOWER YOUR BET"
            return 0
        self.app.balance -= self.bet
        return self.bet

    def finish(self, stake, returned, win_msg=None, lose_msg=None):
        """Pay out, show the result, track stats."""
        self.app.balance += returned
        net = returned - stake
        self.app.record(self.key, stake, returned)
        if net > 0:
            self.message = win_msg or f"YOU WIN {money(returned)}  (+{money(net)})"
            self.app.float_text(f"+{money(net)}", (80, 230, 110))
            self.app.sfx("win")
        elif net < 0:
            self.message = lose_msg or (f"YOU GET BACK {money(returned)}  (-{money(-net)})" if returned
                                        else f"YOU LOSE {money(stake)}")
            self.app.float_text(f"-{money(-net)}", (240, 90, 90))
            self.app.sfx("lose")
        else:
            self.message = "BREAK EVEN - YOUR BET IS RETURNED"
        if self.bet > self.app.balance:
            self.bet = 0
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        self.app.save()

    def draw_bottom(self, surf, mouse, main_btn, main_enabled, color=(10, 8, 12)):
        bottom_bar(surf, color)
        self.tray.draw(surf, self.assets, mouse, dim=lambda v: self.bet + v > self.app.balance, disabled=self.busy())
        self.btn_clear.hint = f"BET {money(self.bet)}"
        self.btn_clear.draw(surf, mouse, self.bet > 0 and not self.busy())
        main_btn.draw(surf, mouse, main_enabled)


class AreaBetGame:
    """Pick a chip, click areas of the table to bet (like roulette). Bets clear after each round."""
    key = ""

    def __init__(self, app, tray_colors=((22, 13, 8), (90, 60, 35))):
        self.app = app
        self.assets = app.assets
        self.tray = ChipTray(*tray_colors)
        self.selected = 10
        self.bets = {}
        self.history = []
        self.last_bets = {}
        self.won = {}
        self.state = "betting"
        self.message = "PICK A CHIP, CLICK THE TABLE TO BET"
        self.btn_clear = Button((22, 652, 100, 52), "CLEAR", (120, 35, 40), 18)
        self.btn_undo = Button((130, 652, 100, 52), "UNDO", (80, 80, 90), 18)
        self.btn_rebet = Button((238, 652, 100, 52), "REBET", (40, 70, 130), 18)

    @property
    def total_bet(self):
        return sum(self.bets.values())

    def busy(self):
        return self.state == "playing"

    def can_leave(self):
        return not self.busy()

    def outstanding_bets(self):
        return self.total_bet if self.state != "result" else 0

    def leave(self):
        if self.state == "betting":
            self.app.balance += self.total_bet
        self.bets, self.history, self.won = {}, [], {}
        self.state = "betting"

    def new_round(self):
        if self.state == "result":
            self.bets, self.history, self.won = {}, [], {}
            self.state = "betting"
            self.message = "PICK A CHIP, CLICK THE TABLE TO BET"

    def place(self, key, v=None):
        self.new_round()
        v = v or self.selected
        if self.app.balance < v:
            self.message = "NOT ENOUGH CHIPS"
            return
        self.app.balance -= v
        self.bets[key] = self.bets.get(key, 0) + v
        self.history.append((key, v))
        self.message = ""
        self.app.sfx("chip")

    def remove(self, key):
        if self.state == "betting" and key in self.bets:
            self.app.balance += self.bets.pop(key)
            self.history = [h for h in self.history if h[0] != key]
            self.app.sfx("chip")

    def clear(self):
        self.new_round()
        self.app.balance += self.total_bet
        self.bets, self.history = {}, []

    def undo(self):
        if self.state == "betting" and self.history:
            key, v = self.history.pop()
            self.bets[key] -= v
            if self.bets[key] <= 0:
                del self.bets[key]
            self.app.balance += v
            self.app.sfx("chip")

    def rebet(self):
        self.clear()
        if sum(self.last_bets.values()) > self.app.balance:
            self.message = "NOT ENOUGH CHIPS TO REBET"
            return
        for k, v in self.last_bets.items():
            self.place(k, v)

    def begin(self):
        """Call when the round starts. Returns False if there's nothing bet."""
        if self.state == "result" and not self.bets and self.last_bets:
            self.new_round()
            self.rebet()
        self.new_round()
        if not self.bets:
            self.message = "PLACE A BET FIRST"
            return False
        self.last_bets = dict(self.bets)
        self.state = "playing"
        self.message = ""
        return True

    def settle(self, returns, headline):
        """returns: bet key -> total paid back (0 if lost)."""
        self.won = returns
        total_ret = sum(returns.values())
        stake = self.total_bet
        self.app.balance += total_ret
        self.app.record(self.key, stake, total_ret)
        net = total_ret - stake
        if net > 0:
            self.message = f"{headline}   -   YOU WIN {money(net)}!"
            self.app.float_text(f"+{money(net)}", (80, 230, 110))
            self.app.sfx("win")
        elif net < 0:
            self.message = f"{headline}   -   YOU LOSE {money(-net)}"
            self.app.float_text(f"-{money(-net)}", (240, 90, 90))
            self.app.sfx("lose")
        else:
            self.message = f"{headline}   -   BREAK EVEN"
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        self.state = "result"
        self.app.save()

    def handle_common(self, e, region_at, start):
        if self.busy():
            return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            v = self.tray.hit(e.pos)
            if v:
                self.selected = v
                self.app.sfx("chip")
            elif self.btn_clear.clicked(e.pos):
                self.clear()
            elif self.btn_undo.clicked(e.pos):
                self.undo()
            elif self.btn_rebet.clicked(e.pos, bool(self.last_bets)):
                self.rebet()
            elif self.btn_main.clicked(e.pos):
                start()
            else:
                key = region_at(e.pos)
                if key:
                    self.place(key)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            key = region_at(e.pos)
            if key:
                self.remove(key)
        elif e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_SPACE, pygame.K_RETURN):
                start()
            elif e.key == pygame.K_BACKSPACE:
                self.undo()

    def draw_bet_stack(self, surf, key, pos, radius=15):
        amt = self.won.get(key) if self.state == "result" else self.bets.get(key)
        if not amt:
            return
        for k, v in enumerate(chip_breakdown(amt)[:8]):
            pygame.draw.circle(surf, darken(CHIP_STYLE[v][0], 80), (pos[0], pos[1] + 2 - k * 4), radius)
            img = self.assets.chip(v, radius)
            surf.blit(img, img.get_rect(center=(pos[0], pos[1] - k * 4)))
        draw_pill(surf, money(amt), font(11, bold=True), (pos[0], pos[1] + radius + 8), WHITE, (0, 0, 0, 200), None,
                  pad=(6, 1))

    def draw_bottom(self, surf, mouse, color=(10, 8, 12)):
        bottom_bar(surf, color)
        busy = self.busy()
        self.tray.draw(surf, self.assets, mouse, selected=self.selected, dim=lambda v: v > self.app.balance,
                       disabled=busy)
        self.btn_clear.draw(surf, mouse, not busy and (bool(self.bets) or self.state == "result"))
        self.btn_undo.draw(surf, mouse, self.state == "betting" and bool(self.history))
        self.btn_rebet.draw(surf, mouse, not busy and bool(self.last_bets))
        self.btn_main.draw(surf, mouse, not busy and (bool(self.bets) or (self.state == "result" and
                                                                          bool(self.last_bets))))


# --------------------------------------------------------------------------
# Plinko
# --------------------------------------------------------------------------
PLINKO_MULTS = {       # 12 rows -> 13 slots; ~96-97% payback at every risk level
    "LOW": [10, 3, 1.6, 1.4, 1.1, 1, 0.4, 1, 1.1, 1.4, 1.6, 3, 10],
    "MEDIUM": [33, 11, 4, 2, 1.1, 0.6, 0.2, 0.6, 1.1, 2, 4, 11, 33],
    "HIGH": [170, 24, 8.1, 2, 0.7, 0.15, 0.15, 0.15, 0.7, 2, 8.1, 24, 170],
}
PLINKO_TOP, PLINKO_ROW, PLINKO_GAP, PLINKO_CX = 104, 35, 44, W / 2


def plinko_color(m):
    t = max(0.0, min(1.0, math.log10(max(m, 0.1) * 10) / 3.25))
    return lerp_col((250, 210, 60), (225, 35, 70), t)


class Plinko(StakeGame):
    key = "plinko"

    def __init__(self, app):
        super().__init__(app, ((12, 8, 24), (70, 60, 110)))
        self.bg = gradient_bg((34, 14, 60), (8, 6, 18))
        glow = pygame.Surface((W, H), pygame.SRCALPHA)
        for i in range(30):
            r = 480 - i * 14
            pygame.draw.ellipse(glow, (140, 80, 255, 4), (W / 2 - r, 330 - r * 0.7, r * 2, r * 1.4))
        self.bg.blit(glow, (0, 0))
        for r in range(12):
            for j in range(r + 3):
                x, y = PLINKO_CX + (j - (r + 2) / 2) * PLINKO_GAP, PLINKO_TOP + r * PLINKO_ROW
                pygame.draw.circle(self.bg, (60, 50, 90), (x + 1, y + 2), 6)
                pygame.draw.circle(self.bg, (235, 230, 255), (x, y), 5)
        self.risk = "MEDIUM"
        self.balls = []
        self.hits = []
        self.bump = [0.0] * 13
        self.t = 0.0
        self.message = "SET YOUR BET, THEN DROP A BALL"
        self.risk_btns = {r: Button((990 + i * 88, 150, 82, 42), r if r != "MEDIUM" else "MED",
                                    [(40, 110, 70), (170, 120, 20), (170, 35, 45)][i], 16)
                          for i, r in enumerate(PLINKO_MULTS)}
        self.btn_drop = Button((965, 646, 270, 62), "DROP BALL", (25, 120, 60), 26, "SPACE")

    def busy(self):
        return bool(self.balls)

    def outstanding_bets(self):
        return sum(b["stake"] for b in self.balls)

    def drop(self):
        stake = self.take_bet()
        if not stake:
            return
        rights, path = 0, [(PLINKO_CX + random.uniform(-2, 2), PLINKO_TOP - 60)]
        for r in range(12):
            path.append((PLINKO_CX + (rights - r / 2) * PLINKO_GAP, PLINKO_TOP + r * PLINKO_ROW - 11))
            rights += random.random() < 0.5
        path.append((PLINKO_CX + (rights - 6) * PLINKO_GAP, PLINKO_TOP + 12 * PLINKO_ROW + 8))
        self.balls.append({"path": path, "seg": 0, "t": 0.0, "stake": stake, "slot": rights,
                           "mults": PLINKO_MULTS[self.risk], "col": random.choice([(255, 90, 160), (255, 220, 90),
                                                                                    (120, 220, 255)])})
        self.message = ""
        self.app.sfx("chip")

    def land(self, b):
        m = b["mults"][b["slot"]]
        ret = int(b["stake"] * m + 1e-9)
        self.bump[b["slot"]] = 1.0
        self.hits = ([m] + self.hits)[:12]
        if b["slot"] in (0, 12):
            self.app.unlock("edge_case")
        self.finish(b["stake"], ret, f"x{m:g}!  YOU WIN {money(ret)}", f"x{m:g}  -  YOU GET BACK {money(ret)}")

    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN):
            self.drop()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.btn_drop.clicked(e.pos):
                self.drop()
                return
            if not self.balls:
                for r, b in self.risk_btns.items():
                    if b.clicked(e.pos):
                        self.risk = r
                        self.app.sfx("chip")
                        return
            if self.chip_click(e.pos):
                return
            if self.btn_clear.clicked(e.pos) and not self.balls:
                self.bet = 0

    def chip_click(self, pos):          # chips can be changed even while balls are falling
        v = self.tray.hit(pos)
        if v is None:
            return False
        if self.bet + v > self.app.balance:
            self.message = "NOT ENOUGH CHIPS FOR THAT BET"
        else:
            self.bet += v
            self.app.sfx("chip")
        return True

    def update(self, dt):
        self.t += dt
        for b in list(self.balls):
            b["t"] += dt / 0.13
            while b["t"] >= 1:
                b["t"] -= 1
                b["seg"] += 1
                if b["seg"] >= len(b["path"]) - 1:
                    self.balls.remove(b)
                    self.land(b)
                    break
                if random.random() < 0.35:
                    self.app.sfx("card")
        self.bump = [max(0.0, v - dt * 3) for v in self.bump]

    def ball_pos(self, b):
        (x0, y0), (x1, y1) = b["path"][b["seg"]], b["path"][b["seg"] + 1]
        t = b["t"]
        return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t - math.sin(math.pi * t) * 13

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        mults = PLINKO_MULTS[self.risk]
        for k, m in enumerate(mults):
            x = PLINKO_CX + (k - 6) * PLINKO_GAP
            y = PLINKO_TOP + 12 * PLINKO_ROW + 8 + self.bump[k] * 8
            r = pygame.Rect(0, 0, 40, 30)
            r.center = (x, y)
            col = plinko_color(m)
            pygame.draw.rect(surf, darken(col, 70), r.move(0, 4), border_radius=6)
            pygame.draw.rect(surf, lighten(col, self.bump[k] * 80), r, border_radius=6)
            draw_text(surf, f"{m:g}x" if m < 100 else f"{int(m)}", font(12 if m < 100 else 11, bold=True),
                      (30, 10, 20), r.center)
        for b in self.balls:
            x, y = self.ball_pos(b)
            if draw_theme_ball(surf, x, y, 10):
                continue
            pygame.draw.circle(surf, darken(b["col"], 90), (x + 1, y + 2), 9)
            pygame.draw.circle(surf, b["col"], (x, y), 9)
            pygame.draw.circle(surf, WHITE, (x - 3, y - 3), 3)
        # side panels
        right = pygame.Rect(970, 80, 290, 540)
        soft_panel(surf, right, 130, (90, 70, 140))
        draw_text(surf, "RISK", font(16, bold=True), GOLD, (right.centerx, 128))
        for r, b in self.risk_btns.items():
            b.draw(surf, mouse, not self.balls)
            if r == self.risk:
                pygame.draw.rect(surf, WHITE, b.rect.inflate(6, 6), width=2, border_radius=12)
        draw_text(surf, "EDGE SLOTS PAY", font(13, bold=True), (200, 190, 230), (right.centerx, 222))
        draw_text(surf, f"{mults[0]:g}x", font(34, bold=True), plinko_color(mults[0]), (right.centerx, 256))
        draw_text(surf, "RECENT DROPS", font(14, bold=True), GOLD, (right.centerx, 310))
        for i, m in enumerate(self.hits[:10]):
            col, row = i % 2, i // 2
            draw_pill(surf, f"{m:g}x", font(14, bold=True), (right.x + 90 + col * 110, 344 + row * 36), (30, 10, 20),
                      (*plinko_color(m), 255), None, pad=(12, 3))
        left = pygame.Rect(20, 80, 290, 540)
        soft_panel(surf, left, 130, (90, 70, 140))
        draw_text(surf, "PLINKO", font(40, bold=True, serif=True), GOLD, (left.centerx, 130))
        for i, line in enumerate(["Drop a ball from the top.", "It bounces off the pegs", "and lands in a slot.",
                                  "", "Your bet is multiplied by", "the slot it lands in.", "",
                                  "Edges are rare but huge.", "The middle is common", "but pays less.", "",
                                  "Drop as many balls", "as you like!"]):
            draw_text(surf, line, font(15), (215, 205, 235), (left.centerx, 190 + i * 24))
        if self.message:
            draw_pill(surf, self.message, font(16, bold=True), (W / 2, 612), GOLD, (0, 0, 0, 200), GOLD_DARK,
                      pad=(14, 3))
        self.draw_bottom(surf, mouse, self.btn_drop, 0 < self.bet <= self.app.balance, (12, 8, 22))
        self.app.draw_top_bar(surf, "PLINKO", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Mines
# --------------------------------------------------------------------------
MINE_OPTIONS = [1, 3, 5, 10, 15, 20, 24]
MINE_TILE, MINE_GAP, MINE_X, MINE_Y = 88, 10, 200, 100


def mines_mult(mines, picks):
    if picks == 0:
        return 1.0
    return 0.97 * math.comb(25, picks) / math.comb(25 - mines, picks)


def draw_bomb(surf, cx, cy, r):
    pygame.draw.line(surf, (160, 120, 70), (cx + r * 0.5, cy - r * 0.7), (cx + r * 0.9, cy - r * 1.1), 3)
    pygame.draw.circle(surf, (255, 170, 40), (cx + r * 0.95, cy - r * 1.15), r * 0.22)
    pygame.draw.circle(surf, (255, 240, 150), (cx + r * 0.95, cy - r * 1.15), r * 0.1)
    pygame.draw.circle(surf, (20, 20, 24), (cx, cy), r)
    pygame.draw.circle(surf, (90, 90, 100), (cx - r * 0.35, cy - r * 0.35), r * 0.25)
    pygame.draw.rect(surf, (50, 50, 55), (cx + r * 0.3, cy - r * 0.95, r * 0.5, r * 0.4), border_radius=3)


class Mines(StakeGame):
    key = "mines"

    def __init__(self, app):
        super().__init__(app, ((8, 10, 24), (60, 70, 110)))
        self.bg = gradient_bg((16, 22, 48), (6, 8, 18))
        self.gem = pygame.transform.smoothscale(make_symbol("diamond"), (58, 45))
        self.mine_idx = 2
        self.state = "betting"
        self.mines = set()
        self.revealed = set()
        self.stake = 0
        self.boom_at = None
        self.shake = 0.0
        self.t = 0.0
        self.message = "CHOOSE HOW MANY MINES, SET YOUR BET, THEN START"
        self.btn_less = Button((800, 176, 60, 48), "-", (50, 60, 100), 26)
        self.btn_more = Button((1130, 176, 60, 48), "+", (50, 60, 100), 26)
        self.btn_start = Button((965, 646, 270, 62), "START", (25, 120, 60), 26, "SPACE")
        self.btn_cash = Button((965, 646, 270, 62), "CASH OUT", (215, 120, 15), 24, "SPACE")

    @property
    def n_mines(self):
        return MINE_OPTIONS[self.mine_idx]

    def busy(self):
        return self.state == "playing"

    def outstanding_bets(self):
        return self.stake if self.state == "playing" else 0

    def tile_rect(self, i):
        r, c = divmod(i, 5)
        return pygame.Rect(MINE_X + c * (MINE_TILE + MINE_GAP), MINE_Y + r * (MINE_TILE + MINE_GAP), MINE_TILE, MINE_TILE)

    def start(self):
        if self.state == "playing":
            return
        stake = self.take_bet()
        if not stake:
            return
        self.stake = stake
        self.mines = set(random.sample(range(25), self.n_mines))
        self.revealed = set()
        self.boom_at = None
        self.state = "playing"
        self.message = "PICK A TILE - FIND GEMS, AVOID THE MINES"
        self.app.sfx("chip")

    def pick(self, i):
        if self.state != "playing" or i in self.revealed:
            return
        self.revealed.add(i)
        r = self.tile_rect(i)
        if i in self.mines:
            self.boom_at = i
            self.state = "result"
            self.shake = 0.4
            self.app.effects.burst(r.centerx, r.centery, 40, [(255, 120, 40), (255, 220, 90), (200, 40, 30)])
            self.app.sfx("boom")
            self.finish(self.stake, 0, lose_msg=f"BOOM! YOU HIT A MINE  -  YOU LOSE {money(self.stake)}")
            return
        self.app.sfx("chip")
        self.app.effects.burst(r.centerx, r.centery, 10, [(120, 220, 255), (255, 255, 255)])
        picks = len(self.revealed)
        if picks >= 10:
            self.app.unlock("minesweeper")
        if picks == 25 - self.n_mines:
            self.cash_out()
        else:
            self.message = f"{picks} GEM{'S' if picks > 1 else ''}!  CASH OUT {money(self.cash_value())} OR KEEP GOING"

    def cash_value(self):
        return int(self.stake * mines_mult(self.n_mines, len(self.revealed)))

    def cash_out(self):
        if self.state != "playing" or not self.revealed:
            return
        win = self.cash_value()
        self.state = "result"
        self.finish(self.stake, win, f"CASHED OUT AT x{mines_mult(self.n_mines, len(self.revealed)):.2f}  -  "
                                     f"YOU WIN {money(win)}")

    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN):
            self.cash_out() if self.state == "playing" else self.start()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.state == "playing":
                for i in range(25):
                    if self.tile_rect(i).collidepoint(e.pos):
                        self.pick(i)
                        return
                if self.btn_cash.clicked(e.pos, bool(self.revealed)):
                    self.cash_out()
                return
            if self.chip_click(e.pos):
                return
            if self.btn_clear.clicked(e.pos):
                self.bet = 0
            elif self.btn_start.clicked(e.pos):
                self.start()
            elif self.btn_less.clicked(e.pos):
                self.mine_idx = max(0, self.mine_idx - 1)
            elif self.btn_more.clicked(e.pos):
                self.mine_idx = min(len(MINE_OPTIONS) - 1, self.mine_idx + 1)

    def update(self, dt):
        self.t += dt
        self.shake = max(0.0, self.shake - dt)

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        ox = random.randint(-6, 6) if self.shake else 0
        board = pygame.Rect(MINE_X - 20, MINE_Y - 20, 5 * MINE_TILE + 4 * MINE_GAP + 40, 5 * MINE_TILE + 4 * MINE_GAP + 40)
        soft_panel(surf, board.move(ox, 0), 120, (60, 70, 120), 18)
        ended = self.state == "result"
        for i in range(25):
            r = self.tile_rect(i).move(ox, 0)
            shown = i in self.revealed or (ended and self.stake)
            if not shown:
                hover = r.collidepoint(mouse) and self.state == "playing"
                base = (70, 85, 150) if hover else (45, 55, 105)
                pygame.draw.rect(surf, (20, 25, 50), r.move(0, 5), border_radius=12)
                pygame.draw.rect(surf, base, r, border_radius=12)
                pygame.draw.rect(surf, lighten(base, 25), (r.x + 6, r.y + 6, r.w - 12, r.h / 2 - 6), border_radius=8)
                continue
            is_mine = i in self.mines
            picked = i in self.revealed
            col = (150, 30, 40) if (is_mine and i == self.boom_at) else (18, 22, 40)
            pygame.draw.rect(surf, col, r, border_radius=12)
            pygame.draw.rect(surf, (80, 90, 140) if picked else (40, 45, 70), r, width=2, border_radius=12)
            if is_mine:
                if not draw_theme_bomb(surf, r.centerx, r.centery + 4, 20 if picked else 15):
                    draw_bomb(surf, r.centerx, r.centery + 4, 20 if picked else 15)
            else:
                gem = theme_gem((52, 52) if picked else (38, 38)) if CURRENT_THEME else None
                img = gem or (self.gem if picked else pygame.transform.smoothscale(self.gem, (40, 31)))
                surf.blit(img, img.get_rect(center=r.center))
            if not picked:
                dim = pygame.Surface(r.size, pygame.SRCALPHA)
                pygame.draw.rect(dim, (0, 0, 0, 110), dim.get_rect(), border_radius=12)
                surf.blit(dim, r)
        # right panel
        panel = pygame.Rect(760, 90, 480, 470)
        soft_panel(surf, panel, 140, (60, 70, 120))
        draw_text(surf, "MINES", font(16, bold=True), GOLD, (panel.centerx, 140))
        for b in (self.btn_less, self.btn_more):
            b.draw(surf, mouse, self.state != "playing")
        draw_text(surf, str(self.n_mines), font(44, bold=True), WHITE, (panel.centerx, 200))
        draw_text(surf, f"{25 - self.n_mines} GEMS", font(14, bold=True), (150, 200, 255), (panel.centerx, 236))
        picks = len(self.revealed - self.mines) if self.state != "betting" else 0
        cur = mines_mult(self.n_mines, picks) if self.state == "playing" else 1.0
        nxt = mines_mult(self.n_mines, picks + 1) if picks < 25 - self.n_mines else None
        draw_text(surf, "CURRENT", font(13, bold=True), (170, 180, 210), (panel.x + 120, 290))
        draw_text(surf, f"x{cur:.2f}", font(34, bold=True), (80, 230, 110), (panel.x + 120, 326))
        draw_text(surf, "NEXT GEM", font(13, bold=True), (170, 180, 210), (panel.right - 120, 290))
        draw_text(surf, f"x{nxt:.2f}" if nxt else "-", font(34, bold=True), GOLD, (panel.right - 120, 326))
        stake = self.stake if self.state != "betting" else self.bet
        draw_text(surf, f"BET  {money(stake)}", font(20, bold=True), WHITE, (panel.centerx, 390))
        if self.state == "playing":
            draw_text(surf, f"CASH OUT NOW:  {money(self.cash_value())}", font(20, bold=True), (80, 230, 110),
                      (panel.centerx, 424))
        draw_text(surf, "More mines = bigger multipliers, more danger.", font(13), (170, 180, 210),
                  (panel.centerx, 520))
        if self.message:
            draw_pill(surf, self.message, font(15, bold=True), (panel.centerx, 474), GOLD, (0, 0, 0, 200), GOLD_DARK,
                      pad=(12, 4))
        if self.state == "playing":
            bottom_bar(surf, (8, 10, 22))
            self.tray.draw(surf, self.assets, mouse, disabled=True)
            self.btn_cash.text = f"CASH OUT {money(self.cash_value())}" if self.revealed else "CASH OUT"
            self.btn_cash.draw(surf, mouse, bool(self.revealed))
        else:
            self.btn_start.text = "PLAY AGAIN" if self.state == "result" else "START"
            self.draw_bottom(surf, mouse, self.btn_start, 0 < self.bet <= self.app.balance, (8, 10, 22))
        self.app.draw_top_bar(surf, "MINES", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Video Poker (Jacks or Better, 8/5 paytable)
# --------------------------------------------------------------------------
VP_PAYS = [("ROYAL FLUSH", 800), ("STRAIGHT FLUSH", 50), ("FOUR OF A KIND", 25), ("FULL HOUSE", 8), ("FLUSH", 5),
           ("STRAIGHT", 4), ("THREE OF A KIND", 3), ("TWO PAIR", 2), ("JACKS OR BETTER", 1)]
VP_CW, VP_CH = 150, 210


def vp_hand(cards):
    score = eval_hand([(RANK_NUM[r], s) for r, s in cards])
    cat = score[0]
    if cat == 8:
        return "ROYAL FLUSH" if score[1] == 14 else "STRAIGHT FLUSH"
    names = {7: "FOUR OF A KIND", 6: "FULL HOUSE", 5: "FLUSH", 4: "STRAIGHT", 3: "THREE OF A KIND", 2: "TWO PAIR"}
    if cat in names:
        return names[cat]
    if cat == 1 and score[1] >= 11:
        return "JACKS OR BETTER"
    return None


class VideoPoker(StakeGame):
    key = "videopoker"

    def __init__(self, app):
        super().__init__(app, ((6, 10, 30), (50, 70, 130)))
        self.bg = gradient_bg((12, 24, 70), (4, 6, 20))
        self.big = {}
        self.deck = []
        self.hand = []
        self.held = [False] * 5
        self.face = [False] * 5
        self.flip = [None] * 5
        self.queue = []
        self.state = "betting"          # betting, hold, drawing, result
        self.stake = 0
        self.result = None
        self.message = "SET YOUR BET, THEN DEAL"
        self.btn_deal = Button((965, 646, 270, 62), "DEAL", (25, 120, 60), 28, "SPACE")
        self.hold_btns = [Button((self.card_x(i), 540, VP_CW, 44), "HOLD", (60, 70, 120), 18, str(i + 1))
                          for i in range(5)]

    @staticmethod
    def card_x(i):
        return W / 2 + (i - 2) * (VP_CW + 22) - VP_CW / 2

    def face_img(self, card):
        if card not in self.big:
            self.big[card] = pygame.transform.smoothscale(self.assets.faces[card], (VP_CW, VP_CH))
        return self.big[card]

    def busy(self):
        return self.state in ("hold", "drawing")

    def outstanding_bets(self):
        return self.stake if self.busy() else 0

    def schedule(self, d, fn):
        self.queue.append([d, fn])

    def reveal(self, i):
        self.flip[i] = 0.0
        self.app.sfx("card")

    def deal(self):
        if self.state == "hold":
            self.draw_cards()
            return
        if self.state == "drawing" or self.queue:
            return
        stake = self.take_bet()
        if not stake:
            return
        self.stake = stake
        self.deck = [(r, s) for s in SUITS for r in RANKS]
        random.shuffle(self.deck)
        self.hand = [self.deck.pop() for _ in range(5)]
        self.held = [False] * 5
        self.face = [False] * 5
        self.result = None
        self.state = "drawing"
        self.message = ""
        for i in range(5):
            self.schedule(0.09, lambda i=i: self.reveal(i))
        self.schedule(0.35, self.to_hold)

    def to_hold(self):
        self.state = "hold"
        self.result = vp_hand(self.hand)
        self.message = "CLICK CARDS TO HOLD THEM, THEN DRAW"

    def draw_cards(self):
        self.state = "drawing"
        swapped = [i for i in range(5) if not self.held[i]]
        for i in swapped:
            self.hand[i] = self.deck.pop()
            self.face[i] = False
        for i in swapped:
            self.schedule(0.12, lambda i=i: self.reveal(i))
        self.schedule(0.45, self.settle)

    def settle(self):
        self.result = vp_hand(self.hand)
        pays = dict(VP_PAYS)
        ret = self.stake * pays[self.result] if self.result else 0
        self.state = "result"
        if self.result in ("FOUR OF A KIND", "STRAIGHT FLUSH", "ROYAL FLUSH"):
            self.app.unlock("four_kind")
        self.finish(self.stake, ret, f"{self.result}!  YOU WIN {money(ret)}" if self.result else None,
                    "NO WINNING HAND" if not self.result else None)

    def toggle(self, i):
        if self.state == "hold":
            self.held[i] = not self.held[i]
            self.app.sfx("chip")

    def handle(self, e):
        if self.queue:
            return
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.deal()
            elif e.unicode in ("1", "2", "3", "4", "5"):
                self.toggle(int(e.unicode) - 1)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.btn_deal.clicked(e.pos):
                self.deal()
                return
            if self.state == "hold":
                for i in range(5):
                    if self.hold_btns[i].rect.collidepoint(e.pos) or \
                            pygame.Rect(self.card_x(i), 300, VP_CW, VP_CH).collidepoint(e.pos):
                        self.toggle(i)
                        return
            elif self.chip_click(e.pos):
                return
            elif self.btn_clear.clicked(e.pos):
                self.bet = 0

    def update(self, dt):
        if self.queue:
            self.queue[0][0] -= dt
            if self.queue[0][0] <= 0:
                _, fn = self.queue.pop(0)
                fn()
        for i in range(5):
            if self.flip[i] is not None:
                self.flip[i] += dt * 4
                if self.flip[i] >= 0.5:
                    self.face[i] = True
                if self.flip[i] >= 1:
                    self.flip[i] = None

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        # paytable
        pt = pygame.Rect(240, 70, 800, 210)
        pygame.draw.rect(surf, (10, 14, 50), pt, border_radius=12)
        pygame.draw.rect(surf, GOLD, pt, width=3, border_radius=12)
        stake = self.stake if self.state != "betting" else self.bet
        for k, (name, mult) in enumerate(VP_PAYS):
            col, row = k // 5, k % 5
            y = pt.y + 26 + row * 38
            x = pt.x + 30 + col * 400
            active = self.result == name
            if active:
                pygame.draw.rect(surf, (200, 40, 50), (x - 12, y - 16, 370, 32), border_radius=6)
            draw_text(surf, name, font(18, bold=True), (255, 235, 120) if not active else WHITE, (x, y),
                      anchor="midleft")
            draw_text(surf, f"{mult}x" + (f"  {money(stake * mult)}" if stake else ""), font(18, bold=True),
                      WHITE, (x + 350, y), anchor="midright")
        draw_text(surf, "JACKS OR BETTER", font(20, bold=True, serif=True), GOLD, (pt.right - 200, pt.bottom - 26))
        # cards
        for i in range(5):
            x, y = self.card_x(i), 300
            if not self.hand:
                pygame.draw.rect(surf, (30, 40, 90), (x, y, VP_CW, VP_CH), width=2, border_radius=12)
                continue
            img = self.face_img(self.hand[i]) if self.face[i] else themed(
                ("big_back",), lambda: pygame.transform.smoothscale(self.assets.back, (VP_CW, VP_CH)))
            w = VP_CW
            if self.flip[i] is not None:
                w = max(2, int(VP_CW * abs(math.cos(math.pi * self.flip[i]))))
                img = pygame.transform.smoothscale(img, (w, VP_CH))
            else:
                pygame.draw.rect(surf, (0, 0, 0), (x + 4, y + 6, VP_CW, VP_CH), border_radius=12)
            surf.blit(img, (x + (VP_CW - w) / 2, y))
            if self.held[i]:
                draw_pill(surf, "HELD", font(18, bold=True), (x + VP_CW / 2, y - 14), (20, 20, 20), (*GOLD, 255), None,
                          pad=(14, 3))
        if self.state == "hold":
            for i, b in enumerate(self.hold_btns):
                b.text = "HELD" if self.held[i] else "HOLD"
                b.color = (170, 120, 20) if self.held[i] else (60, 70, 120)
                b.draw(surf, mouse)
        if self.message:
            draw_pill(surf, self.message, font(18, bold=True), (W / 2, 608), GOLD, (0, 0, 0, 200), GOLD_DARK,
                      pad=(14, 4))
        self.btn_deal.text = "DRAW" if self.state == "hold" else "DEAL"
        if self.state in ("hold", "drawing"):
            bottom_bar(surf, (6, 8, 24))
            self.tray.draw(surf, self.assets, mouse, disabled=True)
            draw_text(surf, f"BET {money(self.stake)}", font(15, bold=True), GOLD, (115, 677))
            self.btn_deal.draw(surf, mouse, self.state == "hold" and not self.queue)
        else:
            self.draw_bottom(surf, mouse, self.btn_deal, 0 < self.bet <= self.app.balance and not self.queue, (6, 8, 24))
        self.app.draw_top_bar(surf, "VIDEO POKER", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Scratch cards
# --------------------------------------------------------------------------
SCRATCH_TICKETS = [
    {"name": "LUCKY 7s", "price": 10, "col": (200, 40, 60), "col2": (255, 190, 60)},
    {"name": "GOLD RUSH", "price": 50, "col": (190, 140, 20), "col2": (255, 240, 150)},
    {"name": "DIAMOND DELUXE", "price": 250, "col": (40, 110, 200), "col2": (170, 235, 255)},
]
SCRATCH_PRIZES = [(1, 0.12), (2, 0.08), (3, 0.04), (5, 0.025), (10, 0.01), (25, 0.004), (100, 0.001), (500, 0.0002)]
SCR_X, SCR_Y, SCR_CW, SCR_CH, SCR_GAP = 520, 236, 180, 96, 12
SCR_GRID = pygame.Rect(SCR_X, SCR_Y, 3 * SCR_CW + 2 * SCR_GAP, 3 * SCR_CH + 2 * SCR_GAP)
SCR_CELL = 12      # scratch-tracking resolution in pixels


class ScratchCards:
    key = "scratch"

    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.bg = gradient_bg((30, 12, 44), (8, 4, 14))
        self.kind = 0
        self.ticket = None           # dict: amounts[9], prize, win_cells
        self.coating = None
        self.cleared = None
        self.revealed = [False] * 9
        self.done = False
        self.last = None
        self.t = 0.0
        self.scratch_sound = 0.0
        self.message = "PICK A TICKET AND BUY IT"
        self.type_rects = [pygame.Rect(40, 130 + i * 118, 330, 104) for i in range(3)]
        self.btn_buy = Button((965, 646, 270, 62), "BUY TICKET", (25, 120, 60), 24, "SPACE")
        self.btn_reveal = Button((40, 652, 200, 52), "REVEAL ALL", (80, 60, 130), 18)

    def can_leave(self):
        return True

    def outstanding_bets(self):
        return self.ticket["prize"] if self.ticket and not self.done else 0

    def leave(self):
        pass

    def make_ticket(self, price):
        r, acc, mult = random.random(), 0.0, 0
        for m, p in SCRATCH_PRIZES:
            acc += p
            if r < acc:
                mult = m
                break
        pool = [m * price for m, _ in SCRATCH_PRIZES]
        cells = []
        if mult:
            cells += [mult * price] * 3
        counts = {a: cells.count(a) for a in pool}
        while len(cells) < 9:
            a = random.choice(pool)
            if counts.get(a, 0) < 2 and a != mult * price:
                cells.append(a)
                counts[a] = counts.get(a, 0) + 1
        random.shuffle(cells)
        win_cells = [i for i, a in enumerate(cells) if mult and a == mult * price]
        return {"amounts": cells, "prize": mult * price, "win_cells": win_cells, "price": price, "kind": self.kind}

    def make_coating(self):
        c = pygame.Surface(SCR_GRID.size, pygame.SRCALPHA)
        for i in range(9):
            r, col = divmod(i, 3)
            rect = pygame.Rect(col * (SCR_CW + SCR_GAP), r * (SCR_CH + SCR_GAP), SCR_CW, SCR_CH)
            pygame.draw.rect(c, (185, 188, 196, 255), rect, border_radius=10)
            c.set_clip(rect.inflate(-6, -6))
            for k in range(0, SCR_CW + 40, 14):
                pygame.draw.line(c, (170, 173, 182, 255), (rect.x + k, rect.y), (rect.x + k - 30, rect.bottom), 2)
            c.set_clip(None)
            draw_text(c, "SCRATCH", font(16, bold=True), (140, 142, 150), rect.center)
        return c

    def buy(self):
        if self.ticket and not self.done:
            self.message = "FINISH SCRATCHING THIS TICKET FIRST (OR REVEAL ALL)"
            return
        price = SCRATCH_TICKETS[self.kind]["price"]
        if self.app.balance < price:
            self.message = "NOT ENOUGH CHIPS FOR THIS TICKET"
            return
        self.app.balance -= price
        self.ticket = self.make_ticket(price)
        self.coating = self.make_coating()
        self.cleared = [[False] * (SCR_GRID.w // SCR_CELL + 1) for _ in range(SCR_GRID.h // SCR_CELL + 1)]
        self.revealed = [False] * 9
        self.done = False
        self.message = "SCRATCH WITH THE MOUSE - HOLD THE BUTTON AND RUB"
        self.app.sfx("chip")

    def cell_rect(self, i):
        r, c = divmod(i, 3)
        return pygame.Rect(c * (SCR_CW + SCR_GAP), r * (SCR_CH + SCR_GAP), SCR_CW, SCR_CH)

    def scratch_at(self, x, y):
        lx, ly = x - SCR_GRID.x, y - SCR_GRID.y
        pygame.draw.circle(self.coating, (0, 0, 0, 0), (lx, ly), 22)
        for gy in range(int((ly - 22) // SCR_CELL), int((ly + 22) // SCR_CELL) + 1):
            for gx in range(int((lx - 22) // SCR_CELL), int((lx + 22) // SCR_CELL) + 1):
                if 0 <= gy < len(self.cleared) and 0 <= gx < len(self.cleared[0]):
                    cx, cy = gx * SCR_CELL + SCR_CELL / 2, gy * SCR_CELL + SCR_CELL / 2
                    if (cx - lx) ** 2 + (cy - ly) ** 2 <= 22 * 22:
                        self.cleared[gy][gx] = True

    def check_cells(self):
        for i in range(9):
            if self.revealed[i]:
                continue
            r = self.cell_rect(i)
            tot = hit = 0
            for gy in range(r.y // SCR_CELL, r.bottom // SCR_CELL):
                for gx in range(r.x // SCR_CELL, r.right // SCR_CELL):
                    tot += 1
                    hit += self.cleared[gy][gx]
            if tot and hit / tot >= 0.55:
                self.reveal_cell(i)

    def reveal_cell(self, i):
        self.revealed[i] = True
        pygame.draw.rect(self.coating, (0, 0, 0, 0), self.cell_rect(i))
        if all(self.revealed) and not self.done:
            self.settle()

    def reveal_all(self):
        if self.ticket and not self.done:
            for i in range(9):
                if not self.revealed[i]:
                    self.reveal_cell(i)

    def settle(self):
        self.done = True
        t = self.ticket
        self.app.balance += t["prize"]
        self.app.record(self.key, t["price"], t["prize"])
        if t["prize"]:
            if t["prize"] >= 50 * t["price"]:
                self.app.unlock("scratch_big")
            self.message = f"MATCHED THREE {money(t['prize'])}!  YOU WIN {money(t['prize'])}"
            self.app.float_text(f"+{money(t['prize'])}", (80, 230, 110))
            self.app.sfx("win")
        else:
            self.message = "NO MATCH THIS TIME - TRY ANOTHER TICKET"
            self.app.sfx("lose")
        if self.app.balance < min(CHIP_VALUES):
            self.message = self.app.broke_message()
        self.app.save()

    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN):
            self.buy()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.btn_buy.clicked(e.pos):
                self.buy()
            elif self.btn_reveal.clicked(e.pos, bool(self.ticket) and not self.done):
                self.reveal_all()
            elif not (self.ticket and not self.done):
                for i, r in enumerate(self.type_rects):
                    if r.collidepoint(e.pos):
                        self.kind = i
                        self.app.sfx("chip")

    def update(self, dt):
        self.t += dt
        self.scratch_sound = max(0.0, self.scratch_sound - dt)
        pressed = pygame.mouse.get_pressed()[0]
        pos = pygame.mouse.get_pos()
        if pressed and self.ticket and not self.done and SCR_GRID.inflate(40, 40).collidepoint(pos):
            if self.last:
                x0, y0 = self.last
                steps = max(1, int(math.hypot(pos[0] - x0, pos[1] - y0) / 8))
                for k in range(1, steps + 1):
                    self.scratch_at(x0 + (pos[0] - x0) * k / steps, y0 + (pos[1] - y0) * k / steps)
            else:
                self.scratch_at(*pos)
            if self.scratch_sound <= 0:
                self.app.sfx("card")
                self.scratch_sound = 0.09
            self.last = pos
            self.check_cells()
        else:
            self.last = None

    def draw_ticket(self, surf, kind):
        info = SCRATCH_TICKETS[kind]
        t = pygame.Rect(460, 86, 700, 540)
        pygame.draw.rect(surf, (0, 0, 0), t.move(8, 10), border_radius=24)
        pygame.draw.rect(surf, info["col"], t, border_radius=24)
        pygame.draw.rect(surf, info["col2"], t.inflate(-14, -14), width=4, border_radius=20)
        surf.set_clip(t.inflate(-14, -14))
        for k in range(16):
            a = k / 16 * 2 * math.pi + self.t * 0.2
            pygame.draw.line(surf, lighten(info["col"], 20), (t.centerx, 150),
                             (t.centerx + math.cos(a) * 400, 150 + math.sin(a) * 400), 18)
        surf.set_clip(None)
        pygame.draw.rect(surf, info["col"], (t.x + 10, 214, t.w - 20, t.h - 138), border_radius=14)
        pygame.draw.rect(surf, info["col2"], t.inflate(-14, -14), width=4, border_radius=20)
        draw_text(surf, info["name"], font(44, bold=True, serif=True), info["col2"], (t.centerx, 130), shadow=(40, 10, 10))
        draw_text(surf, f"{money(info['price'])} TICKET   -   TOP PRIZE {money(info['price'] * 500)}",
                  font(16, bold=True), WHITE, (t.centerx, 172))
        draw_text(surf, "MATCH 3 AMOUNTS - WIN THAT AMOUNT!", font(15, bold=True), info["col2"], (t.centerx, 200))
        inner = SCR_GRID.inflate(24, 24)
        pygame.draw.rect(surf, (250, 245, 230), inner, border_radius=14)
        return t

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        kind = self.ticket["kind"] if self.ticket and not self.done else self.kind
        for i, r in enumerate(self.type_rects):
            info = SCRATCH_TICKETS[i]
            sel = i == self.kind
            pygame.draw.rect(surf, info["col"], r, border_radius=14)
            pygame.draw.rect(surf, WHITE if sel else info["col2"], r, width=4 if sel else 2, border_radius=14)
            draw_text(surf, info["name"], font(24, bold=True, serif=True), info["col2"], (r.x + 20, r.y + 34),
                      anchor="midleft", shadow=(30, 10, 10))
            draw_text(surf, f"{money(info['price'])} per ticket", font(16, bold=True), WHITE, (r.x + 20, r.y + 66),
                      anchor="midleft")
            draw_text(surf, f"TOP PRIZE {money(info['price'] * 500)}", font(12, bold=True), info["col2"],
                      (r.x + 20, r.y + 88), anchor="midleft")
        draw_text(surf, "About 1 in 3.5 tickets wins something.", font(14), (210, 190, 230), (205, 505))
        draw_text(surf, "Prizes from 1x up to 500x the price.", font(14), (210, 190, 230), (205, 528))
        self.draw_ticket(surf, kind)
        if self.ticket:
            pulse = 0.5 + 0.5 * math.sin(self.t * 6)
            for i, amt in enumerate(self.ticket["amounts"]):
                r = self.cell_rect(i).move(SCR_GRID.topleft)
                win = self.done and i in self.ticket["win_cells"]
                if win:
                    pygame.draw.rect(surf, lerp_col((255, 220, 90), (255, 250, 200), pulse), r.inflate(8, 8),
                                     border_radius=12)
                pygame.draw.rect(surf, (255, 252, 240), r, border_radius=10)
                draw_text(surf, money(amt), font(30, bold=True, serif=True),
                          (190, 30, 40) if win else (60, 50, 40), r.center)
            surf.blit(self.coating, SCR_GRID)
        else:
            for i in range(9):
                r = self.cell_rect(i).move(SCR_GRID.topleft)
                pygame.draw.rect(surf, (185, 188, 196), r, border_radius=10)
                draw_text(surf, "?", font(34, bold=True), (150, 152, 160), r.center)
        if self.message:
            draw_pill(surf, self.message, font(15, bold=True), (810, 604), GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(14, 4))
        bottom_bar(surf, (14, 6, 18))
        busy = bool(self.ticket) and not self.done
        price = SCRATCH_TICKETS[self.kind]["price"]
        draw_text(surf, f"CASH {money(self.app.balance)}", font(15, bold=True), (200, 190, 220), (640, 678))
        self.btn_reveal.draw(surf, mouse, busy)
        self.btn_buy.text = f"BUY {money(price)} TICKET"
        self.btn_buy.draw(surf, mouse, not busy and self.app.balance >= price)
        self.app.draw_top_bar(surf, "SCRATCH CARDS", lobby=True)


# --------------------------------------------------------------------------
# Keno
# --------------------------------------------------------------------------
KENO_PAY = {       # picks -> {hits: multiplier}; every pick count pays back ~92-95%
    1: {1: 3.8}, 2: {1: 1, 2: 9}, 3: {2: 3, 3: 37}, 4: {2: 1.5, 3: 7, 4: 100}, 5: {3: 4.5, 4: 22, 5: 450},
    6: {3: 2.5, 4: 7, 5: 70, 6: 1600}, 7: {3: 1.2, 4: 4, 5: 21, 6: 300, 7: 4500},
    8: {4: 3, 5: 12, 6: 90, 7: 1200, 8: 12000}, 9: {4: 1.5, 5: 6, 6: 35, 7: 350, 8: 4000, 9: 25000},
    10: {0: 2.5, 5: 3.5, 6: 22, 7: 120, 8: 1000, 9: 5000, 10: 100000},
}
KENO_X, KENO_Y, KENO_W, KENO_H = 40, 96, 58, 50


class Keno(StakeGame):
    key = "keno"

    def __init__(self, app):
        super().__init__(app, ((6, 14, 30), (50, 80, 130)))
        self.bg = gradient_bg((10, 40, 80), (4, 10, 24))
        self.picks = set()
        self.drawn = []
        self.shown = 0
        self.timer = 0.0
        self.state = "betting"
        self.stake = 0
        self.t = 0.0
        self.message = "PICK 1 TO 10 NUMBERS, SET YOUR BET, THEN PLAY"
        self.btn_play = Button((965, 646, 270, 62), "PLAY", (25, 120, 60), 28, "SPACE")
        self.btn_quick = Button((720, 520, 250, 50), "QUICK PICK", (40, 90, 160), 20)
        self.btn_wipe = Button((990, 520, 250, 50), "CLEAR PICKS", (120, 35, 40), 20)

    def busy(self):
        return self.state == "drawing"

    def outstanding_bets(self):
        return self.stake if self.busy() else 0

    def cell(self, n):
        r, c = divmod(n - 1, 10)
        return pygame.Rect(KENO_X + c * (KENO_W + 6), KENO_Y + r * (KENO_H + 6), KENO_W, KENO_H)

    def play(self):
        if self.busy():
            return
        if not self.picks:
            self.message = "PICK AT LEAST ONE NUMBER FIRST"
            return
        stake = self.take_bet()
        if not stake:
            return
        self.stake = stake
        self.drawn = random.sample(range(1, 81), 20)
        self.shown = 0
        self.timer = 0.0
        self.state = "drawing"
        self.message = ""

    def settle(self):
        hits = len(self.picks & set(self.drawn))
        mult = KENO_PAY[len(self.picks)].get(hits, 0)
        ret = int(self.stake * mult + 1e-9)
        self.state = "result"
        if hits >= 6:
            self.app.unlock("keno_master")
        self.finish(self.stake, ret, f"{hits} HIT{'S' if hits != 1 else ''}!  x{mult:g}  -  YOU WIN {money(ret)}",
                    f"{hits} HIT{'S' if hits != 1 else ''}  -  " +
                    (f"YOU GET BACK {money(ret)}" if ret else f"YOU LOSE {money(self.stake)}"))

    def handle(self, e):
        if self.busy():
            return
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN):
            self.play()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.chip_click(e.pos):
                return
            if self.btn_play.clicked(e.pos):
                self.play()
            elif self.btn_clear.clicked(e.pos):
                self.bet = 0
            elif self.btn_quick.clicked(e.pos):
                n = len(self.picks) or 5
                self.picks = set(random.sample(range(1, 81), n))
                self.drawn = []
                self.state = "betting"
                self.app.sfx("chip")
            elif self.btn_wipe.clicked(e.pos):
                self.picks = set()
                self.drawn = []
                self.state = "betting"
            else:
                for n in range(1, 81):
                    if self.cell(n).collidepoint(e.pos):
                        if self.state == "result":
                            self.drawn = []
                            self.state = "betting"
                        if n in self.picks:
                            self.picks.discard(n)
                        elif len(self.picks) < 10:
                            self.picks.add(n)
                        else:
                            self.message = "YOU CAN PICK UP TO 10 NUMBERS"
                        self.app.sfx("chip")
                        return

    def update(self, dt):
        self.t += dt
        if self.state == "drawing":
            self.timer += dt
            if self.timer >= 0.16:
                self.timer = 0.0
                self.shown += 1
                n = self.drawn[self.shown - 1]
                self.app.sfx("chip" if n in self.picks else "card")
                if n in self.picks:
                    r = self.cell(n)
                    self.app.effects.burst(r.centerx, r.centery, 8, [(255, 230, 120), (255, 255, 255)])
                if self.shown >= 20:
                    self.settle()

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        board = pygame.Rect(KENO_X - 14, KENO_Y - 14, 10 * (KENO_W + 6) + 22, 8 * (KENO_H + 6) + 22)
        soft_panel(surf, board, 120, (60, 90, 140))
        drawn_now = set(self.drawn[:self.shown])
        pulse = 0.5 + 0.5 * math.sin(self.t * 6)
        for n in range(1, 81):
            r = self.cell(n)
            picked, drawn = n in self.picks, n in drawn_now
            if picked and drawn:
                col, tc = lerp_col((60, 200, 90), (140, 255, 160), pulse), (10, 30, 10)
            elif picked:
                col, tc = (230, 180, 40), (40, 25, 5)
            elif drawn:
                col, tc = (50, 110, 210), WHITE
            else:
                col, tc = ((40, 60, 100) if r.collidepoint(mouse) and not self.busy() else (22, 34, 62)), (170, 190, 220)
            pygame.draw.rect(surf, col, r, border_radius=10)
            if drawn:
                pygame.draw.circle(surf, WHITE, r.center, 20, width=2)
            draw_text(surf, str(n), font(20, bold=True), tc, r.center)
        # right panel: paytable for the current number of picks
        panel = pygame.Rect(710, 82, 540, 424)
        soft_panel(surf, panel, 140, (60, 90, 140))
        n = len(self.picks)
        draw_text(surf, f"YOUR PICKS: {n} / 10", font(22, bold=True), GOLD, (panel.centerx, 110))
        hits = len(self.picks & drawn_now)
        if n:
            draw_text(surf, "HITS", font(14, bold=True), (170, 190, 220), (panel.x + 140, 146))
            draw_text(surf, "PAYS", font(14, bold=True), (170, 190, 220), (panel.right - 140, 146))
            rows = sorted(KENO_PAY[n].items(), reverse=True)
            stake = self.stake if self.state != "betting" else self.bet
            for k, (h, m) in enumerate(rows):
                y = 176 + k * 30
                active = self.state == "result" and h == hits
                if active:
                    pygame.draw.rect(surf, (30, 140, 60), (panel.x + 40, y - 14, panel.w - 80, 28), border_radius=6)
                draw_text(surf, f"{h} of {n}", font(18, bold=True), WHITE, (panel.x + 140, y))
                draw_text(surf, f"{m:g}x" + (f"   {money(int(stake * m))}" if stake else ""), font(18, bold=True),
                          GOLD, (panel.right - 140, y))
        else:
            draw_text(surf, "Click numbers on the board to pick them.", font(16), (200, 210, 230), (panel.centerx, 200))
            draw_text(surf, "20 balls are drawn - the more you match,", font(16), (200, 210, 230), (panel.centerx, 230))
            draw_text(surf, "the more you win.", font(16), (200, 210, 230), (panel.centerx, 256))
        if self.drawn:
            draw_text(surf, f"DRAWN {self.shown}/20      HITS {hits}", font(18, bold=True), WHITE, (panel.centerx, 486))
        self.btn_quick.draw(surf, mouse, not self.busy())
        self.btn_wipe.draw(surf, mouse, not self.busy() and bool(self.picks))
        if self.message:
            draw_pill(surf, self.message, font(15, bold=True), (W / 2, 604), GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(14, 4))
        self.btn_play.text = "PLAY AGAIN" if self.state == "result" else "PLAY"
        self.draw_bottom(surf, mouse, self.btn_play, 0 < self.bet <= self.app.balance and bool(self.picks)
                         and not self.busy(), (6, 12, 26))
        self.app.draw_top_bar(surf, "KENO", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Sic Bo (three dice)
# --------------------------------------------------------------------------
SICBO_TOTAL_PAY = {4: 60, 17: 60, 5: 30, 16: 30, 6: 17, 15: 17, 7: 12, 14: 12, 8: 8, 13: 8, 9: 6, 12: 6, 10: 6, 11: 6}


def sicbo_regions():
    r = {"small": pygame.Rect(40, 150, 240, 72), "odd": pygame.Rect(290, 150, 150, 72),
         "anytriple": pygame.Rect(450, 150, 380, 72), "even": pygame.Rect(840, 150, 150, 72),
         "big": pygame.Rect(1000, 150, 240, 72)}
    for i in range(6):
        r[f"double{i + 1}"] = pygame.Rect(40 + i * 100, 232, 96, 68)
        r[f"triple{i + 1}"] = pygame.Rect(640 + i * 100, 232, 96, 68)
    for i, t in enumerate(range(4, 18)):
        r[f"total{t}"] = pygame.Rect(40 + i * 86, 310, 82, 62)
    for i in range(6):
        r[f"single{i + 1}"] = pygame.Rect(40 + i * 200, 382, 196, 70)
    return r


class SicBo(AreaBetGame):
    key = "sicbo"

    def __init__(self, app):
        super().__init__(app, ((20, 6, 6), (110, 60, 50)))
        self.regions = sicbo_regions()
        self.bg = felt_table((90, 20, 20), (150, 40, 35), 300, 9)
        line = (240, 220, 170)
        for key, r in self.regions.items():
            pygame.draw.rect(self.bg, darken((150, 40, 35), 30), r, border_radius=10)
            pygame.draw.rect(self.bg, line, r, width=2, border_radius=10)
            if key in ("small", "big"):
                draw_text(self.bg, key.upper(), font(28, bold=True, serif=True), WHITE, (r.centerx, r.y + 26))
                draw_text(self.bg, "4 - 10  PAYS 1:1" if key == "small" else "11 - 17  PAYS 1:1", font(12, bold=True),
                          (240, 220, 170), (r.centerx, r.y + 54))
            elif key in ("odd", "even"):
                draw_text(self.bg, key.upper(), font(24, bold=True, serif=True), WHITE, (r.centerx, r.y + 26))
                draw_text(self.bg, "PAYS 1:1", font(12, bold=True), (240, 220, 170), (r.centerx, r.y + 54))
            elif key == "anytriple":
                draw_text(self.bg, "ANY TRIPLE", font(22, bold=True, serif=True), WHITE, (r.centerx, r.y + 22))
                for i in range(6):
                    self.bg.blit(make_die(i + 1, 18), (r.x + 42 + i * 52, r.y + 40))
                draw_text(self.bg, "30:1", font(12, bold=True), (240, 220, 170), (r.right - 26, r.y + 22))
            elif key.startswith("double") or key.startswith("triple"):
                n, k = int(key[-1]), (2 if key.startswith("double") else 3)
                for j in range(k):
                    self.bg.blit(make_die(n, 20), (r.centerx - k * 11 + j * 22, r.y + 10))
                draw_text(self.bg, "10:1" if k == 2 else "180:1", font(13, bold=True), (240, 220, 170),
                          (r.centerx, r.bottom - 14))
            elif key.startswith("total"):
                t = int(key[5:])
                draw_text(self.bg, str(t), font(24, bold=True, serif=True), WHITE, (r.centerx, r.y + 22))
                draw_text(self.bg, f"{SICBO_TOTAL_PAY[t]}:1", font(12, bold=True), (240, 220, 170),
                          (r.centerx, r.bottom - 13))
            else:
                n = int(key[-1])
                self.bg.blit(make_die(n, 34), (r.x + 22, r.centery - 17))
                draw_text(self.bg, "1:1 / 2:1 / 3:1", font(12, bold=True), (240, 220, 170), (r.x + 124, r.centery))
        draw_text(self.bg, "ONE DIE PAYS 1:1  -  TWO DICE 2:1  -  THREE DICE 3:1", font(12, bold=True), line,
                  (W / 2, 466))
        # bowl
        pygame.draw.ellipse(self.bg, (20, 8, 8), (W / 2 - 150, 68, 300, 76))
        pygame.draw.ellipse(self.bg, (60, 20, 20), (W / 2 - 146, 70, 292, 70))
        pygame.draw.ellipse(self.bg, GOLD_DARK, (W / 2 - 150, 68, 300, 76), width=3)
        self.btn_main = Button((965, 646, 270, 62), "ROLL DICE", (25, 120, 60), 26, "SPACE")
        self.dice = [1, 2, 3]
        self.show = [1, 2, 3]
        self.anim = None
        self.rolls = []

    def region_at(self, pos):
        for key, r in self.regions.items():
            if r.collidepoint(pos):
                return key
        return None

    def roll(self):
        if not self.begin():
            return
        self.dice = [random.randint(1, 6) for _ in range(3)]
        self.anim = {"t": 0.0, "next": 0.0}
        self.app.sfx("launch")

    def payout(self, key, amt, d):
        total, triple = sum(d), d[0] == d[1] == d[2]
        if key == "small":
            return amt * 2 if 4 <= total <= 10 and not triple else 0
        if key == "big":
            return amt * 2 if 11 <= total <= 17 and not triple else 0
        if key == "odd":
            return amt * 2 if total % 2 and not triple else 0
        if key == "even":
            return amt * 2 if total % 2 == 0 and not triple else 0
        if key == "anytriple":
            return amt * 31 if triple else 0
        if key.startswith("double"):
            return amt * 11 if d.count(int(key[-1])) >= 2 else 0
        if key.startswith("triple"):
            return amt * 181 if d.count(int(key[-1])) == 3 else 0
        if key.startswith("total"):
            t = int(key[5:])
            return amt * (SICBO_TOTAL_PAY[t] + 1) if total == t else 0
        c = d.count(int(key[-1]))
        return amt * (c + 1) if c else 0

    def resolve(self):
        d = self.dice
        self.rolls = ([tuple(d)] + self.rolls)[:8]
        returns = {k: self.payout(k, a, d) for k, a in self.bets.items()}
        if d[0] == d[1] == d[2] and any(returns.get(k) for k in self.bets if "triple" in k):
            self.app.unlock("triple_threat")
        total = sum(d)
        kind = "TRIPLE!" if d[0] == d[1] == d[2] else ("BIG" if total >= 11 else "SMALL")
        self.settle(returns, f"{d[0]} - {d[1]} - {d[2]}  =  {total}  {kind}")

    def handle(self, e):
        self.handle_common(e, self.region_at, self.roll)

    def update(self, dt):
        if self.anim:
            a = self.anim
            a["t"] += dt
            a["next"] -= dt
            if a["t"] < 1.0 and a["next"] <= 0:
                self.show = [random.randint(1, 6) for _ in range(3)]
                a["next"] = 0.07
                if random.random() < 0.4:
                    self.app.sfx("card")
            if a["t"] >= 1.0:
                self.show = list(self.dice)
            if a["t"] >= 1.25:
                self.anim = None
                self.resolve()

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        for i in range(3):
            shake = (random.uniform(-10, 10), random.uniform(-6, 6)) if self.anim and self.anim["t"] < 1 else (0, 0)
            img = pygame.transform.rotozoom(make_die(self.show[i], 46), (i - 1) * 14 + shake[0] * 3, 1)
            surf.blit(img, img.get_rect(center=(W / 2 - 70 + i * 70 + shake[0], 106 + shake[1])))
        draw_text(surf, "LAST ROLLS", font(12, bold=True), GOLD, (1110, 80))
        for i, d in enumerate(self.rolls[:6]):
            s = sum(d)
            tag = "T" if d[0] == d[1] == d[2] else ("B" if s >= 11 else "S")
            col = {"T": (40, 150, 70), "B": (200, 40, 50), "S": (40, 90, 200)}[tag]
            x = 1000 + i * 40
            pygame.draw.circle(surf, col, (x + 20, 112), 16)
            draw_text(surf, str(s), font(13, bold=True), WHITE, (x + 20, 112))
        if not self.busy():
            key = self.region_at(mouse)
            if key:
                r = self.regions[key]
                hl = pygame.Surface(r.size, pygame.SRCALPHA)
                pygame.draw.rect(hl, (255, 255, 255, 45), hl.get_rect(), border_radius=10)
                surf.blit(hl, r)
        if self.state == "result":
            for key, r in self.regions.items():
                if self.won.get(key):
                    pygame.draw.rect(surf, GOLD, r.inflate(4, 4), width=3, border_radius=12)
        for key, r in self.regions.items():
            self.draw_bet_stack(surf, key, (r.right - 20, r.y + 22), 13)
        draw_text(surf, f"TOTAL BET: {money(self.total_bet)}", font(15, bold=True), WHITE, (W / 2, 500))
        if self.message:
            draw_pill(surf, self.message, font(17, bold=True), (W / 2, 540), GOLD, (0, 0, 0, 200), GOLD_DARK)
        self.btn_main.text = "ROLL AGAIN" if self.state == "result" else "ROLL DICE"
        self.draw_bottom(surf, mouse, (20, 8, 8))
        self.app.draw_top_bar(surf, "SIC BO", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Dragon Tiger
# --------------------------------------------------------------------------
DT_VALUE = {r: i + 1 for i, r in enumerate(RANKS)}     # Ace low (1) ... King high (13)


def dt_regions():
    r, x = {}, 92
    for key, w in (("dragon", 330), ("tie", 200), ("suited", 200), ("tiger", 330)):
        r[key] = pygame.Rect(x, 390, w, 110)
        x += w + 12
    return r


def draw_emblem(surf, cx, cy, rad, dragon):
    base = (190, 30, 40) if dragon else (230, 130, 20)
    pygame.draw.circle(surf, (30, 10, 10), (cx + 3, cy + 4), rad)
    pygame.draw.circle(surf, base, (cx, cy), rad)
    pygame.draw.circle(surf, GOLD, (cx, cy), rad, width=4)
    if dragon:      # gold scales
        for row in range(4):
            for col in range(4):
                x = cx - rad * 0.55 + col * rad * 0.37 + (row % 2) * rad * 0.18
                y = cy - rad * 0.5 + row * rad * 0.32
                if math.hypot(x - cx, y - cy) < rad * 0.72:
                    pygame.draw.arc(surf, (255, 210, 110), (x - 12, y - 10, 24, 20), math.pi, 2 * math.pi, 3)
    else:           # tiger stripes
        for k in range(-2, 3):
            pts = [(cx + k * rad * 0.28 - 6, cy - rad * 0.7), (cx + k * rad * 0.28 + 8, cy - rad * 0.1),
                   (cx + k * rad * 0.28 - 4, cy + rad * 0.6)]
            pygame.draw.lines(surf, (30, 20, 10), False, pts, 6)


class DragonTiger(AreaBetGame):
    key = "dragontiger"

    def __init__(self, app):
        super().__init__(app, ((18, 6, 6), (110, 70, 40)))
        self.regions = dt_regions()
        self.bg = felt_table((50, 10, 14), (110, 24, 30), 280, 13)
        draw_emblem(self.bg, 250, 180, 70, True)
        draw_emblem(self.bg, W - 250, 180, 70, False)
        draw_text(self.bg, "DRAGON", font(34, bold=True, serif=True), (255, 120, 120), (250, 285), shadow=(0, 0, 0))
        draw_text(self.bg, "TIGER", font(34, bold=True, serif=True), (255, 190, 90), (W - 250, 285), shadow=(0, 0, 0))
        for cx in (470, W - 470):
            pygame.draw.rect(self.bg, (150, 60, 60), (cx - CW * 0.65, 110, CW * 1.3, CH * 1.3), width=2, border_radius=10)
        labels = {"dragon": ("DRAGON", "PAYS 1:1", (170, 30, 40)), "tie": ("TIE", "PAYS 8:1", (30, 120, 60)),
                  "suited": ("SUITED TIE", "PAYS 50:1", (120, 90, 20)), "tiger": ("TIGER", "PAYS 1:1", (200, 110, 20))}
        for key, r in self.regions.items():
            title, sub, col = labels[key]
            pygame.draw.rect(self.bg, darken(col, 30), r, border_radius=16)
            pygame.draw.rect(self.bg, (240, 215, 160), r, width=2, border_radius=16)
            draw_text(self.bg, title, font(26 if key in ("dragon", "tiger") else 20, bold=True, serif=True), WHITE,
                      (r.centerx, r.y + 30))
            draw_text(self.bg, sub, font(12, bold=True), (240, 220, 170), (r.centerx, r.y + 56))
        draw_text(self.bg, "HIGHEST CARD WINS  -  ACE IS LOW, KING IS HIGH  -  ON A TIE, DRAGON AND TIGER BETS LOSE HALF",
                  font(12, bold=True), (240, 200, 180), (W / 2, 516))
        self.btn_main = Button((965, 646, 270, 62), "DEAL", (25, 120, 60), 28, "SPACE")
        self.shoe = Shoe(8)
        self.cards = [None, None]       # (rank, suit)
        self.face = [False, False]
        self.flip = [None, None]
        self.queue = []
        self.results = []
        self.winner = None

    def busy(self):
        return self.state == "playing"

    def region_at(self, pos):
        for key, r in self.regions.items():
            if r.collidepoint(pos):
                return key
        return None

    def deal(self):
        if not self.begin():
            return
        if self.shoe.needs_shuffle():
            self.shoe.shuffle()
        self.cards = [self.shoe.draw(), self.shoe.draw()]
        self.face = [False, False]
        self.winner = None
        self.queue = [[0.4, lambda: self.reveal(0)], [0.9, lambda: self.reveal(1)], [0.7, self.resolve]]
        self.app.sfx("card")

    def reveal(self, i):
        self.flip[i] = 0.0
        self.app.sfx("card")

    def resolve(self):
        (dr, ds), (tr, ts) = self.cards
        dv, tv = DT_VALUE[dr], DT_VALUE[tr]
        self.winner = "D" if dv > tv else "T" if tv > dv else "X"
        self.results = (self.results + [self.winner])[-48:]
        suited = self.winner == "X" and ds == ts
        ret = {}
        for key, amt in self.bets.items():
            if key == "dragon":
                ret[key] = amt * 2 if self.winner == "D" else amt // 2 if self.winner == "X" else 0
            elif key == "tiger":
                ret[key] = amt * 2 if self.winner == "T" else amt // 2 if self.winner == "X" else 0
            elif key == "tie":
                ret[key] = amt * 9 if self.winner == "X" else 0
            else:
                ret[key] = amt * 51 if suited else 0
        if self.winner == "X" and ret.get("tie"):
            self.app.unlock("tiger_tamer")
        head = {"D": f"DRAGON WINS {dr} OVER {tr}", "T": f"TIGER WINS {tr} OVER {dr}",
                "X": f"{'SUITED ' if suited else ''}TIE - BOTH {dr}"}[self.winner]
        self.settle(ret, head)

    def handle(self, e):
        self.handle_common(e, self.region_at, self.deal)

    def update(self, dt):
        if self.queue:
            self.queue[0][0] -= dt
            if self.queue[0][0] <= 0:
                _, fn = self.queue.pop(0)
                fn()
        for i in range(2):
            if self.flip[i] is not None:
                self.flip[i] += dt * 3.5
                if self.flip[i] >= 0.5:
                    self.face[i] = True
                if self.flip[i] >= 1:
                    self.flip[i] = None

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        cw, ch = int(CW * 1.3), int(CH * 1.3)
        for i, cx in enumerate((470, W - 470)):
            if not self.cards[i]:
                continue
            img = self.assets.faces[self.cards[i]] if self.face[i] else self.assets.back
            w = cw
            if self.flip[i] is not None:
                w = max(2, int(cw * abs(math.cos(math.pi * self.flip[i]))))
            surf.blit(pygame.transform.smoothscale(img, (w, ch)), (cx - w / 2, 110))
            if self.face[i] and self.state == "result":
                draw_pill(surf, str(DT_VALUE[self.cards[i][0]]), font(20, bold=True), (cx, 110 + ch + 20), WHITE,
                          (0, 0, 0, 200), GOLD, pad=(12, 2))
        if self.winner and self.state == "result":
            x = {"D": 250, "T": W - 250, "X": W / 2}[self.winner]
            draw_pill(surf, "WINNER" if self.winner != "X" else "TIE!", font(18, bold=True), (x, 94 if self.winner != "X" else 250),
                      (20, 20, 20), (*GOLD, 255), None, pad=(14, 3))
        # scoreboard
        board = pygame.Rect(W / 2 - 96, 96, 192, 132)
        pygame.draw.rect(surf, (245, 240, 230), board, border_radius=8)
        pygame.draw.rect(surf, GOLD_DARK, board, width=2, border_radius=8)
        for i, r in enumerate(self.results[-48:]):
            col, row = divmod(i, 6)
            c = (board.x + 14 + col * 23.5, board.y + 13 + row * 21)
            pygame.draw.circle(surf, {"D": (200, 40, 50), "T": (230, 130, 20), "X": (40, 150, 70)}[r], c, 9)
            draw_text(surf, {"D": "D", "T": "T", "X": "="}[r], font(10, bold=True), WHITE, c)
        if not self.busy():
            key = self.region_at(mouse)
            if key:
                r = self.regions[key]
                hl = pygame.Surface(r.size, pygame.SRCALPHA)
                pygame.draw.rect(hl, (255, 255, 255, 45), hl.get_rect(), border_radius=16)
                surf.blit(hl, r)
        for key, r in self.regions.items():
            self.draw_bet_stack(surf, key, (r.centerx, r.bottom - 26), 15)
        if self.message:
            draw_pill(surf, self.message, font(17, bold=True), (W / 2, 350), GOLD, (0, 0, 0, 200), GOLD_DARK)
        draw_text(surf, f"TOTAL BET: {money(self.total_bet)}", font(15, bold=True), WHITE, (W / 2, 598))
        self.btn_main.text = "DEAL AGAIN" if self.state == "result" else "DEAL"
        self.draw_bottom(surf, mouse, (18, 6, 8))
        self.app.draw_top_bar(surf, "DRAGON TIGER", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Lottery - a draw every 5 minutes on the real clock, even while you play other games
# --------------------------------------------------------------------------
LOTTO_EVERY = 300
LOTTO_PRICE = 10
LOTTO_PRIZES = {2: 20, 3: 100, 4: 2000, 5: 100000}
LOTTO_MAX_TICKETS = 20


def lotto_numbers(draw_no):
    return sorted(random.Random(f"grand-royale-lotto-{draw_no}").sample(range(1, 37), 5))


class Lottery:
    key = "lottery"

    def __init__(self, app, state=None):
        self.app = app
        self.assets = app.assets
        self.bg = gradient_bg((20, 50, 40), (4, 14, 12))
        state = state or {}
        self.tickets = [dict(t) for t in state.get("tickets", [])]
        self.history = list(state.get("history", []))[:8]
        self.picks = set()
        self.anim = None           # the newest draw's balls popping in
        self.t = 0.0
        self.message = "PICK 5 NUMBERS FROM 1 TO 36, THEN BUY A TICKET"
        self.btn_buy = Button((965, 646, 270, 62), f"BUY TICKET  {money(LOTTO_PRICE)}", (25, 120, 60), 22, "SPACE")
        self.btn_quick = Button((40, 652, 170, 52), "QUICK PICK", (40, 90, 160), 18)
        self.btn_wipe = Button((220, 652, 150, 52), "CLEAR", (120, 35, 40), 18)
        self.check()                 # settle any draws that happened while the game was closed

    def to_state(self):
        return {"tickets": self.tickets, "history": self.history}

    def can_leave(self):
        return True

    def outstanding_bets(self):
        return 0

    def leave(self):
        pass

    @staticmethod
    def next_draw():
        return int(time.time() // LOTTO_EVERY) + 1

    def cell(self, n):
        r, c = divmod(n - 1, 6)
        return pygame.Rect(40 + c * 62, 150 + r * 62, 56, 56)

    def buy(self):
        if len(self.picks) != 5:
            self.message = "PICK EXACTLY 5 NUMBERS"
            return
        draw_no = self.next_draw()
        if sum(1 for t in self.tickets if t["draw"] == draw_no) >= LOTTO_MAX_TICKETS:
            self.message = f"MAX {LOTTO_MAX_TICKETS} TICKETS PER DRAW"
            return
        if self.app.balance < LOTTO_PRICE:
            self.message = "NOT ENOUGH CHIPS"
            return
        self.app.balance -= LOTTO_PRICE
        self.tickets.append({"draw": draw_no, "nums": sorted(self.picks)})
        self.message = f"TICKET BOUGHT FOR DRAW #{draw_no} - GOOD LUCK!"
        self.app.sfx("chip")
        self.app.save()

    def check(self):
        """Settle every ticket whose draw time has passed. Runs every frame from the app."""
        now_draw = int(time.time() // LOTTO_EVERY)
        due = sorted({t["draw"] for t in self.tickets if t["draw"] <= now_draw})
        for draw_no in due:
            nums = lotto_numbers(draw_no)
            mine = [t for t in self.tickets if t["draw"] == draw_no]
            won = 0
            for t in mine:
                m = len(set(t["nums"]) & set(nums))
                won += LOTTO_PRIZES.get(m, 0)
            self.tickets = [t for t in self.tickets if t["draw"] != draw_no]
            self.history = ([{"draw": draw_no, "nums": nums, "tickets": len(mine), "won": won}] + self.history)[:8]
            self.app.balance += won
            self.app.record(self.key, len(mine) * LOTTO_PRICE, won)
            self.anim = {"draw": draw_no, "t": 0.0}
            if won:
                self.app.unlock("lucky_ticket")
                self.app.effects.toast("LOTTERY WIN!", f"Draw #{draw_no}: you won {money(won)}")
                self.app.float_text(f"+{money(won)}", (80, 230, 110))
            else:
                self.app.effects.toast(f"LOTTERY DRAW #{draw_no}", f"No luck on {len(mine)} ticket"
                                                                     f"{'s' if len(mine) != 1 else ''} this time")
        if due:
            self.app.save()

    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN):
            self.buy()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.btn_buy.clicked(e.pos):
                self.buy()
            elif self.btn_quick.clicked(e.pos):
                self.picks = set(random.sample(range(1, 37), 5))
                self.app.sfx("chip")
            elif self.btn_wipe.clicked(e.pos):
                self.picks = set()
            else:
                for n in range(1, 37):
                    if self.cell(n).collidepoint(e.pos):
                        if n in self.picks:
                            self.picks.discard(n)
                        elif len(self.picks) < 5:
                            self.picks.add(n)
                        self.app.sfx("chip")

    def update(self, dt):
        self.t += dt
        if self.anim:
            self.anim["t"] += dt
            if self.anim["t"] > 12:
                self.anim = None

    @staticmethod
    def ball(surf, n, c, r, hit=False):
        col = (230, 180, 40) if hit else (245, 245, 240)
        pygame.draw.circle(surf, (0, 0, 0), (c[0] + 2, c[1] + 3), r)
        pygame.draw.circle(surf, col, c, r)
        pygame.draw.circle(surf, (255, 255, 255), (c[0] - r * 0.35, c[1] - r * 0.35), r * 0.25)
        draw_text(surf, str(n), font(int(r * 0.9), bold=True), (30, 30, 30), c)

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        soft_panel(surf, pygame.Rect(24, 80, 402, 546), 130, (60, 120, 90))
        draw_text(surf, f"PICK 5 NUMBERS  ({len(self.picks)}/5)", font(18, bold=True), GOLD, (225, 118))
        for n in range(1, 37):
            r = self.cell(n)
            on = n in self.picks
            col = (230, 180, 40) if on else ((40, 90, 70) if r.collidepoint(mouse) else (20, 50, 40))
            pygame.draw.rect(surf, col, r, border_radius=28)
            pygame.draw.rect(surf, (90, 160, 120), r, width=2, border_radius=28)
            draw_text(surf, str(n), font(20, bold=True), (40, 25, 5) if on else WHITE, r.center)
        draw_text(surf, f"Each ticket costs {money(LOTTO_PRICE)}.", font(14), (190, 220, 200), (225, 540))
        draw_text(surf, f"Up to {LOTTO_MAX_TICKETS} tickets per draw.", font(14), (190, 220, 200), (225, 562))
        # countdown + prizes
        mid = pygame.Rect(440, 80, 400, 546)
        soft_panel(surf, mid, 130, (60, 120, 90))
        left = LOTTO_EVERY - (time.time() % LOTTO_EVERY)
        draw_text(surf, f"DRAW #{self.next_draw()}", font(16, bold=True), (190, 220, 200), (mid.centerx, 110))
        draw_text(surf, "NEXT DRAW IN", font(14, bold=True), GOLD, (mid.centerx, 138))
        draw_text(surf, f"{int(left // 60)}:{int(left % 60):02d}", font(56, bold=True), WHITE, (mid.centerx, 184))
        draw_text(surf, "PRIZES PER TICKET", font(14, bold=True), GOLD, (mid.centerx, 240))
        for k, (m, p) in enumerate(sorted(LOTTO_PRIZES.items(), reverse=True)):
            y = 268 + k * 28
            draw_text(surf, f"Match {m}", font(17, bold=True), WHITE, (mid.x + 90, y), anchor="midleft")
            draw_text(surf, money(p) + ("  JACKPOT" if m == 5 else ""), font(17, bold=True),
                      (255, 220, 90) if m == 5 else (150, 230, 170), (mid.right - 60, y), anchor="midright")
        mine = [t for t in self.tickets if t["draw"] == self.next_draw()]
        draw_text(surf, f"YOUR TICKETS FOR THIS DRAW: {len(mine)}", font(14, bold=True), GOLD, (mid.centerx, 400))
        for k, tk in enumerate(mine[:7]):
            y = 428 + k * 27
            for j, n in enumerate(tk["nums"]):
                self.ball(surf, n, (mid.x + 110 + j * 40, y), 12)
        if len(mine) > 7:
            draw_text(surf, f"+ {len(mine) - 7} more", font(13), (190, 220, 200), (mid.centerx, 616))
        # results
        right = pygame.Rect(854, 80, 402, 546)
        soft_panel(surf, right, 130, (60, 120, 90))
        draw_text(surf, "RECENT DRAWS", font(18, bold=True), GOLD, (right.centerx, 110))
        draws = [(h["draw"], h["nums"], h.get("won"), h.get("tickets", 0)) for h in self.history]
        known = {d for d, *_ in draws}
        latest = int(time.time() // LOTTO_EVERY)
        for d in range(latest, latest - 8, -1):         # show public results even with no tickets bought
            if d not in known and len(draws) < 8:
                draws.append((d, lotto_numbers(d), None, 0))
        draws = sorted(draws, reverse=True)[:7]
        for k, (d, nums, won, count) in enumerate(draws):
            y = 150 + k * 66
            draw_text(surf, f"#{d}", font(14, bold=True), (190, 220, 200), (right.x + 22, y), anchor="midleft")
            show = 5
            if self.anim and self.anim["draw"] == d:
                show = min(5, int(self.anim["t"] / 0.7))
            for j, n in enumerate(nums[:show]):
                self.ball(surf, n, (right.x + 110 + j * 44, y), 17, hit=False)
            if count:
                txt = f"{count} ticket{'s' if count != 1 else ''}: " + (f"WON {money(won)}" if won else "no win")
                draw_text(surf, txt, font(13, bold=True), (120, 240, 140) if won else (200, 170, 170),
                          (right.x + 110, y + 28), anchor="midleft")
        if self.message:
            draw_pill(surf, self.message, font(15, bold=True), (640, 612), GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(14, 4))
        bottom_bar(surf, (6, 16, 12))
        self.btn_quick.draw(surf, mouse)
        self.btn_wipe.draw(surf, mouse, bool(self.picks))
        draw_text(surf, "The draw happens every 5 minutes - even while you play other games.", font(14),
                  (190, 220, 200), (660, 678))
        self.btn_buy.draw(surf, mouse, len(self.picks) == 5 and self.app.balance >= LOTTO_PRICE)
        self.app.draw_top_bar(surf, "LOTTERY", lobby=True)


# --------------------------------------------------------------------------
# Deep Dive - pilot a submarine down through the real ocean zones
# --------------------------------------------------------------------------
DIVE_RATE = 0.09      # multiplier = e^(rate * seconds), same idea as the Rocket
DIVE_POINTS = [(0, 1.0), (200, 1.3), (1000, 2.0), (4000, 4.0), (6000, 10.0), (10935, 50.0)]   # depth -> multiplier
DIVE_ZONES = [
    ("SUNLIGHT ZONE", 0, (70, 170, 225),
     "Enough sunlight reaches here for plants and algae to make food by photosynthesis."),
    ("TWILIGHT ZONE", 200, (20, 70, 140),
     "Only faint blue light gets down here - too little for photosynthesis."),
    ("MIDNIGHT ZONE", 1000, (6, 18, 48),
     "No sunlight at all. Most animals here make their own light - that's called bioluminescence."),
    ("ABYSSAL ZONE", 4000, (3, 7, 22),
     "Near-freezing water and pressure over 400 times what you feel at the surface."),
    ("HADAL ZONE", 6000, (1, 3, 10),
     "Named after Hades. The Challenger Deep is about 10,935 m deep - Mount Everest would fit inside!"),
]
DIVE_CREATURES = {     # zone index -> (name, shape, colour, fact)
    0: [("Sea Turtle", "turtle", (80, 140, 80), "Sea turtles can sleep underwater for hours on a single breath."),
        ("Bottlenose Dolphin", "dolphin", (135, 150, 170), "Dolphins sleep with only half of their brain at a time."),
        ("Clownfish", "fish", (245, 120, 40), "All clownfish start out male - the biggest in a group becomes female."),
        ("Great White Shark", "shark", (125, 135, 150), "Sharks have been around for over 400 million years - longer than trees."),
        ("Blue Whale", "whale", (70, 95, 145), "The blue whale is the biggest animal known to have ever lived.")],
    1: [("Swordfish", "swordfish", (95, 105, 150), "Swordfish warm their eyes and brain so they can see in cold, dark water."),
        ("Bristlemouth", "bristle", (120, 120, 130), "The bristlemouth is probably the most common vertebrate on Earth."),
        ("Atolla Jellyfish", "jelly", (210, 40, 70), "When attacked, the Atolla jellyfish flashes blue light like a burglar alarm."),
        ("Barreleye Fish", "barreleye", (110, 120, 130), "The barreleye fish has a see-through head - you can see its eyes inside it."),
        ("Sperm Whale", "whale", (85, 85, 95), "Sperm whales dive more than 1,000 m deep to hunt squid.")],
    2: [("Anglerfish", "angler", (70, 55, 60), "Anglerfish lure prey with a glowing 'fishing rod' full of light-making bacteria."),
        ("Giant Squid", "squid", (180, 70, 55), "Giant squid have the biggest eyes of any animal - as big as a dinner plate."),
        ("Vampire Squid", "squid", (120, 25, 45), "Despite the name, the vampire squid eats 'marine snow' - drifting bits of dead stuff."),
        ("Gulper Eel", "eel", (45, 45, 55), "The gulper eel's giant mouth can swallow prey bigger than itself.")],
    3: [("Tripod Fish", "tripod", (160, 160, 170), "The tripod fish stands on the sea floor on three long fins, waiting for food."),
        ("Dumbo Octopus", "octopus", (235, 155, 165), "Dumbo octopuses flap ear-like fins and live deeper than any other octopus."),
        ("Sea Pig", "cucumber", (235, 175, 185), "Sea pigs are sea cucumbers that walk the deep sea floor on tube 'legs'.")],
    4: [("Snailfish", "snailfish", (240, 205, 210), "Snailfish have been filmed over 8,300 m deep - the deepest fish ever seen."),
        ("Supergiant Amphipod", "amphipod", (240, 240, 230), "Trench amphipods can grow over 10 times bigger than their shallow cousins."),
        ("Xenophyophore", "blob", (195, 180, 155), "Xenophyophores are single cells that can grow as big as a grapefruit.")],
}
SUB_POS = (560, 340)
_glow_cache = {}


def dive_mult(depth):
    for (d0, m0), (d1, m1) in zip(DIVE_POINTS, DIVE_POINTS[1:]):
        if depth <= d1:
            t = (depth - d0) / (d1 - d0)
            return math.exp(math.log(m0) + (math.log(m1) - math.log(m0)) * t)
    return DIVE_POINTS[-1][1]


def dive_depth(mult):
    for (d0, m0), (d1, m1) in zip(DIVE_POINTS, DIVE_POINTS[1:]):
        if mult <= m1:
            t = (math.log(mult) - math.log(m0)) / (math.log(m1) - math.log(m0))
            return d0 + (d1 - d0) * t
    return DIVE_POINTS[-1][0]


def dive_zone(depth):
    z = 0
    for i, (_, start, _, _) in enumerate(DIVE_ZONES):
        if depth >= start:
            z = i
    return z


def dive_temp(depth):
    pts = [(0, 22), (200, 15), (1000, 4), (4000, 2), (11000, 1.5)]
    for (d0, t0), (d1, t1) in zip(pts, pts[1:]):
        if depth <= d1:
            return t0 + (t1 - t0) * (depth - d0) / (d1 - d0)
    return 1.5


def water_color(depth):
    stops = [(0, (70, 170, 225)), (200, (20, 75, 145)), (1000, (6, 20, 52)), (4000, (3, 8, 24)), (11000, (1, 3, 10))]
    for (d0, c0), (d1, c1) in zip(stops, stops[1:]):
        if depth <= d1:
            return lerp_col(c0, c1, (depth - d0) / (d1 - d0))
    return stops[-1][1]


def glow(color, radius):
    key = (color, radius)
    if key not in _glow_cache:
        s = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        for i in range(12):
            r = radius * (1 - i / 12)
            pygame.draw.circle(s, (*color, 10 + i * 7), (radius, radius), r)
        _glow_cache[key] = s
    return _glow_cache[key]


def draw_creature(surf, kind, x, y, s, col, f, t):
    """Very simple side-view sea creatures. f = 1 swims right, -1 swims left."""
    X = lambda dx: x + dx * s * f
    dark = darken(col, 50)
    wag = math.sin(t * 7) * 4 * s
    if kind in ("fish", "shark", "dolphin", "whale", "swordfish", "snailfish", "bristle", "barreleye", "tripod"):
        L, Hh = {"fish": (34, 18), "shark": (80, 24), "dolphin": (70, 20), "whale": (160, 44), "swordfish": (64, 16),
                 "snailfish": (40, 16), "bristle": (26, 8), "barreleye": (34, 16), "tripod": (44, 12)}[kind]
        if kind == "tripod":
            for k in (-0.35, 0.05, 0.3):
                pygame.draw.line(surf, lighten(col, 30), (X(L * k), y + Hh * s * 0.3), (X(L * k * 1.4), y + 55 * s), 2)
        pygame.draw.polygon(surf, dark, [(X(-L * 0.4), y), (X(-L * 0.7), y - Hh * s * 0.8 + wag),
                                         (X(-L * 0.7), y + Hh * s * 0.8 + wag)])
        pygame.draw.ellipse(surf, col, (x - L * s / 2, y - Hh * s / 2, L * s, Hh * s))
        if kind in ("shark", "dolphin", "whale"):
            pygame.draw.polygon(surf, dark, [(X(-L * 0.05), y - Hh * s * 0.4), (X(L * 0.05), y - Hh * s * 0.95),
                                             (X(L * 0.18), y - Hh * s * 0.4)])
            pygame.draw.ellipse(surf, lighten(col, 35), (x - L * s * 0.3, y, L * s * 0.6, Hh * s * 0.4))
        if kind == "dolphin":
            pygame.draw.line(surf, col, (X(L * 0.45), y), (X(L * 0.62), y + 2 * s), max(2, int(5 * s)))
        if kind == "swordfish":
            pygame.draw.line(surf, lighten(col, 40), (X(L * 0.45), y - 2 * s), (X(L * 1.0), y - 3 * s), max(2, int(3 * s)))
        if kind == "fish":
            for k in (-0.1, 0.18):
                pygame.draw.line(surf, WHITE, (X(L * k), y - Hh * s * 0.45), (X(L * k), y + Hh * s * 0.45), max(2, int(4 * s)))
        if kind == "bristle":
            for k in range(4):
                surf.blit(glow((120, 200, 255), 5), (X(-L * 0.3 + k * L * 0.18) - 5, y + Hh * s * 0.2 - 5))
        if kind == "barreleye":
            pygame.draw.circle(surf, (190, 230, 255), (X(L * 0.2), y - Hh * s * 0.35), Hh * s * 0.7, width=2)
            pygame.draw.circle(surf, (80, 230, 110), (X(L * 0.2), y - Hh * s * 0.45), 3 * s)
        pygame.draw.circle(surf, WHITE, (X(L * 0.3), y - Hh * s * 0.12), max(2, 3 * s))
        pygame.draw.circle(surf, (10, 10, 10), (X(L * 0.32), y - Hh * s * 0.12), max(1, 1.6 * s))
    elif kind == "turtle":
        for k, dy in ((0.25, -1), (0.25, 1), (-0.25, -1), (-0.25, 1)):
            fl = math.sin(t * 4 + k * 5) * 5 * s
            pygame.draw.ellipse(surf, lighten(col, 20), (X(k * 44) - 9 * s, y + dy * (12 * s + fl) - 5 * s, 18 * s, 10 * s))
        pygame.draw.circle(surf, lighten(col, 20), (X(30), y - 2 * s), 8 * s)
        pygame.draw.ellipse(surf, (110, 85, 50), (x - 26 * s, y - 15 * s, 52 * s, 30 * s))
        for k in (-12, 0, 12):
            pygame.draw.circle(surf, (140, 110, 60), (X(k), y - 3 * s), 6 * s, width=2)
        pygame.draw.circle(surf, (10, 10, 10), (X(34), y - 4 * s), 2 * s)
    elif kind == "jelly":
        surf.blit(glow((90, 150, 255), int(40 * s)), (x - 40 * s, y - 45 * s))
        pygame.draw.circle(surf, col, (x, y), 20 * s, draw_top_left=True, draw_top_right=True)
        for k in range(-3, 4):
            pts = [(x + k * 5 * s + math.sin(t * 3 + j + k) * 4 * s, y + j * 8 * s) for j in range(6)]
            pygame.draw.lines(surf, lighten(col, 30), False, pts, 2)
    elif kind == "squid":
        big = 1.5 if col[0] > 150 else 1.0
        S = s * big
        for k in range(-3, 4):
            pts = [(X(-30 * S - j * 9 * S), y + k * 3 * S + math.sin(t * 4 + j + k) * 5 * S) for j in range(6)]
            pygame.draw.lines(surf, dark, False, pts, max(2, int(3 * S)))
        pygame.draw.ellipse(surf, col, (x - 30 * S, y - 12 * S, 70 * S, 24 * S))
        pygame.draw.polygon(surf, col, [(X(36 * S / s), y), (X(28 * S / s), y - 18 * S), (X(22 * S / s), y)])
        pygame.draw.circle(surf, WHITE, (X(-18 * S / s), y - 2 * S), 6 * S)
        pygame.draw.circle(surf, (10, 10, 10), (X(-18 * S / s), y - 2 * S), 3 * S)
    elif kind == "angler":
        lure = (X(38), y - 34 * s)
        pygame.draw.lines(surf, dark, False, [(X(12), y - 18 * s), (X(28), y - 40 * s), lure], 2)
        surf.blit(glow((180, 255, 200), int(18 * s)), (lure[0] - 18 * s, lure[1] - 18 * s))
        pygame.draw.circle(surf, (230, 255, 230), lure, 4 * s)
        pygame.draw.polygon(surf, dark, [(X(-22), y), (X(-40), y - 14 * s + wag), (X(-40), y + 14 * s + wag)])
        pygame.draw.circle(surf, col, (x, y), 24 * s)
        pygame.draw.polygon(surf, (10, 5, 8), [(X(8), y + 2 * s), (X(26), y - 6 * s), (X(26), y + 14 * s)])
        for k in range(3):
            pygame.draw.polygon(surf, WHITE, [(X(12 + k * 5), y + 1 * s), (X(14 + k * 5), y + 7 * s), (X(16 + k * 5), y + 1 * s)])
        pygame.draw.circle(surf, (230, 230, 200), (X(6), y - 10 * s), 3 * s)
    elif kind == "octopus":
        flap = math.sin(t * 5) * 6 * s
        for dx in (-16, 16):
            pygame.draw.ellipse(surf, lighten(col, 15), (x + dx * s - 9 * s, y - 20 * s - flap, 18 * s, 12 * s))
        for k in range(-3, 4):
            pts = [(x + k * 5 * s + math.sin(t * 3 + j) * 3 * s, y + 10 * s + j * 5 * s) for j in range(4)]
            pygame.draw.lines(surf, col, False, pts, max(2, int(4 * s)))
        pygame.draw.ellipse(surf, col, (x - 18 * s, y - 20 * s, 36 * s, 34 * s))
        pygame.draw.circle(surf, (20, 10, 10), (x - 6 * s, y - 6 * s), 3 * s)
        pygame.draw.circle(surf, (20, 10, 10), (x + 6 * s, y - 6 * s), 3 * s)
    elif kind == "eel":
        pts = [(X(-j * 10), y + math.sin(t * 5 + j * 0.8) * 8 * s) for j in range(10)]
        pygame.draw.lines(surf, col, False, pts, max(2, int(5 * s)))
        pygame.draw.polygon(surf, lighten(col, 20), [(X(0), y), (X(26), y - 18 * s), (X(26), y + 18 * s)])
        pygame.draw.polygon(surf, (5, 5, 8), [(X(4), y), (X(24), y - 12 * s), (X(24), y + 12 * s)])
    elif kind == "cucumber":
        for k in range(5):
            pygame.draw.line(surf, lighten(col, 20), (x - 20 * s + k * 10 * s, y + 8 * s),
                             (x - 22 * s + k * 10 * s, y + 16 * s), 3)
        pygame.draw.ellipse(surf, col, (x - 28 * s, y - 10 * s, 56 * s, 22 * s))
        for k in range(3):
            pygame.draw.line(surf, lighten(col, 20), (x - 10 * s + k * 8 * s, y - 8 * s),
                             (x - 12 * s + k * 8 * s, y - 18 * s), 3)
    elif kind == "amphipod":
        for k in range(6):
            pygame.draw.ellipse(surf, darken(col, k * 6), (X(-24 + k * 8) - 6 * s, y - 8 * s + abs(k - 3) * s, 12 * s, 14 * s))
        pygame.draw.line(surf, col, (X(22), y - 4 * s), (X(40), y - 16 * s), 2)
        pygame.draw.line(surf, col, (X(22), y - 2 * s), (X(42), y - 6 * s), 2)
        for k in range(5):
            pygame.draw.line(surf, darken(col, 40), (X(-18 + k * 8), y + 6 * s), (X(-20 + k * 8), y + 14 * s), 2)
    elif kind == "blob":
        for k in range(7):
            a = k / 7 * 2 * math.pi
            pygame.draw.circle(surf, darken(col, (k % 3) * 15), (x + math.cos(a) * 12 * s, y + math.sin(a) * 9 * s), 11 * s)
        pygame.draw.circle(surf, col, (x, y), 12 * s)


def draw_sub(surf, x, y, t, light, broken=False):
    body = (240, 200, 40) if not broken else (150, 120, 40)
    if light:
        cone = pygame.Surface((520, 260), pygame.SRCALPHA)
        pygame.draw.polygon(cone, (255, 250, 200, 38), [(0, 130), (520, 20), (520, 240)])
        pygame.draw.polygon(cone, (255, 250, 200, 30), [(0, 130), (520, 70), (520, 190)])
        surf.blit(cone, (x + 78, y - 125))
    prop = abs(math.sin(t * 20)) * 18
    pygame.draw.ellipse(surf, (90, 90, 100), (x - 104, y - prop, 12, prop * 2 + 2))
    pygame.draw.rect(surf, darken(body, 60), (x - 96, y - 6, 20, 12), border_radius=4)
    pygame.draw.ellipse(surf, darken(body, 70), (x - 84, y - 30 + 5, 170, 66))
    pygame.draw.ellipse(surf, body, (x - 84, y - 33, 170, 66))
    pygame.draw.rect(surf, body, (x - 22, y - 56, 54, 30), border_radius=10)
    pygame.draw.line(surf, darken(body, 60), (x + 14, y - 56), (x + 14, y - 74), 4)
    pygame.draw.line(surf, darken(body, 60), (x + 14, y - 74), (x + 28, y - 74), 4)
    pygame.draw.ellipse(surf, lighten(body, 40), (x - 60, y - 26, 110, 16))
    for k in range(3):
        c = (x - 36 + k * 34, y + 2)
        pygame.draw.circle(surf, (90, 90, 100), c, 12)
        pygame.draw.circle(surf, (140, 220, 255) if light else (90, 170, 220), c, 9)
        pygame.draw.circle(surf, WHITE, (c[0] - 3, c[1] - 3), 3)
    pygame.draw.circle(surf, (255, 250, 210), (x + 84, y + 2), 7)
    if broken:
        for a, b in (((x - 20, y - 30), (x + 5, y)), ((x + 5, y), (x - 10, y + 24)), ((x + 5, y), (x + 30, y + 10))):
            pygame.draw.line(surf, (40, 20, 10), a, b, 4)


class DeepDive(StakeGame):
    key = "deepdive"

    def __init__(self, app):
        super().__init__(app, ((4, 14, 26), (40, 90, 130)))
        self.state = "surface"          # surface, diving, surfacing, crashed
        self.t = 0.0
        self.clock = 0.0
        self.mult = 1.0
        self.depth = 0.0
        self.fail_at = 99.0
        self.stake = 0
        self.zone = 0
        self.snow = [[random.uniform(0, W), random.uniform(56, 636), random.uniform(1, 2.5)] for _ in range(90)]
        self.bubbles = []
        self.creatures = []
        self.spawn_in = 1.0
        self.last_kind = None
        self.card = None                # (title, text, colour, time left)
        self.shake = 0.0
        self.flash = 0.0
        self.surf_t = 0.0
        self.message = "SET YOUR BET AND DIVE!  SURFACE BEFORE SOMETHING GOES WRONG"
        self.btn_dive = Button((965, 646, 270, 62), "DIVE!", (25, 120, 60), 28, "SPACE")
        self.btn_up = Button((965, 646, 270, 62), "SURFACE", (215, 120, 15), 24, "SPACE")
        self.gauge = pygame.Rect(26, 90, 44, 520)

    def busy(self):
        return self.state in ("diving", "surfacing")

    def outstanding_bets(self):
        return self.stake if self.state == "diving" else 0

    def payout(self):
        return int(self.stake * math.floor(self.mult * 100) / 100 + 1e-9)

    def dive(self):
        if self.busy():
            return
        stake = self.take_bet()
        if not stake:
            return
        self.stake = stake
        u = random.random()
        self.fail_at = max(1.0, math.floor(0.96 / (1 - u) * 100) / 100)     # 96% payback, like the Rocket
        self.state = "diving"
        self.t = 0.0
        self.mult = 1.0
        self.depth = 0.0
        self.zone = 0
        self.creatures = []
        self.spawn_in = 0.8
        self.card = (DIVE_ZONES[0][0], DIVE_ZONES[0][3], DIVE_ZONES[0][2], 5.0)
        self.message = ""
        self.app.sfx("launch")

    def surface(self, bottom=False):
        if self.state != "diving":
            return
        win = self.payout()
        self.state = "surfacing"
        self.surf_t = 0.0
        if bottom:
            self.app.unlock("challenger_deep")
        self.finish(self.stake, win, ("YOU TOUCHED THE BOTTOM OF THE MARIANA TRENCH!  " if bottom else
                                      f"SURFACED SAFELY FROM {int(self.depth):,} m  -  ") + f"YOU WIN {money(win)}")

    def crash(self):
        self.state = "crashed"
        self.shake, self.flash = 0.6, 1.0
        squid_ok = 200 <= self.depth < 6000
        why = random.choice(["A GIANT SQUID GRABBED THE SUB!", "THE HULL CRACKED UNDER THE PRESSURE!"]) if squid_ok \
            else "THE HULL CRACKED UNDER THE PRESSURE!"
        for _ in range(40):
            self.bubbles.append([SUB_POS[0] + random.uniform(-80, 80), SUB_POS[1] + random.uniform(-30, 30),
                                 random.uniform(3, 9), random.uniform(60, 180)])
        self.app.sfx("boom")
        self.finish(self.stake, 0, lose_msg=f"{why}  -  LOST AT {int(self.depth):,} m  -  YOU LOSE {money(self.stake)}")

    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN):
            self.surface() if self.state == "diving" else self.dive()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.state == "diving":
                if self.btn_up.clicked(e.pos):
                    self.surface()
                return
            if self.busy():
                return
            if self.chip_click(e.pos):
                return
            if self.btn_clear.clicked(e.pos):
                self.bet = 0
            elif self.btn_dive.clicked(e.pos):
                self.dive()

    def spawn(self):
        pool = DIVE_CREATURES[self.zone]
        choices = [c for c in pool if c[0] != self.last_kind] or pool
        name, kind, col, fact = random.choice(choices)
        self.last_kind = name
        f = random.choice((1, -1))
        size = {"whale": 1.1, "shark": 1.0}.get(kind, 1.3)
        self.creatures.append({"name": name, "kind": kind, "col": col, "fact": fact, "f": f, "s": size, "zone": self.zone,
                               "x": -120 if f == 1 else W + 120, "y": random.uniform(130, 560),
                               "vx": random.uniform(60, 120) * f, "seen": False})

    def update(self, dt):
        self.clock += dt
        self.shake = max(0.0, self.shake - dt)
        self.flash = max(0.0, self.flash - dt * 2)
        speed = 0.0
        if self.state == "diving":
            self.t += dt
            m = math.exp(DIVE_RATE * self.t)
            if m >= self.fail_at and self.fail_at < 50:
                self.mult = self.fail_at
                self.depth = dive_depth(self.mult)
                self.crash()
            elif m >= 50:
                self.mult = 50.0
                self.depth = DIVE_POINTS[-1][0]
                self.surface(bottom=True)
            else:
                old = self.depth
                self.mult = m
                self.depth = dive_depth(m)
                speed = (self.depth - old) / dt if dt else 0
                z = dive_zone(self.depth)
                if z != self.zone:
                    self.zone = z
                    name, start, col, fact = DIVE_ZONES[z]
                    self.card = (f"{name}  ({start:,} m)", fact, col, 6.0)
                    self.app.sfx("win")
                    if z == 4:
                        self.app.unlock("challenger_deep")
                self.spawn_in -= dt
                if self.spawn_in <= 0:
                    self.spawn()
                    self.spawn_in = random.uniform(2.2, 3.4)
        elif self.state == "surfacing":
            self.surf_t += dt
            self.depth = max(0.0, self.depth * (1 - min(1, dt * 3)))
            speed = -400
            if self.surf_t > 1.6:
                self.state = "surface"
                self.depth = 0.0
                self.zone = 0
                self.creatures = []
        # scenery drifts past: marine snow and creatures rise as the sub sinks
        rise = min(420, speed * 0.6) if speed > 0 else (speed * 0.4 if speed < 0 else 0)
        for p in self.snow:
            p[1] -= (rise + 8) * dt * p[2] * 0.5
            if p[1] < 56:
                p[1], p[0] = 636, random.uniform(0, W)
            elif p[1] > 640:
                p[1], p[0] = 58, random.uniform(0, W)
        for c in self.creatures:
            c["x"] += c["vx"] * dt
            c["y"] -= rise * dt
            if not c["seen"] and 120 < c["x"] < W - 120:
                c["seen"] = True
                self.card = (f"SPOTTED: {c['name'].upper()}", c["fact"], c["col"], 6.5)
        # animals only live in their own zones - anything two zones too shallow has been left behind
        self.creatures = [c for c in self.creatures if -200 < c["x"] < W + 200 and c["y"] > -80
                          and c["zone"] >= self.zone - 1]
        for b in self.bubbles:
            b[1] -= b[3] * dt
            b[0] += math.sin(self.clock * 3 + b[2]) * 20 * dt
        if self.state == "diving" and random.random() < 0.3:
            self.bubbles.append([SUB_POS[0] - 100, SUB_POS[1] + random.uniform(-6, 6), random.uniform(2, 5),
                                 random.uniform(40, 90)])
        self.bubbles = [b for b in self.bubbles if b[1] > 50][-120:]
        if self.card:
            title, text, col, life = self.card
            self.card = (title, text, col, life - dt) if life - dt > 0 else None

    def draw_gauge(self, surf):
        g = self.gauge
        scale = lambda d: g.y + g.h * math.sqrt(d / 11000)
        for i, (name, start, col, _) in enumerate(DIVE_ZONES):
            end = DIVE_ZONES[i + 1][1] if i + 1 < len(DIVE_ZONES) else 11000
            pygame.draw.rect(surf, lighten(col, 20), (g.x, scale(start), g.w, scale(end) - scale(start) + 1))
            draw_text(surf, name.split()[0], font(10, bold=True), (230, 240, 255), (g.right + 6, scale(start) + 10),
                      anchor="midleft")
            draw_text(surf, f"{start:,}m", font(9), (170, 190, 210), (g.right + 6, scale(start) + 22), anchor="midleft")
        pygame.draw.rect(surf, (200, 210, 230), g, width=2, border_radius=4)
        y = scale(self.depth)
        pygame.draw.polygon(surf, (255, 220, 60), [(g.x - 2, y), (g.x - 14, y - 8), (g.x - 14, y + 8)])
        pygame.draw.line(surf, (255, 220, 60), (g.x, y), (g.right, y), 3)
        if self.state == "diving" and self.fail_at < 50:
            pass            # the danger depth stays secret!

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        ox = random.randint(-9, 9) if self.shake else 0
        oy = random.randint(-6, 6) if self.shake else 0
        top, bottom = water_color(self.depth), water_color(self.depth + 900)
        for y in range(56, 636, 4):
            pygame.draw.rect(surf, lerp_col(top, bottom, (y - 56) / 580), (0, y, W, 4))
        # the surface and sky, visible near the top of the dive
        water_line = 150 - self.depth * 2.2
        if water_line > 56:
            for y in range(56, int(water_line)):
                pygame.draw.line(surf, lerp_col((150, 200, 245), (210, 235, 255), (y - 56) / 120), (0, y), (W, y))
            pts = [(x, water_line + math.sin(x * 0.02 + self.clock * 2) * 5) for x in range(0, W + 20, 20)]
            pygame.draw.polygon(surf, top, pts + [(W, water_line + 30), (0, water_line + 30)])
            pygame.draw.lines(surf, (230, 245, 255), False, pts, 3)
        if self.depth < 180:        # sun rays
            rays = pygame.Surface((W, 580), pygame.SRCALPHA)
            a = int(40 * (1 - self.depth / 180))
            for k in range(6):
                x = 100 + k * 220 + math.sin(self.clock * 0.5 + k) * 40
                pygame.draw.polygon(rays, (255, 255, 230, a), [(x, 0), (x + 60, 0), (x + 160, 580), (x + 60, 580)])
            surf.blit(rays, (0, 56))
        for x, y, z in self.snow:
            if self.depth > 150:
                c = int(90 + 50 * z)
                pygame.draw.circle(surf, (c, c, c + 10), (x, y), z * 0.8)
        for c in self.creatures:
            draw_creature(surf, c["kind"], c["x"] + ox, c["y"] + oy, c["s"], c["col"], c["f"], self.clock)
        for b in self.bubbles:
            pygame.draw.circle(surf, (200, 230, 255), (b[0] + ox, b[1] + oy), b[2], width=1)
        sub_y = SUB_POS[1] + math.sin(self.clock * 1.5) * 6
        if self.state == "surface":
            sub_y = min(sub_y, water_line + 30)
        if self.state == "surfacing":
            sub_y -= self.surf_t * 60
        draw_sub(surf, SUB_POS[0] + ox, sub_y + oy, self.clock, self.depth > 200, self.state == "crashed")
        if self.flash:
            fl = pygame.Surface((W, H), pygame.SRCALPHA)
            fl.fill((200, 20, 20, int(110 * self.flash)))
            surf.blit(fl, (0, 0))
        # HUD
        self.draw_gauge(surf)
        hud = pygame.Rect(950, 76, 310, 250)
        soft_panel(surf, hud, 160, (70, 130, 170))
        mcol = (240, 80, 70) if self.state == "crashed" else (90, 240, 130) if self.state in ("surfacing",) else WHITE
        draw_text(surf, f"{self.mult:.2f}x", font(52, bold=True), mcol, (hud.centerx, hud.y + 44), shadow=(0, 0, 0))
        rows = [("DEPTH", f"{int(self.depth):,} m"), ("PRESSURE", f"{1 + self.depth / 10:,.0f} atm"),
                ("WATER TEMP", f"about {dive_temp(self.depth):.0f} C"), ("ZONE", DIVE_ZONES[dive_zone(self.depth)][0].title())]
        for k, (a, b) in enumerate(rows):
            y = hud.y + 100 + k * 34
            draw_text(surf, a, font(13, bold=True), (150, 190, 220), (hud.x + 20, y), anchor="midleft")
            draw_text(surf, b, font(18, bold=True), WHITE, (hud.right - 20, y), anchor="midright")
        if self.card:
            title, text, col, life = self.card
            card = pygame.Rect(950, 340, 310, 170)
            p = pygame.Surface(card.size, pygame.SRCALPHA)
            pygame.draw.rect(p, (0, 10, 20, int(210 * min(1, life))), p.get_rect(), border_radius=14)
            surf.blit(p, card)
            pygame.draw.rect(surf, lighten(col, 60), card, width=2, border_radius=14)
            draw_text(surf, "FUN FACT", font(11, bold=True), (255, 220, 120), (card.x + 16, card.y + 16), anchor="midleft")
            draw_text(surf, title, font(16, bold=True), WHITE, (card.x + 16, card.y + 38), anchor="midleft")
            for k, ln in enumerate(wrap_text(text, font(15), card.w - 32)[:5]):
                draw_text(surf, ln, font(15), (210, 230, 245), (card.x + 16, card.y + 66 + k * 21), anchor="midleft")
        if self.state == "diving":
            draw_text(surf, f"SURFACE NOW FOR {money(self.payout())}", font(22, bold=True), GOLD, (W / 2 - 60, 92),
                      shadow=(0, 0, 0))
        if self.message:
            draw_pill(surf, self.message, font(15, bold=True), (W / 2 - 60, 600), GOLD, (0, 0, 0, 210), GOLD_DARK,
                      pad=(14, 4))
        if self.state == "diving":
            bottom_bar(surf, (4, 12, 22))
            self.tray.draw(surf, self.assets, mouse, disabled=True)
            draw_text(surf, f"BET {money(self.stake)}", font(15, bold=True), GOLD, (115, 677))
            self.btn_up.text = f"SURFACE  {money(self.payout())}"
            self.btn_up.draw(surf, mouse)
        else:
            self.btn_dive.text = "DIVE AGAIN" if self.state == "crashed" else "DIVE!"
            self.draw_bottom(surf, mouse, self.btn_dive, not self.busy() and 0 < self.bet <= self.app.balance,
                             (4, 12, 22))
        self.app.draw_top_bar(surf, "DEEP DIVE", lobby=True, lobby_enabled=self.can_leave())



# --------------------------------------------------------------------------
# Card Counting School - learn the Hi-Lo system, then practise it
# --------------------------------------------------------------------------
def hilo(rank):
    return 1 if rank in ("2", "3", "4", "5", "6") else -1 if rank in ("10", "J", "Q", "K", "A") else 0


def fmt_count(n):
    return f"+{n}" if n > 0 else str(n)


def round_count(x):
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


COUNT_LESSONS = ["What is card counting?", "The Hi-Lo card values", "The running count", "The true count",
                 "Using the count", "Real-life reality check", "Drill: card values", "Drill: running count",
                 "Drill: true count"]
DRILL_SPEEDS = [("SLOW", 1.5), ("MEDIUM", 1.0), ("FAST", 0.6)]
DRILL_SIZES = [10, 20, 30]
VALUE_COL = {1: (60, 200, 100), 0: (150, 150, 160), -1: (230, 70, 70)}


class CountingSchool:
    key = "counting"
    MAIN = pygame.Rect(318, 72, 944, 552)

    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.bg = gradient_bg((14, 50, 34), (4, 14, 10))
        self.page = 0
        self.done = set()
        self.cache = {}
        self.t = 0.0
        self.side = [pygame.Rect(18, 110 + i * 54, 286, 46) for i in range(len(COUNT_LESSONS))]
        self.btn_back = Button((330, 652, 190, 52), "< BACK", (60, 70, 90), 20)
        self.btn_next = Button((1060, 646, 200, 62), "NEXT >", (25, 120, 60), 24, "SPACE")
        # running count demo
        self.demo_shoe, self.demo_cards, self.demo_rc = [], [], 0
        self.btn_deal1 = Button((360, 560, 200, 50), "DEAL A CARD", (25, 120, 60), 18, "SPACE")
        self.btn_deal5 = Button((574, 560, 170, 50), "DEAL 5", (40, 90, 160), 18)
        self.btn_reset = Button((758, 560, 170, 50), "RESET", (120, 35, 40), 18)
        # drill 1: card values
        self.d1_card, self.d1_streak, self.d1_best, self.d1_right, self.d1_total = None, 0, 0, 0, 0
        self.d1_btns = [Button((790 - 275 + i * 190, 470, 170, 70), label, col, 30, key)
                        for i, (label, col, key) in enumerate([("+1", (30, 140, 70), "1"), ("0", (90, 90, 100), "2"),
                                                               ("-1", (170, 40, 45), "3")])]
        self.feedback = None            # (text, colour, time left)
        # drill 2: running count
        self.d2_state = "setup"         # setup, showing, answer, review
        self.d2_speed, self.d2_size = 1, 1
        self.d2_cards, self.d2_shown, self.d2_timer, self.d2_guess = [], 0, 0.0, 0
        self.d2_correct, self.d2_rounds = 0, 0
        self.d2_speed_btns = [Button((520 + i * 150, 250, 136, 50), name, (50, 60, 90), 18)
                              for i, (name, _) in enumerate(DRILL_SPEEDS)]
        self.d2_size_btns = [Button((520 + i * 150, 370, 136, 50), f"{n} CARDS", (50, 60, 90), 18)
                             for i, n in enumerate(DRILL_SIZES)]
        self.d2_start = Button((790 - 130, 470, 260, 62), "START", (25, 120, 60), 26, "SPACE")
        self.d2_minus = Button((790 - 200, 380, 80, 70), "-", (170, 40, 45), 36)
        self.d2_plus = Button((790 + 120, 380, 80, 70), "+", (30, 140, 70), 36)
        self.d2_submit = Button((790 - 130, 490, 260, 62), "SUBMIT", (25, 120, 60), 26, "ENTER")
        # drill 3: true count
        self.d3 = None
        self.d3_streak, self.d3_best = 0, 0
        self.d3_btns = [Button((790 - 370 + i * 190, 430, 170, 66), "", (40, 70, 130), 28) for i in range(4)]

    def can_leave(self):
        return True

    def outstanding_bets(self):
        return 0

    def leave(self):
        pass

    # ---- helpers ---------------------------------------------------------
    def card(self, surf, rank, suit, x, y, scale=1.0, tag=False):
        key = (rank, suit, scale)
        if key not in self.cache:
            self.cache[key] = pygame.transform.smoothscale(self.assets.faces[(rank, suit)],
                                                           (int(CW * scale), int(CH * scale)))
        img = self.cache[key]
        pygame.draw.rect(surf, (0, 0, 0), (x + 3, y + 4, img.get_width(), img.get_height()), border_radius=8)
        surf.blit(img, (x, y))
        if tag:
            v = hilo(rank)
            draw_pill(surf, fmt_count(v), font(15 if scale >= 0.8 else 12, bold=True), (x + img.get_width() / 2,
                      y + img.get_height() + 16), WHITE, (*VALUE_COL[v], 255), None, pad=(9, 2))
        return img.get_width()

    def paras(self, surf, items, x, y, w, size=17):
        f = font(size)
        for kind, text in items:
            if kind == "h":
                draw_text(surf, text, font(size + 3, bold=True), GOLD, (x, y + 10), anchor="midleft")
                y += 32
                continue
            indent = 22 if kind == "b" else 0
            if kind == "b":
                pygame.draw.circle(surf, GOLD, (x + 8, y + 11), 4)
            for ln in wrap_text(text, f, w - indent):
                draw_text(surf, ln, f, (225, 230, 225), (x + indent, y + 11), anchor="midleft")
                y += size + 7
            y += 8
        return y

    def new_shoe(self):
        cards = [(r, s) for _ in range(6) for s in SUITS for r in RANKS]
        random.shuffle(cards)
        return cards

    def go(self, page):
        self.done.add(self.page)
        self.page = max(0, min(len(COUNT_LESSONS) - 1, page))
        self.feedback = None
        if self.page == 6 and not self.d1_card:
            self.d1_card = random.choice([(r, s) for s in SUITS for r in RANKS])
        if self.page == 8 and not self.d3:
            self.new_d3()
        self.app.sfx("card")

    # ---- drills ----------------------------------------------------------
    def d1_answer(self, v):
        rank, suit = self.d1_card
        right = hilo(rank)
        self.d1_total += 1
        if v == right:
            self.d1_right += 1
            self.d1_streak += 1
            self.d1_best = max(self.d1_best, self.d1_streak)
            self.feedback = (f"CORRECT!  {rank} = {fmt_count(right)}", (80, 230, 110), 1.2)
            self.app.sfx("chip")
        else:
            self.d1_streak = 0
            self.feedback = (f"NOPE - {rank} counts as {fmt_count(right)}", (240, 90, 90), 1.8)
            self.app.sfx("lose")
        self.d1_card = random.choice([(r, s) for s in SUITS for r in RANKS])

    def d2_begin(self):
        self.d2_cards = self.new_shoe()[:DRILL_SIZES[self.d2_size]]
        self.d2_shown = 0
        self.d2_timer = 0.4
        self.d2_guess = 0
        self.d2_state = "showing"

    def d2_check(self):
        answer = sum(hilo(r) for r, _ in self.d2_cards)
        self.d2_rounds += 1
        ok = self.d2_guess == answer
        if ok:
            self.d2_correct += 1
            self.app.sfx("win")
            if self.d2_speed == 2 and DRILL_SIZES[self.d2_size] >= 20:
                self.app.unlock("card_sharp")
        else:
            self.app.sfx("lose")
        self.d2_result = (ok, answer)
        self.d2_state = "review"

    def new_d3(self):
        decks = random.choice([0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5])
        tc = random.randint(-5, 6)
        rc = int(round(tc * decks + random.choice([-1, 0, 0, 1]) * (decks > 1)))
        answer = round_count(rc / decks)
        opts = {answer}
        while len(opts) < 4:
            opts.add(answer + random.choice([-3, -2, -1, 1, 2, 3]))
        self.d3 = {"rc": rc, "decks": decks, "answer": answer, "opts": sorted(opts)}

    def d3_answer(self, v):
        d = self.d3
        if v == d["answer"]:
            self.d3_streak += 1
            self.d3_best = max(self.d3_best, self.d3_streak)
            self.feedback = (f"CORRECT!  {fmt_count(d['rc'])} / {d['decks']:g} decks = {fmt_count(d['answer'])}",
                             (80, 230, 110), 2.0)
            self.app.sfx("chip")
        else:
            self.d3_streak = 0
            self.feedback = (f"NOT QUITE:  {fmt_count(d['rc'])} / {d['decks']:g} = {d['rc'] / d['decks']:+.1f}  "
                             f"which rounds to {fmt_count(d['answer'])}", (240, 90, 90), 3.0)
            self.app.sfx("lose")
        self.new_d3()

    # ---- events ----------------------------------------------------------
    def handle(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, r in enumerate(self.side):
                if r.collidepoint(e.pos):
                    self.go(i)
                    return
            if self.btn_back.clicked(e.pos, self.page > 0):
                self.go(self.page - 1)
                return
            if self.btn_next.clicked(e.pos, self.page < len(COUNT_LESSONS) - 1):
                self.go(self.page + 1)
                return
            self.page_click(e.pos)
        elif e.type == pygame.KEYDOWN:
            self.page_key(e)

    def page_click(self, pos):
        if self.page == 2:
            if self.btn_deal1.clicked(pos):
                self.demo_deal(1)
            elif self.btn_deal5.clicked(pos):
                self.demo_deal(5)
            elif self.btn_reset.clicked(pos):
                self.demo_shoe, self.demo_cards, self.demo_rc = [], [], 0
        elif self.page == 6:
            for b, v in zip(self.d1_btns, (1, 0, -1)):
                if b.clicked(pos):
                    self.d1_answer(v)
        elif self.page == 7:
            if self.d2_state in ("setup", "review"):
                for i, b in enumerate(self.d2_speed_btns):
                    if b.clicked(pos):
                        self.d2_speed = i
                        self.d2_state = "setup"
                for i, b in enumerate(self.d2_size_btns):
                    if b.clicked(pos):
                        self.d2_size = i
                        self.d2_state = "setup"
                if self.d2_start.clicked(pos):
                    self.d2_begin()
            elif self.d2_state == "answer":
                if self.d2_minus.clicked(pos):
                    self.d2_guess -= 1
                elif self.d2_plus.clicked(pos):
                    self.d2_guess += 1
                elif self.d2_submit.clicked(pos):
                    self.d2_check()
        elif self.page == 8:
            for b, v in zip(self.d3_btns, self.d3["opts"]):
                if b.clicked(pos):
                    self.d3_answer(v)

    def page_key(self, e):
        if self.page == 2 and e.key == pygame.K_SPACE:
            self.demo_deal(1)
        elif self.page == 6 and e.unicode in ("1", "2", "3"):
            self.d1_answer({"1": 1, "2": 0, "3": -1}[e.unicode])
        elif self.page == 7:
            if self.d2_state in ("setup", "review") and e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.d2_begin()
            elif self.d2_state == "answer":
                if e.key in (pygame.K_RETURN, pygame.K_SPACE):
                    self.d2_check()
                elif e.key in (pygame.K_UP, pygame.K_RIGHT, pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                    self.d2_guess += 1
                elif e.key in (pygame.K_DOWN, pygame.K_LEFT, pygame.K_MINUS, pygame.K_KP_MINUS):
                    self.d2_guess -= 1
        elif self.page == 8 and e.unicode in ("1", "2", "3", "4"):
            self.d3_answer(self.d3["opts"][int(e.unicode) - 1])
        elif e.key == pygame.K_SPACE and self.page < len(COUNT_LESSONS) - 1 and self.page not in (2, 6, 7, 8):
            self.go(self.page + 1)

    def demo_deal(self, n):
        for _ in range(n):
            if not self.demo_shoe:
                self.demo_shoe = self.new_shoe()
            card = self.demo_shoe.pop()
            self.demo_cards.append(card)
            self.demo_rc += hilo(card[0])
        self.app.sfx("card")

    def update(self, dt):
        self.t += dt
        if self.feedback:
            text, col, life = self.feedback
            self.feedback = (text, col, life - dt) if life - dt > 0 else None
        if self.page == 7 and self.d2_state == "showing":
            self.d2_timer -= dt
            if self.d2_timer <= 0:
                if self.d2_shown >= len(self.d2_cards):
                    self.d2_state = "answer"
                else:
                    self.d2_shown += 1
                    self.d2_timer = DRILL_SPEEDS[self.d2_speed][1]
                    self.app.sfx("card")

    # ---- drawing ---------------------------------------------------------
    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        soft_panel(surf, pygame.Rect(10, 66, 302, 560), 140, (60, 120, 90))
        draw_text(surf, "CARD COUNTING SCHOOL", font(17, bold=True), GOLD, (161, 90))
        for i, (r, name) in enumerate(zip(self.side, COUNT_LESSONS)):
            on = i == self.page
            col = (200, 150, 40) if on else ((40, 70, 55) if r.collidepoint(mouse) else (20, 40, 30))
            pygame.draw.rect(surf, col, r, border_radius=10)
            num = pygame.Rect(r.x + 8, r.y + 8, 30, 30)
            done = i in self.done
            pygame.draw.circle(surf, (80, 220, 120) if done else (60, 80, 70), num.center, 14)
            draw_text(surf, "OK" if done else str(i + 1), font(11 if done else 14, bold=True), (10, 30, 15) if done else WHITE,
                      num.center)
            draw_text(surf, name, font(15, bold=True), (30, 15, 5) if on else (220, 235, 225), (r.x + 48, r.centery),
                      anchor="midleft")
        m = self.MAIN
        soft_panel(surf, m, 150, (60, 120, 90))
        draw_text(surf, f"{self.page + 1}.  {COUNT_LESSONS[self.page]}", font(28, bold=True, serif=True), GOLD,
                  (m.x + 30, m.y + 34), anchor="midleft")
        getattr(self, f"draw_page{self.page}")(surf, mouse)
        if self.feedback:
            text, col, life = self.feedback
            draw_pill(surf, text, font(18, bold=True), (m.centerx, m.bottom - 30), WHITE, (*darken(col, 60), 235), col,
                      pad=(16, 5))
        bottom_bar(surf, (6, 16, 12))
        self.btn_back.draw(surf, mouse, self.page > 0)
        self.btn_next.draw(surf, mouse, self.page < len(COUNT_LESSONS) - 1)
        draw_text(surf, f"Lesson {self.page + 1} of {len(COUNT_LESSONS)}", font(14), (170, 200, 180), (790, 678))
        self.app.draw_top_bar(surf, "CARD COUNTING SCHOOL", lobby=True, show_help=False)

    def draw_page0(self, surf, mouse):
        m = self.MAIN
        y = self.paras(surf, [
            ("p", "Card counting is a way to keep track of the cards that have already been played in blackjack, "
                  "so you know whether the cards still in the shoe favor you or the dealer."),
            ("p", "It isn't magic, and you don't memorize every card. You keep one simple number in your head "
                  "and change it a little as each card is dealt."),
            ("h", "Why it works"),
            ("p", "When lots of LOW cards have been dealt, the cards left in the shoe are packed with 10s and Aces. "
                  "That's good for you: you get more blackjacks (which pay 3 to 2), your doubles land on 10s more "
                  "often, and the dealer - who must keep hitting until 17 - busts more."),
            ("p", "When lots of HIGH cards are gone, it's the other way round and the casino's edge gets bigger."),
            ("p", "Mathematician Edward Thorp proved counting works in his 1962 book 'Beat the Dealer'. The Hi-Lo "
                  "system you'll learn here is the most popular counting method in the world."),
        ], m.x + 30, m.y + 74, m.w - 60)
        draw_text(surf, "Played so far:", font(15, bold=True), (200, 220, 205), (m.x + 30, y + 30), anchor="midleft")
        for i, (r, s) in enumerate([("2", "H"), ("5", "C"), ("3", "D"), ("6", "S"), ("4", "H")]):
            self.card(surf, r, s, m.x + 150 + i * 50, y + 6, 0.5)
        draw_text(surf, "->  what's left is rich in:", font(15, bold=True), (200, 220, 205), (m.x + 440, y + 30),
                  anchor="midleft")
        for i, (r, s) in enumerate([("10", "S"), ("K", "H"), ("A", "D"), ("Q", "C"), ("J", "S")]):
            self.card(surf, r, s, m.x + 640 + i * 50, y + 6, 0.5)

    def draw_page1(self, surf, mouse):
        m = self.MAIN
        groups = [(1, "LOW CARDS", ["2", "3", "4", "5", "6"]), (0, "MIDDLE CARDS", ["7", "8", "9"]),
                  (-1, "HIGH CARDS", ["10", "J", "Q", "K", "A"])]
        x = m.x + 30
        for v, name, ranks in groups:
            w = 16 + len(ranks) * 52 + 24
            box = pygame.Rect(x, m.y + 70, w, 186)
            pygame.draw.rect(surf, darken(VALUE_COL[v], 110), box, border_radius=14)
            pygame.draw.rect(surf, VALUE_COL[v], box, width=3, border_radius=14)
            draw_text(surf, name, font(15, bold=True), WHITE, (box.centerx, box.y + 20))
            for i, r in enumerate(ranks):
                self.card(surf, r, "SHDC"[i % 4], box.x + 20 + i * 52, box.y + 42, 0.62)
            draw_text(surf, fmt_count(v), font(40, bold=True), VALUE_COL[v], (box.centerx, box.bottom - 28))
            x += w + 16
        self.paras(surf, [
            ("p", "Every card gets a value. Each time you SEE a card, add its value to your count."),
            ("b", "Low cards (2 to 6) = +1.  A low card leaving the shoe is good news for you."),
            ("b", "Middle cards (7, 8, 9) = 0.  Ignore them."),
            ("b", "High cards (10, J, Q, K, A) = -1.  A high card leaving is bad news for you."),
            ("p", "Handy check: every deck has 20 low cards and 20 high cards, so if you count a whole deck "
                  "perfectly you end up at exactly 0."),
        ], m.x + 30, m.y + 280, m.w - 60)

    def draw_page2(self, surf, mouse):
        m = self.MAIN
        self.paras(surf, [
            ("p", "Start at 0 whenever the shoe is shuffled. Then add the value of EVERY card you see - yours, the "
                  "dealer's and other players'. That total is the RUNNING COUNT."),
            ("p", "Try it: deal some cards and watch the count change. Try to guess the count before you look!"),
        ], m.x + 30, m.y + 70, m.w - 330)
        box = pygame.Rect(m.right - 270, m.y + 70, 240, 150)
        pygame.draw.rect(surf, (6, 24, 16), box, border_radius=14)
        pygame.draw.rect(surf, GOLD, box, width=2, border_radius=14)
        draw_text(surf, "RUNNING COUNT", font(14, bold=True), GOLD, (box.centerx, box.y + 24))
        col = VALUE_COL[(self.demo_rc > 0) - (self.demo_rc < 0)]
        draw_text(surf, fmt_count(self.demo_rc), font(64, bold=True), col, (box.centerx, box.y + 88))
        draw_text(surf, f"{len(self.demo_cards)} cards seen", font(13), (180, 200, 190), (box.centerx, box.bottom - 16))
        shown = self.demo_cards[-9:]
        for i, (r, s) in enumerate(shown):
            self.card(surf, r, s, m.x + 40 + i * 98, m.y + 300, 0.9, tag=True)
        if not shown:
            draw_text(surf, "Press DEAL A CARD (or Space) to start", font(18), (180, 200, 190), (m.centerx, m.y + 360))
        for b in (self.btn_deal1, self.btn_deal5, self.btn_reset):
            b.draw(surf, mouse, b is not self.btn_reset or bool(self.demo_cards))

    def draw_page3(self, surf, mouse):
        m = self.MAIN
        y = self.paras(surf, [
            ("p", "A running count of +6 means a lot more when only a few cards are left than when the shoe is "
                  "almost full - the extra high cards are packed into fewer cards. So we convert it:"),
        ], m.x + 30, m.y + 70, m.w - 60)
        box = pygame.Rect(m.x + 140, y + 4, m.w - 280, 60)
        pygame.draw.rect(surf, (6, 24, 16), box, border_radius=12)
        pygame.draw.rect(surf, GOLD, box, width=2, border_radius=12)
        draw_text(surf, "TRUE COUNT  =  RUNNING COUNT  /  DECKS LEFT", font(24, bold=True), WHITE, box.center)
        y = self.paras(surf, [
            ("b", "Running count +6 with 3 decks left  ->  true count +2"),
            ("b", "Running count +6 with only 1 deck left  ->  true count +6 (much better for you!)"),
            ("b", "Running count -4 with 2 decks left  ->  true count -2"),
            ("p", "To work out decks left, look at the discard tray. In a 6-deck shoe, if about half the cards "
                  "have been played, there are about 3 decks left. Rounding to the nearest half deck is fine."),
        ], m.x + 30, y + 84, m.w - 60)
        # shoe picture: 6 decks, 2.5 used
        for k in range(6):
            r = pygame.Rect(m.x + 250 + k * 72, y + 8, 64, 40)
            pygame.draw.rect(surf, (60, 60, 66) if k < 4 else (150, 20, 32), r, border_radius=6)
            if k == 3:                              # half of this deck is still to come
                pygame.draw.rect(surf, (150, 20, 32), (r.x + 32, r.y, 32, r.h), border_radius=6)
            pygame.draw.rect(surf, (230, 200, 130), r, width=2, border_radius=6)
        draw_text(surf, "played", font(12, bold=True), (180, 180, 190), (m.x + 250 + 1.75 * 72, y + 64))
        draw_text(surf, "2.5 decks left", font(12, bold=True), (255, 200, 200), (m.x + 250 + 4.75 * 72, y + 64))

    def draw_page4(self, surf, mouse):
        m = self.MAIN
        y = self.paras(surf, [
            ("p", "The true count tells you HOW MUCH to bet. When it's low, bet the minimum. When it climbs, the "
                  "shoe is in your favor - bet more. A simple plan (1 unit = your normal minimum bet):"),
        ], m.x + 30, m.y + 70, m.w - 60)
        rows = [("+1 or lower", "1 unit", (150, 150, 160)), ("+2", "2 units", (120, 200, 130)),
                ("+3", "4 units", (80, 210, 110)), ("+4", "6 units", (60, 220, 100)), ("+5 or higher", "8 units", GOLD)]
        for k, (tc, bet, col) in enumerate(rows):
            r = pygame.Rect(m.x + 200, y + 6 + k * 38, m.w - 400, 34)
            pygame.draw.rect(surf, (10, 30, 20) if k % 2 else (16, 40, 28), r, border_radius=6)
            draw_text(surf, f"True count {tc}", font(17, bold=True), WHITE, (r.x + 20, r.centery), anchor="midleft")
            draw_text(surf, f"bet {bet}", font(17, bold=True), col, (r.right - 20, r.centery), anchor="midright")
        self.paras(surf, [
            ("b", "Counting tells you how much to BET. You still need good basic strategy to decide how to PLAY "
                  "each hand - counting without it doesn't work."),
            ("b", "Try it for real: in our Blackjack, press the COUNT button (or C) to show the running and true "
                  "count. Keep the count in your head, then check yourself."),
            ("b", "Our blackjack uses a 6-deck shoe that's reshuffled after about 75% of the cards are dealt - just "
                  "like a real casino. The count resets to 0 at every shuffle."),
        ], m.x + 30, y + 206, m.w - 60)

    def draw_page5(self, surf, mouse):
        m = self.MAIN
        self.paras(surf, [
            ("h", "Is it legal?"),
            ("p", "Counting cards in your head isn't cheating and isn't against the law in the US. But casinos are "
                  "private businesses - they can ask a counter to stop playing blackjack or to leave. Using a phone "
                  "or any device to help you count IS illegal in casinos."),
            ("h", "It's a small edge"),
            ("p", "Even a skilled counter usually has only about a 0.5% to 1.5% advantage. You can still lose for "
                  "hours or days - the edge only shows up over thousands of hands."),
            ("h", "Casinos fight back"),
            ("p", "Continuous shuffling machines, shuffling early, and watching for players whose bets jump up and down "
                  "all make counting much harder."),
            ("h", "It takes practice"),
            ("p", "Real counters keep the count while playing perfect strategy, chatting, and not looking like they're "
                  "counting. Here it's all play money - so practice as much as you like with the drills!"),
        ], m.x + 30, m.y + 66, m.w - 60, 16)

    def draw_page6(self, surf, mouse):
        m = self.MAIN
        draw_text(surf, "What does this card count as?  Press +1, 0 or -1 (or keys 1, 2, 3).", font(17),
                  (210, 230, 215), (m.centerx, m.y + 80))
        if self.d1_card:
            r, s = self.d1_card
            self.card(surf, r, s, m.centerx - CW * 0.8, m.y + 110, 1.6)
        for b in self.d1_btns:
            b.draw(surf, mouse)
        acc = f"{self.d1_right / self.d1_total * 100:.0f}%" if self.d1_total else "-"
        for k, (a, v) in enumerate((("STREAK", self.d1_streak), ("BEST", self.d1_best), ("ACCURACY", acc))):
            x = m.x + 110 + k * 150 if k < 1 else m.right - 110 - (2 - k) * 150
            draw_text(surf, a, font(12, bold=True), (170, 200, 180), (x, m.y + 150))
            draw_text(surf, str(v), font(34, bold=True), GOLD, (x, m.y + 186))

    def draw_page7(self, surf, mouse):
        m = self.MAIN
        if self.d2_state in ("setup", "review"):
            draw_text(surf, "Cards will flash by one at a time. Keep the running count in your head!", font(17),
                      (210, 230, 215), (m.centerx, m.y + 80))
            draw_text(surf, "SPEED", font(14, bold=True), GOLD, (m.x + 60, 275), anchor="midleft")
            draw_text(surf, "HOW MANY", font(14, bold=True), GOLD, (m.x + 60, 395), anchor="midleft")
            for i, b in enumerate(self.d2_speed_btns):
                b.color = (200, 150, 40) if i == self.d2_speed else (50, 60, 90)
                b.draw(surf, mouse)
            for i, b in enumerate(self.d2_size_btns):
                b.color = (200, 150, 40) if i == self.d2_size else (50, 60, 90)
                b.draw(surf, mouse)
            draw_text(surf, f"Correct: {self.d2_correct} of {self.d2_rounds}", font(15, bold=True), (170, 200, 180),
                      (m.right - 120, m.y + 80))
            if self.d2_state == "review":
                ok, answer = self.d2_result
                col = (80, 230, 110) if ok else (240, 90, 90)
                draw_pill(surf, ("CORRECT! " if ok else f"You said {fmt_count(self.d2_guess)}.  ") +
                          f"The count was {fmt_count(answer)}", font(20, bold=True), (m.centerx, m.y + 130), WHITE,
                          (*darken(col, 70), 240), col, pad=(18, 6))
                self.d2_start.text = "TRY AGAIN"
                # small review strip of the cards with their values
                for i, (r, s) in enumerate(self.d2_cards[:15]):
                    x = m.x + 740 + (i % 5) * 38
                    y = m.y + 200 + (i // 5) * 70
                    self.card(surf, r, s, x, y, 0.36)
                    draw_text(surf, fmt_count(hilo(r)), font(12, bold=True), VALUE_COL[hilo(r)], (x + 16, y + 56))
            else:
                self.d2_start.text = "START"
            self.d2_start.draw(surf, mouse)
        elif self.d2_state == "showing":
            n = len(self.d2_cards)
            draw_text(surf, f"CARD {min(self.d2_shown, n)} OF {n}", font(18, bold=True), GOLD, (m.centerx, m.y + 80))
            if self.d2_shown:
                r, s = self.d2_cards[self.d2_shown - 1]
                self.card(surf, r, s, m.centerx - CW * 0.9, m.y + 120, 1.8)
        else:
            draw_text(surf, "WHAT'S THE RUNNING COUNT?", font(26, bold=True), GOLD, (m.centerx, m.y + 140))
            draw_text(surf, "Use + and - (or the arrow keys), then SUBMIT (Enter).", font(15), (200, 220, 205),
                      (m.centerx, m.y + 180))
            box = pygame.Rect(0, 0, 200, 90)
            box.center = (790, 415)
            pygame.draw.rect(surf, (6, 24, 16), box, border_radius=14)
            pygame.draw.rect(surf, GOLD, box, width=3, border_radius=14)
            draw_text(surf, fmt_count(self.d2_guess), font(52, bold=True), WHITE, box.center)
            for b in (self.d2_minus, self.d2_plus, self.d2_submit):
                b.draw(surf, mouse)

    def draw_page8(self, surf, mouse):
        m = self.MAIN
        d = self.d3
        draw_text(surf, "Work out the true count. Round to the nearest whole number.", font(17), (210, 230, 215),
                  (m.centerx, m.y + 80))
        for k, (label, value) in enumerate((("RUNNING COUNT", fmt_count(d["rc"])), ("DECKS LEFT", f"{d['decks']:g}"))):
            box = pygame.Rect(m.x + 170 + k * 330, m.y + 120, 280, 130)
            pygame.draw.rect(surf, (6, 24, 16), box, border_radius=14)
            pygame.draw.rect(surf, GOLD, box, width=2, border_radius=14)
            draw_text(surf, label, font(14, bold=True), GOLD, (box.centerx, box.y + 24))
            draw_text(surf, value, font(56, bold=True), WHITE, (box.centerx, box.y + 80))
        for k in range(6):
            left = k >= 6 - d["decks"]
            half = (6 - d["decks"]) - k == 0.5
            r = pygame.Rect(m.x + 250 + k * 72, m.y + 290, 64, 40)
            pygame.draw.rect(surf, (150, 20, 32) if left else (60, 60, 66), r, border_radius=6)
            if half:
                pygame.draw.rect(surf, (150, 20, 32), (r.x + 32, r.y, 32, r.h), border_radius=6)
            pygame.draw.rect(surf, (230, 200, 130), r, width=2, border_radius=6)
        draw_text(surf, "the shoe (red = decks still to be dealt)", font(12, bold=True), (190, 200, 195),
                  (m.x + 250 + 3 * 72 - 4, m.y + 346))
        for b, v in zip(self.d3_btns, d["opts"]):
            b.text = fmt_count(v)
            b.draw(surf, mouse)
        for i, b in enumerate(self.d3_btns):
            draw_text(surf, str(i + 1), font(11), (190, 200, 210), (b.rect.centerx, b.rect.bottom + 12))
        draw_text(surf, f"STREAK {self.d3_streak}     BEST {self.d3_best}", font(16, bold=True), GOLD,
                  (m.centerx, m.y + 470))


# --------------------------------------------------------------------------
# Pinball - pay for a 3-ball game, your score decides the prize
# --------------------------------------------------------------------------
PB_BALL_R = 10
PB_GRAVITY = 1300
PB_SUBSTEPS = 10
PB_LANE_X = 862                     # centre of the shooter lane
PB_PLUNGER_Y = 676
PB_PRIZES = [(0, 0), (3500, 0.5), (7000, 1), (15000, 1.25), (30000, 1.5), (60000, 2), (120000, 4), (250000, 10)]
PB_BUMPERS = [(560, 250), (720, 250), (640, 336)]
PB_TARGETS = [590, 640, 690]        # drop targets at y = 178
PB_LANES = [(560, 124), (640, 104), (720, 124)]
PB_FLIP_LEN = 78
PB_FLIP_REST, PB_FLIP_UP = math.radians(30), math.radians(-32)


def pb_prize(score):
    m = 0
    for need, mult in PB_PRIZES:
        if score >= need:
            m = mult
    return m


def pb_walls():
    """Static walls as line segments (x1, y1, x2, y2, bounciness)."""
    walls = []
    cx, cy, rx, ry = 640, 262, 238, 200
    pts = [(cx + rx * math.cos(math.pi + math.pi * k / 20), cy + ry * math.sin(math.pi + math.pi * k / 20))
           for k in range(21)]
    for a, b in zip(pts, pts[1:]):
        walls.append((*a, *b, 0.55))
    walls += [(402, 262, 402, 720, 0.5), (436, 556, 544, 616, 0.3),        # left wall + inlane (outlane beside it)
              (878, 262, 878, 700, 0.4), (846, 300, 846, 700, 0.4),        # shooter lane walls
              (812, 556, 736, 616, 0.3),                                    # right inlane (outlane beside it)
              (846, 700, 878, 700, 0.1),                                    # plunger floor
              (402, 495, 470, 530, 0.5),                                    # left slingshot underside
              (846, 495, 778, 530, 0.5)]                                    # right slingshot underside
    # (the slingshots sit high enough that a ball can roll underneath them, down the lane to the flipper)
    return walls


PB_SLINGS = [(402, 415, 470, 530), (846, 415, 778, 530)]      # the kicking faces, against the side walls


class Pinball(StakeGame):
    key = "pinball"

    def __init__(self, app):
        super().__init__(app, ((10, 8, 24), (70, 60, 120)))
        self.walls = pb_walls()
        self.bg = self.make_bg()
        self.state = "betting"          # betting, plunger, play, over
        self.score = 0
        self.balls = 0
        self.mult = 1
        self.stake = 0
        self.bx, self.by, self.vx, self.vy = PB_LANE_X, PB_PLUNGER_Y, 0.0, 0.0
        self.flip_ang = [PB_FLIP_REST, PB_FLIP_REST]
        self.power = 0.0
        self.charging = False
        self.launched_at = 0.0
        self.clock = 0.0
        self.targets = [True] * 3
        self.target_reset = 0.0
        self.lanes = [False] * 3
        self.lit = {}                   # things that flash when hit
        self.pops = []                  # floating "+100" texts
        self.stuck = 0.0
        self.bot = False                # used by the auto-tester
        self.bot_hold = False
        self.bot_skill = 0.85
        self.message = "SET YOUR BET, THEN PRESS START"
        self.btn_start = Button((965, 646, 270, 62), "START  (3 BALLS)", (25, 120, 60), 22, "SPACE")

    def make_bg(self):
        s = gradient_bg((10, 8, 30), (4, 4, 12))
        table = pygame.Surface((W, H), pygame.SRCALPHA)
        pygame.draw.ellipse(table, (20, 30, 80, 255), (402, 62, 476, 400))
        pygame.draw.rect(table, (20, 30, 80, 255), (402, 262, 476, 460))
        mask = pygame.Surface((W, H), pygame.SRCALPHA)
        pygame.draw.polygon(mask, (255, 255, 255, 255), [(402, 262), (402, 520), (548, 628), (548, 720),
                                                          (732, 720), (732, 628), (878, 520), (878, 262)])
        pygame.draw.ellipse(mask, (255, 255, 255, 255), (402, 62, 476, 400))
        pygame.draw.rect(mask, (255, 255, 255, 255), (846, 262, 32, 460))
        table.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        for i in range(12):                          # starburst art on the playfield
            a = i / 12 * 2 * math.pi
            pygame.draw.polygon(table, (40, 60, 140, 255), [(640, 420), (640 + math.cos(a) * 240, 420 + math.sin(a) * 240),
                                                            (640 + math.cos(a + 0.18) * 240, 420 + math.sin(a + 0.18) * 240)])
        table.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        s.blit(table, (0, 0))
        draw_text(s, "GRAND ROYALE", font(22, bold=True, serif=True), (90, 110, 190), (640, 468))
        draw_text(s, "PINBALL", font(34, bold=True, serif=True), (110, 130, 210), (640, 500))
        for x1, y1, x2, y2, _ in self.walls:
            pygame.draw.line(s, (200, 205, 220), (x1, y1), (x2, y2), 6)
            pygame.draw.line(s, (120, 125, 140), (x1, y1 + 2), (x2, y2 + 2), 2)
        for x1, y1, x2, y2 in PB_SLINGS:
            pygame.draw.polygon(s, (170, 40, 60), [(x1, y1), (x2, y2), (x1, 495)])
            pygame.draw.polygon(s, (230, 90, 110), [(x1, y1), (x2, y2), (x1, 495)], 3)
        for x, y in PB_LANES:
            pygame.draw.circle(s, (60, 70, 120), (x, y), 14, width=2)
        return s

    def busy(self):
        return self.state in ("plunger", "play")

    def outstanding_bets(self):
        return self.stake if self.busy() else 0

    # ---- game flow -------------------------------------------------------
    def start(self):
        if self.busy():
            return
        stake = self.take_bet()
        if not stake:
            return
        self.stake = stake
        self.score, self.balls, self.mult = 0, 3, 1
        self.save_used = False
        self.targets, self.lanes = [True] * 3, [False] * 3
        self.message = "HOLD SPACE (OR DOWN) TO PULL THE PLUNGER - LET GO TO LAUNCH"
        self.new_ball()
        self.app.sfx("chip")

    def new_ball(self):
        self.state = "plunger"
        self.bx, self.by, self.vx, self.vy = PB_LANE_X, PB_PLUNGER_Y, 0.0, 0.0
        self.power = 0.0
        self.charging = False

    def launch(self):
        self.vy = -(1150 + 650 * self.power)
        self.vx = 0.0
        self.state = "play"
        self.launched_at = self.clock
        self.charging = False
        self.message = ""
        self.app.sfx("launch")

    def add(self, pts, x, y):
        pts *= self.mult
        self.score += pts
        self.pops.append([f"+{pts:,}", x, y, 0.8])

    def drain(self):
        if self.clock - self.launched_at < 4.0 and not self.save_used:
            self.save_used = True
            self.message = "BALL SAVED!"
            self.new_ball()
            return
        self.save_used = False
        self.balls -= 1
        self.app.sfx("lose")
        if self.balls > 0:
            self.message = f"BALL LOST - {self.balls} LEFT"
            self.new_ball()
        else:
            self.game_over()

    def game_over(self):
        self.state = "over"
        m = pb_prize(self.score)
        ret = int(self.stake * m)
        self.finish(self.stake, ret, f"GAME OVER - {self.score:,} POINTS  -  x{m:g}  -  YOU WIN {money(ret)}",
                    f"GAME OVER - {self.score:,} POINTS  -  " +
                    (f"x{m:g}  -  YOU GET BACK {money(ret)}" if ret else f"YOU LOSE {money(self.stake)}"))

    # ---- physics ---------------------------------------------------------
    def flipper_seg(self, i):
        a = self.flip_ang[i]
        if i == 0:
            px, py = 548, 628
            return px, py, px + math.cos(a) * PB_FLIP_LEN, py + math.sin(a) * PB_FLIP_LEN
        px, py = 732, 628
        return px, py, px - math.cos(a) * PB_FLIP_LEN, py + math.sin(a) * PB_FLIP_LEN

    def collide(self, x1, y1, x2, y2, rad, bounce, surf_v=(0.0, 0.0), kick=0.0):
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy or 1e-9
        t = max(0.0, min(1.0, ((self.bx - x1) * dx + (self.by - y1) * dy) / l2))
        px, py = x1 + t * dx, y1 + t * dy
        nx, ny = self.bx - px, self.by - py
        d = math.hypot(nx, ny)
        need = PB_BALL_R + rad
        if d >= need or d < 1e-6:
            return False
        nx, ny = nx / d, ny / d
        self.bx, self.by = px + nx * need, py + ny * need
        sv = (surf_v[0] * t, surf_v[1] * t) if isinstance(surf_v, tuple) and len(surf_v) == 2 else (0, 0)
        rvx, rvy = self.vx - sv[0], self.vy - sv[1]
        vn = rvx * nx + rvy * ny
        if vn < 0:
            rvx -= (1 + bounce) * vn * nx
            rvy -= (1 + bounce) * vn * ny
            if kick:
                rvx += nx * kick
                rvy += ny * kick
            self.vx, self.vy = rvx + sv[0], rvy + sv[1]
        return True

    def step(self, h, keys):
        # flippers
        for i in range(2):
            old = self.flipper_seg(i)
            target = PB_FLIP_UP if keys[i] else PB_FLIP_REST
            speed = 26 if keys[i] else 14
            a = self.flip_ang[i]
            a += max(-speed * h, min(speed * h, target - a))
            self.flip_ang[i] = a
            new = self.flipper_seg(i)
            tip_v = ((new[2] - old[2]) / h, (new[3] - old[3]) / h)
            self.collide(*new, 8, 0.25, tip_v)
        self.vy += PB_GRAVITY * h
        self.bx += self.vx * h
        self.by += self.vy * h
        for x1, y1, x2, y2, b in self.walls:
            self.collide(x1, y1, x2, y2, 3, b)
        # one-way gate at the top of the shooter lane: only blocks balls falling back in
        if self.vy > 0 and 846 < self.bx < 878:
            self.collide(846, 304, 878, 272, 2, 0.3)
        for k, (x1, y1, x2, y2) in enumerate(PB_SLINGS):
            if self.collide(x1, y1, x2, y2, 4, 0.4, kick=random.uniform(380, 560)):
                if self.lit.get(f"s{k}", 0) <= 0:
                    self.add(50, (x1 + x2) / 2, (y1 + y2) / 2)
                    self.app.sfx("card")
                self.lit[f"s{k}"] = 0.12
        for k, (x, y) in enumerate(PB_BUMPERS):
            dx, dy = self.bx - x, self.by - y
            d = math.hypot(dx, dy)
            if d < 24 + PB_BALL_R and d > 1e-6:
                nx, ny = dx / d, dy / d
                self.bx, self.by = x + nx * (24 + PB_BALL_R), y + ny * (24 + PB_BALL_R)
                tang = self.vx * -ny + self.vy * nx
                j = random.uniform(-0.35, 0.35)
                nx, ny = nx * math.cos(j) - ny * math.sin(j), nx * math.sin(j) + ny * math.cos(j)
                self.vx, self.vy = nx * 700 + -ny * tang * 0.3, ny * 700 + nx * tang * 0.3
                self.add(100, x, y - 30)
                self.lit[f"b{k}"] = 0.15
                self.app.sfx("chip")
        for k, x in enumerate(PB_TARGETS):
            if self.targets[k] and self.collide(x - 16, 178, x + 16, 178, 5, 0.5):
                self.targets[k] = False
                self.add(250, x, 160)
                self.app.sfx("card")
                if not any(self.targets):
                    self.add(1500, 640, 200)
                    self.target_reset = 1.0
        for k, (x, y) in enumerate(PB_LANES):
            if math.hypot(self.bx - x, self.by - y) < 16 and not self.lanes[k]:
                self.lanes[k] = True
                self.add(200, x, y - 22)
                if all(self.lanes):
                    self.lanes = [False] * 3
                    if self.mult < 5:
                        self.mult += 1
                    self.add(1000, 640, 150)
                    self.pops.append([f"MULTIPLIER x{self.mult}!", 640, 230, 1.6])
                    self.app.sfx("win")
        sp = math.hypot(self.vx, self.vy)
        if sp > 1900:
            self.vx, self.vy = self.vx / sp * 1900, self.vy / sp * 1900

    def bot_keys(self):
        """Auto-player used for testing, with human-like timing: it reacts late, taps, and sometimes misses."""
        if not hasattr(self, "bot_plan"):
            self.bot_plan = [None, None]
        keys = [False, False]
        for i, (lo, hi) in enumerate(((520, 640), (640, 760))):
            plan = self.bot_plan[i]
            if plan is None and self.by > 545 and self.vy > 60 and lo < self.bx < hi:
                if random.random() < self.bot_skill:
                    self.bot_plan[i] = [self.clock + random.uniform(0.02, 0.14), self.clock + 0.35]
                else:
                    self.bot_plan[i] = [self.clock + 99, self.clock + 0.35]       # froze - missed it
            plan = self.bot_plan[i]
            if plan:
                keys[i] = plan[0] <= self.clock < plan[0] + 0.22
                if self.clock > plan[1] and self.clock > plan[0] + 0.22 or self.clock > plan[1] + 0.2 and plan[0] > self.clock + 50:
                    self.bot_plan[i] = None
        return keys

    def update(self, dt):
        self.clock += dt
        for k in list(self.lit):
            self.lit[k] -= dt
        for p in self.pops:
            p[2] -= 30 * dt
            p[3] -= dt
        self.pops = [p for p in self.pops if p[3] > 0]
        if self.target_reset > 0:
            self.target_reset -= dt
            if self.target_reset <= 0:
                self.targets = [True] * 3
        if not self.busy():
            return
        pressed = pygame.key.get_pressed()
        mouse = pygame.mouse.get_pressed()
        if self.bot:
            keys = self.bot_keys()
        else:
            keys = [pressed[pygame.K_LEFT] or pressed[pygame.K_z] or pressed[pygame.K_LSHIFT] or mouse[0],
                    pressed[pygame.K_RIGHT] or pressed[pygame.K_m] or pressed[pygame.K_RSHIFT] or mouse[2]]
        if self.state == "plunger":
            hold = self.bot_hold if self.bot else (pressed[pygame.K_SPACE] or pressed[pygame.K_DOWN])
            if hold:
                self.charging = True
                self.power = min(1.0, self.power + dt * 1.1)
            elif self.charging:
                self.launch()
            for i in range(2):          # flippers still move while waiting
                target = PB_FLIP_UP if keys[i] else PB_FLIP_REST
                self.flip_ang[i] += max(-26 * dt, min(26 * dt, target - self.flip_ang[i]))
            return
        h = dt / PB_SUBSTEPS
        for _ in range(PB_SUBSTEPS):
            self.step(h, keys)
        if self.by > 715:
            self.drain()
        elif self.bx > 846 and self.by > PB_PLUNGER_Y - 4 and abs(self.vy) < 60:
            self.new_ball()             # rolled back down the shooter lane - relaunch for free
        # nudge a ball that is trapped somewhere motionless (unless you're holding it on a flipper on purpose)
        if math.hypot(self.vx, self.vy) < 15 and not any(keys):
            self.stuck += dt
            if self.stuck > 2.5:
                self.vx, self.vy = random.uniform(-200, 200), -300
                self.stuck = 0
        else:
            self.stuck = 0

    # ---- events + drawing -------------------------------------------------
    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN) and not self.busy():
            self.start()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and not self.busy():
            if self.chip_click(e.pos):
                return
            if self.btn_clear.clicked(e.pos):
                self.bet = 0
            elif self.btn_start.clicked(e.pos):
                self.start()

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        for k, (x, y) in enumerate(PB_LANES):
            if self.lanes[k]:
                pygame.draw.circle(surf, (255, 220, 90), (x, y), 12)
            draw_text(surf, "RYL"[k], font(12, bold=True), (20, 20, 40) if self.lanes[k] else (120, 130, 170), (x, y))
        for k, x in enumerate(PB_TARGETS):
            if self.targets[k]:
                pygame.draw.rect(surf, (230, 70, 90), (x - 16, 172, 32, 12), border_radius=3)
                pygame.draw.rect(surf, WHITE, (x - 16, 172, 32, 12), width=2, border_radius=3)
            else:
                pygame.draw.rect(surf, (60, 40, 50), (x - 16, 180, 32, 4))
        for k, (x, y) in enumerate(PB_BUMPERS):
            on = self.lit.get(f"b{k}", 0) > 0
            pygame.draw.circle(surf, (10, 10, 20), (x + 2, y + 4), 25)
            pygame.draw.circle(surf, (255, 240, 150) if on else (220, 60, 90), (x, y), 24)
            pygame.draw.circle(surf, WHITE, (x, y), 24, width=3)
            pygame.draw.circle(surf, (255, 255, 255) if on else (250, 180, 200), (x, y), 10)
        for k, (x1, y1, x2, y2) in enumerate(PB_SLINGS):
            if self.lit.get(f"s{k}", 0) > 0:
                pygame.draw.line(surf, (255, 240, 150), (x1, y1), (x2, y2), 7)
        for i in range(2):
            x1, y1, x2, y2 = self.flipper_seg(i)
            pygame.draw.line(surf, (20, 10, 10), (x1 + 2, y1 + 4), (x2 + 2, y2 + 4), 18)
            pygame.draw.line(surf, (230, 60, 60), (x1, y1), (x2, y2), 16)
            pygame.draw.circle(surf, (230, 60, 60), (x1, y1), 9)
            pygame.draw.circle(surf, (230, 60, 60), (x2, y2), 8)
            pygame.draw.line(surf, (255, 170, 170), (x1, y1 - 3), (x2, y2 - 3), 3)
            pygame.draw.circle(surf, (250, 250, 250), (x1, y1), 4)
        # plunger spring
        pull = self.power * 30 if self.state == "plunger" else 0
        top = PB_PLUNGER_Y + PB_BALL_R + pull
        for k in range(6):
            y = top + k * (700 + 16 - top) / 6
            pygame.draw.line(surf, (180, 180, 190), (PB_LANE_X - 8, y), (PB_LANE_X + 8, y + 4), 2)
        pygame.draw.rect(surf, (200, 60, 60), (PB_LANE_X - 10, top, 20, 6), border_radius=3)
        if self.busy():
            by = self.by + (pull if self.state == "plunger" else 0)
            pygame.draw.circle(surf, (40, 40, 60), (self.bx + 2, by + 3), PB_BALL_R)
            if not draw_theme_ball(surf, self.bx, by, PB_BALL_R + 1):
                pygame.draw.circle(surf, (225, 228, 235), (self.bx, by), PB_BALL_R)
            pygame.draw.circle(surf, WHITE, (self.bx - 3, by - 3), 3)
        for text, x, y, life in self.pops:
            img = font(16 if text.startswith("+") else 22, bold=True).render(text, True, (255, 230, 120))
            img.set_alpha(int(255 * min(1, life * 2)))
            surf.blit(img, img.get_rect(center=(x, y)))
        # left panel: score
        left = pygame.Rect(20, 72, 340, 548)
        soft_panel(surf, left, 140, (80, 70, 150))
        draw_text(surf, "SCORE", font(16, bold=True), GOLD, (left.centerx, 104))
        draw_text(surf, f"{self.score:,}", font(46, bold=True), WHITE, (left.centerx, 148))
        draw_text(surf, f"BALL {max(0, 4 - self.balls) if self.busy() else '-'} OF 3", font(18, bold=True),
                  (190, 190, 230), (left.centerx, 200))
        for k in range(3):
            pygame.draw.circle(surf, (225, 228, 235) if k < self.balls and self.busy() else (50, 50, 70),
                               (left.centerx - 30 + k * 30, 230), 9)
        draw_text(surf, f"MULTIPLIER x{self.mult}", font(20, bold=True), (255, 200, 90), (left.centerx, 270))
        tips = ["CONTROLS", "Left flipper:  LEFT arrow / Z / left click", "Right flipper:  RIGHT arrow / M / right click",
                "Launch:  hold SPACE, then let go", "", "SCORING", "Bumpers 100  -  Slingshots 50",
                "Drop targets 250  (all 3: +1,500)", "Light R-Y-L lanes: multiplier +1",
                "Ball save: first 4 seconds, once per ball"]
        for i, t in enumerate(tips):
            bold = t in ("CONTROLS", "SCORING")
            draw_text(surf, t, font(15 if bold else 14, bold=bold), GOLD if bold else (200, 200, 225),
                      (left.centerx, 318 + i * 26))
        # right panel: prizes
        right = pygame.Rect(920, 72, 340, 548)
        soft_panel(surf, right, 140, (80, 70, 150))
        draw_text(surf, "PRIZES", font(20, bold=True, serif=True), GOLD, (right.centerx, 104))
        stake = self.stake if self.state != "betting" else self.bet
        cur = pb_prize(self.score)
        for k, (need, m) in enumerate(reversed(PB_PRIZES)):
            y = 144 + k * 40
            active = m == cur and (self.busy() or self.state == "over")
            if active:
                pygame.draw.rect(surf, (40, 120, 60), (right.x + 16, y - 16, right.w - 32, 32), border_radius=6)
            draw_text(surf, f"{need:,}+" if need else "under 3,500", font(16, bold=True), WHITE, (right.x + 30, y),
                      anchor="midleft")
            draw_text(surf, f"x{m:g}" + (f"  {money(int(stake * m))}" if stake else ""), font(16, bold=True),
                      GOLD if m >= 1 else (200, 160, 160), (right.right - 30, y), anchor="midright")
        if self.message:
            draw_pill(surf, self.message, font(14, bold=True), (right.centerx if len(self.message) < 30 else W / 2, 600),
                      GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(12, 4))
        if self.busy():
            bottom_bar(surf, (10, 8, 22))
            self.tray.draw(surf, self.assets, mouse, disabled=True)
            draw_text(surf, f"BET {money(self.stake)}", font(15, bold=True), GOLD, (115, 677))
            draw_text(surf, "GAME IN PROGRESS", font(18, bold=True), (200, 200, 230), (1100, 677))
        else:
            self.btn_start.text = "PLAY AGAIN" if self.state == "over" else "START  (3 BALLS)"
            self.draw_bottom(surf, mouse, self.btn_start, 0 < self.bet <= self.app.balance, (10, 8, 22))
        self.app.draw_top_bar(surf, "PINBALL", lobby=True, lobby_enabled=self.can_leave())


# --------------------------------------------------------------------------
# Go Work - earn chips the honest way when you're broke
# --------------------------------------------------------------------------
JOBS = {
    "cashier": ("GROCERY CASHIER", "Count out the exact change for each customer.", (40, 140, 90), 30,
                ["The register tells you how much change is due.", "Click bills and coins to build that exact amount.",
                 "Press GIVE CHANGE. Get it exactly right to get paid!"]),
    "pizza": ("PIZZA DELIVERY", "Find the right house before the pizza gets cold.", (200, 80, 40), 12,
              ["Read the address on the order ticket.", "Find the right street, then the right house number.",
               "Click the house. Wrong house = no tip and no pay!"]),
    "dishes": ("DISHWASHER", "Scrub every plate until it sparkles.", (60, 120, 190), 18,
               ["Hold the mouse button down and rub over the dirt.", "Get the plate completely clean.",
                "The faster you finish, the bigger your tip!"]),
    "barista": ("BARISTA", "Memorize the order, then make the drink.", (130, 85, 50), 22,
                ["An order appears for a few seconds - memorize it!", "Then click the ingredients to build the drink.",
                 "Press SERVE. Every ingredient count must be right."]),
    "marine": ("MARINE BIOLOGIST", "Count sea animals on a coral reef survey.", (30, 110, 160), 16,
               ["You'll be told which animal to count.", "Watch the reef carefully as the animals swim past.",
                "Then pick how many you saw."]),
}
SHIFT_TASKS = 5
TASK_BASE, TASK_BONUS = 60, 40          # per task: $60 for doing it right + up to $40 for speed
MONEY_PIECES = [(2000, "$20"), (1000, "$10"), (500, "$5"), (100, "$1"), (25, "25c"), (10, "10c"), (5, "5c"), (1, "1c")]
STREETS = ["MAPLE ST", "OAK AVE"]
DRINK_ITEMS = [("ESPRESSO", (70, 40, 20)), ("MILK", (245, 240, 230)), ("FOAM", (255, 250, 245)),
               ("CHOCOLATE", (100, 55, 30)), ("CARAMEL", (210, 140, 50)), ("ICE", (190, 225, 245))]
REEF_ANIMALS = [("SEA TURTLES", "turtle", (80, 140, 80)), ("CLOWNFISH", "fish", (245, 120, 40)),
                ("JELLYFISH", "jelly", (210, 90, 160)), ("OCTOPUSES", "octopus", (235, 130, 120))]


class Work:
    key = "work"

    def __init__(self, app):
        self.app = app
        self.assets = app.assets
        self.bg = gradient_bg((26, 30, 44), (10, 12, 20))
        self.t = 0.0
        self.new_board()
        self.btn_start = Button((W / 2 - 150, 560, 300, 62), "START SHIFT", (25, 120, 60), 24, "SPACE")
        self.btn_cash = Button((W / 2 - 170, 560, 340, 64), "CASH IN FOR CHIPS", (25, 120, 60), 24, "SPACE")
        self.btn_back = Button((40, 652, 200, 52), "< JOB BOARD", (60, 70, 90), 18)
        # cashier
        self.cash_btns = [Button((360 + (i % 4) * 150, 380 + (i // 4) * 96, 136, 82), label,
                                 (50, 120, 70) if v >= 100 else (150, 120, 60), 22) for i, (v, label) in enumerate(MONEY_PIECES)]
        self.btn_give = Button((990, 520, 230, 62), "GIVE CHANGE", (25, 120, 60), 22)
        self.btn_undo = Button((990, 440, 230, 52), "UNDO", (80, 80, 90), 18)
        # barista
        self.drink_btns = [Button((330 + (i % 3) * 190, 330 + (i // 3) * 96, 176, 80), name, darken(col, 40)
                                  if name not in ("MILK", "FOAM", "ICE") else (90, 100, 120), 18)
                           for i, (name, col) in enumerate(DRINK_ITEMS)]
        self.btn_serve = Button((1000, 520, 220, 62), "SERVE", (25, 120, 60), 24)
        self.btn_dump = Button((1000, 440, 220, 52), "START OVER", (120, 35, 40), 18)
        # marine
        self.fish_btns = [Button((W / 2 - 380 + i * 195, 500, 175, 70), "", (30, 90, 140), 30) for i in range(4)]

    def can_leave(self):
        return True

    def outstanding_bets(self):
        return 0

    def leave(self):
        if self.state in ("task", "feedback") and self.earned:     # paid for work already done
            self.cash_in(silent=True)
        if self.state != "board":
            self.new_board()

    # ---- flow --------------------------------------------------------------
    def new_board(self):
        self.state = "board"
        self.offers = random.sample(list(JOBS), 3)
        self.job = None
        self.results = []
        self.earned = 0
        self.task_no = 0

    def pick(self, job):
        self.job = job
        self.state = "intro"
        self.app.sfx("chip")

    def start_shift(self):
        self.results, self.earned, self.task_no = [], 0, 0
        self.next_task()

    def next_task(self):
        self.task_no += 1
        if self.task_no > SHIFT_TASKS:
            self.state = "paycheck"
            if all(ok for ok, _ in self.results):
                self.app.unlock("employee_month")
            return
        self.state = "task"
        self.time_left = self.limit = JOBS[self.job][3]
        getattr(self, f"setup_{self.job}")()

    def task_done(self, ok, note):
        pay = int(TASK_BASE + TASK_BONUS * max(0.0, self.time_left / self.limit)) if ok else 0
        self.results.append((ok, pay))
        self.earned += pay
        self.feedback = (note, ok, pay)
        self.state = "feedback"
        self.fb_time = 1.6
        self.app.sfx("win" if ok else "lose")

    def cash_in(self, silent=False):
        if self.earned:
            self.app.balance += self.earned
            self.app.float_text(f"+{money(self.earned)}", (80, 230, 110))
            if not silent:
                self.app.effects.chip_rain(30)
                self.app.effects.toast("PAYDAY!", f"You turned {money(self.earned)} of wages into chips.")
            self.app.save()
        self.earned = 0
        self.new_board()
        if not silent:
            self.app.scene = "menu"

    # ---- task setup ----------------------------------------------------------
    def setup_cashier(self):
        price = random.randint(120, 4999)
        paid = next(b for b in (500, 1000, 2000, 5000, 10000) if b > price)
        self.price, self.paid, self.given = price, paid, []

    def setup_pizza(self):
        self.houses = []
        for row, street in enumerate(STREETS):
            nums = sorted(random.sample(range(100, 990, 2) if row else range(101, 991, 2), 8))
            for k, n in enumerate(nums):
                self.houses.append((street, n, pygame.Rect(150 + k * 124, 176 + row * 230, 104, 110),
                                    random.choice([(200, 90, 80), (90, 140, 200), (230, 200, 110), (140, 190, 130),
                                                   (190, 140, 200)])))
        self.target = random.choice(self.houses)[:2]

    def setup_dishes(self):
        self.dirt = pygame.Surface((400, 400), pygame.SRCALPHA)
        self.dirty_cells = set()
        for _ in range(16):
            a, r = random.uniform(0, 2 * math.pi), random.uniform(0, 140)
            cx, cy = 200 + math.cos(a) * r, 200 + math.sin(a) * r
            col = random.choice([(120, 90, 50), (90, 110, 50), (150, 70, 40), (110, 80, 60)])
            for _ in range(5):
                pygame.draw.circle(self.dirt, (*col, 235), (cx + random.uniform(-18, 18), cy + random.uniform(-18, 18)),
                                   random.uniform(10, 24))
        for gy in range(0, 400, 10):
            for gx in range(0, 400, 10):
                if self.dirt.get_at((gx + 5, gy + 5))[3] > 0:
                    self.dirty_cells.add((gx // 10, gy // 10))
        self.clean_cells = set()
        self.last_scrub = None

    def setup_barista(self):
        names = random.sample([n for n, _ in DRINK_ITEMS], 3)
        self.order = {n: random.randint(1, 2) for n in names}
        self.cup = []
        self.memorize = 4.0

    def setup_marine(self):
        self.target_animal = random.choice(REEF_ANIMALS)
        self.answer = random.randint(2, 7)
        pool = [self.target_animal] * self.answer
        others = [a for a in REEF_ANIMALS if a is not self.target_animal]
        pool += [random.choice(others) for _ in range(random.randint(4, 8))]
        random.shuffle(pool)
        self.swimmers = []
        for i, (name, kind, col) in enumerate(pool):
            f = random.choice((1, -1))
            self.swimmers.append({"kind": kind, "col": col, "f": f, "delay": i * 0.55,
                                  "x": -80 if f == 1 else W + 80, "y": random.uniform(170, 520),
                                  "vx": random.uniform(170, 230) * f})
        self.watch = 0.0
        self.watch_len = max(s["delay"] for s in self.swimmers) + 6.5
        opts = {self.answer}
        while len(opts) < 4:
            opts.add(max(0, self.answer + random.choice([-2, -1, 1, 2, 3])))
        self.options = sorted(opts)

    # ---- events ------------------------------------------------------------
    def handle(self, e):
        click = e.type == pygame.MOUSEBUTTONDOWN and e.button == 1
        space = e.type == pygame.KEYDOWN and e.key in (pygame.K_SPACE, pygame.K_RETURN)
        if self.state == "board":
            if click:
                for i, job in enumerate(self.offers):
                    if self.card_rect(i).collidepoint(e.pos):
                        self.pick(job)
        elif self.state == "intro":
            if (click and self.btn_start.clicked(e.pos)) or space:
                self.start_shift()
            elif click and self.btn_back.clicked(e.pos):
                self.state = "board"
        elif self.state == "paycheck":
            if (click and self.btn_cash.clicked(e.pos)) or space:
                self.cash_in()
        elif self.state == "task" and click:
            getattr(self, f"click_{self.job}")(e.pos)

    def click_cashier(self, pos):
        for b, (v, _) in zip(self.cash_btns, MONEY_PIECES):
            if b.clicked(pos):
                self.given.append(v)
                self.app.sfx("chip")
        if self.btn_undo.clicked(pos, bool(self.given)):
            self.given.pop()
        elif self.btn_give.clicked(pos, bool(self.given)):
            due = self.paid - self.price
            got = sum(self.given)
            self.task_done(got == due, "EXACT CHANGE - THANK YOU!" if got == due else
                           f"THAT'S {money(got // 100)}.{got % 100:02d} - THE RIGHT CHANGE WAS "
                           f"{money(due // 100)}.{due % 100:02d}")

    def click_pizza(self, pos):
        for street, n, r, _ in self.houses:
            if r.collidepoint(pos):
                ok = (street, n) == self.target
                self.task_done(ok, "PIZZA DELIVERED - HOT AND ON TIME!" if ok else
                               f"WRONG HOUSE! That was {n} {street.title()}")

    def click_dishes(self, pos):
        pass

    def click_barista(self, pos):
        if self.memorize > 0:
            return
        for b, (name, _) in zip(self.drink_btns, DRINK_ITEMS):
            if b.clicked(pos) and len(self.cup) < 8:
                self.cup.append(name)
                self.app.sfx("card")
        if self.btn_dump.clicked(pos, bool(self.cup)):
            self.cup = []
        elif self.btn_serve.clicked(pos, bool(self.cup)):
            made = {n: self.cup.count(n) for n in set(self.cup)}
            ok = made == self.order
            want = ", ".join(f"{c} {n.lower()}" for n, c in self.order.items())
            self.task_done(ok, "PERFECT DRINK - THE CUSTOMER LOVES IT!" if ok else f"WRONG ORDER! They wanted {want}")

    def click_marine(self, pos):
        if self.watch < self.watch_len:
            return
        for b, v in zip(self.fish_btns, self.options):
            if b.clicked(pos):
                ok = v == self.answer
                self.task_done(ok, f"CORRECT - {self.answer} {self.target_animal[0].lower()} recorded!" if ok else
                               f"NOT QUITE - there were {self.answer} {self.target_animal[0].lower()}")

    # ---- update ------------------------------------------------------------
    def update(self, dt):
        self.t += dt
        if self.state == "feedback":
            self.fb_time -= dt
            if self.fb_time <= 0:
                self.next_task()
            return
        if self.state != "task":
            return
        counting = not (self.job == "barista" and self.memorize > 0) and \
            not (self.job == "marine" and self.watch < self.watch_len)
        if counting:
            self.time_left -= dt
            if self.time_left <= 0:
                self.time_left = 0
                self.task_done(False, "OUT OF TIME!")
                return
        if self.job == "barista" and self.memorize > 0:
            self.memorize -= dt
        if self.job == "marine":
            self.watch += dt
            for s in self.swimmers:
                if self.watch > s["delay"]:
                    s["x"] += s["vx"] * dt
        if self.job == "dishes":
            pos = pygame.mouse.get_pos()
            if pygame.mouse.get_pressed()[0]:
                if self.last_scrub:
                    x0, y0 = self.last_scrub
                    n = max(1, int(math.hypot(pos[0] - x0, pos[1] - y0) / 8))
                    for k in range(1, n + 1):
                        self.scrub(x0 + (pos[0] - x0) * k / n, y0 + (pos[1] - y0) * k / n)
                else:
                    self.scrub(*pos)
                self.last_scrub = pos
            else:
                self.last_scrub = None

    def scrub(self, x, y):
        lx, ly = x - (W / 2 - 200), y - 160
        pygame.draw.circle(self.dirt, (0, 0, 0, 0), (lx, ly), 28)
        for gy in range(int((ly - 28) // 10), int((ly + 28) // 10) + 1):
            for gx in range(int((lx - 28) // 10), int((lx + 28) // 10) + 1):
                if (gx, gy) in self.dirty_cells and (gx * 10 + 5 - lx) ** 2 + (gy * 10 + 5 - ly) ** 2 <= 28 * 28:
                    self.clean_cells.add((gx, gy))
        if random.random() < 0.15:
            self.app.sfx("card")
        if self.state == "task" and len(self.clean_cells) >= 0.95 * len(self.dirty_cells):
            self.dirt.fill((0, 0, 0, 0))
            self.task_done(True, "SPARKLING CLEAN!")

    # ---- drawing -------------------------------------------------------------
    def card_rect(self, i):
        return pygame.Rect(70 + i * 390, 190, 360, 360)

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        if self.state == "board":
            draw_text(surf, "JOB BOARD", font(40, bold=True, serif=True), GOLD, (W / 2, 104))
            draw_text(surf, "Out of chips? Pick a job, finish a shift of 5 tasks, and cash your paycheck in for chips.",
                      font(17), (210, 210, 225), (W / 2, 146))
            for i, job in enumerate(self.offers):
                title, desc, col, limit, _ = JOBS[job]
                r = self.card_rect(i)
                hover = r.collidepoint(mouse)
                pygame.draw.rect(surf, (5, 5, 8), r.move(0, 8), border_radius=18)
                pygame.draw.rect(surf, lighten(col, 20) if hover else col, r, border_radius=18)
                pygame.draw.rect(surf, GOLD if hover else WHITE, r, width=3, border_radius=18)
                self.job_icon(surf, job, r.centerx, r.y + 100)
                draw_text(surf, title, font(26, bold=True, serif=True), WHITE, (r.centerx, r.y + 196), shadow=(0, 0, 0))
                for k, ln in enumerate(wrap_text(desc, font(16), r.w - 40)):
                    draw_text(surf, ln, font(16), (240, 240, 240), (r.centerx, r.y + 234 + k * 22))
                draw_pill(surf, f"UP TO {money((TASK_BASE + TASK_BONUS) * SHIFT_TASKS)} A SHIFT", font(15, bold=True),
                          (r.centerx, r.bottom - 36), (20, 20, 20), (*GOLD, 255), None, pad=(14, 4))
        elif self.state == "intro":
            title, desc, col, limit, steps = JOBS[self.job]
            panel = pygame.Rect(W / 2 - 360, 100, 720, 430)
            soft_panel(surf, panel, 170, col, 20)
            self.job_icon(surf, self.job, W / 2, 190)
            draw_text(surf, title, font(34, bold=True, serif=True), GOLD, (W / 2, 280))
            for k, step in enumerate(steps):
                draw_text(surf, f"{k + 1}.  {step}", font(18), WHITE, (W / 2, 334 + k * 34))
            draw_text(surf, f"{SHIFT_TASKS} tasks  -  {limit} seconds each  -  ${TASK_BASE} per task done right, "
                            f"plus up to ${TASK_BONUS} for speed", font(15, bold=True), (200, 220, 200), (W / 2, 470))
            self.btn_start.draw(surf, mouse)
            self.btn_back.draw(surf, mouse)
        elif self.state == "paycheck":
            panel = pygame.Rect(W / 2 - 300, 90, 600, 450)
            pygame.draw.rect(surf, (245, 240, 225), panel, border_radius=12)
            pygame.draw.rect(surf, (90, 130, 90), panel, width=4, border_radius=12)
            draw_text(surf, "PAYCHECK", font(34, bold=True, serif=True), (40, 90, 50), (W / 2, 130))
            draw_text(surf, JOBS[self.job][0].title(), font(16, bold=True), (90, 90, 80), (W / 2, 166))
            for k, (ok, pay) in enumerate(self.results):
                y = 210 + k * 40
                draw_text(surf, f"Task {k + 1}", font(18, bold=True), (60, 60, 50), (panel.x + 60, y), anchor="midleft")
                draw_text(surf, "done" if ok else "failed", font(16), (40, 130, 60) if ok else (180, 50, 50),
                          (panel.centerx, y))
                draw_text(surf, money(pay), font(18, bold=True), (40, 40, 30), (panel.right - 60, y), anchor="midright")
            pygame.draw.line(surf, (90, 90, 80), (panel.x + 40, 420), (panel.right - 40, 420), 2)
            draw_text(surf, "TOTAL", font(22, bold=True), (40, 40, 30), (panel.x + 60, 452), anchor="midleft")
            draw_text(surf, money(self.earned), font(30, bold=True), (40, 110, 50), (panel.right - 60, 452), anchor="midright")
            if all(ok for ok, _ in self.results):
                draw_text(surf, "PERFECT SHIFT - EMPLOYEE OF THE MONTH!", font(15, bold=True), (170, 120, 20),
                          (W / 2, 500))
            self.btn_cash.draw(surf, mouse)
        else:
            getattr(self, f"draw_{self.job}")(surf, mouse)
            self.draw_hud(surf)
            if self.state == "feedback":
                note, ok, pay = self.feedback
                col = (40, 150, 70) if ok else (170, 40, 45)
                draw_pill(surf, note + (f"   +{money(pay)}" if ok else ""), font(22, bold=True), (W / 2, 330), WHITE,
                          (*col, 240), GOLD, pad=(22, 10))
        bottom_bar(surf, (10, 12, 20))
        if self.state in ("task", "feedback"):
            draw_text(surf, f"EARNED THIS SHIFT: {money(self.earned)}", font(20, bold=True), (120, 230, 140), (W / 2, 678))
        elif self.state == "board":
            draw_text(surf, "Real jobs, fake money: every dollar you earn becomes chips.", font(15), (170, 170, 190),
                      (W / 2, 678))
        self.app.draw_top_bar(surf, "GO WORK", lobby=True)

    def draw_hud(self, surf):
        title = JOBS[self.job][0]
        draw_text(surf, f"{title}  -  TASK {min(self.task_no, SHIFT_TASKS)} OF {SHIFT_TASKS}", font(18, bold=True),
                  GOLD, (40, 80), anchor="midleft")
        bar = pygame.Rect(W - 440, 70, 400, 20)
        pygame.draw.rect(surf, (40, 40, 50), bar, border_radius=10)
        frac = self.time_left / self.limit if self.limit else 0
        col = (80, 220, 120) if frac > 0.5 else (240, 190, 60) if frac > 0.25 else (240, 80, 70)
        pygame.draw.rect(surf, col, (bar.x, bar.y, bar.w * frac, bar.h), border_radius=10)
        draw_text(surf, f"{self.time_left:.0f}s", font(14, bold=True), WHITE, bar.center)

    def job_icon(self, surf, job, x, y):
        if job == "cashier":
            pygame.draw.rect(surf, (60, 60, 70), (x - 60, y - 20, 120, 60), border_radius=8)
            pygame.draw.rect(surf, (90, 200, 120), (x - 45, y - 45, 90, 30), border_radius=4)
            draw_text(surf, "$6.53", font(18, bold=True), (10, 40, 20), (x, y - 30))
            for k in range(3):
                pygame.draw.rect(surf, (200, 200, 210), (x - 40 + k * 30, y, 20, 12), border_radius=3)
        elif job == "pizza":
            pygame.draw.circle(surf, (230, 170, 70), (x, y), 55)
            pygame.draw.circle(surf, (220, 60, 40), (x, y), 45)
            for a in range(6):
                pygame.draw.line(surf, (230, 170, 70), (x, y), (x + math.cos(a) * 45, y + math.sin(a) * 45), 3)
            for px, py in ((-20, -15), (18, -8), (-5, 20), (22, 22), (-25, 12)):
                pygame.draw.circle(surf, (140, 30, 30), (x + px, y + py), 7)
        elif job == "dishes":
            pygame.draw.circle(surf, (240, 245, 250), (x, y), 55)
            pygame.draw.circle(surf, (200, 210, 225), (x, y), 38, width=3)
            for k in range(5):
                pygame.draw.circle(surf, (220, 240, 255), (x + 40 + k * 6, y - 40 + k * 9), 6 - k, width=1)
        elif job == "barista":
            pygame.draw.rect(surf, (245, 240, 230), (x - 35, y - 40, 70, 80), border_radius=8)
            pygame.draw.rect(surf, (90, 50, 25), (x - 30, y - 34, 60, 30), border_radius=6)
            pygame.draw.circle(surf, (245, 240, 230), (x + 45, y), 16, width=6)
            for k in range(3):
                pygame.draw.arc(surf, (220, 220, 220), (x - 20 + k * 14, y - 80, 14, 30), 0, math.pi, 2)
        else:
            draw_creature(surf, "turtle", x - 20, y, 1.6, (80, 140, 80), 1, self.t)
            draw_creature(surf, "fish", x + 50, y - 40, 1.2, (245, 120, 40), -1, self.t)

    def draw_cashier(self, surf, mouse):
        due = self.paid - self.price
        reg = pygame.Rect(360, 110, 590, 240)
        pygame.draw.rect(surf, (40, 44, 56), reg, border_radius=16)
        pygame.draw.rect(surf, (150, 150, 170), reg, width=3, border_radius=16)
        screen = pygame.Rect(reg.x + 24, reg.y + 20, reg.w - 48, 120)
        pygame.draw.rect(surf, (20, 50, 30), screen, border_radius=8)
        for k, (label, v) in enumerate((("TOTAL", self.price), ("CUSTOMER PAID", self.paid), ("CHANGE DUE", due))):
            draw_text(surf, label, font(18, bold=True), (140, 230, 160), (screen.x + 20, screen.y + 24 + k * 36),
                      anchor="midleft")
            draw_text(surf, f"${v // 100}.{v % 100:02d}", font(24, bold=True), (170, 255, 190) if k == 2 else (140, 230, 160),
                      (screen.right - 20, screen.y + 24 + k * 36), anchor="midright")
        got = sum(self.given)
        draw_text(surf, f"YOU'RE GIVING:  ${got // 100}.{got % 100:02d}", font(24, bold=True),
                  (120, 230, 140) if got == due else WHITE, (reg.centerx, reg.bottom - 50))
        for b, (v, label) in zip(self.cash_btns, MONEY_PIECES):
            b.draw(surf, mouse, self.state == "task")
        # the change tray
        tray = pygame.Rect(990, 110, 230, 310)
        soft_panel(surf, tray, 150, (150, 150, 170))
        draw_text(surf, "CHANGE", font(15, bold=True), GOLD, (tray.centerx, tray.y + 20))
        for k, v in enumerate(self.given[-14:]):
            x, y = tray.x + 40 + (k % 3) * 75, tray.y + 60 + (k // 3) * 48
            if v >= 100:
                pygame.draw.rect(surf, (90, 160, 100), (x - 30, y - 16, 60, 32), border_radius=4)
                draw_text(surf, f"${v // 100}", font(14, bold=True), (20, 50, 30), (x, y))
            else:
                pygame.draw.circle(surf, (200, 160, 80) if v == 1 else (200, 200, 210), (x, y), 18)
                draw_text(surf, f"{v}c", font(13, bold=True), (40, 40, 40), (x, y))
        self.btn_undo.draw(surf, mouse, bool(self.given) and self.state == "task")
        self.btn_give.draw(surf, mouse, bool(self.given) and self.state == "task")

    def draw_pizza(self, surf, mouse):
        ticket = pygame.Rect(W / 2 - 200, 100, 400, 56)
        pygame.draw.rect(surf, (250, 245, 230), ticket, border_radius=8)
        pygame.draw.rect(surf, (200, 80, 40), ticket, width=3, border_radius=8)
        draw_text(surf, f"DELIVER TO:  {self.target[1]} {self.target[0].title()}", font(22, bold=True), (60, 30, 20),
                  ticket.center)
        for row, street in enumerate(STREETS):
            y = 300 + row * 230
            pygame.draw.rect(surf, (60, 60, 66), (40, y, W - 80, 44))
            for x in range(60, W - 60, 60):
                pygame.draw.rect(surf, (230, 210, 90), (x, y + 20, 30, 4))
            sign = pygame.Rect(50, y - 10, 120, 28)
            pygame.draw.rect(surf, (30, 110, 60), sign, border_radius=4)
            draw_text(surf, street, font(14, bold=True), WHITE, sign.center)
        for street, n, r, col in self.houses:
            hover = r.collidepoint(mouse) and self.state == "task"
            body = pygame.Rect(r.x + 10, r.y + 40, r.w - 20, r.h - 40)
            pygame.draw.polygon(surf, darken(col, 50), [(r.x, r.y + 44), (r.centerx, r.y + 4), (r.right, r.y + 44)])
            pygame.draw.rect(surf, lighten(col, 30) if hover else col, body)
            pygame.draw.rect(surf, (80, 50, 30), (body.centerx - 10, body.bottom - 34, 20, 34))
            plate = pygame.Rect(0, 0, 54, 22)
            plate.center = (body.centerx, body.y + 16)
            pygame.draw.rect(surf, WHITE, plate, border_radius=4)
            draw_text(surf, str(n), font(15, bold=True), (20, 20, 20), plate.center)
            if hover:
                pygame.draw.rect(surf, GOLD, r.inflate(8, 8), width=3, border_radius=8)

    def draw_dishes(self, surf, mouse):
        draw_text(surf, "Hold the mouse button and scrub off all the dirt!", font(18), (210, 220, 235), (W / 2, 130))
        cx, cy = W / 2, 360
        pygame.draw.circle(surf, (20, 30, 40), (cx + 6, cy + 10), 190)
        pygame.draw.circle(surf, (245, 248, 252), (cx, cy), 190)
        pygame.draw.circle(surf, (215, 222, 235), (cx, cy), 135, width=4)
        surf.blit(self.dirt, (cx - 200, cy - 200))
        pct = len(self.clean_cells) / max(1, len(self.dirty_cells)) * 100
        draw_text(surf, f"CLEAN: {min(100, pct):.0f}%", font(22, bold=True), (120, 230, 140), (W - 180, 140))
        if self.state == "task":
            mx, my = mouse
            pygame.draw.rect(surf, (240, 220, 60), (mx - 24, my - 16, 48, 32), border_radius=8)
            pygame.draw.rect(surf, (60, 150, 60), (mx - 24, my - 16, 48, 10), border_radius=4)

    def draw_barista(self, surf, mouse):
        card = pygame.Rect(W / 2 - 260, 110, 520, 170)
        if self.memorize > 0:
            pygame.draw.rect(surf, (250, 245, 230), card, border_radius=12)
            pygame.draw.rect(surf, (130, 85, 50), card, width=4, border_radius=12)
            draw_text(surf, f"ORDER - MEMORIZE IT!  ({self.memorize:.0f})", font(20, bold=True), (130, 60, 30),
                      (card.centerx, card.y + 30))
            for k, (n, c) in enumerate(self.order.items()):
                draw_text(surf, f"{c}  x  {n}", font(28, bold=True), (50, 30, 20), (card.centerx, card.y + 76 + k * 34))
            return
        draw_text(surf, "Build the drink from memory, then SERVE it.", font(18), (220, 210, 200), (W / 2, 140))
        for b in self.drink_btns:
            b.draw(surf, mouse, self.state == "task")
        # the cup, layer by layer
        cup = pygame.Rect(W / 2 + 270, 240, 120, 250)
        pygame.draw.rect(surf, (230, 235, 240), cup, width=4, border_radius=10)
        for k, name in enumerate(self.cup):
            col = dict(DRINK_ITEMS)[name]
            layer = pygame.Rect(cup.x + 6, cup.bottom - 8 - (k + 1) * 29, cup.w - 12, 27)
            pygame.draw.rect(surf, col, layer, border_radius=5)
            draw_text(surf, name[:4], font(11, bold=True), (40, 30, 20) if sum(col) > 400 else WHITE, layer.center)
        self.btn_dump.draw(surf, mouse, bool(self.cup) and self.state == "task")
        self.btn_serve.draw(surf, mouse, bool(self.cup) and self.state == "task")

    def draw_marine(self, surf, mouse):
        for y in range(56, 636, 4):
            pygame.draw.rect(surf, lerp_col((40, 150, 200), (10, 60, 110), (y - 56) / 580), (0, y, W, 4))
        for k in range(14):                  # coral
            x = 60 + k * 90
            col = [(240, 100, 120), (250, 170, 60), (180, 90, 200), (90, 200, 160)][k % 4]
            for j in range(4):
                pygame.draw.line(surf, col, (x, 636), (x + math.sin(j + k) * 30, 560 + j * 8), 7)
        if self.watch < self.watch_len:
            draw_pill(surf, f"COUNT THE {self.target_animal[0]}!", font(24, bold=True), (W / 2, 110), WHITE,
                      (10, 40, 70, 230), GOLD, pad=(20, 6))
            for s in self.swimmers:
                if self.watch > s["delay"]:
                    draw_creature(surf, s["kind"], s["x"], s["y"], 1.3, s["col"], s["f"], self.t)
            return
        draw_pill(surf, f"HOW MANY {self.target_animal[0]} DID YOU SEE?", font(26, bold=True), (W / 2, 300), WHITE,
                  (10, 40, 70, 230), GOLD, pad=(22, 8))
        for b, v in zip(self.fish_btns, self.options):
            b.text = str(v)
            b.draw(surf, mouse, self.state == "task")


# --------------------------------------------------------------------------
# Chicken Crossing - hop across an endless road, cash out while it's safe
# --------------------------------------------------------------------------
CR_T = 64                       # size of one tile
CR_COLS = 20
CR_BASE_Y = 560                 # screen y of the row the camera sits on
CR_HOP = 0.12                   # seconds per hop
CR_TRAIN_LEN = 10 * CR_T
CR_SPEED = (150, 330)           # car speed on the first roads, and how much faster they get
CR_GAP = (3.4, 1.8)             # gap between cars (in tiles) on the first roads, and how much it shrinks
CR_STEP = (1.0, 0.18)           # multiplier per road at the start, and how much more later roads give
CR_CREEP = (0.1, 0.0035, 0.35)   # screen scroll speed (rows/s): start, increase per row, maximum
CR_CAR_COLS = [(220, 60, 60), (60, 120, 220), (240, 190, 50), (90, 190, 110), (230, 120, 40), (170, 90, 200),
               (235, 235, 240), (60, 200, 210)]


def cr_step(row):
    """How much the multiplier grows when you reach road number `row` - later roads are worth more."""
    return CR_STEP[0] + CR_STEP[1] * min(1.0, row / 80)


def cr_car_sprite(length, col, truck, direction):
    L, h = int(length), 50
    s = pygame.Surface((L, h), pygame.SRCALPHA)
    pygame.draw.rect(s, (0, 0, 0, 90), (4, 8, L - 4, h - 8), border_radius=10)
    for wx in (0.18, 0.78) if not truck else (0.1, 0.3, 0.72, 0.9):
        pygame.draw.rect(s, (20, 20, 22), (L * wx - 8, 1, 16, 8), border_radius=3)
        pygame.draw.rect(s, (20, 20, 22), (L * wx - 8, h - 9, 16, 8), border_radius=3)
    if truck:
        cab = 44
        pygame.draw.rect(s, (225, 225, 230), (0, 5, L - cab - 4, h - 10), border_radius=6)
        pygame.draw.rect(s, (175, 175, 185), (0, 5, L - cab - 4, h - 10), width=2, border_radius=6)
        pygame.draw.rect(s, col, (L - cab, 6, cab, h - 12), border_radius=9)
        pygame.draw.rect(s, (40, 60, 80), (L - 18, 10, 10, h - 20), border_radius=3)
    else:
        pygame.draw.rect(s, col, (0, 5, L, h - 10), border_radius=13)
        pygame.draw.rect(s, darken(col, 40), (0, 5, L, h - 10), width=2, border_radius=13)
        pygame.draw.rect(s, lighten(col, 30), (L * 0.28, 11, L * 0.42, h - 22), border_radius=7)
        pygame.draw.rect(s, (40, 60, 80), (L * 0.62, 12, 9, h - 24), border_radius=3)
        pygame.draw.rect(s, (40, 60, 80), (L * 0.22, 12, 7, h - 24), border_radius=3)
    pygame.draw.circle(s, (255, 240, 150), (L - 5, 12), 4)
    pygame.draw.circle(s, (255, 240, 150), (L - 5, h - 12), 4)
    pygame.draw.circle(s, (200, 30, 30), (4, 12), 3)
    pygame.draw.circle(s, (200, 30, 30), (4, h - 12), 3)
    return s if direction > 0 else pygame.transform.flip(s, True, False)


def draw_chicken(surf, x, y, face=(1, 0), squash=1.0, scale=1.0):
    k = scale
    w, h = 38 * k / squash, 38 * k * squash
    body = pygame.Rect(0, 0, w, h)
    body.center = (x, y)
    pygame.draw.rect(surf, (250, 250, 245), body, border_radius=int(10 * k))
    pygame.draw.rect(surf, (200, 200, 195), body, width=2, border_radius=int(10 * k))
    dr, dc = face
    fx, fy = (dc, -dr) if (dr or dc) else (0, -1)       # facing direction on screen
    side = -1 if fx < 0 else 1
    pygame.draw.ellipse(surf, (225, 225, 220), (x - 14 * k * side - 6 * k, y - 2 * k, 12 * k, 14 * k))
    pygame.draw.rect(surf, (225, 40, 40), (x - 7 * k, body.top - 7 * k, 14 * k, 9 * k), border_radius=int(4 * k))
    bx, by = x + fx * 20 * k, y + fy * 20 * k - 3 * k
    pygame.draw.polygon(surf, (250, 160, 30), [(bx + fx * 9 * k, by + fy * 9 * k), (bx - fy * 6 * k, by + fx * 6 * k),
                                               (bx + fy * 6 * k, by - fx * 6 * k)])
    for ex in (-7, 7):
        if fx and ex * fx < 0:
            continue
        pygame.draw.circle(surf, (20, 20, 20), (x + ex * k + fx * 6 * k, y - 6 * k + fy * 4 * k), 3 * k)
    if squash >= 1:
        pygame.draw.rect(surf, (230, 60, 60), (x - 4 * k, y + 5 * k, 8 * k, 6 * k), border_radius=2)
        draw_chicken_costume(surf, x, y, k, body.top)


class Crossy(StakeGame):
    key = "crossy"

    def __init__(self, app):
        super().__init__(app, ((12, 20, 10), (70, 110, 60)))
        self.state = "betting"          # betting, play, over
        self.stake = 0
        self.t = 0.0
        self.sprites = {}
        self.feathers = []
        self.shadow = pygame.Surface((34, 12), pygame.SRCALPHA)
        pygame.draw.ellipse(self.shadow, (0, 0, 0, 60), self.shadow.get_rect())
        self.message = "SET YOUR BET, THEN PRESS START"
        self.btn_start = Button((965, 646, 270, 62), "START", (25, 120, 60), 26, "SPACE")
        self.btn_cash = Button((965, 646, 270, 62), "CASH OUT", (215, 120, 15), 22, "ENTER or C")
        self.reset_world()

    # ---- world -----------------------------------------------------------
    def reset_world(self):
        self.rows = []
        self.plan = []
        self.pr, self.pc = 0, CR_COLS // 2
        self.hop = None                 # [from_row, from_col, time]
        self.queued = None
        self.face = (1, 0)
        self.cam = 0.0
        self.maxrow = 0
        self.mult = 1.0
        self.roads = 0
        self.started = False
        self.splat = None
        self.eagle = None
        self.ensure(16)

    def ensure(self, n):
        while len(self.rows) <= n:
            self.rows.append(self.gen_row(len(self.rows)))

    def gen_row(self, i):
        if i < 3:
            kind = "grass"
        else:
            if not self.plan:
                d = min(1.0, i / 80)
                if i > 14 and random.random() < 0.12 + 0.1 * d:
                    block = ["rail"]
                else:
                    block = ["road"] * random.randint(1, min(5, 2 + i // 15))
                self.plan = block + ["grass"] * (2 if random.random() < 0.35 * (1 - d) else 1)
            kind = self.plan.pop(0)
        row = {"kind": kind, "trees": set()}
        d = min(1.0, i / 80)
        if kind == "grass" and i >= 3:
            row["trees"] = set(random.sample(range(1, CR_COLS - 1), random.choice((0, 1, 1, 2, 2, 3))))
        elif kind == "road":
            row["dir"] = random.choice((-1, 1))
            row["speed"] = (CR_SPEED[0] + CR_SPEED[1] * d) * random.uniform(0.75, 1.25)
            gap = (CR_GAP[0] - CR_GAP[1] * d) * CR_T
            row["gap"] = (gap, gap * 2.2)
            row["truck"] = 0.15 + 0.3 * d
            row["cars"] = []
            self.fill(row, initial=True)
        elif kind == "rail":
            row["dir"] = random.choice((-1, 1))
            row["timer"] = random.uniform(2.5, 7)
            row["train"] = None
        return row

    def fill(self, row, initial=False):
        cars, lo, hi = row["cars"], -320, W + 320
        def new():
            truck = random.random() < row["truck"]
            return [0.0, CR_T * (2.1 if truck else 1.15), random.choice(CR_CAR_COLS), truck]
        if row["dir"] > 0:
            rear = min((c[0] for c in cars), default=hi - random.uniform(0, row["gap"][0]) if initial else lo + 1)
            while rear > lo:
                c = new()
                c[0] = rear - random.uniform(*row["gap"]) - c[1]
                cars.append(c)
                rear = c[0]
        else:
            front = max((c[0] + c[1] for c in cars), default=lo + random.uniform(0, row["gap"][0]) if initial else hi - 1)
            while front < hi:
                c = new()
                c[0] = front + random.uniform(*row["gap"])
                cars.append(c)
                front = c[0] + c[1]

    def update_row(self, row, dt):
        if row["kind"] == "road":
            for c in row["cars"]:
                c[0] += row["dir"] * row["speed"] * dt
            if row["dir"] > 0:
                row["cars"] = [c for c in row["cars"] if c[0] < W + 320]
            else:
                row["cars"] = [c for c in row["cars"] if c[0] + c[1] > -320]
            self.fill(row)
        elif row["kind"] == "rail":
            if row["train"] is None:
                row["timer"] -= dt
                if row["timer"] <= 0:
                    row["train"] = -CR_TRAIN_LEN - 40 if row["dir"] > 0 else W + 40
            else:
                row["train"] += row["dir"] * 1500 * dt
                if row["train"] > W + 60 or row["train"] < -CR_TRAIN_LEN - 60:
                    row["train"] = None
                    row["timer"] = random.uniform(3, 8)

    def hazards(self, row):
        if row["kind"] == "road":
            return [(c[0], c[0] + c[1]) for c in row["cars"]]
        if row["kind"] == "rail" and row["train"] is not None:
            return [(row["train"], row["train"] + CR_TRAIN_LEN)]
        return []

    # ---- player ----------------------------------------------------------
    @staticmethod
    def col_x(c):
        return c * CR_T + CR_T / 2

    def row_y(self, r):
        return CR_BASE_Y - (r - self.cam) * CR_T

    def player_pos(self):
        if self.hop:
            fr, fc, t = self.hop
            k = min(1.0, t / CR_HOP)
            r, c = fr + (self.pr - fr) * k, fc + (self.pc - fc) * k
            return self.col_x(c), self.row_y(r), math.sin(math.pi * k) * 16
        return self.col_x(self.pc), self.row_y(self.pr), 0.0

    def creep(self):
        """How fast (rows per second) the screen scrolls on its own, so you can't wait forever."""
        return min(CR_CREEP[2], CR_CREEP[0] + CR_CREEP[1] * self.maxrow)

    def move(self, dr, dc):
        if self.state != "play":
            return
        if self.hop:
            self.queued = (dr, dc)
            return
        r, c = self.pr + dr, self.pc + dc
        self.face = (dr, dc)
        if r < 0 or not 1 <= c <= CR_COLS - 2:
            return
        self.ensure(r + 16)
        if self.rows[r]["kind"] == "grass" and c in self.rows[r]["trees"]:
            return
        self.hop = [self.pr, self.pc, 0.0]
        self.pr, self.pc = r, c
        self.started = True
        if r > self.maxrow:
            for k in range(self.maxrow + 1, r + 1):
                if self.rows[k]["kind"] != "grass":
                    self.mult *= cr_step(k)
                    self.roads += 1
            self.maxrow = r
            if self.roads >= 50:
                self.app.unlock("chicken_cross")
        self.app.sfx("card")

    def danger(self):
        """True while a car or train is about to reach you - you can't cash out then."""
        if self.hop:
            return True
        row = self.rows[self.pr]
        if row["kind"] == "grass":
            return False
        if row["kind"] == "rail":
            return row["train"] is not None or row["timer"] < 1.5
        px = self.col_x(self.pc)
        reach = max(170, row["speed"] * 0.8)
        for x, length, *_ in row["cars"]:
            dist = (px - 22) - (x + length) if row["dir"] > 0 else x - (px + 22)
            if -length - 44 < dist < reach:
                return True
        return False

    # ---- betting ---------------------------------------------------------
    def busy(self):
        return self.state == "play"

    def outstanding_bets(self):
        return self.stake if self.state == "play" else 0

    def cash_value(self):
        return int(self.stake * self.mult)

    def can_cash(self):
        return self.state == "play" and self.roads > 0 and not self.danger()

    def start(self):
        if self.state == "play":
            return
        stake = self.take_bet()
        if not stake:
            return
        self.stake = stake
        self.reset_world()
        self.feathers = []
        self.state = "play"
        self.message = "HOP UP WITH THE ARROW KEYS (OR W A S D)"
        self.app.sfx("chip")

    def cash_out(self):
        if not self.can_cash():
            return
        win = self.cash_value()
        self.state = "over"
        self.app.effects.burst(*self.player_pos()[:2], 30, [(255, 220, 90), (255, 255, 255)])
        self.finish(self.stake, win, f"CASHED OUT AT x{self.mult:.2f} AFTER {self.roads} ROADS  -  YOU WIN {money(win)}")

    def die(self, how):
        self.state = "over"
        x, y, _ = self.player_pos()
        self.hop = None
        if how == "eagle":
            self.eagle = {"t": 0.0, "x": x, "y": y}
            msg = f"THE EAGLE GOT YOU FOR BEING TOO SLOW!  YOU LOSE {money(self.stake)}"
        else:
            self.splat = (x, y)
            for _ in range(26):
                a = random.uniform(0, 2 * math.pi)
                v = random.uniform(80, 320)
                self.feathers.append([x, y, math.cos(a) * v, math.sin(a) * v, random.uniform(0.6, 1.3)])
            self.app.sfx("boom")
            msg = f"SPLAT! {'THE TRAIN' if how == 'train' else 'A CAR'} GOT YOU AFTER {self.roads} ROADS  -  " \
                  f"YOU LOSE {money(self.stake)}"
        self.finish(self.stake, 0, lose_msg=msg)

    def handle(self, e):
        if e.type == pygame.KEYDOWN:
            moves = {pygame.K_UP: (1, 0), pygame.K_w: (1, 0), pygame.K_DOWN: (-1, 0), pygame.K_s: (-1, 0),
                     pygame.K_LEFT: (0, -1), pygame.K_a: (0, -1), pygame.K_RIGHT: (0, 1), pygame.K_d: (0, 1)}
            if self.state == "play":
                if e.key in moves:
                    self.move(*moves[e.key])
                elif e.key == pygame.K_SPACE:
                    self.move(1, 0)
                elif e.key in (pygame.K_RETURN, pygame.K_c):
                    self.cash_out()
            elif e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.start()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.state == "play":
                if self.btn_cash.clicked(e.pos, self.can_cash()):
                    self.cash_out()
                return
            if self.chip_click(e.pos):
                return
            if self.btn_clear.clicked(e.pos):
                self.bet = 0
            elif self.btn_start.clicked(e.pos):
                self.start()

    def update(self, dt):
        self.t += dt
        self.ensure(int(self.cam) + 16)
        for row in self.rows[max(0, int(self.cam) - 3):int(self.cam) + 13]:
            self.update_row(row, dt)
        for f in self.feathers:
            f[0] += f[2] * dt
            f[1] += f[3] * dt
            f[2] *= 1 - dt * 3
            f[3] = f[3] * (1 - dt * 3) + 40 * dt
            f[4] -= dt
        self.feathers = [f for f in self.feathers if f[4] > 0]
        if self.eagle:
            self.eagle["t"] += dt
        if self.state != "play":
            return
        if self.hop:
            self.hop[2] += dt
            if self.hop[2] >= CR_HOP:
                self.hop = None
                if self.queued:
                    q, self.queued = self.queued, None
                    self.move(*q)
        target = self.pr - 2
        if target > self.cam:
            self.cam += (target - self.cam) * min(1.0, dt * 6)
        if self.started:
            self.cam += self.creep() * dt
        row = self.rows[self.pr]
        px = self.player_pos()[0]
        for x0, x1 in self.hazards(row):
            if x0 + 6 < px + 17 and x1 - 6 > px - 17:
                self.die("train" if row["kind"] == "rail" else "car")
                return
        if self.pr < self.cam - 1.1:
            self.die("eagle")

    # ---- drawing ---------------------------------------------------------
    def sprite(self, length, col, truck, direction):
        key = (length, col, truck, direction)
        if key not in self.sprites:
            self.sprites[key] = cr_car_sprite(length, col, truck, direction)
        return self.sprites[key]

    def draw_row(self, surf, r):
        row = self.rows[r]
        cy = self.row_y(r)
        top = cy - CR_T / 2
        kind = row["kind"]
        if kind == "grass":
            grass = {"winter": ((232, 240, 250), (220, 232, 245)), "halloween": ((74, 110, 58), (66, 100, 52)),
                     "easter": ((130, 210, 110), (120, 200, 100)), "summer": ((236, 214, 150), (226, 204, 140))
                     }.get(CURRENT_THEME, ((104, 186, 76), (96, 176, 70)))
            pygame.draw.rect(surf, grass[0] if r % 2 else grass[1], (0, top, W, CR_T + 1))
            for c in (0, CR_COLS - 1):
                pygame.draw.rect(surf, (60, 120, 50), (c * CR_T + 4, top + 6, CR_T - 8, CR_T - 12), border_radius=18)
            for c in row["trees"]:
                x = self.col_x(c)
                pygame.draw.ellipse(surf, (60, 110, 45), (x - 24, cy + 6, 48, 18))
                pygame.draw.rect(surf, (110, 70, 40), (x - 6, cy - 2, 12, 22), border_radius=3)
                pygame.draw.rect(surf, (40, 130, 60), (x - 22, cy - 30, 44, 36), border_radius=12)
                if CURRENT_THEME == "winter":
                    pygame.draw.rect(surf, (245, 250, 255), (x - 20, cy - 30, 40, 12), border_radius=8)
                pygame.draw.rect(surf, (70, 165, 85), (x - 16, cy - 26, 26, 16), border_radius=8)
        elif kind == "road":
            pygame.draw.rect(surf, (62, 62, 70), (0, top, W, CR_T + 1))
            above = self.rows[r + 1]["kind"] if r + 1 < len(self.rows) else "grass"
            if above == "road":
                for x in range(10, W, 80):
                    pygame.draw.rect(surf, (220, 220, 200), (x, top - 2, 40, 4))
            else:
                pygame.draw.rect(surf, (150, 150, 155), (0, top, W, 4))
            if r > 0 and self.rows[r - 1]["kind"] != "road":
                pygame.draw.rect(surf, (150, 150, 155), (0, top + CR_T - 4, W, 4))
        else:
            pygame.draw.rect(surf, (120, 104, 92), (0, top, W, CR_T + 1))
            for x in range(0, W, 24):
                pygame.draw.rect(surf, (96, 70, 50), (x, top + 8, 12, CR_T - 16))
            for dy in (-14, 14):
                pygame.draw.line(surf, (190, 190, 200), (0, cy + dy), (W, cy + dy), 4)
            warn = row["train"] is None and row["timer"] < 1.5
            on = warn and int(self.t * 8) % 2 == 0
            pygame.draw.rect(surf, (40, 40, 45), (22, top + 6, 12, CR_T - 12))
            pygame.draw.circle(surf, (255, 40, 40) if on else (90, 30, 30), (28, top + 14), 9)
            if on:
                glow = pygame.Surface((120, 120), pygame.SRCALPHA)
                pygame.draw.circle(glow, (255, 40, 40, 70), (60, 60), 55)
                surf.blit(glow, (-32, top - 46))

    def draw_vehicles(self, surf, r):
        row = self.rows[r]
        cy = self.row_y(r)
        if row["kind"] == "road":
            for x, length, col, truck in row["cars"]:
                if -length < x < W:
                    img = self.sprite(length, col, truck, row["dir"])
                    surf.blit(img, (x, cy - 25))
        elif row["kind"] == "rail" and row["train"] is not None:
            x0 = row["train"]
            for k in range(5):
                x = x0 + k * CR_TRAIN_LEN / 5
                pygame.draw.rect(surf, (0, 0, 0), (x + 4, cy - 22, CR_TRAIN_LEN / 5 - 6, 50), border_radius=8)
                pygame.draw.rect(surf, (40, 70, 160) if k else (200, 50, 50), (x, cy - 26, CR_TRAIN_LEN / 5 - 6, 50),
                                 border_radius=8)
                for wx in range(12, int(CR_TRAIN_LEN / 5) - 20, 26):
                    pygame.draw.rect(surf, (200, 230, 255), (x + wx, cy - 12, 16, 20), border_radius=3)

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.fill({"winter": (220, 232, 245), "halloween": (66, 100, 52), "easter": (120, 200, 100),
                   "summer": (226, 204, 140)}.get(CURRENT_THEME, (96, 176, 70)))
        surf.set_clip(pygame.Rect(0, 56, W, 574))
        lo = max(0, int(self.cam) - 2)
        hi = min(len(self.rows), int(self.cam) + 10)
        for r in range(lo, hi):
            self.draw_row(surf, r)
        px, py, lift = self.player_pos()
        for r in range(hi - 1, lo - 1, -1):
            self.draw_vehicles(surf, r)
            if r == self.pr and (not self.eagle or self.eagle["t"] < 0.5):
                if self.splat:
                    sx, sy = self.splat
                    pygame.draw.ellipse(surf, (240, 240, 235), (sx - 30, sy - 10, 60, 20))
                    pygame.draw.circle(surf, (225, 40, 40), (sx, sy - 8), 6)
                else:
                    surf.blit(self.shadow, (px - 17, py + 10))
                    draw_chicken(surf, px, py - lift, self.face)
        for f in self.feathers:
            pygame.draw.ellipse(surf, (250, 250, 250), (f[0] - 5, f[1] - 3, 10, 6))
        if self.eagle:
            t = self.eagle["t"]
            ex, ey = self.eagle["x"], self.eagle["y"]
            if t < 0.5:
                bx, by = ex - 300 * (1 - t / 0.5), ey - 500 * (1 - t / 0.5)
            else:
                bx, by = ex + (t - 0.5) * 500, ey - (t - 0.5) * 700
                draw_chicken(surf, bx, by + 28, (1, 0), scale=0.85)
            flap = math.sin(t * 20) * 18
            pygame.draw.polygon(surf, (70, 50, 35), [(bx - 70, by - flap), (bx, by - 8), (bx + 70, by - flap), (bx, by + 16)])
            pygame.draw.circle(surf, (245, 245, 240), (bx + 6, by - 10), 10)
            pygame.draw.polygon(surf, (240, 190, 40), [(bx + 14, by - 12), (bx + 26, by - 6), (bx + 14, by - 4)])
        # warning when the screen is catching up with you
        if self.state == "play" and self.pr < self.cam - 0.35:
            a = int(90 + 80 * math.sin(self.t * 12))
            edge = pygame.Surface((W, 90), pygame.SRCALPHA)
            for i in range(90):
                pygame.draw.line(edge, (255, 30, 30, int(a * i / 90)), (0, i), (W, i))
            surf.blit(edge, (0, 540))
            draw_pill(surf, "HURRY UP! THE EAGLE IS COMING!", font(20, bold=True), (640, 590), WHITE,
                      (150, 20, 20, 220), (255, 120, 120))
        surf.set_clip(None)

        # scoreboard
        if self.state in ("play", "over") and self.stake:
            box = pygame.Rect(W / 2 - 200, 66, 400, 76)
            soft_panel(surf, box, 185, GOLD_DARK)
            draw_text(surf, f"x{self.mult:.2f}", font(34, bold=True), GOLD, (box.x + 100, box.y + 32))
            draw_text(surf, f"{self.roads} ROAD{'S' if self.roads != 1 else ''}", font(13, bold=True), (210, 210, 220),
                      (box.x + 100, box.y + 62))
            draw_text(surf, "CASH OUT VALUE", font(12, bold=True), (190, 190, 200), (box.x + 290, box.y + 20))
            draw_text(surf, money(self.cash_value()), font(26, bold=True), (120, 240, 140), (box.x + 290, box.y + 46))
            nxt = self.maxrow + 1
            while nxt < len(self.rows) and self.rows[nxt]["kind"] == "grass":
                nxt += 1
            if self.state == "play":
                draw_text(surf, f"NEXT ROAD: x{self.mult * cr_step(nxt):.2f}", font(13, bold=True), (230, 200, 120),
                          (W / 2, box.bottom + 14))
        if self.state == "betting":
            box = pygame.Rect(W / 2 - 330, 150, 660, 250)
            soft_panel(surf, box, 205, GOLD_DARK)
            draw_text(surf, "CHICKEN CROSSING", font(36, bold=True, serif=True), GOLD, (W / 2, box.y + 42))
            lines = ["Hop across an endless road full of cars, trucks and trains.",
                     "Every new road you reach raises your multiplier - and it gets faster.",
                     "Cash out any time you're not about to get hit.",
                     "Don't dawdle - the screen keeps moving, and the eagle is watching!"]
            for i, ln in enumerate(lines):
                draw_text(surf, ln, font(17), WHITE, (W / 2, box.y + 94 + i * 30))
            draw_text(surf, "ARROWS or W A S D to hop", font(15, bold=True), (150, 230, 160), (W / 2, box.y + 222))
        if self.message:
            draw_pill(surf, self.message, font(16, bold=True), (640, 612), GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(14, 4))
        if self.state == "play":
            danger = self.roads > 0 and self.danger()
            self.btn_cash.text = "TOO CLOSE!" if danger else f"CASH OUT {money(self.cash_value())}"
            self.btn_cash.hint = "WAIT FOR A GAP" if danger else "ENTER or C"
            self.draw_bottom(surf, mouse, self.btn_cash, self.can_cash(), (10, 16, 8))
        else:
            self.draw_bottom(surf, mouse, self.btn_start, self.bet > 0, (10, 16, 8))
        self.app.draw_top_bar(surf, "CHICKEN CROSSING", lobby=True, lobby_enabled=self.can_leave())


def art_crossy(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    band = h * k / 4
    for i, (kind, col) in enumerate((("grass", (104, 186, 76)), ("road", (62, 62, 70)), ("road", (62, 62, 70)),
                                     ("grass", (96, 176, 70)))):
        pygame.draw.rect(s, col, (0, i * band, w * k, band + 1))
    for x in range(10, w * k, 60):
        pygame.draw.rect(s, (220, 220, 200), (x, band * 2 - 3, 30, 6))
    car = pygame.transform.smoothscale(cr_car_sprite(74, (220, 60, 60), False, 1), (int(band * 1.5), int(band * 0.8)))
    s.blit(car, (w * k * 0.18, band * 1.1))
    truck = pygame.transform.smoothscale(cr_car_sprite(134, (60, 120, 220), True, -1), (int(band * 2.6), int(band * 0.8)))
    s.blit(truck, (w * k * 0.62, band * 2.1))
    draw_chicken(s, w * k * 0.5, band * 3.5, (1, 0), scale=band / 44)
    for x in (0.12, 0.85):
        pygame.draw.rect(s, (40, 130, 60), (w * k * x, band * 0.1, band * 0.8, band * 0.8), border_radius=14)
    draw_text(s, "CHICKEN CROSSING", font(h * k * 0.11, bold=True, serif=True), (255, 230, 120), (w * k * 0.5, band * 0.5),
              shadow=(0, 0, 0))
    return pygame.transform.smoothscale(s, (w, h))


# --------------------------------------------------------------------------
# Yes or No - a new question to bet on every 30 seconds (runs in the background)
# --------------------------------------------------------------------------
QN_EVERY = 30                   # seconds per question
QN_LOCK = 5                     # betting closes this many seconds before the answer
QN_EDGE = 0.95                  # payouts give back 95% on average
QN_TEAMS = ["Galaxy Gators", "Casino City Comets", "Royal Rhinos", "Neon Narwhals", "Lucky Llamas"]
QN_STRIKERS = ["Rocket Rodriguez", "Big Banana Bob", "Pixel Pete", "Moonbeam Mia", "Taco Tina"]


def qn_mult(p):
    return max(1.01, math.floor(QN_EDGE / p * 100) / 100)


def qn_poisson(rng, lam):
    k, p, target = 0, math.exp(-lam), rng.random()
    total = p
    while target > total:
        k += 1
        p *= lam / k
        total += p
    return k


def draw_die_face(surf, n, center, size):
    r = pygame.Rect(0, 0, size, size)
    r.center = center
    pygame.draw.rect(surf, (0, 0, 0), r.move(3, 4), border_radius=size // 6)
    pygame.draw.rect(surf, (245, 245, 240), r, border_radius=size // 6)
    pygame.draw.rect(surf, (180, 180, 180), r, width=2, border_radius=size // 6)
    q = size * 0.27
    spots = {1: [(0, 0)], 2: [(-1, -1), (1, 1)], 3: [(-1, -1), (0, 0), (1, 1)], 4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
             5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)], 6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)]}
    for dx, dy in spots[n]:
        pygame.draw.circle(surf, (30, 30, 30), (r.centerx + dx * q, r.centery + dy * q), size * 0.09)


class YesNo(StakeGame):
    key = "yesno"

    def __init__(self, app):
        super().__init__(app, ((10, 12, 30), (70, 80, 140)))
        self.bg = gradient_bg((34, 26, 70), (8, 8, 22))
        self.round = int(time.time() // QN_EVERY)
        self.q = self.make_question(self.round)
        self.bets = {}              # "yes" / "no" -> chips riding on the current question
        self.wins = {}              # "yes" / "no" -> what those bets pay if they're right
        self.last = None            # the question that just finished
        self.history = []
        self.streak = 0
        self.t = 0.0
        self.reveal_t = 99.0
        self.message = "PICK YOUR BET WITH THE CHIPS, THEN PRESS YES OR NO"
        self.btn_yes = Button((965, 646, 130, 62), "YES", (25, 120, 60), 26, "Y")
        self.btn_no = Button((1105, 646, 130, 62), "NO", (160, 35, 40), 26, "N")

    def can_leave(self):
        return True

    def outstanding_bets(self):
        return sum(self.bets.values())

    def leave(self):
        pass

    # ---- questions -------------------------------------------------------
    def make_question(self, n):
        rng = random.Random(n * 7919 + 13)
        kind = rng.choices(["stock", "dice", "coin", "card", "number", "weather", "rocket", "goals", "penalty"],
                           [4, 2, 1, 2, 2, 1, 2, 1, 1])[0]
        q = {"n": n, "kind": kind}
        if kind == "stock":
            st = rng.choice(self.app.market.stocks)
            q["sym"], q["line"] = st["sym"], st["price"]
            q["text"] = f"Will {st['sym']} ({st['name']}) be ABOVE ${st['price']:,.2f} when the timer hits zero?"
            q["p"] = 0.5
        elif kind == "dice":
            t = rng.choice([7, 8, 9, 10])
            q["roll"] = [rng.randint(1, 6), rng.randint(1, 6)]
            q["text"] = f"Will two dice add up to {t} or more?"
            q["p"] = sum(1 for a in range(1, 7) for b in range(1, 7) if a + b >= t) / 36
            q["answer"] = sum(q["roll"]) >= t
            q["reveal"] = f"Rolled {q['roll'][0]} + {q['roll'][1]} = {sum(q['roll'])}"
        elif kind == "coin":
            k = rng.choice([2, 3])
            q["flips"] = [rng.random() < 0.5 for _ in range(k)]
            q["text"] = f"Will a coin land HEADS {k} times in a row?"
            q["p"] = 0.5 ** k
            q["answer"] = all(q["flips"])
            q["reveal"] = "Flips: " + "  ".join("HEADS" if f else "TAILS" for f in q["flips"])
        elif kind == "card":
            rank, suit = rng.choice(RANKS), rng.choice(SUITS)
            q["card"] = (rank, suit)
            name = {"S": "spades", "H": "hearts", "D": "diamonds", "C": "clubs"}[suit]
            text, p, ans = rng.choice([
                ("Will the next card be a face card (J, Q or K)?", 12 / 52, rank in ("J", "Q", "K")),
                ("Will the next card be a heart?", 1 / 4, suit == "H"),
                ("Will the next card be red?", 1 / 2, suit in "HD"),
                ("Will the next card be an ace?", 1 / 13, rank == "A"),
                ("Will the next card be 7 or higher (aces are low)?", 7 / 13, RANKS.index(rank) >= 6)])
            q["text"], q["p"], q["answer"] = text, p, ans
            q["reveal"] = f"The card was the {rank} of {name}"
        elif kind == "number":
            k = rng.choice([50, 70, 90])
            q["num"] = rng.randint(1, 100)
            q["text"] = f"Will the random number (1 to 100) be over {k}?"
            q["p"] = (100 - k) / 100
            q["answer"] = q["num"] > k
            q["reveal"] = f"The number was {q['num']}"
        elif kind == "weather":
            f = rng.choice([20, 30, 40, 50, 60, 70, 80])
            q["text"] = f"Will it rain in Casino City today? The forecast says {f}% chance."
            q["p"] = f / 100
            q["answer"] = rng.random() < q["p"]
            q["reveal"] = "It poured with rain!" if q["answer"] else "Sunny skies all day."
        elif kind == "rocket":
            m = rng.choice([1.5, 2.0, 3.0, 5.0])
            crash = max(1.0, 0.97 / max(1e-9, 1 - rng.random()))
            q["text"] = f"Will the test rocket fly past x{m:.2f} before it explodes?"
            q["p"] = 0.97 / m
            q["answer"] = crash >= m
            q["reveal"] = f"The rocket exploded at x{min(crash, 999):.2f}"
        elif kind == "goals":
            team = rng.choice(QN_TEAMS)
            g = qn_poisson(rng, 1.6)
            q["text"] = f"Will the {team} score 3 or more goals tonight?"
            q["p"] = 1 - sum(math.exp(-1.6) * 1.6 ** k / math.factorial(k) for k in range(3))
            q["answer"] = g >= 3
            q["reveal"] = f"The {team} scored {g} goal{'s' if g != 1 else ''}"
        else:
            who = rng.choice(QN_STRIKERS)
            q["text"] = f"Will {who} score the penalty kick?"
            q["p"] = 0.75
            q["answer"] = rng.random() < 0.75
            q["reveal"] = "GOAL! Right in the top corner." if q["answer"] else "SAVED by the goalkeeper!"
        q["mult"] = {"yes": qn_mult(q["p"]), "no": qn_mult(1 - q["p"])}
        return q

    def resolve(self, q):
        if q["kind"] == "stock":
            now = self.app.market.stock(q["sym"])["price"]
            q["answer"] = now > q["line"]
            dp = 2 if f"{now:.2f}" != f"{q['line']:.2f}" else 4
            q["reveal"] = (f"{q['sym']} ended at ${now:,.{dp}f}  "
                           f"({'above' if q['answer'] else 'not above'} ${q['line']:,.2f})")
        return q["answer"]

    def mult(self, side):
        """Stock questions have live odds (they change as the price moves); the rest are fixed."""
        q = self.q
        if q["kind"] != "stock":
            return q["mult"][side]
        st = self.app.market.stock(q["sym"])
        sd = st["vol"] * 0.45 * math.sqrt(max(1.0, self.time_left()))
        p_yes = 0.5 * (1 + math.erf(math.log(st["price"] / q["line"]) / (sd * math.sqrt(2))))
        p_yes = min(0.94, max(0.04, p_yes))
        return qn_mult(p_yes if side == "yes" else 1 - p_yes)

    def time_left(self):
        return (self.round + 1) * QN_EVERY - time.time()

    def locked(self):
        return self.time_left() <= QN_LOCK

    # ---- the clock (called every frame from the app) ----------------------
    def check(self):
        now = int(time.time() // QN_EVERY)
        if now == self.round:
            return
        self.settle()
        self.round = now
        self.q = self.make_question(now)

    def settle(self):
        q = self.q
        ans = self.resolve(q)
        staked = sum(self.bets.values())
        side = "yes" if ans else "no"
        won = self.wins.get(side, 0)
        if staked:
            self.app.balance += won
            self.app.record(self.key, staked, won)
            if won:
                self.streak += 1
                if self.streak >= 5:
                    self.app.unlock("oracle")
                self.app.effects.toast("YES OR NO: YOU WON!", f"The answer was {side.upper()} - you won {money(won)}")
                self.app.float_text(f"+{money(won - staked)}", (80, 230, 110))
                self.app.sfx("win")
            else:
                self.streak = 0
                self.app.effects.toast("YES OR NO", f"The answer was {side.upper()} - you lost {money(staked)}")
                self.app.sfx("lose")
            if self.app.scene == self.key:
                self.message = (f"CORRECT! YOU WIN {money(won)}" if won else
                                f"THE ANSWER WAS {side.upper()} - YOU LOSE {money(staked)}")
            self.app.save()
        elif self.app.scene == self.key:
            self.message = f"THE ANSWER WAS {side.upper()}"
        self.last = {"q": q, "ans": ans, "bets": dict(self.bets), "won": won}
        self.history = ([{"text": q["text"], "ans": ans, "net": (won - staked) if staked else None}]
                        + self.history)[:7]
        self.bets, self.wins = {}, {}
        self.reveal_t = 0.0

    def place(self, side):
        if self.locked():
            self.message = "BETTING IS CLOSED - THE ANSWER IS COMING!"
            return
        other = "no" if side == "yes" else "yes"
        if self.bets.get(other):
            self.message = f"YOU ALREADY BET {other.upper()} ON THIS QUESTION"
            return
        stake = self.take_bet()
        if not stake:
            return
        self.bets[side] = self.bets.get(side, 0) + stake
        self.wins[side] = self.wins.get(side, 0) + int(stake * self.mult(side))
        self.message = f"{money(self.bets[side])} ON {side.upper()} - PAYS {money(self.wins[side])} IF YOU'RE RIGHT"
        self.app.sfx("chip")
        self.app.save()

    def handle(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_y:
                self.place("yes")
            elif e.key == pygame.K_n:
                self.place("no")
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.chip_click(e.pos):
                return
            if self.btn_clear.clicked(e.pos):
                self.bet = 0
            elif self.btn_yes.clicked(e.pos, self.can_bet("yes")):
                self.place("yes")
            elif self.btn_no.clicked(e.pos, self.can_bet("no")):
                self.place("no")

    def can_bet(self, side):
        other = "no" if side == "yes" else "yes"
        return 0 < self.bet <= self.app.balance and not self.locked() and not self.bets.get(other)

    def update(self, dt):
        self.t += dt
        self.reveal_t += dt

    # ---- drawing -----------------------------------------------------------
    def draw_reveal(self, surf, q, x, y):
        k = q["kind"]
        pop = min(1.0, self.reveal_t / 0.4)
        if k == "dice":
            for i, v in enumerate(q["roll"]):
                draw_die_face(surf, v, (x + 40 + i * 76, y), int(56 * pop) or 1)
        elif k == "coin":
            for i, f in enumerate(q["flips"]):
                c = (x + 36 + i * 68, y)
                pygame.draw.circle(surf, (120, 90, 20), (c[0] + 2, c[1] + 3), 28 * pop)
                pygame.draw.circle(surf, GOLD if f else (200, 200, 210), c, 28 * pop)
                draw_text(surf, "H" if f else "T", font(24, bold=True), (60, 40, 5), c)
        elif k == "card":
            img = pygame.transform.smoothscale(self.assets.faces[q["card"]], (int(CW * 0.62), int(CH * 0.62)))
            surf.blit(img, img.get_rect(midleft=(x + 10, y)))
        elif k == "number":
            pygame.draw.circle(surf, (245, 245, 240), (x + 44, y), 34 * pop)
            draw_text(surf, str(q["num"]), font(28, bold=True), (30, 30, 30), (x + 44, y))
        elif k == "stock":
            st = self.app.market.stock(q["sym"])
            pygame.draw.circle(surf, st["color"], (x + 44, y), 34)
            draw_text(surf, q["sym"], font(18, bold=True), (20, 20, 30), (x + 44, y))
        else:
            icon = {"weather": "RAIN" if q["answer"] else "SUN", "rocket": "BOOM", "goals": "GOAL", "penalty": "KICK"}[k]
            pygame.draw.circle(surf, (60, 70, 130), (x + 44, y), 34)
            draw_text(surf, icon, font(16, bold=True), WHITE, (x + 44, y))

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        q = self.q
        left = self.time_left()
        # the question
        main = pygame.Rect(40, 76, 800, 330)
        soft_panel(surf, main, 150, (110, 100, 190))
        draw_text(surf, f"QUESTION #{q['n'] % 100000}", font(15, bold=True), (190, 180, 240), (main.x + 24, main.y + 26),
                  anchor="midleft")
        for i, ln in enumerate(wrap_text(q["text"], font(30, bold=True), main.w - 60)[:3]):
            draw_text(surf, ln, font(30, bold=True), WHITE, (main.x + 24, main.y + 70 + i * 40), anchor="midleft")
        if q["kind"] == "stock":
            st = self.app.market.stock(q["sym"])
            up = st["price"] > q["line"]
            draw_text(surf, f"{q['sym']} RIGHT NOW: ${st['price']:,.2f}", font(20, bold=True), UP_COL if up else DOWN_COL,
                      (main.x + 24, main.y + 186), anchor="midleft")
            draw_arrow(surf, up, (main.x + 330, main.y + 186), 16, UP_COL if up else DOWN_COL)
            draw_text(surf, "Live odds - they change as the price moves", font(13), (190, 185, 220),
                      (main.right - 24, main.y + 186), anchor="midright")
        for i, side in enumerate(("yes", "no")):
            box = pygame.Rect(main.x + 24 + i * 390, main.y + 214, 362, 96)
            col = (30, 120, 60) if side == "yes" else (150, 35, 45)
            pygame.draw.rect(surf, darken(col, 40), box, border_radius=14)
            pygame.draw.rect(surf, col, box, width=3, border_radius=14)
            draw_text(surf, side.upper(), font(30, bold=True), WHITE, (box.x + 70, box.y + 36))
            draw_text(surf, f"PAYS x{self.mult(side):.2f}", font(16, bold=True), (230, 220, 160), (box.x + 70, box.y + 70))
            amt = self.bets.get(side, 0)
            if amt:
                draw_text(surf, f"YOUR BET {money(amt)}", font(15, bold=True), GOLD, (box.right - 100, box.y + 34))
                draw_text(surf, f"WINS {money(self.wins[side])}", font(18, bold=True), (140, 240, 150),
                          (box.right - 100, box.y + 62))
            else:
                draw_text(surf, "no bet", font(15), (170, 170, 190), (box.right - 100, box.y + 48))
        # countdown
        side_p = pygame.Rect(860, 76, 380, 330)
        soft_panel(surf, side_p, 150, (110, 100, 190))
        c = (side_p.centerx, side_p.y + 150)
        frac = max(0.0, left / QN_EVERY)
        pygame.draw.circle(surf, (40, 36, 70), c, 96, 14)
        col = (235, 80, 80) if self.locked() else (lerp_col((240, 200, 60), (90, 220, 130), frac))
        if frac > 0:
            pygame.draw.arc(surf, col, (c[0] - 96, c[1] - 96, 192, 192), math.pi / 2, math.pi / 2 + 2 * math.pi * frac, 14)
        draw_text(surf, f"0:{int(math.ceil(left)):02d}", font(46, bold=True), WHITE, c)
        draw_text(surf, "BETTING CLOSED" if self.locked() else "TIME TO BET", font(18, bold=True),
                  (235, 110, 110) if self.locked() else (150, 230, 160), (c[0], side_p.y + 280))
        draw_text(surf, f"Bets close {QN_LOCK} seconds before the answer", font(13), (190, 185, 220),
                  (c[0], side_p.y + 306))
        # last answer
        res = pygame.Rect(40, 420, 800, 176)
        soft_panel(surf, res, 150, (110, 100, 190))
        draw_text(surf, "LAST QUESTION", font(15, bold=True), (190, 180, 240), (res.x + 24, res.y + 22), anchor="midleft")
        if self.last:
            lq = self.last["q"]
            txt = lq["text"]
            while font(16).size(txt)[0] > res.w - 200 and len(txt) > 4:
                txt = txt[:-4] + "..."
            draw_text(surf, txt, font(16), WHITE, (res.x + 24, res.y + 50), anchor="midleft")
            self.draw_reveal(surf, lq, res.x + 14, res.y + 112)
            draw_text(surf, lq["reveal"], font(17, bold=True), (230, 225, 250), (res.x + 190, res.y + 98), anchor="midleft")
            b, won = self.last["bets"], self.last["won"]
            if b:
                staked = sum(b.values())
                line = f"You bet {money(staked)} on {next(iter(b)).upper()} - " + (
                    f"you won {money(won)}!" if won else "you lost it.")
                draw_text(surf, line, font(16, bold=True), (140, 240, 150) if won else (240, 140, 140),
                          (res.x + 190, res.y + 130), anchor="midleft")
            stamp = "YES" if self.last["ans"] else "NO"
            sc = (60, 210, 110) if self.last["ans"] else (235, 80, 80)
            k = min(1.0, self.reveal_t / 0.25)
            f = font(int(30 + 30 * (1 - k)) + 16, bold=True)
            box = pygame.Rect(0, 0, 150, 76)
            box.center = (res.right - 90, res.centery + 6)
            pygame.draw.rect(surf, sc, box, width=5, border_radius=12)
            draw_text(surf, stamp, f, sc, box.center)
        else:
            draw_text(surf, "The first answer shows up here when the timer runs out.", font(16), (190, 185, 220),
                      (res.x + 24, res.y + 60), anchor="midleft")
        # history
        hist = pygame.Rect(860, 420, 380, 176)
        soft_panel(surf, hist, 150, (110, 100, 190))
        draw_text(surf, "RECENT ANSWERS", font(15, bold=True), (190, 180, 240), (hist.x + 20, hist.y + 22), anchor="midleft")
        for i, h in enumerate(self.history[:5]):
            y = hist.y + 50 + i * 26
            draw_pill(surf, "YES" if h["ans"] else "NO", font(12, bold=True), (hist.x + 42, y), WHITE,
                      (30, 130, 70) if h["ans"] else (160, 40, 50), pad=(8, 2))
            txt = h["text"]
            while font(13).size(txt)[0] > 210 and len(txt) > 4:
                txt = txt[:-4] + "..."
            draw_text(surf, txt, font(13), (220, 220, 235), (hist.x + 72, y), anchor="midleft")
            if h["net"] is not None:
                draw_text(surf, ("+" if h["net"] >= 0 else "-") + money(abs(h["net"])), font(13, bold=True),
                          (140, 240, 150) if h["net"] >= 0 else (240, 140, 140), (hist.right - 16, y), anchor="midright")
        if not self.history:
            draw_text(surf, "No answers yet.", font(14), (170, 170, 190), (hist.x + 20, hist.y + 56), anchor="midleft")
        if self.message:
            draw_pill(surf, self.message, font(15, bold=True), (640, 612), GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(14, 4))
        bottom_bar(surf, (8, 8, 20))
        self.tray.draw(surf, self.assets, mouse, dim=lambda v: self.bet + v > self.app.balance)
        self.btn_clear.hint = f"BET {money(self.bet)}"
        self.btn_clear.draw(surf, mouse, self.bet > 0)
        self.btn_yes.draw(surf, mouse, self.can_bet("yes"))
        self.btn_no.draw(surf, mouse, self.can_bet("no"))
        self.app.draw_top_bar(surf, "YES OR NO", lobby=True)


def art_yesno(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    for y in range(h * k):
        pygame.draw.line(s, lerp_col((60, 45, 120), (12, 10, 30), y / (h * k)), (0, y), (w * k, y))
    for i, (txt, col) in enumerate((("YES", (40, 170, 80)), ("NO", (200, 50, 60)))):
        r = pygame.Rect(0, 0, w * k * 0.3, h * k * 0.42)
        r.center = (w * k * (0.3 + i * 0.4), h * k * 0.5)
        pygame.draw.rect(s, (0, 0, 0), r.move(4, 6), border_radius=20)
        pygame.draw.rect(s, col, r, border_radius=20)
        pygame.draw.rect(s, WHITE, r, width=4, border_radius=20)
        draw_text(s, txt, font(h * k * 0.2, bold=True), WHITE, r.center)
    draw_text(s, "?", font(h * k * 0.5, bold=True, serif=True), GOLD, (w * k * 0.5, h * k * 0.5))
    draw_text(s, "NEW QUESTION EVERY 30 SECONDS", font(h * k * 0.09, bold=True), (220, 210, 255), (w * k * 0.5, h * k * 0.9))
    return pygame.transform.smoothscale(s, (w, h))


# --------------------------------------------------------------------------
# Cups - follow the ball while the cups are shuffled
# --------------------------------------------------------------------------
CUP_LEVELS = [   # name, cups, moves, first move time, last move time, payout
    ("EASY", 3, 12, 0.42, 0.26, 1.3),
    ("MEDIUM", 3, 18, 0.30, 0.15, 2.0),
    ("HARD", 4, 26, 0.22, 0.09, 3.5),
    ("EXTREME", 5, 100, 0.75, 0.065, 20),
]
CUP_EXTREME = 3                  # index of EXTREME: slow at first, then the cups fly all over the table
CUP_TRICKS = [   # chance of a fake-out, a three-cup spin, and (4 cups only) two swaps at once
    (0.15, 0.10, 0.0),
    (0.25, 0.25, 0.0),
    (0.25, 0.20, 0.35),
    (0.20, 0.25, 0.40),
]
CUP_Y = 430              # where the cups sit on the table
CUP_LIFT = 110


def draw_cup(surf, x, y, scale=1.0):
    """A red cup, upside down, standing with its rim at (x, y)."""
    k = scale
    body_c, shine_c, stripe_c, rim_d, rim_l, base_c = CUP_THEME.get(CURRENT_THEME, (
        (150, 25, 30), (215, 70, 70), (245, 215, 120), (120, 18, 24), (175, 40, 45), (110, 15, 20)))
    top_w, bot_w, h = 70 * k, 118 * k, 128 * k
    body = [(x - bot_w / 2, y), (x - top_w / 2, y - h), (x + top_w / 2, y - h), (x + bot_w / 2, y)]
    pygame.draw.polygon(surf, body_c, body)
    shine = [(x - bot_w / 2 + 16 * k, y - 6 * k), (x - top_w / 2 + 10 * k, y - h + 8 * k),
             (x - top_w / 2 + 24 * k, y - h + 8 * k), (x - bot_w / 2 + 34 * k, y - 6 * k)]
    pygame.draw.polygon(surf, shine_c, shine)
    for f in (0.3, 0.62):
        w = top_w + (bot_w - top_w) * (1 - f)
        pygame.draw.line(surf, stripe_c, (x - w / 2 + 2, y - h * f), (x + w / 2 - 2, y - h * f), max(2, int(5 * k)))
    pygame.draw.ellipse(surf, rim_d, (x - top_w / 2, y - h - 9 * k, top_w, 18 * k))
    pygame.draw.ellipse(surf, rim_l, (x - top_w / 2 + 4 * k, y - h - 7 * k, top_w - 8 * k, 13 * k))
    pygame.draw.rect(surf, base_c, (x - bot_w / 2 - 4 * k, y - 10 * k, bot_w + 8 * k, 12 * k),
                     border_radius=int(6 * k))
    if CURRENT_THEME:                                    # a little seasonal emblem on the cup
        theme_icon(surf, CURRENT_THEME, x, y - h * 0.46, 11 * k)


def draw_cup_ball(surf, x, y, r=20):
    if draw_theme_ball(surf, x, y, r):
        return
    pygame.draw.circle(surf, (0, 0, 0), (x + 3, y + 4), r)
    pygame.draw.circle(surf, (235, 190, 50), (x, y), r)
    pygame.draw.circle(surf, (255, 240, 170), (x - r * 0.35, y - r * 0.35), r * 0.35)


class Cups(StakeGame):
    key = "cups"

    def __init__(self, app):
        super().__init__(app, ((22, 13, 8), (90, 60, 35)))
        self.bg = felt_table((12, 60, 34), (28, 105, 60))
        self.level = 0
        self.state = "betting"      # betting, show, shuffle, pick, reveal, result
        self.stake = 0
        self.t = 0.0
        self.timer = 0.0
        self.slot_of = [0, 1, 2]    # cup id -> slot it's standing in
        self.ball = 0               # which cup id hides the ball
        self.swaps = []
        self.swap = None            # the move in progress: {"paths": {cup: (from, to, arc)}, "t", "len", "fake"}
        self.done_swaps = 0
        self.picked = None
        self.lift = {}              # cup id -> how far it's lifted (0..1)
        self.hard_streak = 0
        self.message = "PICK A DIFFICULTY, SET YOUR BET, THEN PRESS START"
        self.lvl_btns = [Button((W / 2 - 466 + i * 236, 78, 224, 58), name, (40, 70, 130), 22,
                                f"{n} CUPS  -  PAYS x{pay:g}") for i, (name, n, _, _, _, pay) in enumerate(CUP_LEVELS)]
        self.btn_start = Button((965, 646, 270, 62), "START", (25, 120, 60), 26, "SPACE")
        self.reset_cups()

    @property
    def n(self):
        return CUP_LEVELS[self.level][1]

    def reset_cups(self):
        self.slot_of = list(range(self.n))
        self.lift = {i: 0.0 for i in range(self.n)}
        self.swap = None

    def slot_x(self, slot, n=None):
        n = n or self.n
        gap = {3: 260, 4: 215}.get(n, 205)
        return W / 2 + (slot - (n - 1) / 2) * gap

    def cup_pos(self, cup):
        """Screen x, y of a cup's rim, following any swap in progress."""
        s = self.swap
        if s and cup in s["paths"]:
            fs, ts, arc, swing = s["paths"][cup]
            k = min(1.0, s["t"] / s["len"])
            k = k * k * (3 - 2 * k)                         # ease in and out
            lift = math.sin(math.pi * k)
            f = 0.5 * lift if s["fake"] else k              # a fake-out meets in the middle, then goes back
            x = self.slot_x(fs) + (self.slot_x(ts) - self.slot_x(fs)) * f + math.sin(2 * math.pi * k) * swing
            return x, min(CUP_Y + 165, max(CUP_Y - 235, CUP_Y + lift * arc))
        return self.slot_x(self.slot_of[cup]), CUP_Y

    def busy(self):
        return self.state in ("show", "shuffle", "pick", "reveal")

    def outstanding_bets(self):
        return self.stake if self.busy() else 0

    def set_level(self, i):
        if self.busy() or i == self.level:
            return
        self.level = i
        self.reset_cups()
        self.app.sfx("chip")

    def start(self):
        if self.busy():
            return
        stake = self.take_bet()
        if not stake:
            return
        self.stake = stake
        self.reset_cups()
        _, n, count, _, _, _ = CUP_LEVELS[self.level]
        self.ball = random.randrange(n)
        self.swaps = [self.make_move(n) for _ in range(count)]
        self.done_swaps = 0
        self.picked = None
        self.state = "show"
        self.timer = 0.0
        self.message = "WATCH THE BALL..."
        self.app.sfx("chip")

    def wildness(self):
        """0 until move 40 of EXTREME, rising to 1 (cups flying everywhere) by move 80."""
        if self.level != CUP_EXTREME:
            return 0.0
        return max(0.0, min(1.0, (self.done_swaps - 40) / 40))

    def make_move(self, n):
        """One shuffle move: which slot each moving cup goes to, and whether it's a fake-out."""
        fake_p, spin_p, double_p = CUP_TRICKS[self.level]
        if n >= 4 and random.random() < double_p:
            a, b, c, d = random.sample(range(n), 4)
            return {"map": {a: b, b: a, c: d, d: c}, "fake": False}
        if n == 5 and random.random() < 0.12:           # all five cups swap places at once
            order = random.sample(range(5), 5)
            return {"map": {order[i]: order[(i + 1) % 5] for i in range(5)}, "fake": False}
        if random.random() < spin_p:
            a, b, c = random.sample(range(n), 3)
            return {"map": {a: b, b: c, c: a}, "fake": False}
        a, b = random.sample(range(n), 2)
        return {"map": {a: b, b: a}, "fake": random.random() < fake_p}

    def next_swap(self):
        name, n, count, t0, t1, _ = CUP_LEVELS[self.level]
        if self.done_swaps >= count:
            self.swap = None
            self.state = "pick"
            self.message = f"WHERE'S THE BALL?  CLICK A CUP (OR PRESS 1-{n})"
            return
        mv = self.swaps[self.done_swaps]
        paths = {}
        for fs, ts in mv["map"].items():
            cup = self.slot_of.index(fs)
            dist = abs(ts - fs)
            arc = -(30 + 14 * dist) if ts > fs else 22 + 10 * dist      # right-movers pass behind, left-movers in front
            swing = 0.0
            wild = self.wildness()
            if wild:                                    # EXTREME: big loops up and down, and wide swings sideways
                arc = (arc / abs(arc)) * (abs(arc) + wild * random.uniform(110, 230))
                if random.random() < wild * 0.5:
                    arc = -arc
                swing = wild * random.uniform(60, 170) * random.choice((-1, 1))
            paths[cup] = (fs, ts, arc, swing)
        prog = self.done_swaps / max(1, count - 1)
        if self.level == CUP_EXTREME:
            length = t0 * (t1 / t0) ** prog             # starts slow, speeds up the whole way
        else:
            length = t0 + (t1 - t0) * prog              # faster and faster
        self.swap = {"paths": paths, "t": 0.0, "len": length, "fake": mv["fake"]}
        self.app.sfx("card")

    def pick(self, cup):
        if self.state != "pick":
            return
        self.picked = cup
        self.state = "reveal"
        self.timer = 0.0
        self.app.sfx("chip")

    def settle(self):
        name, _, _, _, _, pay = CUP_LEVELS[self.level]
        self.state = "result"
        if self.picked == self.ball:
            win = int(self.stake * pay)
            x, y = self.cup_pos(self.ball)
            self.app.effects.burst(x, y - 20, 30, [(255, 220, 90), (255, 255, 255)])
            if self.level == 2:
                self.hard_streak += 1
                if self.hard_streak >= 3:
                    self.app.unlock("eagle_eye")
            if self.level == CUP_EXTREME:
                self.app.unlock("cup_legend")
            self.finish(self.stake, win, f"YOU FOUND IT!  YOU WIN {money(win)}")
        else:
            if self.level == 2:
                self.hard_streak = 0
            self.finish(self.stake, 0, lose_msg=f"WRONG CUP - THE BALL WAS OVER HERE.  YOU LOSE {money(self.stake)}")

    def cup_at(self, pos):
        for cup in range(self.n):
            x, y = self.cup_pos(cup)
            if pygame.Rect(x - 62, y - 140, 124, 150).collidepoint(pos):
                return cup
        return None

    def handle(self, e):
        if e.type == pygame.KEYDOWN:
            if self.state == "pick" and e.unicode and e.unicode in "12345" and int(e.unicode) <= self.n:
                slot = int(e.unicode) - 1
                self.pick(self.slot_of.index(slot))
            elif e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.start()
            elif e.key == pygame.K_LEFT:
                self.set_level(max(0, self.level - 1))
            elif e.key == pygame.K_RIGHT:
                self.set_level(min(len(CUP_LEVELS) - 1, self.level + 1))
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.state == "pick":
                cup = self.cup_at(e.pos)
                if cup is not None:
                    self.pick(cup)
                return
            if self.busy():
                return
            for i, b in enumerate(self.lvl_btns):
                if b.clicked(e.pos):
                    self.set_level(i)
                    return
            if self.chip_click(e.pos):
                return
            if self.btn_clear.clicked(e.pos):
                self.bet = 0
            elif self.btn_start.clicked(e.pos):
                self.start()

    def update(self, dt):
        self.t += dt
        self.timer += dt
        want = {}
        if self.state == "show":
            # lift the ball's cup, put it down, then start shuffling
            want[self.ball] = 1.0 if 0.3 < self.timer < 1.6 else 0.0
            if self.timer > 2.1:
                self.state = "shuffle"
                self.next_swap()
        elif self.state == "shuffle" and self.swap:
            self.swap["t"] += dt
            if self.swap["t"] >= self.swap["len"]:
                if not self.swap["fake"]:
                    for cup, (fs, ts, *_) in self.swap["paths"].items():
                        self.slot_of[cup] = ts
                self.done_swaps += 1
                self.next_swap()
        elif self.state == "reveal":
            want[self.picked] = 1.0
            if self.timer > 0.6 and self.picked != self.ball:
                want[self.ball] = 1.0
            if self.timer > 0.9:
                self.settle()
        elif self.state == "result":
            want[self.picked] = 1.0
            want[self.ball] = 1.0
        for cup in self.lift:
            target = want.get(cup, 0.0)
            self.lift[cup] += (target - self.lift[cup]) * min(1.0, dt * 10)

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        for i, b in enumerate(self.lvl_btns):
            b.color = (200, 150, 30) if i == self.level else ((150, 30, 60) if i == CUP_EXTREME else (40, 70, 130))
            b.draw(surf, mouse, not self.busy() or i == self.level)
        # progress while shuffling
        name, n, count, _, _, pay = CUP_LEVELS[self.level]
        if self.state == "shuffle":
            draw_text(surf, f"SHUFFLING...  {self.done_swaps}/{count}", font(20, bold=True), (230, 230, 200), (W / 2, 190))
        elif self.stake and self.state in ("pick", "reveal", "result"):
            draw_text(surf, f"BET {money(self.stake)}  -  PAYS {money(int(self.stake * pay))}", font(20, bold=True),
                      GOLD, (W / 2, 190))
        # cups: the one furthest "behind" first
        order = sorted(range(self.n), key=lambda c: self.cup_pos(c)[1])
        hover = self.cup_at(mouse) if self.state == "pick" else None
        for cup in range(self.n):                              # shadows
            x, y = self.cup_pos(cup)
            pygame.draw.ellipse(surf, (8, 40, 20), (x - 66, CUP_Y - 12, 132, 30))
        if self.state in ("show", "reveal", "result") or self.lift.get(self.ball, 0) > 0.05:
            x, _ = self.cup_pos(self.ball)
            draw_cup_ball(surf, x, CUP_Y - 20)
        for cup in order:
            x, y = self.cup_pos(cup)
            y -= self.lift[cup] * CUP_LIFT
            draw_cup(surf, x, y, 1.06 if cup == hover else 1.0)
        if self.state == "pick":
            for cup in range(self.n):
                x, y = self.cup_pos(cup)
                draw_text(surf, str(self.slot_of[cup] + 1), font(22, bold=True), (240, 230, 200), (x, CUP_Y + 44))
        if self.state == "result" and self.picked is not None:
            x, _ = self.cup_pos(self.picked)
            draw_pill(surf, "YOUR PICK", font(14, bold=True), (x, CUP_Y + 50), WHITE,
                      (30, 130, 70) if self.picked == self.ball else (160, 40, 50))
        if self.message:
            draw_pill(surf, self.message, font(18, bold=True), (640, 600), GOLD, (0, 0, 0, 200), GOLD_DARK, pad=(16, 5))
        self.draw_bottom(surf, mouse, self.btn_start, not self.busy() and self.bet > 0)
        self.app.draw_top_bar(surf, "CUPS", lobby=True, lobby_enabled=self.can_leave())


def art_cups(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    for y in range(h * k):
        pygame.draw.line(s, lerp_col((30, 110, 62), (10, 50, 28), y / (h * k)), (0, y), (w * k, y))
    base = h * k * 0.86
    draw_cup_ball(s, w * k * 0.5, base - 22, 22)
    for i, dx in enumerate((-0.28, 0, 0.28)):
        draw_cup(s, w * k * (0.5 + dx), base - (95 if i == 1 else 0), 1.35)
    return pygame.transform.smoothscale(s, (w, h))


# --------------------------------------------------------------------------
# Multiplayer over the same Wi-Fi: one player hosts, the others join.
# The host's game runs a tiny server - no accounts or sign-ups needed.
# --------------------------------------------------------------------------
NET_PORT = 50777                # the game connection
NET_FIND_PORT = 50778           # hosts announce themselves here so friends can find them
NET_MAX_PLAYERS = 8
NET_VERSION = 1
NAME_MAX = 15                   # longest username allowed
CHAT_MAX = 100                  # longest chat message
NET_BIND = ""                   # listen on every network adapter (the Wi-Fi)


def local_ip():
    """This computer's address on the Wi-Fi (what friends type in to join)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))        # no data is sent - this just picks the Wi-Fi adapter
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def clean_name(name):
    name = "".join(ch for ch in str(name) if ch.isalnum() or ch in " _-.").strip()[:NAME_MAX]
    return name or "Player"


def net_int(v, hi=10 ** 9):
    try:
        return max(0, min(hi, int(v)))
    except (TypeError, ValueError):
        return 0


class NetLink:
    """One connection that sends and receives JSON messages without ever freezing the game."""

    def __init__(self, sock):
        self.sock = sock
        sock.setblocking(False)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.inbuf = b""
        self.outbuf = b""
        self.closed = False

    def send(self, msg):
        if not self.closed:
            self.outbuf += (json.dumps(msg, separators=(",", ":")) + "\n").encode()

    def read(self):
        msgs = []
        if not self.closed:
            try:
                while True:
                    data = self.sock.recv(65536)
                    if not data:
                        self.close()
                        break
                    self.inbuf += data
                    if len(self.inbuf) > 4_000_000:
                        self.close()
                        break
            except (BlockingIOError, InterruptedError):
                pass
            except OSError:
                self.close()
        while b"\n" in self.inbuf:
            line, self.inbuf = self.inbuf.split(b"\n", 1)
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict):
                msgs.append(m)
        return msgs

    def flush(self):
        while self.outbuf and not self.closed:
            try:
                n = self.sock.send(self.outbuf)
                self.outbuf = self.outbuf[n:]
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.sock.close()
            except OSError:
                pass


# ---- shared poker table (runs on the host) --------------------------------
PT_TURN = 30.0                  # seconds to act before you're checked / folded automatically
PT_COLORS = [(200, 70, 70), (70, 120, 200), (80, 160, 90), (170, 90, 190)]


class PokerTable:
    """4 seats, always full: computer players sit in any seat a real player isn't using."""

    def __init__(self, srv):
        self.srv = srv
        self.seats = [None] * 4
        self.pending = {}               # players waiting to sit down when the current hand ends
        for i in range(4):
            self.seats[i] = self.new_bot()
        self.phase = "waiting"          # waiting, hand, between
        self.board, self.deck = [], []
        self.dealer = -1
        self.to_act = -1
        self.awaiting = False
        self.current_bet, self.min_raise, self.raises, self.street = 0, POKER_BB, 0, 0
        self.reveal = False
        self.queue = []
        self.timer = 0.0
        self.turn_t = 0.0
        self.ready = set()
        self.hand = 0
        self.message = "SIT DOWN TO START - COMPUTER PLAYERS FILL THE EMPTY SEATS"
        self.dirty = True
        self.resend = 0.0

    def new_bot(self):
        taken = {s["name"] for s in self.seats if s} | {p["name"] for p in self.pending.values()}
        s = {"pid": -1, "bot": True, "name": random.choice([n for n in BOT_NAMES if n not in taken]),
             "stack": random.choice([300, 400, 500, 600, 800, 1000]), "aggr": random.uniform(0.85, 1.2),
             "bubble": "", "bubble_t": 0.0, "gone": False}
        self.reset_seat(s)
        return s

    def humans(self):
        return [s for s in self.seats if s and not s.get("bot")]

    def place(self, pid, name, amt):
        """Put a real player in an empty seat, or in place of a computer player."""
        idx = next((i for i, s in enumerate(self.seats) if not s),
                   next((i for i, s in enumerate(self.seats) if s and s.get("bot")), None))
        if idx is None:
            return False
        old = self.seats[idx]
        s = {"pid": pid, "name": name, "stack": amt, "bubble": "", "bubble_t": 0.0, "gone": False}
        self.reset_seat(s)
        self.seats[idx] = s
        self.message = f"{name.upper()} SITS DOWN" + (f" IN PLACE OF {old['name'].upper()}" if old else "")
        return True

    @staticmethod
    def live(s):
        return bool(s and s["cards"]) and not s["folded"]

    def can_act(self, s):
        return self.live(s) and not s["allin"]

    def seat_of(self, pid):
        return next((i for i, s in enumerate(self.seats) if s and s["pid"] == pid), None)

    def players(self):
        return [s for s in self.seats if s]

    def schedule(self, delay, fn):
        self.queue.append([delay, fn])

    def reset_seat(self, s):
        s.update(cards=[], bet=0, total=0, won=0, folded=False, allin=False, acted=False, best=None)

    # ---- messages ----------------------------------------------------------
    def on_msg(self, pid, client, m):
        t = m.get("t")
        if t == "poker_sit":
            amt = net_int(m.get("buyin"), BUYIN_MAX)
            full = len(self.humans()) + len(self.pending) >= 4
            if self.seat_of(pid) is not None or pid in self.pending or full or amt <= 0:
                self.srv.send(pid, {"t": "poker_cash", "amount": amt, "why": "THE TABLE IS FULL"})
                return
            if self.phase == "hand":
                self.pending[pid] = {"name": client["name"], "buyin": amt}
                self.message = f"{client['name'].upper()} WILL SIT DOWN WHEN THIS HAND ENDS"
            else:
                self.place(pid, client["name"], amt)
                if self.phase == "waiting":
                    self.phase, self.timer = "between", 3.0
        elif t == "poker_stand":
            self.stand(pid)
        elif t == "poker_ready":
            if self.phase == "between" and self.seat_of(pid) is not None:
                self.ready.add(pid)
                if all(s["pid"] in self.ready for s in self.humans()):
                    self.timer = min(self.timer, 0.5)
        elif t == "poker_act":
            i = self.seat_of(pid)
            if i is not None and self.awaiting and self.to_act == i and self.phase == "hand":
                kind = m.get("kind")
                if kind in ("fold", "call", "raise"):
                    self.act(i, kind, net_int(m.get("to")))
        self.dirty = True

    def stand(self, pid, gone=False):
        if self.pending.pop(pid, None):
            self.dirty = True
            return
        i = self.seat_of(pid)
        if i is None:
            return
        s = self.seats[i]
        if self.phase == "hand" and s["total"] and not self.live(s):
            s["gone"] = True                 # already folded: their chips stay in the pot until the hand ends
        elif self.phase == "hand" and self.live(s):
            s["gone"] = True                 # fold now, leave the table when the hand ends
            if self.awaiting and self.to_act == i:
                self.act(i, "fold")
            else:
                s["folded"] = True
                if sum(1 for o in self.players() if self.live(o)) == 1:
                    self.queue = []
                    self.schedule(0.3, self.begin_turn)
        else:
            self.seats[i] = self.new_bot()
            self.message = f"{s['name'].upper()} {'LEFT' if gone else 'STANDS UP'} - {self.seats[i]['name'].upper()} SITS IN"
            if not self.humans() and self.phase != "hand":
                self.phase = "waiting"
        self.dirty = True

    # ---- hand flow (same rules as the single-player table) -----------------
    def next_seat(self, i):
        for k in range(1, 5):
            j = (i + k) % 4
            if self.seats[j]:
                return j
        return i

    def post(self, s, amount):
        a = min(amount, s["stack"])
        s["stack"] -= a
        s["bet"] += a
        s["total"] += a
        if s["stack"] == 0:
            s["allin"] = True
        return a

    def bubble(self, s, text, t=2.2):
        s["bubble"], s["bubble_t"] = text, t

    def start_hand(self):
        self.queue = []
        self.ready = set()
        notice = ""
        for i, s in enumerate(self.seats):
            if s and (s["gone"] or s["stack"] < POKER_BB):
                if s.get("bot"):
                    notice = f"{s['name'].upper()} IS OUT OF CHIPS"
                elif not s["gone"]:
                    self.srv.send(s["pid"], {"t": "poker_cash", "amount": s["stack"], "why": "YOU'RE OUT OF CHIPS"})
                self.seats[i] = None
        for pid, p in list(self.pending.items()):
            if pid in self.srv.clients:
                self.place(pid, p["name"], p["buyin"])
            del self.pending[pid]
        for i in range(4):
            if not self.seats[i]:
                self.seats[i] = self.new_bot()
        for s in self.players():
            self.reset_seat(s)
        self.board = []
        self.reveal = False
        self.dirty = True
        if not self.humans():
            self.phase = "waiting"
            self.to_act = -1
            self.message = "SIT DOWN TO START - COMPUTER PLAYERS FILL THE EMPTY SEATS"
            return
        self.deck = [(r, s) for s in SUITS for r in RANKS]
        random.shuffle(self.deck)
        self.dealer = self.next_seat(self.dealer if self.dealer >= 0 else 3)
        if len(self.players()) == 2:
            sb = self.dealer
        else:
            sb = self.next_seat(self.dealer)
        bb = self.next_seat(sb)
        for _ in range(2):
            for s in self.players():
                s["cards"].append(self.deck.pop())
        self.post(self.seats[sb], POKER_SB)
        self.post(self.seats[bb], POKER_BB)
        self.bubble(self.seats[sb], "SMALL BLIND", 1.5)
        self.bubble(self.seats[bb], "BIG BLIND", 1.5)
        self.current_bet, self.min_raise, self.raises, self.street = POKER_BB, POKER_BB, 0, 0
        self.phase = "hand"
        self.hand += 1
        self.awaiting = False
        self.message = notice
        self.to_act = self.next_seat(bb)
        self.schedule(0.8, self.begin_turn)

    def pot(self):
        return sum(s["total"] for s in self.players())

    def round_done(self):
        actors = [s for s in self.players() if self.can_act(s)]
        if not actors:
            return True
        if len(actors) == 1 and actors[0]["bet"] >= self.current_bet:
            return True
        return all(s["acted"] and s["bet"] == self.current_bet for s in actors)

    def begin_turn(self):
        self.dirty = True
        live = [s for s in self.players() if self.live(s)]
        if len(live) == 1:
            self.award({id(live[0]): self.pot()}, f"{live[0]['name'].upper()} WINS {money(self.pot())} - "
                                                  f"EVERYONE ELSE FOLDED")
            return
        if self.round_done():
            self.end_street()
            return
        for _ in range(4):
            if self.can_act(self.seats[self.to_act]):
                break
            self.to_act = (self.to_act + 1) % 4
        if self.seats[self.to_act].get("bot"):
            i = self.to_act
            self.awaiting = False
            self.schedule(random.uniform(0.6, 1.3), lambda: self.bot_act(i))
            return
        self.awaiting = True
        self.turn_t = PT_TURN

    def bot_act(self, i):
        """Same computer player as the single-player table."""
        s = self.seats[i]
        if self.phase != "hand" or self.to_act != i or not self.can_act(s):
            return
        live = [o for o in self.players() if self.live(o)]
        opp = len(live) - 1
        board = [(RANK_NUM[r], su) for r, su in self.board]
        eq = equity([(RANK_NUM[r], su) for r, su in s["cards"]], board, max(1, opp))
        strength = min(1.0, eq * (opp + 1) / 2) * s["aggr"]
        to_call = self.current_bet - s["bet"]
        pot = self.pot()
        odds = to_call / (pot + to_call) if to_call else 0
        r = random.random()
        if strength > 0.85 and r < 0.75:
            target = self.current_bet + max(self.min_raise, pot + to_call)
            if strength > 0.95 and r < 0.2:
                target = s["bet"] + s["stack"]
            self.act(i, "raise", target)
        elif strength > 0.62 and r < 0.4:
            self.act(i, "raise", self.current_bet + max(self.min_raise, (pot + to_call) // 2))
        elif to_call == 0:
            if r < 0.08 * s["aggr"]:
                self.act(i, "raise", self.current_bet + max(self.min_raise, pot // 2))
            else:
                self.act(i, "call")
        elif eq >= odds * 0.95 or (to_call <= POKER_BB and eq > 1 / (opp + 2)) or r < 0.04:
            self.act(i, "call")
        else:
            self.act(i, "fold")

    def act(self, i, kind, raise_to=0):
        s = self.seats[i]
        to_call = self.current_bet - s["bet"]
        if kind == "raise" and (self.raises >= 4 or s["stack"] <= to_call):
            kind = "call"
        if kind == "fold":
            s["folded"] = True
            self.bubble(s, "FOLD")
        elif kind == "call":
            paid = self.post(s, to_call)
            self.bubble(s, "CHECK" if paid == 0 else "ALL IN" if s["allin"] else f"CALL {money(paid)}")
        else:
            raise_to = min(max(raise_to, self.current_bet + self.min_raise), s["bet"] + s["stack"])
            if raise_to - self.current_bet >= self.min_raise:
                self.min_raise = raise_to - self.current_bet
            was_bet = self.current_bet == 0
            self.post(s, raise_to - s["bet"])
            self.current_bet = max(self.current_bet, s["bet"])
            self.raises += 1
            for o in self.players():
                if o is not s and self.can_act(o):
                    o["acted"] = False
            self.bubble(s, "ALL IN" if s["allin"] else f"{'BET' if was_bet else 'RAISE TO'} {money(s['bet'])}")
        s["acted"] = True
        self.awaiting = False
        self.to_act = (i + 1) % 4
        self.dirty = True
        self.schedule(0.35, self.begin_turn)

    def end_street(self):
        for s in self.players():
            s["bet"] = 0
            s["acted"] = False
        self.current_bet, self.min_raise, self.raises = 0, POKER_BB, 0
        self.awaiting = False
        if self.street == 3:
            self.schedule(0.6, self.showdown)
            return
        self.street += 1
        n = 3 if self.street == 1 else 1
        self.schedule(0.45, lambda: self.deal_board(n))
        if sum(1 for s in self.players() if self.can_act(s)) <= 1:
            self.reveal = True
            self.schedule(1.1, self.end_street)
        else:
            self.to_act = (self.dealer + 1) % 4
            self.schedule(0.8, self.begin_turn)

    def deal_board(self, n):
        for _ in range(n):
            self.board.append(self.deck.pop())
        self.dirty = True

    def showdown(self):
        self.reveal = True
        board = [(RANK_NUM[r], su) for r, su in self.board]
        live = [s for s in self.players() if self.live(s)]
        for s in live:
            s["best"] = eval_hand([(RANK_NUM[r], su) for r, su in s["cards"]] + board)
            self.bubble(s, HAND_NAMES[s["best"][0]], 6.0)
        winnings = {}
        levels = sorted({s["total"] for s in self.players() if s["total"]})
        prev = 0
        main = None
        for lvl in levels:                       # main pot + side pots
            amount = sum(min(s["total"], lvl) - min(s["total"], prev) for s in self.players())
            eligible = [s for s in live if s["total"] >= lvl] or live
            best = max(s["best"] for s in eligible)
            winners = [s for s in eligible if s["best"] == best]
            share, extra = divmod(amount, len(winners))
            for k, s in enumerate(winners):
                winnings[id(s)] = winnings.get(id(s), 0) + share + (extra if k == 0 else 0)
            if main is None:
                main = winners
            prev = lvl
        names = " & ".join(s["name"].upper() for s in main)
        self.award(winnings, f"{names} WIN{'S' if len(main) == 1 else ''} WITH {HAND_NAMES[main[0]['best'][0]].upper()}")

    def award(self, winnings, text):
        for s in self.players():
            amt = winnings.get(id(s), 0)
            s["stack"] += amt
            s["won"] += amt
            if s["total"] and not s["gone"] and not s.get("bot"):
                self.srv.send(s["pid"], {"t": "poker_result", "total": s["total"], "won": s["won"],
                                         "best": s["best"][0] if s["best"] else -1})
            if s["won"]:
                self.bubble(s, f"WINS {money(s['won'])}", 6.0)
        self.message = text
        self.phase = "between"
        self.timer = 7.0
        self.awaiting = False
        self.to_act = -1
        self.dirty = True
        for i, s in enumerate(self.seats):
            if s and s["gone"]:
                self.seats[i] = self.new_bot()
        if not self.humans():
            self.phase = "waiting"

    def update(self, dt):
        if self.queue:
            self.queue[0][0] -= dt
            if self.queue[0][0] <= 0:
                _, fn = self.queue.pop(0)
                fn()
        for s in self.players():
            if s["bubble_t"] > 0:
                s["bubble_t"] -= dt
                if s["bubble_t"] <= 0:
                    self.dirty = True
        if self.phase == "hand" and self.awaiting and not self.queue:
            self.turn_t -= dt
            if self.turn_t <= 0:
                s = self.seats[self.to_act]
                self.act(self.to_act, "call" if self.current_bet == s["bet"] else "fold")
        elif self.phase == "between" and not self.queue:
            self.timer -= dt
            if self.timer <= 0:
                self.start_hand()
        self.resend -= dt
        if self.dirty or self.resend <= 0:
            self.dirty = False
            self.resend = 1.0
            for pid in self.srv.clients:
                self.srv.send(pid, {"t": "poker", "s": self.view(pid)})

    def view(self, pid):
        seats = []
        for s in self.seats:
            if not s:
                seats.append(None)
                continue
            show = s["pid"] == pid or (self.reveal and self.live(s))
            seats.append({"pid": s["pid"], "bot": bool(s.get("bot")), "name": s["name"], "stack": s["stack"],
                          "bet": s["bet"], "total": s["total"],
                          "won": s["won"], "folded": s["folded"], "allin": s["allin"],
                          "cards": [list(c) if show else None for c in s["cards"]],
                          "bubble": s["bubble"] if s["bubble_t"] > 0 else ""})
        return {"seats": seats, "phase": self.phase, "board": [list(c) for c in self.board], "dealer": self.dealer,
                "to_act": self.to_act if self.awaiting else -1, "cur": self.current_bet, "minr": self.min_raise,
                "raises": self.raises, "timer": round(self.timer, 1), "turn": round(self.turn_t, 1),
                "msg": self.message, "reveal": self.reveal, "hand": self.hand, "pending": list(self.pending)}


# ---- shared blackjack table (runs on the host) ----------------------------
BJ_TURN = 20.0                  # seconds to act before you automatically stand
BJ_BET_WAIT = 12.0              # once someone bets, the others have this long to bet too
BJ_SPOTS = [205, 495, 785, 1075]


def bj_total(cards):
    return hand_value([r for r, _ in cards])[0]


class BJTable:
    def __init__(self, srv):
        self.srv = srv
        self.seats = [None] * 4
        self.phase = "betting"          # betting, playing, dealer, result
        self.dealer = []
        self.hole_shown = False
        self.shoe = Shoe(6)
        self.queue = []
        self.timer = 0.0
        self.turn_t = 0.0
        self.to_act = -1
        self.round = 0
        self.message = "PLACE YOUR BETS"
        self.dirty = True
        self.resend = 0.0

    def seat_of(self, pid):
        return next((i for i, s in enumerate(self.seats) if s and s["pid"] == pid), None)

    def in_round(self):
        return [s for s in self.seats if s and s["bet"] and s["cards"]]

    def schedule(self, delay, fn):
        self.queue.append([delay, fn])

    def on_msg(self, pid, client, m):
        t = m.get("t")
        i = self.seat_of(pid)
        if t == "bj_sit":
            free = [k for k in range(4) if not self.seats[k]]
            if i is None and free:
                self.seats[free[0]] = {"pid": pid, "name": client["name"], "bet": 0, "cards": [], "done": False,
                                       "result": "", "payout": 0}
            elif i is None:
                self.srv.send(pid, {"t": "bj_full"})
        elif t == "bj_leave" and i is not None:
            s = self.seats[i]
            if s["bet"] and self.phase == "betting":
                self.srv.send(pid, {"t": "bj_refund", "amount": s["bet"]})
            if not s["bet"] or self.phase == "betting" or self.phase == "result":
                self.seats[i] = None
        elif t == "bj_bet":
            amt = net_int(m.get("amount"))
            if i is None or self.phase != "betting" or self.seats[i]["bet"] or amt <= 0:
                self.srv.send(pid, {"t": "bj_refund", "amount": amt})
            else:
                self.seats[i]["bet"] = amt
                bettors = [s for s in self.seats if s and s["bet"]]
                if len(bettors) == 1:
                    self.timer = BJ_BET_WAIT
                if all(s["bet"] for s in self.seats if s):
                    self.timer = min(self.timer, 1.0)
        elif t == "bj_act" and i is not None and self.phase == "playing" and self.to_act == i and not self.queue:
            s = self.seats[i]
            kind = m.get("kind")
            if kind == "hit":
                self.give(s)
                if bj_total(s["cards"]) >= 21:
                    self.next_player(0.5)
            elif kind == "stand":
                self.next_player(0.2)
            elif kind == "double":
                if len(s["cards"]) == 2:
                    s["bet"] *= 2
                    s["doubled"] = True
                    self.give(s)
                    self.next_player(0.6)
                else:
                    self.srv.send(pid, {"t": "bj_refund", "amount": s["bet"]})
        self.dirty = True

    def drop(self, pid):
        i = self.seat_of(pid)
        if i is None:
            return
        if self.phase == "playing" and self.to_act == i:
            self.seats[i]["done"] = True
            self.seats[i] = None
            self.next_player(0.2)
        else:
            self.seats[i] = None
        self.dirty = True

    def give(self, s):
        s["cards"].append(self.shoe.draw())
        self.dirty = True

    def deal(self):
        if self.shoe.needs_shuffle():
            self.shoe.shuffle()
        players = [s for s in self.seats if s and s["bet"]]
        if not players:
            self.phase = "betting"
            return
        self.round += 1
        self.phase = "playing"
        self.dealer, self.hole_shown = [], False
        self.to_act = -1
        self.message = ""
        order = [s for s in self.seats if s and s["bet"]]
        steps = []
        for k in range(2):
            for s in order:
                steps.append(lambda s=s: self.give(s))
            steps.append(lambda: (self.dealer.append(self.shoe.draw()), setattr(self, "dirty", True)))
        for fn in steps:
            self.schedule(0.3, fn)
        self.schedule(0.4, self.after_deal)

    def after_deal(self):
        up = self.dealer[0][0]
        if up in ("A", "10", "J", "Q", "K") and bj_total(self.dealer) == 21:
            self.hole_shown = True
            self.message = "DEALER HAS BLACKJACK"
            self.schedule(1.0, self.settle)
            return
        self.to_act = -1
        self.next_player(0.1)

    def next_player(self, delay):
        def go():
            for k in range(self.to_act + 1, 4):
                s = self.seats[k]
                if s and s["bet"] and s["cards"] and bj_total(s["cards"]) < 21:
                    self.to_act = k
                    self.turn_t = BJ_TURN
                    self.dirty = True
                    return
            self.to_act = -1
            self.dealer_turn()
        self.to_act = self.to_act if self.to_act >= 0 else -1
        self.schedule(delay, go)

    def dealer_turn(self):
        self.phase = "dealer"
        self.hole_shown = True
        self.dirty = True
        live = [s for s in self.in_round() if bj_total(s["cards"]) <= 21 and not
                (len(s["cards"]) == 2 and bj_total(s["cards"]) == 21)]
        if not live:
            self.schedule(0.8, self.settle)
        else:
            self.schedule(0.8, self.dealer_step)

    def dealer_step(self):
        if bj_total(self.dealer) < 17:
            self.dealer.append(self.shoe.draw())
            self.dirty = True
            self.schedule(0.65, self.dealer_step)
        else:
            self.schedule(0.3, self.settle)

    def settle(self):
        dv = bj_total(self.dealer)
        dbj = len(self.dealer) == 2 and dv == 21
        for s in self.in_round():
            pv = bj_total(s["cards"])
            pbj = len(s["cards"]) == 2 and pv == 21 and not s.get("doubled")
            bet = s["bet"]
            if pv > 21:
                s["result"], s["payout"] = "BUST", 0
            elif pbj and not dbj:
                s["result"], s["payout"] = "BLACKJACK!", bet * 5 // 2
            elif pbj and dbj:
                s["result"], s["payout"] = "PUSH", bet
            elif dbj:
                s["result"], s["payout"] = "LOSE", 0
            elif dv > 21 or pv > dv:
                s["result"], s["payout"] = "WIN", bet * 2
            elif pv == dv:
                s["result"], s["payout"] = "PUSH", bet
            else:
                s["result"], s["payout"] = "LOSE", 0
            self.srv.send(s["pid"], {"t": "bj_result", "stake": bet, "returned": s["payout"], "result": s["result"],
                                     "cards": len(s["cards"])})
        self.message = "DEALER HAS BLACKJACK" if dbj else ("DEALER BUSTS!" if dv > 21 else f"DEALER HAS {dv}")
        self.phase = "result"
        self.timer = 5.0
        self.hole_shown = True
        self.dirty = True

    def reset_round(self):
        for s in self.seats:
            if s:
                s.update(bet=0, cards=[], done=False, result="", payout=0, doubled=False)
        self.dealer, self.hole_shown = [], False
        self.phase = "betting"
        self.to_act = -1
        self.timer = 0.0
        self.message = "PLACE YOUR BETS"
        self.dirty = True

    def update(self, dt):
        if self.queue:
            self.queue[0][0] -= dt
            if self.queue[0][0] <= 0:
                _, fn = self.queue.pop(0)
                fn()
        elif self.phase == "betting" and any(s and s["bet"] for s in self.seats):
            self.timer -= dt
            if self.timer <= 0:
                self.deal()
        elif self.phase == "playing" and self.to_act >= 0:
            self.turn_t -= dt
            if self.turn_t <= 0:
                self.next_player(0.1)
        elif self.phase == "result":
            self.timer -= dt
            if self.timer <= 0:
                self.reset_round()
        self.resend -= dt
        if self.dirty or self.resend <= 0:
            self.dirty = False
            self.resend = 1.0
            self.srv.broadcast({"t": "bj", "s": self.view()})

    def view(self):
        seats = []
        for s in self.seats:
            if not s:
                seats.append(None)
                continue
            seats.append({"pid": s["pid"], "name": s["name"], "bet": s["bet"], "cards": [list(c) for c in s["cards"]],
                          "total": bj_total(s["cards"]) if s["cards"] else 0, "result": s["result"],
                          "payout": s["payout"]})
        dealer = [list(c) if (k != 1 or self.hole_shown) else None for k, c in enumerate(self.dealer)]
        shown = [tuple(c) for c in dealer if c]
        return {"seats": seats, "dealer": dealer, "dtotal": bj_total(shown) if shown else 0, "phase": self.phase,
                "to_act": self.to_act, "timer": round(self.timer, 1), "turn": round(self.turn_t, 1),
                "msg": self.message, "round": self.round}


# ---- the host's server ------------------------------------------------------
class NetServer:
    def __init__(self, host_name):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind((NET_BIND, NET_PORT))
        self.sock.listen(8)
        self.sock.setblocking(False)
        self.name = host_name
        self.clients = {}
        self.next_id = 1
        self.poker = PokerTable(self)
        self.bj = BJTable(self)
        self.party = {"crash": CrashTable(self), "hc": HighCardTable(self), "liar": LiarsTable(self),
                      "bingo": BingoTable(self)}
        self.roster_t = 0.0
        self.beacon_t = 0.0
        self.beacon = None
        try:
            self.beacon = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.beacon.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.beacon.setblocking(False)
        except OSError:
            self.beacon = None

    def send(self, pid, msg):
        c = self.clients.get(pid)
        if c:
            c["link"].send(msg)

    def broadcast(self, msg):
        for c in self.clients.values():
            c["link"].send(msg)

    def update(self, dt):
        while True:
            try:
                sock, _ = self.sock.accept()
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            link = NetLink(sock)
            if len(self.clients) >= NET_MAX_PLAYERS:
                link.send({"t": "full"})
                link.flush()
                link.close()
                continue
            pid = self.next_id
            self.next_id += 1
            self.clients[pid] = {"link": link, "name": f"Player {pid}", "scene": "menu", "balance": 0, "bet": 0}
        for pid, c in list(self.clients.items()):
            for m in c["link"].read():
                self.on_msg(pid, c, m)
            if c["link"].closed:
                self.drop(pid)
        self.poker.update(dt)
        self.bj.update(dt)
        for table in self.party.values():
            table.update(dt)
        self.roster_t -= dt
        if self.roster_t <= 0:
            self.roster_t = 0.5
            self.broadcast({"t": "roster", "host": self.name, "players": [
                {"id": pid, "name": c["name"], "scene": c["scene"], "balance": c["balance"], "bet": c["bet"]}
                for pid, c in self.clients.items()]})
        self.beacon_t -= dt
        if self.beacon and self.beacon_t <= 0:
            self.beacon_t = 1.0
            msg = json.dumps({"game": "grand_royale", "v": NET_VERSION, "name": self.name,
                              "players": len(self.clients)}).encode()
            for addr in ("<broadcast>", "255.255.255.255"):
                try:
                    self.beacon.sendto(msg, (addr, NET_FIND_PORT))
                    break
                except OSError:
                    continue
        for c in self.clients.values():
            c["link"].flush()

    def on_msg(self, pid, c, m):
        t = str(m.get("t", ""))
        if t == "hello":
            name = clean_name(m.get("name"))
            taken = {o["name"] for p, o in self.clients.items() if p != pid}
            base, k = name, 2
            while name in taken:
                name = f"{base[:NAME_MAX - 3]} {k}"
                k += 1
            c["name"] = name
            for table in (self.poker, self.bj):
                for seat in table.seats:
                    if seat and seat.get("pid") == pid:
                        seat["name"] = name
                table.dirty = True
            if pid == min(self.clients):
                self.name = name
            c["link"].send({"t": "welcome", "id": pid, "name": name, "host": self.name, "ver": VERSION})
        elif t == "status":
            c["scene"] = str(m.get("scene", "menu"))[:20]
            c["balance"] = net_int(m.get("balance"), 10 ** 12)
            c["bet"] = net_int(m.get("bet"))
        elif t == "chat":
            text = "".join(ch for ch in str(m.get("text", "")) if ch.isprintable()).strip()[:CHAT_MAX]
            now = time.time()
            c["chat_times"] = [x for x in c.get("chat_times", []) if now - x < 5] + [now]
            if text and len(c["chat_times"]) <= 6:
                self.broadcast({"t": "chat", "id": pid, "name": c["name"], "text": text})
        elif t == "result":
            self.broadcast({"t": "activity", "id": pid, "game": str(m.get("game", ""))[:20],
                            "stake": net_int(m.get("stake")), "returned": net_int(m.get("returned"))})
        elif t.startswith("poker_"):
            self.poker.on_msg(pid, c, m)
        elif t.startswith("bj_"):
            self.bj.on_msg(pid, c, m)
        elif t.split("_")[0] in self.party:
            self.party[t.split("_")[0]].on_msg(pid, c, m)

    def drop(self, pid):
        self.poker.stand(pid, gone=True)
        i = self.poker.seat_of(pid)
        if i is not None:
            self.poker.seats[i]["gone"] = True
        self.bj.drop(pid)
        for table in self.party.values():
            table.drop(pid)
        c = self.clients.pop(pid, None)
        if c:
            c["link"].close()

    def close(self):
        for c in self.clients.values():
            c["link"].flush()
            c["link"].close()
        self.clients = {}
        for s in (self.sock, self.beacon):
            try:
                if s:
                    s.close()
            except OSError:
                pass


# ---- every player's connection (the host connects to itself too) -----------
class NetClient:
    def __init__(self, app, addr, name):
        self.app = app
        self.addr = addr
        sock = socket.create_connection((addr, NET_PORT), timeout=3)
        self.link = NetLink(sock)
        self.id = None
        self.name = name
        self.host_name = ""
        self.players = {}
        self.activity = {}          # player id -> latest result (game, stake, returned, time)
        self.chat = deque(maxlen=60)  # (name, text, time, kind) - kind is "me", "them" or "info"
        self.unread = 0
        self.status_t = 0.0
        self.link.send({"t": "hello", "name": name, "v": NET_VERSION, "ver": VERSION})
        self.link.flush()

    def send(self, msg):
        self.link.send(msg)

    def others(self, scene=None):
        return [p for pid, p in self.players.items() if pid != self.id and (scene is None or p["scene"] == scene)]

    def update(self, dt):
        for m in self.link.read():
            self.on_msg(m)
        self.status_t -= dt
        if self.status_t <= 0:
            self.status_t = 0.4
            self.send({"t": "status", "scene": self.app.scene, "balance": int(self.app.balance),
                       "bet": self.app.live_bet()})
        self.link.flush()
        return not self.link.closed

    def on_msg(self, m):
        t = m.get("t")
        app = self.app
        if t == "welcome":
            first = self.id is None
            self.id, self.name, self.host_name = m.get("id"), m.get("name", self.name), m.get("host", "")
            host_ver = str(m.get("ver", "?"))
            if first and host_ver != VERSION:
                app.effects.toast("DIFFERENT GAME VERSIONS",
                                  f"The host has v{host_ver}, you have v{VERSION} - some things may not work")
        elif t == "full":
            app.effects.toast("LOBBY IS FULL", f"Only {NET_MAX_PLAYERS} players can join one game")
        elif t == "roster":
            self.host_name = m.get("host", self.host_name)
            old = set(self.players)
            old_names = {pid: p["name"] for pid, p in self.players.items()}
            self.players = {p["id"]: p for p in m.get("players", []) if isinstance(p, dict) and "id" in p}
            if old and self.id is not None:
                for pid in set(self.players) - old:
                    app.effects.toast("PLAYER JOINED", f"{self.players[pid]['name']} joined the lobby")
                    self.chat.append(("", f"{self.players[pid]['name']} joined", time.time(), "info"))
                for pid in old - set(self.players):
                    self.activity.pop(pid, None)
                    self.chat.append(("", f"{old_names.get(pid, 'A player')} left", time.time(), "info"))
        elif t == "chat":
            mine = m.get("id") == self.id
            self.chat.append((str(m.get("name", "?")), str(m.get("text", ""))[:CHAT_MAX], time.time(),
                              "me" if mine else "them"))
            if not mine:
                if not app.chat_open:
                    self.unread += 1
                app.sfx("card")
        elif t == "activity":
            self.activity[m.get("id")] = (m.get("game"), net_int(m.get("stake")), net_int(m.get("returned")),
                                          time.time())
        elif t == "poker":
            app.scenes["netpoker"].on_state(m.get("s") or {})
        elif t == "poker_cash":
            amt = net_int(m.get("amount"))
            app.scenes["netpoker"].cash(amt, m.get("why", ""))
        elif t == "poker_result":
            app.scenes["netpoker"].result(net_int(m.get("total")), net_int(m.get("won")), m.get("best", -1))
        elif t == "bj":
            app.scenes["netbj"].on_state(m.get("s") or {})
        elif t in ("bj_result", "bj_refund", "bj_full"):
            app.scenes["netbj"].on_msg(m)
        elif t in MP_KINDS:
            app.scenes[MP_KINDS[t]].on_state(m.get("s") or {})
        elif t in ("mp_result", "mp_refund", "mp_bonus") and m.get("game") in MP_KINDS:
            app.scenes[MP_KINDS[m["game"]]].on_msg(m)

    def close(self):
        self.link.flush()
        self.link.close()


class LabelButton(Button):
    """A button whose label and enabled state are worked out each time it's drawn."""

    def __init__(self, rect, color, size, label_fn, on_fn, hint=None):
        super().__init__(rect, "", color, size, hint)
        self.label_fn, self.on_fn = label_fn, on_fn

    def draw(self, surf, mouse, enabled=True):
        self.text = self.label_fn()
        super().draw(surf, mouse, self.on_fn())

    def clicked(self, pos, enabled=True):
        return self.on_fn() and self.rect.collidepoint(pos)


# ---- multiplayer poker (real players only) ---------------------------------
class NetPoker(Poker):
    key = "netpoker"

    def __init__(self, app):
        super().__init__(app)
        self.reset_view()
        self.btn_next = LabelButton((965, 646, 270, 62), (25, 120, 60), 20, self.next_label, self.next_on,
                                    "EVERYONE READY = DEAL NOW")

    def reset_view(self):
        self.state = None
        self.my = None
        self.sitting = 0
        self.stood = False
        self.hand_no = -1
        self.ready_sent = False
        self.seats = [None] * 4
        self.you = Seat("YOU", 0, bot=False)
        self.phase = "idle"
        self.board, self.my_sprites = [], []
        self.your_turn = False
        self.dealer = self.to_act = -1
        self.message = "BUY IN AND TAKE A SEAT - YOU'LL REPLACE A COMPUTER PLAYER"
        self.drawing = False

    def next_label(self):
        if not self.state or self.state.get("phase") == "waiting":
            return "STARTING..."
        if self.ready_sent:
            return f"READY!  ({max(0, math.ceil(self.state.get('timer', 0)))})"
        return f"DEAL NOW  ({max(0, math.ceil(self.state.get('timer', 0)))})"

    def next_on(self):
        return bool(self.state) and self.state.get("phase") == "between" and not self.ready_sent

    def on_state(self, s):
        if not s or "seats" not in s:
            return
        me = self.app.net.id if self.app.net else None
        my = next((i for i, x in enumerate(s["seats"]) if x and x["pid"] == me), None)
        if self.stood:
            if my is not None:
                my = None                   # an old update from before we stood up
            else:
                self.stood = False
        was_turn = self.your_turn
        self.state = s
        self.my = my
        if my is not None:
            self.sitting = 0
        off = my if my is not None else 0
        rot = lambda i: (i - off) % 4
        seats = [None] * 4
        for i, x in enumerate(s["seats"]):
            if not x or (self.stood and x["pid"] == me):
                continue
            st = Seat("YOU" if i == my else x["name"], x["stack"], bot=i != my)
            st.color = PT_COLORS[i]
            st.cards = [tuple(c) if c else ("A", "S") for c in x["cards"]]
            st.bet, st.total, st.won = x["bet"], x["total"], x["won"]
            st.folded, st.allin = x["folded"], x["allin"]
            st.bubble = x["bubble"]
            st.bubble_t = 1.0 if x["bubble"] else 0.0
            seats[rot(i)] = st
        self.seats = seats
        self.you = seats[0] if my is not None else Seat("YOU", 0, bot=False)
        ph = s["phase"]
        self.phase = "idle" if my is None else ("hand" if ph == "hand" else "between")
        self.dealer = rot(s["dealer"]) if s["dealer"] >= 0 else -1
        self.to_act = rot(s["to_act"]) if s["to_act"] >= 0 else -1
        self.your_turn = my is not None and s["to_act"] == my
        self.current_bet, self.min_raise, self.raises = s["cur"], s["minr"], s["raises"]
        self.reveal = s["reveal"]
        self.between_t = s["timer"]
        if my is None and self.sitting and me in s.get("pending", []):
            self.message = "YOU'LL SIT DOWN WHEN THIS HAND ENDS"
        elif my is not None or s["msg"]:
            self.message = s["msg"]
        if s["hand"] != self.hand_no:
            self.hand_no = s["hand"]
            self.board, self.my_sprites = [], []
            self.ready_sent = False
        if my is not None and ph == "hand" and self.you.cards and not self.my_sprites:
            cx, cy = SEAT_CARDS[0]
            for j, (r, su) in enumerate(self.you.cards):
                sp = CardSprite(r, su, DECK_POS)
                sp.tx, sp.ty = cx - 70 + j * 50, cy - CH / 2
                self.my_sprites.append(sp)
            self.app.sfx("card")
        while len(self.board) < len(s["board"]):
            r, su = s["board"][len(self.board)]
            sp = CardSprite(r, su, DECK_POS)
            sp.tx, sp.ty = 640 - 2 * 98 - 45 + len(self.board) * 98, BOARD_Y
            self.board.append(sp)
            self.app.sfx("card")
        if self.your_turn and not was_turn:
            self.app.sfx("chip")

    def cash(self, amount, why):
        self.app.balance += amount
        self.sitting = 0
        if why:
            self.message = why + (f" - {money(amount)} RETURNED" if amount else "")

    def result(self, total, won, best):
        self.app.record("poker", total, won)
        if won > total:
            self.app.float_text(f"+{money(won - total)}", (80, 230, 110))
            self.app.sfx("win")
            if best >= 5:
                self.app.unlock("flush_cash")
        elif won < total:
            self.app.sfx("lose")

    def pot(self):
        return sum(s.total for s in self.seats if s)

    def seated(self):
        return self.my is not None or self.drawing

    def in_hand(self):
        return self.my is not None and self.phase == "hand" and self.you.live

    def outstanding_bets(self):
        if self.my is not None and self.state:
            return self.state["seats"][self.my]["stack"]
        return self.sitting

    def sit(self):
        if self.my is not None or self.sitting or not self.app.net:
            return
        if self.buyin <= 0 or self.buyin < min(BUYIN_MIN, self.app.balance):
            self.message = f"MINIMUM BUY-IN IS {money(BUYIN_MIN)}"
            return
        self.app.balance -= self.buyin
        self.sitting, self.buyin = self.buyin, 0
        self.app.net.send({"t": "poker_sit", "buyin": self.sitting})
        self.message = "TAKING YOUR SEAT..."
        self.app.sfx("chip")

    def start_hand(self):                  # the NEXT HAND button: tell the table we're ready
        if self.app.net and not self.ready_sent:
            self.ready_sent = True
            self.app.net.send({"t": "poker_ready"})

    def act(self, i, kind, raise_to=0):
        if self.app.net and self.your_turn:
            self.app.net.send({"t": "poker_act", "kind": kind, "to": int(raise_to)})
            self.your_turn = False

    def leave(self):
        if self.my is None and self.sitting:            # still waiting for a seat
            self.app.balance += self.sitting
            self.sitting = 0
            if self.app.net:
                self.app.net.send({"t": "poker_stand"})
        if self.my is not None and not self.in_hand():
            self.app.balance += self.outstanding_bets()
            if self.app.net:
                self.app.net.send({"t": "poker_stand"})
            self.stood = True
            self.my = None
            self.phase = "idle"
            self.message = "YOU STOOD UP - YOUR CHIPS ARE BACK"
        self.buyin = 0

    def lost(self):
        """The connection dropped: take back the chips we had at the table."""
        self.app.balance += self.outstanding_bets()
        self.reset_view()

    def update(self, dt):
        self.t += dt
        for sp in self.board + self.my_sprites:
            sp.update(dt)

    def draw_seat(self, surf, i, s):
        if s is None and not (i == 0 and not self.seated()):
            px, py = SEAT_PANEL[i]
            rect = pygame.Rect(0, 0, 196, 54)
            rect.center = (px, py)
            p = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(p, (0, 0, 0, 110), p.get_rect(), border_radius=27)
            surf.blit(p, rect)
            pygame.draw.rect(surf, (90, 80, 60), rect, width=2, border_radius=27)
            draw_text(surf, "OPEN SEAT", font(15, bold=True), (150, 140, 120), rect.center)
            return
        self.drawing = s is not None            # show the computer player sitting where you'd sit
        super().draw_seat(surf, i, s)
        self.drawing = False

    def draw(self, surf):
        super().draw(surf)
        if self.state and self.state.get("to_act", -1) >= 0 and self.state["phase"] == "hand":
            i = self.to_act
            px, py = SEAT_PANEL[i]
            left = max(0, math.ceil(self.state.get("turn", 0)))
            draw_pill(surf, f"{left}s", font(13, bold=True), (px + 118, py), WHITE,
                      (150, 40, 40, 220) if left <= 10 else (20, 20, 28, 220), pad=(7, 2))
        tag = "LIVE TABLE  -  PLAYERS REPLACE THE COMPUTERS" if self.app.net else "NOT CONNECTED"
        draw_text(surf, tag, font(12, bold=True), (150, 220, 170), (640, 50))


# ---- multiplayer blackjack ---------------------------------------------------
class NetBlackjack(StakeGame):
    key = "netbj"

    def __init__(self, app):
        super().__init__(app)
        self.bg = make_table_bg(app.assets)
        self.btn_deal = Button((965, 646, 270, 62), "PLACE BET", (25, 120, 60), 24, "SPACE")
        self.btn_hit = Button((700, 648, 170, 58), "HIT", (25, 120, 60), 24, "H")
        self.btn_stand = Button((880, 648, 170, 58), "STAND", (150, 35, 40), 24, "S")
        self.btn_double = Button((1060, 648, 180, 58), "DOUBLE", (170, 120, 20), 22, "D")
        self.reset_view()

    def reset_view(self):
        self.state = None
        self.my = None
        self.in_play = 0
        self.sprites = {}
        self.round = -1
        self.sat = False
        self.result_text = ""
        self.t = 0.0
        self.message = "JOINING THE TABLE..."

    def on_enter(self):
        if self.app.net and not self.sat:
            self.app.net.send({"t": "bj_sit"})
            self.sat = True

    def on_state(self, s):
        if not s or "seats" not in s:
            return
        me = self.app.net.id if self.app.net else None
        self.state = s
        self.my = next((i for i, x in enumerate(s["seats"]) if x and x["pid"] == me), None)
        if s["round"] != self.round and s["phase"] != "betting":
            self.round = s["round"]
            self.sprites = {}
        if s["phase"] == "betting" and not any(x and x["cards"] for x in s["seats"]):
            self.sprites = {}
        # cards fly out of the shoe as they appear
        for i, x in enumerate(s["seats"]):
            for k, c in enumerate(x["cards"] if x else []):
                key = (i, k, x["pid"])
                if key not in self.sprites:
                    self.sprites[key] = CardSprite(c[0], c[1], SHOE_POS)
                    self.app.sfx("card")
        for k, c in enumerate(s["dealer"]):
            key = ("d", k)
            sp = self.sprites.get(key)
            if sp is None:
                sp = CardSprite(c[0], c[1], SHOE_POS) if c else CardSprite("A", "S", SHOE_POS, face_up=False)
                self.sprites[key] = sp
                self.app.sfx("card")
            elif c and not sp.face_up and sp.flip_t is None:
                sp.rank, sp.suit = c
                sp.reveal()
        if self.my is not None:
            txt = s["msg"]
            if s["phase"] == "betting":
                mine = s["seats"][self.my]["bet"]
                txt = (f"BET {money(mine)} PLACED - WAITING FOR THE OTHERS ({max(0, math.ceil(s['timer']))})" if mine
                       else "SET YOUR BET WITH THE CHIPS, THEN PRESS PLACE BET")
            elif s["phase"] == "playing" and s["to_act"] == self.my:
                txt = f"YOUR TURN  ({max(0, math.ceil(s['turn']))}s)"
            elif s["phase"] == "playing" and s["to_act"] >= 0:
                txt = f"{s['seats'][s['to_act']]['name'].upper()}'S TURN"
            if s["phase"] == "betting":
                self.result_text = ""
            self.message = self.result_text if s["phase"] == "result" and self.result_text else txt

    def on_msg(self, m):
        t = m.get("t")
        if t == "bj_result":
            stake, ret = net_int(m.get("stake")), net_int(m.get("returned"))
            self.in_play = 0
            self.app.balance += ret
            self.app.record("blackjack", stake, ret)
            res = m.get("result", "")
            if res == "BLACKJACK!":
                self.app.unlock("bj_natural")
            if ret > stake and net_int(m.get("cards")) >= 5:
                self.app.unlock("five_card")
            if ret > stake:
                self.message = f"{res} YOU WIN {money(ret - stake)}!"
                self.app.float_text(f"+{money(ret - stake)}", (80, 230, 110))
                self.app.sfx("win")
            elif ret < stake:
                self.message = f"YOU LOSE {money(stake - ret)}"
                self.app.float_text(f"-{money(stake - ret)}", (240, 90, 90))
                self.app.sfx("lose")
            else:
                self.message = "PUSH - YOUR BET IS RETURNED"
            self.result_text = self.message
            self.app.save()
        elif t == "bj_refund":
            amt = net_int(m.get("amount"))
            self.app.balance += amt
            self.in_play = max(0, self.in_play - amt)
        elif t == "bj_full":
            self.message = "THE TABLE IS FULL (4 PLAYERS) - TRY AGAIN SOON"
            self.sat = False

    def my_seat(self):
        return self.state["seats"][self.my] if self.state and self.my is not None else None

    def my_turn(self):
        return bool(self.state) and self.my is not None and self.state["phase"] == "playing" and \
            self.state["to_act"] == self.my

    def busy(self):
        return self.in_play > 0

    def can_leave(self):
        return self.in_play == 0

    def outstanding_bets(self):
        return self.in_play

    def leave(self):
        if self.app.net and self.sat:
            self.app.net.send({"t": "bj_leave"})
        self.sat = False
        self.sprites = {}

    def lost(self):
        self.app.balance += self.in_play
        self.reset_view()

    def place(self):
        s = self.my_seat()
        if not s or self.state["phase"] != "betting" or s["bet"] or self.in_play:
            return
        stake = self.take_bet()
        if stake:
            self.in_play = stake
            self.app.net.send({"t": "bj_bet", "amount": stake})
            self.app.sfx("chip")

    def action(self, kind):
        if not self.my_turn():
            return
        s = self.my_seat()
        if kind == "double":
            if len(s["cards"]) != 2 or self.app.balance < s["bet"]:
                return
            self.app.balance -= s["bet"]
            self.in_play += s["bet"]
        self.app.net.send({"t": "bj_act", "kind": kind})
        self.app.sfx("chip" if kind == "double" else "card")

    def handle(self, e):
        if not self.app.net:
            return
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_SPACE, pygame.K_RETURN):
                self.place()
            elif e.key == pygame.K_h:
                self.action("hit")
            elif e.key == pygame.K_s:
                self.action("stand")
            elif e.key == pygame.K_d:
                self.action("double")
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.my_turn():
                for b, kind in ((self.btn_hit, "hit"), (self.btn_stand, "stand"), (self.btn_double, "double")):
                    if b.clicked(e.pos):
                        self.action(kind)
                return
            if self.betting_open():
                if self.chip_click(e.pos):
                    return
                if self.btn_clear.clicked(e.pos):
                    self.bet = 0
                elif self.btn_deal.clicked(e.pos):
                    self.place()

    def betting_open(self):
        s = self.my_seat()
        return bool(s) and self.state["phase"] == "betting" and not s["bet"] and not self.in_play

    def layout(self):
        """Where every card on the table should be."""
        s = self.state
        if not s:
            return
        n = len(s["dealer"])
        left = W / 2 - (CW + 40 * (n - 1)) / 2
        for k in range(n):
            sp = self.sprites.get(("d", k))
            if sp:
                sp.tx, sp.ty = left + k * 40, DEALER_Y
        for i, x in enumerate(s["seats"]):
            if not x:
                continue
            w = CW + 26 * (len(x["cards"]) - 1)
            for k in range(len(x["cards"])):
                sp = self.sprites.get((i, k, x["pid"]))
                if sp:
                    sp.tx, sp.ty = BJ_SPOTS[i] - w / 2 + k * 26, 300 - k * 6

    def update(self, dt):
        self.t += dt
        if self.app.net and not self.sat:
            self.on_enter()
        self.layout()
        for sp in self.sprites.values():
            sp.update(dt)

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        s = self.state
        if s:
            # dealer
            for k in range(len(s["dealer"])):
                sp = self.sprites.get(("d", k))
                if sp:
                    sp.draw(surf, self.assets)
            if s["dtotal"]:
                draw_pill(surf, f"DEALER  {s['dtotal']}", font(16, bold=True), (W / 2, DEALER_Y + CH + 22), WHITE,
                          (0, 0, 0, 180), GOLD_DARK, pad=(12, 3))
            # players
            for i, x in enumerate(s["seats"]):
                cx = BJ_SPOTS[i]
                spot = pygame.Rect(0, 0, 250, 64)
                spot.center = (cx, 548)
                mine = i == self.my
                turn = s["phase"] == "playing" and s["to_act"] == i
                if not x:
                    pygame.draw.ellipse(surf, (40, 110, 70), (cx - 50, 380, 100, 60), 2)
                    draw_text(surf, "OPEN SEAT", font(14, bold=True), (150, 200, 160), (cx, 410))
                    continue
                pygame.draw.ellipse(surf, GOLD if mine else (200, 190, 150), (cx - 50, 380, 100, 60), 2)
                if x["bet"]:
                    for k, v in enumerate(chip_breakdown(x["bet"])[:8]):
                        img = self.assets.chip(v, 20)
                        surf.blit(img, img.get_rect(center=(cx, 410 - k * 5)))
                cards = x["cards"]
                for k in range(len(cards)):
                    sp = self.sprites.get((i, k, x["pid"]))
                    if sp:
                        sp.draw(surf, self.assets)
                p = pygame.Surface(spot.size, pygame.SRCALPHA)
                pygame.draw.rect(p, (10, 10, 14, 220), p.get_rect(), border_radius=18)
                surf.blit(p, spot)
                border = lerp_col(GOLD, WHITE, 0.5 + 0.5 * math.sin(self.t * 7)) if turn else (GOLD if mine else GOLD_DARK)
                pygame.draw.rect(surf, border, spot, width=3 if turn or mine else 2, border_radius=18)
                name = "YOU" if mine else x["name"]
                draw_text(surf, name, fit_font(name, 17, 170), GOLD if mine else WHITE, (spot.x + 16, spot.y + 20),
                          anchor="midleft")
                draw_text(surf, f"BET {money(x['bet'])}" if x["bet"] else "no bet", font(14, bold=True),
                          (200, 200, 210), (spot.x + 16, spot.y + 44), anchor="midleft")
                if cards:
                    tot = x["total"]
                    draw_pill(surf, str(tot), font(18, bold=True), (spot.right - 36, spot.y + 20), WHITE,
                              (150, 40, 40, 230) if tot > 21 else (0, 0, 0, 200), GOLD_DARK, pad=(10, 2))
                if x["result"]:
                    good = x["result"] in ("WIN", "BLACKJACK!")
                    draw_pill(surf, x["result"], font(15, bold=True), (cx, 280), WHITE,
                              (30, 140, 60, 235) if good else ((20, 20, 28, 235) if x["result"] == "PUSH"
                                                               else (150, 35, 40, 235)), pad=(12, 3))
                if turn and not mine:
                    draw_text(surf, f"{max(0, math.ceil(s['turn']))}s", font(14, bold=True), GOLD,
                              (spot.right - 36, spot.y + 46))
        if self.message:
            draw_pill(surf, self.message, font(17, bold=True), (640, 612), GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(16, 5))
        if self.my_turn():
            bottom_bar(surf, (10, 8, 12))
            draw_text(surf, "YOUR TURN", font(20, bold=True), GOLD, (360, 677))
            me = self.my_seat()
            self.btn_hit.draw(surf, mouse)
            self.btn_stand.draw(surf, mouse)
            self.btn_double.draw(surf, mouse, len(me["cards"]) == 2 and self.app.balance >= me["bet"])
        elif self.betting_open():
            self.draw_bottom(surf, mouse, self.btn_deal, 0 < self.bet <= self.app.balance)
        else:
            bottom_bar(surf, (10, 8, 12))
            if self.my is None:
                txt = "WAITING FOR A SEAT..." if self.app.net else "NOT CONNECTED"
            elif self.in_play:
                txt = f"YOUR BET: {money(self.in_play)}"
            else:
                txt = "SIT TIGHT - NEXT ROUND SOON"
            draw_text(surf, txt, font(20, bold=True), (220, 220, 200), (W / 2, 677))
        draw_text(surf, "LIVE TABLE  -  PLAY TOGETHER AGAINST THE DEALER", font(12, bold=True), (150, 220, 170),
                  (640, 50))
        self.app.draw_top_bar(surf, "BLACKJACK", lobby=True, lobby_enabled=self.can_leave())


# ---- the multiplayer screen ----------------------------------------------------
SCENE_NAMES = {"menu": "IN THE LOBBY", "netpoker": "POKER TABLE", "netbj": "BLACKJACK TABLE", "online": "MULTIPLAYER",
               "mp_crash": "CRASH PARTY", "mp_highcard": "HIGH CARD SHOWDOWN", "mp_liars": "LIAR'S DICE",
               "mp_bingo": "BINGO NIGHT",
               "work": "AT WORK"}


def scene_name(key):
    if key in SCENE_NAMES:
        return SCENE_NAMES[key]
    return GAME_INFO.get(key, (key.upper(),))[0]


class Online:
    key = "online"

    def __init__(self, app):
        self.app = app
        self.bg = gradient_bg((20, 30, 60), (6, 8, 18))
        self.name = app.player_name
        self.addr = ""
        self.focus = None             # "name" or "addr"
        self.found = {}               # ip -> (host name, players, last seen)
        self.finder = None
        self.error = ""
        self.t = 0.0
        self.name_box = pygame.Rect(90, 170, 440, 52)
        self.addr_box = pygame.Rect(690, 520, 330, 52)
        self.btn_host = Button((90, 250, 440, 70), "HOST A GAME", (25, 120, 60), 26, "FRIENDS ON YOUR WI-FI JOIN YOU")
        self.btn_join_addr = Button((1030, 520, 160, 52), "JOIN", (40, 90, 160), 22)
        self.btn_leave = Button((90, 560, 440, 60), "LEAVE", (150, 35, 40), 24)
        self.join_btns = []

    def can_leave(self):
        return True

    def outstanding_bets(self):
        return 0

    def on_enter(self):
        self.name = self.app.player_name

    def leave(self):
        self.stop_finder()
        self.focus = None

    def start_finder(self):
        if self.finder:
            return
        try:
            f = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            f.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            f.bind((NET_BIND, NET_FIND_PORT))
            f.setblocking(False)
            self.finder = f
        except OSError:
            self.finder = None

    def stop_finder(self):
        if self.finder:
            try:
                self.finder.close()
            except OSError:
                pass
        self.finder = None

    def join(self, addr):
        self.app.player_name = self.name = clean_name(self.name)
        self.error = self.app.join_game(addr) or ""
        if not self.error:
            self.stop_finder()

    def handle(self, e):
        app = self.app
        if e.type == pygame.KEYDOWN and self.focus:
            text = self.name if self.focus == "name" else self.addr
            if e.key == pygame.K_BACKSPACE:
                text = text[:-1]
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self.focus == "addr" and self.addr.strip():
                    self.join(self.addr.strip())
                self.focus = None
                return
            elif e.key == pygame.K_TAB:
                self.focus = "addr" if self.focus == "name" else "name"
                return
            elif e.unicode and e.unicode.isprintable():
                ok = (e.unicode.isalnum() or e.unicode in " _-.") if self.focus == "name" else \
                    (e.unicode.isalnum() or e.unicode in ".-:")
                if ok and len(text) < (NAME_MAX if self.focus == "name" else 40):
                    text += e.unicode
            if self.focus == "name":
                self.name = text
                app.player_name = clean_name(text)
            else:
                self.addr = text
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self.focus = None
            if app.net:
                if self.btn_leave.clicked(e.pos):
                    app.leave_game()
                return
            if self.name_box.collidepoint(e.pos):
                self.focus = "name"
            elif self.addr_box.collidepoint(e.pos):
                self.focus = "addr"
            elif self.btn_host.clicked(e.pos):
                self.app.player_name = self.name = clean_name(self.name)
                self.error = app.host_game() or ""
                if not self.error:
                    self.stop_finder()
            elif self.btn_join_addr.clicked(e.pos, bool(self.addr.strip())):
                self.join(self.addr.strip())
            else:
                for ip, b in self.join_btns:
                    if b.clicked(e.pos):
                        self.join(ip)
                        return

    def update(self, dt):
        self.t += dt
        if self.app.net:
            return
        self.start_finder()
        while self.finder:
            try:
                data, (ip, _) = self.finder.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                self.stop_finder()
                break
            try:
                m = json.loads(data)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("game") == "grand_royale":
                self.found[ip] = (clean_name(m.get("name")), net_int(m.get("players"), 99), time.time())
        now = time.time()
        self.found = {ip: v for ip, v in self.found.items() if now - v[2] < 4}

    def text_box(self, surf, rect, text, focused, placeholder):
        pygame.draw.rect(surf, (6, 8, 16), rect, border_radius=10)
        pygame.draw.rect(surf, GOLD if focused else (90, 100, 140), rect, width=2, border_radius=10)
        shown = text + ("|" if focused and int(self.t * 2) % 2 == 0 else "")
        if text or focused:
            draw_text(surf, shown, font(22, bold=True), WHITE, (rect.x + 16, rect.centery), anchor="midleft")
        else:
            draw_text(surf, placeholder, font(18), (110, 115, 140), (rect.x + 16, rect.centery), anchor="midleft")

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        app = self.app
        net = app.net
        if net:
            left = pygame.Rect(60, 80, 500, 560)
            soft_panel(surf, left, 150, (90, 110, 190))
            if app.net_server:
                draw_text(surf, "YOU'RE HOSTING!", font(28, bold=True), GOLD, (left.centerx, 120))
                draw_text(surf, "Friends on the same Wi-Fi can join by opening", font(15), (210, 215, 235),
                          (left.centerx, 165))
                draw_text(surf, "MULTIPLAYER - your game shows up in their list.", font(15), (210, 215, 235),
                          (left.centerx, 187))
                draw_text(surf, "Or they can type this address:", font(15), (210, 215, 235), (left.centerx, 225))
                draw_pill(surf, app.host_ip, font(34, bold=True), (left.centerx, 275), WHITE, (0, 0, 0, 200), GOLD,
                          pad=(24, 8))
                self.btn_leave.text = "STOP HOSTING"
                self.btn_leave.hint = "EVERYONE GETS DISCONNECTED"
            else:
                draw_text(surf, "CONNECTED!", font(28, bold=True), GOLD, (left.centerx, 120))
                draw_text(surf, f"You joined {net.host_name}'s game", font(18), (210, 215, 235), (left.centerx, 165))
                draw_text(surf, f"at {net.addr}", font(15), (160, 170, 200), (left.centerx, 192))
                self.btn_leave.text = "LEAVE GAME"
                self.btn_leave.hint = None
            tips = ["POKER - friends replace the computer players.", "BLACKJACK - everyone plays at one table.",
                    "Every other game - see who's there, their bets", "and whether they won or lost."]
            for i, tip in enumerate(tips):
                draw_text(surf, tip, font(15, bold=i < 2), (190, 220, 200), (left.x + 40, 350 + i * 28), anchor="midleft")
            self.btn_leave.draw(surf, mouse)
            right = pygame.Rect(600, 80, 620, 560)
            soft_panel(surf, right, 150, (90, 110, 190))
            draw_text(surf, f"PLAYERS IN THIS GAME  ({len(net.players)}/{NET_MAX_PLAYERS})", font(20, bold=True), GOLD,
                      (right.centerx, 116))
            for k, (pid, p) in enumerate(sorted(net.players.items())):
                y = 160 + k * 56
                row = pygame.Rect(right.x + 24, y, right.w - 48, 48)
                pygame.draw.rect(surf, (20, 24, 44), row, border_radius=12)
                me = pid == net.id
                col = PT_COLORS[pid % 4]
                pygame.draw.circle(surf, col, (row.x + 26, row.centery), 16)
                draw_text(surf, p["name"][0].upper(), font(16, bold=True), WHITE, (row.x + 26, row.centery))
                draw_text(surf, p["name"] + ("  (YOU)" if me else ""), font(18, bold=True), GOLD if me else WHITE,
                          (row.x + 54, row.y + 16), anchor="midleft")
                draw_text(surf, scene_name(p["scene"]), font(13), (170, 180, 210), (row.x + 54, row.y + 35),
                          anchor="midleft")
                draw_text(surf, money(p["balance"]), font(18, bold=True), (120, 230, 140), (row.right - 16, row.centery),
                          anchor="midright")
            if len(net.players) < 2:
                draw_text(surf, "Waiting for friends to join...", font(16), (170, 180, 210), (right.centerx, 600))
        else:
            left = pygame.Rect(60, 80, 530, 560)
            soft_panel(surf, left, 150, (90, 110, 190))
            draw_text(surf, "PLAY WITH FRIENDS", font(28, bold=True, serif=True), GOLD, (left.centerx, 116))
            draw_text(surf, "YOUR NAME", font(14, bold=True), (190, 200, 230), (self.name_box.x, 156), anchor="midleft")
            self.text_box(surf, self.name_box, self.name, self.focus == "name", "click to type your name")
            self.btn_host.draw(surf, mouse)
            lines = ["Everyone needs this game on their own computer,", "and you all need to be on the same Wi-Fi.",
                     "", "The first time you host, Windows may ask if Python", "can use the network - click ALLOW",
                     "(private networks only).", "", "No accounts or sign-ups needed."]
            for i, ln in enumerate(lines):
                draw_text(surf, ln, font(15), (190, 200, 225), (left.centerx, 360 + i * 26))
            right = pygame.Rect(620, 80, 600, 560)
            soft_panel(surf, right, 150, (90, 110, 190))
            draw_text(surf, "GAMES ON YOUR WI-FI", font(22, bold=True), GOLD, (right.centerx, 116))
            self.join_btns = []
            for k, (ip, (name, count, _)) in enumerate(sorted(self.found.items())[:5]):
                y = 150 + k * 62
                row = pygame.Rect(right.x + 24, y, right.w - 48, 54)
                pygame.draw.rect(surf, (20, 24, 44), row, border_radius=12)
                draw_text(surf, f"{name}'s game", font(19, bold=True), WHITE, (row.x + 18, row.y + 18), anchor="midleft")
                draw_text(surf, f"{count} player{'s' if count != 1 else ''}  -  {ip}", font(13), (170, 180, 210),
                          (row.x + 18, row.y + 38), anchor="midleft")
                b = Button((row.right - 130, row.y + 7, 118, 40), "JOIN", (25, 120, 60), 20)
                b.draw(surf, mouse)
                self.join_btns.append((ip, b))
            if not self.found:
                dots = "." * (int(self.t * 2) % 4)
                draw_text(surf, "Looking for games" + dots, font(17), (170, 180, 210), (right.centerx, 250))
                draw_text(surf, "When a friend hosts, their game shows up here.", font(14), (140, 150, 180),
                          (right.centerx, 280))
            draw_text(surf, "OR TYPE THE HOST'S ADDRESS", font(14, bold=True), (190, 200, 230), (self.addr_box.x, 505),
                      anchor="midleft")
            self.text_box(surf, self.addr_box, self.addr, self.focus == "addr", "like 192.168.1.23")
            self.btn_join_addr.draw(surf, mouse, bool(self.addr.strip()))
            if self.error:
                draw_pill(surf, self.error, font(15, bold=True), (640, 612), (255, 200, 200), (60, 10, 10, 220),
                          (200, 80, 80), pad=(14, 4))
        app.draw_top_bar(surf, "MULTIPLAYER", lobby=True)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
class Settings:
    key = "settings"

    def __init__(self, app):
        self.app = app
        self.bg = gradient_bg((30, 26, 44), (8, 7, 14))
        self.name = app.player_name
        self.editing = False
        self.confirm_t = 0.0          # delete needs a second click within a few seconds
        self.t = 0.0
        self.note = ""
        self.name_box = pygame.Rect(170, 180, 520, 56)
        self.btn_save_name = Button((710, 180, 180, 56), "SAVE NAME", (25, 120, 60), 20)
        self.btn_sound = Button((170, 330, 260, 56), "", (40, 90, 160), 20)
        self.btn_full = Button((450, 330, 260, 56), "", (40, 90, 160), 20, "F11")
        self.btn_delete = Button((170, 520, 420, 64), "DELETE ALL DATA", (150, 35, 40), 22,
                                 f"RESTART WITH {money(START_BALANCE)}")
        self.btn_logout = Button((690, 180, 180, 56), "LOG OUT", (150, 35, 40), 20)
        self.btn_del_acct = Button((880, 180, 230, 56), "DELETE ACCOUNT", (110, 25, 30), 18)
        self.btn_login = Button((900, 180, 210, 56), "LOG IN", (40, 90, 160), 20, "OR CREATE ACCOUNT")
        self.del_confirm_t = 0.0

    def can_leave(self):
        return True

    def outstanding_bets(self):
        return 0

    def leave(self):
        self.editing = False
        self.confirm_t = 0.0

    def on_enter(self):
        self.name = self.app.player_name

    def account_click(self, pos):
        app = self.app
        if app.session:
            if self.btn_logout.clicked(pos):
                app.log_out()
                return True
            if self.btn_del_acct.clicked(pos, not app.account_flow):
                if self.del_confirm_t > 0:
                    app.start_account_flow("delete", flow_delete(app.session))
                    self.del_confirm_t = 0.0
                else:
                    self.del_confirm_t = 5.0
                    app.sfx("lose")
                return True
        elif self.btn_login.clicked(pos):
            app.scene = "account"
            return True
        return False

    def save_name(self):
        name = clean_name(self.name)
        self.name = name
        self.editing = False
        if name == self.app.player_name:
            self.note = "THAT'S ALREADY YOUR NAME"
            return
        self.app.player_name = name
        if self.app.net:                               # let everyone see the new name right away
            self.app.net.send({"t": "hello", "name": name, "v": NET_VERSION})
        self.app.save()
        self.note = f"NAME SAVED - OTHERS NOW SEE YOU AS {name.upper()}"
        self.app.sfx("chip")

    def handle(self, e):
        if e.type == pygame.KEYDOWN and self.editing:
            if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.save_name()
            elif e.key == pygame.K_BACKSPACE:
                self.name = self.name[:-1]
            elif e.key == pygame.K_ESCAPE:
                self.editing = False
                self.name = self.app.player_name
            elif e.unicode and (e.unicode.isalnum() or e.unicode in " _-.") and len(self.name) < NAME_MAX:
                self.name += e.unicode
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.account_click(e.pos):
                return
            self.editing = self.name_box.collidepoint(e.pos) and not self.app.session
            if self.app.session:
                pass
            elif self.btn_save_name.clicked(e.pos):
                self.save_name()
            elif self.btn_sound.clicked(e.pos):
                self.app.sound_on = not self.app.sound_on
                self.app.save()
                self.app.sfx("chip")
            elif self.btn_full.clicked(e.pos):
                toggle_fullscreen()
            elif self.btn_delete.clicked(e.pos):
                if self.confirm_t > 0:
                    self.app.reset_everything()
                else:
                    self.confirm_t = 5.0
                    self.app.sfx("lose")

    def update(self, dt):
        self.t += dt
        self.confirm_t = max(0.0, self.confirm_t - dt)
        self.del_confirm_t = max(0.0, self.del_confirm_t - dt)

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        panel = pygame.Rect(120, 80, 1040, 540)
        soft_panel(surf, panel, 160, (110, 100, 170))
        if self.app.session:                          # logged in: the account
            app = self.app
            draw_text(surf, "ACCOUNT", font(18, bold=True), GOLD, (170, 130), anchor="midleft")
            draw_text(surf, "Your progress is saved online. Log in with this username anywhere.", font(14),
                      (190, 190, 210), (170, 156), anchor="midleft")
            r = self.name_box.copy()
            r.w = 500
            pygame.draw.rect(surf, (6, 6, 14), r, border_radius=10)
            pygame.draw.rect(surf, (100, 95, 140), r, width=2, border_radius=10)
            nm = draw_text(surf, app.session.name, font(24, bold=True), WHITE, (r.x + 16, r.centery), anchor="midleft")
            if app.session.is_owner:
                draw_pill(surf, "OWNER", font(12, bold=True), (nm.right + 40, r.centery), (40, 25, 0), GOLD, None,
                          pad=(8, 2))
            state = {"saving": "saving...", "offline": "offline", "saved": "saved"}.get(app.cloud_state, "online")
            draw_text(surf, state, font(13, bold=True), (140, 220, 160), (r.right - 14, r.centery), anchor="midright")
            self.btn_logout.draw(surf, mouse)
            if self.del_confirm_t > 0:
                self.btn_del_acct.text, self.btn_del_acct.hint = f"SURE? CLICK ({math.ceil(self.del_confirm_t)})", None
            else:
                self.btn_del_acct.text, self.btn_del_acct.hint = "DELETE ACCOUNT", None
            if app.account_flow and app.account_flow[0] == "delete":
                self.btn_del_acct.text = "DELETING..."
            self.btn_del_acct.draw(surf, mouse, not app.account_flow)
            if app.account_error:
                draw_text(surf, app.account_error, font(13, bold=True), (240, 130, 130), (170, 252), anchor="midleft")
            self.draw_rest(surf, mouse)
            return
        # name
        draw_text(surf, "USERNAME", font(18, bold=True), GOLD, (170, 130), anchor="midleft")
        draw_text(surf, "Playing as a guest - this name is only for multiplayer. Log in to save online.", font(14),
                  (190, 190, 210), (170, 156), anchor="midleft")
        pygame.draw.rect(surf, (6, 6, 14), self.name_box, border_radius=10)
        pygame.draw.rect(surf, GOLD if self.editing else (100, 95, 140), self.name_box, width=2, border_radius=10)
        shown = self.name + ("|" if self.editing and int(self.t * 2) % 2 == 0 else "")
        if shown:
            draw_text(surf, shown, font(24, bold=True), WHITE, (self.name_box.x + 16, self.name_box.centery),
                      anchor="midleft")
        else:
            draw_text(surf, "click to type a name", font(18), (110, 110, 140), (self.name_box.x + 16,
                                                                               self.name_box.centery), anchor="midleft")
        self.btn_save_name.draw(surf, mouse, clean_name(self.name) != self.app.player_name)
        self.btn_login.draw(surf, mouse)
        self.draw_rest(surf, mouse)

    def draw_rest(self, surf, mouse):
        # options
        draw_text(surf, "OPTIONS", font(18, bold=True), GOLD, (170, 300), anchor="midleft")
        self.btn_sound.text = f"SOUND: {'ON' if self.app.sound_on else 'OFF'}"
        self.btn_sound.color = (25, 120, 60) if self.app.sound_on else (90, 90, 100)
        self.btn_sound.draw(surf, mouse)
        self.btn_full.text = "FULLSCREEN"
        self.btn_full.draw(surf, mouse)
        # delete
        pygame.draw.line(surf, (90, 60, 70), (170, 440), (1110, 440), 1)
        draw_text(surf, "DELETE DATA", font(18, bold=True), (240, 110, 110), (170, 470), anchor="midleft")
        if self.app.session:
            what = (f"Wipes all progress on your account and starts over with {money(START_BALANCE)}: chips, stocks, "
                    "stats, trophies, lottery tickets and daily streak. Your account stays.")
        else:
            what = (f"Wipes everything and starts the game over with {money(START_BALANCE)}: chips, stocks, "
                    "stats, trophies, lottery tickets, daily streak and your name.")
        draw_text(surf, what, font(14), (200, 190, 200), (170, 495), anchor="midleft")
        if self.confirm_t > 0:
            self.btn_delete.text = f"CLICK AGAIN TO DELETE ({math.ceil(self.confirm_t)})"
            self.btn_delete.hint = "THIS CAN'T BE UNDONE"
            self.btn_delete.color = (200, 40, 40) if int(self.t * 6) % 2 else (150, 35, 40)
        else:
            self.btn_delete.text = "DELETE ALL DATA"
            self.btn_delete.hint = f"RESTART WITH {money(START_BALANCE)}"
            self.btn_delete.color = (150, 35, 40)
        self.btn_delete.draw(surf, mouse)
        if self.app.net:
            draw_text(surf, "(you'll be disconnected from multiplayer)", font(13), (190, 170, 170), (610, 552),
                      anchor="midleft")
        if self.note:
            draw_pill(surf, self.note, font(15, bold=True), (640, 650), GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(14, 4))
        draw_text(surf, f"Grand Royale Casino  v{VERSION}", font(13, bold=True), (150, 145, 170), (1140, 600),
                  anchor="midright")
        self.app.draw_top_bar(surf, "SETTINGS", lobby=True, show_help=False)


# --------------------------------------------------------------------------
# Main menu - the opening scene, then GAMBLE / QUIT
# --------------------------------------------------------------------------
TITLE_TEXT = "GRAND ROYALE"
TITLE_Y = 262                    # centre line of the big title
TITLE_SLAM = 2.5                 # when the first letter lands
TITLE_LETTER_GAP = 0.07          # seconds between letters
INTRO_END = 4.3                  # everything is in place (a click skips straight here)
FAN_CARDS = [("10", "S"), ("J", "S"), ("Q", "S"), ("K", "S"), ("A", "S")]


def ease_out_back(k):
    c = 1.9
    return 1 + (c + 1) * (k - 1) ** 3 + c * (k - 1) ** 2


class Title:
    key = "title"

    def __init__(self, app):
        self.app = app
        self.t = 0.0
        self.bg = gradient_bg((40, 8, 16), (4, 2, 6))
        self.vignette = self.make_vignette()
        self.letters = self.make_letters()
        self.glow = self.make_glow()
        self.fx = pygame.Surface((W, H), pygame.SRCALPHA)
        self.layer = pygame.Surface((W, H), pygame.SRCALPHA)
        self.chips = []                   # falling / exploding chips
        self.floaters = [self.new_floater(random.uniform(0, H)) for _ in range(14)]
        self.sparks = []
        self.twinkles = []
        self.shake = 0.0
        self.flash = 0.0
        self.played = set()               # sound cues already played
        self.btn_play = Button((W / 2 - 190, 436, 380, 84), "GAMBLE", (25, 120, 60), 38, "ENTER")
        self.btn_quit = Button((W / 2 - 120, 544, 240, 58), "QUIT", (140, 30, 40), 24)
        rng = random.Random(3)
        self.rain = [{"x": rng.uniform(40, W - 40), "delay": rng.uniform(0.9, 2.3), "vy": rng.uniform(520, 820),
                      "spin": rng.uniform(0, 6), "vs": rng.uniform(5, 11), "v": rng.choice(CHIP_VALUES[:9]),
                      "r": rng.randint(16, 26)} for _ in range(34)]

    # ---- pre-drawn pieces --------------------------------------------------
    def make_letters(self):
        f = font(112, bold=True, serif=True)
        out = []
        x = 0
        pieces = []
        for ch in TITLE_TEXT:
            if ch == " ":
                x += 40
                continue
            mask = f.render(ch, True, (255, 255, 255))
            w, h = mask.get_size()
            grad = pygame.Surface((w, h), pygame.SRCALPHA)
            for y in range(h):
                k = y / max(1, h - 1)
                col = lerp_col((255, 244, 190), (255, 196, 60), min(1, k * 1.6)) if k < 0.62 else \
                    lerp_col((255, 196, 60), (170, 110, 20), (k - 0.62) / 0.38)
                pygame.draw.line(grad, col, (0, y), (w, y))
            grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            shadow = f.render(ch, True, (40, 8, 10))
            outline = f.render(ch, True, (90, 50, 10))
            pieces.append((ch, x, grad, shadow, outline, mask))
            x += w - 2
        total = x
        left = W / 2 - total / 2
        for ch, lx, grad, shadow, outline, mask in pieces:
            cx = left + lx + grad.get_width() / 2
            out.append({"cx": cx, "img": grad, "shadow": shadow, "outline": outline, "mask": mask})
        self.title_left, self.title_right = left, left + total
        return out

    @staticmethod
    def make_glow():
        g = pygame.Surface((980, 300), pygame.SRCALPHA)
        for i in range(40):
            k = i / 39
            pygame.draw.ellipse(g, (255, 170, 60, int(5 + 4 * k)),
                                (490 * k, 150 * k, 980 * (1 - k), 300 * (1 - k)))
        return g

    @staticmethod
    def make_vignette():
        v = pygame.Surface((W, H), pygame.SRCALPHA)
        for i in range(60):
            k = i / 59
            pygame.draw.rect(v, (0, 0, 0, int(160 * (1 - k) ** 2)), (i * 4, i * 3, W - i * 8, H - i * 6), width=6)
        return v

    def new_floater(self, y=None):
        return {"x": random.uniform(0, W), "y": H + 40 if y is None else y, "vy": random.uniform(18, 45),
                "spin": random.uniform(0, 6), "vs": random.uniform(0.6, 1.6), "v": random.choice(CHIP_VALUES),
                "r": random.randint(10, 20), "a": random.randint(40, 110)}

    # ---- scene plumbing --------------------------------------------------------
    def can_leave(self):
        return False

    def outstanding_bets(self):
        return 0

    def leave(self):
        pass

    def on_enter(self):
        pass

    def done(self):
        return self.t >= INTRO_END

    def skip(self):
        if not self.done():
            self.t = INTRO_END
            self.played |= {"slam", "boom", "win"}
            for L in self.letters:
                L["landed"] = True

    def handle(self, e):
        if not self.done():
            if e.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
                self.skip()
            return
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            self.gamble()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.btn_play.clicked(e.pos):
                self.gamble()
            elif self.btn_quit.clicked(e.pos) and not WEB:
                self.app.quit()

    def gamble(self):
        self.app.sfx("chip")
        app = self.app
        app.scene = "menu" if (app.session or app.guest) and not app.account_flow else "account"

    # ---- animation -------------------------------------------------------------
    def cue(self, name, at, sound):
        if name not in self.played and self.t >= at:
            self.played.add(name)
            self.app.sfx(sound)
            return True
        return False

    def explode(self):
        cx, cy = W / 2, TITLE_Y
        for _ in range(46):
            a = random.uniform(0, 2 * math.pi)
            v = random.uniform(300, 900)
            self.chips.append({"x": cx + math.cos(a) * 60, "y": cy + math.sin(a) * 30, "vx": math.cos(a) * v,
                               "vy": math.sin(a) * v - 250, "spin": random.uniform(0, 6), "vs": random.uniform(6, 14),
                               "v": random.choice(CHIP_VALUES), "r": random.randint(14, 26)})
        for _ in range(140):
            a = random.uniform(0, 2 * math.pi)
            v = random.uniform(150, 700)
            life = random.uniform(0.6, 1.4)
            self.sparks.append({"x": cx, "y": cy, "vx": math.cos(a) * v, "vy": math.sin(a) * v * 0.6, "life": life,
                                "max": life, "size": random.uniform(3, 7),
                                "col": random.choice([(255, 220, 90), (255, 255, 255), (255, 160, 60)])})
        self.flash = 1.0
        self.shake = 0.35

    def update(self, dt):
        before = self.t
        self.t += dt
        t = self.t
        if before < 0.3 <= t:
            self.app.sfx("launch")
        for k in range(len(FAN_CARDS)):
            self.cue(f"card{k}", 1.2 + k * 0.15, "card")
        for k in range(len(self.letters)):
            if self.cue(f"letter{k}", TITLE_SLAM + k * TITLE_LETTER_GAP + 0.18, "chip"):
                self.shake = max(self.shake, 0.08)
        last = TITLE_SLAM + (len(self.letters) - 1) * TITLE_LETTER_GAP + 0.2
        if self.cue("slam", last, "boom"):
            self.explode()
        self.cue("win", last + 0.35, "win")
        # physics
        for c in self.chips:
            c["x"] += c["vx"] * dt
            c["y"] += c["vy"] * dt
            c["vy"] += 900 * dt
            c["spin"] += c["vs"] * dt
        self.chips = [c for c in self.chips if c["y"] < H + 60]
        for p in self.sparks:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["vy"] += 200 * dt
            p["vx"] *= 1 - dt * 1.2
            p["life"] -= dt
        self.sparks = [p for p in self.sparks if p["life"] > 0]
        for f in self.floaters:
            f["y"] -= f["vy"] * dt
            f["spin"] += f["vs"] * dt
            if f["y"] < -40:
                f.update(self.new_floater())
        if self.done() and random.random() < dt * 6:               # glints on the title
            L = random.choice(self.letters)
            h = L["img"].get_height()
            self.twinkles.append([L["cx"] + random.uniform(-25, 25), TITLE_Y + random.uniform(-h * 0.35, h * 0.3), 0.0])
        for tw in self.twinkles:
            tw[2] += dt
        self.twinkles = [tw for tw in self.twinkles if tw[2] < 0.7]
        self.shake = max(0.0, self.shake - dt)
        self.flash = max(0.0, self.flash - dt * 2.2)

    # ---- drawing -----------------------------------------------------------------
    def draw_chip(self, surf, c, alpha=255):
        w = max(2, int(2 * c["r"] * abs(math.cos(c["spin"]))))
        img = pygame.transform.smoothscale(self.app.assets.chip(c["v"], c["r"]), (w, 2 * c["r"]))
        if alpha < 255:
            img.set_alpha(alpha)
        surf.blit(img, img.get_rect(center=(c["x"], c["y"])))

    def draw_beams(self, surf, strength):
        if strength <= 0:
            return
        self.fx.fill((0, 0, 0, 0))
        for base_x, phase, col in ((W * 0.12, 0.0, (255, 210, 140)), (W * 0.88, 2.2, (255, 190, 120)),
                                   (W * 0.5, 4.1, (210, 170, 255))):
            ang = math.sin(self.t * 0.6 + phase) * 0.45
            tip = (base_x, H + 20)
            length = 900
            for spread, a in ((0.16, 22), (0.09, 26)):
                p1 = (tip[0] + math.sin(ang - spread) * length, tip[1] - math.cos(ang - spread) * length)
                p2 = (tip[0] + math.sin(ang + spread) * length, tip[1] - math.cos(ang + spread) * length)
                pygame.draw.polygon(self.fx, (*col, int(a * strength)), [tip, p1, p2])
        surf.blit(self.fx, (0, 0))

    def draw_rays(self, surf, strength):
        if strength <= 0:
            return
        self.fx.fill((0, 0, 0, 0))
        cx, cy = W / 2, TITLE_Y
        n = 14
        for i in range(n):
            a = self.t * 0.12 + i * 2 * math.pi / n
            pts = [(cx, cy), (cx + math.cos(a) * 1400, cy + math.sin(a) * 1400),
                   (cx + math.cos(a + 0.12) * 1400, cy + math.sin(a + 0.12) * 1400)]
            pygame.draw.polygon(self.fx, (255, 200, 110, int(16 * strength)), pts)
        surf.blit(self.fx, (0, 0))

    def draw_cards(self, surf):
        faces = self.app.assets.faces
        for k, card in enumerate(FAN_CARDS):
            start = 1.2 + k * 0.15
            if self.t < start:
                continue
            p = min(1.0, (self.t - start) / 0.6)
            e = 1 - (1 - p) ** 3
            ang_end = (k - 2) * 13
            tx, ty = W / 2 + (k - 2) * 58, TITLE_Y - 118 + abs(k - 2) * 12
            sx, sy = (-200, 820) if k % 2 == 0 else (W + 200, 820)
            x, y = sx + (tx - sx) * e, sy + (ty - sy) * e - math.sin(p * math.pi) * 180
            bob = math.sin(self.t * 1.4 + k) * 3 if p >= 1 else 0
            ang = ang_end + (1 - e) * (720 if k % 2 == 0 else -720)
            img = pygame.transform.rotozoom(faces[card], -ang, 0.9)
            surf.blit(img, img.get_rect(center=(x, y + bob)))

    def draw_title(self, surf):
        glow_k = min(1.0, max(0.0, (self.t - TITLE_SLAM) / 0.8))
        if glow_k > 0:
            g = self.glow.copy()
            g.set_alpha(int(255 * glow_k * (0.85 + 0.15 * math.sin(self.t * 2))))
            surf.blit(g, g.get_rect(center=(W / 2, TITLE_Y)))
        for k, L in enumerate(self.letters):
            start = TITLE_SLAM + k * TITLE_LETTER_GAP
            if self.t < start:
                continue
            p = min(1.0, (self.t - start) / 0.22)
            scale = 3.2 - 2.2 * p if p < 1 else 1.0
            alpha = int(255 * min(1.0, p * 2))
            y = TITLE_Y + (1 - p) * -40
            for img, off in ((L["shadow"], (6, 7)), (L["outline"], (2, 2)), (L["img"], (0, 0))):
                im = img if scale == 1.0 else pygame.transform.rotozoom(img, 0, scale)
                if alpha < 255:
                    im = im.copy()
                    im.set_alpha(alpha)
                surf.blit(im, im.get_rect(center=(L["cx"] + off[0], y + off[1])))
        # a shine sweeping across the title every few seconds
        if self.done():
            sweep = (self.t - INTRO_END) % 4.0
            if sweep < 1.2:
                x = self.title_left - 120 + (self.title_right - self.title_left + 240) * (sweep / 1.2)
                for L in self.letters:
                    d = abs(L["cx"] - x)
                    if d < 70:
                        m = L["mask"].copy()
                        m.fill((255, 255, 255, int(150 * (1 - d / 70))), special_flags=pygame.BLEND_RGBA_MULT)
                        surf.blit(m, m.get_rect(center=(L["cx"], TITLE_Y)), special_flags=pygame.BLEND_RGBA_ADD)
        for x, y, age in self.twinkles:
            s = 9 * math.sin(age / 0.7 * math.pi)
            col = (255, 250, 220)
            pygame.draw.polygon(surf, col, [(x, y - s * 2), (x + s * 0.35, y - s * 0.35), (x + s * 2, y),
                                            (x + s * 0.35, y + s * 0.35), (x, y + s * 2), (x - s * 0.35, y + s * 0.35),
                                            (x - s * 2, y), (x - s * 0.35, y - s * 0.35)])
        # CASINO, letter by letter
        sub_t = TITLE_SLAM + len(self.letters) * TITLE_LETTER_GAP + 0.35
        if self.t >= sub_t:
            word = "C  A  S  I  N  O"
            n = min(len(word), int((self.t - sub_t) / 0.035) + 1)
            f = font(30, bold=True)
            full = f.size(word)[0]
            draw_text(surf, word[:n], f, (235, 225, 200), (W / 2 - full / 2, TITLE_Y + 88), anchor="midleft")
            line_k = min(1.0, (self.t - sub_t) / 0.5)
            for side in (-1, 1):
                x0 = W / 2 + side * (full / 2 + 24)
                pygame.draw.line(surf, GOLD, (x0, TITLE_Y + 88), (x0 + side * 150 * line_k, TITLE_Y + 88), 2)
                pygame.draw.circle(surf, GOLD, (x0 + side * 150 * line_k, TITLE_Y + 88), 4)

    def draw(self, surf):
        t = self.t
        mouse = pygame.mouse.get_pos()
        ox = oy = 0
        if self.shake:
            ox, oy = random.randint(-9, 9), random.randint(-7, 7)
        canvas = surf
        canvas.fill((0, 0, 0))
        bg_k = min(1.0, max(0.0, (t - 0.6) / 1.2))
        if bg_k > 0:
            b = self.bg
            if bg_k < 1:
                b = self.bg.copy()
                b.set_alpha(int(255 * bg_k))
            canvas.blit(b, (0, 0))
        self.draw_rays(canvas, min(1.0, max(0.0, (t - TITLE_SLAM) / 1.0)))
        self.draw_beams(canvas, bg_k)
        if bg_k >= 1:
            self.app.themefx.draw_backdrop(canvas)
        for f in self.floaters:
            self.draw_chip(canvas, f, int(f["a"] * bg_k))
        for c in self.rain:                               # the opening chip shower
            ct = t - c["delay"]
            if 0 <= ct and t < INTRO_END + 1:
                y = -40 + c["vy"] * ct
                if y < H + 40:
                    self.draw_chip(canvas, {"x": c["x"], "y": y, "r": c["r"], "v": c["v"],
                                            "spin": c["spin"] + c["vs"] * ct})
        # everything that shakes goes on a layer
        layer = self.layer
        layer.fill((0, 0, 0, 0))
        self.draw_cards(layer)
        self.draw_title(layer)
        canvas.blit(layer, (ox, oy))
        for c in self.chips:
            self.draw_chip(canvas, c)
        if self.sparks:
            self.fx.fill((0, 0, 0, 0))
            for p in self.sparks:
                k = p["life"] / p["max"]
                s, x, y = p["size"] * (0.5 + k * 0.5), p["x"], p["y"]
                pygame.draw.polygon(self.fx, (*p["col"], int(255 * k)),
                                    [(x, y - s * 2), (x + s * 0.4, y - s * 0.4), (x + s * 2, y), (x + s * 0.4, y + s * 0.4),
                                     (x, y + s * 2), (x - s * 0.4, y + s * 0.4), (x - s * 2, y), (x - s * 0.4, y - s * 0.4)])
            canvas.blit(self.fx, (0, 0))
        # opening line
        if t < 1.8:
            a = min(1.0, max(0.0, (t - 0.3) / 0.5)) * min(1.0, max(0.0, (1.7 - t) / 0.4))
            img = font(22, serif=True).render("a just-for-fun casino", True, (220, 200, 160))
            img.set_alpha(int(255 * a))
            canvas.blit(img, img.get_rect(center=(W / 2, H / 2)))
        # buttons slide up once the title is in
        btn_k = min(1.0, max(0.0, (t - (INTRO_END - 0.6)) / 0.6))
        if btn_k > 0:
            e = ease_out_back(btn_k)
            for b, y0 in ((self.btn_play, 436), (self.btn_quit, 544)):
                b.rect.y = int(y0 + (1 - e) * 260)
            if self.done():
                pulse = 0.5 + 0.5 * math.sin(t * 3)
                g = pygame.Surface((self.btn_play.rect.w + 40, self.btn_play.rect.h + 40), pygame.SRCALPHA)
                pygame.draw.rect(g, (120, 255, 150, int(40 + 50 * pulse)), g.get_rect(), border_radius=34)
                canvas.blit(g, (self.btn_play.rect.x - 20, self.btn_play.rect.y - 20))
            self.btn_play.draw(canvas, mouse, self.done())
            if not WEB:
                self.btn_quit.draw(canvas, mouse, self.done())
            info_a = int(255 * btn_k)
            who = (f"{self.app.session.name.upper()}'S CHIPS" if self.app.session else
                   "LOGGING IN..." if self.app.account_flow else "YOUR CHIPS")
            img = font(16, bold=True).render(f"{who}: {money(self.app.balance)}", True, GOLD)
            img.set_alpha(info_a)
            canvas.blit(img, img.get_rect(center=(W / 2, 630)))
            img = font(12).render("Play money only - chips have no cash value and can't be exchanged for real money.",
                                  True, (150, 140, 130))
            img.set_alpha(info_a)
            canvas.blit(img, img.get_rect(center=(W / 2, 700)))
            self.app.draw_version(canvas)
        if not self.done():
            hint = font(12).render("click to skip", True, (120, 110, 100))
            canvas.blit(hint, hint.get_rect(bottomright=(W - 14, H - 10)))
        canvas.blit(self.vignette, (0, 0))
        if self.flash:
            f = pygame.Surface((W, H))
            f.fill((255, 245, 220))
            f.set_alpha(int(220 * self.flash))
            canvas.blit(f, (0, 0))


# --------------------------------------------------------------------------
# Chip Pusher - like the coin pushers at the arcade, but with casino chips
# --------------------------------------------------------------------------
CP_R = 20                         # chip radius on the table
CP_LEFT, CP_RIGHT = 330, 950      # table edges
CP_BACK, CP_FRONT = 96, 556       # the pusher's wall, and the edge you win chips over
CP_PUSH_MIN, CP_PUSH_AMP = 40, 112  # how far in front of the back wall the pusher's face goes
CP_PERIOD = 3.2                   # seconds for the pusher to go out and back
CP_DRAIN = 280                    # the front this-many pixels of each side are open drains (chips lost) - tuned so it pays back ~93%
CP_START = 110                    # chips on the table when the machine is new
CP_GOLD_MULT = 5
CP_BONUS = [("gold", 0.04), ("shower", 0.015), ("walls", 0.02)]   # chance per drop
CP_WALL_TIME = 15.0
CP_DROP_GAP = 0.22                # fastest you can drop


class Pusher(StakeGame):
    key = "pusher"

    def __init__(self, app, state=None):
        super().__init__(app, ((20, 12, 8), (110, 80, 40)))
        self.bg = self.make_bg()
        self.t = 0.0
        self.phase = 0.0
        self.chips = []             # [x, y, value, gold]
        self.falling = []           # chips going over the front edge or into a drain: [x, y, v, gold, t, won]
        self.aim = (CP_LEFT + CP_RIGHT) / 2
        self.drop_cd = 0.0
        self.holding = False
        self.walls = 0.0            # seconds the side walls stay up
        self.flash = []             # [text, colour, time]
        self.session_in = self.session_out = 0
        self.pending_in = self.pending_out = 0
        self.record_t = 0.0
        self.win_glow = 0.0
        self.message = "AIM WITH THE MOUSE, CLICK (OR HOLD) TO DROP CHIPS"
        self.btn_drop = Button((965, 646, 270, 62), "DROP", (25, 120, 60), 26, "CLICK THE TABLE / SPACE")
        state = state or {}
        try:
            self.chips = [[float(x), float(y), int(v), bool(g)] for x, y, v, g in state.get("chips", [])]
        except (TypeError, ValueError):
            self.chips = []
        if not state:
            self.fill()

    # ---- machine ---------------------------------------------------------------
    def fill(self):
        """A new machine comes loaded with $10 chips, packed in staggered rows."""
        rng = random.Random(11)
        self.chips = []
        top = CP_BACK + CP_PUSH_MIN + CP_PUSH_AMP + CP_R + 2
        row = 0
        y = top
        while y < CP_FRONT - CP_R * 2.2:
            x = CP_LEFT + CP_R + 6 + (row % 2) * (CP_R + 1)
            while x < CP_RIGHT - CP_R - 6:
                if rng.random() < 0.9:
                    self.chips.append([x + rng.uniform(-2, 2), y + rng.uniform(-2, 2), 10, False])
                x += 2 * CP_R + 3
            y += 2 * CP_R * 0.9 + 1
            row += 1

    def to_state(self):
        return {"chips": [[round(x, 1), round(y, 1), v, g] for x, y, v, g in self.chips]}

    def face(self):
        """Where the pusher's front edge is right now."""
        return CP_BACK + CP_PUSH_MIN + CP_PUSH_AMP * (0.5 - 0.5 * math.cos(2 * math.pi * self.phase / CP_PERIOD))

    def can_leave(self):
        return True

    def busy(self):
        return False

    def outstanding_bets(self):
        return 0

    def leave(self):
        self.flush_stats()
        self.holding = False

    def flush_stats(self):
        if self.pending_in or self.pending_out:
            self.app.record(self.key, self.pending_in, self.pending_out)
            self.pending_in = self.pending_out = 0

    # ---- dropping ----------------------------------------------------------------
    def drop(self):
        if self.drop_cd > 0:
            return
        stake = self.take_bet()
        if not stake:
            self.holding = False
            return
        self.drop_cd = CP_DROP_GAP
        self.session_in += stake
        self.pending_in += stake
        x = max(CP_LEFT + CP_R, min(CP_RIGHT - CP_R, self.aim + random.uniform(-6, 6)))
        self.chips.append([x, self.face() + CP_R + 2, stake, False])
        self.app.sfx("chip")
        r = random.random()
        acc = 0.0
        for kind, chance in CP_BONUS:
            acc += chance
            if r < acc:
                self.bonus(kind, stake)
                break

    def bonus(self, kind, stake):
        if kind == "gold":
            x = random.uniform(CP_LEFT + 60, CP_RIGHT - 60)
            self.chips.append([x, self.face() + CP_R + 2, stake * CP_GOLD_MULT, True])
            self.say(f"GOLD CHIP!  WORTH {money(stake * CP_GOLD_MULT)}", GOLD)
        elif kind == "shower":
            for i in range(8):
                x = CP_LEFT + 50 + i * (CP_RIGHT - CP_LEFT - 100) / 7
                self.chips.append([x + random.uniform(-8, 8), self.face() + CP_R + 2 + random.uniform(0, 30), stake, False])
            self.say("CHIP SHOWER!  8 FREE CHIPS", (120, 220, 255))
        else:
            self.walls = CP_WALL_TIME
            self.say("SIDE WALLS UP!  NO CHIPS LOST FOR 15s", (140, 240, 150))
        self.app.sfx("win")

    def say(self, text, col):
        self.flash.append([text, col, 0.0])

    def handle(self, e):
        if e.type == pygame.MOUSEMOTION:
            self.aim = max(CP_LEFT + CP_R, min(CP_RIGHT - CP_R, e.pos[0]))
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.chip_click(e.pos):
                return
            if self.btn_clear.clicked(e.pos):
                self.bet = 0
            elif self.btn_drop.clicked(e.pos) or (CP_LEFT - 20 < e.pos[0] < CP_RIGHT + 20 and 60 < e.pos[1] < CP_FRONT):
                if e.pos[1] < CP_FRONT:
                    self.aim = max(CP_LEFT + CP_R, min(CP_RIGHT - CP_R, e.pos[0]))
                self.holding = True
                self.drop()
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self.holding = False
        elif e.type == pygame.KEYDOWN and e.key == pygame.K_SPACE:
            self.holding = True
            self.drop()
        elif e.type == pygame.KEYUP and e.key == pygame.K_SPACE:
            self.holding = False
        elif e.type == pygame.KEYDOWN and e.key in (pygame.K_LEFT, pygame.K_a):
            self.aim = max(CP_LEFT + CP_R, self.aim - 30)
        elif e.type == pygame.KEYDOWN and e.key in (pygame.K_RIGHT, pygame.K_d):
            self.aim = min(CP_RIGHT - CP_R, self.aim + 30)

    # ---- physics -------------------------------------------------------------------
    def solve(self):
        """Push chips out of the pusher and out of each other, then see what went over an edge."""
        face = self.face()
        chips = self.chips
        walls = self.walls > 0
        d2 = (2 * CP_R) ** 2
        for _ in range(4):
            for c in chips:
                if c[1] - CP_R < face:
                    c[1] = face + CP_R
            grid = {}
            for i, c in enumerate(chips):
                grid.setdefault((int(c[0] // (2 * CP_R)), int(c[1] // (2 * CP_R))), []).append(i)
            for (gx, gy), members in grid.items():
                near = []
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        near.extend(grid.get((gx + dx, gy + dy), ()))
                for i in members:
                    a = chips[i]
                    for j in near:
                        if j <= i:
                            continue
                        b = chips[j]
                        dx, dy = b[0] - a[0], b[1] - a[1]
                        dist2 = dx * dx + dy * dy
                        if dist2 >= d2:
                            continue
                        dist = math.sqrt(dist2) or 0.01
                        push = 2 * CP_R - dist
                        nx, ny = (dx / dist, dy / dist) if dist2 else (random.uniform(-1, 1), 1.0)
                        a_stuck = a[1] - CP_R <= face + 0.5
                        b_stuck = b[1] - CP_R <= face + 0.5
                        if a_stuck and not b_stuck:
                            ka, kb = 0.0, 1.0
                        elif b_stuck and not a_stuck:
                            ka, kb = 1.0, 0.0
                        else:
                            ka = kb = 0.5
                        a[0] -= nx * push * ka
                        a[1] -= ny * push * ka
                        b[0] += nx * push * kb
                        b[1] += ny * push * kb
            for c in chips:
                open_side = c[1] > CP_FRONT - CP_DRAIN and not walls
                if not open_side:
                    c[0] = max(CP_LEFT + CP_R, min(CP_RIGHT - CP_R, c[0]))
                if c[1] - CP_R < CP_BACK:
                    c[1] = CP_BACK + CP_R
        # over the edge?
        keep = []
        for c in chips:
            if c[1] > CP_FRONT:
                self.falling.append([c[0], c[1], c[2], c[3], 0.0, True])
            elif c[0] < CP_LEFT or c[0] > CP_RIGHT:
                self.falling.append([c[0], c[1], c[2], c[3], 0.0, False])
            else:
                keep.append(c)
        self.chips = keep

    def update(self, dt, draw_fx=True):
        self.t += dt
        self.phase += dt
        self.drop_cd = max(0.0, self.drop_cd - dt)
        self.walls = max(0.0, self.walls - dt)
        self.win_glow = max(0.0, self.win_glow - dt * 2)
        if self.holding:
            self.drop()
        self.solve()
        total = 0
        for f in self.falling:
            if f[4] == 0.0 and f[5]:              # just went over the front edge: it's yours
                total += f[2]
            f[4] += dt
        if total:
            self.app.balance += total
            self.session_out += total
            self.pending_out += total
            self.win_glow = 1.0
            if draw_fx:
                self.app.sfx("chip")
                self.app.float_text(f"+{money(total)}", (80, 230, 110))
        self.falling = [f for f in self.falling if f[4] < 0.6]
        for fl in self.flash:
            fl[2] += dt
        self.flash = [fl for fl in self.flash if fl[2] < 2.5]
        self.record_t += dt
        if self.record_t > 10:
            self.record_t = 0.0
            self.flush_stats()

    # ---- drawing ---------------------------------------------------------------------
    def make_bg(self):
        s = make_wood()
        cab = pygame.Rect(CP_LEFT - 50, 64, CP_RIGHT - CP_LEFT + 100, 560)
        pygame.draw.rect(s, (18, 10, 8), cab.move(0, 8), border_radius=30)
        pygame.draw.rect(s, (60, 20, 30), cab, border_radius=30)
        pygame.draw.rect(s, (140, 40, 60), cab, width=4, border_radius=30)
        field = pygame.Rect(CP_LEFT, CP_BACK, CP_RIGHT - CP_LEFT, CP_FRONT - CP_BACK)
        for y in range(field.h):
            k = y / field.h
            pygame.draw.line(s, lerp_col((20, 70, 110), (40, 120, 170), k), (field.x, field.y + y), (field.right, field.y + y))
        for x in range(field.x + 40, field.right, 40):
            pygame.draw.line(s, (50, 110, 160), (x, field.y), (x, field.bottom), 1)
        # front edge + the win tray under it
        pygame.draw.rect(s, (230, 190, 60), (CP_LEFT, CP_FRONT - 4, field.w, 8))
        tray = pygame.Rect(CP_LEFT - 10, CP_FRONT + 10, field.w + 20, 50)
        pygame.draw.rect(s, (15, 10, 10), tray, border_radius=12)
        pygame.draw.rect(s, (230, 190, 60), tray, width=2, border_radius=12)
        # side drains
        for x in (CP_LEFT - 40, CP_RIGHT):
            pygame.draw.rect(s, (8, 6, 8), (x, CP_FRONT - CP_DRAIN, 40, CP_DRAIN), border_radius=8)
        return s

    def draw_chip(self, surf, x, y, v, gold, scale=1.0, alpha=255):
        r = max(3, int(CP_R * scale))
        if gold:                                      # bonus chips are shiny gold coins
            key = ("gold", r, v)
            cache = self.__dict__.setdefault("gold_imgs", {})
            if key not in cache:
                g = pygame.Surface((2 * r + 6, 2 * r + 6), pygame.SRCALPHA)
                c = (r + 3, r + 3)
                pygame.draw.circle(g, (0, 0, 0, 120), (c[0] + 2, c[1] + 3), r)
                pygame.draw.circle(g, (170, 110, 10), c, r)
                pygame.draw.circle(g, (245, 195, 50), c, r - 2)
                pygame.draw.circle(g, (255, 225, 110), c, int(r * 0.72))
                pygame.draw.circle(g, (200, 140, 20), c, int(r * 0.72), 2)
                label = f"{v / 1e6:g}M" if v >= 10 ** 6 else f"{v / 1000:g}K" if v >= 1000 else str(v)
                if r >= 10:
                    draw_text(g, label, fit_font(label, int(r * 0.62), int(r * 1.3)), (110, 60, 5), c)
                pygame.draw.circle(g, (255, 255, 230), (c[0] - r * 0.35, c[1] - r * 0.4), max(1, r // 6))
                cache[key] = g
            img = cache[key]
            if alpha < 255:
                img = img.copy()
                img.set_alpha(alpha)
            surf.blit(img, img.get_rect(center=(x, y)))
            return
        pygame.draw.circle(surf, (0, 0, 0), (x + 2, y + 3), r)
        img = self.assets.chip(v if v in CHIP_STYLE else max([c for c in CHIP_VALUES if c <= v] or [10]), r)
        if alpha < 255:
            img = img.copy()
            img.set_alpha(alpha)
        surf.blit(img, img.get_rect(center=(x, y)))

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        face = self.face()
        # lights around the cabinet
        for i in range(24):
            x = CP_LEFT - 30 + i * (CP_RIGHT - CP_LEFT + 60) / 23
            on = (i + int(self.t * 8)) % 3 == 0
            pygame.draw.circle(surf, (255, 220, 120) if on else (90, 60, 40), (x, 76), 5)
        # walls bonus
        if self.walls:
            a = int(160 + 80 * math.sin(self.t * 8))
            for x in (CP_LEFT - 8, CP_RIGHT):
                pygame.draw.rect(surf, (120, 255, 150), (x, CP_FRONT - CP_DRAIN, 8, CP_DRAIN))
            draw_text(surf, f"WALLS {math.ceil(self.walls)}s", font(13, bold=True), (140, 240, 150),
                      (CP_LEFT - 25, CP_FRONT - CP_DRAIN - 14))
        else:
            for x in (CP_LEFT - 20, CP_RIGHT + 20):
                draw_text(surf, "LOST", font(11, bold=True), (120, 60, 60), (x, CP_FRONT - CP_DRAIN / 2))
        # chips on the table
        for x, y, v, g in sorted(self.chips, key=lambda c: c[1]):
            self.draw_chip(surf, x, y, v, g)
        # the pusher
        body = pygame.Rect(CP_LEFT, CP_BACK, CP_RIGHT - CP_LEFT, face - CP_BACK)
        pygame.draw.rect(surf, (150, 155, 170), body)
        for k in range(0, body.h, 10):
            pygame.draw.line(surf, (175, 180, 195), (body.x, body.y + k), (body.right, body.y + k), 2)
        pygame.draw.rect(surf, (230, 232, 240), (CP_LEFT, face - 8, CP_RIGHT - CP_LEFT, 8))
        pygame.draw.line(surf, (90, 95, 110), (CP_LEFT, face), (CP_RIGHT, face), 2)
        # the chute you aim
        ax = self.aim
        pygame.draw.polygon(surf, (40, 40, 50), [(ax - 26, 58), (ax + 26, 58), (ax + 14, 90), (ax - 14, 90)])
        pygame.draw.polygon(surf, GOLD, [(ax - 26, 58), (ax + 26, 58), (ax + 14, 90), (ax - 14, 90)], 2)
        if self.bet:
            self.draw_chip(surf, ax, 72, self.bet if self.bet in CHIP_STYLE else max(
                [c for c in CHIP_VALUES if c <= self.bet] or [10]), False, 0.55)
        for yy in range(int(face) + 6, int(face) + 40, 8):
            pygame.draw.line(surf, (255, 255, 255), (ax, yy), (ax, yy + 3), 1)
        # chips falling off
        for x, y, v, g, t, won in self.falling:
            k = t / 0.6
            if won:
                self.draw_chip(surf, x, y + k * 40, v, g, 1 - k * 0.4, int(255 * (1 - k)))
            else:
                self.draw_chip(surf, x + (-1 if x < CP_LEFT else 1) * k * 30, y + k * 20, v, g, 1 - k * 0.6,
                               int(255 * (1 - k)))
        # win tray glow
        if self.win_glow:
            g = pygame.Surface((CP_RIGHT - CP_LEFT + 20, 50), pygame.SRCALPHA)
            pygame.draw.rect(g, (255, 220, 90, int(90 * self.win_glow)), g.get_rect(), border_radius=12)
            surf.blit(g, (CP_LEFT - 10, CP_FRONT + 10))
        draw_text(surf, "WIN TRAY", font(13, bold=True), (230, 190, 60), (CP_LEFT + 12, CP_FRONT + 35), anchor="midleft")
        # side panels
        net = self.session_out - self.session_in
        left = pygame.Rect(22, 90, 250, 250)
        soft_panel(surf, left, 170, (140, 40, 60))
        draw_text(surf, "THIS VISIT", font(16, bold=True), GOLD, (left.centerx, left.y + 24))
        for k, (label, val, col) in enumerate((("DROPPED", money(self.session_in), WHITE),
                                                ("WON", money(self.session_out), (120, 230, 140)),
                                                ("NET", ("+" if net >= 0 else "-") + money(abs(net)),
                                                 (120, 230, 140) if net >= 0 else (240, 120, 120)))):
            y = left.y + 64 + k * 44
            draw_text(surf, label, font(14, bold=True), (190, 180, 200), (left.x + 20, y), anchor="midleft")
            draw_text(surf, val, font(18, bold=True), col, (left.right - 20, y), anchor="midright")
        on_table = sum(c[2] for c in self.chips)
        draw_text(surf, f"On the table: {money(on_table)}", font(13), (170, 170, 190), (left.centerx, left.bottom - 22))
        right = pygame.Rect(W - 272, 90, 250, 250)
        soft_panel(surf, right, 170, (140, 40, 60))
        draw_text(surf, "BONUSES", font(16, bold=True), GOLD, (right.centerx, right.y + 24))
        lines = [("GOLD CHIP", f"worth {CP_GOLD_MULT}x your drop", GOLD),
                 ("CHIP SHOWER", "8 free chips", (120, 220, 255)),
                 ("SIDE WALLS", "nothing lost for 15s", (140, 240, 150))]
        for k, (a, b, col) in enumerate(lines):
            y = right.y + 62 + k * 52
            draw_text(surf, a, font(15, bold=True), col, (right.x + 18, y), anchor="midleft")
            draw_text(surf, b, font(13), (190, 190, 205), (right.x + 18, y + 20), anchor="midleft")
        draw_text(surf, "Any drop can trigger one!", font(12), (160, 160, 180), (right.centerx, right.bottom - 20))
        for k, (text, col, t) in enumerate(reversed(self.flash[-3:])):      # newest on top
            if t < 2.3:
                draw_pill(surf, text, font(20, bold=True), ((CP_LEFT + CP_RIGHT) / 2, 330 + k * 44 - min(t, 0.3) * 60),
                          col, (0, 0, 0, 215), col, pad=(16, 5))
        if self.message:
            draw_pill(surf, self.message, font(14, bold=True), (640, 628), GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(14, 3))
        self.btn_drop.text = f"DROP {money(self.bet)}" if self.bet else "DROP"
        self.draw_bottom(surf, mouse, self.btn_drop, 0 < self.bet <= self.app.balance, (16, 8, 10))
        self.app.draw_top_bar(surf, "CHIP PUSHER", lobby=True)


def art_pusher(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    for y in range(h * k):
        pygame.draw.line(s, lerp_col((30, 90, 140), (15, 50, 90), y / (h * k)), (0, y), (w * k, y))
    pygame.draw.rect(s, (160, 165, 180), (0, 0, w * k, h * k * 0.22))
    pygame.draw.rect(s, (235, 235, 245), (0, h * k * 0.22 - 8, w * k, 8))
    rng = random.Random(4)
    for _ in range(28):
        x, y = rng.uniform(30, w * k - 30), rng.uniform(h * k * 0.35, h * k * 0.9)
        img = pygame.transform.smoothscale(make_chip(rng.choice(CHIP_VALUES[:8]), 26), (44, 44))
        s.blit(img, img.get_rect(center=(x, y)))
    pygame.draw.rect(s, (230, 190, 60), (0, h * k - 10, w * k, 10))
    draw_text(s, "CHIP PUSHER", font(h * k * 0.13, bold=True, serif=True), (255, 225, 120),
              (w * k * 0.5, h * k * 0.11), shadow=(0, 0, 0))
    return pygame.transform.smoothscale(s, (w, h))


# --------------------------------------------------------------------------
# Coin Flip - heads or tails, double your bet
# --------------------------------------------------------------------------
FLIP_EDGE = 0.045                # the coin lands standing on its edge about 1 flip in 22...
FLIP_EDGE_PAY = 20               # ...and calling the edge pays 20x
FLIP_TIME = 1.7
FLIP_R = 90                      # coin radius
FLIP_Y = 380                     # where the coin lands


def make_coin_face(kind, r):
    k = 2
    R = r * k
    s = pygame.Surface((2 * R, 2 * R), pygame.SRCALPHA)
    c = (R, R)
    pygame.draw.circle(s, (150, 100, 15), c, R)
    for i in range(20):
        t = i / 19
        pygame.draw.circle(s, lerp_col((225, 170, 40), (255, 225, 110), t), (R - t * R * 0.15, R - t * R * 0.15),
                           int(R * 0.94 * (1 - t * 0.25)))
    pygame.draw.circle(s, (190, 135, 25), c, int(R * 0.80), 3 * k)
    for i in range(48):                                  # ridged rim
        a = i / 48 * 2 * math.pi
        pygame.draw.line(s, (170, 115, 20), (R + math.cos(a) * R * 0.9, R + math.sin(a) * R * 0.9),
                         (R + math.cos(a) * R * 0.99, R + math.sin(a) * R * 0.99), 2 * k)
    dark = (125, 80, 10)
    if kind == "heads":                                  # a crown
        w, h = R * 0.9, R * 0.5
        x0, y0 = R - w / 2, R - h * 0.55
        pts = [(x0, y0 + h), (x0, y0 + h * 0.2), (x0 + w * 0.25, y0 + h * 0.55), (x0 + w * 0.5, y0),
               (x0 + w * 0.75, y0 + h * 0.55), (x0 + w, y0 + h * 0.2), (x0 + w, y0 + h)]
        pygame.draw.polygon(s, dark, pts)
        for px in (x0, x0 + w * 0.5, x0 + w):
            pygame.draw.circle(s, dark, (px, y0 + (0 if px == x0 + w * 0.5 else h * 0.2) - 6 * k), 7 * k)
        draw_text(s, "HEADS", font(R * 0.24, bold=True, serif=True), dark, (R, R + R * 0.48))
    else:                                                # a big star and TAILS
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rr = R * (0.42 if i % 2 == 0 else 0.18)
            pts.append((R + math.cos(a) * rr, R - R * 0.08 + math.sin(a) * rr))
        pygame.draw.polygon(s, dark, pts)
        draw_text(s, "TAILS", font(R * 0.24, bold=True, serif=True), dark, (R, R + R * 0.52))
    return pygame.transform.smoothscale(s, (2 * r, 2 * r))


class CoinFlip(StakeGame):
    key = "coinflip"

    def __init__(self, app):
        super().__init__(app, ((22, 13, 8), (90, 60, 35)))
        self.bg = felt_table((12, 60, 34), (28, 105, 60))
        self.faces = {k: make_coin_face(k, FLIP_R) for k in ("heads", "tails")}
        self.state = "betting"          # betting, flipping, won (collect or double), result
        self.pick = None
        self.result = None
        self.stake = 0                  # riding on the flip in the air
        self.run_stake = 0              # the chips you first put in this run
        self.pot = 0                    # what you've won so far, waiting to be collected or risked
        self.doubles = 0                # how many times you've let it ride
        self.t = 0.0
        self.flip_t = 0.0
        self.end_angle = 0.0
        self.history = deque(maxlen=16)
        self.streak = 0
        self.message = "SET YOUR BET, THEN CALL IT: HEADS, TAILS - OR THE EDGE"
        self.btn_heads = Button((965, 646, 86, 62), "HEADS", (190, 140, 30), 17, "H  x2")
        self.btn_tails = Button((1057, 646, 86, 62), "TAILS", (80, 95, 130), 17, "T  x2")
        self.btn_edge = Button((1149, 646, 86, 62), "EDGE", (150, 40, 90), 17, f"E  x{FLIP_EDGE_PAY}")
        self.btn_collect = Button((W / 2 - 150, 452, 300, 62), "COLLECT", (25, 120, 60), 24, "C")

    def busy(self):
        return self.state == "flipping"

    def can_leave(self):
        return self.state != "flipping"

    def outstanding_bets(self):
        return self.stake if self.state == "flipping" else (self.pot if self.state == "won" else 0)

    def leave(self):
        if self.state == "won":                         # walking away collects your winnings
            self.collect()

    def flip(self, side):
        if self.busy():
            return
        if self.state == "won":                         # double (or nothing) - let the whole win ride
            stake, self.pot = self.pot, 0
            self.doubles += 1
        else:
            stake = self.take_bet()
            if not stake:
                return
            self.run_stake, self.doubles = stake, 0
        self.stake, self.pick = stake, side
        r = random.random()
        self.result = "edge" if r < FLIP_EDGE else ("heads" if r < FLIP_EDGE + (1 - FLIP_EDGE) / 2 else "tails")
        turns = random.randint(9, 12)                    # half-turns: even = heads up, odd = tails up
        if (turns % 2 == 0) != (self.result == "heads"):
            turns += 1
        self.end_angle = turns * math.pi + (math.pi / 2 if self.result == "edge" else 0)
        self.state = "flipping"
        self.flip_t = 0.0
        self.message = (f"LETTING {money(stake)} RIDE ON {side.upper()}..." if self.doubles
                        else f"YOU CALLED {side.upper()}...")
        self.app.sfx("chip")

    def payout(self, side):
        return FLIP_EDGE_PAY if side == "edge" else 2

    def settle(self):
        self.history.append(self.result)
        if self.result == self.pick:
            self.streak += 1
            self.pot = self.stake * self.payout(self.pick)
            self.stake = 0
            self.state = "won"
            self.app.sfx("win")
            self.app.effects.burst(W / 2, FLIP_Y - 40, 60 if self.pick == "edge" else 36,
                                   [(255, 220, 90), (255, 255, 255)])
            what = "ON ITS EDGE!" if self.result == "edge" else f"{self.result.upper()}!"
            self.message = f"{what}  YOU'VE WON {money(self.pot)}  -  COLLECT, OR DOUBLE OR NOTHING?"
        else:
            self.streak = 0
            lost = self.stake
            self.stake = 0
            self.state = "result"
            if self.result == "edge":
                why = "IT LANDED ON ITS EDGE!"
            else:
                why = f"{self.result.upper()}."
            tail = f"YOU LOSE {money(lost)}" if not self.doubles else f"THE {money(lost)} YOU LET RIDE IS GONE"
            self.finish(self.run_stake, 0, lose_msg=f"{why}  {tail}")

    def collect(self):
        if self.state != "won":
            return
        won, self.pot = self.pot, 0
        self.state = "result"
        extra = f" AFTER {self.doubles} DOUBLE{'S' if self.doubles != 1 else ''}" if self.doubles else ""
        self.finish(self.run_stake, won, f"COLLECTED {money(won)}{extra}!")

    def handle(self, e):
        keys = {pygame.K_h: "heads", pygame.K_t: "tails", pygame.K_e: "edge"}
        if e.type == pygame.KEYDOWN:
            if e.key in keys:
                self.flip(keys[e.key])
            elif e.key in (pygame.K_c, pygame.K_RETURN) and self.state == "won":
                self.collect()
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.state == "won":
                if self.btn_collect.clicked(e.pos):
                    self.collect()
                    return
            elif self.chip_click(e.pos):
                return
            if self.btn_clear.clicked(e.pos, self.state != "won"):
                self.bet = 0
            for b, side in ((self.btn_heads, "heads"), (self.btn_tails, "tails"), (self.btn_edge, "edge")):
                if b.clicked(e.pos, not self.busy()):
                    self.flip(side)
                    return

    def update(self, dt):
        self.t += dt
        if self.state == "flipping":
            self.flip_t += dt
            if self.flip_t >= FLIP_TIME:
                self.settle()

    def coin_pose(self):
        """Height above the table, and how far the coin has turned."""
        if self.state != "flipping":
            if self.result is None:
                return 0.0, 0.0
            return 0.0, self.end_angle
        p = min(1.0, self.flip_t / FLIP_TIME)
        height = math.sin(math.pi * min(1.0, p / 0.9)) * 235 if p < 0.9 else 0.0
        if p >= 0.9:                                     # a little bounce as it lands
            height = abs(math.sin((p - 0.9) / 0.1 * math.pi)) * 18
        ease = 1 - (1 - p) ** 2.2
        return height, self.end_angle * ease

    def draw_coin(self, surf):
        height, ang = self.coin_pose()
        x, y = W / 2, FLIP_Y - height
        sh = max(0.35, 1 - height / 400)
        shadow = pygame.Surface((int(2 * FLIP_R * sh) + 4, int(40 * sh) + 4), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (0, 0, 0, int(110 * sh)), shadow.get_rect())
        surf.blit(shadow, shadow.get_rect(center=(W / 2, FLIP_Y + FLIP_R * 0.9)))
        c = math.cos(ang)
        thin = abs(c)
        if self.state != "flipping" and self.result == "edge":
            wob = math.sin(self.t * 9) * 3
            rim = pygame.Rect(0, 0, 26, 2 * FLIP_R)
            rim.center = (x + wob, y)
            pygame.draw.rect(surf, (150, 100, 15), rim, border_radius=12)
            for k in range(rim.y + 6, rim.bottom - 4, 8):
                pygame.draw.line(surf, (210, 160, 40), (rim.x + 3, k), (rim.right - 3, k), 3)
            return
        kind = "heads" if c >= 0 else "tails"
        face = themed(("coin", kind), lambda: make_theme_coin_face(kind, FLIP_R, CURRENT_THEME)) \
            if CURRENT_THEME else self.faces[kind]
        h = max(6, int(2 * FLIP_R * thin))
        rim_h = int(10 * (1 - thin)) + 3
        pygame.draw.ellipse(surf, (120, 80, 10), (x - FLIP_R, y - h / 2 + rim_h, 2 * FLIP_R, h))
        img = pygame.transform.smoothscale(face, (2 * FLIP_R, h))
        surf.blit(img, img.get_rect(center=(x, y)))

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        draw_text(surf, "COIN FLIP", font(40, bold=True, serif=True), GOLD, (W / 2, 104), shadow=(0, 0, 0))
        draw_text(surf, f"HEADS OR TAILS PAYS x2   -   THE EDGE PAYS x{FLIP_EDGE_PAY}", font(16, bold=True),
                  (200, 225, 205), (W / 2, 142))
        self.draw_coin(surf)
        # your call and the payout
        left = pygame.Rect(40, 170, 250, 220)
        soft_panel(surf, left, 150, (90, 140, 100))
        draw_text(surf, "YOUR CALL", font(15, bold=True), GOLD, (left.centerx, left.y + 26))
        call = self.pick.upper() if self.pick and self.state != "betting" else "-"
        draw_text(surf, call, font(34, bold=True), WHITE, (left.centerx, left.y + 70))
        if self.state == "won":
            draw_text(surf, f"WON SO FAR {money(self.pot)}", font(16, bold=True), (120, 230, 140),
                      (left.centerx, left.y + 116))
            draw_text(surf, f"double it: {money(self.pot * 2)}", font(14), (200, 200, 210), (left.centerx, left.y + 142))
            draw_text(surf, f"edge: {money(self.pot * FLIP_EDGE_PAY)}", font(14), (230, 150, 200),
                      (left.centerx, left.y + 164))
        else:
            amt = self.stake or self.bet
            draw_text(surf, f"BET {money(amt)}", font(16, bold=True), (200, 200, 210), (left.centerx, left.y + 116))
            draw_text(surf, f"x2 pays {money(amt * 2)}", font(14), (120, 230, 140), (left.centerx, left.y + 142))
            draw_text(surf, f"edge pays {money(amt * FLIP_EDGE_PAY)}", font(14), (230, 150, 200),
                      (left.centerx, left.y + 164))
        label = f"DOUBLES: {self.doubles}" if self.state in ("won", "flipping") and self.doubles else f"STREAK: {self.streak}"
        draw_text(surf, label, font(14, bold=True), GOLD if self.streak or self.doubles else (160, 160, 170),
                  (left.centerx, left.y + 196))
        # history
        right = pygame.Rect(W - 290, 170, 250, 220)
        soft_panel(surf, right, 150, (90, 140, 100))
        draw_text(surf, "LAST FLIPS", font(15, bold=True), GOLD, (right.centerx, right.y + 26))
        for i, res in enumerate(reversed(self.history)):
            cx = right.x + 34 + (i % 6) * 36
            cy = right.y + 66 + (i // 6) * 40
            col = (235, 185, 50) if res == "heads" else ((150, 160, 190) if res == "tails" else (230, 80, 160))
            pygame.draw.circle(surf, col, (cx, cy), 15)
            draw_text(surf, {"heads": "H", "tails": "T", "edge": "E"}[res], font(14, bold=True), (40, 30, 10), (cx, cy))
        if not self.history:
            draw_text(surf, "No flips yet", font(14), (170, 180, 170), (right.centerx, right.y + 90))
        # double or nothing
        if self.state == "won":
            box = pygame.Rect(0, 0, 600, 124)
            box.midbottom = (W / 2, 614)
            soft_panel(surf, box, 215, GOLD)
            draw_text(surf, f"YOU'VE WON {money(self.pot)}!", font(24, bold=True), (120, 240, 140), (W / 2, box.y + 22))
            self.btn_collect.text = f"COLLECT {money(self.pot)}"
            self.btn_collect.rect.size = (280, 52)
            self.btn_collect.rect.center = (W / 2, box.y + 66)
            self.btn_collect.draw(surf, mouse)
            draw_text(surf, "...or DOUBLE OR NOTHING: call HEADS or TAILS (or the EDGE for x20) below",
                      font(13, bold=True), (240, 220, 160), (W / 2, box.bottom - 18))
        elif self.state == "result":
            big = {"heads": "HEADS!", "tails": "TAILS!", "edge": "ON ITS EDGE!"}[self.result]
            won = self.pot == 0 and self.message.startswith("COLLECTED")
            draw_pill(surf, big, font(34, bold=True), (W / 2, 548), (120, 240, 140) if won else (250, 140, 140),
                      (0, 0, 0, 200), GOLD if won else (150, 60, 60), pad=(24, 6))
        if self.message and self.state != "won":
            draw_pill(surf, self.message, font(15, bold=True), (640, 604), GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(14, 4))
        riding = self.state == "won"
        ok = not self.busy() and (riding or 0 < self.bet <= self.app.balance)
        for b, side in ((self.btn_heads, "heads"), (self.btn_tails, "tails"), (self.btn_edge, "edge")):
            b.text = side.upper() if not riding else ("DOUBLE" if side != "edge" else "x20")
            b.hint = (f"{side[0].upper()}  x{self.payout(side)}" if not riding else
                      f"ON {side.upper()}")
        bottom_bar(surf, (10, 8, 12))
        self.tray.draw(surf, self.assets, mouse, dim=lambda v: self.bet + v > self.app.balance,
                       disabled=self.busy() or riding)
        self.btn_clear.hint = f"BET {money(self.bet)}"
        self.btn_clear.draw(surf, mouse, self.bet > 0 and not self.busy() and not riding)
        for b in (self.btn_heads, self.btn_tails, self.btn_edge):
            b.draw(surf, mouse, ok)
        self.app.draw_top_bar(surf, "COIN FLIP", lobby=True, lobby_enabled=self.can_leave())


def art_coinflip(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    for y in range(h * k):
        pygame.draw.line(s, lerp_col((25, 100, 60), (8, 40, 24), y / (h * k)), (0, y), (w * k, y))
    r = int(h * k * 0.33)
    heads = make_coin_face("heads", r)
    tails = pygame.transform.smoothscale(make_coin_face("tails", r), (2 * r, int(2 * r * 0.35)))
    s.blit(heads, heads.get_rect(center=(w * k * 0.36, h * k * 0.5)))
    s.blit(tails, tails.get_rect(center=(w * k * 0.68, h * k * 0.3)))
    draw_text(s, "x2", font(h * k * 0.3, bold=True, serif=True), (255, 225, 120), (w * k * 0.78, h * k * 0.68),
              shadow=(0, 0, 0))
    return pygame.transform.smoothscale(s, (w, h))


# --------------------------------------------------------------------------
# Online accounts (Supabase) - the same Supabase project as Cube Shooter, with its own table
# --------------------------------------------------------------------------
SUPABASE_URL = "https://ahjhfwucsivhzhqcjyau.supabase.co"
SUPABASE_KEY = "sb_publishable_MUfA7Qcl32e-XmzP9mYYGg_7AKqOzBf"   # the publishable key: it's meant to ship in apps
ACCOUNT_DOMAIN = "players.grandroyale.game"   # each username gets a stand-in email nobody ever sees
ACCOUNT_TABLE = "casino_players"              # see supabase_casino_setup.sql
NAME_MIN, PASS_MIN = 3, 4
CLOUD_SAVE_EVERY = 20.0                       # seconds between uploads of your progress
REMEMBER_KEY = "grand_royale_remember"
HTTP_TIMEOUT = 10.0


class AccountError(Exception):
    pass


def account_email(name):
    return f"{name.strip().lower()}@{ACCOUNT_DOMAIN}"


def account_password(password):
    return "grand-royale:" + password          # Supabase wants 6+ characters; the game allows 4


def username_problem(name):
    name = name.strip()
    if len(name) < NAME_MIN:
        return f"USERNAMES NEED AT LEAST {NAME_MIN} CHARACTERS"
    if len(name) > NAME_MAX:
        return f"USERNAMES CAN BE AT MOST {NAME_MAX} CHARACTERS"
    if not all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in name):
        return "USE ONLY LETTERS, NUMBERS, _ AND -"
    return ""


class HttpReq:
    """One request to Supabase that never freezes the game. On the PC it runs on a thread; in the browser
    it uses the page's own fetch(). Check .poll() each frame until it says it's done."""
    _next = 0

    def __init__(self, method, path, body=None, token=None, prefer=None):
        self.done = False
        self.status = 0             # 0 means the server couldn't be reached
        self.data = None
        self.text = ""
        headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if prefer:
            headers["Prefer"] = prefer
        url = SUPABASE_URL + path
        body_text = json.dumps(body) if body is not None else None
        if WEB:
            self.start_web(method, url, headers, body_text)
        else:
            threading.Thread(target=self.run, args=(method, url, headers, body_text), daemon=True).start()

    def run(self, method, url, headers, body_text):
        import urllib.error
        import urllib.request
        status, text = 0, ""
        try:
            req = urllib.request.Request(url, data=body_text.encode() if body_text is not None else None,
                                         headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                status, text = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            status = err.code
            try:
                text = err.read().decode("utf-8", "replace")
            except Exception:
                text = ""
        except Exception as err:
            status, text = 0, str(err)
        self.finish(status, text)

    def start_web(self, method, url, headers, body_text):
        import platform
        HttpReq._next += 1
        self.rid = HttpReq._next
        body = json.dumps(body_text) if body_text is not None else "undefined"
        platform.window.eval(
            "(function(){window.__gr=window.__gr||{};const id=%d;"
            "fetch(%s,{method:%s,headers:%s,body:%s})"
            ".then(r=>r.text().then(t=>{window.__gr[id]=JSON.stringify({s:r.status,t:t});}))"
            ".catch(e=>{window.__gr[id]=JSON.stringify({s:0,t:String(e)});});})()"
            % (self.rid, json.dumps(url), json.dumps(method), json.dumps(headers), body))

    def poll(self):
        if WEB and not self.done:
            import platform
            got = platform.window.eval("(window.__gr&&window.__gr[%d])||''" % self.rid)
            got = str(got) if got else ""
            if got:
                platform.window.eval("delete window.__gr[%d]" % self.rid)
                info = json.loads(got)
                self.finish(int(info.get("s", 0)), str(info.get("t", "")))
        return self.done

    def finish(self, status, text):
        self.text = text
        try:
            self.data = json.loads(text) if text else None
        except ValueError:
            self.data = None
        self.status = status
        self.done = True

    @property
    def ok(self):
        return 200 <= self.status < 300

    def message(self):
        d = self.data if isinstance(self.data, dict) else {}
        return str(d.get("msg") or d.get("message") or d.get("error_description") or d.get("error") or self.text)[:200]


def need(r, what):
    """Turn a failed request into a message a player can understand."""
    if r.ok:
        return
    if r.status == 0:
        raise AccountError("CAN'T REACH THE SERVER - CHECK YOUR INTERNET")
    msg = r.message()
    if r.status == 404 or "does not exist" in msg or "schema cache" in msg:
        raise AccountError("THE CASINO'S ACCOUNT DATABASE ISN'T SET UP YET")
    raise AccountError(f"{what} FAILED ({r.status}): {msg[:70]}")


class Session:
    """A logged-in player."""

    def __init__(self, auth, name=""):
        self.user_id = auth["user"]["id"]
        self.email_name = str(auth["user"].get("email", "player@")).split("@")[0]
        self.name = name
        self.is_owner = False            # set in Supabase (casino_players.is_owner) - players can't change it
        self.synced_balance = None       # the balance column as we last wrote it (to spot the owner editing it)
        self.has_balance_column = True   # False on a database from before the balance column existed
        self.update(auth)

    def update(self, auth):
        self.access = auth["access_token"]
        self.refresh = auth["refresh_token"]
        self.expires_at = time.time() + int(auth.get("expires_in", 3600)) - 120


class Flow:
    """Runs a login / save / ... step by step. The step function is a generator that yields each HttpReq and
    gets it back once it's finished, so nothing ever waits on the internet."""

    def __init__(self, gen):
        self.gen = gen
        self.req = None
        self.done = False
        self.result = None
        self.error = ""
        self.step(None)

    def step(self, value):
        try:
            self.req = self.gen.send(value)
        except StopIteration as stop:
            self.done, self.result = True, stop.value
        except AccountError as err:
            self.done, self.error = True, str(err)
        except Exception as err:                     # a surprise reply from the server
            self.done, self.error = True, f"SOMETHING WENT WRONG: {str(err)[:60]}"

    def poll(self):
        if not self.done and self.req.poll():
            self.step(self.req)
        return self.done

    def wait(self, timeout):
        """Block until done (only used on the PC when the game is closing)."""
        end = time.time() + timeout
        while not self.poll() and time.time() < end:
            time.sleep(0.05)
        return self.done and not self.error


def flow_refresh(session):
    if time.time() >= session.expires_at:
        r = yield HttpReq("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": session.refresh})
        need(r, "STAYING LOGGED IN")
        session.update(r.data)


def flow_open(session, typed_name, new_progress):
    """Load the player's row - or create it if it's missing."""
    r = yield HttpReq("GET", f"/rest/v1/{ACCOUNT_TABLE}?id=eq.{session.user_id}"
                             "&select=username,progress,balance,is_owner", token=session.access)
    if r.status == 400 and "does not exist" in r.message():     # an older database without those columns
        session.has_balance_column = False
        r = yield HttpReq("GET", f"/rest/v1/{ACCOUNT_TABLE}?id=eq.{session.user_id}&select=username,progress",
                          token=session.access)
    need(r, "LOADING YOUR ACCOUNT")
    rows = r.data if isinstance(r.data, list) else []
    if rows:
        row = rows[0]
        session.name = row["username"]
        session.is_owner = bool(row.get("is_owner"))
        progress = row.get("progress") or {}
        if row.get("balance") is not None:     # the balance column wins - the owner may have edited it
            progress["balance"] = int(row["balance"])
            session.synced_balance = int(row["balance"])
        return progress
    name = typed_name or session.email_name
    body = {"id": session.user_id, "username": name, "progress": new_progress}
    if session.has_balance_column:
        body["balance"] = int(new_progress.get("balance", START_BALANCE))
        session.synced_balance = body["balance"]
    r = yield HttpReq("POST", f"/rest/v1/{ACCOUNT_TABLE}", body, token=session.access, prefer="return=minimal")
    need(r, "CREATING YOUR ACCOUNT")
    session.name = name
    return new_progress


def flow_login(name, password):
    r = yield HttpReq("POST", "/auth/v1/token?grant_type=password",
                      {"email": account_email(name), "password": account_password(password)})
    if r.status in (400, 401):
        raise AccountError("WRONG USERNAME OR PASSWORD")
    need(r, "LOGGING IN")
    session = Session(r.data)
    progress = yield from flow_open(session, name.strip(), {})
    return session, progress


def flow_signup(name, password, progress):
    name = name.strip()
    r = yield HttpReq("POST", "/rest/v1/rpc/casino_username_taken", {"name": name})
    need(r, "CHECKING THE USERNAME")
    if r.data is True:
        raise AccountError("THAT USERNAME IS TAKEN")
    r = yield HttpReq("POST", "/auth/v1/signup", {"email": account_email(name), "password": account_password(password)})
    if not r.ok and ("already" in r.message().lower() or r.status == 422):
        raise AccountError("THAT USERNAME IS TAKEN")
    need(r, "CREATING YOUR ACCOUNT")
    if not isinstance(r.data, dict) or not r.data.get("access_token"):
        raise AccountError("THE SERVER DIDN'T LOG THE NEW ACCOUNT IN")
    session = Session(r.data, name)
    progress = yield from flow_open(session, name, progress)
    return session, progress


def flow_resume(refresh_token):
    r = yield HttpReq("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": refresh_token})
    if r.status in (400, 401):
        raise AccountError("PLEASE LOG IN AGAIN")
    need(r, "LOGGING IN")
    session = Session(r.data)
    progress = yield from flow_open(session, "", {})
    return session, progress


def flow_save(session, progress):
    """Upload your progress. If the owner changed your balance in Supabase since the last save, their number
    wins: it's put into this save, and the game is told (returns ("edited", new balance))."""
    yield from flow_refresh(session)
    edited = None
    if session.has_balance_column:
        r = yield HttpReq("GET", f"/rest/v1/{ACCOUNT_TABLE}?id=eq.{session.user_id}&select=balance,is_owner",
                          token=session.access)
        need(r, "SAVING")
        rows = r.data if isinstance(r.data, list) else []
        if rows:
            session.is_owner = bool(rows[0].get("is_owner"))
            server = rows[0].get("balance")
            if server is not None and session.synced_balance is not None and int(server) != session.synced_balance:
                edited = int(server)
                progress = dict(progress, balance=edited)
    body = {"progress": progress, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if session.has_balance_column:
        body["balance"] = int(progress.get("balance", 0))
    r = yield HttpReq("PATCH", f"/rest/v1/{ACCOUNT_TABLE}?id=eq.{session.user_id}", body,
                      token=session.access, prefer="return=minimal")
    need(r, "SAVING")
    if session.has_balance_column:
        session.synced_balance = body["balance"]
    return ("edited", edited) if edited is not None else True


def flow_delete(session):
    yield from flow_refresh(session)
    r = yield HttpReq("POST", "/rest/v1/rpc/delete_my_account", {}, token=session.access)
    need(r, "DELETING THE ACCOUNT")
    return True


def remember_path():
    return os.environ.get("GRAND_ROYALE_REMEMBER") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "GrandRoyale", "remember.json")


def remember_load():
    try:
        if WEB:
            import platform
            got = platform.window.localStorage.getItem(REMEMBER_KEY)
            return str(got) if got else ""
        with open(remember_path(), encoding="utf-8") as f:
            return str(json.load(f).get("refresh", ""))
    except Exception:
        return ""


def remember_save(token):
    try:
        if WEB:
            import platform
            platform.window.localStorage.setItem(REMEMBER_KEY, token)
            return
        os.makedirs(os.path.dirname(remember_path()), exist_ok=True)
        with open(remember_path(), "w", encoding="utf-8") as f:
            json.dump({"refresh": token}, f)
    except Exception:
        pass


def remember_clear():
    try:
        if WEB:
            import platform
            platform.window.localStorage.removeItem(REMEMBER_KEY)
        elif os.path.exists(remember_path()):
            os.remove(remember_path())
    except Exception:
        pass


# ---- the log in / sign up screen ------------------------------------------------
class AccountScreen:
    key = "account"

    def __init__(self, app):
        self.app = app
        self.bg = gradient_bg((40, 10, 20), (6, 3, 8))
        self.mode = "login"             # login or signup
        self.name = ""
        self.pw = ""
        self.pw2 = ""
        self.focus = "name"
        self.remember = True
        self.bring = True               # sign up: start the account with the progress on this computer
        self.error = ""
        self.t = 0.0
        self.boxes = {"name": pygame.Rect(W / 2 - 230, 250, 460, 52), "pw": pygame.Rect(W / 2 - 230, 336, 460, 52),
                      "pw2": pygame.Rect(W / 2 - 230, 422, 460, 52)}
        self.tabs = [pygame.Rect(W / 2 - 230, 150, 226, 46), pygame.Rect(W / 2 + 4, 150, 226, 46)]
        self.btn_go = Button((W / 2 - 230, 560, 460, 62), "LOG IN", (25, 120, 60), 26, "ENTER")
        self.btn_guest = Button((W / 2 - 150, 636, 300, 44), "PLAY AS GUEST", (70, 70, 85), 17, "SAVED ON THIS DEVICE ONLY")
        self.btn_logout = Button((W / 2 - 150, 430, 300, 58), "LOG OUT", (150, 35, 40), 22)
        self.btn_continue = Button((W / 2 - 150, 340, 300, 64), "PLAY", (25, 120, 60), 26, "ENTER")
        self.btn_delete = Button((W / 2 - 150, 506, 300, 52), "DELETE ACCOUNT", (110, 25, 30), 18,
                                 "CLICK TWICE TO CONFIRM")
        self.del_confirm_t = 0.0
        self.check_remember = pygame.Rect(0, 0, 26, 26)
        self.check_bring = pygame.Rect(0, 0, 26, 26)

    def can_leave(self):
        return not self.working()

    def outstanding_bets(self):
        return 0

    def leave(self):
        self.app.guest = self.app.guest or not self.app.session

    def on_enter(self):
        self.error = ""
        self.pw = self.pw2 = ""

    def working(self):
        return bool(self.app.account_flow)

    def fields(self):
        return ["name", "pw", "pw2"] if self.mode == "signup" else ["name", "pw"]

    def submit(self):
        if self.working():
            return
        problem = username_problem(self.name)
        if problem:
            self.error = problem
            return
        if len(self.pw) < PASS_MIN:
            self.error = f"PASSWORDS NEED AT LEAST {PASS_MIN} CHARACTERS"
            return
        self.app.remember = self.remember
        if self.mode == "signup":
            if self.pw != self.pw2:
                self.error = "THE TWO PASSWORDS DON'T MATCH"
                return
            progress = self.app.save_data(cloud=True) if self.bring else {}
            self.app.start_account_flow("signup", flow_signup(self.name, self.pw, progress))
        else:
            self.app.start_account_flow("login", flow_login(self.name, self.pw))
        self.error = ""

    def handle(self, e):
        app = self.app
        if app.session:                                  # already logged in
            if e.type == pygame.KEYDOWN and e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                app.scene = "menu"
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                if self.btn_continue.clicked(e.pos):
                    app.scene = "menu"
                elif self.btn_logout.clicked(e.pos):
                    app.log_out()
                elif self.btn_delete.clicked(e.pos, not self.working()):
                    if self.del_confirm_t > 0:
                        app.start_account_flow("delete", flow_delete(app.session))
                        self.del_confirm_t = 0.0
                    else:
                        self.del_confirm_t = 5.0
                        app.sfx("lose")
            return
        if self.working():
            return
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.submit()
            elif e.key == pygame.K_TAB:
                f = self.fields()
                self.focus = f[(f.index(self.focus) + 1) % len(f)] if self.focus in f else f[0]
            elif e.key == pygame.K_BACKSPACE:
                setattr(self, self.focus, getattr(self, self.focus)[:-1])
            elif e.unicode and e.unicode.isprintable():
                cur = getattr(self, self.focus)
                if self.focus == "name":
                    if (e.unicode.isalnum() or e.unicode in "_-") and e.unicode.isascii() and len(cur) < NAME_MAX:
                        self.name = cur + e.unicode
                elif len(cur) < 64:
                    setattr(self, self.focus, cur + e.unicode)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, r in enumerate(self.tabs):
                if r.collidepoint(e.pos):
                    self.mode = ("login", "signup")[i]
                    self.error = app.account_error = ""
                    if self.focus not in self.fields():
                        self.focus = "pw"
                    return
            for key in self.fields():
                if self.boxes[key].collidepoint(e.pos):
                    self.focus = key
                    return
            if self.check_remember.inflate(260, 10).collidepoint(e.pos) and self.check_remember.x < e.pos[0]:
                self.remember = not self.remember
            elif self.mode == "signup" and self.check_bring.inflate(420, 10).collidepoint(e.pos) and \
                    self.check_bring.x < e.pos[0]:
                self.bring = not self.bring
            elif self.btn_go.clicked(e.pos):
                self.submit()
            elif self.btn_guest.clicked(e.pos):
                app.guest = True
                app.scene = "menu"

    def update(self, dt):
        self.t += dt
        self.del_confirm_t = max(0.0, self.del_confirm_t - dt)

    def box(self, surf, key, label, secret):
        r = self.boxes[key]
        on = self.focus == key and not self.working()
        draw_text(surf, label, font(14, bold=True), (220, 200, 210), (r.x, r.y - 14), anchor="midleft")
        pygame.draw.rect(surf, (8, 6, 12), r, border_radius=10)
        pygame.draw.rect(surf, GOLD if on else (110, 90, 110), r, width=2, border_radius=10)
        value = getattr(self, key)
        shown = "*" * len(value) if secret else value
        if on and int(self.t * 2) % 2 == 0:
            shown += "|"
        draw_text(surf, shown, font(24, bold=True), WHITE, (r.x + 16, r.centery), anchor="midleft")

    def checkbox(self, surf, rect, on, label):
        pygame.draw.rect(surf, (8, 6, 12), rect, border_radius=6)
        pygame.draw.rect(surf, GOLD, rect, width=2, border_radius=6)
        if on:
            pygame.draw.lines(surf, GOLD, False, [(rect.x + 5, rect.centery), (rect.x + 11, rect.bottom - 6),
                                                  (rect.right - 5, rect.y + 5)], 3)
        draw_text(surf, label, font(15, bold=True), (220, 210, 220), (rect.right + 10, rect.centery), anchor="midleft")

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        app = self.app
        panel = pygame.Rect(W / 2 - 280, 76, 560, 620)
        soft_panel(surf, panel, 170, GOLD_DARK)
        draw_text(surf, "YOUR ACCOUNT", font(30, bold=True, serif=True), GOLD, (W / 2, 112))
        if app.session:
            draw_text(surf, "LOGGED IN AS", font(16, bold=True), (200, 190, 200), (W / 2, 200))
            draw_text(surf, app.session.name, font(40, bold=True), WHITE, (W / 2, 246))
            draw_text(surf, "Your chips and progress are saved online.", font(15), (190, 190, 200), (W / 2, 292))
            self.btn_continue.draw(surf, mouse)
            self.btn_logout.draw(surf, mouse)
            if self.working():
                self.btn_delete.text, self.btn_delete.hint = "DELETING...", None
            elif self.del_confirm_t > 0:
                self.btn_delete.text = f"SURE? CLICK AGAIN ({math.ceil(self.del_confirm_t)})"
                self.btn_delete.hint = "THIS CAN'T BE UNDONE"
            else:
                self.btn_delete.text, self.btn_delete.hint = "DELETE ACCOUNT", "CLICK TWICE TO CONFIRM"
            self.btn_delete.draw(surf, mouse, not self.working())
            draw_text(surf, "Log in with this username on any computer - or in the web version.", font(14),
                      (170, 170, 185), (W / 2, 600))
            if app.account_error:
                draw_pill(surf, app.account_error, font(15, bold=True), (W / 2, 704), (255, 200, 200),
                          (60, 10, 10, 230), (200, 80, 80), pad=(14, 4))
            app.draw_top_bar(surf, "ACCOUNT", lobby=True, show_help=False)
            return
        for i, (r, label) in enumerate(zip(self.tabs, ["LOG IN", "CREATE ACCOUNT"])):
            on = ("login", "signup")[i] == self.mode
            pygame.draw.rect(surf, (90, 70, 20) if on else (30, 24, 34), r, border_radius=10)
            pygame.draw.rect(surf, GOLD if on else (90, 80, 100), r, width=2, border_radius=10)
            draw_text(surf, label, font(17, bold=True), WHITE if on else (170, 160, 175), r.center)
        self.box(surf, "name", "USERNAME", False)
        self.box(surf, "pw", "PASSWORD", True)
        y = 486
        if self.mode == "signup":
            self.box(surf, "pw2", "TYPE THE PASSWORD AGAIN", True)
            y = 494
            self.check_bring.topleft = (W / 2 - 230, 510)
            self.checkbox(surf, self.check_bring, self.bring, "Start with my chips & trophies from this device")
            self.check_remember.topleft = (W / 2 - 230, 480)
            self.btn_go.rect.y = 552
        else:
            self.check_remember.topleft = (W / 2 - 230, y - 80)
            self.btn_go.rect.y = 470
        self.checkbox(surf, self.check_remember, self.remember, "Keep me logged in on this device")
        self.btn_go.text = "LOG IN" if self.mode == "login" else "CREATE ACCOUNT"
        if self.working():
            self.btn_go.text = "PLEASE WAIT" + "." * (int(self.t * 3) % 4)
        self.btn_go.draw(surf, mouse, not self.working())
        self.btn_guest.rect.y = self.btn_go.rect.bottom + (18 if self.mode == "login" else 10)
        self.btn_guest.draw(surf, mouse, not self.working())
        if self.error or app.account_error:
            draw_pill(surf, self.error or app.account_error, font(15, bold=True), (W / 2, 704),
                      (255, 200, 200), (60, 10, 10, 230), (200, 80, 80), pad=(14, 4))
        elif self.mode == "login":
            draw_text(surf, "New here? Press CREATE ACCOUNT.", font(14), (170, 170, 185),
                      (W / 2, self.btn_guest.rect.bottom + 24))
        app.draw_top_bar(surf, "ACCOUNT", lobby=True, show_help=False)


# --------------------------------------------------------------------------
# Live events and seasonal themes - switched on by the owner, for everyone (see supabase_casino_setup.sql)
# --------------------------------------------------------------------------
EVENT_KINDS = ["double", "rain", "jackpot"]
EVENT_NAMES = {"double": "DOUBLE PAYOUTS", "rain": "CHIP RAIN", "jackpot": "JACKPOT"}
EVENT_INFO = {"double": "Every win pays double!", "rain": "Click the falling chips to grab them!",
              "jackpot": "Three diamonds on the middle line of the slots pays 250x!"}
JACKPOT_SECONDS = 3 * 60 + 3
JACKPOT_PAY = 250
JACKPOT_CHANCE = 0.015           # during Jackpot, this share of slot spins land three diamonds in the middle
EVENT_POLL = 15.0                # seconds between checks for the owner's switches
RAIN_VALUES = [10, 25, 50, 100, 250, 500, 1000]
RAIN_WEIGHTS = [30, 25, 18, 12, 8, 5, 2]
THEMES = ["halloween", "winter", "valentines", "easter", "summer"]
THEME_NAMES = {"halloween": "HALLOWEEN", "winter": "WINTER", "valentines": "VALENTINE'S", "easter": "EASTER",
               "summer": "SUMMER", "": "NONE"}
THEME_GREETING = {"halloween": "HAPPY HALLOWEEN!", "winter": "HAPPY HOLIDAYS!", "valentines": "HAPPY VALENTINE'S DAY!",
                  "easter": "HAPPY EASTER!", "summer": "SUMMER VIBES!"}
THEME_LOOK = {   # colour grade (multiply, then add) and the accent colour
    "halloween": ((255, 200, 170), (16, 2, 20), (255, 140, 20)),
    "winter": ((205, 222, 255), (10, 14, 26), (170, 220, 255)),
    "valentines": ((255, 205, 222), (22, 0, 10), (255, 110, 160)),
    "easter": ((232, 255, 228), (10, 14, 6), (190, 150, 240)),
    "summer": ((255, 242, 205), (24, 14, 0), (255, 200, 60)),
}


def music_file(name):
    """Where the Jackpot song is: bundled with the .exe, or in the music folder next to casino.py."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    for fname in (name + ".ogg", name.capitalize() + ".mp3", name + ".mp3", name.capitalize() + ".wav", name + ".wav"):
        path = os.path.join(base, "music", fname)
        if os.path.exists(path):
            return path
    return None


def flow_owner(session, rpc, body):
    yield from flow_refresh(session)
    r = yield HttpReq("POST", f"/rest/v1/rpc/{rpc}", body, token=session.access)
    if r.status in (400, 401, 403) and "owner" in r.message().lower():
        raise AccountError("ONLY THE OWNER CAN DO THAT")
    need(r, "SENDING")
    return True


def draw_heart(surf, x, y, s, col):
    pygame.draw.circle(surf, col, (x - s * 0.5, y - s * 0.2), s * 0.55)
    pygame.draw.circle(surf, col, (x + s * 0.5, y - s * 0.2), s * 0.55)
    pygame.draw.polygon(surf, col, [(x - s * 1.02, y - s * 0.02), (x + s * 1.02, y - s * 0.02), (x, y + s * 1.05)])


class LiveEvents:
    """Checks Supabase for the owner's events and theme, and runs them on this computer."""

    def __init__(self, app):
        self.app = app
        self.ends = {k: 0.0 for k in EVENT_KINDS}
        self.was = {k: False for k in EVENT_KINDS}
        self.theme = ""                  # the theme the owner picked for everyone
        self.req = None
        self.poll_t = 0.0
        self.owner_flow = None
        self.owner_msg = ""
        self.rain = []
        self.rain_t = 0.0
        self.music = False
        self.t = 0.0

    def active(self, kind):
        return time.time() < self.ends[kind]

    def left(self, kind):
        return max(0.0, self.ends[kind] - time.time())

    def owner_action(self, rpc, body, done_msg):
        if not self.app.session:
            return
        self.owner_msg = "SENDING..."
        self.owner_flow = (Flow(flow_owner(self.app.session, rpc, body)), done_msg)

    def tick(self, dt):
        self.t += dt
        if self.req:
            if self.req.poll():
                if self.req.ok and isinstance(self.req.data, dict):
                    now = time.time()
                    for k in EVENT_KINDS:
                        left = float(self.req.data.get(k) or 0)
                        self.ends[k] = now + left if left > 0.5 else 0.0
                    self.theme = str(self.req.data.get("theme") or "")
                self.req = None
        else:
            self.poll_t -= dt
            if self.poll_t <= 0:
                self.poll_t = EVENT_POLL
                self.req = HttpReq("POST", "/rest/v1/rpc/casino_get_events", {})
        if self.owner_flow:
            flow, msg = self.owner_flow
            if flow.poll():
                self.owner_msg = flow.error or msg
                self.owner_flow = None
                self.poll_t = 0.0                 # see the change straight away
        for k in EVENT_KINDS:
            on = self.active(k)
            if on != self.was[k]:
                self.was[k] = on
                (self.started if on else self.ended)(k)
        # chip rain
        if self.active("rain"):
            self.rain_t -= dt
            while self.rain_t <= 0:
                self.rain_t += random.uniform(0.25, 0.55)
                self.rain.append({"x": random.uniform(40, W - 40), "y": -30.0, "vy": random.uniform(120, 230),
                                  "spin": random.uniform(0, 6), "vs": random.uniform(3, 7), "r": 24,
                                  "v": random.choices(RAIN_VALUES, RAIN_WEIGHTS)[0]})
        for c in self.rain:
            c["y"] += c["vy"] * dt
            c["spin"] += c["vs"] * dt
        self.rain = [c for c in self.rain if c["y"] < H + 40]
        # the Jackpot song follows the event (and stops if sound is switched off)
        want = self.active("jackpot") and self.app.sound_on
        if want != self.music:
            self.music = want
            if want:
                self.play_song()
            else:
                try:
                    pygame.mixer.music.fadeout(1200)
                except Exception:
                    pass

    def play_song(self):
        path = music_file("jackpot")
        if not path or not pygame.mixer.get_init():
            return
        try:
            pygame.mixer.music.load(path)
            into = max(0.0, JACKPOT_SECONDS - self.left("jackpot"))
            try:
                pygame.mixer.music.play(start=into)          # joined late: pick the song up where it is
            except Exception:
                pygame.mixer.music.play()
        except Exception:
            pass

    def started(self, kind):
        left = int(self.left(kind))
        self.app.effects.toast(f"EVENT: {EVENT_NAMES[kind]}!", f"{EVENT_INFO[kind]}  ({left // 60}:{left % 60:02d})",
                               "up")
        self.app.sfx("alert")

    def ended(self, kind):
        self.app.effects.toast(f"{EVENT_NAMES[kind]} IS OVER", "Thanks for playing!")
        if kind == "rain":
            self.rain = []

    def grab(self, pos):
        """Clicked a raining chip? It's yours."""
        for c in reversed(self.rain):
            if math.hypot(pos[0] - c["x"], pos[1] - c["y"]) <= c["r"] + 6:
                self.rain.remove(c)
                self.app.balance += c["v"]
                self.app.float_text(f"+{money(c['v'])}", (80, 230, 110))
                self.app.effects.burst(c["x"], c["y"], 14, [(255, 220, 90), (255, 255, 255)])
                self.app.sfx("chip")
                return True
        return False

    def draw(self, surf):
        for c in self.rain:
            w = max(2, int(2 * c["r"] * abs(math.cos(c["spin"]))))
            img = pygame.transform.smoothscale(self.app.assets.chip(c["v"], c["r"]), (w, 2 * c["r"]))
            surf.blit(img, img.get_rect(center=(c["x"], c["y"])))
        on = [k for k in EVENT_KINDS if self.active(k)]
        for i, k in enumerate(on):
            left = int(self.left(k))
            text = f"{EVENT_NAMES[k]}  {left // 60}:{left % 60:02d}"
            col = {"double": (120, 240, 140), "rain": (140, 200, 255), "jackpot": GOLD}[k]
            glow = 0.5 + 0.5 * math.sin(self.t * 5 + i)
            draw_pill(surf, text, font(14, bold=True), (W - 140, 76 + i * 30), (20, 15, 5) if k == "jackpot" else WHITE,
                      (*lerp_col(col, (255, 255, 255), glow * 0.2), 235) if k == "jackpot" else (0, 0, 0, 200),
                      col, pad=(12, 3))


# ---- seasonal decorations: a scene behind the lobby / main menu, corners in every game, and extra movers ----
def _pine(s, x, base, h, snow):
    for k in range(3):
        w = h * (0.55 - k * 0.12)
        top = base - h * (0.35 + k * 0.3)
        pygame.draw.polygon(s, (20, 70, 45), [(x - w, top + h * 0.38), (x + w, top + h * 0.38), (x, top)])
        if snow:
            pygame.draw.polygon(s, (240, 248, 255), [(x - w * 0.45, top + h * 0.17), (x + w * 0.45, top + h * 0.17), (x, top)])
    pygame.draw.rect(s, (70, 45, 30), (x - 5, base - h * 0.1, 10, h * 0.12))


def _flower(s, x, y, r, col):
    for i in range(5):
        a = i / 5 * 2 * math.pi
        pygame.draw.circle(s, col, (x + math.cos(a) * r, y + math.sin(a) * r), r * 0.75)
    pygame.draw.circle(s, (255, 220, 80), (x, y), r * 0.6)


def _web(s, cx, cy, size, flip):
    col = (230, 230, 240, 150)
    d = -1 if flip else 1
    ends = []
    for i in range(6):
        a = math.radians(i * 18)
        ends.append((cx + d * math.cos(a) * size, cy + math.sin(a) * size))
        pygame.draw.line(s, col, (cx, cy), ends[-1], 1)
    for ring in (0.3, 0.55, 0.8):
        pts = [(cx + (ex - cx) * ring, cy + (ey - cy) * ring) for ex, ey in ends]
        pygame.draw.lines(s, col, False, pts, 1)


def make_backdrop(name):
    """The scene behind the lobby and the main menu (it's drawn under the tiles and buttons)."""
    s = pygame.Surface((W, H), pygame.SRCALPHA)
    rng = random.Random(3)
    if name == "halloween":
        for i in range(12):                                   # moon glow
            pygame.draw.circle(s, (255, 240, 200, 10), (1150, 108), 90 - i * 4)
        pygame.draw.circle(s, (245, 235, 200), (1150, 108), 40)
        for cx, cy, r in ((1138, 98, 7), (1162, 118, 9), (1150, 90, 4)):
            pygame.draw.circle(s, (220, 210, 175), (cx, cy), r)
        for x in (70, 1215):                                  # dead trees
            pygame.draw.line(s, (40, 25, 45), (x, 720), (x, 470), 10)
            for k in range(4):
                y = 520 + k * 40
                d = 1 if k % 2 else -1
                pygame.draw.line(s, (40, 25, 45), (x, y), (x + d * 55, y - 45), 5)
                pygame.draw.line(s, (40, 25, 45), (x + d * 35, y - 28), (x + d * 45, y - 60), 3)
        pts = [(0, 720)] + [(x, 668 + 14 * math.sin(x / 90)) for x in range(0, W + 1, 40)] + [(W, 720)]
        pygame.draw.polygon(s, (32, 16, 38, 235), pts)
        for x in (190, 330, 950, 1090):                       # tombstones
            r = pygame.Rect(0, 0, 58, 74)
            r.midbottom = (x, 690)
            pygame.draw.rect(s, (110, 108, 122), r, border_top_left_radius=29, border_top_right_radius=29)
            draw_text(s, "RIP", font(15, bold=True), (60, 58, 70), (x, r.y + 30))
        for x in (260, 1020):                                 # pumpkins
            pygame.draw.ellipse(s, (230, 120, 20), (x - 22, 664, 44, 32))
            pygame.draw.rect(s, (70, 110, 30), (x - 3, 658, 6, 8))
        for i in range(6):                                    # fog
            pygame.draw.ellipse(s, (160, 110, 200, 22), (rng.uniform(-100, W - 200), rng.uniform(610, 690), 420, 60))
    elif name == "winter":
        for i in range(40):                                   # stars
            pygame.draw.circle(s, (255, 255, 255, 120), (rng.uniform(0, W), rng.uniform(60, 300)), 1)
        pts = [(0, 720)] + [(x, 660 + 18 * math.sin(x / 110 + 1)) for x in range(0, W + 1, 40)] + [(W, 720)]
        pygame.draw.polygon(s, (238, 246, 255, 240), pts)
        for x, h in ((55, 190), (150, 150), (1130, 160), (1225, 200)):
            _pine(s, x, 690, h, True)
        for x in (300, 985):                                  # snowmen
            for r, y in ((30, 668), (22, 626), (15, 594)):
                pygame.draw.circle(s, (250, 252, 255), (x, y), r)
                pygame.draw.circle(s, (200, 215, 235), (x, y), r, 2)
            pygame.draw.rect(s, (25, 25, 30), (x - 13, 566, 26, 16))
            pygame.draw.rect(s, (25, 25, 30), (x - 19, 580, 38, 5))
            pygame.draw.polygon(s, (240, 130, 30), [(x, 594), (x + 16, 597), (x, 600)])
            for ex in (-5, 5):
                pygame.draw.circle(s, (20, 20, 25), (x + ex, 589), 2)
            pygame.draw.rect(s, (200, 40, 40), (x - 20, 606, 40, 6))
    elif name == "valentines":
        for i in range(14):                                   # faint big hearts
            draw_heart(s, rng.uniform(0, W), rng.uniform(80, 640), rng.uniform(20, 45), (255, 120, 170, 26))
        for x in range(20, W, 46):                            # roses
            y = 690 + (x % 3) * 6
            pygame.draw.line(s, (40, 120, 50), (x, 720), (x, y), 3)
            pygame.draw.ellipse(s, (60, 150, 60), (x - 12, y + 10, 12, 7))
            pygame.draw.circle(s, (200, 20, 60), (x, y), 11)
            pygame.draw.circle(s, (240, 60, 100), (x - 2, y - 2), 6)
        for bx in (95, 1185):                                 # balloon bouquets
            for i, (dx, dy, col) in enumerate(((-26, 520, (230, 40, 80)), (0, 490, (255, 120, 170)),
                                                (26, 520, (255, 70, 120)))):
                pygame.draw.line(s, (240, 240, 240, 180), (bx + dx, dy + 24), (bx, 660), 1)
                draw_heart(s, bx + dx, dy, 20, col)
    elif name == "easter":
        pts = [(0, 720)] + [(x, 668 + 10 * math.sin(x / 70)) for x in range(0, W + 1, 30)] + [(W, 720)]
        pygame.draw.polygon(s, (110, 190, 90, 240), pts)
        for x in range(0, W, 9):                              # grass blades
            h = rng.uniform(10, 24)
            pygame.draw.line(s, (90, 170, 70), (x, 676), (x + rng.uniform(-4, 4), 676 - h), 2)
        for x in range(30, W, 70):
            _flower(s, x + rng.uniform(-15, 15), rng.uniform(672, 700), 6,
                    rng.choice([(255, 190, 220), (200, 180, 255), (255, 255, 255), (255, 230, 120)]))
        for x, col in ((420, (255, 190, 220)), (470, (190, 230, 255)), (860, (255, 240, 170))):
            pygame.draw.ellipse(s, col, (x - 12, 668, 24, 30))
        x = 150                                               # bunny
        pygame.draw.ellipse(s, (250, 250, 250), (x - 30, 620, 60, 62))
        pygame.draw.circle(s, (250, 250, 250), (x, 610), 22)
        for ex in (-9, 9):
            pygame.draw.ellipse(s, (250, 250, 250), (x + ex - 7, 548, 14, 52))
            pygame.draw.ellipse(s, (255, 190, 210), (x + ex - 4, 556, 8, 38))
            pygame.draw.circle(s, (30, 30, 30), (x + ex * 0.8, 606), 3)
        pygame.draw.circle(s, (255, 150, 180), (x, 614), 3)
        x = 1130                                              # chick
        pygame.draw.circle(s, (255, 220, 60), (x, 650), 24)
        pygame.draw.circle(s, (255, 225, 80), (x, 620), 16)
        pygame.draw.polygon(s, (240, 140, 30), [(x + 14, 618), (x + 26, 622), (x + 14, 626)])
        pygame.draw.circle(s, (30, 30, 30), (x + 6, 616), 3)
    elif name == "summer":
        for i in range(10):                                   # sun glow
            pygame.draw.circle(s, (255, 220, 120, 14), (1150, 110), 110 - i * 8)
        pygame.draw.circle(s, (255, 215, 80), (1150, 110), 44)
        pygame.draw.rect(s, (240, 215, 150, 240), (0, 690, W, 30))              # sand
        for y, col in ((650, (40, 140, 210, 230)), (668, (60, 170, 230, 235))):    # sea
            pts = [(0, 720)] + [(x, y + 6 * math.sin(x / 45)) for x in range(0, W + 1, 20)] + [(W, 720)]
            pygame.draw.polygon(s, col, pts)
        pygame.draw.rect(s, (240, 215, 150, 240), (0, 694, W, 26))
        for x, d in ((70, 1), (1210, -1)):                    # palm trees
            pts = [(x + d * (k * k * 0.05), 700 - k * 9) for k in range(0, 26)]
            pygame.draw.lines(s, (120, 80, 40), False, pts, 9)
            tx, ty = pts[-1]
            for a in range(0, 360, 50):
                ang = math.radians(a)
                pygame.draw.line(s, (40, 140, 60), (tx, ty), (tx + math.cos(ang) * 70, ty + math.sin(ang) * 30 + 12), 6)
        x = 330                                               # beach umbrella
        pygame.draw.line(s, (90, 70, 50), (x, 700), (x + 10, 610), 4)
        for i in range(6):
            a0, a1 = math.pi + i * math.pi / 6, math.pi + (i + 1) * math.pi / 6
            pts = [(x + 10, 612)] + [(x + 10 + math.cos(a) * 70, 612 + math.sin(a) * 40)
                                     for a in (a0 + (a1 - a0) * k / 6 for k in range(7))]
            pygame.draw.polygon(s, (230, 60, 60) if i % 2 else (255, 255, 255), pts)
        x = 990                                               # beach ball
        for i, col in enumerate(((230, 60, 60), (255, 255, 255), (60, 120, 230), (255, 210, 60))):
            pygame.draw.circle(s, col, (x, 684), 20, draw_top_left=i == 0, draw_top_right=i == 1,
                               draw_bottom_left=i == 2, draw_bottom_right=i == 3)
    return s


def make_corners(name):
    """Small decorations tucked into the top corners (under the top bar) in every game."""
    s = pygame.Surface((W, H), pygame.SRCALPHA)
    y0 = 58
    if name == "halloween":
        _web(s, 0, y0, 110, False)
        _web(s, W, y0, 110, True)
    elif name == "winter":
        for cx, cy, r in ((30, y0 + 34, 22), (74, y0 + 18, 12), (W - 30, y0 + 34, 22), (W - 74, y0 + 18, 12)):
            for k in range(6):
                a = k * math.pi / 3
                pygame.draw.line(s, (230, 245, 255, 200), (cx, cy), (cx + math.cos(a) * r, cy + math.sin(a) * r), 2)
                mx, my = cx + math.cos(a) * r * 0.55, cy + math.sin(a) * r * 0.55
                for side in (-0.6, 0.6):
                    pygame.draw.line(s, (230, 245, 255, 200), (mx, my),
                                     (mx + math.cos(a + side) * r * 0.3, my + math.sin(a + side) * r * 0.3), 2)
    elif name == "valentines":
        for cx in (34, W - 34):                               # ribbon bows
            cy = y0 + 22
            pygame.draw.polygon(s, (220, 30, 70), [(cx, cy), (cx - 26, cy - 14), (cx - 26, cy + 14)])
            pygame.draw.polygon(s, (220, 30, 70), [(cx, cy), (cx + 26, cy - 14), (cx + 26, cy + 14)])
            pygame.draw.line(s, (220, 30, 70), (cx, cy), (cx - 12, cy + 34), 5)
            pygame.draw.line(s, (220, 30, 70), (cx, cy), (cx + 12, cy + 34), 5)
            pygame.draw.circle(s, (255, 90, 130), (cx, cy), 7)
    elif name == "easter":
        for cx in (28, W - 28):
            for dx, dy, col in ((0, 26, (255, 190, 220)), (24 * (1 if cx < 640 else -1), 12, (200, 180, 255)),
                                (14 * (1 if cx < 640 else -1), 46, (255, 240, 150))):
                _flower(s, cx + dx, y0 + dy, 7, col)
    elif name == "summer":
        cx, cy = 44, y0 + 40
        for k in range(12):
            a = k * math.pi / 6
            pygame.draw.line(s, (255, 210, 80, 220), (cx + math.cos(a) * 30, cy + math.sin(a) * 30),
                             (cx + math.cos(a) * 44, cy + math.sin(a) * 44), 4)
        pygame.draw.circle(s, (255, 215, 80), (cx, cy), 24)
        pygame.draw.rect(s, (30, 30, 40), (cx - 16, cy - 6, 14, 8), border_radius=3)       # sunglasses
        pygame.draw.rect(s, (30, 30, 40), (cx + 2, cy - 6, 14, 8), border_radius=3)
        pygame.draw.line(s, (30, 30, 40), (cx - 2, cy - 3), (cx + 2, cy - 3), 2)
        pygame.draw.arc(s, (150, 80, 20), (cx - 9, cy - 2, 18, 12), math.pi + 0.3, 2 * math.pi - 0.3, 2)
    return s


def new_extra(name, y=None):
    """The bigger things drifting around: ghosts, snowflakes, balloons, butterflies, seagulls."""
    e = {"x": random.uniform(0, W), "y": random.uniform(80, H - 80) if y is None else y,
         "ph": random.uniform(0, 6.28), "s": random.uniform(0.8, 1.2)}
    if name == "halloween":
        e.update(x=random.choice([-60.0, W + 60.0]), vy=0.0)
        e["vx"] = random.uniform(25, 45) * (1 if e["x"] < 0 else -1)
    elif name == "winter":
        e.update(y=-40.0 if y is None else y, vx=random.uniform(-10, 10), vy=random.uniform(20, 40))
    elif name == "valentines":
        e.update(y=H + 60.0 if y is None else y, vx=random.uniform(-8, 8), vy=-random.uniform(28, 45),
                 col=random.choice([(230, 40, 80), (255, 120, 170), (255, 70, 120)]))
    elif name == "easter":
        e.update(vx=random.uniform(-40, 40), vy=random.uniform(-25, 25),
                 col=random.choice([(255, 170, 60), (140, 190, 255), (255, 140, 200), (200, 160, 255)]))
    else:
        e.update(x=random.choice([-50.0, W + 50.0]), y=random.uniform(70, 330), vy=0.0)
        e["vx"] = random.uniform(60, 110) * (1 if e["x"] < 0 else -1)
    return e


def draw_extra(surf, name, e, t):
    x, y, s = e["x"], e["y"], e["s"]
    if name == "halloween":                                   # a ghost
        yy = y + math.sin(t * 2 + e["ph"]) * 10
        g = pygame.Surface((60, 70), pygame.SRCALPHA)
        pygame.draw.circle(g, (245, 245, 255, 190), (30, 26), 22)
        pygame.draw.rect(g, (245, 245, 255, 190), (8, 26, 44, 28))
        for k in range(4):
            pygame.draw.circle(g, (245, 245, 255, 190), (13 + k * 11.5, 54), 6)
        for ex in (22, 38):
            pygame.draw.ellipse(g, (30, 30, 40), (ex - 4, 20, 8, 11))
        pygame.draw.ellipse(g, (30, 30, 40), (26, 36, 8, 8))
        if e["vx"] < 0:
            g = pygame.transform.flip(g, True, False)
        surf.blit(g, g.get_rect(center=(x, yy)))
    elif name == "winter":                                    # a big snowflake crystal
        r = 12 * s
        rot = t * 0.6 + e["ph"]
        for k in range(6):
            a = rot + k * math.pi / 3
            ex, ey = x + math.cos(a) * r, y + math.sin(a) * r
            pygame.draw.line(surf, (240, 250, 255), (x, y), (ex, ey), 2)
            mx, my = x + math.cos(a) * r * 0.6, y + math.sin(a) * r * 0.6
            for side in (-0.7, 0.7):
                pygame.draw.line(surf, (240, 250, 255), (mx, my), (mx + math.cos(a + side) * r * 0.35,
                                                                   my + math.sin(a + side) * r * 0.35), 1)
    elif name == "valentines":                                # a heart balloon on a string
        sway = math.sin(t * 1.4 + e["ph"]) * 8
        pts = [(x + sway, y + 18 * s)] + [(x + sway + math.sin(t * 2 + k) * 3, y + 18 * s + k * 9) for k in range(1, 7)]
        pygame.draw.lines(surf, (240, 240, 240), False, pts, 1)
        draw_heart(surf, x + sway, y, 16 * s, e["col"])
        pygame.draw.circle(surf, (255, 255, 255), (x + sway - 7 * s, y - 6 * s), 3)
    elif name == "easter":                                    # a butterfly
        flap = abs(math.sin(t * 10 + e["ph"]))
        w = 12 * s * (0.3 + 0.7 * flap)
        for side in (-1, 1):
            pygame.draw.ellipse(surf, e["col"], (x + (0 if side > 0 else -w), y - 10 * s, w, 12 * s))
            pygame.draw.ellipse(surf, e["col"], (x + (0 if side > 0 else -w * 0.8), y, w * 0.8, 9 * s))
        pygame.draw.line(surf, (40, 30, 30), (x, y - 9 * s), (x, y + 8 * s), 2)
    else:                                                     # a seagull
        flap = math.sin(t * 7 + e["ph"]) * 6 * s
        pygame.draw.lines(surf, (250, 250, 250), False, [(x - 16 * s, y - flap), (x - 7 * s, y - 4 * s), (x, y),
                                                          (x + 7 * s, y - 4 * s), (x + 16 * s, y - flap)], 3)


EXTRA_COUNT = {"halloween": 3, "winter": 7, "valentines": 4, "easter": 5, "summer": 4}


class ThemeFX:
    """Seasonal looks: a colour grade over the whole game, things falling or floating, and decorations."""

    def __init__(self, app):
        self.app = app
        self.name = ""
        self.parts = []
        self.extras = []
        self.t = 0.0
        self.overlay = self.backdrop = self.corners = None

    def current(self):
        mine = getattr(self.app, "theme_mine", "")
        if mine:
            return "" if mine == "none" else mine
        return self.app.events.theme if self.app.events.theme in THEMES else ""

    def reset(self, name):
        self.name = name
        self.parts = []
        self.extras = []
        self.overlay = self.backdrop = self.corners = None
        if not name:
            return
        self.backdrop = make_backdrop(name)
        self.corners = make_corners(name)
        for _ in range(EXTRA_COUNT[name]):
            e = new_extra(name, random.uniform(80, H - 120))
            e["x"] = random.uniform(60, W - 60)
            self.extras.append(e)
        n = {"winter": 110, "halloween": 9, "valentines": 22, "easter": 26, "summer": 30}[name]
        for _ in range(n):
            self.parts.append(self.new_part(random.uniform(0, H)))
        o = pygame.Surface((W, H), pygame.SRCALPHA)       # a soft glow around the edges in the theme's colour
        accent = THEME_LOOK[name][2]
        for i in range(40):
            k = i / 39
            pygame.draw.rect(o, (*accent, int(34 * (1 - k) ** 2)), (i * 6, i * 4, W - i * 12, H - i * 8), width=6)
        if name == "summer":
            for i in range(30):
                pygame.draw.circle(o, (255, 230, 120, 3), (60, 40), 420 - i * 13)
        self.overlay = o

    def new_part(self, y=None):
        n = self.name
        p = {"x": random.uniform(0, W), "y": -20.0 if y is None else y, "s": random.uniform(0.6, 1.4),
             "ph": random.uniform(0, 6.28)}
        if n == "winter":
            p.update(vy=random.uniform(30, 90), vx=random.uniform(-15, 15))
        elif n == "halloween":
            p.update(x=random.choice([-40.0, W + 40.0]), y=random.uniform(70, H * 0.6), vy=random.uniform(-10, 10))
            p["vx"] = random.uniform(70, 140) * (1 if p["x"] < 0 else -1)
        elif n == "valentines":
            p.update(y=H + 20.0 if y is None else y, vy=-random.uniform(25, 60), vx=0.0,
                     col=random.choice([(255, 90, 140), (230, 40, 80), (255, 170, 200)]))
        elif n == "easter":
            egg = random.random() < 0.3
            p.update(vy=random.uniform(30, 70), vx=random.uniform(10, 40), egg=egg,
                     col=random.choice([(255, 190, 220), (190, 230, 255), (255, 240, 170), (200, 255, 200),
                                        (225, 200, 255)]))
        else:
            p.update(y=H + 20.0 if y is None else y, vy=-random.uniform(20, 55), vx=random.uniform(-8, 8))
        return p

    def update(self, dt):
        global CURRENT_THEME
        self.t += dt
        name = self.current()
        if name != self.name:
            self.reset(name)
        CURRENT_THEME = self.name                 # the games dress their pieces up for the season
        for i, p in enumerate(self.parts):
            p["x"] += (p["vx"] + (math.sin(self.t * 1.3 + p["ph"]) * 18 if self.name in ("winter", "easter") else 0)) * dt
            p["y"] += p["vy"] * dt
            if p["y"] > H + 30 or p["y"] < -40 or p["x"] < -80 or p["x"] > W + 80:
                self.parts[i] = self.new_part()
        for i, e in enumerate(self.extras):
            if self.name == "easter" and random.random() < dt * 0.8:     # butterflies change their minds
                e["vx"], e["vy"] = random.uniform(-45, 45), random.uniform(-30, 30)
            e["x"] += e["vx"] * dt
            e["y"] += e["vy"] * dt
            if self.name == "easter":
                e["y"] = max(80, min(H - 100, e["y"]))
                if e["x"] < 20 or e["x"] > W - 20:
                    e["vx"] = -e["vx"]
            elif e["y"] > H + 60 or e["y"] < -80 or e["x"] < -90 or e["x"] > W + 90:
                self.extras[i] = new_extra(self.name)

    def draw_backdrop(self, surf):
        """The seasonal scene - the lobby and main menu draw this behind everything else."""
        if self.name and self.backdrop:
            surf.blit(self.backdrop, (0, 0))

    def draw(self, surf, scene):
        if not self.name:
            return
        mult, add, accent = THEME_LOOK[self.name]
        surf.fill(mult, special_flags=pygame.BLEND_RGB_MULT)
        surf.fill(add, special_flags=pygame.BLEND_RGB_ADD)
        surf.blit(self.overlay, (0, 0))
        if scene not in ("title", "menu"):
            surf.blit(self.corners, (0, 0))
        for p in self.parts:
            self.draw_part(surf, p)
        for e in self.extras:
            draw_extra(surf, self.name, e, self.t)
        self.draw_garland(surf, 0 if scene == "title" else 56)
        if self.name == "halloween" and scene != "title":             # a spider going up and down its thread
            sy = 56 + 70 + math.sin(self.t * 0.9) * 40
            sx = 64
            pygame.draw.line(surf, (220, 220, 230), (sx, 56), (sx, sy), 1)
            for side in (-1, 1):
                for k in range(4):
                    ky = sy - 4 + k * 3
                    pygame.draw.lines(surf, (20, 10, 25), False, [(sx, ky), (sx + side * 9, ky - 5 + k * 2),
                                                                  (sx + side * 14, ky + 3 + k * 2)], 2)
            pygame.draw.circle(surf, (20, 10, 25), (sx, sy), 7)
            pygame.draw.circle(surf, (20, 10, 25), (sx, sy - 8), 4)
            pygame.draw.circle(surf, (230, 30, 30), (sx - 2, sy - 9), 1)
            pygame.draw.circle(surf, (230, 30, 30), (sx + 2, sy - 9), 1)
        if scene in ("menu", "title"):
            draw_pill(surf, THEME_GREETING[self.name], font(18, bold=True), (250, 106) if scene == "menu" else (W / 2, 22),
                      WHITE, (*[int(c * 0.35) for c in accent], 220), accent, pad=(16, 4))

    def draw_part(self, surf, p):
        x, y, s = p["x"], p["y"], p["s"]
        n = self.name
        if n == "winter":
            pygame.draw.circle(surf, (245, 250, 255), (x, y), 1.5 + 2.2 * s)
        elif n == "halloween":
            flap = math.sin(self.t * 12 + p["ph"]) * 10 * s
            d = 1 if p["vx"] > 0 else -1
            body = (18, 10, 24)
            pygame.draw.ellipse(surf, body, (x - 7 * s, y - 5 * s, 14 * s, 10 * s))
            for side in (-1, 1):
                tip = (x + side * 26 * s, y - flap)
                pygame.draw.polygon(surf, body, [(x + side * 4 * s, y - 3 * s), tip,
                                                 (x + side * 16 * s, y + 4 * s - flap * 0.3), (x + side * 8 * s, y + 5 * s)])
            pygame.draw.circle(surf, (255, 160, 40), (x + d * 3 * s, y - 2 * s), max(1, s * 1.4))
        elif n == "valentines":
            draw_heart(surf, x + math.sin(self.t * 1.5 + p["ph"]) * 14, y, 9 * s, p["col"])
        elif n == "easter":
            if p["egg"]:
                r = pygame.Rect(0, 0, 16 * s, 21 * s)
                r.center = (x, y)
                pygame.draw.ellipse(surf, p["col"], r)
                pygame.draw.line(surf, (255, 255, 255), (r.x + 2, r.centery), (r.right - 2, r.centery), 2)
            else:
                pygame.draw.ellipse(surf, p["col"], (x - 6 * s, y - 3 * s, 12 * s, 6 * s))
        else:
            r = 5 + 6 * s
            pygame.draw.circle(surf, (255, 255, 255), (x, y), r, 1)
            pygame.draw.circle(surf, (255, 250, 220), (x - r * 0.35, y - r * 0.35), max(1, r * 0.25))

    def draw_garland(self, surf, y):
        n = self.name
        if n == "winter":                                    # icicles and a line of snow
            pygame.draw.rect(surf, (240, 248, 255), (0, y - 3, W, 6), border_radius=3)
            rng = random.Random(7)
            for x in range(6, W, 22):
                ln = rng.uniform(8, 26)
                pygame.draw.polygon(surf, (215, 235, 255), [(x - 5, y + 2), (x + 5, y + 2), (x, y + ln)])
            cols = [(255, 70, 70), (80, 220, 90), (255, 210, 60), (80, 160, 255)]     # twinkling lights
            pts = [(x, y + 10 + 8 * abs(math.sin(x / 88 * math.pi))) for x in range(0, W + 1, 22)]
            pygame.draw.lines(surf, (40, 60, 40), False, pts, 2)
            for i, (bx, by) in enumerate(pts[1::2]):
                on = (i + int(self.t * 3)) % 3 != 0
                col = cols[i % 4] if on else tuple(c // 3 for c in cols[i % 4])
                pygame.draw.ellipse(surf, col, (bx - 4, by, 8, 11))
            return
        pygame.draw.line(surf, (60, 50, 40), (0, y + 2), (W, y + 2), 2)
        if n == "summer":                                    # party flags
            cols = [(255, 90, 90), (255, 200, 60), (80, 200, 255), (120, 230, 120), (255, 140, 220)]
            for i, x in enumerate(range(10, W, 44)):
                pygame.draw.polygon(surf, cols[i % len(cols)], [(x, y + 2), (x + 34, y + 2), (x + 17, y + 26)])
            return
        for i, x in enumerate(range(40, W, 110)):
            sway = math.sin(self.t * 2 + i) * 2
            cy = y + 14 + sway
            pygame.draw.line(surf, (60, 50, 40), (x, y + 2), (x, cy - 8), 1)
            if n == "halloween":                             # jack-o'-lanterns
                pygame.draw.ellipse(surf, (230, 120, 20), (x - 12, cy - 9, 24, 19))
                pygame.draw.rect(surf, (70, 110, 30), (x - 2, cy - 13, 4, 5))
                for ex in (-5, 5):
                    pygame.draw.polygon(surf, (40, 15, 0), [(x + ex - 3, cy - 1), (x + ex + 3, cy - 1), (x + ex, cy - 5)])
                pygame.draw.line(surf, (40, 15, 0), (x - 6, cy + 4), (x + 6, cy + 4), 2)
            elif n == "valentines":
                draw_heart(surf, x, cy, 9, (230, 40, 80) if i % 2 else (255, 120, 170))
            elif n == "easter":
                col = [(255, 190, 220), (190, 230, 255), (255, 240, 170), (200, 255, 200)][i % 4]
                pygame.draw.ellipse(surf, col, (x - 9, cy - 11, 18, 23))
                pygame.draw.line(surf, (255, 255, 255), (x - 8, cy), (x + 8, cy), 3)


class OwnerMenu:
    """The ` menu - only for accounts marked is_owner in Supabase."""
    PANEL = pygame.Rect(170, 60, 940, 600)

    def __init__(self, app):
        self.app = app
        self.active = False
        self.money_text = ""
        self.editing = False
        self.minutes = {"double": 10, "rain": 5}
        self.scope = "everyone"
        P = self.PANEL
        self.money_box = pygame.Rect(P.x + 40, P.y + 104, 300, 50)
        self.btn_set = Button((P.x + 352, P.y + 104, 110, 50), "SET", (25, 120, 60), 20)
        self.btn_quick = [(Button((P.x + 476 + i * 106, P.y + 104, 98, 50), label, (60, 60, 90), 17), amt)
                          for i, (label, amt) in enumerate((("+10K", 10000), ("+1M", 10 ** 6), ("$500", None)))]
        self.rows = {}
        for i, k in enumerate(EVENT_KINDS):
            y = P.y + 230 + i * 70
            self.rows[k] = {"minus": Button((P.x + 330, y, 44, 48), "-", (60, 60, 90), 22),
                            "plus": Button((P.x + 470, y, 44, 48), "+", (60, 60, 90), 22),
                            "start": Button((P.x + 540, y, 150, 48), "START", (25, 120, 60), 18),
                            "stop": Button((P.x + 700, y, 110, 48), "STOP", (150, 35, 40), 18), "y": y}
        self.theme_btns = [(Button((P.x + 40 + i * 146, P.y + 490, 138, 48), THEME_NAMES[t], (70, 60, 100), 15), t)
                           for i, t in enumerate([""] + THEMES)]
        self.scope_btns = [(Button((P.x + 560 + i * 170, P.y + 430, 160, 40), label, (40, 60, 110), 15), s)
                           for i, (label, s) in enumerate((("EVERYONE", "everyone"), ("JUST ME", "me")))]
        self.btn_close = Button((P.right - 60, P.y + 16, 44, 40), "X", (120, 35, 40), 20)

    def allowed(self):
        return bool(self.app.session and self.app.session.is_owner)

    def toggle(self):
        self.active = not self.active and self.allowed()
        self.editing = False

    def handle(self, e):
        app, ev = self.app, self.app.events
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_BACKQUOTE, pygame.K_ESCAPE):
                self.active = False
            elif self.editing:
                if e.key == pygame.K_BACKSPACE:
                    self.money_text = self.money_text[:-1]
                elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.set_money()
                elif e.unicode and e.unicode.isdigit() and len(self.money_text) < 13:
                    self.money_text += e.unicode
            return
        if e.type != pygame.MOUSEBUTTONDOWN or e.button != 1:
            return
        pos = e.pos
        self.editing = self.money_box.collidepoint(pos)
        if self.btn_close.clicked(pos):
            self.active = False
        elif self.btn_set.clicked(pos):
            self.set_money()
        for b, amt in self.btn_quick:
            if b.clicked(pos):
                app.balance = START_BALANCE if amt is None else app.balance + amt
                app.save()
                ev.owner_msg = f"YOUR CHIPS: {money(app.balance)}"
        busy = bool(ev.owner_flow)
        for k, row in self.rows.items():
            if k in self.minutes:
                if row["minus"].clicked(pos):
                    self.minutes[k] = max(1, self.minutes[k] - (5 if self.minutes[k] > 5 else 1))
                elif row["plus"].clicked(pos):
                    self.minutes[k] = min(240, self.minutes[k] + (5 if self.minutes[k] >= 5 else 1))
            secs = JACKPOT_SECONDS if k == "jackpot" else self.minutes[k] * 60
            if row["start"].clicked(pos, not busy):
                ev.owner_action("casino_owner_event", {"which": k, "seconds": secs},
                                f"{EVENT_NAMES[k]} STARTED FOR EVERYONE")
            elif row["stop"].clicked(pos, not busy):
                ev.owner_action("casino_owner_event", {"which": k, "seconds": 0}, f"{EVENT_NAMES[k]} STOPPED")
        for b, s in self.scope_btns:
            if b.clicked(pos):
                self.scope = s
        for b, t in self.theme_btns:
            if b.clicked(pos, not busy or self.scope == "me"):
                if self.scope == "me":
                    app.theme_mine = t or "none"
                    app.save()
                    ev.owner_msg = f"THEME FOR YOU ONLY: {THEME_NAMES[t]}"
                else:
                    app.theme_mine = ""
                    app.save()
                    ev.owner_action("casino_owner_theme", {"new_theme": t}, f"THEME FOR EVERYONE: {THEME_NAMES[t]}")

    def set_money(self):
        if self.money_text:
            self.app.balance = min(10 ** 15, int(self.money_text))
            self.app.save()
            self.app.events.owner_msg = f"YOUR CHIPS SET TO {money(self.app.balance)}"
            self.money_text = ""
        self.editing = False

    def draw(self, surf):
        app, ev = self.app, self.app.events
        mouse = pygame.mouse.get_pos()
        dim = pygame.Surface((W, H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 170))
        surf.blit(dim, (0, 0))
        P = self.PANEL
        pygame.draw.rect(surf, (18, 12, 22), P, border_radius=18)
        pygame.draw.rect(surf, GOLD, P, width=3, border_radius=18)
        draw_text(surf, "OWNER MENU", font(28, bold=True, serif=True), GOLD, (P.x + 40, P.y + 36), anchor="midleft")
        draw_text(surf, f"{app.session.name}  -  events and themes reach every player within ~15 seconds",
                  font(13), (190, 180, 200), (P.x + 40, P.y + 64), anchor="midleft")
        self.btn_close.draw(surf, mouse)
        # money
        draw_text(surf, "MY CHIPS", font(16, bold=True), GOLD, (P.x + 40, P.y + 86), anchor="midleft")
        pygame.draw.rect(surf, (6, 6, 12), self.money_box, border_radius=10)
        pygame.draw.rect(surf, GOLD if self.editing else (100, 90, 120), self.money_box, width=2, border_radius=10)
        shown = self.money_text + ("|" if self.editing and int(time.time() * 2) % 2 == 0 else "")
        draw_text(surf, shown or f"now {money(app.balance)}", font(20, bold=True), WHITE if shown else (140, 140, 160),
                  (self.money_box.x + 14, self.money_box.centery), anchor="midleft")
        self.btn_set.draw(surf, mouse, bool(self.money_text))
        for b, _ in self.btn_quick:
            b.draw(surf, mouse)
        # events
        draw_text(surf, "EVENTS  (FOR EVERYONE)", font(16, bold=True), GOLD, (P.x + 40, P.y + 200), anchor="midleft")
        busy = bool(ev.owner_flow)
        for k, row in self.rows.items():
            y = row["y"]
            on = ev.active(k)
            draw_text(surf, EVENT_NAMES[k], font(18, bold=True), WHITE, (P.x + 40, y + 16), anchor="midleft")
            left = int(ev.left(k))
            draw_text(surf, f"ON  {left // 60}:{left % 60:02d}" if on else "off", font(13, bold=True),
                      (120, 240, 140) if on else (150, 140, 160), (P.x + 40, y + 38), anchor="midleft")
            if k in self.minutes:
                row["minus"].draw(surf, mouse)
                row["plus"].draw(surf, mouse)
                draw_text(surf, f"{self.minutes[k]} MIN", font(18, bold=True), WHITE, (P.x + 422, y + 24))
            else:
                draw_text(surf, "3:03 + SONG", font(16, bold=True), GOLD, (P.x + 422, y + 24))
            row["start"].draw(surf, mouse, not busy)
            row["stop"].draw(surf, mouse, not busy and on)
        # themes
        draw_text(surf, "THEME", font(16, bold=True), GOLD, (P.x + 40, P.y + 450), anchor="midleft")
        mine = app.theme_mine
        now = f"everyone: {THEME_NAMES.get(ev.theme, 'NONE')}" + (f"   |   just you: {THEME_NAMES.get('' if mine == 'none' else mine, 'NONE')}"
                                                                   if mine else "")
        draw_text(surf, now, font(13), (190, 180, 200), (P.x + 120, P.y + 450), anchor="midleft")
        for b, s in self.scope_btns:
            b.color = (200, 150, 30) if s == self.scope else (40, 60, 110)
            b.draw(surf, mouse)
        current = app.themefx.current()
        for b, t in self.theme_btns:
            b.color = (200, 150, 30) if t == current else (70, 60, 100)
            b.draw(surf, mouse, not busy or self.scope == "me")
        if ev.owner_msg:
            bad = "ONLY" in ev.owner_msg or "FAILED" in ev.owner_msg or "CAN'T" in ev.owner_msg or "ISN'T" in ev.owner_msg
            draw_text(surf, ev.owner_msg, font(15, bold=True), (240, 130, 130) if bad else (140, 230, 160),
                      (P.centerx, P.bottom - 24))


# --------------------------------------------------------------------------
# Seasonal game pieces: card backs, balls, coins, dice, slot symbols, gems and mines, the chicken's costume
# --------------------------------------------------------------------------
CURRENT_THEME = ""            # the seasonal look showing right now (set every frame by ThemeFX)
_theme_cache = {}


def themed(key, make):
    """A seasonal picture, made once per theme and kept."""
    k = (CURRENT_THEME,) + tuple(key)
    if k not in _theme_cache:
        _theme_cache[k] = make()
    return _theme_cache[k]


def theme_icon(surf, theme, cx, cy, r, alt=False):
    """Each season's two emblems: pumpkin/bat, snowflake/snowman, heart/rose, egg/bunny, sun/palm tree."""
    if theme == "halloween" and not alt:                          # jack-o'-lantern
        pygame.draw.ellipse(surf, (230, 115, 20), (cx - r, cy - r * 0.8, 2 * r, 1.7 * r))
        for dx in (-0.45, 0.45):
            pygame.draw.ellipse(surf, (205, 95, 10), (cx + dx * r - r * 0.25, cy - r * 0.8, r * 0.5, 1.7 * r), 2)
        pygame.draw.rect(surf, (80, 120, 30), (cx - r * 0.12, cy - r * 1.05, r * 0.24, r * 0.32))
        for ex in (-0.38, 0.38):
            pygame.draw.polygon(surf, (50, 20, 0), [(cx + ex * r - r * 0.16, cy - r * 0.05), (cx + ex * r + r * 0.16, cy - r * 0.05),
                                                   (cx + ex * r, cy - r * 0.35)])
        pygame.draw.polygon(surf, (50, 20, 0), [(cx - r * 0.5, cy + r * 0.25), (cx + r * 0.5, cy + r * 0.25),
                                               (cx + r * 0.3, cy + r * 0.5), (cx - r * 0.3, cy + r * 0.5)])
    elif theme == "halloween":                                    # bat
        body = (25, 15, 30)
        pygame.draw.ellipse(surf, body, (cx - r * 0.25, cy - r * 0.35, r * 0.5, r * 0.75))
        for side in (-1, 1):
            pygame.draw.polygon(surf, body, [(cx + side * r * 0.15, cy - r * 0.2), (cx + side * r, cy - r * 0.55),
                                             (cx + side * r * 0.8, cy + r * 0.05), (cx + side * r * 0.55, cy - r * 0.05),
                                             (cx + side * r * 0.4, cy + r * 0.3), (cx + side * r * 0.2, cy + r * 0.2)])
            pygame.draw.polygon(surf, body, [(cx + side * r * 0.05, cy - r * 0.3), (cx + side * r * 0.22, cy - r * 0.55),
                                             (cx + side * r * 0.2, cy - r * 0.25)])
        for side in (-1, 1):
            pygame.draw.circle(surf, (255, 170, 40), (cx + side * r * 0.1, cy - r * 0.15), max(1, r * 0.07))
    elif theme == "winter" and not alt:                           # snowflake
        for k in range(6):
            a = k * math.pi / 3
            ex, ey = cx + math.cos(a) * r, cy + math.sin(a) * r
            pygame.draw.line(surf, (235, 248, 255), (cx, cy), (ex, ey), max(2, int(r * 0.12)))
            mx, my = cx + math.cos(a) * r * 0.6, cy + math.sin(a) * r * 0.6
            for side in (-0.7, 0.7):
                pygame.draw.line(surf, (235, 248, 255), (mx, my), (mx + math.cos(a + side) * r * 0.35,
                                                                   my + math.sin(a + side) * r * 0.35), max(1, int(r * 0.08)))
    elif theme == "winter":                                       # snowman
        for rr, yy in ((0.55, 0.4), (0.4, -0.35)):
            pygame.draw.circle(surf, (250, 252, 255), (cx, cy + yy * r), rr * r)
            pygame.draw.circle(surf, (190, 205, 225), (cx, cy + yy * r), rr * r, 2)
        pygame.draw.rect(surf, (25, 25, 35), (cx - r * 0.28, cy - r * 1.05, r * 0.56, r * 0.35))
        pygame.draw.rect(surf, (25, 25, 35), (cx - r * 0.42, cy - r * 0.74, r * 0.84, r * 0.1))
        pygame.draw.polygon(surf, (240, 130, 30), [(cx, cy - r * 0.35), (cx + r * 0.35, cy - r * 0.3), (cx, cy - r * 0.25)])
    elif theme == "valentines" and not alt:
        draw_heart(surf, cx, cy - r * 0.1, r * 0.95, (225, 35, 75))
        pygame.draw.circle(surf, (255, 150, 180), (cx - r * 0.45, cy - r * 0.45), r * 0.18)
    elif theme == "valentines":                                   # rose
        pygame.draw.line(surf, (40, 130, 50), (cx, cy), (cx, cy + r), max(2, int(r * 0.12)))
        pygame.draw.ellipse(surf, (50, 150, 60), (cx, cy + r * 0.35, r * 0.5, r * 0.25))
        pygame.draw.circle(surf, (195, 20, 55), (cx, cy - r * 0.2), r * 0.6)
        pygame.draw.circle(surf, (235, 60, 95), (cx - r * 0.1, cy - r * 0.3), r * 0.38)
        pygame.draw.arc(surf, (150, 10, 40), (cx - r * 0.35, cy - r * 0.55, r * 0.7, r * 0.6), 0.3, 3.5, 2)
    elif theme == "easter" and not alt:                           # painted egg
        rect = pygame.Rect(0, 0, r * 1.5, r * 1.95)
        rect.center = (cx, cy)
        pygame.draw.ellipse(surf, (255, 185, 215), rect)
        for k, col in ((0.38, (150, 200, 255)), (0.62, (255, 235, 120))):
            y = rect.y + rect.h * k
            pts = [(rect.x + rect.w * (i / 8), y + (4 if i % 2 else -4) * r / 30) for i in range(9)]
            pygame.draw.lines(surf, col, False, pts, max(2, int(r * 0.14)))
        pygame.draw.ellipse(surf, (230, 150, 185), rect, 2)
    elif theme == "easter":                                       # bunny
        for ex in (-0.3, 0.3):
            pygame.draw.ellipse(surf, (250, 250, 250), (cx + ex * r - r * 0.17, cy - r * 1.15, r * 0.34, r * 0.9))
            pygame.draw.ellipse(surf, (255, 190, 210), (cx + ex * r - r * 0.08, cy - r * 1.0, r * 0.16, r * 0.62))
        pygame.draw.circle(surf, (250, 250, 250), (cx, cy + r * 0.1), r * 0.62)
        for ex in (-0.22, 0.22):
            pygame.draw.circle(surf, (30, 30, 30), (cx + ex * r, cy), max(1, r * 0.08))
        pygame.draw.circle(surf, (255, 140, 170), (cx, cy + r * 0.15), max(1, r * 0.09))
    elif theme == "summer" and not alt:                           # sun
        for k in range(10):
            a = k * math.pi / 5
            pygame.draw.line(surf, (255, 200, 40), (cx + math.cos(a) * r * 0.62, cy + math.sin(a) * r * 0.62),
                             (cx + math.cos(a) * r, cy + math.sin(a) * r), max(2, int(r * 0.12)))
        pygame.draw.circle(surf, (255, 215, 70), (cx, cy), r * 0.55)
        pygame.draw.circle(surf, (255, 240, 150), (cx - r * 0.15, cy - r * 0.15), r * 0.22)
    elif theme == "summer":                                       # palm tree
        pts = [(cx - r * 0.1 + (k / 10) ** 2 * r * 0.35, cy + r - k * r * 0.16) for k in range(11)]
        pygame.draw.lines(surf, (130, 85, 40), False, pts, max(3, int(r * 0.16)))
        tx, ty = pts[-1]
        for a in range(0, 360, 60):
            ang = math.radians(a + 20)
            pygame.draw.line(surf, (40, 150, 60), (tx, ty), (tx + math.cos(ang) * r * 0.8,
                                                            ty + math.sin(ang) * r * 0.4 + r * 0.12), max(2, int(r * 0.13)))
        pygame.draw.circle(surf, (110, 70, 30), (tx - r * 0.05, ty + r * 0.1), r * 0.1)


# ---- card backs -----------------------------------------------------------------------
THEME_BACK = {"halloween": ((50, 20, 60), (255, 140, 20)), "winter": ((25, 55, 110), (220, 240, 255)),
              "valentines": ((170, 20, 55), (255, 190, 210)), "easter": ((150, 120, 210), (255, 240, 170)),
              "summer": ((20, 150, 180), (255, 220, 90))}


def make_themed_back(theme):
    k = SS
    w, h = CW * k, CH * k
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(s, (250, 250, 246), (0, 0, w, h), border_radius=8 * k)
    pygame.draw.rect(s, (170, 170, 165), (0, 0, w, h), width=k, border_radius=8 * k)
    m = 5 * k
    bg, trim = THEME_BACK[theme]
    pat = pygame.Surface((w - 2 * m, h - 2 * m), pygame.SRCALPHA)
    pat.fill((*bg, 255))
    iw, ih = pat.get_size()
    step = 14 * k
    for row, y in enumerate(range(step // 2, ih, step)):
        for x in range(step // 2 + (row % 2) * step // 2, iw, step):
            theme_icon(pat, theme, x, y, 3.6 * k, alt=(row % 2 == 1))
    pygame.draw.rect(pat, trim, pat.get_rect().inflate(-6 * k, -6 * k), width=k, border_radius=4 * k)
    pygame.draw.circle(pat, bg, (iw / 2, ih / 2), 17 * k)
    pygame.draw.circle(pat, trim, (iw / 2, ih / 2), 17 * k, width=k)
    theme_icon(pat, theme, iw / 2, ih / 2, 11 * k)
    mask = pygame.Surface((iw, ih), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=5 * k)
    pat.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    s.blit(pat, (m, m))
    return pygame.transform.smoothscale(s, (CW, CH))


# ---- balls (cups, plinko, pinball, roulette) -------------------------------------------
def draw_theme_ball(surf, x, y, r):
    """Draw the season's ball instead of a plain one. Returns False when no theme is on."""
    t = CURRENT_THEME
    if not t:
        return False

    def make():
        R = int(r * 3)
        s = pygame.Surface((R * 2 + 8, R * 2 + 8), pygame.SRCALPHA)
        c = R + 4
        if t == "halloween":
            theme_icon(s, t, c, c + R * 0.1, R)
        elif t == "winter":
            pygame.draw.circle(s, (225, 238, 250), (c, c), R)
            pygame.draw.circle(s, (250, 253, 255), (c - R * 0.15, c - R * 0.15), R * 0.8)
            for dx, dy in ((0.3, 0.2), (-0.35, 0.3), (0.1, -0.4)):
                pygame.draw.circle(s, (205, 222, 240), (c + dx * R, c + dy * R), R * 0.12)
        elif t == "valentines":
            draw_heart(s, c, c - R * 0.12, R, (225, 35, 75))
            pygame.draw.circle(s, (255, 170, 195), (c - R * 0.42, c - R * 0.42), R * 0.2)
        elif t == "easter":
            theme_icon(s, t, c, c, R * 1.02)
        else:                                                   # beach ball
            for i, col in enumerate(((230, 60, 60), (255, 255, 255), (60, 130, 230), (255, 210, 60))):
                pygame.draw.circle(s, col, (c, c), R, draw_top_left=i == 0, draw_top_right=i == 1,
                                   draw_bottom_left=i == 3, draw_bottom_right=i == 2)
            pygame.draw.circle(s, (255, 255, 255), (c, c), R * 0.18)
            pygame.draw.circle(s, (40, 40, 50), (c, c), R, 2)
        return pygame.transform.smoothscale(s, (int(r * 2 + 3), int(r * 2 + 3)))

    img = themed(("ball", int(r)), make)
    surf.blit(img, img.get_rect(center=(x, y)))
    return True


# ---- cups ---------------------------------------------------------------------------------
CUP_THEME = {   # body, shine, stripes, rim (dark), rim (light), base
    "halloween": ((215, 105, 15), (250, 160, 60), (30, 15, 30), (150, 70, 10), (235, 130, 40), (120, 55, 5)),
    "winter": ((60, 120, 200), (130, 180, 240), (245, 250, 255), (35, 80, 150), (90, 150, 220), (30, 70, 130)),
    "valentines": ((230, 80, 130), (255, 150, 185), (255, 240, 245), (170, 40, 90), (245, 120, 160), (150, 30, 75)),
    "easter": ((165, 135, 225), (205, 185, 250), (255, 240, 150), (120, 95, 180), (185, 160, 240), (105, 80, 160)),
    "summer": ((30, 165, 195), (110, 215, 235), (255, 220, 80), (20, 115, 140), (60, 190, 215), (15, 100, 125)),
}

# ---- coin flip faces ----------------------------------------------------------------------
COIN_THEME = {   # rim, face dark, face light, ring, ink
    "halloween": ((140, 60, 10), (225, 120, 30), (255, 185, 90), (170, 80, 15), (60, 25, 5)),
    "winter": ((110, 130, 160), (190, 210, 235), (240, 248, 255), (140, 165, 200), (40, 70, 120)),
    "valentines": ((150, 40, 75), (235, 120, 155), (255, 195, 215), (190, 70, 110), (110, 15, 50)),
    "easter": ((150, 125, 200), (215, 195, 250), (250, 240, 255), (175, 150, 225), (90, 60, 140)),
    "summer": ((170, 110, 20), (240, 190, 60), (255, 235, 140), (200, 140, 30), (120, 70, 10)),
}


def make_theme_coin_face(kind, r, theme):
    rim, dark, light, ring, ink = COIN_THEME[theme]
    k = 2
    R = r * k
    s = pygame.Surface((2 * R, 2 * R), pygame.SRCALPHA)
    pygame.draw.circle(s, rim, (R, R), R)
    for i in range(20):
        t = i / 19
        pygame.draw.circle(s, lerp_col(dark, light, t), (R - t * R * 0.15, R - t * R * 0.15), int(R * 0.94 * (1 - t * 0.25)))
    pygame.draw.circle(s, ring, (R, R), int(R * 0.80), 3 * k)
    for i in range(48):
        a = i / 48 * 2 * math.pi
        pygame.draw.line(s, ring, (R + math.cos(a) * R * 0.9, R + math.sin(a) * R * 0.9),
                         (R + math.cos(a) * R * 0.99, R + math.sin(a) * R * 0.99), 2 * k)
    theme_icon(s, theme, R, R - R * 0.1, R * 0.42, alt=(kind == "tails"))
    draw_text(s, kind.upper(), font(R * 0.22, bold=True, serif=True), ink, (R, R + R * 0.55))
    return pygame.transform.smoothscale(s, (2 * r, 2 * r))


# ---- dice -----------------------------------------------------------------------------------
DIE_THEME = {   # edge, face, shine, pips
    "halloween": ((130, 55, 5), (230, 115, 20), (250, 160, 70), (30, 10, 30)),
    "winter": ((120, 150, 190), (225, 238, 252), (250, 253, 255), (30, 70, 140)),
    "valentines": ((160, 30, 70), (240, 110, 150), (255, 170, 200), (255, 255, 255)),
    "easter": ((130, 105, 190), (195, 175, 245), (225, 210, 255), (255, 240, 130)),
    "summer": ((15, 110, 135), (40, 185, 210), (120, 225, 240), (255, 255, 255)),
}

# ---- slot machine: three of the fruit become seasonal (same payouts) --------------------------
THEME_SLOTS = {"halloween": {"cherry": "candycorn", "lemon": "pumpkin", "orange": "ghost"},
               "winter": {"cherry": "candycane", "lemon": "snowflake", "orange": "snowman"},
               "valentines": {"cherry": "heart", "lemon": "rose", "orange": "letter"},
               "easter": {"cherry": "egg", "lemon": "chick", "orange": "carrot"},
               "summer": {"cherry": "watermelon", "lemon": "sun", "orange": "beachball"}}


def make_theme_symbol(kind):
    k = 2
    w, h = SYM_W * k, SYM_H * k
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    cx, cy, r = w / 2, h / 2, h * 0.38
    if kind == "candycorn":
        pts = [(cx - r * 0.8, cy + r * 0.8), (cx + r * 0.8, cy + r * 0.8), (cx, cy - r)]
        pygame.draw.polygon(s, (255, 250, 235), pts)
        pygame.draw.polygon(s, (250, 160, 30), [(cx - r * 0.55, cy + r * 0.25), (cx + r * 0.55, cy + r * 0.25),
                                                (cx + r * 0.8, cy + r * 0.8), (cx - r * 0.8, cy + r * 0.8)])
        pygame.draw.polygon(s, (255, 215, 60), [(cx - r * 0.28, cy - r * 0.35), (cx + r * 0.28, cy - r * 0.35),
                                                (cx + r * 0.55, cy + r * 0.25), (cx - r * 0.55, cy + r * 0.25)])
    elif kind == "ghost":
        pygame.draw.circle(s, (245, 245, 255), (cx, cy - r * 0.2), r * 0.7)
        pygame.draw.rect(s, (245, 245, 255), (cx - r * 0.7, cy - r * 0.2, r * 1.4, r * 0.9))
        for i in range(4):
            pygame.draw.circle(s, (245, 245, 255), (cx - r * 0.52 + i * r * 0.35, cy + r * 0.7), r * 0.18)
        for ex in (-0.25, 0.25):
            pygame.draw.ellipse(s, (30, 30, 40), (cx + ex * r - r * 0.1, cy - r * 0.4, r * 0.2, r * 0.28))
        pygame.draw.ellipse(s, (30, 30, 40), (cx - r * 0.12, cy, r * 0.24, r * 0.2))
    elif kind == "candycane":
        for i in range(14):
            t = i / 13
            x = cx + r * 0.2
            y = cy + r * 0.9 - t * r * 1.2
            pygame.draw.circle(s, (230, 30, 40) if (i // 2) % 2 else (255, 255, 255), (x, y), r * 0.16)
        for i in range(12):
            a = math.pi - i / 11 * math.pi
            pygame.draw.circle(s, (230, 30, 40) if (i // 2) % 2 else (255, 255, 255),
                               (cx - r * 0.18 + math.cos(a) * r * 0.38, cy - r * 0.3 - math.sin(a) * r * 0.38), r * 0.16)
    elif kind == "letter":
        env = pygame.Rect(0, 0, r * 1.9, r * 1.25)
        env.center = (cx, cy)
        pygame.draw.rect(s, (255, 245, 235), env, border_radius=int(r * 0.08))
        pygame.draw.lines(s, (220, 190, 180), False, [env.topleft, env.center, env.topright], 4)
        draw_heart(s, cx, cy + r * 0.05, r * 0.3, (220, 30, 70))
    elif kind == "chick":
        pygame.draw.circle(s, (255, 220, 60), (cx, cy + r * 0.2), r * 0.7)
        pygame.draw.circle(s, (255, 228, 90), (cx + r * 0.1, cy - r * 0.45), r * 0.45)
        pygame.draw.polygon(s, (240, 140, 30), [(cx + r * 0.5, cy - r * 0.5), (cx + r * 0.85, cy - r * 0.4),
                                                (cx + r * 0.5, cy - r * 0.3)])
        pygame.draw.circle(s, (30, 30, 30), (cx + r * 0.25, cy - r * 0.55), r * 0.08)
    elif kind == "carrot":
        pygame.draw.polygon(s, (240, 130, 30), [(cx - r * 0.35, cy - r * 0.55), (cx + r * 0.35, cy - r * 0.55),
                                                (cx, cy + r)])
        for dy in (-0.2, 0.15, 0.5):
            pygame.draw.line(s, (200, 95, 20), (cx - r * 0.2, cy + dy * r), (cx + r * 0.05, cy + dy * r), 3)
        for a in (-0.5, 0, 0.5):
            pygame.draw.line(s, (60, 160, 60), (cx, cy - r * 0.55), (cx + a * r * 0.8, cy - r), 7)
    elif kind == "watermelon":
        pygame.draw.circle(s, (40, 140, 60), (cx, cy - r * 0.2), r, draw_bottom_left=True, draw_bottom_right=True)
        pygame.draw.circle(s, (240, 60, 80), (cx, cy - r * 0.2), r * 0.82, draw_bottom_left=True, draw_bottom_right=True)
        for i in range(5):
            a = math.pi * (0.2 + i * 0.15)
            pygame.draw.ellipse(s, (30, 20, 20), (cx + math.cos(a) * r * 0.5 - 4, cy - r * 0.2 + math.sin(a) * r * 0.45, 8, 12))
    elif kind in ("pumpkin", "snowflake", "snowman", "heart", "rose", "egg", "sun"):
        theme_of = {"pumpkin": ("halloween", False), "snowflake": ("winter", False), "snowman": ("winter", True),
                    "heart": ("valentines", False), "rose": ("valentines", True), "egg": ("easter", False),
                    "sun": ("summer", False)}[kind]
        if kind == "snowflake":                               # snowflakes need a darker outline on white reels
            for k2 in range(6):
                a = k2 * math.pi / 3
                pygame.draw.line(s, (80, 140, 210), (cx, cy), (cx + math.cos(a) * r, cy + math.sin(a) * r), 10)
        if kind == "snowman":                                 # ...and so does the snowman
            for rr, yy in ((0.55, 0.4), (0.4, -0.35)):
                pygame.draw.circle(s, (90, 130, 190), (cx, cy + yy * r), rr * r + 5)
        theme_icon(s, theme_of[0], cx, cy, r, alt=theme_of[1])
        if kind == "snowman":                                 # a red scarf
            pygame.draw.rect(s, (210, 40, 50), (cx - r * 0.38, cy - r * 0.05, r * 0.76, r * 0.14), border_radius=6)
    elif kind == "beachball":
        for i, col in enumerate(((230, 60, 60), (255, 255, 255), (60, 130, 230), (255, 210, 60))):
            pygame.draw.circle(s, col, (cx, cy), r, draw_top_left=i == 0, draw_top_right=i == 1,
                               draw_bottom_left=i == 3, draw_bottom_right=i == 2)
        pygame.draw.circle(s, (40, 40, 50), (cx, cy), r, 3)
    return pygame.transform.smoothscale(s, (SYM_W, SYM_H))


# ---- mines: seasonal gems and seasonal "mines" ------------------------------------------------
def draw_theme_bomb(surf, cx, cy, r):
    t = CURRENT_THEME
    if not t:
        return False
    if t == "halloween":                                       # skull
        pygame.draw.circle(surf, (235, 230, 220), (cx, cy - r * 0.15), r * 0.85)
        pygame.draw.rect(surf, (235, 230, 220), (cx - r * 0.5, cy + r * 0.3, r, r * 0.5), border_radius=4)
        for ex in (-0.35, 0.35):
            pygame.draw.circle(surf, (20, 10, 20), (cx + ex * r, cy - r * 0.15), r * 0.24)
        pygame.draw.polygon(surf, (20, 10, 20), [(cx, cy + r * 0.05), (cx - r * 0.12, cy + r * 0.28), (cx + r * 0.12, cy + r * 0.28)])
    elif t == "winter":                                        # a lump of coal
        pts = [(cx + math.cos(a) * r * (0.8 + 0.2 * math.sin(a * 3)), cy + math.sin(a) * r * (0.75 + 0.2 * math.cos(a * 2)))
               for a in [i / 9 * 2 * math.pi for i in range(9)]]
        pygame.draw.polygon(surf, (30, 30, 34), pts)
        pygame.draw.circle(surf, (80, 80, 90), (cx - r * 0.3, cy - r * 0.3), r * 0.15)
    elif t == "valentines":                                    # a broken heart
        draw_heart(surf, cx, cy, r * 0.9, (90, 20, 40))
        pygame.draw.lines(surf, (20, 10, 15), False, [(cx, cy - r * 0.5), (cx - r * 0.18, cy - r * 0.1),
                                                     (cx + r * 0.15, cy + r * 0.2), (cx, cy + r * 0.8)], 3)
    elif t == "easter":                                        # a rotten egg
        pygame.draw.ellipse(surf, (120, 150, 60), (cx - r * 0.7, cy - r * 0.9, r * 1.4, r * 1.8))
        pygame.draw.lines(surf, (50, 60, 20), False, [(cx - r * 0.6, cy), (cx - r * 0.2, cy - r * 0.2),
                                                     (cx + r * 0.1, cy + r * 0.1), (cx + r * 0.6, cy - r * 0.1)], 3)
        for dx in (-0.4, 0.3):
            pygame.draw.line(surf, (150, 190, 80), (cx + dx * r, cy - r * 1.1), (cx + dx * r + 4, cy - r * 1.5), 2)
    else:                                                      # a spiky sea urchin
        for i in range(16):
            a = i / 16 * 2 * math.pi
            pygame.draw.line(surf, (60, 20, 70), (cx, cy), (cx + math.cos(a) * r * 1.15, cy + math.sin(a) * r * 1.15), 3)
        pygame.draw.circle(surf, (90, 30, 100), (cx, cy), r * 0.65)
    return True


def theme_gem(size):
    """The mines game's gem, as the season's emblem (None with no theme)."""
    t = CURRENT_THEME
    if not t:
        return None

    def make():
        s = pygame.Surface((size[0] * 2, size[1] * 2), pygame.SRCALPHA)
        theme_icon(s, t, size[0], size[1], min(size) * 0.9)
        return pygame.transform.smoothscale(s, size)
    return themed(("gem", size), make)


# ---- the chicken's costume ------------------------------------------------------------------
def draw_chicken_costume(surf, x, y, k, top):
    t = CURRENT_THEME
    if t == "halloween":                                       # witch hat
        pygame.draw.ellipse(surf, (25, 15, 35), (x - 20 * k, top - 6 * k, 40 * k, 9 * k))
        pygame.draw.polygon(surf, (25, 15, 35), [(x - 11 * k, top - 3 * k), (x + 11 * k, top - 3 * k), (x + 6 * k, top - 30 * k)])
        pygame.draw.rect(surf, (140, 60, 200), (x - 10 * k, top - 8 * k, 20 * k, 4 * k))
    elif t == "winter":                                        # beanie and scarf
        pygame.draw.circle(surf, (200, 30, 40), (x, top - 1 * k), 13 * k, draw_top_left=True, draw_top_right=True)
        pygame.draw.rect(surf, (245, 245, 250), (x - 14 * k, top - 2 * k, 28 * k, 5 * k), border_radius=int(2 * k))
        pygame.draw.circle(surf, (245, 245, 250), (x, top - 15 * k), 4 * k)
        pygame.draw.rect(surf, (40, 120, 60), (x - 19 * k, y + 5 * k, 38 * k, 6 * k), border_radius=int(3 * k))
        pygame.draw.rect(surf, (40, 120, 60), (x + 8 * k, y + 7 * k, 6 * k, 14 * k), border_radius=int(2 * k))
    elif t == "valentines":                                    # blushing, with a heart
        for ex in (-12, 12):
            pygame.draw.circle(surf, (255, 150, 180), (x + ex * k, y + 1 * k), 4 * k)
        draw_heart(surf, x, top - 12 * k, 7 * k, (230, 40, 80))
    elif t == "easter":                                        # bunny ears
        for ex in (-7, 7):
            pygame.draw.ellipse(surf, (250, 250, 250), (x + ex * k - 5 * k, top - 26 * k, 10 * k, 28 * k))
            pygame.draw.ellipse(surf, (255, 180, 205), (x + ex * k - 2.5 * k, top - 22 * k, 5 * k, 20 * k))
    elif t == "summer":                                        # sunglasses
        for ex in (-8, 8):
            pygame.draw.rect(surf, (20, 20, 30), (x + ex * k - 6 * k, y - 10 * k, 12 * k, 8 * k), border_radius=int(3 * k))
        pygame.draw.line(surf, (20, 20, 30), (x - 2 * k, y - 7 * k), (x + 2 * k, y - 7 * k), max(1, int(2 * k)))


# --------------------------------------------------------------------------
# Party games - multiplayer only. The host's game runs each table; everyone plays together.
# --------------------------------------------------------------------------
MP_JOIN_WAIT = 12.0          # seconds for others to join a round someone started
MP_TURN = 30.0               # seconds per turn in Liar's Dice
CRASH_BET_TIME = 10.0
CRASH_LAST_BONUS = 0.2       # the last player to cash out gets 20% extra profit
BINGO_BUY_TIME = 20.0
BINGO_CALL_EVERY = 3.0
BINGO_GRACE = 8.0            # a bingo nobody claims is claimed for you after this long
HC_ORDER = {r: i for i, r in enumerate(["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"])}
BINGO_CX = 700
DICE_WORDS = {1: "ones", 2: "twos", 3: "threes", 4: "fours", 5: "fives", 6: "sixes"}


def dice_count(n, face):
    """3 fives, 1 five, no fives"""
    return f"{n} {DICE_WORDS[face][:-2] if face == 6 and n == 1 else (DICE_WORDS[face][:-1] if n == 1 else DICE_WORDS[face])}" if n else f"no {DICE_WORDS[face]}"


class MPTable:
    """A party game table on the host."""
    kind = ""
    RESEND = 1.0

    def __init__(self, srv):
        self.srv = srv
        self.queue = []
        self.dirty = True
        self.resend = 0.0
        self.msg = ""
        self.names = {}                   # remembered, so players who left still show up by name

    def name(self, pid):
        c = self.srv.clients.get(pid)
        if c:
            self.names[pid] = c["name"]
        return self.names.get(pid, "?")

    def pay(self, pid, stake, returned, text=""):
        self.srv.send(pid, {"t": "mp_result", "game": self.kind, "stake": int(stake), "returned": int(returned),
                            "text": text})

    def refund(self, pid, amount, why=""):
        self.srv.send(pid, {"t": "mp_refund", "game": self.kind, "amount": int(amount), "text": why})

    def update(self, dt):
        self.tick(dt)
        self.resend -= dt
        if self.dirty or self.resend <= 0:
            self.dirty = False
            self.resend = self.RESEND
            for pid in list(self.srv.clients):
                self.srv.send(pid, {"t": self.kind, "s": self.view(pid)})

    def tick(self, dt):
        pass

    def drop(self, pid):
        pass


class CrashTable(MPTable):
    kind = "crash"
    RESEND = 0.5

    def __init__(self, srv):
        super().__init__(srv)
        self.phase = "betting"
        self.timer = CRASH_BET_TIME
        self.t = 0.0
        self.bets = {}
        self.crash_at = 1.0
        self.history = []

    def mult(self):
        return math.exp(ROCKET_RATE * self.t)

    def on_msg(self, pid, c, m):
        t = m.get("t")
        if t == "crash_bet":
            amt = net_int(m.get("amount"))
            if self.phase != "betting" or pid in self.bets or amt <= 0:
                self.refund(pid, amt, "BETS ARE CLOSED - WAIT FOR THE NEXT ROUND")
                return
            self.bets[pid] = {"bet": amt, "out": None}
            self.dirty = True
        elif t == "crash_cash":
            b = self.bets.get(pid)
            if self.phase == "flying" and b and b["out"] is None:
                b["out"] = round(min(self.mult(), self.crash_at), 2)
                self.pay(pid, b["bet"], int(b["bet"] * b["out"]), f"CASHED OUT AT x{b['out']:.2f}")
                self.dirty = True

    def drop(self, pid):
        self.bets.pop(pid, None)

    def tick(self, dt):
        if self.phase == "betting":
            self.timer -= dt
            if self.timer <= 0:
                u = random.random()                             # same odds as the single-player Rocket (97%)
                self.crash_at = min(500.0, max(1.0, math.floor(0.97 / (1 - u) * 100) / 100))
                self.phase, self.t = "flying", 0.0
                self.msg = ""
                self.dirty = True
        elif self.phase == "flying":
            self.t += dt
            if self.mult() >= self.crash_at:
                self.crash()
        else:
            self.timer -= dt
            if self.timer <= 0:
                self.phase, self.timer, self.bets = "betting", CRASH_BET_TIME, {}
                self.dirty = True

    def crash(self):
        self.phase, self.timer = "crashed", 5.0
        self.history = ([self.crash_at] + self.history)[:12]
        outs = [(b["out"], pid) for pid, b in self.bets.items() if b["out"]]
        for pid, b in self.bets.items():
            if b["out"] is None:
                self.pay(pid, b["bet"], 0, f"BOOM AT x{self.crash_at:.2f} - YOU LOSE {money(b['bet'])}")
        self.msg = f"BOOM AT x{self.crash_at:.2f}"
        if len(outs) >= 1:
            out, pid = max(outs)
            bonus = int(self.bets[pid]["bet"] * (out - 1) * CRASH_LAST_BONUS)
            if bonus > 0:
                self.srv.send(pid, {"t": "mp_bonus", "game": self.kind, "amount": bonus,
                                    "text": f"LAST ONE OUT! +{money(bonus)} BONUS"})
                self.msg += f"   -   {self.name(pid).upper()} WAS THE LAST ONE OUT (+{money(bonus)})"
        self.dirty = True

    def view(self, pid):
        return {"phase": self.phase, "t": round(self.t, 2), "timer": round(self.timer, 1),
                "crash": self.crash_at if self.phase == "crashed" else None, "history": self.history, "msg": self.msg,
                "bets": [{"pid": p, "name": self.name(p), "bet": b["bet"], "out": b["out"]} for p, b in self.bets.items()]}


class PotTable(MPTable):
    """Someone starts a round with an amount; others have a little while to join for the same amount."""
    start_msg, join_msg = "", ""

    def __init__(self, srv):
        super().__init__(srv)
        self.phase = "idle"
        self.ante = 0
        self.players = []                 # pids in this round, in join order
        self.timer = 0.0

    @property
    def pot(self):
        return self.ante * len(self.players)

    def on_msg(self, pid, c, m):
        t = m.get("t", "")
        amt = net_int(m.get("amount"))
        if t.endswith("_start"):
            if self.phase != "idle" or amt <= 0:
                self.refund(pid, amt, "A ROUND IS ALREADY GOING - JOIN IT INSTEAD")
                return
            self.ante, self.players, self.phase, self.timer = amt, [pid], "joining", MP_JOIN_WAIT
            self.msg = f"{self.name(pid).upper()} STARTED A ROUND FOR {money(amt)}"
            self.new_round()
            self.dirty = True
        elif t.endswith("_join"):
            if self.phase != "joining" or pid in self.players or amt != self.ante:
                self.refund(pid, amt, "YOU CAN'T JOIN RIGHT NOW")
                return
            self.players.append(pid)
            self.dirty = True
        else:
            self.play_msg(pid, t, m)

    def new_round(self):
        pass

    def play_msg(self, pid, t, m):
        pass

    def tick(self, dt):
        if self.queue:
            self.queue[0][0] -= dt
            if self.queue[0][0] <= 0:
                _, fn = self.queue.pop(0)
                fn()
                self.dirty = True
            return
        if self.phase == "joining":
            self.timer -= dt
            if self.timer <= 0:
                live = [p for p in self.players if p in self.srv.clients]
                if len(live) < 2:
                    for p in live:
                        self.refund(p, self.ante, "NOBODY ELSE JOINED - YOUR CHIPS ARE BACK")
                    self.phase, self.players = "idle", []
                    self.msg = "NOT ENOUGH PLAYERS JOINED"
                else:
                    self.players = live
                    self.begin()
                self.dirty = True
        else:
            self.play_tick(dt)

    def begin(self):
        pass

    def play_tick(self, dt):
        pass

    def settle(self, winner, text):
        pot = self.pot
        for p in self.players:
            if p == winner:
                self.pay(p, self.ante, pot, f"YOU WIN THE {money(pot)} POT!")
            else:
                self.pay(p, self.ante, 0, f"{self.name(winner).upper()} WINS THE {money(pot)} POT")
        self.msg = text
        self.phase, self.timer = "over", 6.0
        self.dirty = True

    def finish_over(self, dt):
        self.timer -= dt
        if self.timer <= 0:
            self.phase, self.players = "idle", []
            self.dirty = True

    def drop(self, pid):
        if self.phase == "joining" and pid in self.players:
            self.players.remove(pid)
            self.dirty = True


class HighCardTable(PotTable):
    kind = "hc"

    def new_round(self):
        self.cards, self.out, self.winner, self.sudden = {}, set(), None, 0

    def begin(self):
        self.deal(list(self.players))

    def deal(self, pids):
        deck = [(r, s) for s in SUITS for r in RANKS]
        random.shuffle(deck)
        for p in pids:
            self.cards[p] = deck.pop()
        self.contest = pids
        self.phase, self.timer = "dealt", 2.5
        self.dirty = True

    def play_tick(self, dt):
        if self.phase == "dealt":
            self.timer -= dt
            if self.timer <= 0:
                self.phase, self.timer = "shown", 2.5
                self.dirty = True
        elif self.phase == "shown":
            self.timer -= dt
            if self.timer <= 0:
                best = max(HC_ORDER[self.cards[p][0]] for p in self.contest)
                tied = [p for p in self.contest if HC_ORDER[self.cards[p][0]] == best]
                self.out |= set(self.contest) - set(tied)
                if len(tied) > 1:
                    self.sudden += 1
                    self.msg = "TIE!  SUDDEN DEATH BETWEEN " + " & ".join(self.name(p).upper() for p in tied)
                    self.deal(tied)
                else:
                    self.winner = tied[0]
                    rank = self.cards[tied[0]][0]
                    self.settle(tied[0], f"{self.name(tied[0]).upper()} WINS {money(self.pot)} WITH THE {rank}!")
        elif self.phase == "over":
            self.finish_over(dt)

    def view(self, pid):
        shown = self.phase in ("shown", "over")
        return {"phase": self.phase, "ante": self.ante, "pot": self.pot, "timer": round(self.timer, 1), "msg": self.msg,
                "sudden": getattr(self, "sudden", 0),
                "players": [{"pid": p, "name": self.name(p), "card": list(self.cards[p]) if shown and p in getattr(self, "cards", {}) else None,
                             "has_card": p in getattr(self, "cards", {}), "out": p in getattr(self, "out", set()),
                             "win": p == getattr(self, "winner", None)} for p in self.players]}


class LiarsTable(PotTable):
    kind = "liar"

    def new_round(self):
        self.dice, self.bid, self.turn, self.log, self.reveal, self.turn_t = {}, None, 0, [], None, MP_TURN

    def begin(self):
        for p in self.players:
            self.dice[p] = [random.randint(1, 6) for _ in range(5)]
        self.turn = random.randrange(len(self.players))
        self.phase, self.turn_t = "playing", MP_TURN
        self.log = [f"{self.name(self.cur()).upper()} BIDS FIRST"]

    def alive(self):
        return [p for p in self.players if self.dice.get(p)]

    def cur(self):
        return self.players[self.turn]

    def next_turn(self):
        for k in range(1, len(self.players) + 1):
            i = (self.turn + k) % len(self.players)
            if self.dice.get(self.players[i]):
                self.turn = i
                break
        self.turn_t = MP_TURN

    def total_dice(self):
        return sum(len(self.dice.get(p, [])) for p in self.players)

    def play_msg(self, pid, t, m):
        if self.phase != "playing" or pid != self.cur():
            return
        if t == "liar_bid":
            qty, face = net_int(m.get("qty"), 99), net_int(m.get("face"), 6)
            b = self.bid
            ok = 1 <= face <= 6 and 1 <= qty <= self.total_dice() and (
                b is None or qty > b[0] or (qty == b[0] and face > b[1]))
            if ok:
                self.bid = (qty, face, pid)
                self.log = (self.log + [f"{self.name(pid).upper()}: {dice_count(qty, face)}"])[-7:]
                self.next_turn()
                self.dirty = True
        elif t == "liar_call" and self.bid:
            self.call(pid)

    def call(self, caller):
        qty, face, bidder = self.bid
        count = sum(d.count(face) for d in self.dice.values())
        loser = caller if count >= qty else bidder
        self.reveal = {"count": count, "qty": qty, "face": face, "loser": self.name(loser), "caller": self.name(caller),
                       "bidder": self.name(bidder)}
        self.log = (self.log + [f"{self.name(caller).upper()} CALLS LIAR!  THERE WERE {dice_count(count, face).upper()}"])[-7:]
        self.phase, self.timer = "reveal", 5.0
        self.loser = loser
        self.dirty = True

    def play_tick(self, dt):
        if self.phase == "playing":
            if not self.dice.get(self.cur()):
                self.next_turn()
            self.turn_t -= dt
            if self.turn_t <= 0:                        # too slow: call liar (or open with a small bid)
                p = self.cur()
                if self.bid:
                    self.call(p)
                else:
                    self.play_msg(p, "liar_bid", {"qty": 1, "face": random.randint(1, 6)})
        elif self.phase == "reveal":
            self.timer -= dt
            if self.timer <= 0:
                loser = self.loser
                if self.dice.get(loser):
                    self.dice[loser].pop()
                alive = self.alive()
                if len(alive) == 1:
                    self.settle(alive[0], f"{self.name(alive[0]).upper()} IS THE LAST ONE WITH DICE - WINS {money(self.pot)}!")
                    return
                for p in alive:
                    self.dice[p] = [random.randint(1, 6) for _ in range(len(self.dice[p]))]
                self.bid, self.reveal = None, None
                self.turn = self.players.index(loser) if self.dice.get(loser) else self.turn
                if not self.dice.get(self.cur()):
                    self.next_turn()
                self.turn_t = MP_TURN
                self.phase = "playing"
                self.log = (self.log + [f"NEW ROUND - {self.name(self.cur()).upper()} BIDS"])[-7:]
                self.dirty = True
        elif self.phase == "over":
            self.finish_over(dt)

    def drop(self, pid):
        super().drop(pid)
        if self.phase in ("playing", "reveal") and self.dice.get(pid):
            self.dice[pid] = []
            alive = self.alive()
            if len(alive) == 1:
                self.settle(alive[0], f"EVERYONE ELSE LEFT - {self.name(alive[0]).upper()} WINS")
            elif self.phase == "playing" and self.cur() == pid:
                self.next_turn()
            self.dirty = True

    def view(self, pid):
        show_all = self.phase in ("reveal", "over")
        d = getattr(self, "dice", {})
        return {"phase": self.phase, "ante": self.ante, "pot": self.pot, "timer": round(self.timer, 1), "msg": self.msg,
                "turn": self.cur() if self.phase == "playing" and self.players else None,
                "turn_t": round(getattr(self, "turn_t", 0), 1), "total": self.total_dice() if d else 0,
                "bid": {"qty": self.bid[0], "face": self.bid[1], "name": self.name(self.bid[2])} if getattr(self, "bid", None) else None,
                "mine": d.get(pid, []), "log": getattr(self, "log", []), "reveal": getattr(self, "reveal", None),
                "players": [{"pid": p, "name": self.name(p), "n": len(d.get(p, [])),
                             "dice": d.get(p, []) if show_all else None} for p in self.players]}


def bingo_card():
    cols = [random.sample(range(1 + 15 * c, 16 + 15 * c), 5) for c in range(5)]
    card = [[cols[c][r] for c in range(5)] for r in range(5)]
    card[2][2] = 0                                   # the free space
    return card


def bingo_lines(card, called):
    marked = set(called) | {0}
    lines = [row for row in card] + [[card[r][c] for r in range(5)] for c in range(5)]
    lines += [[card[i][i] for i in range(5)], [card[i][4 - i] for i in range(5)]]
    return any(all(n in marked for n in line) for line in lines)


class BingoTable(PotTable):
    kind = "bingo"

    def new_round(self):
        self.cards, self.called, self.call_t, self.ready_since = {}, [], 1.0, {}
        self.cards[self.players[0]] = bingo_card()

    def on_msg(self, pid, c, m):
        before = list(self.players)
        super().on_msg(pid, c, m)
        if m.get("t") == "bingo_join" and pid in self.players and pid not in before:
            self.cards[pid] = bingo_card()

    def begin(self):
        self.phase = "calling"
        self.pool = list(range(1, 76))
        random.shuffle(self.pool)

    def play_msg(self, pid, t, m):
        if t == "bingo_claim" and self.phase == "calling" and pid in self.cards and bingo_lines(self.cards[pid], self.called):
            self.settle(pid, f"BINGO!  {self.name(pid).upper()} WINS {money(self.pot)}!")

    def play_tick(self, dt):
        if self.phase == "calling":
            self.call_t -= dt
            if self.call_t <= 0 and self.pool:
                self.call_t = BINGO_CALL_EVERY
                self.called.append(self.pool.pop())
                self.dirty = True
            now = time.time()
            for p in self.players:
                if bingo_lines(self.cards[p], self.called):
                    self.ready_since.setdefault(p, now)
                    if now - self.ready_since[p] > BINGO_GRACE:        # nobody claimed it - claim it for them
                        self.settle(p, f"BINGO!  {self.name(p).upper()} WINS {money(self.pot)}!")
                        return
        elif self.phase == "over":
            self.finish_over(dt)

    def view(self, pid):
        cards = getattr(self, "cards", {})
        return {"phase": self.phase, "ante": self.ante, "pot": self.pot, "timer": round(self.timer, 1), "msg": self.msg,
                "called": getattr(self, "called", []), "card": cards.get(pid),
                "players": [{"pid": p, "name": self.name(p)} for p in self.players]}


# ---- the screens ------------------------------------------------------------------------------
MP_KINDS = {"crash": "mp_crash", "hc": "mp_highcard", "liar": "mp_liars", "bingo": "mp_bingo"}


class MPGame(StakeGame):
    """A party game screen. Chips you put in are 'in play' until the host says how the round went."""
    kind = ""
    title = ""

    def __init__(self, app):
        super().__init__(app, ((12, 10, 26), (80, 70, 140)))
        self.state = None
        self.in_play = 0
        self.t = 0.0
        self.bg = gradient_bg((40, 18, 70), (8, 5, 18))
        self.btn_main = Button((965, 646, 270, 62), "", (25, 120, 60), 20)
        self.message = "HOST OR JOIN A MULTIPLAYER GAME TO PLAY"

    def me(self):
        return self.app.net.id if self.app.net else None

    def on_state(self, s):
        self.state = s

    def on_msg(self, m):
        t = m.get("t")
        if t == "mp_result":
            stake, ret = net_int(m.get("stake")), net_int(m.get("returned"))
            self.in_play = max(0, self.in_play - stake)
            self.app.balance += ret
            self.app.record(self.key, stake, ret)
            self.message = str(m.get("text", ""))[:90]
            if ret > stake:
                self.app.float_text(f"+{money(ret - stake)}", (80, 230, 110))
                self.app.sfx("win")
            elif ret < stake:
                self.app.sfx("lose")
            self.app.save()
        elif t == "mp_refund":
            amt = net_int(m.get("amount"))
            self.app.balance += amt
            self.in_play = max(0, self.in_play - amt)
            self.message = str(m.get("text", ""))[:90]
        elif t == "mp_bonus":
            amt = net_int(m.get("amount"))
            self.app.balance += amt
            self.app.float_text(f"+{money(amt)}", (255, 220, 90))
            self.app.effects.toast(str(m.get("text", "BONUS!")), f"{self.title}: +{money(amt)}")
            self.app.sfx("win")

    def busy(self):
        return self.in_play > 0

    def can_leave(self):
        return self.in_play == 0

    def outstanding_bets(self):
        return self.in_play

    def lost(self):
        self.app.balance += self.in_play
        self.in_play = 0
        self.state = None

    def send(self, msg):
        if self.app.net:
            self.app.net.send(msg)

    def commit(self, amount):
        if amount <= 0:
            self.message = "ADD CHIPS TO SET THE AMOUNT FIRST"
            return False
        if amount > self.app.balance:
            self.message = "NOT ENOUGH CHIPS"
            return False
        self.app.balance -= amount
        self.in_play += amount
        self.app.sfx("chip")
        return True

    def pot_action(self, prefix):
        """START a round with your chip amount, or JOIN the one that's starting."""
        s = self.state or {}
        mine = self.me() in [p["pid"] for p in s.get("players", [])]
        if s.get("phase") == "idle" and self.commit(self.bet):
            self.send({"t": prefix + "_start", "amount": self.bet})
        elif s.get("phase") == "joining" and not mine and self.commit(s["ante"]):
            self.send({"t": prefix + "_join", "amount": s["ante"]})

    def pot_button(self):
        s = self.state or {}
        mine = self.me() in [p["pid"] for p in s.get("players", [])]
        ph = s.get("phase")
        if not self.app.net:
            return "NOT CONNECTED", False
        if ph == "idle":
            return (f"START FOR {money(self.bet)}" if self.bet else "SET AN AMOUNT"), 0 < self.bet <= self.app.balance
        if ph == "joining" and not mine:
            return f"JOIN FOR {money(s['ante'])}", s["ante"] <= self.app.balance
        if ph == "joining":
            return f"STARTING IN {max(0, math.ceil(s['timer']))}", False
        if ph == "over":
            return "NEXT ROUND SOON", False
        return "ROUND IN PLAY", False

    def update(self, dt):
        self.t += dt

    def handle(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.chip_click(e.pos):
                return
            if self.btn_clear.clicked(e.pos):
                self.bet = 0

    def draw_players(self, surf, rows, title="PLAYERS"):
        """rows: (name, detail, colour, highlighted)"""
        box = pygame.Rect(W - 300, 70, 280, 40 + 40 * max(1, len(rows)))
        soft_panel(surf, box, 170, (110, 90, 170))
        draw_text(surf, title, font(14, bold=True), GOLD, (box.x + 16, box.y + 20), anchor="midleft")
        for i, (name, detail, col, hl) in enumerate(rows):
            y = box.y + 48 + i * 40
            if hl:
                pygame.draw.rect(surf, (70, 55, 110), (box.x + 8, y - 17, box.w - 16, 34), border_radius=8)
            draw_text(surf, name, fit_font(name, 16, 130), WHITE, (box.x + 16, y), anchor="midleft")
            draw_text(surf, detail, font(14, bold=True), col, (box.right - 14, y), anchor="midright")
        if not rows:
            draw_text(surf, "nobody yet", font(14), (170, 160, 190), (box.x + 16, box.y + 50), anchor="midleft")

    def draw_frame(self, surf, main_label, main_on, color=(25, 120, 60)):
        mouse = pygame.mouse.get_pos()
        if self.message:
            draw_pill(surf, self.message, font(15, bold=True), (560, 612), GOLD, (0, 0, 0, 210), GOLD_DARK, pad=(14, 4))
        self.btn_main.text, self.btn_main.color = main_label, color
        self.btn_main.size = 20 if len(main_label) < 16 else 17
        self.draw_bottom(surf, mouse, self.btn_main, main_on, (10, 6, 18))
        self.app.draw_top_bar(surf, self.title, lobby=True, lobby_enabled=self.can_leave())
        draw_text(surf, "PARTY GAME  -  MULTIPLAYER", font(12, bold=True), (190, 160, 255), (640, 50))


class CrashParty(MPGame):
    key, kind, title = "mp_crash", "crash", "CRASH PARTY"

    def __init__(self, app):
        super().__init__(app)
        self.local_t = 0.0
        self.message = "BET BEFORE THE ROCKET LAUNCHES - THEN CASH OUT BEFORE IT BLOWS UP!"

    def on_state(self, s):
        prev = (self.state or {}).get("phase")
        self.state = s
        if s["phase"] == "flying" and (prev != "flying" or abs(self.local_t - s["t"]) > 0.4):
            self.local_t = s["t"]
        if s["phase"] == "crashed" and prev == "flying":
            self.app.sfx("boom")
            self.app.effects.burst(*self.rocket_pos(), 40, [(255, 140, 40), (255, 230, 120), (220, 60, 40)])
        if s["phase"] == "flying" and prev == "betting":
            self.app.sfx("launch")

    def my_bet(self):
        me = self.me()
        return next((b for b in (self.state or {}).get("bets", []) if b["pid"] == me), None)

    def mult(self):
        s = self.state or {}
        if s.get("phase") == "crashed":
            return s["crash"]
        if s.get("phase") == "flying":
            return math.exp(ROCKET_RATE * self.local_t)
        return 1.0

    def rocket_pos(self):
        g = pygame.Rect(60, 90, 780, 470)
        m = self.mult()
        top = max(2.0, m * 1.25)
        tmax = max(8.0, math.log(top) / ROCKET_RATE)
        tt = math.log(max(1.0, m)) / ROCKET_RATE
        return g.x + tt / tmax * g.w, g.bottom - (m - 1) / (top - 1) * g.h

    def update(self, dt):
        self.t += dt
        if (self.state or {}).get("phase") == "flying":
            self.local_t += dt

    def handle(self, e):
        s = self.state or {}
        act = (e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.btn_main.rect.collidepoint(e.pos)) or \
              (e.type == pygame.KEYDOWN and e.key == pygame.K_SPACE)
        if act and self.app.net:
            b = self.my_bet()
            if s.get("phase") == "betting" and not b and self.commit(self.bet):
                self.send({"t": "crash_bet", "amount": self.bet})
            elif s.get("phase") == "flying" and b and b["out"] is None:
                self.send({"t": "crash_cash"})
            return
        super().handle(e)

    def draw(self, surf):
        surf.blit(self.bg, (0, 0))
        s = self.state or {}
        g = pygame.Rect(60, 90, 780, 470)
        soft_panel(surf, g.inflate(20, 20), 120, (110, 90, 170))
        m = self.mult()
        ph = s.get("phase")
        if ph in ("flying", "crashed"):
            top = max(2.0, m * 1.25)
            tmax = max(8.0, math.log(top) / ROCKET_RATE)
            tt = math.log(max(1.0, m)) / ROCKET_RATE
            pts = [(g.x + x / 40 * tt / tmax * g.w, g.bottom - (math.exp(ROCKET_RATE * tt * x / 40) - 1) / (top - 1) * g.h)
                   for x in range(41)]
            pygame.draw.lines(surf, (255, 90, 90) if ph == "crashed" else (120, 240, 160), False, pts, 4)
            x, y = pts[-1]
            if ph == "flying":
                pygame.draw.polygon(surf, (235, 235, 245), [(x + 16, y - 10), (x - 12, y - 4), (x - 6, y + 12)])
                pygame.draw.circle(surf, (255, 170, 40), (x - 10, y + 8), 6 + 3 * math.sin(self.t * 30))
        big = f"x{m:.2f}"
        col = (255, 90, 90) if ph == "crashed" else ((120, 240, 160) if ph == "flying" else (200, 190, 230))
        draw_text(surf, big, font(80, bold=True), col, (g.centerx, g.y + 120), shadow=(0, 0, 0))
        if ph == "betting":
            draw_text(surf, f"LAUNCHING IN {max(0, math.ceil(s.get('timer', 0)))}", font(28, bold=True), GOLD,
                      (g.centerx, g.y + 200))
        elif ph == "crashed":
            draw_text(surf, "BOOM!", font(40, bold=True), (255, 120, 90), (g.centerx, g.y + 200))
        hist = s.get("history", [])
        for i, h in enumerate(hist[:10]):
            draw_pill(surf, f"x{h:.2f}", font(12, bold=True), (g.x + 40 + i * 76, g.bottom + 26),
                      WHITE, (30, 130, 70, 220) if h >= 2 else (150, 40, 50, 220), None, pad=(8, 2))
        rows = []
        for b in sorted(s.get("bets", []), key=lambda b: -(b["out"] or 0)):
            if b["out"]:
                rows.append((b["name"], f"OUT x{b['out']:.2f}", (120, 240, 150), b["pid"] == self.me()))
            elif ph == "crashed":
                rows.append((b["name"], f"LOST {money(b['bet'])}", (240, 120, 120), b["pid"] == self.me()))
            else:
                rows.append((b["name"], f"{money(b['bet'])} RIDING", GOLD, b["pid"] == self.me()))
        self.draw_players(surf, rows, "THIS ROUND")
        if s.get("msg") and ph == "crashed":
            draw_pill(surf, s["msg"], font(14, bold=True), (g.centerx, g.y + 250), WHITE, (0, 0, 0, 200), None, pad=(12, 3))
        b = self.my_bet()
        if not self.app.net:
            label, on = "NOT CONNECTED", False
        elif ph == "flying" and b and b["out"] is None:
            label, on = f"CASH OUT {money(int(b['bet'] * m))}", True
        elif ph == "betting" and not b:
            label, on = (f"BET {money(self.bet)}" if self.bet else "SET YOUR BET"), 0 < self.bet <= self.app.balance
        elif b and ph == "betting":
            label, on = "YOU'RE IN!", False
        else:
            label, on = "NEXT ROUND SOON", False
        self.draw_frame(surf, label, on, (215, 120, 15) if label.startswith("CASH") else (25, 120, 60))


class HighCardShowdown(MPGame):
    key, kind, title = "mp_highcard", "hc", "HIGH CARD SHOWDOWN"

    def __init__(self, app):
        super().__init__(app)
        self.bg = felt_table((12, 60, 34), (28, 105, 60))
        self.message = "START A ROUND (OR JOIN ONE) - HIGHEST CARD TAKES THE POT"

    def handle(self, e):
        act = (e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.btn_main.rect.collidepoint(e.pos)) or \
              (e.type == pygame.KEYDOWN and e.key == pygame.K_SPACE)
        if act:
            self.pot_action("hc")
            return
        super().handle(e)

    def draw(self, surf):
        surf.blit(self.bg, (0, 0))
        s = self.state or {}
        ph = s.get("phase", "idle")
        players = s.get("players", [])
        draw_text(surf, "HIGH CARD SHOWDOWN", font(36, bold=True, serif=True), GOLD, (500, 110), shadow=(0, 0, 0))
        if s.get("pot"):
            draw_pill(surf, f"POT  {money(s['pot'])}", font(22, bold=True), (500, 160), GOLD, (0, 0, 0, 200), GOLD_DARK,
                      pad=(16, 4))
        n = len(players)
        for i, p in enumerate(players):
            x = 500 + (i - (n - 1) / 2) * min(140, 700 / max(1, n))
            y = 330
            if p["card"]:
                img = self.assets.faces[tuple(p["card"])]
            elif p["has_card"] and ph in ("dealt",):
                img = self.assets.back
            else:
                img = None
            if img:
                bounce = -12 * abs(math.sin(self.t * 3 + i)) if ph == "dealt" else 0
                surf.blit(img, img.get_rect(center=(x, y + bounce)))
                if p["out"]:
                    d = pygame.Surface((CW, CH), pygame.SRCALPHA)
                    pygame.draw.rect(d, (0, 0, 0, 150), d.get_rect(), border_radius=8)
                    surf.blit(d, d.get_rect(center=(x, y)))
            else:
                pygame.draw.rect(surf, (20, 80, 45), (x - CW / 2, y - CH / 2, CW, CH), 2, border_radius=8)
            col = GOLD if p["win"] else (WHITE if p["pid"] != self.me() else (140, 220, 255))
            draw_text(surf, "YOU" if p["pid"] == self.me() else p["name"], fit_font(p["name"], 16, 130), col, (x, y + 84))
            if p["win"]:
                draw_pill(surf, "WINNER!", font(14, bold=True), (x, y - 88), (30, 20, 0), (*GOLD, 255), None, pad=(10, 2))
        if ph == "joining":
            draw_text(surf, f"JOIN FOR {money(s['ante'])}  -  STARTING IN {max(0, math.ceil(s['timer']))}", font(20, bold=True),
                      WHITE, (500, 480))
        elif ph == "dealt":
            draw_text(surf, "SUDDEN DEATH!" if s.get("sudden") else "FLIPPING IN A MOMENT...", font(22, bold=True), GOLD, (500, 480))
        if s.get("msg") and ph in ("over", "shown") or (ph == "dealt" and s.get("sudden")):
            draw_pill(surf, s["msg"], font(16, bold=True), (500, 530), WHITE, (0, 0, 0, 200), GOLD_DARK, pad=(14, 4))
        rows = [(p["name"], "IN", GOLD, p["pid"] == self.me()) for p in players]
        self.draw_players(surf, rows, "IN THIS ROUND")
        label, on = self.pot_button()
        self.draw_frame(surf, label, on)


class LiarsDice(MPGame):
    key, kind, title = "mp_liars", "liar", "LIAR'S DICE"

    def __init__(self, app):
        super().__init__(app)
        self.bg = felt_table((60, 25, 20), (110, 50, 35))
        self.qty, self.face = 1, 2
        self.btn_qm = Button((140, 470, 50, 50), "-", (60, 60, 90), 24)
        self.btn_qp = Button((290, 470, 50, 50), "+", (60, 60, 90), 24)
        self.face_btns = [Button((360 + i * 58, 470, 52, 50), str(i + 1), (60, 60, 90), 20) for i in range(6)]
        self.btn_bid = Button((360, 530, 170, 52), "BID", (40, 90, 160), 22)
        self.btn_liar = Button((540, 530, 170, 52), "LIAR!", (170, 30, 40), 24)
        self.message = "START A GAME (OR JOIN ONE) - LAST PLAYER WITH DICE TAKES THE POT"

    def my_turn(self):
        s = self.state or {}
        return s.get("phase") == "playing" and s.get("turn") == self.me()

    def on_state(self, s):
        was = self.my_turn()
        self.state = s
        if self.my_turn() and not was:
            b = s.get("bid")
            if b:                                         # start the picker at the smallest legal raise
                self.qty, self.face = (b["qty"], b["face"] + 1) if b["face"] < 6 else (b["qty"] + 1, 1)
            else:
                self.qty, self.face = 1, 2
            self.app.sfx("chip")

    def legal(self, qty, face):
        b = (self.state or {}).get("bid")
        return 1 <= qty <= (self.state or {}).get("total", 0) and (not b or qty > b["qty"] or (qty == b["qty"] and face > b["face"]))

    def handle(self, e):
        s = self.state or {}
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.btn_main.rect.collidepoint(e.pos):
                self.pot_action("liar")
                return
            if self.my_turn():
                if self.btn_qm.clicked(e.pos):
                    self.qty = max(1, self.qty - 1)
                elif self.btn_qp.clicked(e.pos):
                    self.qty = min(s.get("total", 1), self.qty + 1)
                elif self.btn_bid.clicked(e.pos, self.legal(self.qty, self.face)):
                    self.send({"t": "liar_bid", "qty": self.qty, "face": self.face})
                elif self.btn_liar.clicked(e.pos, bool(s.get("bid"))):
                    self.send({"t": "liar_call"})
                for i, b in enumerate(self.face_btns):
                    if b.clicked(e.pos):
                        self.face = i + 1
                return
        super().handle(e)

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        s = self.state or {}
        ph = s.get("phase", "idle")
        draw_text(surf, "LIAR'S DICE", font(36, bold=True, serif=True), GOLD, (430, 104), shadow=(0, 0, 0))
        if s.get("pot"):
            draw_pill(surf, f"POT  {money(s['pot'])}", font(20, bold=True), (430, 150), GOLD, (0, 0, 0, 200), GOLD_DARK,
                      pad=(14, 4))
        b = s.get("bid")
        if b:
            draw_text(surf, "CURRENT BID", font(14, bold=True), (230, 210, 190), (430, 198))
            draw_text(surf, f"{b['qty']} x", font(40, bold=True), WHITE, (390, 240))
            draw_die_face(surf, b["face"], (470, 240), 50)
            draw_text(surf, f"by {b['name']}", font(14), (230, 210, 190), (430, 280))
        elif ph == "playing":
            draw_text(surf, "NO BID YET", font(20, bold=True), (230, 210, 190), (430, 240))
        if ph == "joining":
            draw_text(surf, f"JOIN FOR {money(s['ante'])}  -  STARTING IN {max(0, math.ceil(s['timer']))}", font(20, bold=True),
                      WHITE, (430, 240))
        rv = s.get("reveal")
        if rv and ph == "reveal":
            draw_pill(surf, f"{rv['caller']} called LIAR on {rv['bidder']}!  There were {dice_count(rv['count'], rv['face'])} "
                            f"(bid: {rv['qty']})  -  {rv['loser']} loses a die", font(15, bold=True), (430, 320), WHITE,
                      (120, 20, 30, 230), GOLD, pad=(14, 4))
        # your dice
        mine = s.get("mine", [])
        if mine:
            draw_text(surf, "YOUR DICE (only you can see them)", font(13, bold=True), (230, 210, 190), (430, 360))
            for i, v in enumerate(mine):
                draw_die_face(surf, v, (430 + (i - (len(mine) - 1) / 2) * 62, 410), 50)
        if self.my_turn():
            left = max(0, math.ceil(s.get("turn_t", 0)))
            draw_text(surf, f"YOUR TURN ({left}s)  -  pick how many, and which face:", font(14, bold=True), GOLD, (60, 452),
                      anchor="midleft")
            self.btn_qm.draw(surf, mouse)
            self.btn_qp.draw(surf, mouse)
            draw_text(surf, str(self.qty), font(28, bold=True), WHITE, (240, 495))
            for i, btn in enumerate(self.face_btns):
                btn.color = (200, 150, 30) if self.face == i + 1 else (60, 60, 90)
                btn.draw(surf, mouse)
            self.btn_bid.text = f"BID {self.qty} x {self.face}"
            self.btn_bid.draw(surf, mouse, self.legal(self.qty, self.face))
            self.btn_liar.draw(surf, mouse, bool(b))
        # the table log
        log = s.get("log", [])
        box = pygame.Rect(W - 300, 380, 280, 210)
        soft_panel(surf, box, 170, (140, 90, 70))
        draw_text(surf, "WHAT'S HAPPENED", font(13, bold=True), GOLD, (box.x + 14, box.y + 18), anchor="midleft")
        for i, line in enumerate(log[-6:]):
            draw_text(surf, line, fit_font(line, 13, box.w - 28, bold=False), (230, 220, 210), (box.x + 14, box.y + 44 + i * 28),
                      anchor="midleft")
        rows = []
        for p in s.get("players", []):
            detail = f"{p['n']} dice" if ph != "joining" else "IN"
            if p.get("dice"):
                detail = " ".join(str(v) for v in p["dice"])
            col = (240, 120, 120) if (ph not in ("joining",) and p["n"] == 0) else GOLD
            rows.append((("> " if s.get("turn") == p["pid"] else "") + p["name"], detail, col, p["pid"] == self.me()))
        self.draw_players(surf, rows[:6], "PLAYERS")
        if s.get("msg") and ph == "over":
            draw_pill(surf, s["msg"], font(16, bold=True), (430, 560), WHITE, (0, 0, 0, 210), GOLD_DARK, pad=(14, 4))
        label, on = self.pot_button()
        self.draw_frame(surf, label, on)


class BingoNight(MPGame):
    key, kind, title = "mp_bingo", "bingo", "BINGO NIGHT"

    def __init__(self, app):
        super().__init__(app)
        self.btn_bingo = Button((BINGO_CX - 110, 520, 220, 60), "BINGO!", (200, 40, 90), 30)
        self.message = "START A GAME (OR JOIN ONE) - FIRST TO GET A LINE AND CALL BINGO WINS"

    def can_bingo(self):
        s = self.state or {}
        return s.get("phase") == "calling" and s.get("card") and bingo_lines(s["card"], s.get("called", []))

    def handle(self, e):
        if (e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.btn_bingo.clicked(e.pos, bool(self.can_bingo()))) or \
                (e.type == pygame.KEYDOWN and e.key == pygame.K_b and self.can_bingo()):
            self.send({"t": "bingo_claim"})
            return
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.btn_main.rect.collidepoint(e.pos):
            self.pot_action("bingo")
            return
        super().handle(e)

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        surf.blit(self.bg, (0, 0))
        s = self.state or {}
        ph = s.get("phase", "idle")
        called = s.get("called", [])
        cset = set(called)
        # your card
        card = s.get("card")
        cx0, cy0, cell = 70, 110, 78
        for c, letter in enumerate("BINGO"):
            draw_text(surf, letter, font(34, bold=True, serif=True), GOLD, (cx0 + c * cell + cell / 2, cy0 - 26))
        for r in range(5):
            for c in range(5):
                rect = pygame.Rect(cx0 + c * cell, cy0 + r * cell, cell - 6, cell - 6)
                pygame.draw.rect(surf, (250, 248, 240), rect, border_radius=10)
                if not card:
                    continue
                n = card[r][c]
                marked = n == 0 or n in cset
                if marked:
                    pygame.draw.circle(surf, (230, 40, 110), rect.center, cell * 0.36)
                draw_text(surf, "FREE" if n == 0 else str(n), font(15 if n == 0 else 26, bold=True),
                          WHITE if marked else (40, 30, 60), rect.center)
        if not card:
            draw_text(surf, "BUY A CARD TO PLAY", font(22, bold=True), (140, 120, 170), (cx0 + 2.5 * cell, cy0 + 2.5 * cell))
        # the caller
        last = called[-1] if called else None
        if last:
            letter = "BINGO"[(last - 1) // 15]
            pygame.draw.circle(surf, (255, 250, 240), (BINGO_CX, 200), 70)
            pygame.draw.circle(surf, (230, 40, 110), (BINGO_CX, 200), 70, 8)
            draw_text(surf, letter, font(24, bold=True), (230, 40, 110), (BINGO_CX, 165))
            draw_text(surf, str(last), font(56, bold=True), (40, 30, 60), (BINGO_CX, 215))
        draw_text(surf, f"{len(called)} CALLED", font(14, bold=True), (200, 190, 230), (BINGO_CX, 290))
        for i, n in enumerate(called[-12:-1][::-1]):
            draw_pill(surf, str(n), font(13, bold=True), (BINGO_CX - 90 + (i % 4) * 60, 330 + (i // 4) * 34), (40, 30, 60),
                      (245, 240, 230, 255), None, pad=(8, 2))
        if ph == "joining":
            draw_text(surf, f"CARDS COST {money(s['ante'])}  -  CALLING STARTS IN {max(0, math.ceil(s['timer']))}",
                      font(16, bold=True), WHITE, (BINGO_CX, 470))
        if s.get("pot"):
            draw_pill(surf, f"POT  {money(s['pot'])}", font(20, bold=True), (BINGO_CX, 100), GOLD, (0, 0, 0, 200), GOLD_DARK, pad=(14, 4))
        if ph == "calling" and card:
            ready = self.can_bingo()
            if ready:
                glow = pygame.Surface((260, 100), pygame.SRCALPHA)
                pygame.draw.rect(glow, (255, 80, 150, int(90 + 70 * math.sin(self.t * 8))), glow.get_rect(), border_radius=30)
                surf.blit(glow, (self.btn_bingo.rect.x - 20, self.btn_bingo.rect.y - 20))
            self.btn_bingo.draw(surf, mouse, bool(ready))
        if s.get("msg") and ph == "over":
            draw_pill(surf, s["msg"], font(16, bold=True), (BINGO_CX, 480), WHITE, (0, 0, 0, 210), GOLD_DARK, pad=(14, 4))
        rows = [(p["name"], "PLAYING", GOLD, p["pid"] == self.me()) for p in s.get("players", [])]
        self.draw_players(surf, rows, "CARDS BOUGHT")
        label, on = self.pot_button()
        self.draw_frame(surf, label.replace("START FOR", "START BINGO:").replace("JOIN FOR", "BUY A CARD:"), on)


def art_party(kind):
    def make(w, h):
        k = 2
        s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
        top, bottom = {"crash": ((40, 30, 90), (10, 8, 30)), "hc": ((25, 100, 60), (8, 40, 24)),
                       "liar": ((110, 45, 30), (40, 15, 10)), "bingo": ((120, 40, 120), (40, 10, 50))}[kind]
        for y in range(h * k):
            pygame.draw.line(s, lerp_col(top, bottom, y / (h * k)), (0, y), (w * k, y))
        cx, cy = w * k / 2, h * k / 2
        if kind == "crash":
            pts = [(40 + x * 7, h * k - 30 - (math.exp(x / 22) - 1) * 7) for x in range(90)]
            pygame.draw.lines(s, (120, 240, 160), False, pts, 6)
            x, y = pts[-1]
            pygame.draw.polygon(s, (235, 235, 245), [(x + 30, y - 20), (x - 20, y - 8), (x - 10, y + 20)])
            for i in range(4):
                pygame.draw.circle(s, [(80, 160, 255), (255, 120, 90), (120, 230, 120), (255, 210, 80)][i],
                                   (70 + i * 50, 60), 18)
            draw_text(s, "x3.47", font(h * k * 0.22, bold=True), (120, 240, 160), (w * k * 0.72, h * k * 0.55), shadow=(0, 0, 0))
        elif kind == "hc":
            faces = [("A", "S"), ("K", "H"), ("7", "D")]
            for i, card in enumerate(faces):
                img = pygame.transform.rotozoom(make_card_face(*card), (i - 1) * -12, 1.4)
                s.blit(img, img.get_rect(center=(cx + (i - 1) * 150, cy + abs(i - 1) * 16)))
        elif kind == "liar":
            for i, v in enumerate((5, 5, 2, 6, 5)):
                draw_die_face(s, v, (cx - 200 + i * 100, cy + 40), 80)
            draw_text(s, "LIAR!", font(h * k * 0.26, bold=True, serif=True), (255, 90, 80), (cx, cy - 70), shadow=(0, 0, 0))
        else:
            for i, (n, letter) in enumerate(((7, "B"), (22, "I"), (41, "N"), (58, "G"), (66, "O"))):
                x = cx - 280 + i * 140
                pygame.draw.circle(s, (255, 250, 240), (x, cy), 56)
                pygame.draw.circle(s, (230, 40, 110), (x, cy), 56, 8)
                draw_text(s, letter, font(26, bold=True), (230, 40, 110), (x, cy - 24))
                draw_text(s, str(n), font(40, bold=True), (40, 30, 60), (x, cy + 14))
        draw_text(s, "PARTY", font(h * k * 0.1, bold=True), (255, 220, 120), (w * k - 70, 28), shadow=(0, 0, 0))
        return pygame.transform.smoothscale(s, (w, h))
    return make


# --------------------------------------------------------------------------
# Lobby / menu
# --------------------------------------------------------------------------
def art_blackjack(assets, w, h):
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(s, (18, 100, 56), (0, 0, w, h), border_radius=12)
    pygame.draw.ellipse(s, (24, 120, 68), (-40, -60, w + 80, h + 20))
    a = pygame.transform.rotozoom(assets.faces[("A", "S")], 12, h / 180)
    k = pygame.transform.rotozoom(assets.faces[("K", "H")], -10, h / 180)
    s.blit(a, a.get_rect(center=(w / 2 - w * 0.15, h / 2 + 2)))
    s.blit(k, k.get_rect(center=(w / 2 + w * 0.15, h / 2 + 8)))
    for i, v in enumerate([500, 100, 100, 25]):
        img = assets.chip(v, 18)
        s.blit(img, img.get_rect(center=(w - 26, h - 24 - i * 4)))
    return s


def art_roulette(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    pygame.draw.rect(s, (18, 100, 56), (0, 0, w * k, h * k), border_radius=12 * k)
    cx, cy = w * k / 2, h * k / 2
    R = min(w, h) * k / 2 - 10 * k
    pygame.draw.circle(s, (70, 40, 20), (cx, cy + 4 * k), R)
    pygame.draw.circle(s, (100, 58, 30), (cx, cy), R)
    pygame.draw.circle(s, GOLD_DARK, (cx, cy), R * 0.9, width=k * 2)
    n = 37
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        col = (20, 140, 60) if i == 0 else (190, 25, 30) if i % 2 else (20, 20, 22)
        pts = [(cx + R * 0.86 * math.cos(a0 + (a1 - a0) * t / 3), cy + R * 0.86 * math.sin(a0 + (a1 - a0) * t / 3))
               for t in range(4)]
        pts += [(cx + R * 0.6 * math.cos(a1 - (a1 - a0) * t / 3), cy + R * 0.6 * math.sin(a1 - (a1 - a0) * t / 3))
                for t in range(4)]
        pygame.draw.polygon(s, col, pts)
    pygame.draw.circle(s, (120, 72, 36), (cx, cy), R * 0.6)
    pygame.draw.circle(s, (150, 95, 50), (cx, cy), R * 0.42)
    for i in range(4):
        a = math.pi / 2 * i + 0.3
        pygame.draw.line(s, GOLD, (cx, cy), (cx + R * 0.45 * math.cos(a), cy + R * 0.45 * math.sin(a)), 3 * k)
    pygame.draw.circle(s, GOLD, (cx, cy), R * 0.1)
    pygame.draw.circle(s, WHITE, (cx + R * 0.73 * math.cos(1.1), cy + R * 0.73 * math.sin(1.1)), R * 0.055)
    return pygame.transform.smoothscale(s, (w, h))


def art_slots(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    pygame.draw.rect(s, (40, 12, 60), (0, 0, w * k, h * k), border_radius=12 * k)
    cab = pygame.Rect(22 * k, 12 * k, (w - 56) * k, (h - 24) * k)
    pygame.draw.rect(s, (170, 25, 35), cab, border_radius=14 * k)
    pygame.draw.rect(s, GOLD, cab, width=3 * k, border_radius=14 * k)
    draw_text(s, "JACKPOT", font(min(cab.w * 0.14, cab.h * 0.28), bold=True, serif=True), GOLD,
              (cab.centerx, cab.top + 22 * k),
              shadow=(60, 0, 0))
    win = pygame.Rect(cab.x + 12 * k, cab.y + 44 * k, cab.w - 24 * k, min(80 * k, cab.h - 54 * k))
    pygame.draw.rect(s, (20, 20, 20), win.inflate(6 * k, 6 * k), border_radius=6 * k)
    rw = win.w / 3
    for i in range(3):
        r = pygame.Rect(win.x + i * rw + 2 * k, win.y, rw - 4 * k, win.h)
        pygame.draw.rect(s, (250, 248, 240), r, border_radius=4 * k)
        draw_text(s, "7", font(min(52 * k, win.h * 0.95), bold=True, serif=True), (210, 20, 30), r.center,
                  shadow=(120, 90, 20))
    pygame.draw.line(s, (200, 200, 200), (cab.right + 8 * k, cab.centery), (cab.right + 8 * k, cab.top + 10 * k),
                     4 * k)
    pygame.draw.circle(s, (220, 30, 40), (cab.right + 8 * k, cab.top + 10 * k), 9 * k)
    return pygame.transform.smoothscale(s, (w, h))


def art_poker(assets, w, h):
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(s, (15, 60, 110), (0, 0, w, h), border_radius=12)
    pygame.draw.ellipse(s, (22, 80, 140), (-40, -60, w + 80, h + 20))
    ranks = ["10", "J", "Q", "K", "A"]
    px, py, rad = w / 2, h * 1.35, h * 0.95
    for i, r in enumerate(ranks):
        a = math.radians(-26 + i * 13)
        img = pygame.transform.rotozoom(assets.faces[(r, "H")], -math.degrees(a), 0.95 * h / 190)
        s.blit(img, img.get_rect(center=(px + rad * math.sin(a), py - rad * math.cos(a))))
    return s


def art_rocket(w, h):
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    k = 2
    bg = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    for y in range(h * k):
        pygame.draw.line(bg, lerp_col((10, 10, 34), (40, 14, 60), y / (h * k)), (0, y), (w * k, y))
    rng = random.Random(4)
    for _ in range(50):
        pygame.draw.circle(bg, (220, 220, 255), (rng.uniform(0, w * k), rng.uniform(0, h * k)), rng.choice((1, 2)))
    pts = [(14 * k + t * (w - 50) * k, (h - 18) * k - (math.exp(2.2 * t) - 1) / (math.exp(2.2) - 1) * (h - 70) * k)
           for t in (i / 30 for i in range(31))]
    pygame.draw.polygon(bg, (255, 200, 60, 50), pts + [(pts[-1][0], (h - 18) * k), (pts[0][0], (h - 18) * k)])
    pygame.draw.lines(bg, (255, 200, 60), False, pts, 3 * k)
    m = pygame.Surface(bg.get_size(), pygame.SRCALPHA)
    pygame.draw.rect(m, (255, 255, 255, 255), m.get_rect(), border_radius=12 * k)
    bg.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    s.blit(pygame.transform.smoothscale(bg, (w, h)), (0, 0))
    art = pygame.Surface((150, 60), pygame.SRCALPHA)
    pygame.draw.polygon(art, (255, 120, 30), [(66, 22), (30, 30), (66, 38)])
    pygame.draw.polygon(art, (255, 230, 120), [(66, 26), (46, 30), (66, 34)])
    art.blit(make_rocket_sprite(), (60, 10))
    rot = pygame.transform.rotozoom(art, 50, 0.95)
    s.blit(rot, rot.get_rect(center=(w - 52, 52)))
    draw_text(s, "4.20x", font(30, bold=True), WHITE, (w / 2 - 20, h / 2 + 10), shadow=(0, 0, 0))
    return s


def art_stocks(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    pygame.draw.rect(s, (10, 16, 32), (0, 0, w * k, h * k), border_radius=12 * k)
    for x in range(0, w * k, 20 * k):
        pygame.draw.line(s, (22, 32, 58), (x, 0), (x, h * k))
    for y in range(0, h * k, 20 * k):
        pygame.draw.line(s, (22, 32, 58), (0, y), (w * k, y))
    rng = random.Random(12)
    v, pts = 0.0, []
    for i in range(40):
        v += rng.gauss(0.5, 1.6)
        pts.append((8 * k + i / 39 * (w - 16) * k, (h - 22) * k - v * 2.6 * k))
    pygame.draw.polygon(s, (*UP_COL, 45), pts + [(pts[-1][0], (h - 12) * k), (pts[0][0], (h - 12) * k)])
    pygame.draw.lines(s, UP_COL, False, pts, 3 * k)
    pygame.draw.circle(s, UP_COL, pts[-1], 5 * k)
    m = pygame.Surface(s.get_size(), pygame.SRCALPHA)
    pygame.draw.rect(m, (255, 255, 255, 255), m.get_rect(), border_radius=12 * k)
    s.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    s = pygame.transform.smoothscale(s, (w, h))
    draw_arrow(s, True, (22, 28), 16, UP_COL)
    draw_text(s, "+12.4%", font(22, bold=True), UP_COL, (36, 28), anchor="midleft")
    return s


def art_horses(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    for y in range(h * k):
        pygame.draw.line(s, lerp_col((120, 180, 235), (175, 215, 240), y / (h * k)), (0, y), (w * k, y))
    pygame.draw.rect(s, (60, 140, 60), (0, h * k * 0.45, w * k, h * k))
    pygame.draw.rect(s, (164, 120, 78), (0, h * k * 0.55, w * k, h * k * 0.4))
    pygame.draw.rect(s, (245, 245, 245), (0, h * k * 0.53, w * k, 3 * k))
    for x in range(0, w * k, 18 * k):
        pygame.draw.rect(s, (230, 230, 230), (x, h * k * 0.53, 2 * k, 9 * k))
    draw_horse(s, w * k * 0.34, h * k * 0.83, 1.2, (120, 70, 35), HORSE_SILKS[1], 2, 1.25 * k * h / 132)
    draw_horse(s, w * k * 0.62, h * k * 0.97, 2.6, (38, 32, 32), HORSE_SILKS[0], 5, 1.25 * k * h / 132)
    m = pygame.Surface(s.get_size(), pygame.SRCALPHA)
    pygame.draw.rect(m, (255, 255, 255, 255), m.get_rect(), border_radius=12 * k)
    s.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return pygame.transform.smoothscale(s, (w, h))


def art_wheel(w, h):
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(s, (40, 12, 30), (0, 0, w, h), border_radius=12)
    r = int(h / 2 - 8)
    wheel = pygame.transform.rotozoom(make_mult_wheel(r), 7, 1)
    s.blit(wheel, wheel.get_rect(center=(w / 2, h / 2 + 5)))
    pygame.draw.circle(s, (20, 14, 10), (w / 2, h / 2 + 5), r * 0.26)
    pygame.draw.circle(s, GOLD, (w / 2, h / 2 + 5), r * 0.26, width=2)
    draw_text(s, "x5", font(r * 0.3, bold=True), GOLD, (w / 2, h / 2 + 5))
    pygame.draw.polygon(s, (225, 35, 45), [(w / 2 - 9, 1), (w / 2 + 9, 1), (w / 2, 20)])
    pygame.draw.polygon(s, GOLD, [(w / 2 - 9, 1), (w / 2 + 9, 1), (w / 2, 20)], width=2)
    return s


def art_bus(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    for y in range(h * k):
        pygame.draw.line(s, lerp_col((255, 170, 90), (120, 70, 160), y / (h * k)), (0, y), (w * k, y))
    pygame.draw.circle(s, (255, 230, 150), (w * k * 0.8, h * k * 0.38), h * k * 0.16)
    pygame.draw.rect(s, (60, 60, 70), (0, h * k * 0.78, w * k, h * k))
    for x in range(0, w * k, 36 * k):
        pygame.draw.rect(s, (230, 220, 120), (x, h * k * 0.87, 18 * k, 3 * k))
    bx, by, bw, bh = w * k * 0.1, h * k * 0.32, w * k * 0.62, h * k * 0.44
    pygame.draw.rect(s, (40, 30, 10), (bx + 4 * k, by + 5 * k, bw, bh), border_radius=14 * k)
    pygame.draw.rect(s, (250, 200, 40), (bx, by, bw, bh), border_radius=14 * k)
    pygame.draw.rect(s, (200, 150, 20), (bx, by + bh * 0.62, bw, bh * 0.12))
    for i in range(4):
        pygame.draw.rect(s, (120, 190, 230), (bx + bw * (0.06 + i * 0.2), by + bh * 0.14, bw * 0.15, bh * 0.36),
                         border_radius=4 * k)
    pygame.draw.rect(s, (90, 150, 190), (bx + bw * 0.86, by + bh * 0.14, bw * 0.1, bh * 0.66), border_radius=3 * k)
    for wx in (bx + bw * 0.22, bx + bw * 0.78):
        pygame.draw.circle(s, (20, 20, 22), (wx, by + bh), bh * 0.2)
        pygame.draw.circle(s, (170, 170, 175), (wx, by + bh), bh * 0.08)
    pygame.draw.circle(s, (255, 250, 200), (bx + bw - 4 * k, by + bh * 0.8), 5 * k)
    for i, (r, su) in enumerate([("7", "H"), ("K", "S"), ("A", "D")]):
        img = pygame.transform.rotozoom(Assets_faces_cache[(r, su)], -12 + i * 12, h * k / 330)
        s.blit(img, img.get_rect(center=(w * k * (0.62 + i * 0.12), h * k * 0.3)))
    m = pygame.Surface(s.get_size(), pygame.SRCALPHA)
    pygame.draw.rect(m, (255, 255, 255, 255), m.get_rect(), border_radius=12 * k)
    s.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return pygame.transform.smoothscale(s, (w, h))


def art_craps(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    pygame.draw.rect(s, (18, 100, 56), (0, 0, w * k, h * k), border_radius=12 * k)
    pygame.draw.ellipse(s, (24, 120, 68), (-40 * k, -60 * k, (w + 80) * k, (h + 20) * k))
    pygame.draw.rect(s, (235, 235, 225), (10 * k, h * k * 0.62, w * k - 20 * k, h * k * 0.26), width=2 * k,
                     border_radius=8 * k)
    draw_text(s, "PASS LINE", font(h * k * 0.13, bold=True, serif=True), WHITE, (w * k / 2, h * k * 0.75))
    for (x, y, v, ang) in ((0.36, 0.34, 5, -20), (0.62, 0.38, 2, 25)):
        img = pygame.transform.rotozoom(make_die(v, int(h * k * 0.34)), ang, 1)
        pygame.draw.ellipse(s, (8, 60, 32), (w * k * x - h * k * 0.18, h * k * (y + 0.14), h * k * 0.38, h * k * 0.1))
        s.blit(img, img.get_rect(center=(w * k * x, h * k * y)))
    return pygame.transform.smoothscale(s, (w, h))


def art_baccarat(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    pygame.draw.rect(s, (90, 14, 30), (0, 0, w * k, h * k), border_radius=12 * k)
    pygame.draw.ellipse(s, (125, 25, 45), (-40 * k, -60 * k, (w + 80) * k, (h + 20) * k))
    for i, (r, su, ang) in enumerate((("9", "H", 10), ("K", "S", -8))):
        img = pygame.transform.rotozoom(Assets_faces_cache[(r, su)], ang, h * k / 250)
        s.blit(img, img.get_rect(center=(w * k * (0.36 + i * 0.26), h * k * 0.42)))
    for x, label, col in ((0.2, "P", (40, 90, 200)), (0.5, "T", (40, 150, 70)), (0.8, "B", (200, 40, 50))):
        c = (w * k * x, h * k * 0.84)
        pygame.draw.circle(s, col, c, h * k * 0.1)
        pygame.draw.circle(s, WHITE, c, h * k * 0.1, width=k)
        draw_text(s, label, font(h * k * 0.12, bold=True), WHITE, c)
    return pygame.transform.smoothscale(s, (w, h))


def Menu_make_bg(pattern=True):
    s = pygame.Surface((W, H))
    for y in range(H):
        pygame.draw.line(s, lerp_col((48, 10, 22), (8, 6, 12), y / H), (0, y), (W, y))
    glow = pygame.Surface((W, H), pygame.SRCALPHA)
    for i in range(40):
        r = 700 - i * 16
        pygame.draw.ellipse(glow, (255, 190, 90, 3), (W / 2 - r, -r * 0.55, r * 2, r * 1.2))
    s.blit(glow, (0, 0))
    if pattern:
        pat = pygame.Surface((W, H), pygame.SRCALPHA)
        for y in range(0, H, 60):
            for x in range(0, W + 60, 60):
                ox = 30 if (y // 60) % 2 else 0
                draw_suit(pat, SUITS[(x // 60 + y // 60) % 4], x + ox, y + 30, 16, (255, 255, 255, 10))
        s.blit(pat, (0, 0))
    return s


def Menu_make_pattern():
    # repeats every 240px both ways, so it can scroll forever without a visible jump
    pat = pygame.Surface((W + 240, H + 240), pygame.SRCALPHA)
    for row in range((H + 240) // 60 + 1):
        for col in range((W + 240) // 60 + 2):
            ox = 30 if row % 2 else 0
            draw_suit(pat, SUITS[(col + row) % 4], col * 60 + ox, row * 60 + 30, 16, (255, 255, 255, 12))
    return pat


def Menu_make_vignette():
    v = pygame.Surface((W, H), pygame.SRCALPHA)
    v.fill((0, 0, 0, 150))
    for i in range(40):
        k = i / 39
        pygame.draw.ellipse(v, (0, 0, 0, int(150 * (1 - k) ** 1.6)),
                            (W / 2 - W * 0.78 * (1 - k * 0.5), H / 2 - H * 0.85 * (1 - k * 0.5),
                             W * 1.56 * (1 - k * 0.5), H * 1.7 * (1 - k * 0.5)))
    return v


def Menu_new_drifter(y):
    return {"x": random.uniform(40, W - 40), "y": y, "vy": random.uniform(12, 26), "rot": random.uniform(0, 360),
            "vr": random.uniform(-35, 35), "v": random.choice(CHIP_VALUES), "r": random.randint(16, 26),
            "sway": random.uniform(0, 6.3)}


def art_plinko(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    for y in range(h * k):
        pygame.draw.line(s, lerp_col((40, 16, 70), (12, 6, 26), y / (h * k)), (0, y), (w * k, y))
    rows = 6
    gap = min(w * k / 11, h * k / 8)
    for r in range(rows):
        for j in range(r + 3):
            pygame.draw.circle(s, (230, 225, 255), (w * k / 2 + (j - (r + 2) / 2) * gap, h * k * 0.12 + r * gap * 0.9), 3 * k)
    mults = [9, 3, 1, 0.4, 1, 3, 9]
    for i, m in enumerate(mults):
        rr = pygame.Rect(0, 0, gap * 0.9, gap * 0.7)
        rr.center = (w * k / 2 + (i - 3) * gap, h * k * 0.12 + rows * gap * 0.9 + gap * 0.3)
        pygame.draw.rect(s, plinko_color(m * 10), rr, border_radius=3 * k)
    pygame.draw.circle(s, (255, 90, 160), (w * k / 2 + gap * 0.5, h * k * 0.12 + 2.5 * gap), 7 * k)
    pygame.draw.circle(s, WHITE, (w * k / 2 + gap * 0.5 - 2 * k, h * k * 0.12 + 2.5 * gap - 2 * k), 2 * k)
    return pygame.transform.smoothscale(s, (w, h))


def art_mines(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    pygame.draw.rect(s, (16, 22, 48), (0, 0, w * k, h * k))
    size = min(w, h) * k / 4.2
    x0 = w * k / 2 - size * 2.5 - 8 * k
    gem = pygame.transform.smoothscale(make_symbol("diamond"), (int(size * 0.8), int(size * 0.62)))
    layout = "..g.b|.g...|..g.."
    for r, row in enumerate(layout.split("|")):
        for c, ch in enumerate(row):
            rect = pygame.Rect(x0 + c * (size + 4 * k), h * k * 0.1 + r * (size + 4 * k), size, size)
            pygame.draw.rect(s, (45, 55, 105) if ch == "." else (18, 22, 40), rect, border_radius=4 * k)
            if ch == "g":
                s.blit(gem, gem.get_rect(center=rect.center))
            elif ch == "b":
                draw_bomb(s, rect.centerx, rect.centery + 2 * k, size * 0.28)
    return pygame.transform.smoothscale(s, (w, h))


def art_videopoker(assets, w, h):
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(s, (12, 24, 70), (0, 0, w, h))
    sc = h / 170
    for i, card in enumerate([("A", "S"), ("K", "S"), ("Q", "S"), ("J", "S"), ("10", "S")]):
        img = pygame.transform.rotozoom(assets.faces[card], 0, sc)
        s.blit(img, img.get_rect(center=(w / 2 + (i - 2) * img.get_width() * 1.05, h * 0.46)))
    draw_text(s, "ROYAL FLUSH  800x", font(max(10, h * 0.13), bold=True), GOLD, (w / 2, h * 0.88))
    return s


def art_scratch(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    pygame.draw.rect(s, (40, 14, 50), (0, 0, w * k, h * k))
    t = pygame.Rect(w * k * 0.2, h * k * 0.08, w * k * 0.6, h * k * 0.84)
    pygame.draw.rect(s, (190, 140, 20), t, border_radius=8 * k)
    pygame.draw.rect(s, (255, 240, 150), t, width=2 * k, border_radius=8 * k)
    draw_text(s, "GOLD RUSH", font(h * k * 0.13, bold=True, serif=True), (255, 240, 150), (t.centerx, t.y + h * k * 0.12))
    cw, ch = t.w / 3.4, t.h / 4.6
    for i in range(6):
        r, c = divmod(i, 3)
        cell = pygame.Rect(t.x + t.w * 0.06 + c * cw * 1.08, t.y + h * k * 0.26 + r * ch * 1.15, cw, ch)
        pygame.draw.rect(s, (250, 245, 230) if i in (0, 4) else (185, 188, 196), cell, border_radius=3 * k)
        if i in (0, 4):
            draw_text(s, "$500", font(ch * 0.5, bold=True), (190, 30, 40), cell.center)
    pygame.draw.circle(s, (230, 190, 60), (t.right - 4 * k, t.bottom - 6 * k), h * k * 0.1)
    pygame.draw.circle(s, (255, 230, 120), (t.right - 4 * k, t.bottom - 6 * k), h * k * 0.1, width=2 * k)
    return pygame.transform.smoothscale(s, (w, h))


def art_keno(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    pygame.draw.rect(s, (10, 40, 80), (0, 0, w * k, h * k))
    rng = random.Random(7)
    size = min(w * k / 11, h * k / 5.2)
    x0 = w * k / 2 - size * 5
    for r in range(4):
        for c in range(10):
            n = r * 10 + c + 1
            rect = pygame.Rect(x0 + c * size, h * k * 0.12 + r * size, size * 0.9, size * 0.85)
            v = rng.random()
            col = (230, 180, 40) if v < 0.12 else (60, 200, 90) if v < 0.2 else (50, 110, 210) if v < 0.35 else (22, 34, 62)
            pygame.draw.rect(s, col, rect, border_radius=3 * k)
            draw_text(s, str(n), font(size * 0.4, bold=True), WHITE, rect.center)
    return pygame.transform.smoothscale(s, (w, h))


def art_sicbo(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    pygame.draw.rect(s, (110, 24, 22), (0, 0, w * k, h * k))
    pygame.draw.ellipse(s, (60, 20, 20), (w * k / 2 - h * k * 0.9, h * k * 0.12, h * k * 1.8, h * k * 0.76))
    pygame.draw.ellipse(s, GOLD_DARK, (w * k / 2 - h * k * 0.9, h * k * 0.12, h * k * 1.8, h * k * 0.76), width=2 * k)
    for i, (v, ang) in enumerate(((6, -15), (6, 10), (6, -5))):
        img = pygame.transform.rotozoom(make_die(v, int(h * k * 0.3)), ang, 1)
        s.blit(img, img.get_rect(center=(w * k / 2 + (i - 1) * h * k * 0.34, h * k * 0.5)))
    draw_text(s, "SMALL", font(h * k * 0.12, bold=True, serif=True), WHITE, (w * k * 0.13, h * k * 0.5))
    draw_text(s, "BIG", font(h * k * 0.12, bold=True, serif=True), WHITE, (w * k * 0.87, h * k * 0.5))
    return pygame.transform.smoothscale(s, (w, h))


def art_dragontiger(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    pygame.draw.rect(s, (70, 14, 20), (0, 0, w * k, h * k))
    draw_emblem(s, w * k * 0.2, h * k * 0.5, int(h * k * 0.3), True)
    draw_emblem(s, w * k * 0.8, h * k * 0.5, int(h * k * 0.3), False)
    for i, (card, ang) in enumerate(((("K", "H"), 8), (("7", "C"), -8))):
        img = pygame.transform.rotozoom(Assets_faces_cache[card], ang, h * k / 260)
        s.blit(img, img.get_rect(center=(w * k * (0.42 + i * 0.16), h * k * 0.5)))
    return pygame.transform.smoothscale(s, (w, h))


def art_lottery(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    for y in range(h * k):
        pygame.draw.line(s, lerp_col((30, 80, 60), (8, 24, 18), y / (h * k)), (0, y), (w * k, y))
    r = h * k * 0.14
    for i, n in enumerate((7, 12, 19, 23, 31)):
        c = (w * k / 2 + (i - 2) * r * 2.4, h * k * 0.48 + (i % 2) * r * 0.4)
        pygame.draw.circle(s, (0, 0, 0), (c[0] + 2 * k, c[1] + 3 * k), r)
        pygame.draw.circle(s, (230, 180, 40) if i == 2 else (245, 245, 240), c, r)
        draw_text(s, str(n), font(r * 0.95, bold=True), (30, 30, 30), c)
    draw_text(s, "JACKPOT $100,000", font(h * k * 0.12, bold=True), GOLD, (w * k / 2, h * k * 0.86))
    return pygame.transform.smoothscale(s, (w, h))


def art_deepdive(w, h):
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        pygame.draw.line(s, lerp_col((70, 170, 225), (3, 8, 24), y / h), (0, y), (w, y))
    for k in range(4):
        x = w * 0.1 + k * w * 0.28
        pygame.draw.polygon(s, lighten((70, 170, 225), 25), [(x, 0), (x + 14, 0), (x + 40, h * 0.5), (x + 20, h * 0.5)])
    sub = pygame.Surface((340, 200), pygame.SRCALPHA)
    draw_sub(sub, 150, 110, 0.3, True)
    sub = pygame.transform.rotozoom(sub, -12, h / 210)
    s.blit(sub, sub.get_rect(center=(w * 0.42, h * 0.5)))
    draw_creature(s, "jelly", w * 0.84, h * 0.3, h / 150, (210, 60, 90), -1, 1.0)
    draw_creature(s, "angler", w * 0.8, h * 0.78, h / 170, (90, 70, 75), -1, 0.5)
    return s


def art_counting(assets, w, h):
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        pygame.draw.line(s, lerp_col((24, 110, 64), (8, 50, 30), y / h), (0, y), (w, y))
    sc = h / 200
    for i, (card, v) in enumerate(((("5", "H"), "+1"), (("K", "S"), "-1"), (("8", "D"), "0"), (("2", "C"), "+1"))):
        img = pygame.transform.rotozoom(assets.faces[card], 0, sc)
        x = w * 0.08 + i * img.get_width() * 1.15
        s.blit(img, (x, h * 0.1))
        col = {"+1": (60, 200, 100), "0": (150, 150, 160), "-1": (230, 70, 70)}[v]
        draw_pill(s, v, font(max(9, h * 0.1), bold=True), (x + img.get_width() / 2, h * 0.1 + img.get_height() + 10),
                  WHITE, (*col, 255), None, pad=(5, 1))
    draw_text(s, "COUNT", font(h * 0.12, bold=True), GOLD, (w * 0.84, h * 0.3))
    draw_text(s, "+1", font(h * 0.38, bold=True), (90, 240, 130), (w * 0.84, h * 0.6), shadow=(0, 0, 0))
    return s


def art_pinball(w, h):
    k = 2
    s = pygame.Surface((w * k, h * k), pygame.SRCALPHA)
    for y in range(h * k):
        pygame.draw.line(s, lerp_col((30, 40, 110), (8, 8, 30), y / (h * k)), (0, y), (w * k, y))
    for i in range(10):
        a = i / 10 * 2 * math.pi
        pygame.draw.polygon(s, (45, 60, 150), [(w * k / 2, h * k * 0.55), (w * k / 2 + math.cos(a) * w * k,
                            h * k * 0.55 + math.sin(a) * w * k), (w * k / 2 + math.cos(a + 0.2) * w * k,
                            h * k * 0.55 + math.sin(a + 0.2) * w * k)])
    r = h * k * 0.13
    for x, y in ((0.36, 0.3), (0.64, 0.3), (0.5, 0.52)):
        pygame.draw.circle(s, (220, 60, 90), (w * k * x, h * k * y), r)
        pygame.draw.circle(s, WHITE, (w * k * x, h * k * y), r, width=2 * k)
        pygame.draw.circle(s, (255, 190, 210), (w * k * x, h * k * y), r * 0.4)
    for sx, d in ((0.3, 1), (0.7, -1)):
        pygame.draw.line(s, (230, 60, 60), (w * k * sx, h * k * 0.82), (w * k * (sx + d * 0.12), h * k * 0.9), int(h * k * 0.07))
    pygame.draw.circle(s, (225, 228, 235), (w * k * 0.56, h * k * 0.7), h * k * 0.06)
    draw_text(s, "PINBALL", font(h * k * 0.14, bold=True, serif=True), (255, 220, 100), (w * k * 0.84, h * k * 0.18))
    return pygame.transform.smoothscale(s, (w, h))


GAME_INFO = {       # scene -> (title, one-line description)
    "blackjack": ("BLACKJACK", "Beat the dealer without going over 21."),
    "poker": ("POKER", "Texas Hold'em against three computer players."),
    "baccarat": ("BACCARAT", "Bet on Player, Banker or a Tie."),
    "videopoker": ("VIDEO POKER", "Hold your best cards - Jacks or Better."),
    "bus": ("RIDE THE BUS", "Four guesses in a row. All four pays 20x!"),
    "dragontiger": ("DRAGON TIGER", "One card each - the highest card wins."),
    "roulette": ("ROULETTE", "Pick your numbers and spin the wheel."),
    "craps": ("CRAPS", "Roll the dice and hit your point before a 7."),
    "sicbo": ("SIC BO", "Three dice and dozens of ways to bet."),
    "wheel": ("WHEEL", "Spin for a multiplier from x0.1 to x5."),
    "cups": ("CUPS", "Follow the ball as the cups get shuffled."),
    "coinflip": ("COIN FLIP", "Heads or tails pays 2x. The edge pays 20x. Then double or nothing!"),
    "pusher": ("CHIP PUSHER", "Drop chips, push them over the edge. Just like the arcade!"),
    "slots": ("SLOTS", "Line up the symbols on 5 paylines."),
    "rocket": ("ROCKET", "Cash out before the rocket explodes!"),
    "plinko": ("PLINKO", "Drop balls through the pegs - edges pay 170x."),
    "mines": ("MINES", "Find the gems, dodge the mines, cash out."),
    "scratch": ("SCRATCH CARDS", "Scratch a ticket and match three prizes."),
    "keno": ("KENO", "Pick up to 10 numbers - 20 balls are drawn."),
    "deepdive": ("DEEP DIVE", "Dive the real ocean zones. Surface in time!"),
    "horses": ("HORSE RACING", "Back a horse to Win, Place or Show."),
    "stocks": ("STOCKS", "Buy and sell shares. The market never sleeps."),
    "lottery": ("LOTTERY", "A new draw every 5 minutes. Match 5 to win!"),
    "counting": ("CARD COUNTING", "Learn the Hi-Lo system, then practise with drills."),
    "pinball": ("PINBALL", "Real flippers and bumpers. Score big to win big!"),
    "crossy": ("CHICKEN CROSSING", "Hop across an endless road. Cash out before you get hit!"),
    "yesno": ("YES OR NO", "A new question to bet on every 30 seconds."),
    "mp_crash": ("CRASH PARTY", "Everyone rides one rocket. Last one out gets a bonus!"),
    "mp_highcard": ("HIGH CARD SHOWDOWN", "One card each - the highest card takes the whole pot."),
    "mp_liars": ("LIAR'S DICE", "Bluff about your hidden dice. Last player with dice wins."),
    "mp_bingo": ("BINGO NIGHT", "Everyone buys a card. First to call BINGO takes the pot!"),
}
NON_GAMES = {"counting"}        # lobby entries that aren't betting games (no stats, not needed for Grand Tour)
MP_ONLY = {"mp_crash", "mp_highcard", "mp_liars", "mp_bingo"}      # need a multiplayer game (not needed for Grand Tour)
CATEGORIES = [("CARDS", ["blackjack", "poker", "baccarat", "videopoker", "bus", "dragontiger"]),
              ("TABLE & DICE", ["roulette", "craps", "sicbo", "wheel", "cups", "coinflip"]),
              ("INSTANT WIN", ["slots", "rocket", "plinko", "mines", "scratch", "keno"]),
              ("ARCADE", ["crossy", "pinball", "deepdive", "pusher"]),
              ("SPECIAL", ["yesno", "horses", "stocks", "lottery"]),
              ("PARTY", ["mp_crash", "mp_highcard", "mp_liars", "mp_bingo"]),
              ("LEARN", ["counting"])]
DAILY_MAX = 1000


def daily_amount(streak):
    return min(DAILY_MAX, 200 + 100 * (streak - 1))


class Menu:
    def __init__(self, app):
        global Assets_faces_cache
        Assets_faces_cache = app.assets.faces
        self.app = app
        aw, ah = 358, 118
        a = app.assets
        makers = {
            "blackjack": lambda: art_blackjack(a, aw, ah), "poker": lambda: art_poker(a, aw, ah),
            "baccarat": lambda: art_baccarat(aw, ah), "videopoker": lambda: art_videopoker(a, aw, ah),
            "bus": lambda: art_bus(aw, ah), "dragontiger": lambda: art_dragontiger(aw, ah),
            "roulette": lambda: art_roulette(aw, ah), "craps": lambda: art_craps(aw, ah),
            "sicbo": lambda: art_sicbo(aw, ah), "wheel": lambda: art_wheel(aw, ah), "slots": lambda: art_slots(aw, ah),
            "rocket": lambda: art_rocket(aw, ah), "plinko": lambda: art_plinko(aw, ah), "mines": lambda: art_mines(aw, ah),
            "scratch": lambda: art_scratch(aw, ah), "keno": lambda: art_keno(aw, ah),
            "deepdive": lambda: art_deepdive(aw, ah), "horses": lambda: art_horses(aw, ah),
            "stocks": lambda: art_stocks(aw, ah), "lottery": lambda: art_lottery(aw, ah),
            "counting": lambda: art_counting(a, aw, ah), "pinball": lambda: art_pinball(aw, ah),
            "crossy": lambda: art_crossy(aw, ah), "cups": lambda: art_cups(aw, ah),
            "pusher": lambda: art_pusher(aw, ah), "coinflip": lambda: art_coinflip(aw, ah), "yesno": lambda: art_yesno(aw, ah),
            "mp_crash": lambda: art_party("crash")(aw, ah), "mp_highcard": lambda: art_party("hc")(aw, ah),
            "mp_liars": lambda: art_party("liar")(aw, ah), "mp_bingo": lambda: art_party("bingo")(aw, ah),
        }
        self.art = {}
        for key, make in makers.items():
            img = make()
            m = pygame.Surface(img.get_size(), pygame.SRCALPHA)       # round the corners of every picture
            pygame.draw.rect(m, (255, 255, 255, 255), m.get_rect(), border_radius=12)
            framed = img.copy()
            framed.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            self.art[key] = framed
        self.art_masks = {}
        for key, art in self.art.items():
            m = art.copy()
            m.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
            self.art_masks[key] = m
        self.tab = 0
        tw_ = min(222, (W - 40) // len(CATEGORIES))
        x0 = (W - (len(CATEGORIES) * tw_ - 10)) / 2
        self.tab_rects = [pygame.Rect(x0 + i * tw_, 144, tw_ - 10, 38) for i in range(len(CATEGORIES))]
        self.tw, self.th = 388, 206
        self.layout()
        self.refill = Button((W / 2 - 170, 640, 340, 58), "GO WORK", (25, 120, 60), 24, "EARN CHIPS AT A JOB")
        self.btn_daily = Button((40, 644, 280, 50), "DAILY BONUS", (25, 120, 60), 18)
        self.btn_profile = Button((W - 320, 644, 280, 50), "STATS & TROPHIES", (60, 50, 120), 18)
        self.btn_online = Button((250, 9, 210, 38), "MULTIPLAYER", (40, 90, 160), 16)
        self.btn_settings = Button((470, 9, 140, 38), "SETTINGS", (70, 70, 85), 16)
        # animated background pieces
        self.bg = self.make_bg(pattern=False)
        self.pattern = self.make_pattern()
        self.vignette = self.make_vignette()
        self.fx = pygame.Surface((W, H), pygame.SRCALPHA)
        self.t = 0.0
        self.enter_t = 0.0
        self.beams = [(W * 0.18, 0.35, 0.0, (255, 215, 140)), (W * 0.82, 0.42, 2.1, (255, 190, 120)),
                      (W * 0.5, 0.27, 4.0, (200, 170, 255))]
        self.dust = [[random.uniform(0, W), random.uniform(0, H), random.uniform(8, 30), random.uniform(1, 2.6),
                      random.uniform(0, 6.3)] for _ in range(90)]
        self.drift = [self.new_drifter(random.uniform(60, H + 60)) for _ in range(8)]
        tf = font(46, bold=True, serif=True)
        self.title = tf.render("GRAND ROYALE", True, GOLD)
        self.title_shadow = tf.render("GRAND ROYALE", True, (40, 10, 10))
        self.title_mask = tf.render("GRAND ROYALE", True, (255, 255, 255))
        th_ = self.title.get_height()
        self.band = pygame.Surface((160, th_), pygame.SRCALPHA)
        for i in range(120):
            pygame.draw.line(self.band, (255, 250, 225, int(210 * (1 - abs(i - 60) / 60))), (i + 40, 0), (i, th_), 2)
        self.title_glow = pygame.Surface((820, 180), pygame.SRCALPHA)
        for i in range(30):
            q = i / 29
            pygame.draw.ellipse(self.title_glow, (255, 190, 90, int(4 + 6 * q)),
                                (410 - 400 * (1 - q * 0.8), 90 - 80 * (1 - q * 0.8), 800 * (1 - q * 0.8),
                                 160 * (1 - q * 0.8)))
        self.tile_glow = pygame.Surface((self.tw + 40, self.th + 40), pygame.SRCALPHA)
        for i in range(10):
            pygame.draw.rect(self.tile_glow, (255, 210, 110, 10 + i * 6),
                             (i * 2, i * 2, self.tw + 40 - i * 4, self.th + 40 - i * 4), width=2, border_radius=26 - i)
        self.art_shine = pygame.Surface((90, ah), pygame.SRCALPHA)
        for i in range(60):
            pygame.draw.line(self.art_shine, (255, 255, 255, int(120 * (1 - abs(i - 30) / 30))), (i + 30, 0), (i, ah), 2)

    def layout(self):
        keys = CATEGORIES[self.tab][1]
        gap, per_row = 18, 3
        x0 = (W - (per_row * self.tw + (per_row - 1) * gap)) / 2
        self.tiles = []
        for i, key in enumerate(keys):
            row, col = divmod(i, per_row)
            in_row = min(per_row, len(keys) - row * per_row)
            shift = (per_row - in_row) * (self.tw + gap) / 2
            self.tiles.append((key, pygame.Rect(x0 + shift + col * (self.tw + gap), 194 + row * (self.th + 14),
                                                self.tw, self.th)))
        self.lift = [0.0] * len(self.tiles)
        self.hover_t = [0.0] * len(self.tiles)

    make_bg = staticmethod(Menu_make_bg)
    make_pattern = staticmethod(Menu_make_pattern)
    make_vignette = staticmethod(Menu_make_vignette)
    new_drifter = staticmethod(Menu_new_drifter)

    def can_refill(self):
        return self.app.net_worth() < min(CHIP_VALUES)

    def on_enter(self):
        self.enter_t = 0.0

    def set_tab(self, i):
        if i != self.tab:
            self.tab = i
            self.layout()
            self.enter_t = 0.0
            self.app.sfx("chip")

    def handle(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, r in enumerate(self.tab_rects):
                if r.collidepoint(e.pos):
                    self.set_tab(i)
                    return
            if self.btn_online.clicked(e.pos):
                if WEB:
                    self.app.effects.toast("MULTIPLAYER", "Playing with friends needs the downloaded version of the game")
                else:
                    self.app.scene = "online"
                return
            if self.btn_settings.clicked(e.pos):
                self.app.scene = "settings"
                return
            for key, rect in self.tiles:
                if rect.collidepoint(e.pos):
                    self.app.go(key)
                    return
            if self.can_refill() and self.refill.clicked(e.pos):
                self.app.scene = "work"
            elif self.btn_daily.clicked(e.pos, self.app.daily_ready()):
                self.app.claim_daily()
            elif self.btn_profile.clicked(e.pos):
                self.app.profile.open()
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.app.scene = "title"
            elif e.key in (pygame.K_LEFT, pygame.K_a):
                self.set_tab((self.tab - 1) % len(CATEGORIES))
            elif e.key in (pygame.K_RIGHT, pygame.K_d):
                self.set_tab((self.tab + 1) % len(CATEGORIES))
            elif e.unicode and e.unicode.isdigit() and 1 <= int(e.unicode) <= len(self.tiles):
                self.app.go(self.tiles[int(e.unicode) - 1][0])

    def update(self, dt):
        self.t += dt
        self.enter_t += dt
        mouse = pygame.mouse.get_pos()
        for i, (_, rect) in enumerate(self.tiles):
            hover = rect.collidepoint(mouse)
            self.lift[i] += ((8 if hover else 0) - self.lift[i]) * min(1, dt * 12)
            self.hover_t[i] = self.hover_t[i] + dt if hover else 0.0
        for d in self.dust:
            d[1] -= d[2] * dt
            d[0] += math.sin(self.t * 0.6 + d[4]) * 6 * dt
            if d[1] < -5:
                d[0], d[1] = random.uniform(0, W), H + 5
        for c in self.drift:
            c["y"] -= c["vy"] * dt
            c["rot"] += c["vr"] * dt
            if c["y"] < -60:
                c.update(self.new_drifter(H + 60))

    def draw_background(self, surf):
        surf.blit(self.bg, (0, 0))
        surf.blit(self.pattern, (-(self.t * 14 % 240), -(self.t * 9 % 240)))
        fx = self.fx
        fx.fill((0, 0, 0, 0))
        for base_x, speed, phase, col in self.beams:
            ang = math.sin(self.t * speed + phase) * 0.5
            ax, ay = base_x, -60
            for spread, alpha in ((0.17, 12), (0.11, 17), (0.05, 24)):
                p1 = (ax + math.sin(ang - spread) * 1100, ay + math.cos(ang - spread) * 1100)
                p2 = (ax + math.sin(ang + spread) * 1100, ay + math.cos(ang + spread) * 1100)
                pygame.draw.polygon(fx, (*col, alpha), [(ax, ay), p1, p2])
        for x, y, _, size, ph in self.dust:
            pygame.draw.circle(fx, (255, 220, 140, int(70 + 110 * (0.5 + 0.5 * math.sin(self.t * 3 + ph * 3)))),
                               (x, y), size)
        surf.blit(fx, (0, 0))
        for c in self.drift:
            img = pygame.transform.rotozoom(self.app.assets.chip(c["v"], c["r"]), c["rot"], 1)
            img.set_alpha(70)
            surf.blit(img, img.get_rect(center=(c["x"] + math.sin(self.t * 0.8 + c["sway"]) * 18, c["y"])))
        surf.blit(self.vignette, (0, 0))
        self.app.themefx.draw_backdrop(surf)

    def draw_title(self, surf):
        self.title_glow.set_alpha(int(150 + 90 * math.sin(self.t * 1.6)))
        surf.blit(self.title_glow, self.title_glow.get_rect(center=(W / 2, 90)))
        r = self.title.get_rect(center=(W / 2, 88))
        surf.blit(self.title_shadow, r.move(3, 3))
        surf.blit(self.title, r)
        phase = (self.t % 4.5) / 1.3
        if phase <= 1:
            tmp = pygame.Surface(r.size, pygame.SRCALPHA)
            tmp.blit(self.band, (-160 + (r.w + 160) * phase, 0))
            tmp.blit(self.title_mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            surf.blit(tmp, r)
        draw_text(surf, "C  A  S  I  N  O", font(15, bold=True), (220, 200, 160), (W / 2, 124))
        for side in (-1, 1):
            for k in range(6):
                lit = (k + int(self.t * 8)) % 3 == 0
                pygame.draw.circle(surf, (255, 235, 150) if lit else (110, 80, 40), (W / 2 + side * (90 + k * 18), 124), 3)

    def draw(self, surf):
        mouse = pygame.mouse.get_pos()
        self.draw_background(surf)
        self.draw_title(surf)
        for i, (r, (name, keys)) in enumerate(zip(self.tab_rects, CATEGORIES)):
            on = i == self.tab
            hover = r.collidepoint(mouse)
            pygame.draw.rect(surf, (200, 150, 40) if on else ((60, 45, 50) if hover else (30, 22, 30)), r, border_radius=19)
            pygame.draw.rect(surf, GOLD if on else GOLD_DARK, r, width=2, border_radius=19)
            draw_text(surf, f"{name}  ({len(keys)})", font(16, bold=True), (30, 15, 5) if on else (230, 210, 170), r.center)
        for i, (key, rect) in enumerate(self.tiles):
            e = max(0.0, min(1.0, (self.enter_t - i * 0.06) / 0.45))
            slide = (1 - e) ** 3 * 60
            r = rect.move(0, -self.lift[i] + slide)
            hover = rect.collidepoint(mouse)
            if hover:
                self.tile_glow.set_alpha(int(170 + 80 * math.sin(self.t * 5)))
                surf.blit(self.tile_glow, r.inflate(40, 40).topleft)
            pygame.draw.rect(surf, (5, 5, 8), r.move(0, 8 + self.lift[i] * 0.5), border_radius=16)
            pygame.draw.rect(surf, (28, 24, 34), r, border_radius=16)
            pygame.draw.rect(surf, lighten(GOLD, 30) if hover else GOLD_DARK, r, width=3 if hover else 2, border_radius=16)
            art = self.art[key]
            surf.blit(art, (r.x + 15, r.y + 14))
            if hover:
                p = (self.hover_t[i] % 1.8) / 0.7
                if p <= 1:
                    tmp = pygame.Surface(art.get_size(), pygame.SRCALPHA)
                    tmp.blit(self.art_shine, (-90 + (art.get_width() + 90) * p, 0))
                    tmp.blit(self.art_masks[key], (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
                    surf.blit(tmp, (r.x + 15, r.y + 14))
            title, desc = GAME_INFO[key]
            if self.app.net and key in ("poker", "blackjack"):
                desc = ("LIVE TABLE - your friends replace the computers." if key == "poker"
                        else "LIVE TABLE - play together against the dealer.")
            size = 23
            while size > 14 and font(size, bold=True, serif=True).size(title)[0] > r.w - 120:      # keep clear of PLAY
                size -= 1
            draw_text(surf, title, font(size, bold=True, serif=True), GOLD, (r.x + 18, r.y + 154), anchor="midleft")
            draw_text(surf, desc, font(14), (215, 205, 185), (r.x + 18, r.y + 182), anchor="midleft")
            draw_pill(surf, "LEARN" if key in NON_GAMES else "PLAY", font(15, bold=True), (r.right - 50, r.y + 154), WHITE,
                      (30, 150, 70, 255) if hover else (25, 115, 55, 255), GOLD, pad=(16, 4))
            if key == "stocks" and self.app.market.holdings:
                pnl = int(round(self.app.market.total_value() - self.app.market.total_cost()))
                draw_pill(surf, f"{'+' if pnl >= 0 else '-'}{money(abs(pnl))} in stocks", font(13, bold=True),
                          (r.right - 90, r.y + 116), WHITE, (*(UP_COL if pnl >= 0 else DOWN_COL), 240), None, pad=(10, 3))
            if key == "lottery":
                left = LOTTO_EVERY - (time.time() % LOTTO_EVERY)
                n = sum(1 for t in self.app.lottery.tickets if t["draw"] == Lottery.next_draw())
                draw_pill(surf, f"NEXT DRAW {int(left // 60)}:{int(left % 60):02d}" + (f"  -  {n} TICKETS" if n else ""),
                          font(13, bold=True), (r.right - 110, r.y + 30), WHITE, (20, 110, 60, 240), None, pad=(10, 3))
        # bottom row
        ready = self.app.daily_ready()
        amt = daily_amount(self.app.next_streak())
        if ready:
            glow = pygame.Surface((300, 70), pygame.SRCALPHA)
            pygame.draw.rect(glow, (120, 255, 150, int(60 + 50 * math.sin(self.t * 4))), glow.get_rect(), border_radius=30)
            surf.blit(glow, (30, 634))
            self.btn_daily.text = f"DAILY BONUS  {money(amt)}"
            self.btn_daily.hint = f"DAY {self.app.next_streak()} STREAK"
        else:
            now = time.localtime()
            left = 86400 - (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec)
            self.btn_daily.text = "DAILY BONUS"
            self.btn_daily.hint = f"NEXT IN {left // 3600}h {left % 3600 // 60}m"
        self.btn_daily.draw(surf, mouse, ready)
        got = len(self.app.achievements)
        self.btn_profile.hint = f"{got} / {len(ACHIEVEMENTS)} TROPHIES"
        self.btn_profile.draw(surf, mouse)
        if self.can_refill():
            glow = pygame.Surface((380, 80), pygame.SRCALPHA)
            pygame.draw.rect(glow, (120, 255, 150, int(60 + 50 * math.sin(self.t * 4))), glow.get_rect(), border_radius=30)
            surf.blit(glow, (W / 2 - 190, 629))
            self.refill.draw(surf, mouse)
        elif self.app.balance < min(CHIP_VALUES):
            draw_pill(surf, f"Low on cash? Sell some stocks to play.", font(15, bold=True), (W / 2, 668), GOLD,
                      (0, 0, 0, 200), GOLD_DARK)
        else:
            draw_text(surf, "Left / Right arrows switch tabs  -  number keys open a game  -  Esc: main menu", font(13),
                      (170, 160, 150), (W / 2, 668))
        draw_text(surf, "Play money only - chips have no cash value and can't be exchanged for real money.",
                  font(12), (150, 140, 130), (W / 2, 706))
        self.app.draw_top_bar(surf, None, lobby=False)
        self.app.draw_version(surf)
        net = self.app.net
        self.btn_online.text = f"ONLINE  ({len(net.players)} PLAYERS)" if net else "MULTIPLAYER"
        self.btn_online.color = (25, 120, 60) if net else (40, 90, 160)
        self.btn_online.draw(surf, pygame.mouse.get_pos())
        self.btn_settings.draw(surf, pygame.mouse.get_pos())

# --------------------------------------------------------------------------
# Screen-wide effects: win sparkles, chip rain, balance glow, scene fades
# --------------------------------------------------------------------------
class Effects:
    def __init__(self, app):
        self.app = app
        self.sparks = []
        self.chips = []
        self.flash = 0.0
        self.fade = 0.0
        self.toasts = []            # [title, subtitle, time shown]
        self.fx = pygame.Surface((W, H), pygame.SRCALPHA)
        self.glow = pygame.Surface((276, 64), pygame.SRCALPHA)
        for i in range(8):
            pygame.draw.rect(self.glow, (255, 215, 110, 18 + i * 12), (i, i, 276 - i * 2, 64 - i * 2), width=2,
                             border_radius=30 - i)

    def burst(self, x, y, n=30, colors=None):
        n = min(n, max(0, 500 - len(self.sparks)))
        for _ in range(n):
            a = random.uniform(0, 2 * math.pi)
            sp = random.uniform(60, 280)
            life = random.uniform(0.5, 1.1)
            self.sparks.append({"x": x, "y": y, "vx": math.cos(a) * sp, "vy": math.sin(a) * sp - 60, "life": life,
                                "max": life, "size": random.uniform(3, 7),
                                "col": random.choice(colors or [(255, 225, 110), (255, 255, 230), (255, 190, 70)])})

    def win(self):
        self.burst(W - 120, 30)
        self.flash = 1.0

    def chip_rain(self, n=45):
        for _ in range(n):
            self.chips.append({"x": random.uniform(20, W - 20), "y": random.uniform(-600, -40),
                               "vy": random.uniform(220, 420), "vx": random.uniform(-40, 40),
                               "spin": random.uniform(0, 6.3), "vs": random.uniform(4, 10),
                               "v": random.choice(CHIP_VALUES)})

    def toast(self, title, subtitle, kind=None):
        self.toasts.append([title, subtitle, 0.0, kind])

    @staticmethod
    def toast_len(kind):
        return 7.0 if kind else 3.6

    def update(self, dt):
        if self.toasts:
            self.toasts[0][2] += dt
            if self.toasts[0][2] > self.toast_len(self.toasts[0][3]):
                self.toasts.pop(0)
        for p in self.sparks:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["vy"] += 260 * dt
            p["vx"] *= 1 - dt * 1.5
            p["life"] -= dt
        self.sparks = [p for p in self.sparks if p["life"] > 0]
        for c in self.chips:
            c["y"] += c["vy"] * dt
            c["x"] += c["vx"] * dt
            c["spin"] += c["vs"] * dt
        self.chips = [c for c in self.chips if c["y"] < H + 40]
        self.flash = max(0.0, self.flash - dt * 1.4)
        self.fade = max(0.0, self.fade - dt * 3.5)

    def draw(self, surf):
        for c in self.chips:
            w = max(2, int(40 * abs(math.cos(c["spin"]))))
            img = pygame.transform.smoothscale(self.app.assets.chip(c["v"], 20), (w, 40))
            surf.blit(img, img.get_rect(center=(c["x"], c["y"])))
        if self.flash:
            self.glow.set_alpha(int(255 * self.flash))
            surf.blit(self.glow, (W - 276, -4))
        if self.sparks:
            self.fx.fill((0, 0, 0, 0))
            for p in self.sparks:
                k = p["life"] / p["max"]
                s, x, y = p["size"] * (0.5 + k * 0.5), p["x"], p["y"]
                pts = [(x, y - s * 2), (x + s * 0.45, y - s * 0.45), (x + s * 2, y), (x + s * 0.45, y + s * 0.45),
                       (x, y + s * 2), (x - s * 0.45, y + s * 0.45), (x - s * 2, y), (x - s * 0.45, y - s * 0.45)]
                pygame.draw.polygon(self.fx, (*p["col"], int(255 * k)), pts)
            surf.blit(self.fx, (0, 0))
        if self.toasts and self.toasts[0][3]:
            title, sub, t, kind = self.toasts[0]
            slide = min(1.0, t * 4, (self.toast_len(kind) - t) * 4)
            col = UP_COL if kind == "up" else DOWN_COL
            f_sub = font(15)
            box = pygame.Rect(0, 0, max(560, min(W - 60, f_sub.size(sub)[0] + 120)), 96)
            box.midtop = (W / 2, int(-100 + 166 * slide))
            pygame.draw.rect(surf, (0, 0, 0), box.move(4, 5), border_radius=16)
            pygame.draw.rect(surf, (20, 30, 24) if kind == "up" else (40, 16, 18), box, border_radius=16)
            pulse = 0.5 + 0.5 * math.sin(t * 10)
            pygame.draw.rect(surf, lerp_col(col, WHITE, pulse * 0.4), box, width=4, border_radius=16)
            pygame.draw.circle(surf, col, (box.x + 44, box.centery), 28)
            draw_arrow(surf, kind == "up", (box.x + 44, box.centery), 30, (255, 255, 255))
            draw_text(surf, "BREAKING MARKET NEWS", font(12, bold=True), col, (box.x + 88, box.y + 18), anchor="midleft")
            draw_text(surf, title, font(22, bold=True), WHITE, (box.x + 88, box.y + 45), anchor="midleft")
            draw_text(surf, sub, f_sub, (225, 225, 230), (box.x + 88, box.y + 74), anchor="midleft")
        elif self.toasts:
            title, sub, t, _ = self.toasts[0]
            slide = min(1.0, t * 4, (3.6 - t) * 4)
            box = pygame.Rect(0, 0, 520, 76)
            box.midtop = (W / 2, int(-80 + 146 * slide))
            pygame.draw.rect(surf, (0, 0, 0), box.move(4, 5), border_radius=16)
            pygame.draw.rect(surf, (30, 24, 40), box, border_radius=16)
            pygame.draw.rect(surf, GOLD, box, width=3, border_radius=16)
            pygame.draw.circle(surf, GOLD, (box.x + 40, box.centery), 22)
            pygame.draw.polygon(surf, (120, 80, 10), [(box.x + 40, box.centery - 13), (box.x + 44, box.centery - 4),
                                                       (box.x + 53, box.centery - 3), (box.x + 46, box.centery + 3),
                                                       (box.x + 48, box.centery + 13), (box.x + 40, box.centery + 7),
                                                       (box.x + 32, box.centery + 13), (box.x + 34, box.centery + 3),
                                                       (box.x + 27, box.centery - 3), (box.x + 36, box.centery - 4)])
            draw_text(surf, title, font(18, bold=True), GOLD, (box.x + 76, box.y + 26), anchor="midleft")
            draw_text(surf, sub, font(15), WHITE, (box.x + 76, box.y + 52), anchor="midleft")
        if self.fade:
            f = pygame.Surface((W, H))
            f.fill((0, 0, 0))
            f.set_alpha(int(255 * self.fade))
            surf.blit(f, (0, 0))

# --------------------------------------------------------------------------
# Achievements and the Stats & Trophies screen
# --------------------------------------------------------------------------
ACHIEVEMENTS = [     # (id, name, how to get it, bonus chips)
    ("first_win", "Beginner's Luck", "Win your first bet in any game", 100),
    ("tourist", "Grand Tour", "Play every game in the casino at least once", 1000),
    ("bj_natural", "Natural 21", "Get a blackjack", 100),
    ("five_card", "Five Card Charlie", "Win a blackjack hand holding 5 or more cards", 250),
    ("straight_up", "Right on the Number", "Win a single-number roulette bet", 250),
    ("jackpot", "Jackpot!", "Line up three 7s or three diamonds on the slots", 500),
    ("to_the_moon", "To the Moon", "Cash out the rocket at 10x or higher", 500),
    ("flush_cash", "Flush with Cash", "Win a poker hand with a flush or better", 250),
    ("long_shot", "Long Shot", "Win a WIN bet on a horse with under a 10% chance", 250),
    ("top_wheel", "Top of the Wheel", "Land on x5 on the multiplier wheel", 200),
    ("rode_bus", "Rode the Bus", "Get all four right in Ride the Bus", 500),
    ("point_maker", "Point Maker", "Make your point in craps with a Pass Line bet", 100),
    ("dead_heat", "Dead Heat", "Win a tie bet in baccarat", 250),
    ("edge_case", "Edge Case", "Land a Plinko ball in an edge slot", 500),
    ("minesweeper", "Minesweeper", "Uncover 10 gems in a single Mines round", 250),
    ("four_kind", "Quads!", "Get four of a kind or better in video poker", 250),
    ("scratch_big", "Scratch That", "Win 50x or more on a scratch card", 500),
    ("keno_master", "Keno Master", "Match 6 or more numbers in keno", 500),
    ("triple_threat", "Triple Threat", "Win a triple bet in Sic Bo", 500),
    ("tiger_tamer", "Tiger Tamer", "Win a tie bet in Dragon Tiger", 250),
    ("lucky_ticket", "Lucky Ticket", "Win any prize in the lottery", 100),
    ("challenger_deep", "Challenger Deep", "Reach the Hadal Zone in Deep Dive", 500),
    ("buy_low", "Buy Low, Sell High", "Make $500 of realized profit in stocks", 250),
    ("high_roller", "High Roller", "Reach a net worth of $10,000", 500),
    ("big_shot", "Big Shot", "Reach a net worth of $100,000", 2500),
    ("millionaire", "Millionaire", "Reach a net worth of $1,000,000", 10000),
    ("regular", "Regular", "Claim the daily bonus 7 days in a row", 1000),
    ("card_sharp", "Card Sharp", "Nail the running-count drill on FAST with 20+ cards", 500),
    ("mikki_mase", "Mikki Mase Jr.", "Win 25 baccarat hands in a row", 25000),
    ("employee_month", "Employee of the Month", "Finish a work shift with all 5 tasks done right", 250),
    ("chicken_cross", "Why Did The Chicken...", "Reach 50 roads in one game of Chicken Crossing", 2500),
    ("oracle", "The Oracle", "Win 5 Yes or No bets in a row", 1000),
    ("eagle_eye", "Eagle Eye", "Find the ball on HARD cups 3 times in a row", 1000),
    ("cup_legend", "Cup Legend", "Find the ball on EXTREME cups", 5000),
]
ACH_BY_ID = {a[0]: a for a in ACHIEVEMENTS}


class ProfileOverlay:
    PANEL = pygame.Rect(110, 66, 1060, 610)

    def __init__(self, app):
        self.app = app
        self.active = False
        self.tab = 0
        self.tabs = [pygame.Rect(self.PANEL.x + 30, self.PANEL.y + 20, 180, 40),
                     pygame.Rect(self.PANEL.x + 220, self.PANEL.y + 20, 200, 40)]
        self.x_rect = pygame.Rect(self.PANEL.right - 52, self.PANEL.y + 16, 38, 38)
        self.close_btn = Button((W / 2 - 100, 616, 200, 48), "CLOSE", (60, 50, 120), 20, "ESC")

    def open(self):
        self.active = True

    def handle(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_SPACE, pygame.K_RETURN):
            self.active = False
        elif e.type == pygame.KEYDOWN and e.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_TAB):
            self.tab = 1 - self.tab
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            for i, r in enumerate(self.tabs):
                if r.collidepoint(e.pos):
                    self.tab = i
                    return
            if self.x_rect.collidepoint(e.pos) or not self.PANEL.collidepoint(e.pos):
                self.active = False

    def draw(self, surf):
        dim = pygame.Surface((W, H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 175))
        surf.blit(dim, (0, 0))
        r = self.PANEL
        pygame.draw.rect(surf, (4, 3, 6), r.move(6, 8), border_radius=20)
        pygame.draw.rect(surf, (22, 18, 28), r, border_radius=20)
        pygame.draw.rect(surf, GOLD, r, width=3, border_radius=20)
        for i, (tr, label) in enumerate(zip(self.tabs, ["STATS", f"TROPHIES  {len(self.app.achievements)}/{len(ACHIEVEMENTS)}"])):
            on = i == self.tab
            pygame.draw.rect(surf, (200, 150, 40) if on else (40, 32, 44), tr, border_radius=20)
            draw_text(surf, label, font(17, bold=True), (30, 15, 5) if on else (220, 200, 170), tr.center)
        pygame.draw.rect(surf, (70, 30, 35), self.x_rect, border_radius=10)
        draw_text(surf, "X", font(20, bold=True), WHITE, self.x_rect.center)
        (self.draw_stats if self.tab == 0 else self.draw_trophies)(surf)
        draw_text(surf, "ESC or X to close", font(12), (150, 140, 130), (r.right - 80, r.bottom - 14))

    def draw_stats(self, surf):
        r, st = self.PANEL, self.app.stats
        rounds = sum(v["rounds"] for v in st.values())
        net = sum(v["returned"] - v["wagered"] for v in st.values())
        best = max([v["best"] for v in st.values()] + [0])
        summary = [("NET WORTH", money(int(self.app.net_worth())), WHITE), ("ROUNDS PLAYED", f"{rounds:,}", WHITE),
                   ("ALL-TIME RESULT", f"{'+' if net >= 0 else '-'}{money(abs(int(net)))}", UP_COL if net >= 0 else DOWN_COL),
                   ("BIGGEST WIN", money(int(best)), GOLD)]
        for k, (a, b, col) in enumerate(summary):
            x = r.x + 40 + k * 250
            draw_text(surf, a, font(12, bold=True), (180, 170, 150), (x, r.y + 84), anchor="midleft")
            draw_text(surf, b, font(24, bold=True), col, (x, r.y + 110), anchor="midleft")
        cols = [("GAME", r.x + 40, "midleft"), ("PLAYED", r.x + 420, "midright"), ("WINS", r.x + 520, "midright"),
                ("WAGERED", r.x + 680, "midright"), ("RESULT", r.x + 840, "midright"), ("BIGGEST WIN", r.x + 1010, "midright")]
        y0 = r.y + 146
        for name, x, anc in cols:
            draw_text(surf, name, font(12, bold=True), GOLD, (x, y0), anchor=anc)
        pygame.draw.line(surf, GOLD_DARK, (r.x + 30, y0 + 12), (r.right - 30, y0 + 12))
        keys = [k for _, ks in CATEGORIES for k in ks if k not in NON_GAMES and k not in MP_ONLY]
        for i, key in enumerate(keys):
            y = y0 + 30 + i * 21
            v = st.get(key)
            if i % 2:
                pygame.draw.rect(surf, (30, 25, 38), (r.x + 30, y - 10, r.w - 60, 21))
            draw_text(surf, GAME_INFO[key][0].title(), font(14, bold=True), WHITE if v else (120, 115, 110),
                      (r.x + 40, y), anchor="midleft")
            if not v:
                draw_text(surf, "not played yet", font(13), (120, 115, 110), (r.x + 420, y), anchor="midright")
                continue
            res = v["returned"] - v["wagered"]
            vals = [f"{v['rounds']:,}", f"{v['wins']:,}", money(int(v["wagered"])),
                    f"{'+' if res >= 0 else '-'}{money(abs(int(res)))}", money(int(v["best"]))]
            for (name, x, anc), val, col in zip(cols[1:], vals, [WHITE, WHITE, WHITE, UP_COL if res >= 0 else DOWN_COL, GOLD]):
                draw_text(surf, val, font(14, bold=True), col, (x, y), anchor=anc)

    def draw_trophies(self, surf):
        r = self.PANEL
        for i, (aid, name, desc, reward) in enumerate(ACHIEVEMENTS):
            col, row = divmod(i, 17)
            x = r.x + 34 + col * 510
            y = r.y + 82 + row * 30
            got = aid in self.app.achievements
            pygame.draw.circle(surf, GOLD if got else (60, 55, 65), (x + 14, y + 10), 14)
            if got:
                pygame.draw.lines(surf, (60, 40, 5), False, [(x + 7, y + 10), (x + 12, y + 15), (x + 21, y + 5)], 3)
            else:
                draw_text(surf, "?", font(14, bold=True), (140, 135, 145), (x + 14, y + 10))
            draw_text(surf, name, font(15, bold=True), GOLD if got else (170, 165, 175), (x + 38, y + 2), anchor="midleft")
            draw_text(surf, desc, font(12), (210, 205, 215) if got else (130, 125, 135), (x + 38, y + 19), anchor="midleft")
            draw_text(surf, f"+{money(reward)}", font(13, bold=True), (120, 230, 140) if got else (110, 105, 115),
                      (x + 480, y + 10), anchor="midright")

# --------------------------------------------------------------------------
# How-to-play guides
# --------------------------------------------------------------------------
# ("h", heading)  ("p", paragraph)  ("b", bullet point)  ("x", highlighted example)
HELP = {
    "blackjack": ("BLACKJACK", [
        ("h", "The goal"),
        ("p", "Get a hand that adds up closer to 21 than the dealer's hand - without going over 21."),
        ("h", "What the cards are worth"),
        ("b", "Number cards (2-10) are worth their number."),
        ("b", "Jack, Queen and King are worth 10."),
        ("b", "An Ace is worth 1 or 11 - whichever helps you more. A hand with an Ace counted as 11 "
              "is called \"soft\", and its total shows both ways, like 7/17."),
        ("h", "Playing a hand"),
        ("b", "Click chips at the bottom to make your bet, then press DEAL (or Space)."),
        ("b", "You get two cards face up. The dealer gets two cards, one of them face down."),
        ("b", "HIT (H) - take another card. You can hit as many times as you like."),
        ("b", "STAND (S) - you're happy with your total; it's the dealer's turn."),
        ("b", "DOUBLE (D) - double your bet and take exactly one more card. Good with 10 or 11."),
        ("b", "SPLIT (P) - if your first two cards have the same value, split them into two separate "
              "hands. This adds a second bet the same size as the first."),
        ("b", "If your total goes over 21 you BUST and lose that bet right away."),
        ("h", "The dealer's turn"),
        ("p", "The dealer flips their hidden card, then must keep taking cards until they reach 17 or more. "
              "If the dealer goes over 21, every hand still in play wins."),
        ("h", "Payouts"),
        ("b", "Win: you get double your bet back."),
        ("x", "Bet $100 and win  ->  you get $200 back ($100 profit)."),
        ("b", "Blackjack (an Ace + a 10-value card as your first two cards) pays 3 to 2."),
        ("x", "Bet $100 with a blackjack  ->  you get $250 back ($150 profit)."),
        ("b", "Tie (\"push\"): your bet is returned."),
        ("h", "Handy tips"),
        ("b", "Click the betting circle (or right-click) to take back your last chip. CLEAR removes all of them."),
        ("b", "REBET puts down the same bet as last hand."),
        ("b", "Learning to count cards? Press the COUNT button (or C) to show the Hi-Lo running count and true "
              "count. The Card Counting School in the lobby's LEARN tab teaches you how."),
    ]),
    "roulette": ("ROULETTE", [
        ("h", "The goal"),
        ("p", "Guess where the ball will land. The wheel has the numbers 0 to 36: half of 1-36 are red, "
              "half are black, and 0 is green."),
        ("h", "Placing bets"),
        ("b", "Click a chip in the tray to choose its value (it lifts up with a gold ring)."),
        ("b", "Click the table to put that chip down. The squares your bet covers light up as you hover."),
        ("b", "You can place as many bets as you like before spinning."),
        ("b", "Right-click a bet to take it back. UNDO removes the last chip, CLEAR removes everything."),
        ("b", "Press SPIN (or Space). REBET / SPIN AGAIN repeats your last bets."),
        ("h", "Kinds of bets and what they pay"),
        ("b", "One number - click the middle of a number. Pays 35 to 1."),
        ("b", "Two numbers - click the line between them. Pays 17 to 1."),
        ("b", "Four numbers - click the corner where they meet. Pays 8 to 1."),
        ("b", "A column (\"2 TO 1\") or a dozen (1st 12, 2nd 12, 3rd 12). Pays 2 to 1."),
        ("b", "Red or Black, Even or Odd, 1-18 or 19-36. Pays 1 to 1."),
        ("x", "\"Pays 35 to 1\": bet $10 on 17, the ball lands on 17  ->  you win $350 and keep your $10."),
        ("h", "Watch out for zero"),
        ("p", "If the ball lands on green 0, every Red/Black, Even/Odd, 1-18/19-36, dozen and column bet loses. "
              "That's the casino's edge."),
    ]),
    "slots": ("SLOTS", [
        ("h", "The goal"),
        ("p", "Spin the three reels and line up matching symbols."),
        ("h", "How to play"),
        ("b", "Click chips to set your bet. The bet stays the same for every spin until you press CLEAR BET."),
        ("b", "Press SPIN (or Space). Each spin costs your bet."),
        ("h", "Paylines"),
        ("p", "There are 5 lines, shown on the left: the top, middle and bottom rows, plus two diagonals. "
              "Your bet is split evenly across them, so each line gets 1/5 of your bet (the \"line bet\")."),
        ("h", "Winning"),
        ("b", "Three of the same symbol along any line wins. The paytable on the right shows how much, "
              "as a multiple of your line bet."),
        ("b", "Cherries are special: one cherry on the left reel pays 2x, two cherries in a row from the "
              "left pay 4x, even without a third."),
        ("b", "Several lines can win on the same spin - they all add up."),
        ("x", "Bet $100  ->  line bet $20.  Three bells on one line pay 35 x $20 = $700."),
        ("p", "Wins of 10 times your bet or more are a BIG WIN, with a shower of coins."),
    ]),
    "rocket": ("ROCKET", [
        ("h", "The goal"),
        ("p", "Cash out before the rocket explodes."),
        ("h", "How to play"),
        ("b", "Click chips to make your bet, then press LAUNCH (or Space)."),
        ("b", "The multiplier starts at 1.00x and climbs as the rocket flies higher."),
        ("b", "Press CASH OUT (or Space) at any time to collect your bet x the current multiplier."),
        ("b", "If the rocket explodes before you cash out, you lose your bet."),
        ("x", "Bet $100 and cash out at 2.50x  ->  you get $250 back ($150 profit)."),
        ("h", "When does it explode?"),
        ("p", "It's random every round. Sometimes it blows up at 1.00x straight away, sometimes it flies past "
              "100x. The higher the number, the less likely it is to get there. The history at the top shows "
              "recent crash points."),
        ("h", "Auto cash out"),
        ("p", "Click AUTO to pick a multiplier (1.5x, 2x, 3x, 5x or 10x). The game will cash out for you the "
              "moment the rocket reaches it - handy if you don't trust your reflexes."),
    ]),
    "poker": ("POKER  (TEXAS HOLD'EM)", [
        ("h", "The goal"),
        ("p", "Win chips from the three computer players, either by having the best hand at the end or by "
              "making everyone else give up (fold)."),
        ("h", "Sitting down"),
        ("b", "Click chips to choose your buy-in ($50 - $2,000). That's the money you bring to the table."),
        ("b", "Press SIT DOWN. When you're done, STAND UP (or LOBBY) puts your table chips back in your balance."),
        ("h", "How a hand works"),
        ("b", "Everyone gets 2 private cards. Only you can see yours."),
        ("b", "Two players must put in small forced bets called blinds ($5 and $10). This moves around the table "
              "each hand - the white \"D\" button shows the dealer."),
        ("b", "There's a round of betting, then 3 shared cards are dealt face up (the \"flop\"), another round, "
              "a 4th shared card (the \"turn\"), another round, and a 5th (the \"river\") and a final round."),
        ("b", "Your hand is the best 5 cards you can make from your 2 cards plus the 5 shared ones. "
              "The label next to your cards tells you what you've got."),
        ("h", "Your choices when it's your turn"),
        ("b", "FOLD (F) - give up this hand. You lose what you've already put in."),
        ("b", "CHECK (C) - pass without betting, if nobody has bet yet."),
        ("b", "CALL (C) - match the current bet to stay in."),
        ("b", "BET / RAISE - put in more. Choose the minimum, half the pot, or the whole pot."),
        ("b", "ALL IN (A) - bet every chip you have at the table."),
        ("h", "Winning"),
        ("p", "If everyone else folds, you win the pot. Otherwise the remaining players show their cards and "
              "the best hand takes it."),
        ("h", "Hands from best to worst"),
        ("b", "Straight Flush - five in a row, all the same suit."),
        ("b", "Four of a Kind - four cards of the same rank."),
        ("b", "Full House - three of a kind plus a pair."),
        ("b", "Flush - five cards of the same suit."),
        ("b", "Straight - five in a row (like 5-6-7-8-9)."),
        ("b", "Three of a Kind, then Two Pair, then Pair."),
        ("b", "High Card - nothing matches; your highest card counts."),
    ]),
    "horses": ("HORSE RACING", [
        ("h", "The goal"),
        ("p", "Bet on which horses will finish near the front."),
        ("h", "Types of bet"),
        ("b", "WIN - your horse must finish 1st. Pays the most."),
        ("b", "PLACE - your horse must finish 1st or 2nd."),
        ("b", "SHOW - your horse must finish in the top 3. Easiest to hit, pays the least."),
        ("h", "Reading the numbers"),
        ("p", "The number on each button (like x4.5) is how much you get back for every $1 you bet, if it hits."),
        ("x", "$10 on WIN x4.5 and your horse wins  ->  you get $45 back ($35 profit)."),
        ("p", "Each horse also shows its win chance. Favourites win more often but pay less; long shots rarely "
              "win but pay a lot."),
        ("h", "How to play"),
        ("b", "Click a chip in the tray to choose its value, then click WIN, PLACE or SHOW next to a horse."),
        ("b", "You can bet on several horses and bet types at once. Right-click a bet to remove it."),
        ("b", "Press START RACE (or Space) - or WATCH RACE if you just want to watch."),
        ("b", "After the race, NEXT RACE brings out a brand new field of horses. REBET repeats your last bets."),
    ]),
    "wheel": ("MULTIPLIER WHEEL", [
        ("h", "The goal"),
        ("p", "Spin the wheel and hope the pointer stops on a big multiplier."),
        ("h", "How to play"),
        ("b", "Click chips to set your bet. It stays the same for every spin until you press CLEAR BET."),
        ("b", "Press SPIN (or Space). You get back your bet times the number the pointer lands on."),
        ("h", "Winning and losing"),
        ("b", "x1.2, x1.5, x2, x3 and x5 (green, teal, blue, purple, gold) are wins."),
        ("b", "x0.1, x0.3, x0.5 and x0.8 (reds and oranges) give back only part of your bet - you lose the rest."),
        ("x", "Bet $100:  x3  ->  $300 back (+$200).   x0.3  ->  $30 back (-$70)."),
        ("p", "The chance of landing on each multiplier is listed next to the wheel. About 1 spin in 3 is a win."),
    ]),
    "bus": ("RIDE THE BUS", [
        ("h", "The goal"),
        ("p", "Guess your way through four cards. Every correct guess makes your prize bigger - "
              "but one wrong guess and the bus leaves without you (you lose your bet)."),
        ("h", "The four rounds"),
        ("b", "Round 1 - RED OR BLACK?  Hearts and diamonds are red, spades and clubs are black."),
        ("b", "Round 2 - HIGHER OR LOWER?  Will the second card be higher or lower than the first?"),
        ("b", "Round 3 - INSIDE OR OUTSIDE?  Will the third card land between your first two cards, "
              "or outside them?"),
        ("b", "Round 4 - PICK THE SUIT.  Hearts, diamonds, spades or clubs?"),
        ("h", "Cashing out"),
        ("p", "After each correct guess you choose: CASH OUT and take the prize, or keep riding for a bigger one."),
        ("b", "After round 1: 1.9x your bet.   After round 2: 2.6x.   After round 3: 4x."),
        ("b", "Get all four right and you win 20x your bet automatically."),
        ("x", "Bet $100:  cash out after round 2  ->  $260.   Go all the way  ->  $2,000."),
        ("h", "Rules to know"),
        ("b", "Aces are high (above Kings). 2 is the lowest card."),
        ("b", "Ties lose. If your second card is the same rank as the first, or your third card matches either "
              "of the first two, that round is lost."),
        ("b", "If your first two cards are the same rank, nothing can be \"inside\" - pick OUTSIDE."),
        ("h", "Tips"),
        ("b", "Round 2 is easy when the first card is very high or very low - pick the side with more cards."),
        ("b", "Round 4 is only about a 1 in 4 shot. Cashing out at 4x is often the smart move."),
        ("b", "Keys: R/B, H/L, I/O, 1-4 for the suits, C to cash out, Space to deal."),
    ]),
    "craps": ("CRAPS", [
        ("h", "The goal"),
        ("p", "Bet on what two dice will roll. Craps looks complicated, but you only need the Pass Line to play."),
        ("h", "The basic game (Pass Line)"),
        ("b", "Pick a chip, click PASS LINE, then press ROLL DICE (or Space)."),
        ("b", "The first roll is the \"come-out\" roll. A 7 or 11 wins right away. A 2, 3 or 12 (\"craps\") loses."),
        ("b", "Any other number (4, 5, 6, 8, 9 or 10) becomes the POINT. The white ON puck marks it."),
        ("b", "Now keep rolling. Roll the point again before a 7 and you win. Roll a 7 first (\"seven out\") "
              "and you lose."),
        ("x", "$10 on the Pass Line, come-out roll is 6 (the point). Roll 9, 4, then 6  ->  you win $10."),
        ("h", "Other bets"),
        ("b", "DON'T PASS - the opposite of the Pass Line. Wins on 2 or 3, loses on 7 or 11, 12 is a tie. "
              "Once a point is set, it wins if a 7 comes before the point."),
        ("b", "ODDS - after a point is set, add up to 3x your Pass Line bet here. It pays true odds with no "
              "house edge: 2:1 on 4 or 10, 3:2 on 5 or 9, 6:5 on 6 or 8."),
        ("b", "FIELD - a one-roll bet. Wins on 2, 3, 4, 9, 10, 11 or 12 (2 pays double, 12 triple). "
              "Loses on 5, 6, 7 or 8."),
        ("b", "PLACE 6 / PLACE 8 - click the 6 or 8 box. Wins 7:6 every time that number rolls before a 7. "
              "Only works while a point is on."),
        ("b", "ONE ROLL BETS - Any Seven pays 4:1, Any Craps (2, 3 or 12) pays 7:1, Yo Eleven pays 15:1. "
              "They win or lose on the very next roll."),
        ("h", "Good to know"),
        ("b", "Winning bets stay on the table and you're paid the profit, just like a real craps table. "
              "Right-click a bet (or CLEAR) to take it back."),
        ("b", "Pass and Don't Pass bets are locked in while a point is on - you can't leave the table until the "
              "point is decided."),
        ("b", "The Pass Line plus Odds is the best-value bet in the whole casino."),
    ]),
    "baccarat": ("BACCARAT", [
        ("h", "The goal"),
        ("p", "Two hands are dealt: PLAYER and BANKER. You bet on which one will end up closer to 9. "
              "(You're not the Player - it's just the name of the hand.)"),
        ("h", "How the cards count"),
        ("b", "Ace = 1.  2 to 9 = their number.  10, Jack, Queen, King = 0."),
        ("b", "Only the last digit of the total counts: 7 + 8 = 15, which counts as 5."),
        ("h", "How to play"),
        ("b", "Pick a chip, click PLAYER, BANKER or TIE (or a pair bet), then press DEAL (or Space)."),
        ("b", "Each hand gets two cards. Whether a third card is drawn follows fixed casino rules - "
              "you don't make any decisions, just watch."),
        ("b", "A total of 8 or 9 on the first two cards is a \"natural\" and nobody draws."),
        ("h", "Payouts"),
        ("b", "PLAYER wins pay 1:1.  BANKER wins pay 0.95:1 (the casino keeps a 5% commission)."),
        ("b", "TIE pays 8:1. If it's a tie, Player and Banker bets are simply returned."),
        ("b", "PLAYER PAIR / BANKER PAIR pay 11:1 if that hand's first two cards are the same rank."),
        ("x", "$100 on BANKER and Banker wins  ->  you get $195 back ($95 profit)."),
        ("h", "The scoreboard"),
        ("p", "The board at the top shows recent results: blue P for Player, red B for Banker, green T for Tie. "
              "Every hand is independent, so past results can't predict the next one - but everyone watches them anyway!"),
        ("h", "Tip"),
        ("p", "Banker wins slightly more often than Player, which is why it pays a little less. Both are good bets; "
              "Tie and the pair bets pay big but rarely hit."),
        ("h", "The Mikki Mase Jr. challenge"),
        ("p", "Win 25 hands in a row to unlock the rarest trophy in the casino (+$25,000). A hand where you lose "
              "money resets your streak; a tie that just returns your bet doesn't. Good luck - you'll need it!"),
    ]),
    "plinko": ("PLINKO", [
        ("h", "The goal"),
        ("p", "Drop a ball from the top. It bounces off the pegs and lands in one of the slots at the bottom. "
              "Your bet is multiplied by the number on that slot."),
        ("h", "How to play"),
        ("b", "Click chips to set your bet, then press DROP BALL (or Space). Each ball costs your bet."),
        ("b", "You can drop lots of balls one after another - they all fall at the same time."),
        ("b", "Pick a RISK level on the right: LOW, MEDIUM or HIGH."),
        ("h", "Risk levels"),
        ("b", "LOW: the edges pay 10x and the middle slots lose only a little."),
        ("b", "HIGH: the edges pay a massive 170x, but the middle slots give back only 0.15x."),
        ("x", "Bet $10 on HIGH and hit an edge  ->  $1,700!"),
        ("p", "Each peg is a 50/50 bounce left or right, so most balls land near the middle and the edges are rare."),
    ]),
    "mines": ("MINES", [
        ("h", "The goal"),
        ("p", "25 tiles hide gems and mines. Every gem you uncover raises your multiplier. "
              "Hit a mine and you lose your bet. Cash out any time!"),
        ("h", "How to play"),
        ("b", "Choose how many mines to hide with the - and + buttons (1 to 24)."),
        ("b", "Set your bet with chips and press START."),
        ("b", "Click tiles one at a time. The panel shows your current multiplier and what the next gem would pay."),
        ("b", "Press CASH OUT (or Space) to take your winnings."),
        ("h", "More mines, more money"),
        ("p", "With 1 mine, each gem only adds a little. With 10 or more mines, a couple of gems can multiply "
              "your bet many times - but one wrong click ends it."),
        ("x", "3 mines, 5 gems found  ->  about 2.1x your bet."),
    ]),
    "videopoker": ("VIDEO POKER  (JACKS OR BETTER)", [
        ("h", "The goal"),
        ("p", "Make the best 5-card poker hand you can. You get one chance to swap cards."),
        ("h", "How to play"),
        ("b", "Set your bet with chips and press DEAL (or Space). You get 5 cards."),
        ("b", "Click the cards you want to KEEP (or press 1-5). They show HELD."),
        ("b", "Press DRAW. Every card you didn't hold is replaced with a new one."),
        ("b", "Your final hand is paid from the table at the top."),
        ("h", "What pays"),
        ("b", "A pair of Jacks, Queens, Kings or Aces is the smallest win (you get your bet back)."),
        ("b", "Two Pair pays 2x, Three of a Kind 3x, Straight 4x, Flush 5x, Full House 8x, Four of a Kind 25x, "
              "Straight Flush 50x and a Royal Flush 800x!"),
        ("h", "Tips"),
        ("b", "Always hold any winning hand you're dealt."),
        ("b", "Holding a high pair (Jacks or better) is almost always right."),
        ("b", "With nothing good, hold your high cards (J, Q, K, A) and draw the rest."),
    ]),
    "scratch": ("SCRATCH CARDS", [
        ("h", "The goal"),
        ("p", "Scratch off the silver coating to reveal 9 prize amounts. If three of them are the same, "
              "you win that amount!"),
        ("h", "How to play"),
        ("b", "Pick a ticket on the left: $10, $50 or $250. Pricier tickets have bigger prizes."),
        ("b", "Press BUY TICKET."),
        ("b", "Hold the mouse button down and rub over the silver spots to scratch them off."),
        ("b", "Once all 9 are revealed, you find out if you won. REVEAL ALL skips the scratching."),
        ("h", "Prizes"),
        ("p", "Prizes go from 1x the ticket price up to 500x. About 1 ticket in 3.5 wins something."),
        ("x", "A $50 Gold Rush ticket with three $500 spots  ->  you win $500."),
    ]),
    "keno": ("KENO", [
        ("h", "The goal"),
        ("p", "Pick up to 10 numbers from 1 to 80. Then 20 numbers are drawn. The more of yours that come up, "
              "the more you win."),
        ("h", "How to play"),
        ("b", "Click numbers on the board to pick them (gold). QUICK PICK chooses random ones for you."),
        ("b", "Set your bet with chips and press PLAY (or Space)."),
        ("b", "Balls are drawn one at a time. Your matches glow green."),
        ("b", "Your picks stay for the next game - just press PLAY again."),
        ("h", "Paytable"),
        ("p", "The table on the right changes depending on how many numbers you picked. Picking more numbers "
              "means you need more hits, but the top prizes get huge (up to 100,000x for 10 out of 10!)."),
    ]),
    "sicbo": ("SIC BO", [
        ("h", "The goal"),
        ("p", "Three dice are shaken in a bowl. You bet on what they'll show."),
        ("h", "How to play"),
        ("b", "Pick a chip, click the table to place bets (as many as you like), then press ROLL DICE."),
        ("b", "Right-click a bet to take it back. REBET repeats your last bets."),
        ("h", "The bets"),
        ("b", "SMALL (total 4-10) or BIG (total 11-17) - pays 1:1. Loses if all three dice match."),
        ("b", "ODD or EVEN total - pays 1:1. Also loses on three of a kind."),
        ("b", "A number (1-6, bottom row) - pays 1:1 for each die showing it, up to 3:1."),
        ("b", "A total (4 to 17) - pays between 6:1 and 60:1. The rarer the total, the more it pays."),
        ("b", "A specific double (two of the same number) pays 10:1."),
        ("b", "ANY TRIPLE pays 30:1, a specific triple pays 180:1!"),
    ]),
    "dragontiger": ("DRAGON TIGER", [
        ("h", "The goal"),
        ("p", "One card is dealt to DRAGON and one to TIGER. Whichever card is higher wins. That's it!"),
        ("h", "How to play"),
        ("b", "Pick a chip, click DRAGON, TIGER, TIE or SUITED TIE, then press DEAL (or Space)."),
        ("b", "Card ranks: Ace is lowest (1), then 2 to 10, Jack, Queen, and King is highest (13). Suits don't matter."),
        ("h", "Payouts"),
        ("b", "DRAGON or TIGER pays 1:1."),
        ("b", "TIE (both cards the same rank) pays 8:1. On a tie, Dragon and Tiger bets lose only half."),
        ("b", "SUITED TIE (same rank AND same suit) pays 50:1."),
        ("p", "The scoreboard shows past results: red D for Dragon, orange T for Tiger, green = for a tie."),
    ]),
    "lottery": ("LOTTERY", [
        ("h", "The goal"),
        ("p", "Pick 5 numbers from 1 to 36. If your numbers match the ones drawn, you win!"),
        ("h", "How to play"),
        ("b", "Click 5 numbers (or QUICK PICK), then press BUY TICKET. Each ticket costs $10."),
        ("b", "You can buy up to 20 tickets for each draw."),
        ("b", "A new draw happens every 5 minutes on the real clock - watch the countdown."),
        ("b", "You don't have to wait here! The draw happens while you play other games (or even with the "
              "game closed), and you'll get a pop-up with your result."),
        ("h", "Prizes per ticket"),
        ("b", "Match 2: $20.  Match 3: $100.  Match 4: $2,000.  Match all 5: $100,000 JACKPOT!"),
        ("p", "Like a real lottery, the jackpot is incredibly rare - about 1 chance in 377,000 per ticket."),
    ]),
    "deepdive": ("DEEP DIVE", [
        ("h", "The goal"),
        ("p", "Pilot a submarine down through the real layers of the ocean. The deeper you go, the more your bet "
              "is multiplied. Surface before something goes wrong!"),
        ("h", "How to play"),
        ("b", "Set your bet with chips and press DIVE! (or Space)."),
        ("b", "Watch the depth, pressure and multiplier climb as you sink."),
        ("b", "Press SURFACE (or Space) any time to cash out at the current multiplier."),
        ("b", "If the hull cracks or a giant squid grabs the sub before you surface, you lose your bet."),
        ("b", "Reach the very bottom of the Mariana Trench and you automatically win 50x!"),
        ("h", "The ocean zones"),
        ("b", "Sunlight Zone (0 - 200 m) - about 1x to 1.3x. Sunlight lets plants grow."),
        ("b", "Twilight Zone (200 - 1,000 m) - up to 2x. Dim blue light."),
        ("b", "Midnight Zone (1,000 - 4,000 m) - up to 4x. Total darkness; animals glow."),
        ("b", "Abyssal Zone (4,000 - 6,000 m) - up to 10x. Near freezing, crushing pressure."),
        ("b", "Hadal Zone (6,000 - 10,935 m) - up to 50x. The deep ocean trenches."),
        ("h", "Fun facts"),
        ("p", "Real animals from each zone swim past as you dive. When one appears, a FUN FACT card tells you "
              "something true about it - there are 20 animals to discover!"),
    ]),
    "pinball": ("PINBALL", [
        ("h", "The goal"),
        ("p", "Your bet buys one game with 3 balls. Keep the ball in play and rack up points - the higher your "
              "final score, the bigger your prize (see the PRIZES table on the right)."),
        ("h", "Controls"),
        ("b", "Launch: hold SPACE (or the Down arrow) to pull back the plunger. Let go to shoot. Hold longer for more power."),
        ("b", "Left flipper: LEFT arrow, Z or left mouse button."),
        ("b", "Right flipper: RIGHT arrow, M or right mouse button."),
        ("b", "If the ball falls between the flippers, you lose it. Lose all 3 and the game is over."),
        ("h", "Scoring"),
        ("b", "Pop bumpers: 100 points.  Slingshots (the red triangles): 50 points."),
        ("b", "Drop targets at the top: 250 each. Knock down all three for a 1,500 bonus."),
        ("b", "Roll through the R, Y and L lanes at the top to light them. Light all three and your score "
              "multiplier goes up (up to x5) - every point after that is multiplied!"),
        ("b", "Ball save: lose a ball in the first 4 seconds and you get it back (once per ball)."),
        ("h", "Prizes"),
        ("p", "Under 3,500 points pays nothing, 7,000 gets your bet back, and 250,000+ pays 10x. "
              "Tip: flip just as the ball reaches the flipper to aim it back up the table."),
    ]),
    "work": ("GO WORK", [
        ("h", "Out of chips?"),
        ("p", "Instead of free chips, you can earn them. Pick a job on the job board and complete a shift of "
              "5 quick tasks. Each task done right pays $60, plus up to $40 more for being fast."),
        ("h", "The jobs"),
        ("b", "Grocery Cashier - count out the exact change with bills and coins."),
        ("b", "Pizza Delivery - find the right house number on the right street."),
        ("b", "Dishwasher - hold the mouse button and scrub the plate clean."),
        ("b", "Barista - memorize the order, then build the drink from memory."),
        ("b", "Marine Biologist - count one kind of sea animal on a reef survey."),
        ("h", "Payday"),
        ("p", "At the end of the shift you get a paycheck. Cash it in and it turns into chips. Get all 5 tasks "
              "right for the Employee of the Month trophy!"),
    ]),
    "online": ("MULTIPLAYER", [
        ("h", "Play with friends on the same Wi-Fi"),
        ("p", "Everyone needs the game on their own computer, and you all need to be on the same Wi-Fi. "
              "No accounts or sign-ups - one person's game acts as the host."),
        ("b", "Type your name, then one person presses HOST A GAME."),
        ("b", "Everyone else opens MULTIPLAYER - the host's game shows up in the list. Press JOIN. "
              "(If it doesn't show up, type the address the host's screen shows.)"),
        ("b", "The first time you host, Windows may ask if Python can use the network. Click Allow "
              "(private networks only)."),
        ("h", "What you can do together"),
        ("b", "Everyone can play any game at any time."),
        ("b", "POKER - a 4-seat table. Friends who sit down replace the computer players."),
        ("b", "BLACKJACK - everyone sits at one table and plays against the same dealer."),
        ("b", "Every other game - if friends are in the same game, a panel shows their bets and whether they "
              "won or lost. Press TAB to hide it."),
        ("b", "CHAT - click the CHAT button at the top (or press / ) to talk to everyone in the game. "
              "Enter sends, Esc closes. New messages pop up in the top-right corner."),
        ("h", "Your chips"),
        ("p", "Everyone keeps their own chips and their own save. If the host stops hosting, chips you had "
              "sitting at a table come back to you."),
    ]),
    "netpoker": ("POKER (MULTIPLAYER)", [
        ("h", "A table of real players"),
        ("p", "Texas Hold'em with your friends. The table always has 4 players: computer players sit in any "
              "empty seat, and when a friend sits down they take a computer player's place. The rules are the "
              "same as the normal poker table."),
        ("b", "Choose a buy-in with the chips and press SIT DOWN. If a hand is already going, you sit down "
              "as soon as it ends."),
        ("b", "Up to 4 real players per table. Press DEAL NOW when you're ready - if every real player does, "
              "the next hand starts right away."),
        ("b", "You have 30 seconds to act, or you'll automatically check (or fold)."),
        ("b", "FOLD (F), CHECK / CALL (C), bet or raise, or ALL IN (A)."),
        ("b", "STAND UP between hands to take your chips back. Leaving the table stands you up."),
    ]),
    "netbj": ("BLACKJACK (MULTIPLAYER)", [
        ("h", "Everyone against the dealer"),
        ("p", "Up to 4 players sit at the same table. Everyone plays their own hand against the dealer - "
              "you're not playing against each other."),
        ("b", "Set your bet with the chips and press PLACE BET (Space)."),
        ("b", "Once someone bets, the others have 12 seconds to bet too. Then the cards are dealt."),
        ("b", "Players take turns from left to right: HIT (H), STAND (S) or DOUBLE (D). "
              "You have 20 seconds, then you stand automatically."),
        ("b", "Blackjack pays 3 to 2 and the dealer stands on all 17s. (Splitting isn't available at the "
              "multiplayer table.)"),
    ]),
    "mp_crash": ("CRASH PARTY", [
        ("h", "One rocket for everyone"),
        ("p", "A party game - you need to be in a multiplayer game. Everyone bets on the same rocket, and "
              "everyone sees who's still riding."),
        ("b", "Set your bet with the chips and press BET before the rocket launches (10 seconds between rounds)."),
        ("b", "The multiplier climbs while it flies. Press CASH OUT (Space) to take your bet times the multiplier."),
        ("b", "If it blows up before you cash out, you lose your bet."),
        ("b", "LAST ONE OUT: whoever cashes out at the highest multiplier gets 20% extra on top of their profit."),
        ("x", "Bet $100, cash out at x2.50  ->  you get $250. Last one out  ->  +$30 bonus."),
    ]),
    "mp_highcard": ("HIGH CARD SHOWDOWN", [
        ("h", "Highest card wins"),
        ("p", "A party game - you need to be in a multiplayer game. Everyone puts in the same amount and "
              "gets one card. The highest card takes the whole pot."),
        ("b", "Set an amount with the chips and press START. The others have 12 seconds to JOIN for the same amount."),
        ("b", "Aces are high, 2s are low. Suits don't matter."),
        ("b", "A tie at the top means SUDDEN DEATH: the tied players get a new card each until someone wins."),
        ("b", "If nobody joins, you get your chips back."),
    ]),
    "mp_liars": ("LIAR'S DICE", [
        ("h", "Bluffing with dice"),
        ("p", "A party game - you need to be in a multiplayer game. Everyone pays in the same amount and "
              "rolls 5 dice that only they can see. The last player with dice left takes the pot."),
        ("b", "On your turn, bid how many dice of one face you think there are on the WHOLE table - "
              "like 'four 5s'. Each bid must be higher: more dice, or the same number of a bigger face."),
        ("b", "Or, if you think the last bid is too high, call LIAR! Everyone shows their dice."),
        ("b", "If there are at least as many as the bid said, the caller loses a die. If not, the bidder loses one."),
        ("b", "Everyone re-rolls and a new round starts. Lose all your dice and you're out."),
        ("b", "You have 30 seconds per turn - if you run out, LIAR is called for you."),
    ]),
    "mp_bingo": ("BINGO NIGHT", [
        ("h", "Eyes down"),
        ("p", "A party game - you need to be in a multiplayer game. Everyone buys a card for the same price, "
              "and the first to finish a line takes the whole pot."),
        ("b", "Set a price with the chips and press START. Others have 12 seconds to buy a card too."),
        ("b", "A number is called every 3 seconds and marked on your card for you. The middle square is free."),
        ("b", "When you have a full row, column or diagonal, press BINGO! (or B) - fast! "
              "If you don't, it's called for you after a few seconds, but someone else might beat you."),
    ]),
    "coinflip": ("COIN FLIP", [
        ("h", "Call it"),
        ("b", "Click chips to set your bet."),
        ("b", "Press HEADS (H) or TAILS (T) - call it right and you get 2x your bet."),
        ("b", "Or call the EDGE (E) - the coin landing standing up on its skinny side. It only happens about 1 flip "
              "in 22, but it pays 20x!"),
        ("x", "Bet $100 on HEADS, it lands HEADS  ->  you've won $200."),
        ("h", "Double or nothing"),
        ("p", "After a win you can COLLECT (C) your winnings - or let them ride on another flip. Call it right and "
              "your win doubles (or goes 20x on the edge). Call it wrong and you lose the lot."),
        ("x", "$100 -> win $200 -> double $400 -> double $800 -> collect $800."),
        ("b", "If you call heads or tails and it lands on the edge, you lose."),
        ("b", "Leaving the table collects whatever you've won."),
    ]),
    "pusher": ("CHIP PUSHER", [
        ("h", "Just like the arcade"),
        ("p", "The table is covered in chips, and a pusher slides back and forth shoving them towards the front. "
              "Drop your chips in, and try to push the pile over the edge!"),
        ("b", "Set how much each drop costs with the chips at the bottom."),
        ("b", "Move the mouse to aim the chute left or right. Click the table (or hold the mouse, or Space) to drop."),
        ("b", "Chips that fall off the FRONT edge land in the win tray - they're yours."),
        ("b", "Chips pushed into the side drains near the front are LOST."),
        ("h", "Bonuses"),
        ("b", "GOLD CHIP - a gold chip worth 5x your drop lands on the table. Push it off for a big win."),
        ("b", "CHIP SHOWER - 8 free chips rain onto the table."),
        ("b", "SIDE WALLS - the side drains close for 15 seconds, so nothing is lost."),
        ("h", "The table is saved"),
        ("p", "Chips you leave on the table stay there, even if you close the game - come back later and keep pushing. "
              "Bigger drops make bigger chips, and every chip pays out what it's worth."),
    ]),
    "cups": ("CUPS", [
        ("h", "The goal"),
        ("p", "A ball is hidden under one of the cups. Watch closely while the cups are shuffled, then pick the "
              "cup you think the ball is under."),
        ("h", "How to play"),
        ("b", "Pick a difficulty at the top (or use the LEFT / RIGHT arrows)."),
        ("b", "Click chips to set your bet, then press START (or Space)."),
        ("b", "The ball is shown under its cup, then the cups start swapping places - faster and faster."),
        ("b", "When they stop, click a cup (or press the number under it - 1 to 5)."),
        ("h", "Difficulties"),
        ("b", "EASY - 3 cups, 12 moves. Pays x1.3."),
        ("b", "MEDIUM - 3 cups, 18 fast moves. Pays x2."),
        ("b", "HARD - 4 cups, 26 lightning-fast moves. Pays x3.5."),
        ("b", "EXTREME - 5 cups and 100 moves. It starts slow and easy... but it never stops speeding up, and "
              "by the end the cups are flying all over the table. Pays x20!"),
        ("h", "Watch out for tricks"),
        ("b", "FAKE-OUTS - two cups start to swap, meet in the middle... and go back where they were."),
        ("b", "SPINS - all three cups move at once, each one sliding to a different spot."),
        ("b", "DOUBLE SWAPS (hard) - two pairs of cups swap at the same time."),
        ("x", "Bet $100 on HARD and find the ball  ->  you get $350 back."),
        ("p", "Tip: pick one cup with your eyes and never look away from it! Find the ball on HARD 3 times in a "
              "row for the Eagle Eye trophy."),
    ]),
    "crossy": ("CHICKEN CROSSING", [
        ("h", "The goal"),
        ("p", "Help the chicken hop across an endless road. Every new road (or train track) you reach raises your "
              "multiplier. Cash out whenever you like - but get hit and you lose your bet."),
        ("h", "Controls"),
        ("b", "UP arrow, W or SPACE - hop forward.  DOWN or S - hop back."),
        ("b", "LEFT / RIGHT arrows or A / D - hop sideways. Trees block your way."),
        ("b", "ENTER, C or the CASH OUT button - take your winnings."),
        ("h", "Cashing out"),
        ("p", "You can cash out at any time EXCEPT when a car or train is about to hit you - the button says "
              "TOO CLOSE! until there's a safe gap. On the grass you're always safe to cash out."),
        ("x", "Bet $100, reach 20 roads at about x2.50  ->  cash out about $250."),
        ("h", "It gets harder"),
        ("b", "The further you go, the faster the cars, the closer together they are, and the more lanes in a row."),
        ("b", "Train tracks: when the red light flashes, a train is coming. Get off the tracks!"),
        ("b", "The first few roads barely raise your multiplier - the later, harder roads raise it a LOT."),
        ("b", "Don't wait around - the screen keeps moving up. Fall off the bottom and the eagle grabs you!"),
    ]),
    "yesno": ("YES OR NO", [
        ("h", "How it works"),
        ("p", "Every 30 seconds there's a new yes-or-no question - about the stock market, dice, cards, coins, "
              "rockets, sports and more. Bet on YES or NO before the timer runs out."),
        ("b", "Click chips to set your bet, then press YES (Y key) or NO (N key)."),
        ("b", "Betting closes 5 seconds before the answer is revealed."),
        ("b", "You can only bet one side of each question, but you can add more to that side."),
        ("h", "Payouts"),
        ("p", "Unlikely answers pay more. Each side shows what it pays, like x1.90 or x12.35."),
        ("x", "\"Will the next card be an ace?\"  YES pays x12.35 (it's rare)  -  NO pays x1.02."),
        ("p", "Stock questions have LIVE odds - they change as the price moves, and your payout is locked in "
              "when you place the bet."),
        ("h", "It runs in the background"),
        ("p", "Questions keep coming even while you play other games. If you have a bet riding, a notification "
              "pops up when it's answered. Win 5 in a row for The Oracle trophy!"),
    ]),
    "stocks": ("STOCKS", [
        ("h", "What is a stock?"),
        ("p", "A share of stock is a tiny piece of a company. The 20 companies here are made up, and all the "
              "money is play money, but it works the same way as the real thing."),
        ("p", "A share's price changes all the time. It tends to go up when the company does well and down when "
              "it does badly - but a lot of the moves are just random ups and downs."),
        ("h", "How you make money"),
        ("p", "Buy shares while the price is low and sell them later when the price is higher. "
              "Your profit is the difference, times how many shares you own."),
        ("x", "Buy 10 shares at $40 = $400.  Price rises to $50  ->  sell for $500 = $100 profit."),
        ("x", "Buy 10 shares at $40 = $400.  Price falls to $30  ->  sell for $300 = $100 loss."),
        ("p", "Nothing happens to your money until you sell. Until then it's \"unrealized\" - "
              "just what it WOULD be worth if you sold right now."),
        ("h", "Reading the screen"),
        ("b", "Left side - all 20 companies (scroll with the mouse wheel or the up / down keys). Each shows its "
              "price and how much it moved in the last hour. Click one to look at it."),
        ("b", "Chart - the price over time. The 1M / 5M / 30M / 1H buttons change how far back it goes. "
              "Hover over the chart to see past prices."),
        ("b", "Dashed gold line - the average price you paid. Above it you're making money, below it you're losing."),
        ("b", "BID / ASK - you buy at the ASK price and sell at the BID price, which is a little lower. "
              "So buying and selling straight away loses a few cents a share. Don't trade every second!"),
        ("h", "Buying and selling"),
        ("b", "Choose how many shares with the -10 / -1 / +1 / +10 / +100 buttons, or just type a number. "
              "MAX picks the most you can afford."),
        ("b", "The bar shows exactly what it will cost to BUY, or what you'd get to SELL, before you press anything."),
        ("b", "BUY spends your cash. SELL turns shares back into cash. SELL ALL sells every share of that company."),
        ("h", "Your position"),
        ("b", "Shares - how many you own.  Avg cost - the average price you paid per share."),
        ("b", "Cost basis - the total you paid.  Market value - what they're worth right now."),
        ("b", "The big green or red number is your profit or loss if you sold everything now."),
        ("b", "MY TRADES (next to Market News) lists everything you've bought and sold. \"Realized\" is profit "
              "you've actually locked in by selling."),
        ("h", "News"),
        ("p", "Headlines pop up now and then (good news in green, bad in red). They can make a price jump or drop "
              "suddenly - keep an eye on them."),
        ("p", "Every so often a BIG event happens - a market crash, a boom, a rocket exploding, cheese found on the "
              "Moon... These can move a stock 15% to 90% and pop up as a notification wherever you are in the game. "
              "They show in gold in the news box."),
        ("p", "Very rarely there's a MEGA EVENT - a company getting bought for a fortune (3x to 6x its price!), "
              "going nearly bankrupt (down 60-85%), or the whole market crashing or booming."),
        ("h", "5-minute bets"),
        ("p", "Open the 5-MIN BETS tab (next to MY SHARES). Choose an amount, then bet whether the stock you're "
              "looking at will be HIGHER or LOWER than it is right now, 5 minutes from now."),
        ("b", "Right pays x1.9 your bet. If the price ends exactly where it started, you get your bet back."),
        ("b", "You can have up to 10 bets running. Each one shows a countdown and whether it's winning right now, "
              "and a blue line on the chart marks its starting price."),
        ("b", "Bets finish even if you leave the stocks screen or close the game - a notification tells you how "
              "you did."),
        ("h", "It never stops"),
        ("p", "The market keeps moving while you play the other games, and it even catches up (up to 6 hours) for "
              "time the game was closed. Once you own shares, the box at the top of every screen shows what they're "
              "worth - green if you're up overall, red if you're down."),
        ("h", "Good to know"),
        ("b", "Prices can fall as easily as they rise. Nothing is guaranteed."),
        ("b", "Spreading your money over a few companies is safer than putting it all in one."),
        ("b", "Money in stocks isn't cash - sell some shares to get chips for the other games."),
        ("b", "Free chips from the lobby are only for when your cash AND your stocks together are nearly zero."),
    ]),
}


def wrap_text(text, f, width):
    lines, cur = [], ""
    for word in text.split(" "):
        test = f"{cur} {word}".strip()
        if f.size(test)[0] <= width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


class HelpOverlay:
    PANEL = pygame.Rect(150, 66, 980, 610)
    VIEW = pygame.Rect(180, 152, 910, 440)

    def __init__(self):
        self.scene = None
        self.scroll = 0.0
        self.target = 0.0
        self.pages = {}
        self.close_btn = Button((W / 2 - 110, 612, 220, 50), "GOT IT", (25, 120, 60), 22, "ESC / SPACE")
        self.x_rect = pygame.Rect(self.PANEL.right - 52, self.PANEL.y + 14, 38, 38)

    @property
    def active(self):
        return self.scene is not None

    def open(self, scene):
        if scene in HELP:
            self.scene, self.scroll, self.target = scene, 0.0, 0.0

    def close(self):
        self.scene = None

    def page(self, scene):
        """Render the whole guide once onto a tall surface."""
        if scene in self.pages:
            return self.pages[scene]
        w = self.VIEW.w - 24
        fh, fp, fx = font(22, bold=True, serif=True), font(17), font(16, bold=True)
        items, y = [], 0
        for kind, text in HELP[scene][1]:
            if kind == "h":
                y += 14 if items else 0
                items.append((fh.render(text, True, GOLD), 0, y))
                y += 34
            elif kind == "p":
                for ln in wrap_text(text, fp, w):
                    items.append((fp.render(ln, True, (225, 225, 220)), 0, y))
                    y += 24
                y += 6
            elif kind == "b":
                items.append(("dot", 8, y + 11))
                for ln in wrap_text(text, fp, w - 26):
                    items.append((fp.render(ln, True, (225, 225, 220)), 24, y))
                    y += 24
                y += 4
            elif kind == "x":
                lines = wrap_text(text, fx, w - 40)
                h = len(lines) * 22 + 14
                items.append(("box", h, y))
                for k, ln in enumerate(lines):
                    items.append((fx.render(ln, True, (150, 235, 170)), 20, y + 7 + k * 22))
                y += h + 8
        surf = pygame.Surface((self.VIEW.w, y + 10), pygame.SRCALPHA)
        for img, x, yy in items:
            if img == "dot":
                pygame.draw.circle(surf, GOLD, (x, yy), 4)
            elif img == "box":
                pygame.draw.rect(surf, (20, 60, 35, 200), (4, yy, self.VIEW.w - 32, x), border_radius=8)
                pygame.draw.rect(surf, (60, 140, 80), (4, yy, self.VIEW.w - 32, x), width=1, border_radius=8)
            else:
                surf.blit(img, (x, yy))
        self.pages[scene] = surf
        return surf

    def max_scroll(self):
        return max(0, self.page(self.scene).get_height() - self.VIEW.h)

    def handle(self, e):
        if e.type == pygame.MOUSEWHEEL:
            self.target = max(0, min(self.max_scroll(), self.target - e.y * 60))
        elif e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_ESCAPE, pygame.K_SPACE, pygame.K_RETURN):
                self.close()
            elif e.key in (pygame.K_DOWN, pygame.K_s, pygame.K_PAGEDOWN):
                self.target = min(self.max_scroll(), self.target + (400 if e.key == pygame.K_PAGEDOWN else 60))
            elif e.key in (pygame.K_UP, pygame.K_w, pygame.K_PAGEUP):
                self.target = max(0, self.target - (400 if e.key == pygame.K_PAGEUP else 60))
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.close_btn.clicked(e.pos) or self.x_rect.collidepoint(e.pos) or \
                    not self.PANEL.collidepoint(e.pos):
                self.close()

    def update(self, dt):
        self.scroll += (self.target - self.scroll) * min(1, dt * 14)

    def draw(self, surf):
        dim = pygame.Surface((W, H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 170))
        surf.blit(dim, (0, 0))
        r = self.PANEL
        pygame.draw.rect(surf, (4, 3, 6), r.move(6, 8), border_radius=20)
        pygame.draw.rect(surf, (22, 18, 28), r, border_radius=20)
        pygame.draw.rect(surf, GOLD, r, width=3, border_radius=20)
        draw_text(surf, "HOW TO PLAY", font(14, bold=True), (200, 180, 140), (r.x + 30, r.y + 26), anchor="midleft")
        draw_text(surf, HELP[self.scene][0], font(30, bold=True, serif=True), GOLD, (r.x + 30, r.y + 50),
                  anchor="midleft")
        hover_x = self.x_rect.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(surf, (120, 35, 40) if hover_x else (70, 30, 35), self.x_rect, border_radius=10)
        draw_text(surf, "X", font(20, bold=True), WHITE, self.x_rect.center)
        pygame.draw.line(surf, GOLD_DARK, (r.x + 24, r.y + 76), (r.right - 24, r.y + 76), 1)
        page = self.page(self.scene)
        surf.blit(page, self.VIEW.topleft, area=pygame.Rect(0, int(self.scroll), self.VIEW.w, self.VIEW.h))
        ms = self.max_scroll()
        if ms > 0:
            track = pygame.Rect(self.VIEW.right + 6, self.VIEW.y, 6, self.VIEW.h)
            pygame.draw.rect(surf, (50, 45, 60), track, border_radius=3)
            th = max(40, track.h * self.VIEW.h / page.get_height())
            ty = track.y + (track.h - th) * (self.scroll / ms)
            pygame.draw.rect(surf, GOLD, (track.x, ty, track.w, th), border_radius=3)
            if self.scroll < ms - 4:
                fade = pygame.Surface((self.VIEW.w, 40), pygame.SRCALPHA)
                for i in range(40):
                    pygame.draw.line(fade, (22, 18, 28, int(6 * i)), (0, i), (self.VIEW.w, i))
                surf.blit(fade, (self.VIEW.x, self.VIEW.bottom - 40))
                draw_text(surf, "scroll for more", font(12, bold=True), (180, 170, 140),
                          (self.VIEW.centerx, self.VIEW.bottom - 8))
        self.close_btn.draw(surf, pygame.mouse.get_pos())


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
class App:
    def __init__(self):
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        if not WEB:
            pygame.mixer.pre_init(22050, -16, 1, 256)
        pygame.init()
        pygame.display.set_caption("Grand Royale Casino  (play money)")
        self.screen = pygame.display.set_mode((W, H)) if WEB else \
            pygame.display.set_mode((W, H), pygame.SCALED | pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.saved = {}
        self.net = None             # our connection to a multiplayer game
        self.net_server = None      # the server, if we're the host
        self.balance = self.load()
        self.shown_balance = float(self.balance)
        self.floaters = []
        self.sounds = make_sounds() if pygame.mixer.get_init() else {}

        # loading screen while art is generated
        self.screen.fill((10, 8, 12))
        draw_text(self.screen, "Shuffling the decks...", font(28, serif=True), GOLD, (W / 2, H / 2))
        pygame.display.flip()

        self.market = Market(self.saved.get("market"))
        if not hasattr(self, "auto_updater"):   # (a DELETE ALL DATA restart keeps the one that's running)
            self.auto_updater = updater.AutoUpdater() if updater else None
            if self.auto_updater:
                self.auto_updater.start()
        self.autosave_t = 30.0
        self.assets = Assets()
        self.effects = Effects(self)
        self.stats = {k: dict(v) for k, v in self.saved.get("stats", {}).items()}
        self.achievements = dict(self.saved.get("achievements", {}))
        self.played = set(self.saved.get("played", []))
        self.daily = dict(self.saved.get("daily", {}))
        self.worth_check = 0.0
        self.lottery = Lottery(self, self.saved.get("lottery"))
        self.player_name = clean_name(self.saved.get("name", "Player"))
        self.sound_on = bool(self.saved.get("sound", True))
        self.host_ip = ""
        self.show_others = True
        self.chat_open = False
        self.chat_text = ""
        self.chat_btn = Button((320, 9, 110, 38), "CHAT", (40, 90, 160), 16, None)
        self.all_in_armed = False
        self.yesno = YesNo(self)
        self.menu = Menu(self)
        self.blackjack = Blackjack(self)
        self.roulette = Roulette(self)
        self.slots = Slots(self)
        self.rocket = Rocket(self)
        self.poker = Poker(self)
        self.stocks = Stocks(self)
        self.horses = HorseRace(self)
        self.wheel = MultWheel(self)
        self.bus = RideTheBus(self)
        self.craps = Craps(self)
        self.baccarat = Baccarat(self)
        self.scenes = {"blackjack": self.blackjack, "roulette": self.roulette, "slots": self.slots,
                       "rocket": self.rocket, "poker": self.poker, "stocks": self.stocks, "horses": self.horses,
                       "wheel": self.wheel, "bus": self.bus, "craps": self.craps, "baccarat": self.baccarat,
                       "plinko": Plinko(self), "mines": Mines(self), "videopoker": VideoPoker(self),
                       "scratch": ScratchCards(self), "keno": Keno(self), "sicbo": SicBo(self),
                       "dragontiger": DragonTiger(self), "lottery": self.lottery, "deepdive": DeepDive(self),
                       "counting": CountingSchool(self), "pinball": Pinball(self), "work": Work(self),
                       "crossy": Crossy(self), "yesno": self.yesno, "cups": Cups(self),
                       "netpoker": NetPoker(self), "netbj": NetBlackjack(self), "online": Online(self),
                       "settings": Settings(self), "title": Title(self),
                       "pusher": Pusher(self, self.saved.get("pusher")), "coinflip": CoinFlip(self),
                       "account": AccountScreen(self), "mp_crash": CrashParty(self),
                       "mp_highcard": HighCardShowdown(self), "mp_liars": LiarsDice(self), "mp_bingo": BingoNight(self)}
        self.games = list(self.scenes.values())
        self.profile = ProfileOverlay(self)
        self.scene = "title"            # the game opens on the main menu
        self.lobby_btn = Button((14, 9, 120, 38), "< LOBBY", (60, 45, 30), 18)
        self.help_btn = Button((142, 9, 170, 38), "? HOW TO PLAY", (40, 60, 110), 16)
        self.help = HelpOverlay()
        self.help_seen = set(self.saved.get("help_seen", []))
        self.last_scene = "menu"
        if not hasattr(self, "session"):            # first start (not a profile switch): accounts
            self.session = None           # the logged-in account, or None for a guest
            self.guest = False            # chose "play as guest" this time
            self.remember = False
            self.account_flow = None      # (what, Flow) while logging in / signing up / deleting
            self.account_error = ""
            self.cloud_flow = None        # an upload of your progress that's under way
            self.cloud_data = None
            self.cloud_dirty = False
            self.cloud_wait = 0.0
            self.cloud_state = ""
            self.cloud_retry_data = None
            self.events = LiveEvents(self)
            self.themefx = ThemeFX(self)
            self.owner_menu = OwnerMenu(self)
            self.remembered = remember_load()
            if self.remembered:           # "keep me logged in": log back in while the title plays
                self.start_account_flow("resume", flow_resume(self.remembered))
        if self.session:
            self.player_name = self.session.name
        self.theme_mine = str(self.saved.get("theme_mine", ""))    # the owner's "just me" theme

    # ---- persistence -----------------------------------------------------
    @staticmethod
    def web_storage():
        import platform                 # pygbag's platform module gives access to the page's JavaScript
        return platform.window.localStorage

    def load(self):
        if getattr(self, "profile_data", None) is not None:      # switching to an account's progress
            self.saved, self.profile_data = self.profile_data, None
            try:
                return max(0, int(self.saved.get("balance", START_BALANCE)))
            except (TypeError, ValueError):
                return START_BALANCE
        try:
            if WEB:
                text = self.web_storage().getItem(WEB_SAVE_KEY)
                self.saved = json.loads(str(text)) if text else {}
            else:
                with open(SAVE_FILE) as f:
                    self.saved = json.load(f)
            return max(0, int(self.saved["balance"]))
        except Exception:
            return START_BALANCE

    def read_local_save(self):
        """The guest save on this device."""
        try:
            if WEB:
                text = self.web_storage().getItem(WEB_SAVE_KEY)
                return json.loads(str(text)) if text else {}
            with open(SAVE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self):
        try:
            data = self.save_data()
            if getattr(self, "session", None):         # logged in: it goes online (see cloud_tick)
                self.cloud_data = self.save_data(cloud=True)
                self.cloud_dirty = True
                return
            self.write_save(data)
        except Exception:
            pass

    def save_data(self, cloud=False):
        data = self.save_dict()
        if cloud:                                      # keep online saves small: less price history
            for st in data.get("market", {}).get("stocks", {}).values():
                st["hist"] = st["hist"][-300:]
        return data

    def save_dict(self):
        return ({"balance": self.balance, "market": self.market.to_state(),
                           "help_seen": sorted(getattr(self, "help_seen", [])),
                           "stats": getattr(self, "stats", {}), "achievements": getattr(self, "achievements", {}),
                           "played": sorted(getattr(self, "played", [])), "daily": getattr(self, "daily", {}),
                           "lottery": self.lottery.to_state() if hasattr(self, "lottery") else {},
                           "name": getattr(self, "player_name", "Player"),
                           "sound": getattr(self, "sound_on", True),
                           "theme_mine": getattr(self, "theme_mine", ""),
                           "pusher": self.scenes["pusher"].to_state() if hasattr(self, "scenes") else
                           self.saved.get("pusher", {})})

    def write_save(self, data):
        if WEB:
            self.web_storage().setItem(WEB_SAVE_KEY, json.dumps(data, separators=(",", ":")))
            return
        tmp = SAVE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, SAVE_FILE)

    # ---- shared UI -------------------------------------------------------
    def sfx(self, name):
        snd = self.sounds.get(name) if getattr(self, "sound_on", True) else None
        if snd:
            snd.play()

    # ---- accounts ----------------------------------------------------------------------
    def start_account_flow(self, what, gen):
        self.account_error = ""
        self.account_flow = (what, Flow(gen))

    def account_tick(self, dt):
        if self.account_flow:
            what, flow = self.account_flow
            if flow.poll():
                self.account_flow = None
                if flow.error:
                    if what == "resume":
                        if "LOG IN AGAIN" in flow.error:
                            remember_clear()
                        self.account_error = "COULDN'T LOG YOU BACK IN: " + flow.error
                    else:
                        self.account_error = flow.error
                elif what == "delete":
                    self.effects.toast("ACCOUNT DELETED", "Your account and its progress are gone")
                    self.log_out(upload=False)
                else:
                    session, progress = flow.result
                    self.enter_account(session, progress, what)
        # uploading progress
        if self.cloud_flow:
            if self.cloud_flow.poll():
                if self.cloud_flow.error:                  # try again soon (newer progress wins if there is some)
                    self.cloud_state = "offline"
                    if self.cloud_data is None:
                        self.cloud_data = self.cloud_retry_data
                    self.cloud_dirty = True
                    self.cloud_wait = 10.0
                else:
                    self.cloud_state = "saved"
                    res = self.cloud_flow.result
                    if isinstance(res, tuple) and res[0] == "edited" and self.session:
                        self.balance = res[1]
                        self.effects.toast("YOUR CHIPS WERE CHANGED", f"The owner set your balance to {money(res[1])}")
                        self.sfx("win")
                if self.session and self.remember and self.session.refresh != self.remembered:
                    self.remembered = self.session.refresh
                    remember_save(self.remembered)
                self.cloud_flow = None
        elif self.session and self.cloud_dirty and self.cloud_wait <= 0:
            self.cloud_retry_data = self.cloud_data
            self.cloud_flow = Flow(flow_save(self.session, self.cloud_data))
            self.cloud_data = None
            self.cloud_dirty = False
            self.cloud_state = "saving"
            self.cloud_wait = CLOUD_SAVE_EVERY
        self.cloud_wait -= dt

    def enter_account(self, session, progress, how):
        was_guest = not self.session
        self.switch_profile(progress, session)
        if self.remember or how == "resume":
            self.remembered = session.refresh
            remember_save(session.refresh)
        if self.scene == "account":
            self.scene = "menu"
            self.last_scene = "menu"
        word = {"signup": "ACCOUNT CREATED", "resume": "WELCOME BACK"}.get(how, "LOGGED IN")
        self.effects.toast(word, f"Playing as {session.name} - your progress saves online")
        if how == "signup" and was_guest:
            self.save()                                # put the chosen starting progress online right away
            self.cloud_wait = 0

    def switch_profile(self, saved, session):
        """Load a different player's progress (an account, or the guest save) without restarting the game."""
        if self.net:
            self.leave_game()
        title, scene = self.scenes.get("title"), self.scene
        self.session = session
        self.profile_data = saved or {}
        self.__init__()
        if title:
            self.scenes["title"] = title
        self.scene = self.last_scene = scene if scene in ("title", "menu", "account", "settings") else "menu"
        if session:
            self.player_name = session.name

    def log_out(self, upload=True):
        if self.session and upload:
            self.save()
            data = self.cloud_data
            if data:
                flow = Flow(flow_save(self.session, data))
                if not WEB:
                    flow.wait(8)                       # make sure the last save gets up there
                else:
                    self.cloud_flow = flow             # it finishes in the background
        remember_clear()
        self.remembered = ""
        self.remember = False
        self.guest = True
        self.cloud_data, self.cloud_dirty, self.cloud_state = None, False, ""
        self.switch_profile(self.read_local_save(), None)
        self.effects.toast("LOGGED OUT", "Playing as a guest on this device")

    def reset_everything(self):
        """DELETE ALL DATA: wipe the save and start over exactly like a brand-new game."""
        if getattr(self, "session", None):             # logged in: reset the account's progress online
            self.switch_profile({}, self.session)
            self.save()
            self.cloud_wait = 0
            self.effects.toast("GAME RESET", f"All progress deleted - you're starting over with {money(START_BALANCE)}")
            return
        if self.net:
            self.net.close()
        if self.net_server:
            self.net_server.close()
        self.net = self.net_server = None
        if WEB:
            try:
                self.web_storage().removeItem(WEB_SAVE_KEY)
            except Exception:
                pass
        for f in (SAVE_FILE, SAVE_FILE + ".tmp"):
            try:
                os.remove(f)
            except OSError:
                pass
        self.__init__()
        self.save()
        self.effects.toast("GAME RESET", f"All data deleted - you're starting over with {money(START_BALANCE)}")

    def net_worth(self):
        return self.balance + self.market.total_value() + sum(b["amount"] for b in self.market.bets)

    # ---- stats, achievements, daily bonus --------------------------------
    def record(self, game, stake, returned):
        ev = getattr(self, "events", None)
        if ev and ev.active("double") and returned > stake and game not in ("stocks", "work"):
            self.balance += returned                   # DOUBLE PAYOUTS: the win is paid a second time
            self.float_text(f"+{money(returned)} DOUBLE!", (255, 220, 90))
            returned *= 2
        st = self.stats.setdefault(game, {"rounds": 0, "wins": 0, "wagered": 0, "returned": 0, "best": 0})
        st["rounds"] += 1
        st["wagered"] += stake
        st["returned"] += returned
        if returned > stake:
            st["wins"] += 1
            st["best"] = max(st["best"], returned - stake)
            self.unlock("first_win")
        self.played.add(game)
        if all(k in self.played for k in GAME_INFO if k not in NON_GAMES and k not in MP_ONLY):
            self.unlock("tourist")
        if self.net:
            self.net.send({"t": "result", "game": game, "stake": int(stake), "returned": int(returned)})

    def unlock(self, aid):
        if aid in self.achievements or aid not in ACH_BY_ID:
            return
        _, name, desc, reward = ACH_BY_ID[aid]
        self.achievements[aid] = time.strftime("%Y-%m-%d")
        self.balance += reward
        self.effects.toast(f"TROPHY UNLOCKED: {name.upper()}", f"{desc}  -  bonus +{money(reward)}")
        self.effects.burst(W / 2, 110, 36)
        self.sfx("win")
        self.save()

    @staticmethod
    def day(offset=0):
        return time.strftime("%Y-%m-%d", time.localtime(time.time() + offset * 86400))

    def daily_ready(self):
        return self.daily.get("last") != self.day()

    def next_streak(self):
        if not self.daily_ready():
            return self.daily.get("streak", 1)
        return self.daily.get("streak", 0) + 1 if self.daily.get("last") == self.day(-1) else 1

    def claim_daily(self):
        if not self.daily_ready():
            return
        streak = self.next_streak()
        amt = daily_amount(streak)
        self.daily = {"last": self.day(), "streak": streak}
        self.balance += amt
        self.float_text(f"+{money(amt)}", (80, 230, 110))
        self.effects.chip_rain(35)
        self.effects.toast(f"DAILY BONUS - DAY {streak}", f"+{money(amt)}!  Come back tomorrow to keep the streak going.")
        self.sfx("win")
        if streak >= 7:
            self.unlock("regular")
        self.save()

    def broke_message(self):
        if self.market.total_value() >= min(CHIP_VALUES):
            return "OUT OF CHIPS!  SELL SOME STOCKS TO RAISE CASH"
        return "OUT OF CHIPS!  HEAD TO THE LOBBY AND GO TO WORK"

    def float_text(self, text, color):
        self.floaters.append({"text": text, "color": color, "y": 76.0, "life": 1.8})
        if text.startswith("+"):
            self.effects.win()

    def draw_top_bar(self, surf, title, lobby, lobby_enabled=True, show_help=True):
        for y in range(56):
            pygame.draw.line(surf, lerp_col((32, 26, 30), (12, 10, 14), y / 56), (0, y), (W, y))
        pygame.draw.line(surf, GOLD_DARK, (0, 56), (W, 56), 2)
        if lobby:
            self.lobby_btn.draw(surf, pygame.mouse.get_pos(), lobby_enabled)
            if show_help:
                self.help_btn.draw(surf, pygame.mouse.get_pos())
        else:
            draw_suit(surf, "S", 32, 28, 22, GOLD)
            draw_text(surf, "GRAND ROYALE", font(20, bold=True, serif=True), GOLD, (52, 28), anchor="midleft")
        if title:
            draw_text(surf, title, font(26, bold=True, serif=True), GOLD, (W / 2, 28), shadow=(0, 0, 0))

        panel = pygame.Rect(W - 262, 8, 248, 40)
        pygame.draw.rect(surf, (8, 8, 10), panel, border_radius=20)
        pygame.draw.rect(surf, GOLD_DARK, panel, width=2, border_radius=20)
        chip = self.assets.chip(1000, 14)
        surf.blit(chip, chip.get_rect(center=(panel.x + 22, panel.centery)))
        draw_text(surf, money(int(round(self.shown_balance))), font(24, bold=True), WHITE,
                  (panel.right - 18, panel.centery), anchor="midright")

        # stock portfolio, always ticking in the background
        if self.market.holdings:
            tv, tc = self.market.total_value(), self.market.total_cost()
            up = tv >= tc
            col = UP_COL if up else DOWN_COL
            sp = pygame.Rect(panel.x - 222, 8, 212, 40)
            pygame.draw.rect(surf, (8, 8, 10), sp, border_radius=20)
            pygame.draw.rect(surf, col, sp, width=2, border_radius=20)
            draw_text(surf, "STOCKS", font(12, bold=True), (170, 170, 180), (sp.x + 16, sp.centery), anchor="midleft")
            draw_arrow(surf, up, (sp.x + 76, sp.centery), 12, col)
            draw_text(surf, money(int(round(tv))), font(20, bold=True), col, (sp.right - 16, sp.centery),
                      anchor="midright")

    # ---- multiplayer -------------------------------------------------------
    def go(self, key):
        """Open a game from the lobby. When you're online, poker and blackjack use the shared tables."""
        if self.net and key in ("poker", "blackjack"):
            key = "netpoker" if key == "poker" else "netbj"
        if key in MP_ONLY and not self.net:
            if WEB:
                self.effects.toast("PARTY GAMES NEED MULTIPLAYER", "Multiplayer is only in the downloaded game")
                return
            self.effects.toast("PARTY GAMES NEED MULTIPLAYER", "Host a game or join a friend's, then pick a party game")
            key = "online"
        self.scene = key

    def host_game(self):
        if self.net:
            return ""
        try:
            self.net_server = NetServer(self.player_name)
        except OSError:
            self.net_server = None
            return "COULDN'T START HOSTING - IS THIS COMPUTER ALREADY HOSTING A GAME?"
        self.host_ip = local_ip()
        err = self.join_game("127.0.0.1")
        if err:
            self.net_server.close()
            self.net_server = None
        return err

    def join_game(self, addr):
        if self.net:
            return ""
        try:
            self.net = NetClient(self, addr, self.player_name)
        except OSError:
            return f"COULDN'T CONNECT TO {addr} - CHECK THE ADDRESS AND THAT YOU'RE ON THE SAME WI-FI"
        self.save()
        return ""

    def leave_game(self, why=None):
        for key in ("netpoker", "netbj", *MP_KINDS.values()):
            self.scenes[key].lost()          # take back any chips still on the shared tables
        if self.net:
            self.net.close()
            self.net = None
        if self.net_server:
            self.net_server.close()
            self.net_server = None
        if self.scene in ("netpoker", "netbj", *MP_KINDS.values()):
            self.scene = "menu"
        self.chat_open = False
        self.chat_text = ""
        if why:
            self.effects.toast("DISCONNECTED", why)
        self.save()

    def live_bet(self):
        sc = self.current()
        try:
            b = sc.outstanding_bets() or getattr(sc, "total_bet", 0) or getattr(sc, "bet", 0)
            return int(b) if isinstance(b, (int, float)) else 0
        except Exception:
            return 0

    # ---- chat ----------------------------------------------------------------
    CHAT_BOX = pygame.Rect(W - 430, 62, 420, 330)

    def chat_button_rect(self):
        return pygame.Rect(620, 9, 110, 38) if self.scene == "menu" else pygame.Rect(320, 9, 110, 38)

    def typing_elsewhere(self):
        sc = self.current()
        return bool(getattr(sc, "focus", None)) or bool(getattr(sc, "editing", False))

    def chat_handle(self, e):
        """Returns True if the chat box used this event."""
        if not self.net:
            self.chat_open = False
            return False
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            if self.chat_button_rect().collidepoint(e.pos):
                self.chat_open = not self.chat_open
                self.net.unread = 0
                return True
            if self.chat_open:
                if self.CHAT_BOX.collidepoint(e.pos):
                    return True
                self.chat_open = False          # clicking the game closes the chat
            return False
        if e.type != pygame.KEYDOWN:
            return False
        if not self.chat_open:
            if e.key == pygame.K_SLASH and not self.typing_elsewhere():
                self.chat_open = True
                self.net.unread = 0
                return True
            return False
        if e.key == pygame.K_ESCAPE:
            self.chat_open = False
        elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            text = self.chat_text.strip()
            if text:
                self.net.send({"t": "chat", "text": text[:CHAT_MAX]})
                self.chat_text = ""
            else:
                self.chat_open = False
        elif e.key == pygame.K_BACKSPACE:
            self.chat_text = self.chat_text[:-1]
        elif e.unicode and e.unicode.isprintable() and len(self.chat_text) < CHAT_MAX:
            self.chat_text += e.unicode
        return True

    def draw_chat(self, surf):
        if not self.net:
            return
        mouse = pygame.mouse.get_pos()
        b = self.chat_btn
        b.rect = self.chat_button_rect()
        b.text = "CLOSE CHAT" if self.chat_open else "CHAT"
        b.size = 14 if self.chat_open else 16
        b.color = (25, 120, 60) if self.chat_open else (40, 90, 160)
        b.draw(surf, mouse)
        if self.net.unread and not self.chat_open:
            c = (b.rect.right - 4, b.rect.y + 4)
            pygame.draw.circle(surf, (220, 40, 40), c, 11)
            draw_text(surf, str(min(self.net.unread, 9)), font(13, bold=True), WHITE, c)
        now = time.time()
        f = font(14)
        if self.chat_open:
            box = self.CHAT_BOX
            p = pygame.Surface(box.size, pygame.SRCALPHA)
            pygame.draw.rect(p, (6, 8, 18, 235), p.get_rect(), border_radius=14)
            surf.blit(p, box)
            pygame.draw.rect(surf, (90, 130, 220), box, width=2, border_radius=14)
            draw_text(surf, "CHAT", font(15, bold=True), GOLD, (box.x + 16, box.y + 18), anchor="midleft")
            draw_text(surf, "Enter = send   Esc = close", font(12), (150, 160, 190), (box.right - 16, box.y + 18),
                      anchor="midright")
            lines = []
            for name, text, t, kind in self.net.chat:
                if kind == "info":
                    lines.append((f"- {text} -", (150, 160, 190), None))
                    continue
                col = GOLD if kind == "me" else (140, 200, 255)
                first = True
                for ln in wrap_text(f"{name}: {text}", f, box.w - 36):
                    lines.append((ln, WHITE, (name, col) if first else None))
                    first = False
            lines = lines[-11:]
            for i, (ln, col, tag) in enumerate(lines):
                y = box.y + 44 + i * 22
                x = box.x + 16
                if tag:                                   # the name in colour, then the message
                    head = tag[0] + ":"
                    x += draw_text(surf, head, font(14, bold=True), tag[1], (x, y), anchor="midleft").w
                    ln = ln[len(head):]
                draw_text(surf, ln, f, col, (x, y), anchor="midleft")
            if not self.net.chat:
                draw_text(surf, "Say hi to everyone in the game!", f, (150, 160, 190), (box.centerx, box.y + 120))
            inp = pygame.Rect(box.x + 12, box.bottom - 48, box.w - 24, 36)
            pygame.draw.rect(surf, (16, 20, 36), inp, border_radius=8)
            pygame.draw.rect(surf, GOLD, inp, width=2, border_radius=8)
            shown = self.chat_text + ("|" if int(now * 2) % 2 == 0 else "")
            while f.size(shown)[0] > inp.w - 20 and len(shown) > 1:
                shown = shown[1:]
            if self.chat_text or int(now * 2) % 2 == 0:
                draw_text(surf, shown, f, WHITE, (inp.x + 10, inp.centery), anchor="midleft")
            if not self.chat_text:
                draw_text(surf, "type a message...", f, (110, 115, 140), (inp.x + 18, inp.centery), anchor="midleft")
            return
        # closed: new messages pop up for a few seconds
        recent = [m for m in self.net.chat if now - m[2] < 8 and m[3] != "me"][-3:]
        for i, (name, text, t, kind) in enumerate(recent):
            msg = text if kind == "info" else f"{name}: {text}"
            while f.size(msg)[0] > 380 and len(msg) > 4:
                msg = msg[:-4] + "..."
            alpha = int(230 * min(1.0, (8 - (now - t)) / 1.5))
            w = f.size(msg)[0] + 24
            p = pygame.Surface((w, 26), pygame.SRCALPHA)
            pygame.draw.rect(p, (6, 10, 30, alpha), p.get_rect(), border_radius=13)
            surf.blit(p, (W - 14 - w, 64 + i * 30))
            draw_text(surf, msg, f, (150, 160, 190) if kind == "info" else (220, 230, 255),
                      (W - 14 - w + 12, 77 + i * 30), anchor="midleft")

    def draw_version(self, surf):
        """Bottom-left of the lobby: the version, and what the auto-updater is doing."""
        up = self.auto_updater
        col = (150, 140, 130)
        text = f"v{VERSION}"
        if up and up.update:
            new = up.update.version
            if up.state == "downloading":
                text, col = f"Downloading update v{new}...  {int(up.progress * 100)}%", (140, 200, 255)
            elif up.state == "ready" and self.net:
                text, col = f"Update v{new} ready - installs when you leave multiplayer", (140, 230, 150)
            elif up.state in ("ready", "installing"):
                text, col = f"Installing update v{new}...", (140, 230, 150)
            elif up.state == "failed":
                text = f"v{VERSION}  (update v{new} failed - it'll try again next time)"
        if self.session:
            state = {"saving": "saving...", "offline": "offline - will retry", "saved": "saved online"}.get(
                self.cloud_state, "online")
            text += f"   |   {self.session.name}{' (OWNER)' if self.session.is_owner else ''}: {state}"
        elif self.account_flow and self.account_flow[0] == "resume":
            text += "   |   logging in..."
        else:
            text += "   |   guest"
        draw_text(surf, text, font(12, bold=True), col, (12, 708), anchor="midleft")

    def can_all_in(self):
        sc = self.current()
        return CHIP_WIN.visible() and (hasattr(sc, "all_in") or hasattr(sc, "selected"))

    def press_all_in(self):
        sc = self.current()
        if hasattr(sc, "all_in"):
            sc.all_in()
        elif self.balance <= 0:
            sc.message = "YOU HAVE NO CHIPS TO BET"
        else:                                    # roulette, craps...: the next spot you click gets everything
            self.all_in_armed = not self.all_in_armed
            self.sfx("chip")

    def armed_click(self, e):
        """Place the whole balance on whichever spot was clicked, using the game's own betting."""
        sc = self.current()
        old, before = sc.selected, self.balance
        sc.selected = self.balance
        try:
            sc.handle(e)
        finally:
            sc.selected = old
        if self.balance != before:
            self.all_in_armed = False

    def draw_chip_arrows(self, surf):
        if not CHIP_WIN.visible():
            self.all_in_armed = False
            return
        mouse = pygame.mouse.get_pos()
        if self.can_all_in():
            r = CHIP_WIN.all_in_rect()
            armed = self.all_in_armed
            base = (200, 40, 40) if not armed else lerp_col((230, 50, 50), (255, 200, 60),
                                                                0.5 + 0.5 * math.sin(time.time() * 8))
            if r.collidepoint(mouse):
                base = lighten(base, 30)
            pygame.draw.rect(surf, darken(base, 60), r.move(0, 3), border_radius=12)
            pygame.draw.rect(surf, base, r, border_radius=12)
            pygame.draw.rect(surf, GOLD, r, width=2, border_radius=12)
            draw_text(surf, "ALL", font(15, bold=True), WHITE, (r.centerx, r.centery - 9), shadow=(0, 0, 0))
            draw_text(surf, "IN", font(15, bold=True), WHITE, (r.centerx, r.centery + 9), shadow=(0, 0, 0))
            if armed:
                draw_pill(surf, f"ALL IN: CLICK A BET SPOT TO PUT ALL {money(self.balance)} ON IT", font(16, bold=True),
                          (640, 612), WHITE, (160, 30, 30, 235), GOLD, pad=(16, 5))
        for rect, d in zip(CHIP_WIN.arrows(), (-1, 1)):
            can = CHIP_WIN.start > 0 if d < 0 else CHIP_WIN.start < CHIP_WIN.max_start()
            col = GOLD if can else (80, 72, 64)
            if can and rect.collidepoint(mouse):
                col = lighten(col, 40)
            pygame.draw.rect(surf, (0, 0, 0), rect, border_radius=8)
            pygame.draw.rect(surf, col, rect, width=2, border_radius=8)
            cx, cy = rect.center
            pygame.draw.polygon(surf, col, [(cx + 5 * d, cy), (cx - 4 * d, cy - 8), (cx - 4 * d, cy + 8)])

    def draw_others(self, surf):
        """Other players in the same game: their bets and their latest win or loss."""
        if not self.net or self.scene in ("menu", "title", "online", "netpoker", "netbj", "work", "counting", *MP_ONLY):
            return
        here = self.net.others(self.scene)
        if not here:
            return
        if not self.show_others:
            draw_pill(surf, f"{len(here)} FRIEND{'S' if len(here) > 1 else ''} HERE  (TAB)", font(12, bold=True),
                      (95, 76), WHITE, (20, 60, 120, 220), None, pad=(10, 3))
            return
        here = here[:5]
        box = pygame.Rect(10, 62, 240, 30 + 44 * len(here))
        p = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(p, (0, 0, 0, 175), p.get_rect(), border_radius=12)
        surf.blit(p, box)
        pygame.draw.rect(surf, (80, 120, 200), box, width=2, border_radius=12)
        draw_text(surf, "PLAYERS HERE   (TAB hides)", font(12, bold=True), (170, 200, 255), (box.x + 12, box.y + 15),
                  anchor="midleft")
        now = time.time()
        for k, pl in enumerate(here):
            y = box.y + 30 + k * 44
            col = PT_COLORS[pl["id"] % 4]
            pygame.draw.circle(surf, col, (box.x + 24, y + 20), 14)
            draw_text(surf, pl["name"][0].upper(), font(14, bold=True), WHITE, (box.x + 24, y + 20))
            draw_text(surf, pl["name"], fit_font(pl["name"], 15, 110), WHITE, (box.x + 46, y + 11), anchor="midleft")
            draw_text(surf, f"BET {money(pl['bet'])}" if pl["bet"] else "watching", font(12), (190, 195, 210),
                      (box.x + 46, y + 29), anchor="midleft")
            act = self.net.activity.get(pl["id"])
            if act and act[0] == self.scene and now - act[3] < 7:
                _, stake, ret, _ = act
                if ret > stake:
                    txt, bg = f"WON {money(ret - stake)}", (30, 140, 60, 235)
                elif ret < stake:
                    txt, bg = f"LOST {money(stake - ret)}", (150, 35, 40, 235)
                else:
                    txt, bg = "EVEN", (60, 60, 70, 235)
                draw_pill(surf, txt, font(12, bold=True), (box.right - 50, y + 20), WHITE, bg, None, pad=(8, 2))

    # ---- main loop -------------------------------------------------------
    def current(self):
        return self.scenes.get(self.scene, self.menu)

    def quit(self):
        for game in self.games:
            self.balance += game.outstanding_bets()
        self.save()
        if self.session and self.cloud_data and not WEB:        # last upload before closing
            Flow(flow_save(self.session, self.cloud_data)).wait(8)
        if self.net:
            self.net.close()
        if self.net_server:
            self.net_server.close()
        if WEB:                          # a browser tab can't close itself - you just close the tab
            return
        pygame.quit()
        sys.exit()

    def run(self):
        for _ in self.frames():
            pass

    def frames(self):
        """The main loop. It pauses (yields) at the start of every frame: the desktop just carries straight on,
        and the browser version uses the pause to let the web page draw and react."""
        while True:
            yield
            dt = min(self.clock.tick(FPS) / 1000, 0.05)
            for e in pygame.event.get():
                if WEB and WEB_DEBUG and e.type not in (pygame.MOUSEMOTION, pygame.WINDOWMOVED):
                    self.debug_log = (getattr(self, "debug_log", []) + [
                        f"{pygame.event.event_name(e.type)} {dict((k, v) for k, v in e.dict.items() if k in ('key', 'unicode', 'pos', 'button', 'x', 'y'))} [{self.scene}]"])[-12:]
                if e.type == pygame.QUIT:
                    self.quit()
                if e.type == pygame.KEYDOWN and e.key == pygame.K_F11:
                    toggle_fullscreen()
                    continue
                if self.help.active:
                    self.help.handle(e)
                    continue
                if self.owner_menu.active:
                    self.owner_menu.handle(e)
                    continue
                if self.chat_handle(e):
                    continue
                if (e.type == pygame.KEYDOWN and e.key == pygame.K_BACKQUOTE and self.owner_menu.allowed()
                        and not self.typing_elsewhere()):
                    self.owner_menu.toggle()
                    continue
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.events.rain and self.events.grab(e.pos):
                    continue
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and CHIP_WIN.visible():
                    hit = [d for r, d in zip(CHIP_WIN.arrows(), (-1, 1)) if r.collidepoint(e.pos)]
                    if hit:
                        CHIP_WIN.shift(hit[0])
                        self.sfx("chip")
                        continue
                    if self.can_all_in() and CHIP_WIN.all_in_rect().collidepoint(e.pos):
                        self.press_all_in()
                        continue
                    if self.all_in_armed:
                        if e.pos[1] >= 636:              # clicked the chips or buttons instead - cancel
                            self.all_in_armed = False
                        elif self.scene != "menu" and not self.lobby_btn.rect.collidepoint(e.pos):
                            self.armed_click(e)
                            continue
                if e.type == pygame.MOUSEWHEEL and CHIP_WIN.visible() and pygame.mouse.get_pos()[1] > 636:
                    CHIP_WIN.shift(-1 if e.y > 0 else 1)
                    continue
                if (self.net and e.type == pygame.KEYDOWN and e.key == pygame.K_TAB and self.scene != "online"
                        and not self.profile.active):
                    self.show_others = not self.show_others
                    continue
                if self.profile.active:
                    self.profile.handle(e)
                    continue
                if self.scene != "menu" and ((e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and
                                              self.help_btn.rect.collidepoint(e.pos)) or
                                             (e.type == pygame.KEYDOWN and e.key == pygame.K_F1)):
                    self.help.open(self.scene)
                    continue
                if self.scene != "menu":
                    back = (e.type == pygame.MOUSEBUTTONDOWN and e.button == 1
                            and self.lobby_btn.rect.collidepoint(e.pos)) or \
                           (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE)
                    if back:
                        if self.current().can_leave():
                            self.current().leave()
                            self.save()
                            self.scene = "menu"
                        continue
                self.current().handle(e)

            self.market.update(dt)
            self.account_tick(dt)
            self.events.tick(dt)
            self.themefx.update(dt)
            if self.owner_menu.active and not self.owner_menu.allowed():
                self.owner_menu.active = False
            if self.net_server:
                self.net_server.update(dt)
            if self.net and not self.net.update(dt):
                self.leave_game("Lost the connection to the game host")
            for ev in self.market.pop_alerts():
                self.effects.toast(ev["title"], ev["sub"], ev["kind"])
                self.sfx("alert")
            for bet in self.market.pop_settled():
                self.balance += bet["payout"]
                self.record("stockbet", bet["amount"], bet["payout"])
                word = "HIGHER" if bet["dir"] == "up" else "LOWER"
                move = f"{bet['sym']} ${bet['start']:,.2f} -> ${bet['end']:,.2f}"
                if bet["result"] == "win":
                    self.effects.toast("STOCK BET WON!", f"{word} was right ({move}) - you won {money(bet['payout'])}")
                    self.float_text(f"+{money(bet['payout'] - bet['amount'])}", (80, 230, 110))
                    self.sfx("win")
                elif bet["result"] == "push":
                    self.effects.toast("STOCK BET - NO CHANGE", f"{move} - your {money(bet['amount'])} is returned")
                else:
                    self.effects.toast("STOCK BET LOST", f"You said {word} ({move}) - you lost {money(bet['amount'])}")
                    self.sfx("lose")
                self.save()
            self.lottery.check()
            self.yesno.check()
            self.worth_check -= dt
            if self.worth_check <= 0:
                self.worth_check = 1.0
                worth = self.net_worth()
                for aid, need in (("high_roller", 10_000), ("big_shot", 100_000), ("millionaire", 1_000_000)):
                    if worth >= need:
                        self.unlock(aid)
            self.autosave_t -= dt
            if self.autosave_t <= 0:
                self.autosave_t = 10.0 if WEB else 30.0      # a browser tab can be closed at any moment
                self.save()
            up = self.auto_updater
            if (up and up.state == "ready" and self.scene in ("menu", "title") and not self.net and not self.help.active
                    and not self.profile.active):
                if updater.install(up.staged):     # a helper swaps in the new .exe once we close, then restarts it
                    up.state = "installing"
                    self.quit()
                up.state = "failed"
            if self.scene != self.last_scene:            # first visit to a game: show its guide
                self.last_scene = self.scene
                self.all_in_armed = False
                self.effects.fade = 1.0
                CHIP_WIN.auto(self.balance)
                if self.scene == "menu":
                    self.menu.on_enter()
                elif hasattr(self.current(), "on_enter"):
                    self.current().on_enter()
                if self.scene in HELP and self.scene not in self.help_seen:
                    self.help_seen.add(self.scene)
                    self.help.open(self.scene)
                    self.save()
            if self.help.active:
                self.help.update(dt)                      # the game waits while you read
            elif not self.profile.active:
                self.current().update(dt)
            diff = self.balance - self.shown_balance
            self.shown_balance = self.balance if abs(diff) < 1 else self.shown_balance + diff * min(1, dt * 7)

            CHIP_WIN.frame += 1
            self.current().draw(self.screen)
            if WEB and WEB_DEBUG:
                for i, line in enumerate(getattr(self, "debug_log", [])):
                    draw_text(self.screen, line, font(13, bold=True), (255, 255, 0), (8, 70 + i * 16), anchor="topleft",
                              shadow=(0, 0, 0))
            self.draw_chip_arrows(self.screen)
            self.themefx.draw(self.screen, self.scene)
            self.draw_others(self.screen)
            self.draw_chat(self.screen)
            self.events.draw(self.screen)
            for f in self.floaters:
                f["y"] += 28 * dt
                f["life"] -= dt
                img = font(24, bold=True).render(f["text"], True, f["color"])
                img.set_alpha(int(255 * max(0, min(1, f["life"]))))
                self.screen.blit(img, img.get_rect(midright=(W - 32, f["y"] + 8)))
            self.floaters = [f for f in self.floaters if f["life"] > 0]
            self.effects.update(dt)
            self.effects.draw(self.screen)
            if self.help.active:
                self.help.draw(self.screen)
            if self.profile.active:
                self.profile.draw(self.screen)
            if self.owner_menu.active:
                self.owner_menu.draw(self.screen)
            pygame.display.flip()


if __name__ == "__main__":
    App().run()
