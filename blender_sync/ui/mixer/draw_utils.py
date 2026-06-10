# =============================================================================
# ui/mixer/draw_utils.py
# Low-level GPU drawing helpers used by every other UI file.
#
# draw_element() is the PNG bridge:
#   - checks texture_cache for a loaded PNG skin first
#   - falls back to GPU primitives if no texture is found
#   - callers never need to change when textures are added
#
# All other functions are pure GPU primitives used for elements that don't
# have a PNG skin yet (or never will — like waveforms and spectrum bars).
# =============================================================================

import math
import gpu
import blf
from gpu_extras.batch import batch_for_shader
# ---------------------------------------------------------------------------
# Shader singleton — gpu.shader.from_builtin() is expensive; reuse one instance.
# ---------------------------------------------------------------------------
_shader = None
_image_shader = None

# =============================================================================
# NUMBOX TEXT TUNING
# All values are UNSCALED — multiplied by UI_SCALE (via h) at draw time.
# Offsets are relative to the computed default position.
# =============================================================================

# ── Line 1 (label: "CH N PK") ────────────────────────────────────────────────
NUMBOX_L1_FONT_SIZE  = 0.34   # fraction of box height → font size in px
NUMBOX_L1_COLOR      = (0.604, 0.729, 0.710, 1.0)   # dim cyan
NUMBOX_L1_X_OFFSET   = 0.0   # px nudge left/right from centred position
NUMBOX_L1_Y_OFFSET   = 2.0   # px nudge up/down from stacked position

# ── Line 2 (value: "0.850") ───────────────────────────────────────────────────
NUMBOX_L2_FONT_SIZE  = 0.34   # fraction of box height → font size in px
NUMBOX_L2_COLOR      = (0.604, 0.729, 0.710, 1.0)   # bright neon cyan
NUMBOX_L2_X_OFFSET   = 0.0   # px nudge left/right from centred position
NUMBOX_L2_Y_OFFSET   = 12.0   # px nudge up/down from 2px-from-bottom position

def _get_shader():
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _shader

def _get_image_shader():
    """Cached IMAGE shader — gpu.shader.from_builtin() is expensive, reuse it."""
    global _image_shader
    if _image_shader is None:
        _image_shader = gpu.shader.from_builtin("IMAGE")
    return _image_shader

def _draw_led_batch(tex, quads: list) -> None:
    """Draw a list of (x, y, w, h) quads sharing one LED texture in a single draw call."""
    if tex is None or not quads:
        return
    verts = []
    uvs   = []
    for (qx, qy, qw, qh) in quads:
        verts += [(qx,    qy),    (qx+qw, qy),    (qx+qw, qy+qh),
                  (qx,    qy),    (qx+qw, qy+qh), (qx,    qy+qh)]
        uvs   += [(0.0,   0.0),   (1.0,   0.0),   (1.0,   1.0),
                  (0.0,   0.0),   (1.0,   1.0),   (0.0,   1.0)]
    from ui.mixer.texture_cache import blit_texture as _bt
    shader = _get_image_shader()
    gpu.state.blend_set("ALPHA_PREMULT")
    batch  = batch_for_shader(shader, "TRIS", {"pos": verts, "texCoord": uvs})
    shader.bind()
    shader.uniform_sampler("image", tex)
    batch.draw(shader)
    gpu.state.blend_set("ALPHA")



from ui.mixer.texture_cache import get_texture, blit_texture


# ---------------------------------------------------------------------------
# Core primitives
# ---------------------------------------------------------------------------

def draw_rect(x: float, y: float, w: float, h: float, color: tuple) -> None:
    """Filled axis-aligned rectangle."""
    if w <= 0 or h <= 0:
        return
    shader = _get_shader()
    batch  = batch_for_shader(shader, "TRI_STRIP",
                              {"pos": [(x, y), (x+w, y), (x, y+h), (x+w, y+h)]})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_rounded_rect(x: float, y: float, w: float, h: float,
                      r: float, color: tuple) -> None:
    """Filled rounded rectangle. r = corner radius in px."""
    if w <= 0 or h <= 0:
        return
    r = min(r, w / 2.0, h / 2.0)
    if r < 1.0:
        draw_rect(x, y, w, h, color)
        return

    shader = _get_shader()
    cx, cy = x + w / 2.0, y + h / 2.0
    verts  = [(cx, cy)]
    corners = [
        (x + r,     y + r,     math.pi,       1.5 * math.pi),
        (x + w - r, y + r,     1.5 * math.pi, 2.0 * math.pi),
        (x + w - r, y + h - r, 0.0,           0.5 * math.pi),
        (x + r,     y + h - r, 0.5 * math.pi, math.pi),
    ]
    SEGS = 8
    for (ox, oy, a0, a1) in corners:
        for i in range(SEGS + 1):
            a = a0 + (a1 - a0) * i / SEGS
            verts.append((ox + math.cos(a) * r, oy + math.sin(a) * r))
    verts.append(verts[1])
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_rect_outline(x: float, y: float, w: float, h: float,
                      color: tuple, line_w: float = 1.0) -> None:
    """Rectangle outline only."""
    shader = _get_shader()
    verts  = [(x, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)]
    batch  = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    gpu.state.line_width_set(line_w)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)


def draw_line(x1: float, y1: float, x2: float, y2: float,
              color: tuple, width: float = 1.0) -> None:
    shader = _get_shader()
    batch  = batch_for_shader(shader, "LINES", {"pos": [(x1, y1), (x2, y2)]})
    gpu.state.line_width_set(width)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)


def draw_circle(cx: float, cy: float, r: float, color: tuple,
                filled: bool = True, segs: int = 20) -> None:
    shader = _get_shader()
    if filled:
        verts = [(cx, cy)]
        for i in range(segs + 1):
            a = 2 * math.pi * i / segs
            verts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
        batch = batch_for_shader(shader, "TRI_FAN", {"pos": verts})
    else:
        verts = [(cx + math.cos(2*math.pi*i/segs)*r,
                  cy + math.sin(2*math.pi*i/segs)*r)
                 for i in range(segs + 1)]
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def draw_text(text: str, x: float, y: float, size: float,
              color: tuple = (1, 1, 1, 1)) -> None:
    blf.size(0, max(1, int(size)))
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, text)


def text_width(text: str, size: float) -> float:
    blf.size(0, max(1, int(size)))
    return blf.dimensions(0, text)[0]


# ---------------------------------------------------------------------------
# Compound primitives
# ---------------------------------------------------------------------------

def draw_knob(cx: float, cy: float, radius: float, norm_value: float,
              color: tuple, label: str, value_str: str, scale: float,
              label_above: bool = False) -> None:
    """Rotary knob — 270° arc, inner cap, pointer line, label + value."""
    draw_circle(cx, cy, radius, (0.13, 0.13, 0.13, 1.0))
    draw_circle(cx, cy, radius, (0.33, 0.33, 0.33, 1.0), filled=False)
    inner_r = radius * 0.7
    draw_circle(cx, cy, inner_r, (0.1, 0.1, 0.1, 1.0))

    # Hardware knob convention: starts lower-left (225°), sweeps clockwise
    # to lower-right (315°). In y-up GPU coords clockwise = decreasing angle,
    # so we start at 225° and sweep -270°.
    arc_start = 225.0
    arc_total = -270.0
    shader = _get_shader()
    segs   = 24

    # Active arc
    active_angle = arc_start + norm_value * arc_total
    arc_pts = []
    a0 = math.radians(arc_start)
    a1 = math.radians(active_angle)
    steps = max(2, int(abs(a1 - a0) / (2 * math.pi) * segs))
    for i in range(steps + 1):
        a = a0 + (a1 - a0) * i / steps
        arc_pts.append((cx + math.cos(a) * radius * 0.88,
                        cy + math.sin(a) * radius * 0.88))
    if len(arc_pts) >= 2:
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": arc_pts})
        gpu.state.line_width_set(max(2.0, scale * 1.5))
        shader.bind()
        shader.uniform_float("color", (*color, 1.0))
        batch.draw(shader)
        gpu.state.line_width_set(1.0)

    # Inactive arc remainder
    inactive_pts = []
    a_start_inactive = a1
    a_end_inactive   = math.radians(arc_start + arc_total)
    steps2 = max(2, int(abs(a_end_inactive - a_start_inactive)
                        / (2 * math.pi) * segs))
    for i in range(steps2 + 1):
        a = a_start_inactive + (a_end_inactive - a_start_inactive) * i / steps2
        inactive_pts.append((cx + math.cos(a) * radius * 0.88,
                              cy + math.sin(a) * radius * 0.88))
    if len(inactive_pts) >= 2:
        batch2 = batch_for_shader(shader, "LINE_STRIP", {"pos": inactive_pts})
        gpu.state.line_width_set(max(1.5, scale))
        shader.bind()
        shader.uniform_float("color", (0.25, 0.25, 0.25, 1.0))
        batch2.draw(shader)
        gpu.state.line_width_set(1.0)

    # Pointer line
    ptr_angle = math.radians(active_angle)
    px = cx + math.cos(ptr_angle) * inner_r * 0.75
    py = cy + math.sin(ptr_angle) * inner_r * 0.75
    pline = batch_for_shader(shader, "LINES", {"pos": [(cx, cy), (px, py)]})
    gpu.state.line_width_set(max(1.5, scale))
    shader.bind()
    shader.uniform_float("color", (0.9, 0.9, 0.9, 1.0))
    pline.draw(shader)
    gpu.state.line_width_set(1.0)

    # Labels
    fs = max(1, int(8 * scale))
    if label_above:
        draw_text(label,
                  cx - text_width(label, fs) / 2,
                  cy + radius + 3 * scale,
                  fs, (0.6, 0.6, 0.6, 1.0))
        draw_text(value_str,
                  cx - text_width(value_str, fs) / 2,
                  cy - radius - fs - 2 * scale,
                  fs, (0.8, 0.8, 0.8, 1.0))
    else:
        draw_text(label,
                  cx - text_width(label, fs) / 2,
                  cy - radius - fs - 2 * scale,
                  fs, (0.6, 0.6, 0.6, 1.0))
        draw_text(value_str,
                  cx - text_width(value_str, fs) / 2,
                  cy - radius - fs * 2 - 4 * scale,
                  fs, (0.8, 0.8, 0.8, 1.0))


def draw_circle_knob(x: float, y: float, radius: float, value: float,
                     color: tuple, label: str,
                     ui_scale: float = 1.0) -> None:
    """Simple circle knob used in the mixer strip (gain, EQ, pan).

    ui_scale must be passed explicitly by callers — avoids a circular import
    of mixer_hud.UI_SCALE that previously executed on every draw call.
    """

    shader = _get_shader()
    verts  = [(x, y)]
    for i in range(17):
        a = 2 * math.pi * i / 16
        verts.append((x + math.cos(a) * radius, y + math.sin(a) * radius))
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", (color[0]*.3, color[1]*.3, color[2]*.3, 1.0))
    batch.draw(shader)

    a  = (1.25 * math.pi) - (value * 1.5 * math.pi)
    le = (x + math.cos(a) * radius, y + math.sin(a) * radius)
    lb = batch_for_shader(shader, "LINES", {"pos": [(x, y), le]})
    shader.uniform_float("color", (1, 1, 1, 1))
    lb.draw(shader)

    blf.size(0, int(9 * ui_scale))
    blf.position(0, x - radius * .8, y - (radius + 12 * ui_scale), 0)
    blf.draw(0, label)


def draw_meter(x: float, y: float, w: float, h: float,
               level: float, peak: float) -> None:
    """VU LED meter — 19 segments, batched by colour zone (4 draw calls max).

    Uses meter_led_green/yellow/red/off tiles if loaded.
    Falls back to original solid bar if tiles not found.
    """
    N_LEDS     = 19
    GREEN_TOP  = 0.70   # bottom 70% of segments are green
    YELLOW_TOP = 0.90   # 70-90% yellow, above 90% red

    from ui.mixer.texture_cache import get_texture as _gt
    tex_green  = _gt("meter_led_green")
    tex_yellow = _gt("meter_led_yellow")
    tex_red    = _gt("meter_led_red")
    tex_off    = _gt("meter_led_off")

    # ── Fallback: solid bar when tiles not loaded ────────────────────────
    if not (tex_green or tex_yellow or tex_red or tex_off):
        draw_rect(x, y, w, h, (0.03, 0.03, 0.03, 1.0))
        if level > 0.0:
            fh = min(level, 1.0) * h
            gt, yt = h * GREEN_TOP, h * YELLOW_TOP
            if fh > 0:
                draw_rect(x, y, w, min(fh, gt), (0.0, 0.55, 0.15, 1.0))
            if fh > gt:
                draw_rect(x, y + gt, w, min(fh - gt, yt - gt), (0.7, 0.6, 0.0, 1.0))
            if fh > yt:
                draw_rect(x, y + yt, w, fh - yt, (0.85, 0.1, 0.1, 1.0))
        if peak > 0.0:
            ph  = min(peak, 1.0) * h
            col = ((0.85, 0.1, 0.1, 1.0) if peak > 0.90 else
                   (0.7, 0.6, 0.0, 1.0)  if peak > 0.70 else
                   (0.0, 0.85, 0.3, 1.0))
            draw_rect(x, y + ph - 1, w, max(1.5, h * 0.01), col)
        return

    # ── LED tile path ────────────────────────────────────────────────────
    tile_h    = h / N_LEDS
    lit_count = int(min(max(level, 0.0), 1.0) * N_LEDS)
    peak_idx  = (min(int(min(peak, 1.0) * N_LEDS), N_LEDS - 1)
                 if peak > 0.0 else -1)

    green_q = []
    yellow_q = []
    red_q   = []
    off_q   = []

    for i in range(N_LEDS):
        quad    = (x, y + i * tile_h, w, tile_h)
        is_lit  = (i < lit_count) or (i == peak_idx)
        frac    = (i + 1) / N_LEDS
        if is_lit:
            if frac <= GREEN_TOP:
                green_q.append(quad)
            elif frac <= YELLOW_TOP:
                yellow_q.append(quad)
            else:
                red_q.append(quad)
        else:
            off_q.append(quad)

    # One draw call per colour type — 4 max regardless of segment count
    _draw_led_batch(tex_off,                off_q)
    _draw_led_batch(tex_green  or tex_off,  green_q)
    _draw_led_batch(tex_yellow or tex_off,  yellow_q)
    _draw_led_batch(tex_red    or tex_off,  red_q)


def draw_numbox(x: float, y: float, w: float, h: float,
                value: float, highlighted: bool = False,
                channel: int = 0, peak_hold: float = 0.0,
                skip_bg: bool = False, scale: float = 1.0) -> None:
    """Fader value display box.

    Two-line LED style when skip_bg=True (skin active):
      top row    — dim cyan label  "CH N  PK"
      bottom row — bright cyan value e.g. "0.850"

    skip_bg=True  → no background rect / border drawn (strip_bottom_bg PNG provides it).
    skip_bg=False → original solid rect + border fallback.
    """
    if not skip_bg:
        bg = (0.18, 0.25, 0.18, 1.0) if highlighted else (0.08, 0.08, 0.08, 1.0)
        draw_rect(x, y, w, h, bg)
        shader = _get_shader()
        verts  = [(x, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)]
        batch  = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
        shader.bind()
        bc = (0.3, 0.5, 0.3, 1.0) if highlighted else (0.2, 0.2, 0.2, 1.0)
        shader.uniform_float("color", bc)
        batch.draw(shader)

    # ── Two-line LED readout ──────────────────────────────────────────────────
    # Line 2 (bottom): bright value  e.g. "0.850"
    # Line 1 (top):    dim label     e.g. "CH 3 PK"
    # Tuning constants at top of file: NUMBOX_L1_* / NUMBOX_L2_*

    # Line 2 — value, displayed as dB (linear multiplier → dB conversion)
    # 0.001 is the hard fader floor → shows -∞. Otherwise 20*log10(value).
    fs_val  = max(1, int(h * NUMBOX_L2_FONT_SIZE))
    if value <= 0.1:
        val_str = "-∞ db"
    else:
        _db = 20.0 * math.log10(value)
        val_str = f"+{_db:.1f} db" if _db >= 0 else f"{_db:.1f} db"
    tw_val  = text_width(val_str, fs_val)
    val_col = (0.0, 0.95, 1.0, 1.0) if highlighted else NUMBOX_L2_COLOR
    val_y   = y + 2 * scale + NUMBOX_L2_Y_OFFSET * scale
    draw_text(val_str,
              x + (w - tw_val) / 2 + NUMBOX_L2_X_OFFSET * scale,
              val_y,
              fs_val, val_col)

    # Line 1 — label "CH N PK"  (always this format, peak value omitted to keep it short)
    fs_label = max(1, int(h * NUMBOX_L1_FONT_SIZE))
    lbl_str  = f"CH {channel + 1} PK"
    # Shrink font until label fits inside box width with 2px margin each side
    _fs = fs_label
    while _fs > 1 and text_width(lbl_str, _fs) > (w - 4):
        _fs -= 1
    tw_lbl = text_width(lbl_str, _fs)
    lbl_y  = val_y + fs_val + 1 * scale + NUMBOX_L1_Y_OFFSET * scale
    draw_text(lbl_str,
              x + (w - tw_lbl) / 2 + NUMBOX_L1_X_OFFSET * scale,
              lbl_y,
              _fs, NUMBOX_L1_COLOR)


# ---------------------------------------------------------------------------
# PNG bridge — the key function for future skin support
# ---------------------------------------------------------------------------

def draw_element(key: str, x: float, y: float, w: float, h: float,
                 fallback_fn, *fallback_args, **fallback_kwargs) -> None:
    """Draw a UI element — uses PNG texture if available, else calls fallback_fn.

    key          : skin map key (e.g. "knob", "fader_handle", "btn_mute_on")
    x, y, w, h  : bounding rect (bottom-left origin, scaled pixels)
    fallback_fn  : called with (x, y, w, h, *fallback_args) when no texture found

    Example:
        draw_element("fader_handle", hx, hy, fw, fhh,
                     draw_rect, (0.5, 0.5, 0.5, 1.0))
    """
    tex = get_texture(key)
    if tex is not None:
        blit_texture(tex, x, y, w, h, key=key)
    else:
        fallback_fn(x, y, w, h, *fallback_args, **fallback_kwargs)


def draw_element_if_loaded(key: str, x: float, y: float,
                           w: float, h: float) -> bool:
    """Blit PNG skin only if the texture is loaded — draw nothing if not found.

    Use this where a parent-level skin (e.g. mixer_desk_bg) should show through
    rather than a solid fallback colour. Returns True if blitted, False if skipped.

    Example:
        # Strip background — only draws if strip_bg.png exists,
        # otherwise mixer_desk_bg.png shows through
        draw_element_if_loaded("strip_bg", sx, base_y - strip_h,
                               STRIP_W * scale, strip_h)
    """
    tex = get_texture(key)
    if tex is not None:
        blit_texture(tex, x, y, w, h, key=key)
        return True
    return False
