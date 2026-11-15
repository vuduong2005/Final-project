"""Maria as pixel art, drawn in code: 64x64 blocks, three moods.

  idle  - eyes open, small smile (and a blink frame)
  talk  - happy closed eyes, blush, an open smiling mouth that follows her voice
  alarm - wide shocked eyes and a big screaming mouth, for danger warnings

She is laid out on an 800x800 design grid, drawn four times larger than needed, and then every
block takes the most common flat colour of the area it covers. One frame takes about 0.1 s to
draw, so build_frames() draws them all once and the overlay only picks between them.
"""
from typing import Dict, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

DESIGN = 800
CELLS = 64                     # blocks across and down
TALK_STEPS = 6                 # mouth positions while talking (0 = closed smile)
ALARM_STEPS = 3                # mouth positions while warning

HAIR, HAIR_DARK, HAIR_LIGHT = (124, 82, 62), (92, 58, 46), (160, 114, 90)
SKIN, SKIN_SHADE, BLUSH = (253, 234, 217), (240, 206, 188), (246, 170, 168)
LINE = (66, 42, 36)
TOP, CARDIGAN, CARDIGAN_SHADE = (250, 250, 250), (170, 218, 224), (134, 190, 200)
IRIS, IRIS_DARK = (146, 136, 212), (88, 78, 160)
MOUTH_IN, TONGUE = (150, 58, 70), (232, 130, 132)
WHITE = (255, 255, 255)
GLOW, GLOW_ALARM = (255, 240, 200), (255, 70, 60)

_COLOURS = [HAIR, HAIR_DARK, HAIR_LIGHT, SKIN, SKIN_SHADE, BLUSH, LINE, TOP, CARDIGAN, CARDIGAN_SHADE,
            IRIS, IRIS_DARK, MOUTH_IN, TONGUE, WHITE]
_PALETTE = Image.new("P", (1, 1))
_PALETTE.putpalette([v for c in _COLOURS for v in c] + list(_COLOURS[0]) * (256 - len(_COLOURS)))

# The ponytail's centre line: from the tie, bowing out over her shoulder, back in towards her chest,
# then the tip hooks outward and up a little.
_TAIL_PATH = [(280, 494), (254, 534), (242, 588), (252, 642), (282, 692), (294, 738), (272, 770),
              (240, 778), (218, 764), (210, 742)]


class _Painter:
    """A large drawing surface addressed in design units."""

    def __init__(self, width: int, line_units: float):
        self.w = width
        self.k = width / DESIGN
        self.image = Image.new("RGBA", (width, width), (0, 0, 0, 0))
        self.line = max(1, round(line_units * self.k))

    def pts(self, points):
        return [(x * self.k, y * self.k) for x, y in points]

    def box(self, x0, y0, x1, y1):
        return [x0 * self.k, y0 * self.k, x1 * self.k, y1 * self.k]

    def mask(self):
        m = Image.new("L", (self.w, self.w), 0)
        return m, ImageDraw.Draw(m)

    def soften(self, mask, radius):
        """Round off the corners of a shape."""
        return mask.filter(ImageFilter.GaussianBlur(radius * self.k)).point(lambda a: 255 if a > 127 else 0)

    def shifted(self, mask, dx, dy):
        moved = Image.new("L", mask.size, 0)
        moved.paste(mask, (round(dx * self.k), round(dy * self.k)))
        return moved

    def paint(self, mask, colour, outline=True):
        if outline:
            self.image.paste(LINE + (255,), (0, 0), mask.filter(ImageFilter.MaxFilter(self.line * 2 + 1)))
        self.image.paste(colour + (255,), (0, 0), mask)

    def draw(self):
        return ImageDraw.Draw(self.image)


# ============================================================================
# CURVES (for the ponytail)
# ============================================================================

def _spline(points, steps):
    """A smooth curve through the points (Catmull-Rom)."""
    pts = [points[0]] + list(points) + [points[-1]]
    out = []
    per_piece = max(2, steps // (len(points) - 1))
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for j in range(per_piece):
            t = j / per_piece
            out.append(tuple(
                0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t * t
                       + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t ** 3) for k in (0, 1)))
    out.append(points[-1])
    return out


def _sweep(p, path, radius):
    """A round brush dragged along the path, radius(t) thick at fraction t of the way."""
    m, d = p.mask()
    for i, (x, y) in enumerate(path):
        r = radius(i / (len(path) - 1))
        d.ellipse(p.box(x - r, y - r, x + r, y + r), fill=255)
    return m


def _offset(path, distance):
    """The path moved sideways by `distance`."""
    out = []
    for i, (x, y) in enumerate(path):
        ax, ay = path[max(0, i - 1)]
        bx, by = path[min(len(path) - 1, i + 1)]
        dx, dy = bx - ax, by - ay
        length = (dx * dx + dy * dy) ** 0.5 or 1
        out.append((x - dy / length * distance, y + dx / length * distance))
    return out


# ============================================================================
# THE DRAWING
# ============================================================================

def _ponytail(p):
    """Slim at the tie, fuller in the middle, thinning to a hooked point. Three tones and two
    strand lines make it look round instead of flat."""
    path = _spline(_TAIL_PATH, 120)
    tail = _sweep(p, path, lambda t: 17 + 15 * t / 0.4 if t < 0.4 else 32 - 29 * ((t - 0.4) / 0.6) ** 0.75)
    p.paint(tail, HAIR)
    p.image.paste(HAIR_DARK + (255,), (0, 0), ImageChops.subtract(tail, p.shifted(tail, -26, -10)))   # inner side
    band, d = p.mask()
    d.ellipse(p.box(150, 500, 280, 700), fill=255)
    light = ImageChops.subtract(tail, p.shifted(tail, 20, 0))                                         # outer side
    p.image.paste(HAIR_LIGHT + (255,), (0, 0), ImageChops.multiply(light, band))

    dr = p.draw()
    thin = max(1, round(p.line * 0.7))
    for side in (-0.45, 0.35):
        dr.line(p.pts(_offset(path, side * 22)[6:-30]), fill=HAIR_DARK, width=thin, joint="curve")
    dr.line(p.pts(_offset(path[int(len(path) * 0.72):-4], -6)), fill=LINE,
            width=max(1, round(p.line * 0.9)), joint="curve")                                         # inside the hook
    dr.line(p.pts(_offset(path[int(len(path) * 0.5):int(len(path) * 0.66)], 18)), fill=HAIR_LIGHT,
            width=max(1, round(p.line * 1.2)), joint="curve")                                         # shine

    tie, d = p.mask()
    d.rounded_rectangle(p.box(262, 468, 324, 502), radius=14 * p.k, fill=255)
    p.paint(tie, IRIS_DARK)
    knot, d = p.mask()                                           # the bump of hair above the tie
    d.ellipse(p.box(266, 440, 326, 476), fill=255)
    p.paint(knot, HAIR)
    p.image.paste(HAIR_DARK + (255,), (0, 0), ImageChops.subtract(knot, p.shifted(knot, -14, -10)))


def _body_and_face(p):
    m, d = p.mask()                                              # hair behind the head
    d.ellipse(p.box(222, 150, 578, 540), fill=255)
    d.rectangle(p.box(266, 480, 534, 612), fill=255)             # and behind her neck, so no see-through gaps there
    p.paint(m, HAIR_DARK)

    body, d = p.mask()                                           # neck and shoulders
    d.rectangle(p.box(358, 480, 442, 640), fill=255)
    d.ellipse(p.box(150, 600, 650, 1000), fill=255)
    p.paint(body, SKIN)
    shade, d = p.mask()
    d.ellipse(p.box(330, 470, 470, 572), fill=255)
    p.image.paste(SKIN_SHADE + (255,), (0, 0), ImageChops.multiply(shade, body))

    m, d = p.mask()                                              # white top under the cardigan
    d.polygon(p.pts([(340, 712), (460, 712), (456, 810), (344, 810)]), fill=255)
    p.paint(m, TOP)

    m, d = p.mask()                                              # light blue cardigan, open at the front
    d.ellipse(p.box(150, 600, 650, 1000), fill=255)
    opening, od = p.mask()
    od.polygon(p.pts([(318, 590), (482, 590), (462, 700), (440, 810), (360, 810), (338, 700)]), fill=255)
    p.paint(ImageChops.subtract(m, opening), CARDIGAN)
    dr = p.draw()
    dr.line(p.pts([(228, 700), (214, 800)]), fill=CARDIGAN_SHADE, width=p.line * 2)
    dr.line(p.pts([(572, 700), (586, 800)]), fill=CARDIGAN_SHADE, width=p.line * 2)

    _ponytail(p)

    m, d = p.mask()                                              # face: round cheeks, soft chin
    d.ellipse(p.box(256, 190, 544, 486), fill=255)
    d.polygon(p.pts([(262, 372), (538, 372), (508, 458), (452, 516), (400, 536), (348, 516), (292, 458)]), fill=255)
    p.paint(p.soften(m, 6), SKIN)


def _front_hair(p):
    """Drawn last, so the fringe falls over the eyes."""
    m, d = p.mask()
    d.ellipse(p.box(236, 146, 564, 470), fill=255)
    window, wd = p.mask()                                        # where the face shows
    wd.ellipse(p.box(264, 262, 540, 620), fill=255)
    m = ImageChops.subtract(m, window)
    d = ImageDraw.Draw(m)
    d.polygon(p.pts([(288, 262), (356, 236), (330, 300), (300, 368)]), fill=255)       # fringe, swept to one side
    d.polygon(p.pts([(340, 240), (432, 232), (392, 286), (352, 340)]), fill=255)
    d.polygon(p.pts([(420, 232), (470, 246), (440, 300)]), fill=255)
    d.polygon(p.pts([(470, 244), (530, 290), (522, 368), (500, 318)]), fill=255)
    d.polygon(p.pts([(250, 320), (280, 296), (280, 420), (268, 470), (254, 430)]), fill=255)   # side locks
    d.polygon(p.pts([(550, 320), (524, 296), (524, 440), (536, 540), (552, 600), (550, 480)]), fill=255)
    p.paint(m, HAIR)
    p.draw().arc(p.box(270, 170, 470, 330), 200, 285, fill=HAIR_LIGHT, width=p.line * 2)


def _blush(p, strength):
    m, d = p.mask()
    for cx in (316, 484):
        d.ellipse(p.box(cx - 34, 430, cx + 34, 430 + 34 * strength), fill=255)
    p.image.paste(BLUSH + (255,), (0, 0), m)


def _features(p, level, blink, mood):
    thin = max(1, round(p.line * 0.8))

    if mood == "alarm":
        _blush(p, 0.7)
        for cx in (330, 470):                                    # huge white eyes, tiny pupils
            m, d = p.mask()
            d.rounded_rectangle(p.box(cx - 46, 340, cx + 46, 420), radius=30 * p.k, fill=255)
            p.paint(m, WHITE)
            dr = p.draw()
            dr.ellipse(p.box(cx - 13, 368, cx + 13, 394), fill=IRIS_DARK + (255,))
            dr.ellipse(p.box(cx - 5, 375, cx + 5, 385), fill=TONGUE + (255,))
            dr.line(p.pts([(cx - 44, 322), (cx + 44, 322)]), fill=LINE, width=p.line)          # raised brows
            dr.line(p.pts([(cx - 30, 434), (cx + 30, 434)]), fill=LINE, width=thin)            # stress lines
        depth = 54 + 18 * level                                  # big screaming mouth
        m, d = p.mask()
        d.polygon(p.pts([(338, 446), (462, 446), (456, 446 + depth * 0.75), (400, 446 + depth),
                         (344, 446 + depth * 0.75)]), fill=255)
        m = p.soften(m, 7)
        p.paint(m, MOUTH_IN)
        tongue, d = p.mask()
        d.ellipse(p.box(356, 446 + depth * 0.45, 444, 446 + depth * 1.5), fill=255)
        p.image.paste(TONGUE + (255,), (0, 0), ImageChops.multiply(tongue, m))
        return

    happy = mood == "talk"
    _blush(p, 1.0 if happy else 0.45)
    dr = p.draw()
    dr.arc(p.box(296, 300, 372, 350), 200, 320, fill=LINE, width=thin)                          # eyebrows
    dr.arc(p.box(428, 300, 504, 350), 220, 340, fill=LINE, width=thin)
    for cx in (336, 464):
        if happy:                                                # smiling closed eyes: an upward curve
            dr.arc(p.box(cx - 38, 366, cx + 38, 436), 200, 340, fill=LINE, width=round(p.line * 1.4))
        elif blink:
            dr.arc(p.box(cx - 36, 350, cx + 36, 410), 20, 160, fill=LINE, width=p.line)
        else:
            dr.ellipse(p.box(cx - 34, 346, cx + 34, 428), fill=WHITE + (255,))
            dr.ellipse(p.box(cx - 19, 356, cx + 19, 426), fill=IRIS + (255,))
            dr.ellipse(p.box(cx - 9, 374, cx + 9, 412), fill=IRIS_DARK + (255,))
            dr.ellipse(p.box(cx - 18, 358, cx - 2, 378), fill=WHITE + (255,))
            dr.arc(p.box(cx - 42, 336, cx + 42, 440), 190, 350, fill=LINE, width=round(p.line * 2.0))

    if level < 0.08:                                             # closed smile
        dr.arc(p.box(352, 424, 448, 492), 25, 155, fill=LINE, width=round(p.line * 1.3))
    else:                                                        # open and smiling: flat on top, round below
        half_w, depth = 36 + 12 * level, 16 + 44 * level
        m, d = p.mask()
        d.chord(p.box(400 - half_w, 462 - depth, 400 + half_w, 462 + depth), 0, 180, fill=255)
        p.paint(m, MOUTH_IN)
        if level > 0.3:
            tongue, d = p.mask()
            d.ellipse(p.box(400 - half_w * 0.6, 462 + depth * 0.45, 400 + half_w * 0.6, 462 + depth * 1.5), fill=255)
            p.image.paste(TONGUE + (255,), (0, 0), ImageChops.multiply(tongue, m))


# ============================================================================
# FRAMES
# ============================================================================

def render(level: float = 0.0, blink: bool = False, mood: str = "idle") -> Image.Image:
    """One frame as a CELLS x CELLS RGBA picture. `level` (0 to 1) opens the mouth."""
    level = max(0.0, min(1.0, level))
    p = _Painter(CELLS * 4, line_units=DESIGN / CELLS * 0.95)    # outlines about one block thick
    _body_and_face(p)
    _features(p, level, blink, mood)
    _front_hair(p)
    alpha = p.image.getchannel("A").resize((CELLS, CELLS), Image.BOX).point(lambda a: 255 if a > 120 else 0)
    flat = p.image.convert("RGB").quantize(palette=_PALETTE, dither=Image.Dither.NONE).filter(ImageFilter.ModeFilter(3))
    return Image.merge("RGBA", (*flat.resize((CELLS, CELLS), Image.NEAREST).convert("RGB").split(), alpha))


def frame_key(mood: str, level: float = 0.0, blink: bool = False) -> Tuple:
    """Which ready-made frame fits this moment."""
    level = max(0.0, min(1.0, level))
    if mood == "alarm":
        return ("alarm", round(level * (ALARM_STEPS - 1)))
    if mood == "talk":
        return ("talk", round(level * (TALK_STEPS - 1)))
    return ("idle", bool(blink))


def build_frames() -> Dict[Tuple, Image.Image]:
    """Every frame the overlay can show (about 1.5 s of drawing, done once)."""
    frames = {("idle", False): render(), ("idle", True): render(blink=True)}
    for i in range(TALK_STEPS):
        frames[("talk", i)] = render(i / (TALK_STEPS - 1), mood="talk")
    for i in range(ALARM_STEPS):
        frames[("alarm", i)] = render(i / (ALARM_STEPS - 1), mood="alarm")
    return frames


def with_glow(sprite: Image.Image, mood: str, level: float) -> Image.Image:
    """The frame with a ring of light around her outline: warm while talking, red and thicker
    for a warning, brighter when she is louder."""
    alpha = sprite.getchannel("A")
    solid = alpha.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))      # close small gaps
    ring = ImageChops.subtract(solid.filter(ImageFilter.MaxFilter(5 if mood == "alarm" else 3)), solid)
    strength = 0.55 + 0.45 * max(0.0, min(1.0, level))
    glow = Image.new("RGBA", sprite.size, (GLOW_ALARM if mood == "alarm" else GLOW) + (0,))
    glow.putalpha(ring.point(lambda a: int(a * strength)))
    return Image.alpha_composite(glow, sprite)
