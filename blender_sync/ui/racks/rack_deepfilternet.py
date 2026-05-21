# =============================================================================
# ui/racks/rack_deepfilternet.py
# DeepFilterNet AI noise reduction rack — HAL 9000 visual theme.
#
# Layout (body area only — rail drawn by _draw_ai_rack_expanded in Racks.py):
#
#  ┌───────────────────────────────────────────────────────────────────────┐
#  │ [TOP RAIL — collapse▲  badge#  DEEPFILTERNET  CH1 CH2…  ON/OFF   X] │
#  ├────────────────────┬────────────────────────┬────────────────────────┤
#  │  INPUT WAVEFORM    │      HAL EYE           │  OUTPUT WAVEFORM       │
#  │  (red, noisy)      │   (animated status)    │  (green, filtered)     │
#  ├────────────────────┤  ┌──────────────────┐  ├────────────────────────┤
#  │  ATTEN SENS PGAIN  │  │   [ PROCESS ]    │  │  OUT LEVEL bar         │
#  │  (3 knobs)         │  └──────────────────┘  │                        │
#  ├────────────────────┴────────────────────────┴────────────────────────┤
#  │  MODEL: DEEPFILTERNET3  |  ONNX  |  48kHz  |  OFFLINE          ●    │
#  └───────────────────────────────────────────────────────────────────────┘
#
# rx, ry  = bottom-left of the FULL rack (including rail at top)
# rw, rh  = full rack width and height
# Body    = ry → ry + rh - RACK_RAIL_H*scale
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

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_BG         = (0.05,  0.03,  0.03,  1.0)
_BORDER     = (0.20,  0.08,  0.05,  1.0)
_PANEL      = (0.03,  0.02,  0.02,  1.0)
_GRID       = (0.14,  0.05,  0.03,  0.5)
_RED_BRIGHT = (0.85,  0.13,  0.06,  1.0)
_RED_MID    = (0.55,  0.08,  0.03,  1.0)
_RED_DIM    = (0.22,  0.04,  0.01,  1.0)
_TEXT       = (0.80,  0.22,  0.12,  1.0)
_TEXT_DIM   = (0.38,  0.12,  0.06,  1.0)
_WAVE_IN    = (0.75,  0.14,  0.06)        # (r,g,b) no alpha — noisy red
_WAVE_OUT   = (0.08,  0.65,  0.22)        # (r,g,b) no alpha — clean green
_GREEN      = (0.05,  0.80,  0.30,  1.0)

# Status strings — match ai_status property
_READY      = "READY"
_PROCESSING = "PROCESSING"
_DONE       = "DONE"
_ERROR      = "ERROR"
_NO_CH      = "NO_CHANNEL"

RACK_RAIL_H = 32   # must match Racks.py


# ---------------------------------------------------------------------------
# HAL eye
# ---------------------------------------------------------------------------
def _draw_hal_eye(cx, cy, radius, status, scale):
    """Draw the HAL 9000 eye.  radius is already in screen pixels."""
    t = time.time()

    if status == _PROCESSING:
        pulse   = 0.5 + 0.5 * math.sin(t * 7.0 + math.sin(t * 3.1))
        core_a  = 0.7 + 0.3 * pulse
        ring_sc = 1.0 + 0.08 * math.sin(t * 5.5)
        bright  = _RED_BRIGHT
        mid     = _RED_MID
    elif status == _DONE:
        pulse   = 0.5 + 0.5 * math.sin(t * 1.2)
        core_a  = 0.6 + 0.4 * pulse
        ring_sc = 1.0 + 0.04 * pulse
        bright  = (0.25, 0.80, 0.25, 1.0)
        mid     = (0.08, 0.35, 0.08, 1.0)
    elif status == _ERROR:
        blink   = 1.0 if math.sin(t * 8.0) > 0 else 0.15
        pulse   = blink
        core_a  = blink
        ring_sc = 1.0
        bright  = (1.0, 0.08, 0.04, 1.0)
        mid     = (0.6, 0.04, 0.02, 1.0)
    else:
        pulse   = 0.5 + 0.5 * math.sin(t * 1.4)
        core_a  = 0.28 + 0.20 * pulse
        ring_sc = 1.0 + 0.025 * pulse
        bright  = _RED_BRIGHT
        mid     = _RED_MID

    sh = gpu.shader.from_builtin("UNIFORM_COLOR")

    def _disc(r, col):
        seg = max(32, int(r * 1.8))
        pts = [(cx, cy)]
        for i in range(seg + 1):
            a = 2 * math.pi * i / seg
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        b = batch_for_shader(sh, "TRI_FAN", {"pos": pts})
        sh.bind(); sh.uniform_float("color", col); b.draw(sh)

    def _ring(r, col):
        seg = max(32, int(r * 1.8))
        pts = [(cx + r * math.cos(2 * math.pi * i / seg),
                cy + r * math.sin(2 * math.pi * i / seg))
               for i in range(seg + 1)]
        b = batch_for_shader(sh, "LINE_STRIP", {"pos": pts})
        sh.bind(); sh.uniform_float("color", col); b.draw(sh)

    _disc(radius, _BG)

    fracs = [0.96, 0.84, 0.72, 0.60, 0.48, 0.36]
    for i, f in enumerate(fracs):
        t_r = i / len(fracs)
        a   = min(1.0, 0.12 + 0.50 * t_r + 0.25 * pulse * t_r)
        rc  = (
            _RED_DIM[0] + (_RED_MID[0] - _RED_DIM[0]) * t_r,
            _RED_DIM[1] + (_RED_MID[1] - _RED_DIM[1]) * t_r,
            _RED_DIM[2] + (_RED_MID[2] - _RED_DIM[2]) * t_r,
            a,
        )
        _ring(radius * f * ring_sc, rc)

    _disc(radius * 0.30, (_RED_DIM[0]*0.4, _RED_DIM[1]*0.4, _RED_DIM[2]*0.4, 1.0))
    _disc(radius * 0.23, _RED_DIM)
    _disc(radius * 0.16, (mid[0],    mid[1],    mid[2],    1.0))
    _disc(radius * 0.09, (bright[0], bright[1], bright[2], core_a))

    # Specular catch-light
    cr  = max(1.5, radius * 0.045)
    ccx = cx - radius * 0.055
    ccy = cy + radius * 0.065
    pts = [(ccx, ccy)]
    for i in range(13):
        a = 2 * math.pi * i / 12
        pts.append((ccx + cr * math.cos(a), ccy + cr * math.sin(a)))
    b = batch_for_shader(sh, "TRI_FAN", {"pos": pts})
    sh.bind()
    sh.uniform_float("color", (1.0, 0.65, 0.45, 0.48 + 0.24 * pulse))
    b.draw(sh)


# ---------------------------------------------------------------------------
# Waveform panel
# ---------------------------------------------------------------------------
def _draw_waveform_panel(px, py, pw, ph, scale, data, rgb, label,
                         placeholder=None):
    """px,py = bottom-left, pw,ph = width,height.  rgb = (r,g,b) no alpha."""
    _draw_rect(px, py, pw, ph, _PANEL)

    sh = gpu.shader.from_builtin("UNIFORM_COLOR")
    bv = [(px,py),(px+pw,py),(px+pw,py+ph),(px,py+ph),(px,py)]
    b  = batch_for_shader(sh, "LINE_STRIP", {"pos": bv})
    sh.bind(); sh.uniform_float("color", _BORDER); b.draw(sh)

    fs = max(1, int(8 * scale))
    _draw_text(label, px + 5*scale, py + ph - fs - 3*scale, fs, _TEXT_DIM)

    cy_p = py + ph * 0.5
    amp  = ph * 0.40

    _draw_line(px + 2*scale, cy_p, px + pw - 2*scale, cy_p,
               _GRID, max(0.5, scale * 0.5))
    for f in (0.25, 0.75):
        gy = py + ph * f
        _draw_line(px + 2*scale, gy, px + pw - 2*scale, gy,
                   (_GRID[0], _GRID[1], _GRID[2], 0.28),
                   max(0.5, scale * 0.5))

    if data and len(data) > 1:
        n    = len(data)
        step = (pw - 4*scale) / max(1, n - 1)
        ox   = px + 2*scale

        top_pts = [(ox + i*step, cy_p + data[i]*amp) for i in range(n)]
        bot_pts = [(ox + i*step, cy_p - data[i]*amp) for i in range(n)]

        bt = batch_for_shader(sh, "LINE_STRIP", {"pos": top_pts})
        sh.bind(); sh.uniform_float("color", rgb + (0.88,)); bt.draw(sh)

        bot_col = (rgb[0]*0.65, rgb[1]*0.65, rgb[2]*0.65, 0.55)
        bb = batch_for_shader(sh, "LINE_STRIP", {"pos": bot_pts})
        sh.bind(); sh.uniform_float("color", bot_col); bb.draw(sh)

        # Thin fill segments
        segs = min(n, int((pw - 4*scale) / max(2.0, scale * 2.0)))
        for j in range(segs):
            idx = int(j * (n-1) / max(1, segs-1))
            x   = ox + j * ((pw - 4*scale) / max(1, segs-1))
            v   = data[idx]
            _draw_line(x, cy_p - v*amp, x, cy_p + v*amp,
                       (rgb[0], rgb[1], rgb[2], 0.12), max(1.0, scale))
    else:
        flat = (rgb[0]*0.28, rgb[1]*0.28, rgb[2]*0.28, 0.50)
        _draw_line(px + 2*scale, cy_p, px + pw - 2*scale, cy_p,
                   flat, max(0.8, scale * 0.8))
        if placeholder:
            fs_ph = max(1, int(7 * scale))
            tw    = _text_width(placeholder, fs_ph)
            _draw_text(placeholder, px + pw/2 - tw/2,
                       cy_p - fs_ph/2, fs_ph, _TEXT_DIM)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def _draw_deepfilternet_body(rx, ry, rw, rh, rack, ai_idx, scale):
    """Draw the DeepFilterNet rack body.

    Called by _draw_ai_rack_expanded (Racks.py) after the rail is drawn.
    rx, ry = bottom-left of FULL rack.  rw, rh = full dimensions.
    Body occupies  ry  →  ry + rh - RACK_RAIL_H*scale.
    """

    # ── Status ────────────────────────────────────────────────────────────────
    status = getattr(rack, 'ai_status', _READY)
    if status not in (_READY, _PROCESSING, _DONE, _ERROR, _NO_CH):
        status = _READY

    # ── Waveform data ─────────────────────────────────────────────────────────
    input_wave  = []
    output_wave = []
    try:
        import Racks as _rk
        assigned = list(_rk.get_ai_rack_channels(rack))
        if assigned:
            ch_idx = assigned[0]
            try:
                # Read input waveform from the shared live meter ring buffer
                # (same buffer used by the noise gate rack — populated every
                # draw call from hj.get_meter_rms during playback).
                from ui.racks.rack_noisegate import _NG_RMS_HISTORY
                buf = _NG_RMS_HISTORY.get(ch_idx)
                if buf and len(buf) > 1:
                    # Extract just the rms values in order, normalise to 0-1
                    vals = [rms for _, rms in buf]
                    n_s  = 80
                    step = max(1, len(vals) // n_s)
                    for i in range(0, len(vals), step):
                        input_wave.append(min(1.0, max(0.0, float(vals[i]))))
                    input_wave = input_wave[:n_s]
            except Exception:
                pass
            try:
                from core.ai_deepfilternet import get_output_wave
                output_wave = get_output_wave(ch_idx)
            except Exception:
                output_wave = []
        else:
            if status == _READY:
                status = _NO_CH
    except Exception:
        pass

    # ── Geometry ──────────────────────────────────────────────────────────────
    rail_h   = RACK_RAIL_H * scale
    body_bot = ry
    body_top = ry + rh - rail_h
    body_h   = body_top - body_bot

    # Status bar — very bottom strip
    sbar_h = max(18*scale, body_h * 0.07)
    sbar_y = body_bot
    sbar_x = rx + 6*scale
    sbar_w = rw - 12*scale

    # Lower zone — knobs / process button / level bar
    lower_h   = body_h * 0.30
    lower_bot = sbar_y + sbar_h + 2*scale
    lower_top = lower_bot + lower_h

    # Upper zone — waveforms
    wave_h = body_top - lower_top - 4*scale
    wave_y = lower_top + 2*scale

    # Three columns
    margin    = 8*scale
    centre_w  = rw * 0.26
    side_w    = (rw - centre_w - margin * 4) * 0.5

    wave_in_x  = rx + margin
    centre_x   = wave_in_x + side_w + margin
    wave_out_x = centre_x + centre_w + margin

    eye_cx = centre_x + centre_w * 0.5
    eye_cy = wave_y   + wave_h   * 0.50
    # Radius from available space only — no scale cap
    eye_r  = min(centre_w * 0.42, wave_h * 0.42)

    # ── Background + border ───────────────────────────────────────────────────
    _draw_rect(rx, body_bot, rw, body_h, _BG)
    sh  = gpu.shader.from_builtin("UNIFORM_COLOR")
    bv  = [(rx, body_bot), (rx+rw, body_bot),
           (rx+rw, body_top), (rx, body_top), (rx, body_bot)]
    b   = batch_for_shader(sh, "LINE_STRIP", {"pos": bv})
    sh.bind(); sh.uniform_float("color", _BORDER); b.draw(sh)

    # No scanlines — they compound badly at small scales

    # ── INPUT waveform ────────────────────────────────────────────────────────
    ph_in = "NO AUDIO ASSIGNED" if status == _NO_CH else "WAITING..."
    _draw_waveform_panel(wave_in_x, wave_y, side_w, wave_h, scale,
                         input_wave, _WAVE_IN, "INPUT",
                         placeholder=ph_in)

    # ── OUTPUT waveform ───────────────────────────────────────────────────────
    ph_out = None if status == _DONE else "RUN PROCESS TO SEE OUTPUT"
    _draw_waveform_panel(wave_out_x, wave_y, side_w, wave_h, scale,
                         output_wave, _WAVE_OUT, "OUTPUT — FILTERED",
                         placeholder=ph_out)

    # ── HAL EYE ───────────────────────────────────────────────────────────────
    _draw_hal_eye(eye_cx, eye_cy, eye_r, status, scale)

    # Status text — sits just above the bottom of the waveform area
    stxt_map = {
        _READY:      "I AM READY",
        _PROCESSING: "I AM PROCESSING",
        _DONE:       "PROCESS COMPLETE",
        _ERROR:      "ERROR — CHECK CONSOLE",
        _NO_CH:      "ASSIGN A CHANNEL",
    }
    stxt   = stxt_map.get(status, "STANDBY")
    fs_st  = max(1, int(7 * scale))
    tw_st  = _text_width(stxt, fs_st)
    _draw_text(stxt,
               eye_cx - tw_st / 2,
               wave_y + 3*scale,
               fs_st, _TEXT)

    # ── KNOB ROW — left column ─────────────────────────────────────────────────
    knobs = [
        ('p0', 'ATTEN',    -40.0,  0.0, 0.50, "{:.0f}dB"),
        ('p1', 'SENSITIV',   0.0,  1.0, 0.75, "{:.2f}"),
        ('p2', 'POST GAIN',-12.0, 12.0, 0.50, "{:+.1f}dB"),
    ]
    n_k    = len(knobs)
    knob_y = lower_bot + lower_h * 0.55
    knob_r = min(13*scale, lower_h * 0.36)
    kw     = side_w / n_k
    for i, (attr, lbl, pmin, pmax, pdef_n, fmt) in enumerate(knobs):
        kx   = wave_in_x + kw * (i + 0.5)
        norm = getattr(rack, attr, pdef_n)
        val  = pmin + norm * (pmax - pmin)
        try:    vs = fmt.format(val)
        except: vs = f"{val:.2f}"
        _draw_knob(kx, knob_y, knob_r,
                   norm, (_RED_BRIGHT[0], _RED_BRIGHT[1], _RED_BRIGHT[2]),
                   lbl, vs, scale)

    # ── PROCESS button — centre column ────────────────────────────────────────
    p_w = centre_w * 0.76
    p_h = min(lower_h * 0.52, 24*scale)
    p_x = centre_x + (centre_w - p_w) * 0.5
    p_y = lower_bot + (lower_h - p_h) * 0.5

    if status == _PROCESSING:
        p_bg  = (0.18, 0.06, 0.02, 1.0)
        p_col = (0.90, 0.35, 0.10, 1.0)
        p_lbl = "PROCESSING..."
    else:
        p_bg  = (0.14, 0.04, 0.02, 1.0)
        p_col = _RED_BRIGHT
        p_lbl = "PROCESS"

    _draw_rect(p_x, p_y, p_w, p_h, p_bg)
    pvs = [(p_x, p_y), (p_x+p_w, p_y),
           (p_x+p_w, p_y+p_h), (p_x, p_y+p_h), (p_x, p_y)]
    pb = batch_for_shader(sh, "LINE_STRIP", {"pos": pvs})
    sh.bind(); sh.uniform_float("color", p_col); pb.draw(sh)
    fs_p  = max(1, int(9 * scale))
    tw_p  = _text_width(p_lbl, fs_p)
    _draw_text(p_lbl,
               p_x + p_w/2 - tw_p/2,
               p_y + p_h/2 - fs_p/2,
               fs_p, p_col)

    # ── OUTPUT LEVEL BAR — right column ───────────────────────────────────────
    lbar_x = wave_out_x
    lbar_w = side_w
    lbar_h = max(7*scale, lower_h * 0.18)
    lbar_y = lower_bot + lower_h * 0.38

    _draw_rect(lbar_x, lbar_y, lbar_w, lbar_h, (0.04, 0.07, 0.03, 1.0))
    lvs = [(lbar_x, lbar_y), (lbar_x+lbar_w, lbar_y),
           (lbar_x+lbar_w, lbar_y+lbar_h),
           (lbar_x, lbar_y+lbar_h), (lbar_x, lbar_y)]
    lb = batch_for_shader(sh, "LINE_STRIP", {"pos": lvs})
    sh.bind(); sh.uniform_float("color", (0.08, 0.25, 0.08, 1.0)); lb.draw(sh)

    if status == _DONE and output_wave:
        level = sum(output_wave) / len(output_wave)
    elif input_wave and status != _NO_CH:
        level = sum(input_wave) / len(input_wave) * 0.65
    else:
        level = 0.0
    level = min(1.0, max(0.0, level))

    fill_w = lbar_w * min(level, 0.92)
    if fill_w > 2:
        _draw_rect(lbar_x, lbar_y, fill_w, lbar_h, (0.06, 0.52, 0.16, 0.85))
        _draw_rect(lbar_x + fill_w - max(2, scale*2), lbar_y,
                   max(2, scale*2), lbar_h, (0.10, 0.88, 0.32, 1.0))
    # Red clip zone
    red_x = lbar_x + lbar_w * 0.92
    _draw_rect(red_x, lbar_y, lbar_w * 0.08, lbar_h, (0.28, 0.04, 0.02, 1.0))
    if level > 0.92:
        clip_w = (level - 0.92) / 0.08 * (lbar_w * 0.08)
        _draw_rect(red_x, lbar_y, clip_w, lbar_h, (0.80, 0.10, 0.04, 0.9))

    fs_lv = max(1, int(7 * scale))
    _draw_text("OUT LEVEL",
               lbar_x, lbar_y + lbar_h + 2*scale,
               fs_lv, _TEXT_DIM)

    # ── STATUS BAR ────────────────────────────────────────────────────────────
    _draw_rect(sbar_x, sbar_y, sbar_w, sbar_h, (0.04, 0.02, 0.02, 1.0))
    svs = [(sbar_x, sbar_y), (sbar_x+sbar_w, sbar_y),
           (sbar_x+sbar_w, sbar_y+sbar_h),
           (sbar_x, sbar_y+sbar_h), (sbar_x, sbar_y)]
    sb = batch_for_shader(sh, "LINE_STRIP", {"pos": svs})
    sh.bind(); sh.uniform_float("color", _BORDER); sb.draw(sh)

    fs_sb = max(1, int(7 * scale))
    _draw_text(
        "MODEL: DEEPFILTERNET3  |  ONNX  |  SAMPLE RATE: 48kHz  |  OFFLINE",
        sbar_x + 7*scale, sbar_y + sbar_h/2 - fs_sb/2,
        fs_sb, _TEXT_DIM)

    dot_cols = {
        _READY:      _GREEN,
        _PROCESSING: (0.90, 0.50, 0.10, 1.0),
        _DONE:       _GREEN,
        _ERROR:      (0.90, 0.10, 0.05, 1.0),
        _NO_CH:      (0.30, 0.30, 0.30, 1.0),
    }
    _draw_circle(sbar_x + sbar_w - 9*scale,
                 sbar_y + sbar_h / 2,
                 3.5 * scale,
                 dot_cols.get(status, (0.4, 0.4, 0.4, 1.0)))
