"""
Racks.py — Pedalboard effects rack UI
Imported by Loader.py.

Draws a Reason-style rack of effect units below the mixer fader section.
Each rack unit can be expanded (full UI) or collapsed (single row).
No audio processing here — this file is UI only.
Audio wiring happens in Loader.py at play-start.
"""

import math
import bpy
import gpu
import blf
from gpu_extras.batch import batch_for_shader

# ---------------------------------------------------------------------------
# Shared state — set by Loader.py before calling draw_racks()
# ---------------------------------------------------------------------------
_UI_SCALE  = 1.0
_SCROLL_X  = 0.0
_SCROLL_Y  = 0.0
_IS_PLAYING = False

# GR levels written by the audio engine — rack reads these for GR meters
# gr_levels[rack_idx][channel_idx] = float 0.0-1.0 (0=no reduction, 1=max)
_gr_levels = {}

# LED pulse state — toggled by meter timer for "actively compressing" blink
_led_states = {}   # rack_idx -> bool

# Popup state
_popup_open     = False
_popup_x        = 0.0
_popup_y        = 0.0

# ---------------------------------------------------------------------------
# Rack dimensions (in unscaled pixels, multiplied by UI_SCALE at draw time)
# ---------------------------------------------------------------------------
# Rack width matches the fader section exactly:
# left margin=30px, each fader strip=120px wide, stride=135px
# right edge of 9th fader = 30 + 8*135 + 120 = 1230px
# rack starts at 30px → rack width = 1230 - 30 = 1200px
RACK_WIDTH          = 1200         # matches 9-fader section width exactly
RACK_EXPANDED_H     = 260
RACK_EXPANDED_H_MB  = 340   # taller for multiband compressor
RACK_COLLAPSED_H    = 36
RACK_MARGIN_TOP     = 40           # gap between fader section and racks
RACK_GAP            = 4            # gap between rack units
RACK_RAIL_H         = 32           # top rail height

# Knob layout (left section of expanded rack)
KNOB_SECTION_W      = 360
KNOB_START_X        = 55
KNOB_Y_OFFSET       = 130          # from rack top
KNOB_SPACING        = 68

# Spectrum display
SPEC_X              = 430
SPEC_W              = 480
SPEC_H              = 200
SPEC_Y_OFFSET       = 50           # from rack top
SPEC_BANDS          = 48           # number of frequency bars

# GR meter section
GR_X                = 862
GR_BAR_W            = 12
GR_BAR_SPACING      = 16

# Channel buttons (right section)
CH_BTN_X            = 870
CH_BTN_SIZE         = 26
CH_BTN_SPACING      = 28
CH_BTN_ROWS         = 3

# ---------------------------------------------------------------------------
# Effect type definitions
# ---------------------------------------------------------------------------
EFFECT_TYPES = [
    ("COMP_SINGLE", "Single Band Compressor"),
    ("COMP_MULTI",  "Multiband Compressor"),
    ("EQ",          "Parametric EQ"),
    ("REVERB",      "Reverb"),
    ("NOISE_GATE",  "Noise Gate"),
    ("DELAY",       "Delay"),
]

EFFECT_PARAMS = {
    "COMP_SINGLE": [
        ("THRESHOLD", "Threshold", -40.0,  0.0,  -18.0, "{:.0f}dB"),
        ("RATIO",     "Ratio",       1.0, 20.0,    4.0, "{:.1f}:1"),
        ("ATTACK",    "Attack",      0.1,100.0,   10.0, "{:.0f}ms"),
        ("RELEASE",   "Release",    10.0,1000.0,  80.0, "{:.0f}ms"),
        ("MAKEUP",    "Makeup",      0.0, 24.0,    0.0, "+{:.1f}dB"),
    ],
    # Multiband compressor — 4 bands (Low, Low-Mid, High-Mid, High)
    # p0-p3:   threshold per band  (-40..0 dB)
    # p4-p7:   ratio per band      (1..20)
    # p8-p11:  attack per band     (0.1..100 ms)
    # p12-p15: release per band    (10..1000 ms)
    # p16-p19: gain fader per band (-12..+12 dB, stored 0-1, 0.5=unity)
    "COMP_MULTI": [
        ("THR_LOW",   "Low Thr",   -40.0,   0.0,  -18.0, "{:.0f}dB"),
        ("THR_LMD",   "LMid Thr",  -40.0,   0.0,  -18.0, "{:.0f}dB"),
        ("THR_HMD",   "HMid Thr",  -40.0,   0.0,  -18.0, "{:.0f}dB"),
        ("THR_HIGH",  "High Thr",  -40.0,   0.0,  -18.0, "{:.0f}dB"),
        ("RAT_LOW",   "Low Rat",     1.0,  20.0,    4.0, "{:.1f}:1"),
        ("RAT_LMD",   "LMid Rat",    1.0,  20.0,    4.0, "{:.1f}:1"),
        ("RAT_HMD",   "HMid Rat",    1.0,  20.0,    4.0, "{:.1f}:1"),
        ("RAT_HIGH",  "High Rat",    1.0,  20.0,    4.0, "{:.1f}:1"),
        ("ATK_LOW",   "Low Atk",     0.1, 100.0,   10.0, "{:.0f}ms"),
        ("ATK_LMD",   "LMid Atk",    0.1, 100.0,   10.0, "{:.0f}ms"),
        ("ATK_HMD",   "HMid Atk",    0.1, 100.0,   10.0, "{:.0f}ms"),
        ("ATK_HIGH",  "High Atk",    0.1, 100.0,   10.0, "{:.0f}ms"),
        ("REL_LOW",   "Low Rel",    10.0,1000.0,   80.0, "{:.0f}ms"),
        ("REL_LMD",   "LMid Rel",   10.0,1000.0,   80.0, "{:.0f}ms"),
        ("REL_HMD",   "HMid Rel",   10.0,1000.0,   80.0, "{:.0f}ms"),
        ("REL_HIGH",  "High Rel",   10.0,1000.0,   80.0, "{:.0f}ms"),
    ],
    "EQ": [
        ("LOW",       "Low",        -24.0, 24.0,   0.0, "{:+.0f}dB"),
        ("LOW_MID",   "Low Mid",    -24.0, 24.0,   0.0, "{:+.0f}dB"),
        ("MID",       "Mid",        -24.0, 24.0,   0.0, "{:+.0f}dB"),
        ("HIGH_MID",  "High Mid",   -24.0, 24.0,   0.0, "{:+.0f}dB"),
        ("HIGH",      "High",       -24.0, 24.0,   0.0, "{:+.0f}dB"),
    ],
    "REVERB": [
        ("ROOM",      "Room",         0.0,  1.0,   0.5, "{:.0%}"),
        ("DAMP",      "Damp",         0.0,  1.0,   0.5, "{:.0%}"),
        ("WIDTH",     "Width",        0.0,  1.0,   1.0, "{:.0%}"),
        ("WET",       "Wet",          0.0,  1.0,   0.3, "{:.0%}"),
        ("DRY",       "Dry",          0.0,  1.0,   1.0, "{:.0%}"),
    ],
    "NOISE_GATE": [
        ("THRESHOLD", "Threshold",  -80.0,  0.0, -40.0, "{:.0f}dB"),
        ("ATTACK",    "Attack",       0.1,100.0,   5.0, "{:.0f}ms"),
        ("HOLD",      "Hold",         0.0,500.0,  50.0, "{:.0f}ms"),
        ("RELEASE",   "Release",     10.0,1000.0,100.0, "{:.0f}ms"),
        ("RANGE",     "Range",      -90.0,  0.0, -90.0, "{:.0f}dB"),
    ],
    "DELAY": [
        ("TIME",      "Time",         1.0,2000.0,250.0, "{:.0f}ms"),
        ("FEEDBACK",  "Feedback",     0.0,  1.0,  0.4, "{:.0%}"),
        ("MIX",       "Mix",          0.0,  1.0,  0.3, "{:.0%}"),
        ("SPREAD",    "Spread",       0.0,  1.0,  0.5, "{:.0%}"),
        ("FILTER",    "Filter",       0.0,  1.0,  0.5, "{:.0%}"),
    ],
}

PRESETS = {
    "COMP_SINGLE": ["Vocal Compress", "Drum Bus", "Gentle Glue",
                    "Heavy Squash", "Transparent"],
    "COMP_MULTI":  ["Master Bus", "Drum Glue", "Broadcast",
                    "Gentle Master", "Loud & Proud"],
    "EQ":         ["Presence Boost", "Low Cut", "Air", "Mud Remove",
                   "Telephone"],
    "REVERB":     ["Small Room", "Large Hall", "Plate", "Spring",
                   "Ambience"],
    "NOISE_GATE": ["Tight Gate", "Soft Gate", "Drum Gate",
                   "Vocal Gate", "Natural"],
    "DELAY":      ["Slapback", "Quarter Note", "Dotted 8th",
                   "Ping Pong", "Tape Echo"],
}

# ---------------------------------------------------------------------------
# Property group — one rack instance
# ---------------------------------------------------------------------------
class PB_RackSettings(bpy.types.PropertyGroup):
    effect_type:      bpy.props.StringProperty(default="COMP_SINGLE")
    enabled:          bpy.props.BoolProperty(default=True)
    collapsed:        bpy.props.BoolProperty(default=False)
    preset_idx:       bpy.props.IntProperty(default=0)
    # 9 channel assignment booleans
    ch0: bpy.props.BoolProperty(default=False)
    ch1: bpy.props.BoolProperty(default=False)
    ch2: bpy.props.BoolProperty(default=False)
    ch3: bpy.props.BoolProperty(default=False)
    ch4: bpy.props.BoolProperty(default=False)
    ch5: bpy.props.BoolProperty(default=False)
    ch6: bpy.props.BoolProperty(default=False)
    ch7: bpy.props.BoolProperty(default=False)
    ch8: bpy.props.BoolProperty(default=False)
    # 8 parameter values (covers all effect types)
    p0: bpy.props.FloatProperty(default=0.0)
    p1: bpy.props.FloatProperty(default=0.0)
    p2: bpy.props.FloatProperty(default=0.0)
    p3: bpy.props.FloatProperty(default=0.0)
    p4: bpy.props.FloatProperty(default=0.0)
    p5: bpy.props.FloatProperty(default=0.0)
    p6: bpy.props.FloatProperty(default=0.0)
    p7: bpy.props.FloatProperty(default=0.0)
    # Extended params for multiband compressor (p8-p19)
    # p0-p3:  band thresholds (norm), p4-p7:  band ratios (norm)
    # p8-p11: band attacks (norm),    p12-p15: band releases (norm)
    # p16-p19: band gain faders (norm, default 0.5 = unity)
    p8:  bpy.props.FloatProperty(default=0.0)
    p9:  bpy.props.FloatProperty(default=0.0)
    p10: bpy.props.FloatProperty(default=0.0)
    p11: bpy.props.FloatProperty(default=0.0)
    p12: bpy.props.FloatProperty(default=0.0)
    p13: bpy.props.FloatProperty(default=0.0)
    p14: bpy.props.FloatProperty(default=0.0)
    p15: bpy.props.FloatProperty(default=0.0)
    p16: bpy.props.FloatProperty(default=0.5)
    p17: bpy.props.FloatProperty(default=0.5)
    p18: bpy.props.FloatProperty(default=0.5)
    p19: bpy.props.FloatProperty(default=0.5)


def _rp(rack, idx, default=0.0):
    """Safely get rack param by index, returning default if not yet registered."""
    try:
        return getattr(rack, f'p{idx}', default)
    except Exception:
        return default


def get_rack_channels(rack):
    """Return list of assigned channel indices (0-based) for a rack."""
    return [i for i, attr in enumerate(['ch0','ch1','ch2','ch3','ch4',
                                         'ch5','ch6','ch7','ch8'])
            if getattr(rack, attr, False)]


def get_rack_params(rack):
    """Return list of 8 param values for a rack."""
    return [getattr(rack, f'p{i}', 0.0) for i in range(8)]


def set_rack_param(rack, idx, value):
    setattr(rack, f'p{idx}', value)


def get_param_value(rack, param_idx):
    """Get a parameter value scaled to its actual range."""
    etype  = rack.effect_type
    params = EFFECT_PARAMS.get(etype, [])
    if param_idx >= len(params): return 0.0
    _, _, pmin, pmax, pdefault, _ = params[param_idx]
    raw = getattr(rack, f'p{param_idx}', 0.0)
    # raw is stored as 0-1 normalised, convert to actual range
    return pmin + raw * (pmax - pmin)


def normalise_param(rack, param_idx, actual_value):
    """Convert actual value to 0-1 normalised storage."""
    etype  = rack.effect_type
    params = EFFECT_PARAMS.get(etype, [])
    if param_idx >= len(params): return 0.0
    _, _, pmin, pmax, _, _ = params[param_idx]
    return max(0.0, min(1.0, (actual_value - pmin) / (pmax - pmin)))


def init_rack_defaults(rack):
    """Set parameter values to defaults for the effect type."""
    params = EFFECT_PARAMS.get(rack.effect_type, [])
    for i, (_, _, pmin, pmax, pdefault, _) in enumerate(params):
        if i < 8:
            setattr(rack, f'p{i}', normalise_param(rack, i, pdefault))


# ---------------------------------------------------------------------------
# Drawing helpers (local versions — don't depend on Loader.py globals)
# ---------------------------------------------------------------------------
def _draw_rect(x, y, w, h, color):
    if w <= 0 or h <= 0: return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch  = batch_for_shader(shader, "TRI_STRIP",
                              {"pos": [(x,y),(x+w,y),(x,y+h),(x+w,y+h)]})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_line(x1, y1, x2, y2, color, width=1.0):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch  = batch_for_shader(shader, "LINES",
                              {"pos": [(x1,y1),(x2,y2)]})
    gpu.state.line_width_set(width)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)


def _draw_circle(cx, cy, r, color, filled=True):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    segs   = 20
    if filled:
        verts = [(cx, cy)]
        for i in range(segs+1):
            a = 2*math.pi*i/segs
            verts.append((cx+math.cos(a)*r, cy+math.sin(a)*r))
        batch = batch_for_shader(shader, "TRI_FAN", {"pos": verts})
    else:
        verts = []
        for i in range(segs+1):
            a = 2*math.pi*i/segs
            verts.append((cx+math.cos(a)*r, cy+math.sin(a)*r))
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_text(text, x, y, size, color=(1,1,1,1)):
    blf.size(0, max(1, int(size)))
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, text)


def _text_width(text, size):
    blf.size(0, max(1, int(size)))
    return blf.dimensions(0, text)[0]


def _draw_knob(cx, cy, radius, norm_value, color, label, value_str, scale, label_above=False):
    """Draw a single rotary knob. label_above=True draws labels above the knob."""
    # Outer ring
    _draw_circle(cx, cy, radius, (0.13, 0.13, 0.13, 1.0))
    _draw_circle(cx, cy, radius, (0.33, 0.33, 0.33, 1.0), filled=False)
    # Inner cap
    inner_r = radius * 0.7
    _draw_circle(cx, cy, inner_r, (0.1, 0.1, 0.1, 1.0))
    # Arc background (270 degrees, from ~7 o'clock to ~5 o'clock)
    arc_start = -225.0
    arc_total = 270.0
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    segs = 24
    # Active arc
    active_angle = arc_start + norm_value * arc_total
    arc_pts = []
    a0 = math.radians(arc_start)
    a1 = math.radians(active_angle)
    steps = max(2, int(abs(a1-a0)/(2*math.pi)*segs))
    for i in range(steps+1):
        t = i/steps
        a = a0 + t*(a1-a0)
        arc_pts.append((cx+math.cos(a)*(radius*0.85),
                        cy+math.sin(a)*(radius*0.85)))
    if len(arc_pts) >= 2:
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": arc_pts})
        gpu.state.line_width_set(max(2.0, scale*2))
        shader.bind()
        c = color[:3] if len(color) >= 3 else color
        shader.uniform_float("color", (*c, 1.0))
        batch.draw(shader)
        gpu.state.line_width_set(1.0)
    # Indicator line
    angle = math.radians(arc_start + norm_value * arc_total)
    lx = cx + math.cos(angle) * inner_r * 0.75
    ly = cy + math.sin(angle) * inner_r * 0.75
    _draw_line(cx, cy, lx, ly, (1,1,1,0.9), max(1.5, scale*1.5))
    # Labels always below the knob — scale with zoom
    fs = max(1, int(7*scale))
    tw = _text_width(label, fs)
    _draw_text(label, cx - tw/2, cy - radius - 12*scale, fs,
               (0.5, 0.5, 0.5, 1.0))
    vw = _text_width(value_str, fs)
    _draw_text(value_str, cx - vw/2, cy - radius - 22*scale, fs,
               (0.8, 0.8, 0.8, 1.0))


def _draw_spectrum(rx, ry, rw, rh, rack_idx, scale):
    """Draw the spectrum analyser display."""
    # Background
    _draw_rect(rx, ry, rw, rh, (0.04, 0.04, 0.04, 1.0))
    # Border
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind(); shader.uniform_float("color", (0.15,0.15,0.15,1.0))
    batch.draw(shader)

    # Grid lines horizontal (every 6dB)
    for i in range(1, 6):
        gy = ry + (i/6)*rh
        _draw_rect(rx, gy, rw, max(0.5, scale*0.5), (0.1,0.1,0.1,1.0))

    # Grid lines vertical
    for i in range(1, 8):
        gx = rx + (i/8)*rw
        _draw_rect(gx, ry, max(0.5, scale*0.5), rh, (0.1,0.1,0.1,1.0))

    # Spectrum bars — use GR levels if available, else static preview
    gr = _gr_levels.get(rack_idx, {})
    bar_w = (rw - 4*scale) / SPEC_BANDS
    for b in range(SPEC_BANDS):
        # Static preview pattern (sine curve shaped for realism)
        t        = b / SPEC_BANDS
        h_frac   = (math.sin(t * math.pi) * 0.7 +
                    math.sin(t * math.pi * 3) * 0.2 +
                    0.1)
        h_frac   = max(0.05, min(0.95, h_frac))
        bar_h    = h_frac * rh
        bx       = rx + 2*scale + b * bar_w

        # Colour by frequency zone
        if t < 0.3:
            col = (0.0, 0.7, 0.45, 0.75)   # green — lows
        elif t < 0.6:
            col = (0.0, 0.85, 0.55, 0.85)  # bright green — mids
        elif t < 0.8:
            col = (0.9, 0.65, 0.0, 0.7)    # amber — upper mids
        else:
            col = (0.7, 0.35, 0.0, 0.5)    # orange — highs

        _draw_rect(bx, ry,
                   max(bar_w - scale, 1.0), bar_h, col)

    # GR curve overlay (red dashed line, calculated from compressor settings)
    scene = bpy.context.scene
    racks = getattr(scene, "pb_racks", [])
    if rack_idx < len(racks):
        rack = racks[rack_idx]
        if rack.effect_type in ("COMP_SINGLE", "COMP_MULTI"):
            # Draw proper compressor transfer function curve
            # X axis = input level (left=silence, right=loud)
            # Y axis = output level (bottom=silence, top=loud)
            # Below threshold: 45-degree line (unity gain)
            # Above threshold: shallower slope determined by ratio
            thr_norm   = rack.p0   # 0-1 normalised threshold
            ratio_norm = rack.p1   # 0-1 normalised ratio

            # Convert normalised to actual values
            thr_db  = -40.0 + thr_norm * 40.0    # -40 to 0 dB
            ratio   = 1.0   + ratio_norm * 19.0  # 1:1 to 20:1

            pts = []
            steps = 64
            for s in range(steps + 1):
                # Input level in dB: map 0-1 across display to -60..0 dB
                t        = s / steps
                in_db    = -60.0 + t * 60.0

                # Transfer function
                if in_db < thr_db:
                    out_db = in_db  # no compression below threshold
                else:
                    # Gain reduction above threshold
                    over   = in_db - thr_db
                    out_db = thr_db + over / ratio

                # Map output dB (-60..0) to Y position in display
                out_norm = (out_db + 60.0) / 60.0
                py = ry + out_norm * rh
                px = rx + t * rw
                pts.append((px, py))

            if len(pts) >= 2:
                for i in range(0, len(pts)-1, 2):
                    _draw_line(pts[i][0], pts[i][1],
                               pts[i+1][0], pts[i+1][1],
                               (0.9, 0.2, 0.2, 0.85), max(1.5, scale*1.5))

    # Frequency labels
    freq_labels = [("20", 0.0), ("200", 0.22), ("1k", 0.44),
                   ("4k", 0.63), ("10k", 0.8), ("20k", 0.95)]
    fs = max(1, int(7*scale))
    for label, t in freq_labels:
        lx = rx + t*rw
        _draw_text(label, lx, ry - 12*scale, fs, (0.3,0.3,0.3,1.0))

    # dB scale on left
    db_labels = [("0", 1.0), ("-12", 0.66), ("-24", 0.33), ("-36", 0.0)]
    for label, t in db_labels:
        ly = ry + t*rh - 3*scale
        tw = _text_width(label, fs)
        _draw_text(label, rx - tw - 4*scale, ly, fs, (0.3,0.3,0.3,1.0))

    # Legend
    lfs = max(1, int(7*scale))
    _draw_rect(rx + 4*scale, ry + rh - 12*scale, 12*scale, 2*scale,
               (0.0, 0.8, 0.5, 0.8))
    _draw_text("signal", rx + 18*scale, ry + rh - 14*scale, lfs,
               (0.4, 0.4, 0.4, 1.0))
    _draw_rect(rx + 60*scale, ry + rh - 12*scale, 12*scale, 2*scale,
               (0.9, 0.2, 0.2, 0.7))
    _draw_text("GR curve", rx + 74*scale, ry + rh - 14*scale, lfs,
               (0.4, 0.4, 0.4, 1.0))


def _draw_gr_meters(rx, ry, rh, rack_idx, assigned_channels, scale):
    """Draw one slim GR meter per assigned channel."""
    if not assigned_channels: return
    bar_w   = GR_BAR_W * scale
    spacing = GR_BAR_SPACING * scale
    fs      = max(1, int(7*scale))

    for i, ch_idx in enumerate(assigned_channels[:6]):  # max 6 GR meters
        bx = rx + i * spacing
        # Channel label
        tw = _text_width(str(ch_idx+1), fs)
        _draw_text(str(ch_idx+1), bx + bar_w/2 - tw/2,
                   ry + rh + 4*scale, fs, (0.4,0.4,0.4,1.0))
        # Meter background
        _draw_rect(bx, ry, bar_w, rh, (0.06, 0.06, 0.06, 1.0))
        # GR fill — green from bottom, red peak at top
        gr_val = _gr_levels.get(rack_idx, {}).get(ch_idx, 0.0)
        gr_val = max(0.0, min(1.0, gr_val))
        fill_h = (1.0 - gr_val) * rh
        _draw_rect(bx, ry + rh - fill_h, bar_w, fill_h,
                   (0.0, 0.75, 0.45, 0.85))
        # Peak marker
        if gr_val > 0.05:
            peak_y = ry + rh - fill_h - 2*scale
            _draw_rect(bx, peak_y, bar_w, max(2.0, 2*scale),
                       (1.0, 0.2, 0.2, 0.9))
        # dB scale markers
        for db_t in [0.25, 0.5, 0.75]:
            my = ry + db_t * rh
            _draw_rect(bx + bar_w - 3*scale, my, 3*scale,
                       max(0.5, scale*0.5), (0.2,0.2,0.2,1.0))


def _draw_channel_buttons(rx, ry, rack, scale):
    """Draw channel assignment buttons — one per active VSE channel.
    Channels are laid out in rows of 3, only showing channels that
    actually have strips in the VSE sequence editor.
    """
    btn_s = CH_BTN_SIZE * scale
    gap   = 4 * scale
    fs    = max(1, int(10*scale))

    # Show DEFAULT_CHANNELS minimum buttons, expand if higher channels exist
    try:
        from Loader import DEFAULT_CHANNELS
    except Exception:
        DEFAULT_CHANNELS = 9

    scene   = bpy.context.scene
    highest = 0
    if scene and scene.sequence_editor:
        for s in scene.sequence_editor.sequences_all:
            if s.type == "SOUND" and s.sound:
                highest = max(highest, s.channel - 1)

    num_buttons = max(DEFAULT_CHANNELS, highest + 1)
    active      = list(range(num_buttons))

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    for col_idx, ch_idx in enumerate(active):
        row = col_idx // 3
        col = col_idx % 3
        bx  = rx + col * (btn_s + gap)
        by  = ry - row * (btn_s + gap) - btn_s

        attr     = f'ch{ch_idx}' if ch_idx < 9 else None
        assigned = getattr(rack, attr, False) if attr else False

        if assigned:
            bg = (0.0, 0.18, 0.10, 1.0)
            bc = (0.0, 0.75, 0.45, 1.0)
            tc = (0.0, 0.85, 0.55, 1.0)
        else:
            bg = (0.07, 0.07, 0.07, 1.0)
            bc = (0.2,  0.2,  0.2,  1.0)
            tc = (0.2,  0.2,  0.2,  1.0)

        _draw_rect(bx, by, btn_s, btn_s, bg)
        verts = [(bx,by),(bx+btn_s,by),(bx+btn_s,by+btn_s),(bx,by+btn_s),(bx,by)]
        batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
        shader.bind(); shader.uniform_float("color", bc); batch.draw(shader)

        label = str(ch_idx + 1)
        tw    = _text_width(label, fs)
        _draw_text(label, bx + btn_s/2 - tw/2,
                   by + btn_s/2 - fs/2, fs, tc)


# Band colours for multiband display (matching C6-style)
BAND_COLORS = [
    (0.2, 0.5, 1.0, 0.85),   # Low      — blue
    (0.2, 0.9, 0.4, 0.85),   # Low-Mid  — green
    (1.0, 0.7, 0.1, 0.85),   # High-Mid — amber
    (1.0, 0.3, 0.3, 0.85),   # High     — red
]
BAND_NAMES  = ["Low", "L-Mid", "H-Mid", "High"]
BAND_FREQS  = ["<120Hz", "120-800Hz", "800Hz-5kHz", ">5kHz"]


def _draw_multiband_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """Draw multiband compressor body.
    Layout matches agreed sketch:
    - Top 48%: spectrum display with 4 equal-width band curves
    - Bottom 52%: 4 equal columns, each with gain fader left + 2x2 knobs right
    - No coloured backgrounds, only elements carry band colour
    - Equal margins on left and right of band area
    """
    rail_h       = RACK_RAIL_H * scale
    body_h       = rh - rail_h
    spec_zone_h  = body_h * 0.48
    fader_zone_h = body_h * 0.52

    # Channel buttons take 108px on right — equal margin on left
    ch_btn_w   = 108 * scale
    side_margin = ch_btn_w / 2   # 54px each side

    # Total content width available
    content_w   = rw - ch_btn_w - side_margin
    band_w      = content_w / 4
    content_x   = rx + side_margin / 2

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    # ----------------------------------------------------------------
    # SPECTRUM DISPLAY — top portion, same width as band columns
    # ----------------------------------------------------------------
    spec_x = content_x
    spec_y = ry + fader_zone_h + 4*scale
    spec_w = content_w
    spec_h = spec_zone_h - 8*scale

    _draw_rect(spec_x, spec_y, spec_w, spec_h, (0.04, 0.04, 0.04, 1.0))
    bv = [(spec_x,spec_y),(spec_x+spec_w,spec_y),
          (spec_x+spec_w,spec_y+spec_h),(spec_x,spec_y+spec_h),(spec_x,spec_y)]
    bb = batch_for_shader(shader,"LINE_STRIP",{"pos":bv})
    shader.bind(); shader.uniform_float("color",(0.15,0.15,0.15,1.0)); bb.draw(shader)

    # Grid lines
    for gi in range(1, 5):
        gy = spec_y + gi/5 * spec_h
        _draw_rect(spec_x, gy, spec_w, max(0.5,scale*0.5), (0.09,0.09,0.09,1.0))

    # Band dividers in spectrum — align with column edges
    for b in range(1, 4):
        dx = spec_x + b * band_w
        _draw_rect(dx, spec_y, max(0.5,scale*0.5), spec_h, (0.25,0.25,0.25,1.0))

    # Per-band transfer curves — each curve spans its own band column
    for band in range(4):
        thr_norm   = _rp(rack, band)
        ratio_norm = _rp(rack, band+4)
        thr_db     = -40.0 + thr_norm  * 40.0
        ratio      =   1.0 + ratio_norm * 19.0
        col        = BAND_COLORS[band]
        x0         = spec_x + band * band_w
        x1         = spec_x + (band+1) * band_w

        pts = []
        for s in range(33):
            t      = s / 32
            in_db  = -60.0 + t * 60.0
            out_db = in_db if in_db < thr_db else thr_db + (in_db-thr_db)/ratio
            out_n  = (out_db + 60.0) / 60.0
            pts.append((x0 + t*(x1-x0), spec_y + out_n*spec_h))

        for s in range(0, len(pts)-1, 2):
            _draw_line(pts[s][0], pts[s][1], pts[s+1][0], pts[s+1][1],
                       col, max(1.5, scale*1.5))

        # Freq label centred in band column, below spectrum
        fs_fl = max(1, int(7*scale))
        label = BAND_FREQS[band]
        tw_fl = _text_width(label, fs_fl)
        _draw_text(label,
                   x0 + band_w/2 - tw_fl/2,
                   spec_y - 10*scale,
                   fs_fl, (col[0]*0.6, col[1]*0.6, col[2]*0.6, 1.0))

    # ----------------------------------------------------------------
    # FADER + KNOB ZONE — bottom portion, 4 equal columns
    # Layout per column:
    #   Left ~30px: vertical gain fader + "GAIN" label
    #   Thin divider
    #   Right section: 2x2 knob grid (Thr, Ratio top row; Atk, Rel bottom row)
    #   Bottom: band name + freq range
    # ----------------------------------------------------------------
    fader_area_y = ry + 4*scale
    fader_area_h = fader_zone_h - 8*scale

    label_h  = 26*scale   # band name + freq at bottom
    ctrl_y   = fader_area_y + label_h
    ctrl_h   = fader_area_h - label_h

    # Knob layout: 2 rows, 2 cols within right section
    # Calculated so labels (22px below each knob) never overlap adjacent row:
    #   Bottom row centre: ctrl_y + 4 + 22 + r
    #   Top row centre:    bottom_row + r + 6 + 22 + r
    fader_strip_w = 38*scale
    knob_area_w   = band_w - fader_strip_w - 8*scale
    knob_r        = min(knob_area_w * 0.15, 16*scale)
    knob_r        = max(knob_r, 10*scale)
    knob_col_gap  = knob_area_w / 2
    knob_label_h  = 22*scale   # space needed below each knob for labels
    knob_gap      = 6*scale    # gap between top-row label and bottom-row knob

    for band in range(4):
      try:
        bx  = content_x + band * band_w
        col = BAND_COLORS[band]

        # Band name + freq label at bottom
        fs_bn = max(1, int(9*scale))
        tw_bn = _text_width(BAND_NAMES[band], fs_bn)
        _draw_text(BAND_NAMES[band],
                   bx + band_w/2 - tw_bn/2,
                   fader_area_y + 14*scale, fs_bn, col)
        fs_fr = max(1, int(7*scale))
        tw_fr = _text_width(BAND_FREQS[band], fs_fr)
        _draw_text(BAND_FREQS[band],
                   bx + band_w/2 - tw_fr/2,
                   fader_area_y + 3*scale, fs_fr,
                   (col[0]*0.55, col[1]*0.55, col[2]*0.55, 1.0))

        # --- GAIN FADER (left strip) ---
        fdr_x  = bx + 6*scale
        fdr_w  = 10*scale
        fdr_cx = fdr_x + fdr_w/2 - 2*scale   # rail centre
        gain_norm = _rp(rack, band+16, 0.5)
        # dB label above fader
        gain_db  = (gain_norm - 0.5) * 24.0  # -12 to +12 dB
        fs_g     = max(1, int(7*scale))
        gain_str = f"{gain_db:+.0f}"
        tw_g     = _text_width(gain_str, fs_g)
        _draw_text(gain_str, bx+6*scale + fdr_w/2 - tw_g/2,
                   ctrl_y + ctrl_h - 12*scale, fs_g, (0.75,0.75,0.75,1.0))
        fs_gl    = max(1, int(7*scale))
        _draw_text("GAIN", bx+6*scale, ctrl_y + ctrl_h - 22*scale,
                   fs_gl, (col[0]*0.7,col[1]*0.7,col[2]*0.7,1.0))

        fdr_h  = ctrl_h - 26*scale
        fdr_y  = ctrl_y + 2*scale
        # Rail
        _draw_rect(fdr_cx, fdr_y, 4*scale, fdr_h, (0.06,0.06,0.06,1.0))
        # 0dB mark
        unity_y = fdr_y + 0.5 * fdr_h
        _draw_rect(fdr_cx - 2*scale, unity_y, 8*scale,
                   max(0.5, scale*0.5), (0.3,0.3,0.3,1.0))
        # Handle
        handle_h = max(8*scale, fdr_h * 0.07)
        handle_y = fdr_y + gain_norm * (fdr_h - handle_h)
        _draw_rect(fdr_x, handle_y, fdr_w, handle_h,
                   (col[0]*0.85, col[1]*0.85, col[2]*0.85, 1.0))
        _draw_rect(fdr_x, handle_y + handle_h/2 - max(0.5,scale*0.5),
                   fdr_w, max(1.0, scale),
                   (min(1.0,col[0]*1.4), min(1.0,col[1]*1.4), min(1.0,col[2]*1.4), 1.0))

        # Thin divider after fader strip
        div_x = bx + fader_strip_w
        _draw_rect(div_x, ctrl_y, max(0.5,scale*0.5), ctrl_h, (0.18,0.18,0.18,1.0))

        # --- 2x2 KNOB GRID ---
        knob_base_x = div_x + 4*scale
        # Col centres
        kx0 = knob_base_x + knob_col_gap * 0.5
        kx1 = knob_base_x + knob_col_gap * 1.5
        # Row centres — calculated so labels below top row
        # don't overlap body of bottom row knobs
        ky1 = ctrl_y + 4*scale + knob_label_h + knob_r          # bottom row
        ky0 = ky1 + knob_r + knob_gap + knob_label_h + knob_r   # top row

        # Threshold (p0-p3)
        thr_n   = _rp(rack, band)
        thr_db  = -40.0 + thr_n*40.0
        _draw_knob(kx0, ky0, knob_r, thr_n, col,
                   "Thr", f"{thr_db:.0f}dB", scale)

        # Ratio (p4-p7)
        rat_n  = _rp(rack, band+4)
        ratio  = 1.0 + rat_n*19.0
        _draw_knob(kx1, ky0, knob_r, rat_n, col,
                   "Ratio", f"{ratio:.1f}:1", scale)

        # Attack (p8-p11)
        atk_n  = _rp(rack, band+8)
        atk_ms = 0.1 + atk_n*99.9
        _draw_knob(kx0, ky1, knob_r, atk_n, col,
                   "Atk", f"{atk_ms:.0f}ms", scale)

        # Release (p12-p15)
        rel_n  = _rp(rack, band+12)
        rel_ms = 10.0 + rel_n*990.0
        _draw_knob(kx1, ky1, knob_r, rel_n, col,
                   "Rel", f"{rel_ms:.0f}ms", scale)

        # Column divider (not after last band)
        if band < 3:
            _draw_rect(bx + band_w, fader_area_y,
                       max(0.5,scale*0.5), fader_area_h,
                       (0.18,0.18,0.18,1.0))
      except Exception as e:
        print(f"[MB] band {band} draw error: {e}")


def _draw_rack_expanded(rx, ry, rack, rack_idx, scale, rack_width=None):
    """Draw a fully expanded rack unit."""
    rw  = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh  = (RACK_EXPANDED_H_MB if rack.effect_type == "COMP_MULTI"
           else RACK_EXPANDED_H) * scale

    # --- CHASSIS ---
    _draw_rect(rx, ry, rw, rh, (0.1, 0.1, 0.1, 1.0))
    # Chassis border
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts  = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch  = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.28,0.28,0.28,1.0))
    batch.draw(shader)

    # --- TOP RAIL ---
    rail_h = RACK_RAIL_H * scale
    _draw_rect(rx, ry+rh-rail_h, rw, rail_h, (0.16, 0.16, 0.16, 1.0))
    _draw_rect(rx, ry+rh-rail_h-2*scale, rw, 2*scale, (0.1,0.1,0.1,1.0))

    # Corner screws
    for sx2, sy2 in [(rx+14*scale, ry+rh-16*scale),
                     (rx+rw-14*scale, ry+rh-16*scale),
                     (rx+14*scale, ry+14*scale),
                     (rx+rw-14*scale, ry+14*scale)]:
        _draw_circle(sx2, sy2, 4*scale, (0.07,0.07,0.07,1.0))
        _draw_circle(sx2, sy2, 4*scale, (0.3,0.3,0.3,1.0), filled=False)
        _draw_line(sx2-3*scale, sy2, sx2+3*scale, sy2, (0.3,0.3,0.3,0.8))
        _draw_line(sx2, sy2-3*scale, sx2, sy2+3*scale, (0.3,0.3,0.3,0.8))

    # --- COLLAPSE ARROW (▼) ---
    ax = rx + 26*scale
    ay = ry + rh - 20*scale
    arrow = [(ax-6*scale, ay+5*scale),
             (ax+6*scale, ay+5*scale),
             (ax, ay-5*scale)]
    batch = batch_for_shader(shader,"TRIS",{"pos":arrow})
    shader.uniform_float("color",(0.5,0.5,0.5,1.0)); batch.draw(shader)

    # --- RACK NUMBER BADGE + EFFECT NAME ---
    etype  = rack.effect_type
    enames = dict(EFFECT_TYPES)
    ename  = enames.get(etype, etype)
    fs_name = max(1, int(11*scale))

    # Rack number — glowing blue, no box, clearly a label not a button
    badge_label = str(rack_idx + 1)
    badge_fs    = max(1, int(13*scale))
    badge_x     = rx + 42*scale
    badge_y     = ry + rh - 22*scale
    _draw_text(badge_label, badge_x, badge_y,
               badge_fs, (0.35, 0.7, 1.0, 1.0))
    badge_w     = _text_width(badge_label, badge_fs) + 6*scale

    # Effect name — shifted right to clear the number
    _draw_text(ename.upper(),
               rx + 42*scale + badge_w,
               ry+rh-22*scale, fs_name, (0.75,0.75,0.75,1.0))

    # --- PRESET SELECTOR ---
    presets   = PRESETS.get(etype, ["Default"])
    p_idx     = rack.preset_idx % max(1, len(presets))
    p_name    = presets[p_idx]
    p_box_x   = rx + 280*scale
    p_box_w   = 160*scale
    p_box_y   = ry + rh - 26*scale
    p_box_h   = 16*scale

    # Left arrow
    lax = p_box_x - 14*scale
    lay = ry + rh - 18*scale
    la  = [(lax, lay), (lax+10*scale, lay+6*scale), (lax+10*scale, lay-6*scale)]
    batch = batch_for_shader(shader,"TRIS",{"pos":la})
    shader.uniform_float("color",(0.4,0.4,0.4,1.0)); batch.draw(shader)

    _draw_rect(p_box_x, p_box_y, p_box_w, p_box_h, (0.07,0.07,0.07,1.0))
    fs_p = max(1, int(9*scale))
    tw   = _text_width(p_name, fs_p)
    _draw_text(p_name, p_box_x + p_box_w/2 - tw/2,
               p_box_y + p_box_h/2 - fs_p/2 + 1, fs_p, (0.7,0.7,0.7,1.0))

    # Right arrow
    rax = p_box_x + p_box_w + 4*scale
    ray = ry + rh - 18*scale
    ra  = [(rax+10*scale, ray), (rax, ray+6*scale), (rax, ray-6*scale)]
    batch = batch_for_shader(shader,"TRIS",{"pos":ra})
    shader.uniform_float("color",(0.4,0.4,0.4,1.0)); batch.draw(shader)

    # --- DELETE BUTTON (X) ---
    del_x = rx + rw - 26*scale
    del_y = ry + rh - 27*scale
    del_w = 18*scale
    del_h = 16*scale
    _draw_rect(del_x, del_y, del_w, del_h, (0.18, 0.04, 0.04, 1.0))
    shader2 = gpu.shader.from_builtin("UNIFORM_COLOR")
    dv = [(del_x,del_y),(del_x+del_w,del_y),
          (del_x+del_w,del_y+del_h),(del_x,del_y+del_h),(del_x,del_y)]
    db = batch_for_shader(shader2,"LINE_STRIP",{"pos":dv})
    shader2.bind(); shader2.uniform_float("color",(0.6,0.1,0.1,1.0)); db.draw(shader2)
    fs_del = max(1, int(9*scale))
    tw_del = _text_width("X", fs_del)
    _draw_text("X", del_x+del_w/2-tw_del/2, del_y+del_h/2-fs_del/2+1,
               fs_del, (0.8, 0.15, 0.15, 1.0))

    # --- ON/BYPASS BUTTON ---
    on_x = rx + rw - 68*scale
    on_y = ry + rh - 27*scale
    on_w = 40*scale
    on_h = 16*scale
    if rack.enabled:
        _draw_rect(on_x, on_y, on_w, on_h, (0.0, 0.13, 0.0, 1.0))
        on_col = (0.0, 0.65, 0.3, 1.0)
        on_txt = "ON"
    else:
        _draw_rect(on_x, on_y, on_w, on_h, (0.13, 0.0, 0.0, 1.0))
        on_col = (0.65, 0.0, 0.0, 1.0)
        on_txt = "OFF"
    verts = [(on_x,on_y),(on_x+on_w,on_y),
             (on_x+on_w,on_y+on_h),(on_x,on_y+on_h),(on_x,on_y)]
    batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.uniform_float("color", on_col); batch.draw(shader)
    fs_on = max(1, int(9*scale))
    tw    = _text_width(on_txt, fs_on)
    _draw_text(on_txt, on_x+on_w/2-tw/2, on_y+on_h/2-fs_on/2+1,
               fs_on, on_col)

    # --- BODY CONTENT — branches by effect type ---
    body_h = rh - RACK_RAIL_H * scale
    spec_h = min(SPEC_H * scale, body_h - 50*scale)

    if etype == "COMP_MULTI":
        _draw_multiband_body(rx, ry, rw, rh, rack, rack_idx, scale)
    else:
        # Standard layout: knobs + spectrum + GR meters
        params = EFFECT_PARAMS.get(etype, [])
        knob_r = 20 * scale
        ky     = ry + rh - RACK_RAIL_H*scale - KNOB_Y_OFFSET*scale
        for pi, (pkey, plabel, pmin, pmax, pdef, pfmt) in enumerate(params[:5]):
            kx     = rx + (KNOB_START_X + pi*KNOB_SPACING) * scale
            norm   = getattr(rack, f'p{pi}', 0.0)
            actual = pmin + norm*(pmax-pmin)
            try:    val_str = pfmt.format(actual)
            except: val_str = f"{actual:.1f}"
            _draw_knob(kx, ky, knob_r, norm,
                       (0.0, 0.65, 0.4), plabel, val_str, scale)

        div_x = rx + KNOB_SECTION_W * scale
        _draw_rect(div_x, ry+4*scale, max(1.0, scale),
                   rh-RACK_RAIL_H*scale-8*scale, (0.2, 0.2, 0.2, 1.0))

        spec_x = rx + SPEC_X * scale
        spec_y = ry + (body_h - spec_h) / 2 - 5*scale
        spec_w = SPEC_W * scale
        _draw_spectrum(spec_x, spec_y, spec_w, spec_h, rack_idx, scale)

        assigned = get_rack_channels(rack)
        gr_x     = spec_x + spec_w + 16*scale
        _draw_gr_meters(gr_x, spec_y, spec_h, rack_idx, assigned, scale)

    # Channel buttons always on right regardless of effect type
    ch_right_x = rx + rw - 100*scale
    ch_top_y   = ry + rh - RACK_RAIL_H*scale - 20*scale
    _draw_channel_buttons(ch_right_x, ch_top_y, rack, scale)
    fs_ch = max(1, int(8*scale))
    _draw_text("CHANNELS", ch_right_x + 10*scale,
               ch_top_y + 8*scale, fs_ch, (0.35,0.35,0.35,1.0))


def _draw_rack_collapsed(rx, ry, rack, rack_idx, scale, rack_width=None):
    """Draw a collapsed rack unit — single row."""
    rw = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh = RACK_COLLAPSED_H * scale

    # Chassis
    _draw_rect(rx, ry, rw, rh, (0.1, 0.1, 0.1, 1.0))
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts  = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch  = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.25,0.25,0.25,1.0))
    batch.draw(shader)

    # Corner screws
    for sx2, sy2 in [(rx+12*scale, ry+rh/2),
                     (rx+rw-12*scale, ry+rh/2)]:
        _draw_circle(sx2, sy2, 3*scale, (0.07,0.07,0.07,1.0))
        _draw_circle(sx2, sy2, 3*scale, (0.28,0.28,0.28,1.0), filled=False)
        _draw_line(sx2-2*scale, sy2, sx2+2*scale, sy2, (0.28,0.28,0.28,0.8))
        _draw_line(sx2, sy2-2*scale, sx2, sy2+2*scale, (0.28,0.28,0.28,0.8))

    # Expand arrow (►)
    ax = rx + 26*scale
    ay = ry + rh/2
    arrow = [(ax-5*scale, ay+6*scale),
             (ax-5*scale, ay-6*scale),
             (ax+5*scale, ay)]
    batch = batch_for_shader(shader,"TRIS",{"pos":arrow})
    shader.uniform_float("color",(0.45,0.45,0.45,1.0)); batch.draw(shader)

    # Effect name + rack number badge
    etype  = rack.effect_type
    enames = dict(EFFECT_TYPES)
    ename  = enames.get(etype, etype)
    fs     = max(1, int(11*scale))

    # Rack number — glowing blue, no box
    badge_label = str(rack_idx + 1)
    badge_fs    = max(1, int(13*scale))
    badge_x     = rx + 40*scale
    badge_y     = ry + rh/2 - badge_fs/2
    _draw_text(badge_label, badge_x, badge_y,
               badge_fs, (0.35, 0.7, 1.0, 1.0))
    badge_w     = _text_width(badge_label, badge_fs) + 6*scale

    # Effect name shifted right of number
    _draw_text(ename.upper(),
               rx + 40*scale + badge_w,
               ry + rh/2 - fs/2, fs, (0.6,0.6,0.6,1.0))

    # Assigned channel buttons (small, only assigned ones)
    assigned = get_rack_channels(rack)
    btn_x    = rx + 250*scale
    btn_y    = ry + rh/2 - 8*scale
    btn_w    = 20*scale
    btn_h    = 16*scale
    btn_gap  = 4*scale
    fs_b     = max(1, int(9*scale))

    for i, ch_idx in enumerate(assigned):
        bx = btn_x + i*(btn_w+btn_gap)
        _draw_rect(bx, btn_y, btn_w, btn_h, (0.0, 0.15, 0.08, 1.0))
        verts = [(bx,btn_y),(bx+btn_w,btn_y),
                 (bx+btn_w,btn_y+btn_h),(bx,btn_y+btn_h),(bx,btn_y)]
        batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
        shader.uniform_float("color",(0.0,0.65,0.38,1.0)); batch.draw(shader)
        tw = _text_width(str(ch_idx+1), fs_b)
        _draw_text(str(ch_idx+1),
                   bx+btn_w/2-tw/2, btn_y+btn_h/2-fs_b/2+1,
                   fs_b, (0.0,0.8,0.5,1.0))

        # Pulsing LED next to channel button
        led_x = bx + btn_w + 3*scale
        led_y = btn_y + btn_h/2
        is_lit = _led_states.get((rack_idx, ch_idx), False)
        if is_lit:
            led_col = (0.0, 1.0, 0.55, 1.0)
        else:
            led_col = (0.0, 0.25, 0.14, 1.0)
        _draw_circle(led_x, led_y, 4*scale, led_col)

    # Preset name (centre)
    presets = PRESETS.get(etype, ["Default"])
    p_idx   = rack.preset_idx % max(1, len(presets))
    p_name  = presets[p_idx]
    fs_p    = max(1, int(9*scale))
    _draw_text(p_name, rx + rw/2 - _text_width(p_name, fs_p)/2,
               ry + rh/2 - fs_p/2, fs_p, (0.3,0.3,0.3,1.0))

    # Delete button (collapsed)
    cdel_x = rx + rw - 28*scale
    cdel_y = ry + rh/2 - 7*scale
    cdel_w = 18*scale
    cdel_h = 14*scale
    _draw_rect(cdel_x, cdel_y, cdel_w, cdel_h, (0.18,0.04,0.04,1.0))
    shader_d = gpu.shader.from_builtin("UNIFORM_COLOR")
    dv2 = [(cdel_x,cdel_y),(cdel_x+cdel_w,cdel_y),
           (cdel_x+cdel_w,cdel_y+cdel_h),(cdel_x,cdel_y+cdel_h),(cdel_x,cdel_y)]
    db2 = batch_for_shader(shader_d,"LINE_STRIP",{"pos":dv2})
    shader_d.bind(); shader_d.uniform_float("color",(0.5,0.1,0.1,1.0)); db2.draw(shader_d)
    fs_d2 = max(1, int(8*scale))
    tw_d2 = _text_width("X", fs_d2)
    _draw_text("X", cdel_x+cdel_w/2-tw_d2/2, cdel_y+cdel_h/2-fs_d2/2+1,
               fs_d2, (0.7,0.1,0.1,1.0))

    # ON/OFF indicator
    on_x  = rx + rw - 50*scale
    on_y  = ry + rh/2 - 7*scale
    on_w  = 28*scale
    on_h  = 14*scale
    if rack.enabled:
        _draw_rect(on_x, on_y, on_w, on_h, (0.0, 0.1, 0.0, 1.0))
        _draw_text("ON", on_x+4*scale, on_y+3*scale,
                   max(1, int(8*scale)), (0.0,0.6,0.3,1.0))
    else:
        _draw_rect(on_x, on_y, on_w, on_h, (0.1, 0.0, 0.0, 1.0))
        _draw_text("OFF", on_x+2*scale, on_y+3*scale,
                   max(1, int(8*scale)), (0.5,0.0,0.0,1.0))


def _draw_add_rack_button(rx, ry, scale, rack_width=None):
    """Draw the + ADD RACK EFFECT button."""
    rw  = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh  = 28*scale
    fs  = max(1, int(10*scale))

    _draw_rect(rx, ry, rw, rh, (0.07, 0.07, 0.07, 1.0))

    # Dashed border
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    segs   = 60
    verts  = []
    for i in range(segs+1):
        t   = i/segs
        if i % 2 == 0:
            px = rx + t*rw
            py = ry
            verts.append((px, py))
        else:
            if verts:
                px2 = rx + t*rw
                verts.append((px2, ry))
    # Simple rect border instead of true dashed (GPU line dashing is complex)
    border_verts = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch = batch_for_shader(shader,"LINE_STRIP",{"pos":border_verts})
    shader.bind()
    shader.uniform_float("color",(0.2,0.2,0.2,1.0))
    batch.draw(shader)

    label = "+  ADD RACK EFFECT"
    tw    = _text_width(label, fs)
    _draw_text(label, rx + rw/2 - tw/2,
               ry + rh/2 - fs/2, fs, (0.3,0.3,0.3,1.0))


def _draw_add_popup(px, py, scale):
    """Draw the effect type selector popup."""
    popup_w  = 200*scale
    title_h  = 24*scale
    popup_h  = (len(EFFECT_TYPES) * 28 + 10) * scale + title_h
    popup_x  = px
    popup_y  = py

    # Shadow
    _draw_rect(popup_x+3*scale, popup_y-3*scale,
               popup_w, popup_h, (0.0,0.0,0.0,0.5))
    # Background
    _draw_rect(popup_x, popup_y, popup_w, popup_h, (0.15,0.15,0.15,1.0))
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts  = [(popup_x, popup_y),
              (popup_x+popup_w, popup_y),
              (popup_x+popup_w, popup_y+popup_h),
              (popup_x, popup_y+popup_h),
              (popup_x, popup_y)]
    batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.35,0.35,0.35,1.0))
    batch.draw(shader)

    # Title bar at top of popup
    _draw_rect(popup_x, popup_y+popup_h-title_h,
               popup_w, title_h, (0.18,0.18,0.18,1.0))
    fs_t = max(1, int(9*scale))
    _draw_text("SELECT EFFECT", popup_x+8*scale,
               popup_y+popup_h-title_h+6*scale, fs_t, (0.6,0.6,0.6,1.0))
    _draw_rect(popup_x, popup_y+popup_h-title_h,
               popup_w, max(0.5,scale*0.5), (0.3,0.3,0.3,1.0))

    # Effect type buttons — start below title bar
    fs_b = max(1, int(10*scale))
    for i, (etype, ename) in enumerate(EFFECT_TYPES):
        by = popup_y + popup_h - title_h - (i+1)*28*scale - 4*scale
        bh = 24*scale
        # Hover highlight — simple alternating for now
        bg = (0.18,0.18,0.18,1.0) if i % 2 == 0 else (0.14,0.14,0.14,1.0)
        _draw_rect(popup_x+2*scale, by, popup_w-4*scale, bh, bg)
        _draw_text(ename, popup_x+12*scale, by+bh/2-fs_b/2,
                   fs_b, (0.75,0.75,0.75,1.0))


# ---------------------------------------------------------------------------
# Main draw entry point — called by Loader.py draw_callback_px
# ---------------------------------------------------------------------------
def draw_racks(region_width, region_height, scroll_x, scroll_y, ui_scale):
    """
    Draw all rack units below the fader section.
    Called from Loader.py draw_callback_px after drawing faders.
    """
    global _UI_SCALE, _SCROLL_X, _SCROLL_Y
    _UI_SCALE = ui_scale
    _SCROLL_X = scroll_x
    _SCROLL_Y = scroll_y

    scene = bpy.context.scene
    if not scene: return

    racks = getattr(scene, "pb_racks", [])

    # Calculate Y position: below the fader+numbox section
    # base_y in Loader = height - 150*scale - scroll_y
    # fader bottom = base_y - FADER_TRACK_BOTTOM*scale
    # numbox bottom = fader_bottom - numbox_h - margin
    from Loader import FADER_TRACK_BOTTOM, NUMBOX_H, NUMBOX_Y_OFFSET
    base_y       = region_height - 150*ui_scale - scroll_y
    # fader bottom moves down by send section height
    try:
        from Loader import _send_section_height, SEND_MIN_SLOTS
        n_racks_s = len(getattr(scene, "pb_racks", []))
        send_h    = _send_section_height(n_racks_s, ui_scale)
    except Exception:
        send_h = 0.0
    fader_bot_y  = base_y - FADER_TRACK_BOTTOM*ui_scale - send_h
    rack_top_y   = fader_bot_y - (NUMBOX_H + RACK_MARGIN_TOP)*ui_scale

    # Draw racks from top downward
    cur_y = rack_top_y
    rack_x = 30*ui_scale + scroll_x

    # Dynamic rack width — matches however many faders are currently shown
    # Each fader is 120px wide with 135px stride, starting at 30px
    scene2       = bpy.context.scene
    num_tracks   = len(getattr(scene2, "pb_sync_tracks", [])) if scene2 else 9
    num_tracks   = max(9, num_tracks)
    dynamic_rack_w = num_tracks * 135 - 15  # = right edge of last fader - left margin

    for i, rack in enumerate(racks):
        if rack.collapsed:
            rh = RACK_COLLAPSED_H * ui_scale
        elif rack.effect_type == "COMP_MULTI":
            rh = RACK_EXPANDED_H_MB * ui_scale
        else:
            rh = RACK_EXPANDED_H * ui_scale

        # Cull if completely off screen vertically
        if cur_y - rh > region_height or cur_y < -rh:
            cur_y -= rh + RACK_GAP*ui_scale
            continue

        try:
            if rack.collapsed:
                _draw_rack_collapsed(rack_x, cur_y - rh, rack, i,
                                     ui_scale, dynamic_rack_w)
            else:
                _draw_rack_expanded(rack_x, cur_y - rh, rack, i,
                                    ui_scale, dynamic_rack_w)
        except Exception as e:
            print(f"[RACKS] draw error rack {i}: {e}")

        cur_y -= rh + RACK_GAP*ui_scale

    # Add rack button
    _draw_add_rack_button(rack_x, cur_y - 28*ui_scale, ui_scale,
                          dynamic_rack_w)

    # Popup (drawn on top of everything)
    if _popup_open:
        _draw_add_popup(_popup_x, _popup_y, ui_scale)


# ---------------------------------------------------------------------------
# Hit testing — returns (rack_idx, zone, sub_idx) or None
# Called from Loader.py modal operator LEFTMOUSE handler
# ---------------------------------------------------------------------------
def rack_knob_hit_test(rx, ry, region_height, scroll_x, scroll_y, ui_scale):
    """Check if a rack knob was clicked.
    Returns (rack_idx, param_idx) or None.
    Only checks expanded racks — collapsed racks have no knobs.
    """
    import math
    try:
        from Loader import FADER_TRACK_BOTTOM, NUMBOX_H, _send_section_height
        scene = bpy.context.scene
        if not scene: return None
        n_racks_s = len(getattr(scene, "pb_racks", []))
        send_h    = _send_section_height(n_racks_s, ui_scale)
    except Exception:
        return None

    scene = bpy.context.scene
    if not scene: return None
    racks = getattr(scene, "pb_racks", [])

    base_y      = region_height - 150*ui_scale - scroll_y
    fader_bot_y = base_y - FADER_TRACK_BOTTOM*ui_scale - send_h
    rack_top_y  = fader_bot_y - (NUMBOX_H + RACK_MARGIN_TOP)*ui_scale

    cur_y  = rack_top_y
    rack_x = 30*ui_scale + scroll_x

    # Dynamic rack width
    nt  = max(9, len(getattr(scene, "pb_sync_tracks", [])))
    rw  = (nt * 135 - 15) * ui_scale

    knob_r = 20 * ui_scale

    for i, rack in enumerate(racks):
        if rack.collapsed:
            rh = RACK_COLLAPSED_H * ui_scale
            cur_y -= rh + RACK_GAP * ui_scale
            continue

        rh = (RACK_EXPANDED_H_MB if rack.effect_type == "COMP_MULTI"
              else RACK_EXPANDED_H) * ui_scale
        rack_y = cur_y - rh

        if rack.effect_type == "COMP_MULTI":
            # Multiband: hit test gain faders and 2x2 knob grid
            body_h       = rh - RACK_RAIL_H*ui_scale
            fader_zone_h = body_h * 0.52
            ch_btn_w     = 108*ui_scale
            side_margin  = ch_btn_w / 2
            content_w    = rw - ch_btn_w - side_margin
            band_w       = content_w / 4
            content_x    = rack_x + side_margin / 2
            fader_area_y = rack_y + 4*ui_scale
            fader_area_h = fader_zone_h - 8*ui_scale
            label_h      = 26*ui_scale
            ctrl_y       = fader_area_y + label_h
            ctrl_h       = fader_area_h - label_h
            fader_strip_w = 38*ui_scale
            knob_area_w  = band_w - fader_strip_w - 8*ui_scale
            knob_r       = max(min(knob_area_w*0.22, ctrl_h*0.22), 12*ui_scale)
            knob_col_gap = knob_area_w / 2
            knob_row_gap = ctrl_h / 2

            for band in range(4):
                bx = content_x + band * band_w

                # Gain fader (p16-p19)
                fdr_x = bx + 6*ui_scale
                fdr_w = 10*ui_scale
                fdr_h = ctrl_h - 26*ui_scale
                fdr_y = ctrl_y + 2*ui_scale
                if fdr_x <= rx <= fdr_x+fdr_w and fdr_y <= ry <= fdr_y+fdr_h:
                    return (i, band + 16)

                # 2x2 knob grid
                knob_base_x = bx + fader_strip_w + 4*ui_scale
                kx0 = knob_base_x + knob_col_gap * 0.5
                kx1 = knob_base_x + knob_col_gap * 1.5
                _knob_label_h = 22*ui_scale
                _knob_gap     = 6*ui_scale
                ky1 = ctrl_y + 4*ui_scale + _knob_label_h + knob_r
                ky0 = ky1 + knob_r + _knob_gap + _knob_label_h + knob_r

                if math.dist((rx,ry),(kx0,ky0)) < knob_r+4*ui_scale:
                    return (i, band)       # threshold
                if math.dist((rx,ry),(kx1,ky0)) < knob_r+4*ui_scale:
                    return (i, band+4)     # ratio
                if math.dist((rx,ry),(kx0,ky1)) < knob_r+4*ui_scale:
                    return (i, band+8)     # attack
                if math.dist((rx,ry),(kx1,ky1)) < knob_r+4*ui_scale:
                    return (i, band+12)    # release
        else:
            # Standard knobs
            ky = rack_y + rh - RACK_RAIL_H*ui_scale - KNOB_Y_OFFSET*ui_scale
            params = EFFECT_PARAMS.get(rack.effect_type, [])
            for pi in range(min(5, len(params))):
                kx = rack_x + (KNOB_START_X + pi*KNOB_SPACING) * ui_scale
                if math.dist((rx, ry), (kx, ky)) < knob_r:
                    return (i, pi)

        cur_y -= rh + RACK_GAP * ui_scale

    return None


def hit_test(rx, ry, region_height, scroll_x, scroll_y, ui_scale):
    """
    Return what was clicked.
    Returns dict with 'zone' key, or None if nothing hit.
    Zones: 'collapse', 'preset_left', 'preset_right', 'on_off',
           'channel_btn', 'add_rack', 'popup_effect'
    """
    global _popup_open

    try:
        from Loader import FADER_TRACK_BOTTOM, NUMBOX_H
    except Exception:
        return None

    scene = bpy.context.scene
    if not scene: return None
    racks = getattr(scene, "pb_racks", [])

    base_y       = region_height - 150*ui_scale - scroll_y
    try:
        from Loader import _send_section_height
        n_racks_s2 = len(getattr(scene, "pb_racks", [])) if scene else 0
        send_h2    = _send_section_height(n_racks_s2, ui_scale)
    except Exception:
        send_h2 = 0.0
    fader_bot_y  = base_y - FADER_TRACK_BOTTOM*ui_scale - send_h2
    rack_top_y   = fader_bot_y - (NUMBOX_H + RACK_MARGIN_TOP)*ui_scale

    cur_y  = rack_top_y
    rack_x = 30*ui_scale + scroll_x
    # Dynamic width — same calculation as draw_racks
    scene3     = bpy.context.scene
    nt         = len(getattr(scene3, "pb_sync_tracks", [])) if scene3 else 9
    nt         = max(9, nt)
    rw         = (nt * 135 - 15) * ui_scale

    # Check popup first (drawn on top)
    if _popup_open:
        pw  = 200*ui_scale
        ph  = (len(EFFECT_TYPES)*28 + 10)*ui_scale + 24*ui_scale
        if _popup_x <= rx <= _popup_x+pw and _popup_y <= ry <= _popup_y+ph:
            # Which effect was clicked? Mirror draw calculation
            title_h = 24*ui_scale
            for i, (etype, _) in enumerate(EFFECT_TYPES):
                by = _popup_y + ph - title_h - (i+1)*28*ui_scale - 4*ui_scale
                bh = 24*ui_scale
                if by <= ry <= by+bh:
                    return {'zone': 'popup_effect', 'effect_type': etype}
            return {'zone': 'popup_dismiss'}
        else:
            return {'zone': 'popup_dismiss'}

    for i, rack in enumerate(racks):
        if rack.collapsed:
            rh = RACK_COLLAPSED_H * ui_scale
        elif rack.effect_type == "COMP_MULTI":
            rh = RACK_EXPANDED_H_MB * ui_scale
        else:
            rh = RACK_EXPANDED_H * ui_scale

        rack_y = cur_y - rh

        if rack_x <= rx <= rack_x+rw and rack_y <= ry <= rack_y+rh:
            # Hit in this rack — determine zone
            rail_top = rack_y + rh - RACK_RAIL_H*ui_scale

            # Collapse/expand arrow (top-left of rail)
            if rail_top <= ry <= rack_y+rh and rx <= rack_x+38*ui_scale:
                return {'zone': 'collapse', 'rack_idx': i}

            # Delete button
            del_x = rack_x + rw - 26*ui_scale
            del_y = rack_y + rh - 27*ui_scale
            if del_x <= rx <= del_x+18*ui_scale and del_y <= ry <= del_y+16*ui_scale:
                return {'zone': 'delete_rack', 'rack_idx': i}

            # ON/OFF button (expanded: shifted left of delete)
            on_x = rack_x + rw - 68*ui_scale
            on_y = rack_y + rh - 27*ui_scale
            if on_x <= rx <= on_x+40*ui_scale and on_y <= ry <= on_y+16*ui_scale:
                return {'zone': 'on_off', 'rack_idx': i}

            # Delete button (collapsed)
            if rack.collapsed:
                cdel_x = rack_x + rw - 28*ui_scale
                cdel_y = rack_y + rh/2 - 7*ui_scale
                if cdel_x <= rx <= cdel_x+18*ui_scale and cdel_y <= ry <= cdel_y+14*ui_scale:
                    return {'zone': 'delete_rack', 'rack_idx': i}

            # Preset arrows
            p_box_x = rack_x + 280*ui_scale
            p_box_w = 160*ui_scale
            if rack_y+rh-30*ui_scale <= ry <= rack_y+rh-12*ui_scale:
                if p_box_x-18*ui_scale <= rx <= p_box_x:
                    return {'zone': 'preset_left', 'rack_idx': i}
                if p_box_x+p_box_w <= rx <= p_box_x+p_box_w+18*ui_scale:
                    return {'zone': 'preset_right', 'rack_idx': i}

            # Channel buttons (expanded only)
            if not rack.collapsed:
                ch_right_x = rack_x + rw - 100*ui_scale
                ch_top_y   = rack_y + rh - RACK_RAIL_H*ui_scale - 20*ui_scale
                btn_s      = CH_BTN_SIZE * ui_scale
                btn_gap    = 4*ui_scale
                # Use active channels only — same layout as _draw_channel_buttons
                try:
                    from Loader import DEFAULT_CHANNELS as _DC
                except Exception:
                    _DC = 9
                scene2  = bpy.context.scene
                highest2 = 0
                if scene2 and scene2.sequence_editor:
                    for _s in scene2.sequence_editor.sequences_all:
                        if _s.type == "SOUND" and _s.sound:
                            highest2 = max(highest2, _s.channel - 1)
                active_chs = list(range(max(_DC, highest2 + 1)))
                for col_idx, ch_idx in enumerate(active_chs):
                    row = col_idx // 3
                    col = col_idx % 3
                    bx  = ch_right_x + col*(btn_s+btn_gap)
                    by  = ch_top_y - row*(btn_s+btn_gap) - btn_s
                    if bx <= rx <= bx+btn_s and by <= ry <= by+btn_s:
                        return {'zone': 'channel_btn',
                                'rack_idx': i, 'ch_idx': ch_idx}

            # Collapsed channel buttons
            if rack.collapsed:
                assigned = get_rack_channels(rack)
                btn_x    = rack_x + 250*ui_scale
                btn_y    = rack_y + rh/2 - 8*ui_scale
                btn_w    = 20*ui_scale
                btn_h    = 16*ui_scale
                btn_gap  = 4*ui_scale
                for j, ch_idx in enumerate(assigned):
                    bx = btn_x + j*(btn_w+btn_gap)
                    if bx <= rx <= bx+btn_w and btn_y <= ry <= btn_y+btn_h:
                        return {'zone': 'channel_btn',
                                'rack_idx': i, 'ch_idx': ch_idx}

            return {'zone': 'rack_body', 'rack_idx': i}

        cur_y -= rh + RACK_GAP*ui_scale

    # Add rack button
    add_y = cur_y - 28*ui_scale
    if rack_x <= rx <= rack_x+rw and add_y <= ry <= add_y+28*ui_scale:
        return {'zone': 'add_rack', 'click_x': rx, 'click_y': ry}

    return None


# ---------------------------------------------------------------------------
# Handle a click result from hit_test
# ---------------------------------------------------------------------------
def handle_click(hit, context):
    """Process a hit_test result. Returns True if redraw needed."""
    global _popup_open, _popup_x, _popup_y

    if hit is None:
        if _popup_open:
            _popup_open = False
            return True
        return False

    zone = hit.get('zone')

    if zone == 'popup_dismiss':
        _popup_open = False
        return True

    if zone == 'popup_effect':
        _popup_open = False
        etype = hit['effect_type']
        rack  = context.scene.pb_racks.add()
        rack.effect_type = etype
        rack.collapsed   = False
        init_rack_defaults(rack)
        print(f"[RACKS] added {etype} rack")
        return True

    if zone == 'add_rack':
        # Open popup — store click position and draw BELOW it
        _popup_open = True
        click_y = hit.get('click_y', 200.0)
        click_x = hit.get('click_x', 200.0)
        popup_h = (len(EFFECT_TYPES) * 28 + 10) * _UI_SCALE + 24 * _UI_SCALE
        # Draw below the click point, centred horizontally on click
        _popup_x = click_x - 100*_UI_SCALE
        _popup_y = click_y - popup_h - 4*_UI_SCALE
        return True

    if zone == 'collapse':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            racks[i].collapsed = not racks[i].collapsed
            print(f"[RACKS] rack {i} collapsed={racks[i].collapsed}")
        return True

    if zone == 'delete_rack':
        i     = hit['rack_idx']
        racks = context.scene.pb_racks
        if i < len(racks):
            racks.remove(i)
            print(f"[RACKS] deleted rack {i}")
        return True

    if zone == 'on_off':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            racks[i].enabled = not racks[i].enabled
        return True

    if zone == 'preset_left':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            rack    = racks[i]
            presets = PRESETS.get(rack.effect_type, ["Default"])
            rack.preset_idx = (rack.preset_idx - 1) % len(presets)
        return True

    if zone == 'preset_right':
        i     = hit['rack_idx']
        racks = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            rack    = racks[i]
            presets = PRESETS.get(rack.effect_type, ["Default"])
            rack.preset_idx = (rack.preset_idx + 1) % len(presets)
        return True

    if zone == 'channel_btn':
        i      = hit['rack_idx']
        ch_idx = hit['ch_idx']
        racks  = getattr(context.scene, "pb_racks", [])
        if i < len(racks):
            attr = f'ch{ch_idx}'
            rack = racks[i]
            setattr(rack, attr, not getattr(rack, attr, False))
        return True

    return False


# ---------------------------------------------------------------------------
# LED pulse update — called by meter timer in Loader.py
# ---------------------------------------------------------------------------
def update_led_states(is_playing):
    """Toggle LEDs for channels where compression is active."""
    global _led_states
    if not is_playing:
        _led_states.clear()
        return
    scene = bpy.context.scene
    if not scene: return
    racks = getattr(scene, "pb_racks", [])
    for i, rack in enumerate(racks):
        if not rack.enabled: continue
        assigned = get_rack_channels(rack)
        for ch_idx in assigned:
            gr = _gr_levels.get(i, {}).get(ch_idx, 0.0)
            # Lit when actively compressing (GR > 5%)
            _led_states[(i, ch_idx)] = (gr > 0.05 and is_playing)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register_racks():
    bpy.utils.register_class(PB_RackSettings)
    bpy.types.Scene.pb_racks = bpy.props.CollectionProperty(
        type=PB_RackSettings)
    print("[RACKS] registered")


def unregister_racks():
    try:
        bpy.utils.unregister_class(PB_RackSettings)
        del bpy.types.Scene.pb_racks
    except Exception:
        pass
    print("[RACKS] unregistered")
