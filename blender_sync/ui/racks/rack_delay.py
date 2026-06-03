# =============================================================================
# rack_delay.py
# Delay rack UI — impulse response curve display
# ┌─ LAYOUT CONSTANTS ─────────────────────────────────────────────────────┐
# │ RACK_EXPANDED_H_DL — change height here
# └────────────────────────────────────────────────────────────────────────┘
# =============================================================================

import math
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



# These drawing helpers are imported from draw_utils so the PNG bridge
# (draw_element) can replace them with texture blits when PNGs are loaded.
# Until then they call GPU primitives directly.
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
    # Fallback when loaded standalone — Racks.py re-exports these
    pass

RACK_RAIL_H = 32  # duplicated from Racks.py to avoid circular import


def _draw_delay_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """Delay rack — impulse response curve display.

    Upper display (~56% of body height):
      Impulse response envelope: a smooth mirrored decay curve built entirely
      from the five knob values — no audio timeline data needed.

      The curve simulates what a single impulse sounds like through the delay:
        - A dry impulse spike at x=0
        - A series of echo peaks at x = n * delay_ms, each with amplitude
          feedback^n * mix, smoothed into a continuous envelope
        - The LP filter darkens (attenuates) each successive echo by
          multiplying the amplitude by filter^n (fully open = no attenuation)
        - Ping-pong: even echoes drawn on the upper half of the display,
          odd echoes on the lower half, with a magenta centre divider
        - The overall shape updates instantly as any knob moves

    Lower knob strip (~42% of body height):
      Time (cyan) | Feedback (orange) | Mix (green) | Spread (magenta) | Filter (warm-white)
    """
    # Lazy imports — avoids circular import at module load time
    import Racks as _racks_mod
    get_rack_channels = _racks_mod.get_rack_channels
    import math as _md
    ui     = scale
    rail_h = RACK_RAIL_H * ui
    body_h = rh - rail_h
    # ── Skin background
    try:
        from ui.mixer.draw_utils import draw_element as _de
        _de("rack_delay_bg", rx, ry, rw, body_h, _draw_rect, (0.07, 0.07, 0.07, 1.0))
    except Exception:
        _draw_rect(rx, ry, rw, body_h, (0.07, 0.07, 0.07, 1.0))

    # ── Geometry — identical to previous version ──────────────────────────────
    margin_l  = 42 * ui
    ch_btn_w  = 108 * ui
    margin_r  = ch_btn_w + 8 * ui
    disp_x    = rx + margin_l
    disp_w    = rw - margin_l - margin_r
    disp_prop = 0.56
    disp_h    = body_h * disp_prop - 4 * ui
    disp_y    = ry + body_h - disp_h - 2 * ui
    knob_h    = body_h * 0.42 - 4 * ui
    knob_y    = ry + 2 * ui

    # ── Knob values — identical to previous version ───────────────────────────
    time_norm  = getattr(rack, 'p0', 0.121)
    fb_norm    = getattr(rack, 'p1', 0.40)
    mix_norm   = getattr(rack, 'p2', 0.30)
    spread_n   = getattr(rack, 'p3', 0.50)
    filt_norm  = getattr(rack, 'p4', 0.60)

    delay_ms   = 1.0 + time_norm * 1999.0
    feedback   = fb_norm
    mix        = mix_norm
    ping_pong  = spread_n > 0.5

    filt_hz    = 200.0 * (100.0 ** filt_norm)
    filt_str   = (f"LP {filt_hz:.0f}Hz" if filt_hz < 10000
                  else f"LP {filt_hz/1000:.1f}kHz")
    delay_str  = (f"{delay_ms:.0f}ms" if delay_ms < 1000
                  else f"{delay_ms/1000:.2f}s")

    shader = _get_shader()

    # ── Display background ────────────────────────────────────────────────────
    _draw_rect(disp_x, disp_y, disp_w, disp_h, (0.035, 0.040, 0.055, 1.0))

    # Subtle grid lines
    for _gfrac in (0.25, 0.50, 0.75):
        _gly = disp_y + _gfrac * disp_h
        _ggb = batch_for_shader(shader, "LINES",
                                {"pos": [(disp_x, _gly), (disp_x + disp_w, _gly)]})
        shader.bind()
        shader.uniform_float("color", (0.10, 0.11, 0.16, 1.0))
        _ggb.draw(shader)

    # ── Impulse response curve ────────────────────────────────────────────────
    # Build the envelope by sampling at N_PTS points across the display.
    # For each x position we work out which delay tap it falls under and what
    # the envelope amplitude is, including LP darkening across taps.
    #
    # Envelope model per tap n (1-based):
    #   peak_amp  = mix * 0.9 * feedback^(n-1)
    #   lp_factor = filt_norm^n  (1.0 = no darkening, 0.0 = silent)
    #   amplitude = peak_amp * lp_factor
    #   The shape within each tap is a Gaussian bell centred on the tap's x,
    #   with sigma = delay_px * 0.18 (narrows/widens with delay time naturally).
    #
    # The dry impulse (tap 0) is always drawn as a sharp spike at x=0.

    N_PTS    = 512
    MAX_TAPS = 12
    cy       = disp_y + disp_h * 0.5   # vertical centre of display

    # Map delay_ms to pixels using the full display width = 2500ms of "view time"
    # so the curve always fits: at max delay (2000ms) the first echo is at 80% width.
    VIEW_MS    = 2500.0
    px_per_ms  = disp_w / VIEW_MS

    # LP per-tap attenuation: each echo is multiplied by lp_atten once more
    # filt_norm=1.0 → lp_atten=1.0 (fully open, no roll-off)
    # filt_norm=0.0 → lp_atten=0.2 (very dark, heavy roll-off)
    lp_atten = 0.2 + filt_norm * 0.8

    # Gaussian sigma in pixels — scales with delay so shape looks natural
    sigma_px  = max(8.0 * ui, delay_ms * px_per_ms * 0.18)

    def _gaussian(x_px, centre_px):
        d = (x_px - centre_px) / sigma_px
        return _md.exp(-0.5 * d * d)

    # Sample the full envelope across the display width
    pts_top = []   # upper half (or full if no ping-pong)
    pts_bot = []   # lower half (mirrored)
    pts_top_pp_even = []   # ping-pong even taps (upper)
    pts_bot_pp_even = []
    pts_top_pp_odd  = []   # ping-pong odd taps (lower)
    pts_bot_pp_odd  = []

    half_h = disp_h * 0.43   # max half-height the curve can reach

    for _pi in range(N_PTS):
        _xf  = _pi / (N_PTS - 1)
        _x   = disp_x + _xf * disp_w
        _xms = _xf * VIEW_MS

        # Dry spike — sharp Gaussian centred at 0
        dry_amp = _gaussian(_xms, 0.0) * (half_h * 0.92)

        # Sum echo contributions across taps
        echo_amp_even = 0.0   # ping-pong even taps
        echo_amp_odd  = 0.0   # ping-pong odd taps
        echo_amp_full = 0.0   # non-ping-pong

        tap_lp = lp_atten
        for _tn in range(1, MAX_TAPS + 1):
            tap_centre_ms = _tn * delay_ms
            tap_centre_px = tap_centre_ms   # we're working in ms-space for gaussian
            peak = mix * 0.9 * (feedback ** (_tn - 1)) * tap_lp
            if peak < 0.005:
                break
            g = _gaussian(_xms, tap_centre_ms)
            contribution = g * peak * half_h

            if ping_pong:
                if _tn % 2 == 0:
                    echo_amp_even += contribution
                else:
                    echo_amp_odd  += contribution
            else:
                echo_amp_full += contribution

            tap_lp *= lp_atten

        # Build point lists
        if ping_pong:
            # Dry impulse goes on both halves
            pts_top_pp_even.append((_x, cy - dry_amp * 0.5 - echo_amp_even))
            pts_bot_pp_even.append((_x, cy + dry_amp * 0.5 + echo_amp_even))
            pts_top_pp_odd.append( (_x, cy - dry_amp * 0.5 - echo_amp_odd))
            pts_bot_pp_odd.append( (_x, cy + dry_amp * 0.5 + echo_amp_odd))
        else:
            pts_top.append((_x, cy - dry_amp - echo_amp_full))
            pts_bot.append((_x, cy + dry_amp + echo_amp_full))

    def _draw_envelope(top_pts, bot_pts, fill_col, line_col):
        """Draw a filled envelope from two point lists (top and bottom)."""
        if len(top_pts) < 2:
            return
        try:
            # Filled interior — TRI_STRIP interleaved top/bottom
            _fv = []
            for (_tx, _ty), (_bx, _by) in zip(top_pts, bot_pts):
                _fv += [(_tx, _ty), (_bx, _by)]
            if len(_fv) >= 4:
                _bf = batch_for_shader(shader, "TRI_STRIP", {"pos": _fv})
                shader.bind()
                shader.uniform_float("color", fill_col)
                _bf.draw(shader)
            # Top edge line
            gpu.state.line_width_set(max(1.5, ui))
            _bl = batch_for_shader(shader, "LINE_STRIP", {"pos": top_pts})
            shader.bind()
            shader.uniform_float("color", line_col)
            _bl.draw(shader)
            # Bottom edge line (mirrored)
            _bb = batch_for_shader(shader, "LINE_STRIP", {"pos": bot_pts})
            shader.bind()
            shader.uniform_float("color", line_col)
            _bb.draw(shader)
            gpu.state.line_width_set(1.0)
        except Exception:
            pass

    if ping_pong:
        centre_x = disp_x + disp_w * 0.5

        # Clip even taps to upper half, odd taps to lower half
        def _clamp_half(pts, upper):
            out = []
            for (x, y) in pts:
                if upper:
                    out.append((x, max(disp_y + 1*ui, min(cy, y))))
                else:
                    out.append((x, max(cy, min(disp_y + disp_h - 1*ui, y))))
            return out

        top_e = _clamp_half(pts_top_pp_even, True)
        bot_e = _clamp_half(pts_bot_pp_even, True)
        top_o = _clamp_half(pts_top_pp_odd,  False)
        bot_o = _clamp_half(pts_bot_pp_odd,  False)

        # Even taps (upper half) — cyan tint
        _draw_envelope(top_e, bot_e,
                       (0.04, 0.22, 0.40, 0.45),
                       (0.15, 0.55, 0.90, 0.80))
        # Odd taps (lower half) — magenta tint
        _draw_envelope(top_o, bot_o,
                       (0.28, 0.04, 0.35, 0.40),
                       (0.72, 0.20, 0.88, 0.75))

        # Centre divider
        _ppv = [(centre_x, disp_y + 2*ui), (centre_x, disp_y + disp_h - 2*ui)]
        _ppb = batch_for_shader(shader, "LINES", {"pos": _ppv})
        gpu.state.line_width_set(max(1.2, ui))
        shader.bind()
        shader.uniform_float("color", (0.75, 0.20, 0.85, 0.50))
        _ppb.draw(shader)
        gpu.state.line_width_set(1.0)
        _fs_pp = max(1, int(7 * ui))
        _draw_text("L", disp_x + 3*ui, disp_y + disp_h * 0.25 - _fs_pp*0.5,
                   _fs_pp, (0.30, 0.65, 0.95, 0.65))
        _draw_text("R", disp_x + 3*ui, disp_y + disp_h * 0.75 - _fs_pp*0.5,
                   _fs_pp, (0.72, 0.20, 0.88, 0.65))
    else:
        # Single stereo envelope — cyan fill with brighter edge
        _draw_envelope(pts_top, pts_bot,
                       (0.04, 0.22, 0.42, 0.40),
                       (0.15, 0.58, 0.92, 0.85))

    # ── Tap marker lines — thin verticals at each echo centre ────────────────
    # Shows exactly where each tap lands, colour fades with amplitude
    tap_lp2 = lp_atten
    _fs_tap = max(1, int(7 * ui))
    for _tn in range(1, MAX_TAPS + 1):
        _tap_ms = _tn * delay_ms
        if _tap_ms > VIEW_MS:
            break
        _tap_x  = disp_x + (_tap_ms / VIEW_MS) * disp_w
        _tap_amp = mix * 0.9 * (feedback ** (_tn - 1)) * tap_lp2
        if _tap_amp < 0.005:
            break
        _alpha = min(0.70, _tap_amp * 1.4)
        _tv2 = [(_tap_x, disp_y + 2*ui), (_tap_x, disp_y + disp_h - 2*ui)]
        _tb2 = batch_for_shader(shader, "LINES", {"pos": _tv2})
        gpu.state.line_width_set(max(1.0, ui * 0.7))
        shader.bind()
        if ping_pong and _tn % 2 == 0:
            shader.uniform_float("color", (0.72, 0.20, 0.88, _alpha * 0.55))
        else:
            shader.uniform_float("color", (0.15, 0.58, 0.92, _alpha * 0.55))
        _tb2.draw(shader)
        gpu.state.line_width_set(1.0)
        # Tap number label on first few taps
        if _tn <= 5:
            _draw_text(str(_tn), _tap_x + 2*ui, disp_y + 3*ui,
                       _fs_tap,
                       (0.72, 0.20, 0.88, _alpha * 0.7) if (ping_pong and _tn % 2 == 0)
                       else (0.15, 0.58, 0.92, _alpha * 0.7))
        tap_lp2 *= lp_atten

    # ── LP filter cutoff marker — amber dashed vertical ───────────────────────
    # Position maps the cutoff frequency to the x position of the tap whose
    # centre frequency would be most attenuated — simpler: place it as a
    # fraction of the display width scaled by filt_norm so it visually moves
    # with the Filter knob in an intuitive left=dark, right=bright direction.
    _fc_x = disp_x + filt_norm * disp_w * 0.88 + disp_w * 0.06
    _dy_fc = disp_y; _segs_fc = []; _tog_fc = True
    _dash_fc = 5*ui; _gap_fc = 3*ui
    while _dy_fc < disp_y + disp_h:
        _ye_fc = min(_dy_fc + (_dash_fc if _tog_fc else _gap_fc), disp_y + disp_h)
        if _tog_fc:
            _segs_fc += [(_fc_x, _dy_fc), (_fc_x, _ye_fc)]
        _dy_fc = _ye_fc; _tog_fc = not _tog_fc
    if _segs_fc:
        _bfc = batch_for_shader(shader, "LINES", {"pos": _segs_fc})
        gpu.state.line_width_set(max(1.2, ui))
        shader.bind()
        shader.uniform_float("color", (0.90, 0.60, 0.12, 0.65))
        _bfc.draw(shader)
        gpu.state.line_width_set(1.0)

    # ── Info labels ───────────────────────────────────────────────────────────
    _fs8 = max(1, int(8 * ui))
    _draw_text(f"t = {delay_str}", disp_x + 4*ui,
               disp_y + disp_h - _fs8 - 3*ui, _fs8, (0.25, 0.68, 0.95, 0.85))
    _tw_f = _text_width(filt_str, _fs8)
    _draw_text(filt_str, disp_x + disp_w - _tw_f - 4*ui,
               disp_y + disp_h - _fs8 - 3*ui, _fs8, (0.90, 0.60, 0.12, 0.80))
    _draw_text(f"fb {int(feedback * 100)}%", disp_x + 4*ui,
               disp_y + 3*ui, _fs8, (0.25, 0.68, 0.95, 0.75))

    # ── Knob strip — identical to previous version ────────────────────────────
    _N_dl  = 5
    _cw_dl = disp_w / _N_dl
    _rk_dl = knob_y + knob_h * 0.68
    _kr_dl = min(max(13*ui, _cw_dl * 0.16), 20*ui)
    _kr_dl = min(_kr_dl, knob_h * 0.42 * 0.42)

    _knob_defs_dl = [
        ("Time",     time_norm, delay_str,              (0.25, 0.72, 0.95)),
        ("Feedback", fb_norm,   f"{int(feedback*100)}%",(0.95, 0.48, 0.12)),
        ("Mix",      mix_norm,  f"{int(mix*100)}%",     (0.30, 0.85, 0.45)),
        ("Spread",   spread_n,  f"{int(spread_n*100)}%",(0.82, 0.22, 0.92)),
        ("Filter",   filt_norm, filt_str,               (0.88, 0.78, 0.55)),
    ]
    for _ki_dl, (_lbl, _val, _vstr, _col_k) in enumerate(_knob_defs_dl):
        _cx_dl = disp_x + (_ki_dl + 0.5) * _cw_dl
        _draw_knob(_cx_dl, _rk_dl, _kr_dl, _val, _col_k, _lbl, _vstr, ui)


