# =============================================================================
# ui/mixer/channel_strip.py
# Draws a single channel fader strip.
#
# LAYOUT ARCHITECTURE
# -------------------
# The strip is drawn top-to-bottom using a Y cursor that advances downward
# after each section. Every section is self-contained: it draws its background,
# its content, and advances the cursor by its own height. Moving a section
# just means changing its height constant — everything below it moves too.
#
# Sections in order (top → bottom):
#   1. HEADER     — channel label + M/S buttons
#   2. GAIN       — gain knob + G:dB label
#   3. [divider]
#   4. SENDS      — SENDS label + one slot per rack (dynamic height)
#   5. [divider]
#   6. EQ         — H/M/L knobs
#   7. PAN        — pan knob
#   8. FADER      — fader rail + handle + VU meter + numbox
# =============================================================================

import math

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

_shader = None


def _get_shader():
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _shader


from core.constants import (
    EFFECT_ABBREV,
    FADER_HANDLE_H,
    FADER_HANDLE_W,
    FADER_HANDLE_X_OFF,
    FADER_HEIGHT,
    FADER_MAX,
    FADER_MIN,
    FADER_TRACK_BOTTOM,
    GAIN_MAX,
    GAIN_MIN,
    MAX_CHANNELS,
    METER_W,
    METER_X_OFF,
    NUMBOX_H,
    SEND_BTN_GAP,
    SEND_BTN_H,
    SEND_MIN_SLOTS,
)
from ui.mixer.texture_cache import get_texture
from ui.mixer.draw_utils import (
    draw_circle,
    draw_circle_knob,
    draw_element,
    draw_element_if_loaded,
    draw_meter,
    draw_numbox,
    draw_rect,
    draw_rect_outline,
    draw_text,
    text_width,
)

# ---------------------------------------------------------------------------
# Strip identity constants
# ---------------------------------------------------------------------------
STRIP_W = 120  # width of one channel strip (STRIP_STRIDE=135 gives 15px gap)
STRIP_STRIDE = 135  # left-edge to left-edge spacing
STRIP_LEFT_MARGIN = 30  # x offset of ch1 from canvas left

# ---------------------------------------------------------------------------
# SECTION HEIGHTS  (unscaled px)
# Change a height → that section grows/shrinks, everything below shifts.
# ---------------------------------------------------------------------------
# =============================================================================
# SECTION HEIGHTS — change any value to resize that section.
# Everything below it shifts automatically.
# =============================================================================
SEC_HEADER_H     = 65    # M/S buttons section height
SEC_GAIN_H       = 70    # gain knob + label section height
SEC_EQ_H         = 175   # three EQ knobs section height
SEC_PAN_H        = 120    # pan knob section — increase for more space above pan knob

# Gap on each side of every divider line (total visual gap = DIVIDER_GAP x 2)
DIVIDER_GAP      = 6     # px at scale=1

# Sends section
SENDS_LABEL_H    = 20    # "SENDS" text row height above the slots
SLOT_H           = SEND_BTN_H + SEND_BTN_GAP + 4   # 27px per send slot

# Fader section
FADER_TOP_PAD    = 10    # px gap above fader rail
FADER_BOTTOM_PAD = 20    # px gap below numbox before strip ends  ← increase for more space
FADER_VISUAL_BOTTOM_PAD = 20  # px from rail bottom the handle never goes below — pure visual, no effect on values
METER_X_OFFSET   = 25     # px from strip left edge to VU meter  ← increase to move right
# STRIP_TOTAL_H must equal: SEC_HEADER_H + SEC_GAIN_H + (DIVIDER_GAP*4) +
#   SEC_EQ_H + SEC_PAN_H + FADER_TOP_PAD + FADER_HEIGHT + NUMBOX_H + FADER_BOTTOM_PAD
# = 65+70+24+175+120+10+180+18+20 = 682. Update this if you change any section height.
STRIP_TOTAL_H    = 682   # matches mixer_desk_bg.png — update image before changing this

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
M_COLOR_ON = (0.9, 0.1, 0.1, 1.0)
M_COLOR_OFF = (0.2, 0.2, 0.2, 1.0)
S_COLOR_ON = (0.9, 0.9, 0.1, 1.0)
S_COLOR_OFF = (0.2, 0.2, 0.2, 1.0)
KNOB_GAIN_COLOR = (0.1, 0.5, 0.1)
KNOB_EQ_H_COLOR = (0.2, 0.2, 0.6)
KNOB_EQ_M_COLOR = (0.4, 0.2, 0.6)
KNOB_EQ_L_COLOR = (0.6, 0.2, 0.4)
KNOB_PAN_COLOR = (0.65, 0.45, 0.10)
FADER_HANDLE_COLOR = (0.5, 0.5, 0.5, 1.0)

# ── Fader handle image draw size ────────────────────────────────────────────
# These control how large the fader_handle PNG is drawn.
# Independent of the hitbox (FADER_HANDLE_W/H in constants.py).
# Increase to match the actual pixel size of your cropped handle images.
FADER_HANDLE_IMG_W  = 134   # drawn width of handle image (unscaled px)
FADER_HANDLE_IMG_H  = 135   # drawn height of handle image (unscaled px)
FADER_HANDLE_IMG_X  = 2     # x nudge applied to ALL channels (global offset)
FADER_HANDLE_IMG_Y  = -23     # y nudge applied to ALL channels (global offset)

# Per-channel fine-tune — index 0 = CH1, index 8 = CH9
# Use these to align handles that sit slightly off due to 3D perspective in the image.
# Values are unscaled px at scale 1.0. Added on top of the global IMG_X/Y above.
FADER_HANDLE_CH_X = [7, 0, 0, 0, 0, 0, 0, 0, -3]
FADER_HANDLE_CH_Y = [0, 0, 0, 0, 0, 0, 0, 0, 0]
FADER_RAIL_COLOR = (0.02, 0.02, 0.02, 1.0)
UNITY_COLOR = (0.6, 0.6, 0.6, 0.8)
DIVIDER_COLOR = (0.40, 0.40, 0.45, 1.0)
SENDS_TEXT_COLOR = (0.35, 0.35, 0.35, 1.0)

# ---------------------------------------------------------------------------
# Knob geometry
# ---------------------------------------------------------------------------
KNOB_GAIN_R = 15
KNOB_EQ_R = 14
KNOB_PAN_R = 12

# ── Per-channel knob position offsets ────────────────────────────────────────
# Adjust these to align knobs with your skin image's 3D perspective look.
# Values are UNSCALED pixels at scale 1.0 — multiplied by UI_SCALE at draw time.
# Index 0 = CH1, index 1 = CH2, ... index 8 = CH9
# Positive X = nudge right,  Negative X = nudge left
# Positive Y = nudge up,     Negative Y = nudge down
# ── Per-channel knob position offsets ───────────────────────────────────────
# Each list has one value per channel (index 0 = CH1 … index 8 = CH9).
# Unscaled pixels at scale 1.0 — multiplied by UI_SCALE at draw time.
# Positive X = nudge right, Negative X = nudge left.
# Positive Y = nudge up,    Negative Y = nudge down.
GAIN_KNOB_X_OFFSETS = [-4.5, -3, -2, -1, 0.5, 1.5, 2, 3.5, 6]
GAIN_KNOB_Y_OFFSETS = [1, 1, 1, 1, 1, 1, 1, 1, 1]

EQ_KNOB_X_OFFSETS   = [-4, -3, -1.5, -1, 0, 1, 2, 3.5, 6]
EQ_KNOB_Y_OFFSETS   = [-2, -2, -2, -2, -2, -2, -2, -2, -2]

PAN_KNOB_X_OFFSETS  = [-4, -3, -1.5, -0.5, 1, 2, 2.75, 4, 6]
PAN_KNOB_Y_OFFSETS  = [-1.5, -1.5, -1.5, -1.5, -1.5, -1.5, -1.5, -1.5, -1.5]

# ── Mute / Solo button layout ────────────────────────────────────────────────
# Adjust these to align the on-state overlay images with your skin's buttons.
# All values are UNSCALED pixels at scale 1.0 — multiplied by UI_SCALE at draw time.
BTN_W           = 86     # width of each button image
BTN_H           = 51     # height of each button image
BTN_MUTE_X      = -11     # x offset of M button from strip left edge
BTN_SOLO_X      = 45     # x offset of S button from strip left edge
BTN_Y_OFFSET    = -6.5      # fine-tune vertical position (positive = up, negative = down)
BTN_Y_CENTER    = True   # if True, buttons are centred in the header section

# ── Send slot button layout ──────────────────────────────────────────────────
# Adjust to align the send button images within each slot tile.
# All values unscaled pixels at scale 1.0.
SEND_BTN_IMG_W   = 125   # drawn width of the send button image
SEND_BTN_IMG_H   = 27    # drawn height of the send button image
SEND_BTN_IMG_X   = -1    # x offset from strip left edge
SEND_BTN_IMG_Y   = 0     # y offset up from tile bottom
SEND_BTN_TEXT_X  = 30    # x offset of rack label text from button left edge
SEND_BTN_TEXT_Y  = -2.5     # y offset of rack label text (positive = up, negative = down)


# ---------------------------------------------------------------------------
# Helper: send section total height
# ---------------------------------------------------------------------------
def send_section_height(n_racks: int, scale: float) -> float:
    slots = max(SEND_MIN_SLOTS, n_racks)
    return (SENDS_LABEL_H + slots * SLOT_H) * scale


def strip_bottom_y(base_y: float, n_racks: int, scale: float) -> float:
    """Y coordinate of the bottom of the strip (below numbox + padding).
    Single source of truth used by both channel_strip.py and Racks.py.
    base_y is the TOP of the strip body passed to draw_channel_strip.
    """
    sends_h = send_section_height(n_racks, scale)
    return base_y - STRIP_TOTAL_H * scale - sends_h


# ---------------------------------------------------------------------------
# Helper: draw a horizontal divider line across one strip
# ---------------------------------------------------------------------------
def _divider(sx, y, scale):
    shader = _get_shader()
    b = batch_for_shader(shader, "LINES", {"pos": [(sx, y), (sx + STRIP_W * scale, y)]})
    shader.bind()
    shader.uniform_float("color", DIVIDER_COLOR)
    b.draw(shader)


# ---------------------------------------------------------------------------
# Main draw function
# ---------------------------------------------------------------------------
def draw_channel_strip(
    i: int,
    track,
    sx: float,
    base_y: float,
    scale: float,
    tracks,
    engine_level: float,
    peak_hold: float,
    group_idx: int = 0,
) -> None:
    """Draw one complete fader strip. base_y is the TOP of the strip."""

    scene = bpy.context.scene
    all_racks = getattr(scene, "pb_racks", []) if scene else []
    n_racks = sum(1 for r in all_racks if getattr(r, "group_idx", 0) == group_idx)
    slots = max(SEND_MIN_SLOTS, n_racks)
    sends_h = send_section_height(n_racks, scale)  # dynamic section height
    strip_h = STRIP_TOTAL_H * scale + sends_h

    kx = sx + STRIP_W * 0.5 * scale  # horizontal centre of strip
    _ch = i % 9
    # Per-group offsets — each knob type has its own x/y nudge per channel
    _gain_kx = GAIN_KNOB_X_OFFSETS[_ch] * scale if _ch < len(GAIN_KNOB_X_OFFSETS) else 0
    _gain_ky = GAIN_KNOB_Y_OFFSETS[_ch] * scale if _ch < len(GAIN_KNOB_Y_OFFSETS) else 0
    _eq_kx   = EQ_KNOB_X_OFFSETS[_ch]   * scale if _ch < len(EQ_KNOB_X_OFFSETS)   else 0
    _eq_ky   = EQ_KNOB_Y_OFFSETS[_ch]   * scale if _ch < len(EQ_KNOB_Y_OFFSETS)   else 0
    _pan_kx  = PAN_KNOB_X_OFFSETS[_ch]  * scale if _ch < len(PAN_KNOB_X_OFFSETS)  else 0
    _pan_ky  = PAN_KNOB_Y_OFFSETS[_ch]  * scale if _ch < len(PAN_KNOB_Y_OFFSETS)  else 0

    # Y cursor — starts at strip top, advances downward through each section.
    # In Blender GPU coords y=0 is BOTTOM of screen, so "down" = decreasing y.
    # base_y here is the TOP of the strip body (just below the channel label).
    cursor = base_y  # cursor points to the TOP edge of the current section

    # ── Channel label (above the strip body) ─────────────────────────────────
    blf.size(0, int(11 * scale))
    blf.color(0, 1, 1, 1, 1)
    blf.position(0, sx + 10 * scale, cursor + 5 * scale, 0)
    blf.draw(0, f"CH {i + 1}")

    # ── SECTION 1: HEADER (M/S buttons) ──────────────────────────────────────
    sec_top = cursor
    sec_bot = cursor - SEC_HEADER_H * scale

    # strip_top_bg drawn full-width in mixer_hud — fallback only
    if get_texture("strip_top_bg") is None:
        draw_rect(
            sx, sec_bot, STRIP_W * scale, SEC_HEADER_H * scale, (0.10, 0.10, 0.10, 1.0)
        )

    # M/S buttons — position and size driven by constants at top of file
    _btn_w  = BTN_W * scale
    _btn_h  = BTN_H * scale
    if BTN_Y_CENTER:
        _btn_mid_y = sec_bot + (SEC_HEADER_H * scale) * 0.5
        _btn_y = _btn_mid_y - _btn_h * 0.5 + BTN_Y_OFFSET * scale
    else:
        _btn_y = sec_bot + BTN_Y_OFFSET * scale
    _mute_x = sx + BTN_MUTE_X * scale
    _solo_x = sx + BTN_SOLO_X * scale

    _skin_active = get_texture("strip_top_bg") is not None

    # Mute button
    if track.mute:
        draw_element("btn_mute_on", _mute_x, _btn_y, _btn_w, _btn_h,
                     draw_rect, M_COLOR_ON)
    elif not _skin_active:
        draw_rect(_mute_x, _btn_y, _btn_w, _btn_h, M_COLOR_OFF)

    # Solo button
    if track.solo:
        draw_element("btn_solo_on", _solo_x, _btn_y, _btn_w, _btn_h,
                     draw_rect, S_COLOR_ON)
    elif not _skin_active:
        draw_rect(_solo_x, _btn_y, _btn_w, _btn_h, S_COLOR_OFF)

    # M / S labels — only when no skin loaded (skin has labels baked in)
    if not _skin_active:
        blf.size(0, int(9 * scale))
        blf.color(0, 1, 1, 1, 1)
        blf.position(0, _mute_x + _btn_w * 0.4, _btn_y + _btn_h * 0.3, 0)
        blf.draw(0, "M")
        blf.position(0, _solo_x + _btn_w * 0.4, _btn_y + _btn_h * 0.3, 0)
        blf.draw(0, "S")

    cursor = sec_bot  # advance

    # ── SECTION 2: GAIN KNOB ─────────────────────────────────────────────────
    sec_top = cursor
    sec_bot = cursor - SEC_GAIN_H * scale

    # covered by strip_top_bg — fallback only
    if get_texture("strip_top_bg") is None:
        draw_rect(
            sx, sec_bot, STRIP_W * scale, SEC_GAIN_H * scale, (0.10, 0.10, 0.10, 1.0)
        )

    knob_cy = sec_bot + (SEC_GAIN_H * scale) * 0.55 + _gain_ky
    gain_norm = (track.gain - GAIN_MIN) / (GAIN_MAX - GAIN_MIN)
    gain_db = round(20 * math.log10(max(0.001, track.gain)), 1)
    draw_circle_knob(
        kx + _gain_kx,
        knob_cy,
        KNOB_GAIN_R * scale,
        gain_norm,
        KNOB_GAIN_COLOR,
        "" if _skin_active else f"G:{gain_db:+.1f}dB",
        ui_scale=scale,
    )

    cursor = sec_bot

    # ── DIVIDER 1 ─────────────────────────────────────────────────────────────
    cursor -= DIVIDER_GAP * scale
    if not _skin_active:
        _divider(sx, cursor, scale)
    cursor -= DIVIDER_GAP * scale

    # ── SECTION 3: SENDS ─────────────────────────────────────────────────────
    sec_top = cursor
    sec_bot = cursor - sends_h


    # "SENDS" label at top of section
    if not _skin_active:
        blf.size(0, int(8 * scale))
        blf.color(0, *SENDS_TEXT_COLOR)
        blf.position(0, sx + 10 * scale, cursor - SENDS_LABEL_H * scale * 0.5, 0)
        blf.draw(0, "SENDS")
        blf.color(0, 1, 1, 1, 1)

    # Send slot tiles + buttons — flow down from below the label
    slot_cursor = cursor - SENDS_LABEL_H * scale
    btn_w = SEND_BTN_IMG_W * scale
    btn_x = sx + SEND_BTN_IMG_X * scale
    racks = [r for r in all_racks if getattr(r, "group_idx", 0) == group_idx]
    local_ch = i % 9

    for slot in range(slots):
        tile_y = slot_cursor - SLOT_H * scale
        btn_y = tile_y + SEND_BTN_IMG_Y * scale
        btn_h = SEND_BTN_IMG_H * scale


        if slot < n_racks:
            rack = racks[slot]
            active = getattr(rack, f"ch{local_ch}", False)
            abbrev = EFFECT_ABBREV.get(rack.effect_type, rack.effect_type[:3])
            label = f"{abbrev} - {slot + 1}"
            bg = (0.0, 0.18, 0.08, 1.0) if active else (0.09, 0.09, 0.09, 1.0)
            bc = (0.0, 0.65, 0.35, 1.0) if active else (0.22, 0.22, 0.22, 1.0)
            dot = (0.0, 0.9, 0.5, 1.0) if active else (0.2, 0.2, 0.2, 1.0)
            tc = (0.0, 0.85, 0.5, 1.0) if active else (0.35, 0.35, 0.35, 1.0)
        else:
            bg = bc = (0.06, 0.06, 0.06, 1.0)
            dot = tc = (0.14, 0.14, 0.14, 1.0)
            label = ""

        # Send button — PNG skin replaces the rect/border/dot primitives.
        # SendOn.png / SendOff.png used when loaded; fallback to GPU primitives.
        _send_key = "send_btn_on" if (slot < n_racks and active) else "send_btn_off"
        draw_element(_send_key, btn_x, btn_y, btn_w, btn_h, draw_rect, bg)

        # Label text drawn on top when a rack is assigned
        if label:
            blf.size(0, max(1, int(8 * scale)))
            blf.color(0, *tc)
            blf.position(0, btn_x + SEND_BTN_TEXT_X * scale, btn_y + btn_h * 0.5 + SEND_BTN_TEXT_Y * scale, 0)
            blf.draw(0, label)
            blf.color(0, 1, 1, 1, 1)

        slot_cursor = tile_y  # advance by full SLOT_H (includes gap)

    cursor = sec_bot

    # ── DIVIDER 2 ─────────────────────────────────────────────────────────────
    cursor -= DIVIDER_GAP * scale
    if not _skin_active:
        _divider(sx, cursor, scale)
    cursor -= DIVIDER_GAP * scale

    # ── SECTION 4: EQ KNOBS ──────────────────────────────────────────────────
    sec_top = cursor
    sec_bot = cursor - SEC_EQ_H * scale

    # strip_bottom_bg drawn full-width in mixer_hud — fallback only
    if get_texture("strip_bottom_bg") is None:
        draw_rect(
            sx, sec_bot, STRIP_W * scale, SEC_EQ_H * scale, (0.07, 0.07, 0.07, 1.0)
        )

    eq_spacing = (SEC_EQ_H * scale) / 3.0
    eq_configs = [
        ((track.eq_high + 24) / 48, KNOB_EQ_H_COLOR, "" if _skin_active else f"H:{int(track.eq_high)}"),
        ((track.eq_mid + 24) / 48, KNOB_EQ_M_COLOR, "" if _skin_active else f"M:{int(track.eq_mid)}"),
        ((track.eq_low + 24) / 48, KNOB_EQ_L_COLOR, "" if _skin_active else f"L:{int(track.eq_low)}"),
    ]
    for idx, (norm, col, lbl) in enumerate(eq_configs):
        ky = sec_top - (idx + 0.5) * eq_spacing + _eq_ky
        draw_circle_knob(kx + _eq_kx, ky, KNOB_EQ_R * scale, norm, col, lbl, ui_scale=scale)

    cursor = sec_bot

    # ── SECTION 5: PAN KNOB ───────────────────────────────────────────────────
    sec_top = cursor
    sec_bot = cursor - SEC_PAN_H * scale

    # covered by strip_bottom_bg — fallback only
    if get_texture("strip_bottom_bg") is None:
        draw_rect(
            sx, sec_bot, STRIP_W * scale, SEC_PAN_H * scale, (0.07, 0.07, 0.07, 1.0)
        )

    pan_val = getattr(track, "pan", 0.5)
    pan_lbl = (
        "C"
        if 0.45 <= pan_val <= 0.55
        else f"L{int((0.5 - pan_val) * 200)}"
        if pan_val < 0.45
        else f"R{int((pan_val - 0.5) * 200)}"
    )
    pan_cy = sec_bot + (SEC_PAN_H * scale) * 0.55 + _pan_ky
    draw_circle_knob(
        kx + _pan_kx,
        pan_cy,
        KNOB_PAN_R * scale,
        pan_val,
        KNOB_PAN_COLOR,
        "" if _skin_active else f"PAN:{pan_lbl}",
        ui_scale=scale,
    )

    cursor = sec_bot

    # ── SECTION 6: FADER + VU + NUMBOX ──────────────────────────────────────
    # Layout (top to bottom within this section):
    #   [FADER_TOP_PAD]  small gap at top
    #   [FADER_HEIGHT]   fader rail travel
    #   [FADER_BOTTOM_PAD//2]  gap between fader bottom and numbox
    #   [NUMBOX_H]       value box
    #   [FADER_BOTTOM_PAD//2]  gap below numbox before strip ends
    sec_top = cursor
    sec_h   = STRIP_TOTAL_H * scale - (
        SEC_HEADER_H + SEC_GAIN_H + (DIVIDER_GAP * 4) + SEC_EQ_H + SEC_PAN_H
    ) * scale  # remaining height = FADER_TOP_PAD + FADER_HEIGHT + NUMBOX_H + FADER_BOTTOM_PAD

    # Fader section background — skin PNG if available, otherwise transparent
    # (mixer_desk_bg drawn in mixer_hud.py shows through if no PNG present)
    # covered by strip_bottom_bg — fallback only
    if get_texture("strip_bottom_bg") is None:
        draw_element_if_loaded("strip_fader_bg", sx, sec_top - sec_h,
                               STRIP_W * scale, sec_h)

    # Fader rail starts FADER_TOP_PAD below sec_top
    fader_h = FADER_HEIGHT * scale
    f_y     = sec_top - FADER_TOP_PAD * scale - fader_h   # f_y = bottom of fader rail
    f_hw    = FADER_HANDLE_W * scale
    f_hh    = FADER_HANDLE_H * scale
    f_hx    = sx + FADER_HANDLE_X_OFF * scale
    rail_cx = f_hx + f_hw / 2 - 5 * scale

    # Numbox drawn FIRST so fader rail and handle always paint on top
    nb_h = NUMBOX_H * scale
    nb_y = f_y - (FADER_BOTTOM_PAD // 2) * scale - nb_h
    draw_numbox(
        sx + 15 * scale, nb_y, 90 * scale, nb_h,
        track.volume,
        channel=i,
        peak_hold=peak_hold,
        skip_bg=get_texture("strip_bottom_bg") is not None,
        scale=scale,
    )

    draw_element(
        "fader_rail", rail_cx, f_y, 10 * scale, fader_h, draw_rect, FADER_RAIL_COLOR
    )

    # Visual travel range — handle and unity mark both remapped into this.
    # Values and drag sensitivity are completely unaffected.
    _vis_pad    = FADER_VISUAL_BOTTOM_PAD * scale
    _vis_travel = fader_h - 2 * _vis_pad

    unity_norm = (1.0 - FADER_MIN) / (FADER_MAX - FADER_MIN)
    unity_y    = f_y + _vis_pad + unity_norm * _vis_travel
    draw_rect(rail_cx - 5 * scale, unity_y, 20 * scale, max(1.0, scale), UNITY_COLOR)

    fader_norm = max(
        0.0, min(1.0, (track.volume - FADER_MIN) / (FADER_MAX - FADER_MIN))
    )
    h_p = f_y + _vis_pad + fader_norm * _vis_travel - f_hh / 2
    # Per-channel fader handle: try fader_handle_{ch} first, fall back to fader_handle
    _fh_key = f"fader_handle_{(i % 9) + 1}"
    if get_texture(_fh_key) is None:
        _fh_key = "fader_handle"
    # Image draw rect uses FADER_HANDLE_IMG_* — adjust to match your PNG dimensions
    _img_w = FADER_HANDLE_IMG_W * scale
    _img_h = FADER_HANDLE_IMG_H * scale
    _ch_idx = i % 9
    _ch_x   = FADER_HANDLE_CH_X[_ch_idx] * scale if _ch_idx < len(FADER_HANDLE_CH_X) else 0
    _ch_y   = FADER_HANDLE_CH_Y[_ch_idx] * scale if _ch_idx < len(FADER_HANDLE_CH_Y) else 0
    _img_x  = f_hx + (FADER_HANDLE_IMG_X + 0) * scale - (_img_w - f_hw) / 2 + _ch_x
    _img_y  = h_p  + (FADER_HANDLE_IMG_Y + 0) * scale - (_img_h - f_hh) / 2 + _ch_y
    draw_element(_fh_key, _img_x, _img_y, _img_w, _img_h, draw_rect, FADER_HANDLE_COLOR)

    draw_meter(
        sx + METER_X_OFFSET * scale, f_y, METER_W * scale, fader_h, engine_level, peak_hold
    )
