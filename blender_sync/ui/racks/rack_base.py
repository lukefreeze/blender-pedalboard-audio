# =============================================================================
# ui/racks/rack_base.py
# Shared rack chassis, rail, channel buttons, spectrum display, GR meters,
# and the expanded/collapsed shell renderers that dispatch to rack_*.py.
# ┌─ LAYOUT CONSTANTS ─────────────────────────────────────────────────────┐
# │ RACK_WIDTH, RACK_EXPANDED_H, RACK_RAIL_H, RACK_GAP, RACK_COLLAPSED_H  │
# │ Change any of these to resize the rack chassis globally.               │
# └────────────────────────────────────────────────────────────────────────┘
# =============================================================================
print("[RACK_BASE] loading rack_base.py")

import math
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
# ---------------------------------------------------------------------------
# Shader singleton — gpu.shader.from_builtin() is expensive; reuse one instance.
# ---------------------------------------------------------------------------
_shader = None

def _get_shader():
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _shader



# Rack layout constants — defined here directly to avoid circular imports.
# (rack_base is imported BY Racks.py, so importing FROM Racks at module level
#  always fails. These values must be kept in sync with Racks.py manually.)
RACK_WIDTH          = 1200
RACK_EXPANDED_H     = 260
RACK_EXPANDED_H_MB  = 400
RACK_EXPANDED_H_EQ  = 580
RACK_EXPANDED_H_RV  = 340
RACK_EXPANDED_H_NG  = 320
RACK_EXPANDED_H_DL  = 320
RACK_EXPANDED_H_MX  = 320
RACK_COLLAPSED_H    = 48
RACK_RAIL_H         = 32
RACK_GAP            = 8

# On/Off + Close button PNG size tuning.
# The source PNG is 100×38px — RACK_BTN_SCALE shrinks/grows the blit.
# 1.0 = full size, 0.6 = 60×23px (fits neatly inside a 32px rail).
RACK_BTN_SCALE = 0.7
# Derived unscaled sizes used by both draw and hitbox code
RACK_BTN_W = 100 * RACK_BTN_SCALE   # unscaled px
RACK_BTN_H = 38  * RACK_BTN_SCALE   # unscaled px

# Position nudge — unscaled px, applied to both blit and hitboxes.
# Positive X = right, Negative X = left.
# Positive Y = up,    Negative Y = down.
RACK_BTN_X_OFFSET = -5.0
RACK_BTN_Y_OFFSET = -2.0

# ---------------------------------------------------------------------------
# HITBOX TUNING — independent of the PNG blit size/position.
# Adjust these to make the clickable areas match the visual buttons exactly.
#
# RACK_HB_W            : total hitbox width  (unscaled px)
# RACK_HB_H            : total hitbox height (unscaled px)
# RACK_HB_X_OFFSET     : nudge hitbox left/right (unscaled px)
# RACK_HB_Y_OFFSET     : nudge hitbox up/down    (unscaled px)
# RACK_BTN_ONOFF_SPLIT : fraction of RACK_HB_W where ON/OFF ends / close X begins
# ---------------------------------------------------------------------------
RACK_HB_W        = 65.0   # hitbox width  (unscaled px) — tune independently of PNG size
RACK_HB_H        = 22.8   # hitbox height (unscaled px) — tune independently of PNG size
RACK_HB_X_OFFSET = -7.0
RACK_HB_Y_OFFSET = 0.0

# Fraction of RACK_HB_W where ON/OFF ends and close X begins.
# 0.60 = ON/OFF takes left 60%, close X takes right 40%.
RACK_BTN_ONOFF_SPLIT = 0.70

# ---------------------------------------------------------------------------
# DEBUG — set True to draw coloured outlines over the ON/OFF and close X
# hitboxes so you can align them precisely. Green = ON/OFF, Red = close X.
# Set False when done tuning.
# ---------------------------------------------------------------------------
RACK_BTN_DEBUG = False

SPEC_H          = 200
SPEC_W          = 480
SPEC_X          = 430
SPEC_BANDS      = 48
KNOB_SECTION_W  = 360
KNOB_SPACING    = 68
KNOB_START_X    = 55
CH_BTN_SIZE     = 26

# =============================================================================
# RACK CHANNEL BUTTON SKIN TUNING
# All values are unscaled px — multiplied by scale at draw time.
# CH_BTN_PNG_SCALE    : size multiplier on the PNG (1.0 = same as btn_s)
# CH_BTN_PNG_X_OFFSET : nudge PNG left(−) / right(+)
# CH_BTN_PNG_Y_OFFSET : nudge PNG down(−) / up(+)
# CH_BTN_LABEL_X_OFFSET / Y_OFFSET : nudge the channel number label
# =============================================================================
CH_BTN_PNG_SCALE      = 1.2
CH_BTN_PNG_X_OFFSET   = -2.0
CH_BTN_PNG_Y_OFFSET   = -2.0
CH_BTN_LABEL_X_OFFSET = -1.75
CH_BTN_LABEL_Y_OFFSET = -0.5
GR_BAR_W        = 12
GR_BAR_SPACING  = 16

# The preset selector box is centred in the rack's top bar for every effect
# type (see _draw_rack_expanded), so titles on the left and on/off/close
# buttons on the right always have clear room either side of it.
# Fine-tune nudge only — unscaled px, positive = right. Applies to all racks.
PRESET_BOX_CENTER_X_OFFSET = 0.0

# =============================================================================
# COLLAPSED RACK — per-effect-type background skin
# =============================================================================
# One PNG per effect type, same idea as the expanded "_bg" skins: the title/
# logo, expand-arrow icon, and any decorative chrome are baked into the art;
# the row layout (badge number, preset name, channel badges, ON/OFF+close)
# stays fixed and is drawn on top at the same coordinates for every rack type.
#
# To add a new rack's collapsed art: drop a PNG named exactly like the value
# below into ui/assets/skins/default/, matching the key for that effect_type.
# No code changes needed — get_texture() picks it up automatically. Any type
# missing its PNG falls back to the old flat rect + border so it doesn't
# disappear.
_COLLAPSED_BG_KEY = {
    "COMP_SINGLE": "rack_comp_single_collapsed_bg",
    "COMP_MULTI":  "rack_comp_multi_collapsed_bg",
    "EQ":          "rack_eq_collapsed_bg",
    "REVERB":      "rack_reverb_collapsed_bg",
    "NOISE_GATE":  "rack_noisegate_collapsed_bg",
    "DELAY":       "rack_delay_collapsed_bg",
    "BOOSTER":     "rack_booster_collapsed_bg",
    "MIXDOWN":     "rack_mixdown_collapsed_bg",
}

# =============================================================================
# COLLAPSED RACK — per-channel LED strip
# =============================================================================
# All 9 channels are always shown as a small numbered LED each (not just the
# ones this rack uses). Channels routed to this rack blink between the "off"
# and "on" LED skins to show they're live; unused channels sit on "off".
# PNGs: rack_collapsed_ch_led_off.png / rack_collapsed_ch_led_on.png in
# skins/default/ — see SKIN_MAP in texture_cache.py. Falls back to plain
# circles (dim grey / blinking green) if the PNGs aren't there yet.
RACK_COLLAPSED_LED_W        = 10.0   # unscaled px — LED icon width
RACK_COLLAPSED_LED_H        = 10.0   # unscaled px — LED icon height
RACK_COLLAPSED_LED_GAP      = 5.0    # unscaled px — gap between adjacent LEDs
RACK_COLLAPSED_LED_NUM_GAP  = 3.0    # unscaled px — gap between number and LED top
RACK_COLLAPSED_LED_FLASH_HZ = 2.0    # blink rate, full on/off cycles per second

# =============================================================================
# SINGLE-BAND COMPRESSOR KNOB TUNING
# All values are unscaled px — multiplied by scale at draw time.
#
# SB_KNOB_SCALE   : size multiplier relative to the base knob_r (1.0 = no change)
# SB_KNOB_X_*     : X nudge for each knob column (col 0/1/2 = left/mid/right)
# SB_KNOB_Y_ROW0  : Y nudge for the top row of knobs
# SB_KNOB_Y_ROW1  : Y nudge for the bottom row of knobs
#
# Knob draw order (param_order = [0,1,5, 2,3,4]):
#   Row 0 (top):    p0=Threshold  p1=Ratio     p5=Knee
#   Row 1 (bottom): p2=Attack     p3=Release   p4=Gain
# =============================================================================
SB_KNOB_SCALE   = 0.75   # shrink/grow all 6 knobs — try 0.75 to start

SB_KNOB_X_COL0  = 1.5   # p0 Threshold (top) / p2 Attack (bottom)
SB_KNOB_X_COL1  = 1.5   # p1 Ratio     (top) / p3 Release (bottom)
SB_KNOB_X_COL2  = 1.5   # p5 Knee      (top) / p4 Gain    (bottom)

SB_KNOB_Y_ROW0  = 4.5   # top row    — positive moves up
SB_KNOB_Y_ROW1  = -5.0   # bottom row — positive moves up

# EFFECT_TYPES and PRESETS are fetched lazily from Racks inside each function
# that needs them — see _get_racks_state() helper below.

def _get_racks_state():
    """Lazily import live state from Racks.py. Safe to call inside any draw function."""
    import Racks as _r
    return {
        'EFFECT_TYPES':     _r.EFFECT_TYPES,
        'PRESETS':          _r.PRESETS,
        '_reorder_open':    _r._reorder_open,
        '_reorder_rack_idx':_r._reorder_rack_idx,
        '_led_states':      _r._led_states,
    }



try:
    from ui.mixer.draw_utils import (
        draw_rect as _draw_rect,
        draw_line as _draw_line,
        draw_circle as _draw_circle,
        draw_text as _draw_text,
        text_width as _text_width,
        draw_knob as _draw_knob,
    )
except ImportError:
    pass

def _draw_spectrum(rx, ry, rw, rh, rack_idx, scale):
    """Draw the spectrum analyser display."""
    # Lazy import — get_rack_channels not available at module level
    try:
        import Racks as _rs_spec
        get_rack_channels = _rs_spec.get_rack_channels
    except Exception:
        get_rack_channels = lambda r: set()
    # Background — skin PNG if available, otherwise solid colour
    try:
        from ui.mixer.draw_utils import draw_element as _de
        _rack_skin_key = {
            "COMP_MULTI":  "rack_comp_multi_bg",
            "COMP_SINGLE": "rack_comp_single_bg",
            "EQ":          "rack_eq_bg",
            "REVERB":      "rack_reverb_bg",
            "NOISE_GATE":  "rack_noisegate_bg",
            "DELAY":       "rack_delay_bg",
            "BOOSTER":     "rack_booster_bg",
            "MIXDOWN":     "rack_mixdown_bg",
        }.get(getattr(rack, 'effect_type', ''), None)
        if _rack_skin_key:
            _de(_rack_skin_key, rx, ry, rw, rh, _draw_rect, (0.04, 0.04, 0.04, 1.0))
        else:
            _draw_rect(rx, ry, rw, rh, (0.04, 0.04, 0.04, 1.0))
    except Exception:
        _draw_rect(rx, ry, rw, rh, (0.04, 0.04, 0.04, 1.0))
    # Border
    shader = _get_shader()
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

    # Spectrum bars — clear when rack is bypassed, no static fallback
    fft_flat = None
    _rack_on = True
    try:
        import bpy as _bpys0
        _sc0   = _bpys0.context.scene
        _rs0   = getattr(_sc0, "pb_racks", []) if _sc0 else []
        _rack_on = _rs0[rack_idx].enabled if rack_idx < len(_rs0) else True
    except Exception:
        pass

    if _rack_on:
        try:
            from Loader import get_engine as _get_eng_spec
            import bpy as _bpys
            import numpy as _np
            scene_s = _bpys.context.scene
            racks_s = getattr(scene_s, "pb_racks", [])
            if rack_idx < len(racks_s):
                assigned_s = get_rack_channels(racks_s[rack_idx])
                if assigned_s:
                    ch_s = list(assigned_s)[0]
                    _eng_spec = _get_eng_spec()
                    if _eng_spec:
                        _hj_spec = _eng_spec.get_engine()
                        if _hj_spec and 0 <= ch_s < 32:
                            _s_spec = _hj_spec.get_state()
                            all_bins = list(_s_spec.get_spec_bins(ch_s))
                            if any(v > 0 for v in all_bins):
                                fft_flat = _np.array(all_bins, dtype=float)
        except Exception:
            fft_flat = None

    bar_w = (rw - 4*scale) / SPEC_BANDS
    if fft_flat is not None and _rack_on:
        import math as _mth
        n_bins = len(fft_flat)

        # Log-spaced centre bin for each bar
        centres = [_mth.pow(n_bins, b / SPEC_BANDS) - 1.0
                   for b in range(SPEC_BANDS)]

        # Sigma = half the gap to adjacent centres, wider for low-freq bars
        # that share only a few real FFT bins between many display bars
        sigmas = []
        for b in range(SPEC_BANDS):
            if b == 0:
                gap = max(0.5, centres[1] - centres[0])
            elif b == SPEC_BANDS - 1:
                gap = max(0.5, centres[-1] - centres[-2])
            else:
                gap = max(0.5, (centres[b + 1] - centres[b - 1]) * 0.5)
            sigmas.append(max(1.5, gap * 1.5))

        for b in range(SPEC_BANDS):
            t      = b / SPEC_BANDS
            centre = centres[b]
            sigma  = sigmas[b]
            # Gaussian-weighted average — each bar blends across its neighbourhood
            lo = max(0, int(centre - 3.0 * sigma))
            hi = min(n_bins - 1, int(centre + 3.0 * sigma) + 1)
            total_w = 0.0
            total_v = 0.0
            for i in range(lo, hi + 1):
                w = _mth.exp(-0.5 * ((i - centre) / sigma) ** 2)
                total_w += w
                total_v += w * float(fft_flat[i])
            h_frac = (total_v / total_w) if total_w > 0 else float(fft_flat[max(0, min(n_bins - 1, int(centre)))])
            h_frac = max(0.04, min(0.95, h_frac))
            bar_h  = h_frac * rh
            bx     = rx + 2*scale + b * bar_w
            if t < 0.3:   col = (0.0, 0.7,  0.45, 0.75)
            elif t < 0.6: col = (0.0, 0.85, 0.55, 0.85)
            elif t < 0.8: col = (0.9, 0.65, 0.0,  0.7)
            else:         col = (0.7, 0.35, 0.0,  0.5)
            _draw_rect(bx, ry, max(bar_w - scale, 1.0), bar_h, col)

    # GR curve — classic soft knee transfer function (input→output diagonal).
    # X = input level -60..0 dB, Y = output level -60..0 dB.
    # 1:1 slope below threshold, compressed slope above, soft knee join.
    import math as _math
    scene = bpy.context.scene
    racks = getattr(scene, "pb_racks", [])
    if rack_idx < len(racks):
        rack = racks[rack_idx]
        if rack.effect_type == "COMP_SINGLE":
            thr_db  = -40.0 + rack.p0 * 40.0
            ratio   =  1.0  + rack.p1 * 19.0
            knee_db =  0.5  + rack.p5 * 23.5

            pts = []
            for s in range(129):
                t      = s / 128.0
                in_db  = -60.0 + t * 60.0
                half_k = knee_db * 0.5
                if in_db <= thr_db - half_k:
                    out_db = in_db
                elif in_db <= thr_db + half_k and knee_db > 0:
                    x      = in_db - thr_db + half_k
                    out_db = in_db + (1.0/ratio - 1.0)*(x*x)/(2.0*knee_db)
                else:
                    out_db = thr_db + (in_db - thr_db) / ratio
                out_norm = (out_db + 60.0) / 60.0
                pts.append((rx + t*rw, ry + out_norm*rh))

            # Thick red diagonal line — clearly visible over bars
            for i in range(len(pts)-1):
                _draw_line(pts[i][0], pts[i][1],
                           pts[i+1][0], pts[i+1][1],
                           (0.9, 0.2, 0.2, 0.9), max(2.5, scale*2.5))

            # Threshold vertical marker
            thr_norm = (thr_db + 60.0) / 60.0
            thr_x    = rx + thr_norm * rw
            _draw_line(thr_x, ry, thr_x, ry+rh,
                       (0.6, 0.2, 0.2, 0.4), max(1.0, scale*1.0))

    # Frequency labels — suppressed for COMP_SINGLE (baked into background PNG)
    _spec_etype = getattr(rack, 'effect_type', '') if rack_idx < len(racks) else ''
    if _spec_etype != "COMP_SINGLE":
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
    """Draw one slim GR meter per assigned channel.
    GR meter: 0dB at TOP, reduction fills downward from top.
    Like a VU meter — full bar = heavy compression, empty = no compression.
    """
    if not assigned_channels: return
    bar_w   = GR_BAR_W * scale
    spacing = GR_BAR_SPACING * scale
    fs      = max(1, int(7*scale))

    import bpy as _bpy_gr
    scene_gr = _bpy_gr.context.scene
    racks_gr = getattr(scene_gr, "pb_racks", []) if scene_gr else []
    rack_gr  = racks_gr[rack_idx] if rack_idx < len(racks_gr) else None
    is_enabled = rack_gr.enabled if rack_gr else True

    for i, ch_idx in enumerate(assigned_channels[:6]):
        bx = rx + i * spacing
        tw = _text_width(str(ch_idx+1), fs)
        _draw_text(str(ch_idx+1), bx + bar_w/2 - tw/2,
                   ry - 12*scale, fs, (0.4,0.4,0.4,1.0))
        _draw_rect(bx, ry, bar_w, rh, (0.04, 0.04, 0.04, 1.0))

        # Premier Pro style: green signal bar + red GR cap.
        # Signal from FFT timeline. GR computed from signal + rack params
        # (COMP_SINGLE has no separate GR timeline — compute it here).
        sig_norm  = 0.0
        gr_db_val = 0.0
        if is_enabled:
            try:
                from Loader import get_engine as _get_eng_sb
                _eng_sb = _get_eng_sb()
                if _eng_sb:
                    _hj_sb = _eng_sb.get_engine()
                    if _hj_sb and 0 <= ch_idx < 32:
                        _st_sb = _hj_sb.get_state()
                        _gv_sb = _st_sb.get_gr_levels(ch_idx)
                        gr_db_val = min(12.0, max(0.0, float(_gv_sb[0]))) if _gv_sb else 0.0
                        sig_norm  = min(1.0, _hj_sb.get_meter_rms(ch_idx) * 4.0)
            except Exception:
                pass

        sig_h   = max(0.0, min(1.0, sig_norm)) * rh
        if sig_h > 0.5:
            gr_h    = max(0.0, min(1.0, gr_db_val / 24.0)) * sig_h  # 24dB full scale
            green_h = sig_h - gr_h
            if green_h > 0.5:
                _draw_rect(bx, ry, bar_w, green_h, (0.05, 0.55, 0.25, 0.85))
                if green_h > 3*scale:
                    _draw_rect(bx, ry+green_h-2*scale, bar_w, 2*scale,
                               (0.1, 0.9, 0.4, 0.95))
            if gr_h > 0.5:
                _draw_rect(bx, ry+green_h, bar_w, gr_h, (0.85, 0.15, 0.15, 0.9))
                if gr_h > 2*scale:
                    _draw_rect(bx, ry+green_h+gr_h-2*scale, bar_w, 2*scale,
                               (1.0, 0.35, 0.35, 1.0))

        for db_t in [0.25, 0.5, 0.75]:
            _draw_rect(bx, ry + db_t*rh, bar_w, max(0.5, scale*0.5),
                       (0.2, 0.2, 0.2, 1.0))
        _draw_text("GR", bx + bar_w/2 - _text_width("GR",fs)/2,
                   ry + rh + 2*scale, fs, (0.3,0.3,0.3,1.0))


def _draw_gr_meter_band(bx, by, bw, bh, gr_db, scale, signal_norm=0.0):
    """Premier Pro style: green signal bar rising from bottom + red GR cap on top."""
    _draw_rect(bx, by, bw, bh, (0.04, 0.04, 0.04, 1.0))
    sig_h = max(0.0, min(1.0, signal_norm)) * bh
    if sig_h > 0.5:
        gr_h    = max(0.0, min(1.0, gr_db / 12.0)) * sig_h
        green_h = sig_h - gr_h
        if green_h > 0.5:
            _draw_rect(bx, by, bw, green_h, (0.05, 0.55, 0.25, 0.85))
            if green_h > 3*scale:
                _draw_rect(bx, by + green_h - 2*scale, bw, 2*scale,
                           (0.1, 0.9, 0.4, 0.95))
        if gr_h > 0.5:
            _draw_rect(bx, by + green_h, bw, gr_h, (0.85, 0.15, 0.15, 0.9))
            if gr_h > 2*scale:
                _draw_rect(bx, by + green_h + gr_h - 2*scale, bw, 2*scale,
                           (1.0, 0.35, 0.35, 1.0))
    for t in [0.25, 0.5, 0.75]:
        _draw_rect(bx, by + t*bh, bw, max(0.5, scale*0.5), (0.2, 0.2, 0.2, 1.0))


def _draw_channel_buttons(rx, ry, rack, scale):
    """Draw channel assignment buttons — always exactly 9 per rack group.

    ch0–ch8 are LOCAL indices within the rack's group.
    The label shows the absolute VSE channel number (group_idx*9 + local + 1).

    For racks with channel buttons baked into the background PNG
    (currently DELAY), only the number is drawn — no PNG blit or rect,
    just the channel number in assigned/unassigned colour.
    """
    btn_s = CH_BTN_SIZE * scale
    gap   = 4 * scale
    fs    = max(1, int(10*scale))

    group_idx   = getattr(rack, 'group_idx', 0)
    offset      = group_idx * 9
    etype       = getattr(rack, 'effect_type', '')
    number_only = True  # all racks now have channel buttons baked into background PNG

    shader = _get_shader()
    for local_idx in range(9):
        row = local_idx // 3
        col = local_idx % 3
        bx  = rx + col * (btn_s + gap)
        by  = ry - row * (btn_s + gap) - btn_s

        attr     = f'ch{local_idx}'
        assigned = getattr(rack, attr, False)

        if number_only:
            # Background baked into PNG — just draw the number in the right colour
            tc = (0.9, 0.9, 0.9, 1.0) if assigned else (0.4, 0.4, 0.4, 1.0)
        else:
            # Try PNG skin — per-rack override first, then universal default
            try:
                from ui.mixer.texture_cache import get_texture_with_fallback as _gtf
                from ui.mixer.texture_cache import blit_texture as _blt_btn
                _etype_btn = etype.lower()
                _state     = 'on' if assigned else 'off'
                _btn_tex, _used_key = _gtf(
                    f"rack_{_etype_btn}_ch_btn_{_state}",
                    f"rack_ch_btn_{_state}",
                )
            except Exception:
                _btn_tex, _used_key = None, None

            if _btn_tex:
                _png_s = btn_s * CH_BTN_PNG_SCALE
                _png_x = bx + (btn_s - _png_s) / 2 + CH_BTN_PNG_X_OFFSET * scale
                _png_y = by + (btn_s - _png_s) / 2 + CH_BTN_PNG_Y_OFFSET * scale
                _blt_btn(_btn_tex, _png_x, _png_y, _png_s, _png_s, key=_used_key)
                tc = (0.9, 0.9, 0.9, 1.0) if assigned else (0.4, 0.4, 0.4, 1.0)
            else:
                # Fallback — solid rect + border
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

        # Channel number label always drawn on top
        label = str(offset + local_idx + 1)
        tw    = _text_width(label, fs)
        _draw_text(label,
                   bx + btn_s/2 - tw/2 + CH_BTN_LABEL_X_OFFSET * scale,
                   by + btn_s/2 - fs/2  + CH_BTN_LABEL_Y_OFFSET * scale,
                   fs, tc)


# Band colours for multiband display (matching C6-style)
BAND_COLORS = [
    (0.2, 0.5, 1.0, 0.85),   # Low      — blue
    (0.2, 0.9, 0.4, 0.85),   # Low-Mid  — green
    (1.0, 0.7, 0.1, 0.85),   # High-Mid — amber
    (1.0, 0.3, 0.3, 0.85),   # High     — red
]
BAND_NAMES  = ["Low", "L-Mid", "H-Mid", "High"]
BAND_FREQS  = ["<120Hz", "120-800Hz", "800Hz-5kHz", ">5kHz"]




def _draw_rack_expanded(rx, ry, rack, rack_idx, scale, rack_width=None):
    """Draw a fully expanded rack unit."""
    _rs = _get_racks_state()
    EFFECT_TYPES      = _rs['EFFECT_TYPES']
    PRESETS           = _rs['PRESETS']
    _reorder_open     = _rs['_reorder_open']
    _reorder_rack_idx = _rs['_reorder_rack_idx']
    _led_states       = _rs['_led_states']
    rw  = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh  = (RACK_EXPANDED_H_MB  if rack.effect_type == "COMP_MULTI"
           else RACK_EXPANDED_H_EQ if rack.effect_type == "EQ"
           else RACK_EXPANDED_H_RV if rack.effect_type == "REVERB"
           else RACK_EXPANDED_H_NG if rack.effect_type == "NOISE_GATE"
           else RACK_EXPANDED_H_DL if rack.effect_type == "DELAY"
           else RACK_EXPANDED_H_DL if rack.effect_type == "BOOSTER"
           else RACK_EXPANDED_H_MX if rack.effect_type == "MIXDOWN"
           else RACK_EXPANDED_H) * scale

    # --- CHASSIS ---
    _draw_rect(rx, ry, rw, rh, (0.1, 0.1, 0.1, 1.0))
    shader = _get_shader()
    verts  = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch  = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.28,0.28,0.28,1.0))
    batch.draw(shader)

    # --- TOP RAIL --- (background drawn after body so PNG doesn't cover it)
    rail_h = RACK_RAIL_H * scale
    # --- BODY CONTENT — dispatch by effect type ---
    # Lazy imports: body draw functions live in separate files.
    # Imported here (inside function) to avoid circular import at load time.
    from ui.racks.rack_comp    import _draw_multiband_body
    from ui.racks.rack_eq      import _draw_eq_body
    from ui.racks.rack_reverb  import _draw_reverb_body
    from ui.racks.rack_noisegate import _draw_noisegate_body
    from ui.racks.rack_delay   import _draw_delay_body
    # EFFECT_PARAMS and other live state from Racks
    import Racks as _racks_mod
    EFFECT_PARAMS     = _racks_mod.EFFECT_PARAMS
    KNOB_START_X      = _racks_mod.KNOB_START_X
    KNOB_SPACING      = _racks_mod.KNOB_SPACING
    KNOB_SECTION_W    = _racks_mod.KNOB_SECTION_W
    SPEC_H            = _racks_mod.SPEC_H
    SPEC_W            = _racks_mod.SPEC_W
    SPEC_X            = _racks_mod.SPEC_X
    get_rack_channels = _racks_mod.get_rack_channels
    _gr_levels        = _racks_mod._gr_levels
    etype  = rack.effect_type
    body_h = rh - RACK_RAIL_H * scale
    spec_h = min(SPEC_H * scale, body_h - 50*scale)

    if etype == "COMP_MULTI":
        _draw_multiband_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "EQ":
        _draw_eq_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "REVERB":
        _draw_reverb_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "NOISE_GATE":
        _draw_noisegate_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "DELAY":
        _draw_delay_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "BOOSTER":
        try:
            from ui.racks.rack_booster import _draw_booster_body
            _draw_booster_body(rx, ry, rw, rh, rack, rack_idx, scale)
        except ImportError as e:
            print(f"[BOOSTER] ImportError: {e}")
        except Exception as e:
            print(f"[BOOSTER] draw error: {e}")
            import traceback; traceback.print_exc()
    elif etype == "MIXDOWN":
        try:
            from ui.racks.rack_mixdown import _draw_mixdown_body
            _draw_mixdown_body(rx, ry, rw, rh, rack, rack_idx, scale)
        except ImportError as e:
            print(f"[MIXDOWN] ImportError — rack_mixdown.py not found: {e}")
        except Exception as e:
            print(f"[MIXDOWN] draw error: {e}")
            import traceback; traceback.print_exc()
    else:
        # Single band: 2x3 knob grid + spectrum + GR meters
        # PNG background — blit first so all body content draws on top
        try:
            from ui.mixer.texture_cache import get_texture as _gtc_sb
            from ui.mixer.texture_cache import blit_texture as _blt_sb
            _sb_tex = _gtc_sb("rack_comp_single_bg")
            if _sb_tex:
                _blt_sb(_sb_tex, rx, ry, rw, rh, key="rack_comp_single_bg")
            else:
                _draw_rect(rx, ry, rw, body_h, (0.07, 0.07, 0.07, 1.0))
        except Exception:
            _draw_rect(rx, ry, rw, body_h, (0.07, 0.07, 0.07, 1.0))

        params  = EFFECT_PARAMS.get(etype, [])
        col     = (0.0, 0.65, 0.4)
        knob_r  = 18 * scale * SB_KNOB_SCALE
        knob_kx  = [rx + (KNOB_START_X + c*KNOB_SPACING) * scale for c in range(3)]
        body_top = ry
        body_bot = ry + rh - RACK_RAIL_H*scale
        mid_y    = (body_top + body_bot) * 0.5
        ky0      = mid_y + knob_r + 14*scale
        ky1      = mid_y - knob_r - 14*scale

        _sb_x_offsets = [SB_KNOB_X_COL0, SB_KNOB_X_COL1, SB_KNOB_X_COL2]
        _sb_y_offsets = [SB_KNOB_Y_ROW0, SB_KNOB_Y_ROW1]

        param_order = [0,1,5, 2,3,4]
        for idx, pi in enumerate(param_order):
            col_i = idx % 3
            row_i = idx // 3
            kx    = knob_kx[col_i] + _sb_x_offsets[col_i] * scale
            ky    = (ky0 if row_i == 0 else ky1) + _sb_y_offsets[row_i] * scale
            if pi < len(params):
                pkey, plabel, pmin, pmax, pdef, pfmt = params[pi]
                norm   = getattr(rack, f'p{pi}', 0.0)
                actual = pmin + norm*(pmax-pmin)
                try:    val_str = pfmt.format(actual)
                except: val_str = f"{actual:.1f}"
                _draw_knob(kx, ky, knob_r, norm, col, plabel, val_str, scale)

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

        # Glass overlay — composited on top of spectrum/GR meters, same rect as body PNG
        try:
            from ui.mixer.texture_cache import get_texture as _gtc_gl
            from ui.mixer.texture_cache import blit_texture as _blt_gl
            _gl_tex = _gtc_gl("rack_comp_single_glass")
            if _gl_tex:
                _blt_gl(_gl_tex, rx, ry, rw, rh, key="rack_comp_single_glass", blend="ALPHA")
        except Exception:
            pass

    # Rail background drawn AFTER body so it always sits on top of any body PNG.
    # Suppressed for racks that have a full PNG skin loaded (PNG provides the rail look).
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_rail
        _rail_skin_keys = {
            "COMP_MULTI":  "rack_comp_multi_bg",
            "COMP_SINGLE": "rack_comp_single_bg",
            "EQ":          "rack_eq_bg",
            "REVERB":      "rack_reverb_bg",
            "NOISE_GATE":  "rack_noisegate_bg",
            "DELAY":       "rack_delay_bg",
            "BOOSTER":     "rack_booster_bg",
            "MIXDOWN":     "rack_mixdown_bg",
        }
        _rsk = _rail_skin_keys.get(etype)
        if not (_rsk and _gtc_rail(_rsk) is not None):
            _draw_rect(rx, ry+rh-rail_h, rw, rail_h, (0.16, 0.16, 0.16, 1.0))
            _draw_rect(rx, ry+rh-rail_h-2*scale, rw, 2*scale, (0.1, 0.1, 0.1, 1.0))
    except Exception:
        _draw_rect(rx, ry+rh-rail_h, rw, rail_h, (0.16, 0.16, 0.16, 1.0))
        _draw_rect(rx, ry+rh-rail_h-2*scale, rw, 2*scale, (0.1, 0.1, 0.1, 1.0))

    # Channel buttons always on right
    ch_right_x = rx + rw - 100*scale
    ch_top_y   = ry + rh - RACK_RAIL_H*scale - 20*scale
    _draw_channel_buttons(ch_right_x, ch_top_y, rack, scale)
    fs_ch = max(1, int(8*scale))
    # --- TOP RAIL INTERACTIVE ELEMENTS (drawn last, always on top) ---

    # Corner screws — suppressed for racks with PNG skins that include them
    if etype not in ("COMP_MULTI", "COMP_SINGLE", "EQ", "REVERB", "NOISE_GATE", "DELAY", "BOOSTER", "MIXDOWN"):
        for sx2, sy2 in [(rx+14*scale, ry+rh-16*scale),
                         (rx+rw-14*scale, ry+rh-16*scale),
                         (rx+14*scale, ry+14*scale),
                         (rx+rw-14*scale, ry+14*scale)]:
            _draw_circle(sx2, sy2, 4*scale, (0.07,0.07,0.07,1.0))
            _draw_circle(sx2, sy2, 4*scale, (0.3,0.3,0.3,1.0), filled=False)
            _draw_line(sx2-3*scale, sy2, sx2+3*scale, sy2, (0.3,0.3,0.3,0.8))
            _draw_line(sx2, sy2-3*scale, sx2, sy2+3*scale, (0.3,0.3,0.3,0.8))

    # --- COLLAPSE ARROW — baked into background PNG, only position needed for hitbox ---
    col_btn_x = rx + 4*scale
    col_btn_y = ry + rh - 28*scale
    col_btn_w = 24*scale
    col_btn_h = 20*scale
    # rect, border and triangle suppressed — baked into background PNG

    # --- RACK NUMBER BADGE + EFFECT NAME ---
    etype   = rack.effect_type
    enames  = dict(EFFECT_TYPES)
    ename   = enames.get(etype, etype)
    fs_name = max(1, int(11*scale))

    badge_label = str(rack_idx + 1)
    badge_fs    = max(1, int(13*scale))
    badge_x     = col_btn_x + col_btn_w + 4*scale
    badge_y     = ry + rh - 28*scale
    badge_w     = max(22*scale, _text_width(badge_label, badge_fs) + 12*scale)
    badge_h     = 20*scale

    # Badge rect, border and dropdown arrow suppressed — baked into background PNG
    # Only draw the number text
    tw_b = _text_width(badge_label, badge_fs)
    _draw_text(badge_label, badge_x + badge_w/2 - tw_b/2,
               badge_y + badge_h/2 - badge_fs/2,
               badge_fs, (0.35, 0.7, 1.0, 1.0))
    badge_w = badge_w + 6*scale

    if etype not in ("COMP_MULTI", "COMP_SINGLE", "EQ", "REVERB", "NOISE_GATE", "DELAY", "BOOSTER", "MIXDOWN"):  # suppressed — label baked into background PNG
        _draw_text(ename.upper(),
                   badge_x + badge_w,
                   ry+rh-22*scale, fs_name, (0.75,0.75,0.75,1.0))

    # --- PRESET SELECTOR ---
    presets   = PRESETS.get(etype, ["Default"])
    p_idx     = rack.preset_idx % max(1, len(presets))
    p_name    = presets[p_idx]
    p_box_w   = 160*scale
    p_box_x   = rx + (rw - p_box_w) / 2.0 + PRESET_BOX_CENTER_X_OFFSET*scale
    p_box_y   = ry + rh - 26*scale
    p_box_h   = 16*scale

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

    rax = p_box_x + p_box_w + 4*scale
    ray = ry + rh - 18*scale
    ra  = [(rax+10*scale, ray), (rax, ray+6*scale), (rax, ray-6*scale)]
    batch = batch_for_shader(shader,"TRIS",{"pos":ra})
    shader.uniform_float("color",(0.4,0.4,0.4,1.0)); batch.draw(shader)

    # --- ON/OFF + CLOSE BUTTONS (PNG) ---
    # RackOff.png (100×38px source): OFF button + close X — always drawn when expanded
    # RackOn.png  (100×38px source): ON button only — drawn on top if enabled
    _btn_w = RACK_BTN_W * scale
    _btn_h = RACK_BTN_H * scale
    _btn_x = rx + rw - _btn_w + RACK_BTN_X_OFFSET * scale
    _btn_y = ry + rh - (RACK_RAIL_H * scale + _btn_h) / 2 + RACK_BTN_Y_OFFSET * scale
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_btn
        from ui.mixer.texture_cache import blit_texture as _blt_btn
        _tex_off = _gtc_btn("rack_btn_off")
        if _tex_off:
            _blt_btn(_tex_off, _btn_x, _btn_y, _btn_w, _btn_h, key="rack_btn_off")
        else:
            # GPU fallback — delete button
            _draw_rect(del_x := rx+rw-26*scale, del_y := ry+rh-27*scale, 18*scale, 16*scale, (0.18,0.04,0.04,1.0))
            fs_del = max(1, int(9*scale)); tw_del = _text_width("X", fs_del)
            _draw_text("X", del_x+9*scale-tw_del/2, del_y+8*scale-fs_del/2+1, fs_del, (0.8,0.15,0.15,1.0))
            # on/off fallback
            _ox = rx+rw-68*scale; _oy = ry+rh-27*scale
            _draw_rect(_ox, _oy, 40*scale, 16*scale,
                       (0.0,0.13,0.0,1.0) if rack.enabled else (0.13,0.0,0.0,1.0))
        if rack.enabled:
            _tex_on = _gtc_btn("rack_btn_on")
            if _tex_on:
                _blt_btn(_tex_on, _btn_x, _btn_y, _btn_w, _btn_h, key="rack_btn_on")
    except Exception:
        pass

    # Debug outlines — green = ON/OFF hitbox, red = close X hitbox
    if RACK_BTN_DEBUG:
        try:
            _sd = _get_shader()
            _split = RACK_BTN_ONOFF_SPLIT
            _hbw = RACK_HB_W * scale
            _hbh = RACK_HB_H * scale
            _hbx = rx + rw - _hbw + RACK_HB_X_OFFSET * scale
            _hby = ry + rh - (RACK_RAIL_H * scale + _hbh) / 2 + RACK_HB_Y_OFFSET * scale
            # ON/OFF zone (left portion)
            _ox1, _ox2 = _hbx, _hbx + _hbw * _split
            _oy1, _oy2 = _hby, _hby + _hbh
            _sd.bind(); _sd.uniform_float("color", (0.0, 1.0, 0.0, 1.0))
            batch_for_shader(_sd, "LINE_STRIP", {"pos": [
                (_ox1,_oy1),(_ox2,_oy1),(_ox2,_oy2),(_ox1,_oy2),(_ox1,_oy1)]}).draw(_sd)
            # Close X zone (right portion)
            _cx1, _cx2 = _hbx + _hbw * _split, _hbx + _hbw
            _sd.uniform_float("color", (1.0, 0.0, 0.0, 1.0))
            batch_for_shader(_sd, "LINE_STRIP", {"pos": [
                (_cx1,_oy1),(_cx2,_oy1),(_cx2,_oy2),(_cx1,_oy2),(_cx1,_oy1)]}).draw(_sd)
        except Exception:
            pass
    on_h  = 16*scale

    if etype not in ("COMP_MULTI", "COMP_SINGLE", "EQ", "REVERB", "NOISE_GATE", "DELAY", "BOOSTER", "MIXDOWN"):
        _draw_text("CHANNELS", ch_right_x + 10*scale,
                   ch_top_y + 8*scale, fs_ch, (0.35,0.35,0.35,1.0))



def _draw_rack_collapsed(rx, ry, rack, rack_idx, scale, rack_width=None):
    """Draw a collapsed rack unit — single row.

    Layout (left → right):
      [screws] [▶ expand arrow] [badge#] [EFFECT NAME]  [CH badges…]  [ON/OFF] [X]
    Right side buttons are right-aligned; channel badges sit just left of them.
    No preset name here — that's expanded-only, see _draw_rack_expanded.
    """
    _rs = _get_racks_state()
    EFFECT_TYPES      = _rs['EFFECT_TYPES']

    rw = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh = RACK_COLLAPSED_H * scale
    cy = ry + rh / 2   # vertical centre
    etype = rack.effect_type

    # Background — per-effect-type PNG skin (see _COLLAPSED_BG_KEY above).
    # Falls back to the old flat rect + border for any type without art yet.
    _collapsed_bg_drawn = False
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_cbg
        from ui.mixer.texture_cache import blit_texture as _blt_cbg
        _cbg_key = _COLLAPSED_BG_KEY.get(etype)
        _cbg_tex = _gtc_cbg(_cbg_key) if _cbg_key else None
        if _cbg_tex:
            _blt_cbg(_cbg_tex, rx, ry, rw, rh, key=_cbg_key)
            _collapsed_bg_drawn = True
    except Exception:
        _collapsed_bg_drawn = False
    if not _collapsed_bg_drawn:
        _draw_rect(rx, ry, rw, rh, (0.1, 0.1, 0.1, 1.0))
        shader = _get_shader()
        verts  = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
        batch  = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
        shader.bind()
        shader.uniform_float("color",(0.25,0.25,0.25,1.0))
        batch.draw(shader)

    # Corner screws suppressed — baked into background PNG

    # Expand arrow suppressed — baked into background PNG

    # Badge number only — rect, border and effect name suppressed (baked into PNG)
    badge_fs    = max(1, int(12*scale))
    badge_label = str(rack_idx + 1)
    badge_x     = rx + 38*scale
    _draw_text(badge_label, badge_x, cy - badge_fs/2,
               badge_fs, (0.35, 0.7, 1.0, 1.0))

    # ── Right-side controls — same pixel sizes as expanded rack, centred in row ──
    # Expanded rack reference: X = 18×16, ON/OFF = 40×16
    btn_h    = 16*scale   # matches expanded rail button height exactly
    btn_y    = cy - btn_h / 2

    # [ON/OFF + CLOSE] PNG buttons — same PNGs as expanded, centred in collapsed rail
    _btn_w = RACK_BTN_W * scale
    _btn_h = RACK_BTN_H * scale
    _btn_x = rx + rw - _btn_w + RACK_BTN_X_OFFSET * scale
    _btn_y = cy - _btn_h / 2  + RACK_BTN_Y_OFFSET * scale
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_cbtn
        from ui.mixer.texture_cache import blit_texture as _blt_cbtn
        _tex_coff = _gtc_cbtn("rack_btn_off")
        if _tex_coff:
            _blt_cbtn(_tex_coff, _btn_x, _btn_y, _btn_w, _btn_h, key="rack_btn_off")
        else:
            # GPU fallback
            _draw_rect(rx+rw-22*scale, btn_y := cy-8*scale, 18*scale, 16*scale, (0.18,0.04,0.04,1.0))
            fs_xf = max(1, int(9*scale)); tw_xf = _text_width("X", fs_xf)
            _draw_text("X", rx+rw-13*scale-tw_xf/2, cy-fs_xf/2+1, fs_xf, (0.8,0.15,0.15,1.0))
            _onx = rx+rw-66*scale
            _draw_rect(_onx, cy-8*scale, 40*scale, 16*scale,
                       (0.0,0.13,0.0,1.0) if rack.enabled else (0.13,0.0,0.0,1.0))
        if rack.enabled:
            _tex_con = _gtc_cbtn("rack_btn_on")
            if _tex_con:
                _blt_cbtn(_tex_con, _btn_x, _btn_y, _btn_w, _btn_h, key="rack_btn_on")
    except Exception:
        pass

    # Debug outlines — green = ON/OFF hitbox, red = close X hitbox
    if RACK_BTN_DEBUG:
        try:
            _sd = _get_shader()
            _split = RACK_BTN_ONOFF_SPLIT
            _hbw = RACK_HB_W * scale
            _hbh = RACK_HB_H * scale
            _hbx = rx + rw - _hbw + RACK_HB_X_OFFSET * scale
            _hby = cy - _hbh / 2 + RACK_HB_Y_OFFSET * scale
            _ox1, _ox2 = _hbx, _hbx + _hbw * _split
            _oy1, _oy2 = _hby, _hby + _hbh
            _sd.bind(); _sd.uniform_float("color", (0.0, 1.0, 0.0, 1.0))
            batch_for_shader(_sd, "LINE_STRIP", {"pos": [
                (_ox1,_oy1),(_ox2,_oy1),(_ox2,_oy2),(_ox1,_oy2),(_ox1,_oy1)]}).draw(_sd)
            _cx1, _cx2 = _hbx + _hbw * _split, _hbx + _hbw
            _sd.uniform_float("color", (1.0, 0.0, 0.0, 1.0))
            batch_for_shader(_sd, "LINE_STRIP", {"pos": [
                (_cx1,_oy1),(_cx2,_oy1),(_cx2,_oy2),(_cx1,_oy2),(_cx1,_oy1)]}).draw(_sd)
        except Exception:
            pass

    # Channel LED strip — left of ON/OFF. All 9 channels always shown (not
    # just the ones this rack uses), each a small LED with its channel number
    # above it. Channels this rack IS using blink between the off/on LED skins
    # to show they're live routing; unused channels sit on "off". This is a
    # simple assignment blink, independent of playback — see
    # update_led_states()/_led_states in Racks.py for the real audio-activity
    # indicator used on the expanded rack's channel buttons.
    import time as _time_led
    _flash_on = (_time_led.time() * RACK_COLLAPSED_LED_FLASH_HZ) % 1.0 < 0.5

    fs_led  = max(1, int(9*scale))
    led_w   = RACK_COLLAPSED_LED_W * scale
    led_h   = RACK_COLLAPSED_LED_H * scale
    led_gap = RACK_COLLAPSED_LED_GAP * scale
    num_gap = RACK_COLLAPSED_LED_NUM_GAP * scale

    unit_h  = fs_led + num_gap + led_h   # number + gap + LED, stacked
    led_y   = cy - unit_h / 2            # bottom of LED — whole unit centred on cy
    num_y   = led_y + led_h + num_gap    # baseline for the channel number, above the LED

    group_offset = getattr(rack, 'group_idx', 0) * 9
    ch_right = _btn_x - 6*scale
    strip_w  = 9*led_w + 8*led_gap
    strip_x0 = ch_right - strip_w

    try:
        from ui.mixer.texture_cache import get_texture as _gtc_led
        from ui.mixer.texture_cache import blit_texture as _blt_led
    except Exception:
        _gtc_led = None
        _blt_led = None

    for local_idx in range(9):
        led_x   = strip_x0 + local_idx * (led_w + led_gap)
        is_used = getattr(rack, f'ch{local_idx}', False)
        show_on = is_used and _flash_on
        led_key = "rack_collapsed_ch_led_on" if show_on else "rack_collapsed_ch_led_off"

        _led_tex = _gtc_led(led_key) if _gtc_led else None
        if _led_tex:
            _blt_led(_led_tex, led_x, led_y, led_w, led_h, key=led_key)
        else:
            # Fallback until the LED PNGs are dropped in — dim grey / green dot
            fb_col = (0.0, 0.9, 0.4, 1.0) if show_on else (0.15, 0.15, 0.15, 1.0)
            _draw_circle(led_x + led_w/2, led_y + led_h/2, led_w/2, fb_col)

        label   = str(group_offset + local_idx + 1)
        tw      = _text_width(label, fs_led)
        num_col = (0.75, 0.75, 0.75, 1.0) if is_used else (0.35, 0.35, 0.35, 1.0)
        _draw_text(label, led_x + led_w/2 - tw/2, num_y, fs_led, num_col)

    # Preset name — collapsed rows have no preset box/arrows to attach it to,
    # so it's expanded-rack-only. See _draw_rack_expanded's PRESET SELECTOR
    # section for the live preset display.


def _draw_add_rack_button(rx, ry, scale, rack_width=None):
    """Draw the + ADD RACK EFFECT button."""
    rw  = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh  = 28*scale
    fs  = max(1, int(10*scale))

    # PNG skin — brushed-metal button art. Unlike add_ai_rack_btn.png, the
    # label is NOT baked into this art, so it's drawn on top every time,
    # whether the PNG or the flat-rect fallback is used underneath.
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_addbtn
        from ui.mixer.texture_cache import blit_texture as _blt_addbtn
        _addbtn_tex = _gtc_addbtn("add_rack_btn")
    except Exception:
        _addbtn_tex = None

    if _addbtn_tex:
        _blt_addbtn(_addbtn_tex, rx, ry, rw, rh, key="add_rack_btn")
        label_col = (0.78, 0.78, 0.78, 1.0)   # light grey — reads on brushed metal
    else:
        _draw_rect(rx, ry, rw, rh, (0.07, 0.07, 0.07, 1.0))

        shader = _get_shader()
        border_verts = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
        batch = batch_for_shader(shader,"LINE_STRIP",{"pos":border_verts})
        shader.bind()
        shader.uniform_float("color",(0.2,0.2,0.2,1.0))
        batch.draw(shader)
        label_col = (0.3, 0.3, 0.3, 1.0)      # dim grey — matches old flat-rect look

    label = "+  ADD RACK EFFECT"
    tw    = _text_width(label, fs)
    _draw_text(label, rx + rw/2 - tw/2,
               ry + rh/2 - fs/2, fs, label_col)


def _draw_add_popup(px, py, scale):
    """Draw the effect type selector popup."""
    _rs = _get_racks_state()
    EFFECT_TYPES = _rs['EFFECT_TYPES']
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
    shader = _get_shader()
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