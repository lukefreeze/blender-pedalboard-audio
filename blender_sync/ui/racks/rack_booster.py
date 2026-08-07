# =============================================================================
# ui/racks/rack_booster.py
# THE BOOSTER!! — Volume amplification beyond Blender's strip.volume=100 cap.
#
# p0 = boost_norm   (0.0 → 0dB,  1.0 → 40dB)
# p1 = limiter_on   (> 0.5 = on)
# p2 = target_ch    (0 = auto, 1–32 = specific VSE channel)
#
# Column layout (same widths as original):
#   Left   (25%):  big boost knob — unchanged geometry
#   Centre (50%):  IN meter / OUT preview meter / preset grid / apply+limiter
#   Right  (25%):  top section: clear (channel assignment buttons drawn by rack_base)
#                  bottom section: output channel stepper + status dot
#
# Channel assignment buttons are drawn by rack_base.py at rx+rw-100*scale.
# They occupy the TOP-RIGHT of the body (approx top 110*scale of body height).
# Our right column content sits in the lower portion, clear of that overlap.
# =============================================================================

import math
import time
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



try:
    from ui.mixer.draw_utils import (
        draw_rect   as _draw_rect,
        draw_line   as _draw_line,
        draw_circle as _draw_circle,
        draw_text   as _draw_text,
        text_width  as _text_width,
        draw_knob   as _draw_knob,
    )
except ImportError:
    pass

RACK_RAIL_H = 32

# =============================================================================
# BOOSTER LAYOUT TUNING — all values unscaled px, multiplied by scale at draw time.
# Every section has fully INDEPENDENT position + size constants.
# Nothing derives from another section — change one without affecting others.
# Y values are measured from body_bot (bottom of rack body area).
# Positive Y = up. Positive X = right.
# Set any value to 0 to use the computed default.
# =============================================================================
# BOOSTER LAYOUT TUNING — all values unscaled px, same pattern as other racks.
# Positive = right/up/bigger. Negative = left/down/smaller.
# =============================================================================
BST_KNOB_SCALE = 0.75
BST_KNOB_X     = -12.5
BST_KNOB_Y     = 0.0
BST_KNOB_VAL_Y = -40.0   # move +6.0 dB text down (negative) or up (positive)
BST_KNOB_VAL_X = 10.0     # move +6.0 dB text left (negative) or right (positive)
BST_KNOB_FS    = 12.0    # font size for +6.0 dB text (0 = default 8pt)

# IN meter bar — position and size within the centre column
BST_IN_X       = 30.0   # nudge left/right
BST_IN_Y       = 3.0   # nudge up/down
BST_IN_W       = -62.0   # adjust width  (negative = narrower)
BST_IN_H       = -20.0   # adjust height (negative = shorter)

# OUT meter bar
BST_OUT_X      = 30.0
BST_OUT_Y      = 9.5
BST_OUT_W      = -62.0
BST_OUT_H      = -20.0

# LED meter constants (booster_Green__LED.png / booster_Orange__LED.png, 17x43px each)
# LEDs tile horizontally across the meter bar.
BST_LED_GAP      = 2.0    # gap between each LED in unscaled px
BST_LED_W        = 0.0    # LED width override in unscaled px (0 = auto-fit to bar height)
# IN meter LED position nudge
BST_IN_LED_X     = 0.0
BST_IN_LED_Y     = 0.0
# OUT meter LED position nudge
BST_OUT_LED_X    = 0.0
BST_OUT_LED_Y    = 0.0
BST_GRID_X     = 27.0   # nudge entire grid left/right
BST_GRID_Y     = -18.0   # nudge entire grid up/down
BST_GRID_W     = -50.0   # adjust total grid width
BST_GRID_H     = 15.0   # adjust total grid height
# Active preset button PNG (booster__button.png, 282x70px)
BST_BTN_ON_SCALE = 1.05   # width multiplier (1.0 = matches button area exactly)
BST_BTN_ON_SCALE_H = 1.2  # height multiplier — adjust independently of width
BST_BTN_ON_X     = 0.0   # nudge left/right (unscaled px)
BST_BTN_ON_Y     = 0.0   # nudge up/down   (unscaled px)
# Per-button nudges — [+6, +12, +18, +24, +30, +40]
BST_BTN_X      = [-3.0, 2.0, 7.0, -3.0, 2.0, 7.0]
BST_BTN_Y      = [0.0, 0.0, 0.0, 6.0, 6.0, 6.0]
BST_BTN_W      = [-7.0, -7.0, -7.0, -7.0, -7.0, -7.0]  # per-button width adjust
BST_BTN_H      = [-5.0, -5.0, -5.0, -5.0, -5.0, -5.0]  # per-button height adjust

# Apply + Limiter row
BST_APPLY_X    = 23.0
BST_APPLY_Y    = 8.0
BST_APPLY_W    = -55.0
BST_APPLY_H    = -8.0
BST_LIM_W      = 85.0  # limiter button width (unscaled px)
BST_LIM_X      = 9.0   # nudge limiter button left/right (relative to its auto position)
BST_LIM_Y      = -5.0   # nudge limiter button up/down
BST_LIM_H      = 8.0   # adjust limiter button height independently of apply button

# Output channel stepper
BST_STEPPER_X  = -5
BST_STEPPER_Y  = 6.0
BST_STEPPER_W  = 6.0   # adjust stepper width
BST_STEPPER_H  = -6.0     # adjust stepper height

# Font sizes — unscaled pt, multiplied by scale at draw time
BST_FS_GRID    = 12.0    # preset grid button labels (+6, +12 etc.)
BST_FS_APPLY   = 15.0    # apply button text (▶ APPLY BOOST etc.)
BST_FS_LIM     = 14.0    # limiter button text (LIMIT: ON / LIMIT: OFF)
BST_FS_STEPPER = 12.0    # channel stepper display (auto / ch N)
BST_FS_STEPPER_LBL = 7.0  # "Output ch" label above stepper

# Channel buttons drawn by rack_base occupy approx the top 110px of body (at scale=1)
# Our right column must stay below: body_top - 115*scale
_CH_CLEAR_TOP = 115   # px at scale=1 to keep clear from body_top downward

_BG        = (0.05,  0.04,  0.02,  1.0)
_BORDER    = (0.35,  0.22,  0.04,  1.0)
_AMBER     = (0.95,  0.60,  0.08,  1.0)
_AMBER_DIM = (0.40,  0.25,  0.04,  1.0)
_GREEN     = (0.15,  0.85,  0.35,  1.0)
_RED       = (0.90,  0.15,  0.10,  1.0)
_TEXT_DIM  = (0.45,  0.32,  0.08,  1.0)
_METER_BG  = (0.03,  0.02,  0.01,  1.0)


def _tanh_limit(x):
    if abs(x) <= 1.0:
        return x
    s = 1.0 if x >= 0 else -1.0
    return s * math.tanh(abs(x))


def _level_to_dbfs(level):
    if level <= 0.0001:
        return "-inf"
    db = 20.0 * math.log10(max(level, 0.0001))
    return f"{db:+.1f}"


def _draw_vu_bar(shader, bx, by, bw, bh, level, peak, is_output=False,
                 led_x_offset=0.0, led_y_offset=0.0):
    """Draw a horizontal LED meter bar using PNG LED tiles.

    LEDs tile left-to-right across the bar width. Unlit LEDs are skipped
    (transparent — the baked background shows through). Peak line kept.
    led_x_offset / led_y_offset: unscaled-px nudge already multiplied by scale.
    """
    # LED sizing — height fills the bar, width preserves the LED's 17:43 aspect ratio
    led_h = bh
    _led_w_auto = led_h * (17.0 / 43.0)   # 17x43 aspect ratio
    led_w = BST_LED_W if BST_LED_W > 0 else _led_w_auto
    led_gap = BST_LED_GAP   # already unscaled; caller multiplies by scale externally
    # Note: BST_LED_GAP is unscaled px — we need scale here, but _draw_vu_bar
    # doesn't receive scale. Use a module-level ref via the closure.
    # Instead, treat BST_LED_GAP as already-in-screen-px relative to current bh.
    # A simpler approach: gap is a fraction of led_w.
    stride = led_w + led_gap   # px per LED slot

    if stride <= 0:
        return

    n_leds = max(1, int(bw / stride))
    lit_count = int(min(level, 1.0) * n_leds)

    # Pick LED texture key based on meter type
    led_key = "rack_booster_led_orange" if is_output else "rack_booster_led_green"

    try:
        from ui.mixer.texture_cache import get_texture as _gtc_led
        from ui.mixer.texture_cache import blit_texture as _blt_led
        led_tex = _gtc_led(led_key)
    except Exception:
        led_tex = None

    _ox = bx + led_x_offset
    _oy = by + led_y_offset

    for i in range(lit_count):
        lx = _ox + i * stride
        if led_tex:
            _blt_led(led_tex, lx, _oy, led_w, led_h, key=led_key)
        else:
            # Fallback — solid colour rect
            col = (_RED if i / n_leds > 0.90 else
                   (_AMBER if i / n_leds > 0.70 else
                    ((0.90, 0.55, 0.05, 1.0) if is_output else (0.12, 0.70, 0.25, 1.0))))
            _draw_rect(lx, _oy, led_w, led_h, col)

    # Peak line — kept as-is
    if peak > 0.001:
        px = bx + min(peak, 1.0) * bw - 1
        peak_col = _RED if peak > 0.90 else (_AMBER if peak > 0.70 else _GREEN)
        pb = batch_for_shader(shader, "LINES",
                              {"pos": [(px, by+1), (px, by+bh-1)]})
        shader.bind(); shader.uniform_float("color", peak_col); pb.draw(shader)


def _get_live_levels(rack):
    try:
        import Racks as _rk
        assigned = list(_rk.get_rack_channels(rack))
        if not assigned:
            return 0.0, 0.0
        ch_idx = assigned[0]
        from core.meters import _engine_levels, _peak_hold
        rms  = _engine_levels[ch_idx] if ch_idx < len(_engine_levels) else 0.0
        peak = _peak_hold[ch_idx]     if ch_idx < len(_peak_hold)     else 0.0
        return rms, peak
    except Exception:
        return 0.0, 0.0


def _get_free_channels():
    try:
        scene = bpy.context.scene
        if not scene or not scene.sequence_editor:
            return []
        used = {s.channel for s in scene.sequence_editor.sequences_all
                if s.type == "SOUND" and s.sound}
        return [ch for ch in range(1, 33) if ch not in used][:5]
    except Exception:
        return []


def _draw_booster_body(rx, ry, rw, rh, rack, rack_idx, scale):
    boost_norm = getattr(rack, 'p0', 0.30)
    limiter_on = getattr(rack, 'p1', 1.0) > 0.5
    target_ch  = int(getattr(rack, 'p2', 0))
    boost_db   = boost_norm * 40.0
    boost_str  = f"+{boost_db:.1f} dB"
    gain_lin   = 10.0 ** (boost_db / 20.0)

    status = getattr(rack, 'ai_status', 'READY')
    if status not in ('READY', 'PROCESSING', 'DONE', 'ERROR', 'NO_CHANNEL'):
        status = 'READY'

    in_rms, in_peak = _get_live_levels(rack)
    out_rms  = _tanh_limit(in_rms  * gain_lin) if limiter_on else min(in_rms  * gain_lin, 1.0)
    out_peak = _tanh_limit(in_peak * gain_lin) if limiter_on else min(in_peak * gain_lin, 1.0)

    rail_h   = RACK_RAIL_H * scale
    body_bot = ry
    body_top = ry + rh - rail_h
    body_h   = body_top - body_bot
    shader   = _get_shader()

    # ── Skin background — full rack height (rail/title now baked into PNG)
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_bs
        from ui.mixer.texture_cache import blit_texture as _blt_bs
        _bs_tex = _gtc_bs("rack_booster_bg")
        if _bs_tex:
            _blt_bs(_bs_tex, rx, ry, rw, rh, key="rack_booster_bg")
        else:
            _draw_rect(rx, body_bot, rw, body_h, _BG)
    except Exception:
        _draw_rect(rx, body_bot, rw, body_h, _BG)
    # Border suppressed — baked into background PNG

    # Original column widths — unchanged from first working version
    left_w   = rw * 0.25
    centre_w = rw * 0.50
    centre_x = rx + left_w
    right_x  = rx + left_w + centre_w

    # Dividers suppressed — baked into background PNG

    # ── LEFT: Big boost knob — original geometry ──────────────────────────────
    knob_cx = rx + left_w * 0.5 + BST_KNOB_X * scale
    knob_cy = body_bot + body_h * 0.52 + BST_KNOB_Y * scale
    knob_r  = min(left_w * 0.30, body_h * 0.35) * BST_KNOB_SCALE
    # "BOOST" label suppressed — baked into background PNG
    # Draw knob with empty label; value text drawn manually below for X control
    _draw_knob(knob_cx, knob_cy, knob_r,
               boost_norm, (_AMBER[0], _AMBER[1], _AMBER[2]),
               "", "", scale)
    # +6.0 dB value text — position and size controlled by BST_KNOB_VAL_* constants
    _fs_val = max(1, int((BST_KNOB_FS if BST_KNOB_FS > 0 else 8) * scale))
    _tw_val = _text_width(boost_str, _fs_val)
    _draw_text(boost_str,
               knob_cx - _tw_val/2 + BST_KNOB_VAL_X * scale,
               knob_cy - knob_r - _fs_val * 2 - 4*scale + BST_KNOB_VAL_Y * scale,
               _fs_val, (0.8, 0.8, 0.8, 1.0))

    # ── CENTRE + APPLY ────────────────────────────────────────────────────────
    # All elements baseline from centre_x/centre_w — same as other racks.
    # BST_* constants nudge/resize each element independently.
    _cx   = centre_x          # left edge of centre column
    _cw   = centre_w          # width of centre column
    _pad  = 8 * scale         # inner padding
    fs_lbl = max(1, int(7*scale))
    fs_db  = max(1, int(8*scale))

    # IN meter — sits in upper portion of body
    in_bx    = _cx + _pad            + BST_IN_X * scale
    in_bw    = _cw - _pad*2          + BST_IN_W * scale
    in_bh    = body_h * 0.15         + BST_IN_H * scale
    in_bar_y = body_bot + body_h*0.73 + BST_IN_Y * scale
    in_lbl_y = in_bar_y + in_bh + 2*scale

    # "IN" label suppressed — baked into background PNG
    in_db_str = _level_to_dbfs(in_rms)
    _draw_text(in_db_str, in_bx + in_bw - _text_width(in_db_str, fs_db),
               in_lbl_y, fs_db,
               _GREEN if in_rms < 0.7 else (_AMBER if in_rms < 0.9 else _RED))
    _draw_vu_bar(shader, in_bx, in_bar_y, in_bw, in_bh, in_rms, in_peak, False,
                 led_x_offset=BST_IN_LED_X*scale, led_y_offset=BST_IN_LED_Y*scale)

    # OUT meter — sits below IN meter
    out_bx    = _cx + _pad            + BST_OUT_X * scale
    out_bw    = _cw - _pad*2          + BST_OUT_W * scale
    out_bh    = body_h * 0.15         + BST_OUT_H * scale
    out_bar_y = body_bot + body_h*0.54 + BST_OUT_Y * scale
    out_lbl_y = out_bar_y + out_bh + 2*scale

    # "OUT (est.)" label suppressed — baked into background PNG
    out_db_str = _level_to_dbfs(out_rms)
    _draw_text(out_db_str, out_bx + out_bw - _text_width(out_db_str, fs_db),
               out_lbl_y, fs_db,
               _GREEN if out_rms < 0.7 else (_AMBER if out_rms < 0.9 else _RED))
    _draw_vu_bar(shader, out_bx, out_bar_y, out_bw, out_bh, out_rms, out_peak, True,
                 led_x_offset=BST_OUT_LED_X*scale, led_y_offset=BST_OUT_LED_Y*scale)

    # Preset grid — 6 buttons 3 cols x 2 rows, sits between meters and apply row
    grid_x   = _cx + _pad            + BST_GRID_X * scale
    grid_w   = _cw - _pad*2          + BST_GRID_W * scale
    grid_bot = body_bot + body_h*0.28 + BST_GRID_Y * scale
    grid_h   = body_h * 0.22         + BST_GRID_H * scale
    _btn_w_base = (grid_w - 2*3*scale) / 3
    _btn_h_base = (grid_h - 3*scale)   / 2
    fs_pre   = max(1, int(BST_FS_GRID*scale))
    presets  = [("+6",  6/40), ("+12", 12/40), ("+18", 18/40),
                ("+24", 24/40), ("+30", 30/40), ("+40", 1.0)]
    for pi, (lbl, norm) in enumerate(presets):
        col_i = pi % 3
        row_i = pi // 3
        btn_w = _btn_w_base + BST_BTN_W[pi] * scale
        btn_h = _btn_h_base + BST_BTN_H[pi] * scale
        bx_p  = grid_x + col_i * (_btn_w_base + 3*scale) + BST_BTN_X[pi] * scale
        by_p  = grid_bot + row_i * (_btn_h_base + 3*scale) + BST_BTN_Y[pi] * scale
        active = abs(boost_norm - norm) < 0.015
        col_p = _AMBER if active else _AMBER_DIM
        # Active state — blit PNG behind text
        if active:
            try:
                from ui.mixer.texture_cache import get_texture as _gtc_bb
                from ui.mixer.texture_cache import blit_texture as _blt_bb
                _bb_tex = _gtc_bb("rack_booster_btn_on")
                if _bb_tex:
                    _bb_w = btn_w * BST_BTN_ON_SCALE
                    _bb_h = btn_h * BST_BTN_ON_SCALE_H
                    _bb_x = bx_p + (btn_w - _bb_w)/2 + BST_BTN_ON_X * scale
                    _bb_y = by_p + (btn_h - _bb_h)/2 + BST_BTN_ON_Y * scale
                    _blt_bb(_bb_tex, _bb_x, _bb_y, _bb_w, _bb_h, key="rack_booster_btn_on")
            except Exception:
                pass
        # label text on top
        tw = _text_width(lbl, fs_pre)
        _draw_text(lbl, bx_p+btn_w/2-tw/2, by_p+btn_h/2-fs_pre/2, fs_pre, col_p)

    # Apply + Limiter row — sits at bottom of centre column
    apply_x  = _cx + _pad            + BST_APPLY_X * scale
    apply_y  = body_bot + body_h*0.06 + BST_APPLY_Y * scale
    ctrl_h   = body_h * 0.10         + BST_APPLY_H * scale
    lim_w    = BST_LIM_W * scale
    apply_w  = _cw - _pad*2          + BST_APPLY_W * scale - lim_w - 6*scale
    lim_x    = apply_x + apply_w + 6*scale + BST_LIM_X * scale
    lim_y    = apply_y                      + BST_LIM_Y * scale
    lim_h    = ctrl_h                       + BST_LIM_H * scale

    # Apply button
    if status == 'PROCESSING':
        pulse  = 0.5 + 0.5 * math.sin(time.time() * 5.0)
        ap_bg  = (0.18, 0.10, 0.01, 1.0)
        ap_col = (_AMBER[0], _AMBER[1]*0.5 + 0.5*pulse, _AMBER[2], 1.0)
        ap_lbl = "BOOSTING..."
    elif status == 'DONE':
        ap_bg  = (0.02, 0.12, 0.05, 1.0); ap_col = _GREEN; ap_lbl = "DONE  (apply again)"
    elif status == 'ERROR':
        ap_bg  = (0.14, 0.02, 0.02, 1.0); ap_col = _RED;   ap_lbl = "ERROR — RETRY"
    elif status == 'NO_CHANNEL':
        ap_bg  = (0.08, 0.06, 0.01, 1.0); ap_col = _AMBER_DIM; ap_lbl = "NO CHANNEL"
    else:
        ap_bg  = (0.15, 0.09, 0.01, 1.0); ap_col = _AMBER; ap_lbl = "▶  APPLY BOOST"

    # rect and border suppressed — baked into background PNG
    fs_ap = max(1, int(BST_FS_APPLY*scale))
    tw_ap = _text_width(ap_lbl, fs_ap)
    _draw_text(ap_lbl, apply_x+apply_w/2-tw_ap/2, apply_y+ctrl_h/2-fs_ap/2, fs_ap, ap_col)

    # Limiter toggle
    lim_bg  = (0.02, 0.10, 0.04, 1.0) if limiter_on else (0.08, 0.05, 0.01, 1.0)
    lim_col = _GREEN if limiter_on else _AMBER_DIM
    lim_lbl = "LIMIT: ON" if limiter_on else "LIMIT: OFF"
    # rect and border suppressed — baked into background PNG
    fs_lim = max(1, int(BST_FS_LIM*scale))
    tw_lim = _text_width(lim_lbl, fs_lim)
    _draw_text(lim_lbl, lim_x+lim_w/2-tw_lim/2, lim_y+lim_h/2-fs_lim/2, fs_lim, lim_col)

    # ── RIGHT: Output channel stepper ────────────────────────────────────────
    _rpad  = 6 * scale
    _rw_base = left_w - _rpad * 2
    rw2    = _rw_base             + BST_STEPPER_W * scale
    rx2    = right_x + _rpad      + BST_STEPPER_X * scale
    fs_r   = max(1, int(BST_FS_STEPPER_LBL*scale))
    fs_ch  = max(1, int(BST_FS_STEPPER*scale))
    stepper_h = body_h * 0.08 + BST_STEPPER_H * scale
    stepper_y = apply_y + ctrl_h + 6*scale + BST_STEPPER_Y * scale
    ch_lbl_y   = stepper_y + stepper_h + 3*scale

    # "Output ch" label suppressed — baked into background PNG

    # Stepper box, border and +/- suppressed — baked into background PNG
    ch_display = f"ch {target_ch}" if target_ch > 0 else "auto"
    _draw_text(ch_display,
               rx2 + rw2/2 - _text_width(ch_display, fs_ch)/2,
               stepper_y + stepper_h/2 - fs_ch/2, fs_ch, _AMBER)

    # Free channels hint below stepper
    free = _get_free_channels()
    if free:
        free_str = "free: " + " ".join(str(c) for c in free)
        _draw_text(free_str, rx2, stepper_y - fs_r - 3*scale, fs_r, _TEXT_DIM)

    # Status dot at bottom of right column
    dot_cols = {
        'READY':      _AMBER_DIM, 'PROCESSING': _AMBER,
        'DONE':       _GREEN,     'ERROR':      _RED,
        'NO_CHANNEL': (0.25, 0.25, 0.25, 1.0),
    }
    status_y = body_bot + 4*scale + 13*scale
    _draw_circle(rx2 + 4*scale, status_y, 4*scale, dot_cols.get(status, _AMBER_DIM))
    st_lbl = status.replace('_', ' ')
    _draw_text(st_lbl, rx2 + 12*scale, status_y - fs_r/2, fs_r,
               dot_cols.get(status, _AMBER_DIM))