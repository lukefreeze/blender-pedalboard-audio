# =============================================================================
# ui/mixer/channel_strip.py
# Draws a single channel fader strip.
#
# ┌─ LAYOUT CONSTANTS ──────────────────────────────────────────────────────┐
# │ All pixel dimensions are UNSCALED — multiply by scale at draw time.     │
# │ To resize any element, change the constant here and nowhere else.       │
# └─────────────────────────────────────────────────────────────────────────┘
# =============================================================================

import math
import bpy
import blf

from ui.mixer.draw_utils import (
    draw_rect, draw_rect_outline, draw_circle, draw_circle_knob,
    draw_meter, draw_numbox, draw_text, text_width, draw_element,
)
from core.constants import (
    FADER_MIN, FADER_MAX, FADER_HEIGHT, FADER_HANDLE_H, FADER_HANDLE_W,
    FADER_HANDLE_X_OFF, FADER_TRACK_BOTTOM, METER_W, METER_X_OFF,
    NUMBOX_H, GAIN_MIN, GAIN_MAX, GAIN_DEFAULT,
    SEND_BTN_H, SEND_BTN_GAP, SEND_MIN_SLOTS, SEND_START_Y, EFFECT_ABBREV,
    MAX_CHANNELS,
)

# ---------------------------------------------------------------------------
# Strip layout constants — tweak these to resize the mixing desk
# ---------------------------------------------------------------------------
STRIP_W             = 120    # width of one channel strip in px
STRIP_STRIDE        = 135    # left-edge to left-edge between strips
STRIP_LEFT_MARGIN   = 30     # x offset of ch1 from canvas left
STRIP_BG_COLOR      = (0.07, 0.07, 0.07, 1.0)

# Mute / Solo buttons
M_BTN_X_OFF         = 10     # from strip left
M_BTN_W             = 45
M_BTN_H             = 30
M_BTN_Y_OFF         = 50     # below base_y
S_BTN_X_OFF         = 65
M_COLOR_ON          = (0.9, 0.1, 0.1, 1.0)
M_COLOR_OFF         = (0.2, 0.2, 0.2, 1.0)
S_COLOR_ON          = (0.9, 0.9, 0.1, 1.0)
S_COLOR_OFF         = (0.2, 0.2, 0.2, 1.0)

# Gain knob
KNOB_GAIN_X_OFF     = 60     # from strip left (centred knob)
KNOB_GAIN_Y_OFF     = 100    # below base_y
KNOB_GAIN_R         = 20
KNOB_GAIN_COLOR     = (0.1, 0.5, 0.1)

# EQ knobs
EQ_KNOB_X_OFF       = 60
EQ_KNOB_START_Y     = 175    # below base_y (before send height added)
EQ_KNOB_SPACING     = 50
KNOB_EQ_R           = 16
KNOB_EQ_H_COLOR     = (0.2, 0.2, 0.6)
KNOB_EQ_M_COLOR     = (0.4, 0.2, 0.6)
KNOB_EQ_L_COLOR     = (0.6, 0.2, 0.4)

# Pan knob
KNOB_PAN_X_OFF      = 60
KNOB_PAN_R          = 14
KNOB_PAN_CLEARANCE  = 42     # above fader top
KNOB_PAN_COLOR      = (0.65, 0.45, 0.10)

# Fader rail
RAIL_W              = 10
UNITY_LINE_W        = 20     # width of unity mark
FADER_HANDLE_COLOR  = (0.5, 0.5, 0.5, 1.0)
FADER_RAIL_COLOR    = (0.02, 0.02, 0.02, 1.0)
UNITY_COLOR         = (0.6, 0.6, 0.6, 0.8)

# Channel label
CH_LABEL_SIZE       = 11
CH_LABEL_Y_OFF      = 5      # above base_y


# ---------------------------------------------------------------------------
# Send section height helper (also used by mixer_hud.py for layout)
# ---------------------------------------------------------------------------
def send_section_height(n_racks: int, scale: float) -> float:
    """Total pixel height of the send button column for n_racks."""
    slots = max(SEND_MIN_SLOTS, n_racks)
    return slots * (SEND_BTN_H + SEND_BTN_GAP) * scale


def send_section_height_for_group(group_idx: int, scale: float) -> float:
    """send_section_height for a specific fader group."""
    import bpy as _bpy
    scene = _bpy.context.scene
    if not scene:
        return send_section_height(0, scale)
    all_racks = getattr(scene, "pb_racks", [])
    n = sum(1 for r in all_racks if getattr(r, 'group_idx', 0) == group_idx)
    return send_section_height(n, scale)


# ---------------------------------------------------------------------------
# Draw the send button column for one strip
# ---------------------------------------------------------------------------
def draw_send_buttons(sx: float, base_y: float, channel_idx: int,
                      tracks, scale: float, group_idx: int = 0) -> None:
    scene   = bpy.context.scene
    all_racks = getattr(scene, "pb_racks", []) if scene else []
    # Only show racks belonging to this fader group
    racks   = [r for r in all_racks if getattr(r, 'group_idx', 0) == group_idx]
    n_racks = len(racks)
    slots   = max(SEND_MIN_SLOTS, n_racks)

    btn_w   = 100 * scale
    btn_h   = SEND_BTN_H * scale
    btn_x   = sx + 10 * scale
    start_y = base_y - SEND_START_Y * scale

    import gpu
    from gpu_extras.batch import batch_for_shader

    # local_ch = channel_idx within the group (0-8)
    local_ch = channel_idx % 9

    for slot in range(slots):
        by = start_y - slot * (btn_h + SEND_BTN_GAP * scale)

        if slot < n_racks:
            rack   = racks[slot]
            attr   = f'ch{local_ch}'
            active = getattr(rack, attr, False)
            abbrev = EFFECT_ABBREV.get(rack.effect_type, rack.effect_type[:3])
            label  = f"{abbrev} - {slot+1}"
            if active:
                bg  = (0.0,  0.18, 0.08, 1.0)
                bc  = (0.0,  0.65, 0.35, 1.0)
                dot = (0.0,  0.9,  0.5,  1.0)
                tc  = (0.0,  0.85, 0.5,  1.0)
            else:
                bg  = (0.09, 0.09, 0.09, 1.0)
                bc  = (0.22, 0.22, 0.22, 1.0)
                dot = (0.2,  0.2,  0.2,  1.0)
                tc  = (0.35, 0.35, 0.35, 1.0)
        else:
            bg    = (0.06, 0.06, 0.06, 1.0)
            bc    = (0.14, 0.14, 0.14, 1.0)
            dot   = (0.14, 0.14, 0.14, 1.0)
            tc    = (0.2,  0.2,  0.2,  1.0)
            label = ""

        draw_rect(btn_x, by, btn_w, btn_h, bg)

        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        verts  = [(btn_x, by), (btn_x+btn_w, by),
                  (btn_x+btn_w, by+btn_h), (btn_x, by+btn_h), (btn_x, by)]
        batch  = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
        shader.bind()
        shader.uniform_float("color", bc)
        batch.draw(shader)

        draw_circle(btn_x + 8*scale, by + btn_h/2, 3*scale, dot)

        if label:
            blf.size(0, max(1, int(8 * scale)))
            blf.color(0, *tc)
            blf.position(0, btn_x + 16*scale, by + btn_h/2 - 4*scale, 0)
            blf.draw(0, label)
            blf.color(0, 1, 1, 1, 1)


# ---------------------------------------------------------------------------
# Draw a complete channel strip
# ---------------------------------------------------------------------------
def draw_channel_strip(i: int, track, sx: float, base_y: float,
                       scale: float, tracks,
                       engine_level: float, peak_hold: float,
                       group_idx: int = 0) -> None:
    """Draw one complete fader strip at screen x=sx, anchored to base_y."""

    # Background — use only racks in this group for send height
    scene    = bpy.context.scene
    all_racks = getattr(scene, "pb_racks", []) if scene else []
    n_racks  = sum(1 for r in all_racks if getattr(r, 'group_idx', 0) == group_idx)
    send_h   = send_section_height(n_racks, scale)
    strip_h  = 650 * scale + send_h
    draw_element("strip_bg", sx, base_y - strip_h,
                 STRIP_W * scale, strip_h,
                 draw_rect, STRIP_BG_COLOR)

    # Channel label
    blf.size(0, int(CH_LABEL_SIZE * scale))
    blf.color(0, 1, 1, 1, 1)
    blf.position(0, sx + 10*scale, base_y + CH_LABEL_Y_OFF*scale, 0)
    blf.draw(0, f"CH {i+1}")

    # Mute button
    m_c = M_COLOR_ON if track.mute else M_COLOR_OFF
    draw_element("btn_mute_on" if track.mute else "btn_mute_off",
                 sx + M_BTN_X_OFF*scale,
                 base_y - M_BTN_Y_OFF*scale,
                 M_BTN_W*scale, M_BTN_H*scale,
                 draw_rect, m_c)

    # Solo button
    s_c = S_COLOR_ON if track.solo else S_COLOR_OFF
    draw_element("btn_solo_on" if track.solo else "btn_solo_off",
                 sx + S_BTN_X_OFF*scale,
                 base_y - M_BTN_Y_OFF*scale,
                 M_BTN_W*scale, M_BTN_H*scale,
                 draw_rect, s_c)

    blf.size(0, int(9 * scale))
    blf.color(0, 1, 1, 1, 1)
    blf.position(0, sx + 24*scale, base_y - 38*scale, 0)
    blf.draw(0, "M")
    blf.position(0, sx + 79*scale, base_y - 38*scale, 0)
    blf.draw(0, "S")

    # Gain knob
    kx        = sx + KNOB_GAIN_X_OFF * scale
    gain_norm = (track.gain - GAIN_MIN) / (GAIN_MAX - GAIN_MIN)
    gain_db   = round(20 * math.log10(max(0.001, track.gain)), 1)
    draw_circle_knob(kx, base_y - KNOB_GAIN_Y_OFF*scale,
                     KNOB_GAIN_R*scale, gain_norm,
                     KNOB_GAIN_COLOR, f"G:{gain_db:+.1f}dB")

    # Send buttons — only shows racks from this group
    draw_send_buttons(sx, base_y, i, tracks, scale, group_idx)
    blf.color(0, 1, 1, 1, 1)

    # EQ knobs
    eq_start = EQ_KNOB_START_Y * scale + send_h
    draw_circle_knob(kx, base_y - eq_start,
                     KNOB_EQ_R*scale,
                     (track.eq_high + 24) / 48,
                     KNOB_EQ_H_COLOR, f"H:{int(track.eq_high)}")
    draw_circle_knob(kx, base_y - (eq_start + EQ_KNOB_SPACING*scale),
                     KNOB_EQ_R*scale,
                     (track.eq_mid + 24) / 48,
                     KNOB_EQ_M_COLOR, f"M:{int(track.eq_mid)}")
    draw_circle_knob(kx, base_y - (eq_start + 2*EQ_KNOB_SPACING*scale),
                     KNOB_EQ_R*scale,
                     (track.eq_low + 24) / 48,
                     KNOB_EQ_L_COLOR, f"L:{int(track.eq_low)}")

    # Fader
    f_h   = FADER_HEIGHT * scale
    f_y   = base_y - (FADER_TRACK_BOTTOM * scale) - send_section_height(n_racks, scale)
    f_hw  = FADER_HANDLE_W * scale
    f_hh  = FADER_HANDLE_H * scale
    f_hx  = sx + FADER_HANDLE_X_OFF * scale

    # Pan knob — between EQ low and fader top
    pan_ky  = f_y + f_h + KNOB_PAN_CLEARANCE * scale
    pan_val = getattr(track, 'pan', 0.5)
    if   pan_val < 0.45: pan_lbl = f"L{int((0.5 - pan_val)*200)}"
    elif pan_val > 0.55: pan_lbl = f"R{int((pan_val - 0.5)*200)}"
    else:                pan_lbl = "C"
    draw_circle_knob(kx, pan_ky, KNOB_PAN_R*scale,
                     pan_val, KNOB_PAN_COLOR, f"PAN:{pan_lbl}")

    # Rail
    rail_cx = f_hx + f_hw/2 - 5*scale
    draw_element("fader_rail", rail_cx, f_y,
                 RAIL_W*scale, f_h,
                 draw_rect, FADER_RAIL_COLOR)

    # Unity mark
    unity_norm = (1.0 - FADER_MIN) / (FADER_MAX - FADER_MIN)
    unity_y    = f_y + unity_norm * f_h
    draw_rect(rail_cx - 5*scale, unity_y,
              UNITY_LINE_W*scale, max(1.0, scale), UNITY_COLOR)

    # Handle
    fader_norm = max(0.0, min(1.0, (track.volume - FADER_MIN) / (FADER_MAX - FADER_MIN)))
    h_p        = f_y + fader_norm * f_h - f_hh / 2
    draw_element("fader_handle", f_hx, h_p, f_hw, f_hh,
                 draw_rect, FADER_HANDLE_COLOR)

    # VU meter
    draw_meter(sx + METER_X_OFF*scale, f_y,
               METER_W*scale, f_h,
               engine_level, peak_hold)

    # Number box
    nb_w = 90 * scale
    nb_h = NUMBOX_H * scale
    nb_x = sx + 15 * scale
    nb_y = f_y - (nb_h + 4*scale)
    draw_numbox(nb_x, nb_y, nb_w, nb_h, track.volume)

    # SENDS label
    blf.size(0, int(8 * scale))
    blf.color(0, 0.35, 0.35, 0.35, 1.0)
    blf.position(0, sx + 10*scale,
                 base_y - (SEND_START_Y - SEND_BTN_H - 6)*scale, 0)
    blf.draw(0, "SENDS")
    blf.color(0, 1, 1, 1, 1)
