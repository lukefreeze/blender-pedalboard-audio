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

from ui.mixer.texture_cache import get_texture, blit_texture


# ---------------------------------------------------------------------------
# Core primitives
# ---------------------------------------------------------------------------

def draw_rect(x: float, y: float, w: float, h: float, color: tuple) -> None:
    """Filled axis-aligned rectangle."""
    if w <= 0 or h <= 0:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts  = [(x, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)]
    batch  = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    gpu.state.line_width_set(line_w)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)


def draw_line(x1: float, y1: float, x2: float, y2: float,
              color: tuple, width: float = 1.0) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch  = batch_for_shader(shader, "LINES", {"pos": [(x1, y1), (x2, y2)]})
    gpu.state.line_width_set(width)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)


def draw_circle(cx: float, cy: float, r: float, color: tuple,
                filled: bool = True, segs: int = 20) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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

    arc_start = -225.0
    arc_total = 270.0
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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
                     color: tuple, label: str) -> None:
    """Simple circle knob used in the mixer strip (gain, EQ, pan)."""
    # This function needs UI_SCALE for the label size — it's passed by
    # mixer_hud.py which has access to the global.
    from ui.mixer.mixer_hud import UI_SCALE as _scale

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
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

    blf.size(0, int(9 * _scale))
    blf.position(0, x - radius * .8, y - (radius + 12 * _scale), 0)
    blf.draw(0, label)


def draw_meter(x: float, y: float, w: float, h: float,
               level: float, peak: float) -> None:
    """VU bar meter: green/yellow/red zones with peak-hold line."""
    draw_rect(x, y, w, h, (0.03, 0.03, 0.03, 1.0))

    if level > 0.0:
        fh = min(level, 1.0) * h
        gt, yt = h * 0.70, h * 0.90

        if fh > 0:
            green_h = min(fh, gt)
            draw_rect(x, y, w, green_h, (0.0, 0.55, 0.15, 1.0))
        if fh > gt:
            yellow_h = min(fh - gt, yt - gt)
            draw_rect(x, y + gt, w, yellow_h, (0.7, 0.6, 0.0, 1.0))
        if fh > yt:
            red_h = fh - yt
            draw_rect(x, y + yt, w, red_h, (0.85, 0.1, 0.1, 1.0))

    if peak > 0.0:
        ph  = min(peak, 1.0) * h
        col = ((0.85, 0.1, 0.1, 1.0) if peak > 0.90 else
               (0.7, 0.6, 0.0, 1.0)  if peak > 0.70 else
               (0.0, 0.85, 0.3, 1.0))
        draw_rect(x, y + ph - 1, w, max(1.5, h * 0.01), col)


def draw_numbox(x: float, y: float, w: float, h: float,
                value: float, highlighted: bool = False) -> None:
    """Fader value display box."""
    bg = (0.18, 0.25, 0.18, 1.0) if highlighted else (0.08, 0.08, 0.08, 1.0)
    draw_rect(x, y, w, h, bg)
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts  = [(x, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)]
    batch  = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind()
    bc = (0.3, 0.5, 0.3, 1.0) if highlighted else (0.2, 0.2, 0.2, 1.0)
    shader.uniform_float("color", bc)
    batch.draw(shader)

    label = f"{value:.3f}"
    fs    = max(1, int(h * 0.65))
    tw    = text_width(label, fs)
    draw_text(label, x + (w - tw) / 2, y + (h - fs) / 2 + 1,
              fs, (0.6, 0.9, 0.6, 1.0) if highlighted else (0.55, 0.55, 0.55, 1.0))


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
        blit_texture(tex, x, y, w, h)
    else:
        fallback_fn(x, y, w, h, *fallback_args, **fallback_kwargs)
