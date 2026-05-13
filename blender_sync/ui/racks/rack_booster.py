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


def _draw_vu_bar(shader, bx, by, bw, bh, level, peak, is_output=False):
    _draw_rect(bx, by, bw, bh, _METER_BG)
    outline = [(bx, by), (bx+bw, by), (bx+bw, by+bh), (bx, by+bh), (bx, by)]
    ob = batch_for_shader(shader, "LINE_STRIP", {"pos": outline})
    shader.bind(); shader.uniform_float("color", (0.20, 0.12, 0.02, 1.0)); ob.draw(shader)

    if level > 0.0:
        fill_w = min(level, 1.0) * bw
        if is_output:
            col = (_RED if fill_w > bw*0.90 else
                   (0.90, 0.55, 0.05, 1.0) if fill_w > bw*0.70 else
                   (0.70, 0.40, 0.04, 1.0))
        else:
            col = (_RED if fill_w > bw*0.90 else
                   (0.75, 0.70, 0.05, 1.0) if fill_w > bw*0.70 else
                   (0.12, 0.70, 0.25, 1.0))
        _draw_rect(bx, by, fill_w, bh, col)

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
    shader   = gpu.shader.from_builtin("UNIFORM_COLOR")

    # Background + border
    _draw_rect(rx, body_bot, rw, body_h, _BG)
    bv = [(rx, body_bot), (rx+rw, body_bot),
          (rx+rw, body_top), (rx, body_top), (rx, body_bot)]
    b  = batch_for_shader(shader, "LINE_STRIP", {"pos": bv})
    shader.bind(); shader.uniform_float("color", _BORDER); b.draw(shader)

    # Original column widths — unchanged from first working version
    left_w   = rw * 0.25
    centre_w = rw * 0.50
    centre_x = rx + left_w
    right_x  = rx + left_w + centre_w

    # Dividers
    for dx in (centre_x, right_x):
        dv = [(dx, body_bot+4*scale), (dx, body_top-4*scale)]
        db = batch_for_shader(shader, "LINES", {"pos": dv})
        shader.bind(); shader.uniform_float("color", (0.20, 0.12, 0.02, 1.0)); db.draw(shader)

    # ── LEFT: Big boost knob — original geometry ──────────────────────────────
    knob_cx = rx + left_w * 0.5
    knob_cy = body_bot + body_h * 0.52
    knob_r  = min(left_w * 0.30, body_h * 0.35)   # original formula
    _draw_knob(knob_cx, knob_cy, knob_r,
               boost_norm, (_AMBER[0], _AMBER[1], _AMBER[2]),
               "BOOST", boost_str, scale)
    fs_mul  = max(1, int(7*scale))
    mul_str = f"×{gain_lin:.1f}" if gain_lin < 100 else f"×{gain_lin:.0f}"
    _draw_text(mul_str,
               knob_cx - _text_width(mul_str, fs_mul)/2,
               knob_cy - knob_r - fs_mul*2 - 4*scale,
               fs_mul, _TEXT_DIM)

    # ── CENTRE: IN meter / OUT meter / presets / apply ────────────────────────
    bar_pad = 8 * scale
    bar_x   = centre_x + bar_pad
    bar_w   = centre_w - bar_pad * 2

    fs_lbl  = max(1, int(7*scale))
    fs_db   = max(1, int(8*scale))
    bar_h_m = max(6*scale, body_h * 0.11)

    # Apply + Limiter at bottom
    ctrl_h  = min(body_h * 0.26, 26*scale)
    apply_y = body_bot + 4*scale
    lim_w   = 88 * scale
    apply_w = bar_w - lim_w - 6*scale
    apply_x = bar_x
    lim_x   = apply_x + apply_w + 6*scale

    # IN meter — positioned in upper portion of centre
    in_lbl_y = body_bot + body_h * 0.83
    in_bar_y = in_lbl_y - fs_lbl - 2*scale - bar_h_m

    _draw_text("IN", bar_x, in_lbl_y, fs_lbl, _TEXT_DIM)
    in_db_str = _level_to_dbfs(in_rms)
    _draw_text(in_db_str,
               bar_x + bar_w - _text_width(in_db_str, fs_db),
               in_lbl_y, fs_db,
               _GREEN if in_rms < 0.7 else (_AMBER if in_rms < 0.9 else _RED))
    _draw_vu_bar(shader, bar_x, in_bar_y, bar_w, bar_h_m, in_rms, in_peak, False)

    # OUT preview meter
    out_lbl_y = in_bar_y - 6*scale - fs_lbl
    out_bar_y = out_lbl_y - 2*scale - bar_h_m

    _draw_text("OUT (est.)", bar_x, out_lbl_y, fs_lbl, _AMBER_DIM)
    out_db_str = _level_to_dbfs(out_rms)
    _draw_text(out_db_str,
               bar_x + bar_w - _text_width(out_db_str, fs_db),
               out_lbl_y, fs_db,
               _GREEN if out_rms < 0.7 else (_AMBER if out_rms < 0.9 else _RED))
    _draw_vu_bar(shader, bar_x, out_bar_y, bar_w, bar_h_m, out_rms, out_peak, True)

    # Separator between meters and presets
    sep_y = out_bar_y - 5*scale
    sl = batch_for_shader(shader, "LINES",
                          {"pos": [(bar_x, sep_y), (bar_x+bar_w, sep_y)]})
    shader.bind(); shader.uniform_float("color", (0.20, 0.12, 0.02, 1.0)); sl.draw(shader)

    # Preset grid between apply row and separator
    grid_bot = apply_y + ctrl_h + 4*scale
    grid_top = sep_y - 4*scale
    grid_h   = grid_top - grid_bot
    if grid_h > 4*scale:
        cols, rows = 3, 2
        btn_w  = (bar_w - (cols-1)*3*scale) / cols
        btn_h  = max(scale, (grid_h - (rows-1)*3*scale) / rows)
        fs_pre = max(1, int(8*scale))
        presets = [("+6", 6/40), ("+12", 12/40), ("+18", 18/40),
                   ("+24", 24/40), ("+30", 30/40), ("+40", 1.0)]
        for pi, (lbl, norm) in enumerate(presets):
            col_i = pi % cols
            row_i = pi // cols
            bx_p  = bar_x + col_i * (btn_w + 3*scale)
            by_p  = grid_bot + row_i * (btn_h + 3*scale)
            active = abs(boost_norm - norm) < 0.015
            bg_p  = (0.22, 0.14, 0.02, 1.0) if active else (0.08, 0.06, 0.01, 1.0)
            col_p = _AMBER if active else _AMBER_DIM
            _draw_rect(bx_p, by_p, btn_w, btn_h, bg_p)
            pv = [(bx_p, by_p), (bx_p+btn_w, by_p),
                  (bx_p+btn_w, by_p+btn_h), (bx_p, by_p+btn_h), (bx_p, by_p)]
            pb = batch_for_shader(shader, "LINE_STRIP", {"pos": pv})
            shader.bind(); shader.uniform_float("color", col_p); pb.draw(shader)
            tw = _text_width(lbl, fs_pre)
            _draw_text(lbl, bx_p+btn_w/2-tw/2, by_p+btn_h/2-fs_pre/2, fs_pre, col_p)

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

    _draw_rect(apply_x, apply_y, apply_w, ctrl_h, ap_bg)
    av = [(apply_x, apply_y), (apply_x+apply_w, apply_y),
          (apply_x+apply_w, apply_y+ctrl_h), (apply_x, apply_y+ctrl_h), (apply_x, apply_y)]
    abt = batch_for_shader(shader, "LINE_STRIP", {"pos": av})
    shader.bind(); shader.uniform_float("color", ap_col); abt.draw(shader)
    fs_ap = max(1, int(9*scale))
    tw_ap = _text_width(ap_lbl, fs_ap)
    _draw_text(ap_lbl, apply_x+apply_w/2-tw_ap/2, apply_y+ctrl_h/2-fs_ap/2, fs_ap, ap_col)

    # Limiter toggle
    lim_bg  = (0.02, 0.10, 0.04, 1.0) if limiter_on else (0.08, 0.05, 0.01, 1.0)
    lim_col = _GREEN if limiter_on else _AMBER_DIM
    lim_lbl = "LIMIT: ON" if limiter_on else "LIMIT: OFF"
    _draw_rect(lim_x, apply_y, lim_w, ctrl_h, lim_bg)
    lv = [(lim_x, apply_y), (lim_x+lim_w, apply_y),
          (lim_x+lim_w, apply_y+ctrl_h), (lim_x, apply_y+ctrl_h), (lim_x, apply_y)]
    ltb = batch_for_shader(shader, "LINE_STRIP", {"pos": lv})
    shader.bind(); shader.uniform_float("color", lim_col); ltb.draw(shader)
    fs_lim = max(1, int(8*scale))
    tw_lim = _text_width(lim_lbl, fs_lim)
    _draw_text(lim_lbl, lim_x+lim_w/2-tw_lim/2, apply_y+ctrl_h/2-fs_lim/2, fs_lim, lim_col)

    # ── RIGHT: Output channel stepper (below channel buttons area) ───────────
    # Channel buttons occupy approx body_top - _CH_CLEAR_TOP*scale downward.
    # Our stepper sits in the bottom portion of the right column.
    rpad       = 6 * scale
    rx2        = right_x + rpad
    rw2        = left_w - rpad * 2   # same width as left column
    fs_r       = max(1, int(7*scale))
    fs_ch      = max(1, int(9*scale))

    # Stepper sits just above the apply button row, in the right column
    stepper_h  = 18 * scale
    stepper_y  = apply_y + ctrl_h + 6*scale   # just above apply button bottom
    ch_lbl_y   = stepper_y + stepper_h + 3*scale

    _draw_text("Output ch", rx2, ch_lbl_y, fs_r, _TEXT_DIM)

    # Stepper box [−]  auto/chN  [+]
    _draw_rect(rx2, stepper_y, rw2, stepper_h, (0.03, 0.02, 0.01, 1.0))
    sv = [(rx2, stepper_y), (rx2+rw2, stepper_y),
          (rx2+rw2, stepper_y+stepper_h), (rx2, stepper_y+stepper_h), (rx2, stepper_y)]
    sb = batch_for_shader(shader, "LINE_STRIP", {"pos": sv})
    shader.bind(); shader.uniform_float("color", _AMBER_DIM); sb.draw(shader)

    # Minus / Plus buttons
    _draw_text("−", rx2 + 3*scale,
               stepper_y + stepper_h/2 - fs_ch/2, fs_ch, _AMBER)
    plus_lbl = "+"
    _draw_text(plus_lbl,
               rx2 + rw2 - _text_width(plus_lbl, fs_ch) - 3*scale,
               stepper_y + stepper_h/2 - fs_ch/2, fs_ch, _AMBER)

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
    status_y = body_bot + 4*scale + ctrl_h/2
    _draw_circle(rx2 + 4*scale, status_y, 4*scale, dot_cols.get(status, _AMBER_DIM))
    st_lbl = status.replace('_', ' ')
    _draw_text(st_lbl, rx2 + 12*scale, status_y - fs_r/2, fs_r,
               dot_cols.get(status, _AMBER_DIM))
