# =============================================================================
# rack_comp.py
# Single-band and multiband compressor rack UI
# ┌─ LAYOUT CONSTANTS ─────────────────────────────────────────────────────┐
# │ RACK_EXPANDED_H, RACK_EXPANDED_H_MB — change height here
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


# ---------------------------------------------------------------------------
# Module-level cache for multiband spectrum display.
# Weight arrays and hz-to-bin mapping are constant — computed once at import.
# Only vertex positions depend on live spec_bins and are rebuilt per frame.
# ---------------------------------------------------------------------------
import math as _math_mb

_MB_N_SPEC    = 128
_MB_HZ_BINS   = [20.0 * (1000.0 ** (i / (_MB_N_SPEC - 1))) for i in range(_MB_N_SPEC)]
_MB_LOG_HZ    = [_math_mb.log10(hz) for hz in _MB_HZ_BINS]
_MB_LOG_MIN   = _math_mb.log10(20.0)
_MB_LOG_RNG   = _math_mb.log10(20000.0) - _MB_LOG_MIN
_MB_CROSS_HZ  = [120.0, 800.0, 5000.0]
_MB_CROSS_LOG = [_math_mb.log10(f) for f in _MB_CROSS_HZ]

def _mb_band_weight_precomp(f_lo, f_hi, fade_oct=0.15):
    log_lo = _math_mb.log10(f_lo);  log_hi = _math_mb.log10(f_hi)
    out = []
    for lhz in _MB_LOG_HZ:
        if lhz < log_lo - fade_oct or lhz > log_hi + fade_oct:
            out.append(0.0); continue
        w_lo = 1.0
        if lhz < log_lo + fade_oct:
            t = (lhz - (log_lo - fade_oct)) / (2.0 * fade_oct)
            w_lo = 0.5 - 0.5 * _math_mb.cos(_math_mb.pi * t)
        w_hi = 1.0
        if lhz > log_hi - fade_oct:
            t = ((log_hi + fade_oct) - lhz) / (2.0 * fade_oct)
            w_hi = 0.5 - 0.5 * _math_mb.cos(_math_mb.pi * t)
        out.append(w_lo * w_hi)
    return out

def _mb_cross_weight_precomp(f_cross, half_oct=0.25):
    log_c = _math_mb.log10(f_cross)
    out = []
    for lhz in _MB_LOG_HZ:
        dist = abs(lhz - log_c)
        out.append(0.0 if dist >= half_oct else 0.5 + 0.5 * _math_mb.cos(_math_mb.pi * dist / half_oct))
    return out

# All 7 weight arrays computed ONCE at module load — never rebuilt
_MB_WEIGHTS = [
    _mb_band_weight_precomp(20.0,    120.0),
    _mb_cross_weight_precomp(120.0),
    _mb_band_weight_precomp(120.0,   800.0),
    _mb_cross_weight_precomp(800.0),
    _mb_band_weight_precomp(800.0,   5000.0),
    _mb_cross_weight_precomp(5000.0),
    _mb_band_weight_precomp(5000.0, 20000.0),
]
_MB_GR_BAND  = [0, None, 1, None, 2, None, 3]
_MB_SHADER   = None
def _get_mb_shader():
    global _MB_SHADER
    if _MB_SHADER is None:
        _MB_SHADER = _get_shader()
    return _MB_SHADER


def _rp(rack, idx, default=0.0):
    """Safely get rack param by index. Mirrors Racks._rp to avoid import."""
    try:
        return getattr(rack, f'p{idx}', default)
    except Exception:
        return default


RACK_RAIL_H = 32  # duplicated from Racks.py to avoid circular import

# Band display constants — duplicated from rack_base.py (can't import at module level)
BAND_COLORS = [
    (0.2, 0.5, 1.0, 0.85),   # Low      — blue
    (0.2, 0.9, 0.4, 0.85),   # Low-Mid  — green
    (1.0, 0.7, 0.1, 0.85),   # High-Mid — amber
    (1.0, 0.3, 0.3, 0.85),   # High     — red
]
BAND_NAMES = ["Low", "L-Mid", "H-Mid", "High"]
BAND_FREQS = ["<120Hz", "120-800Hz", "800Hz-5kHz", ">5kHz"]


def _draw_gr_meter_band(bx, by, bw, bh, gr_db, scale, signal_norm=0.0):
    """Premier Pro style: green signal bar rising from bottom + red GR cap on top."""
    _draw_rect(bx, by, bw, bh, (0.04, 0.04, 0.04, 1.0))
    sig_h = max(0.0, min(1.0, signal_norm)) * bh
    if sig_h > 0.5:
        gr_h    = max(0.0, min(1.0, gr_db / 24.0)) * sig_h  # 24dB full scale
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


def _draw_multiband_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """Draw multiband compressor body.
    Layout matches agreed sketch:
    - Top 48%: spectrum display with 4 equal-width band curves
    - Bottom 52%: 4 equal columns, each with gain fader left + 2x2 knobs right
    - No coloured backgrounds, only elements carry band colour
    - Equal margins on left and right of band area
    """
    # Lazy imports — avoids circular import at module load time
    import Racks as _racks_mod
    get_rack_channels = _racks_mod.get_rack_channels
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

    shader = _get_mb_shader()  # cached — never recreated per frame

    # ----------------------------------------------------------------
    # SPECTRUM DISPLAY — FabFilter style full-width GR curve
    # Background: grey FFT bars showing audio content
    # Foreground: smooth coloured GR curve dipping at compressed bands
    # ----------------------------------------------------------------
    spec_x = content_x
    spec_y = ry + fader_zone_h + 4*scale
    spec_w = content_w
    spec_h = spec_zone_h - 8*scale

    # Background
    _draw_rect(spec_x, spec_y, spec_w, spec_h, (0.04, 0.04, 0.04, 1.0))
    bv = [(spec_x,spec_y),(spec_x+spec_w,spec_y),
          (spec_x+spec_w,spec_y+spec_h),(spec_x,spec_y+spec_h),(spec_x,spec_y)]
    bb = batch_for_shader(shader,"LINE_STRIP",{"pos":bv})
    shader.bind(); shader.uniform_float("color",(0.15,0.15,0.15,1.0)); bb.draw(shader)

    # (dB grid lines drawn later with correct _db_to_y mapping)

    # ----------------------------------------------------------------
    # 7-LAYER OVERLAPPING SPECTRUM — Premiere Pro multiband style
    #
    # Y AXIS CONVENTION (Blender GPU: y=0 at BOTTOM, increases upward):
    #   spec_y           = bottom of rect = -80dB
    #   spec_y + spec_h  = top of rect    =   0dB
    #   amp = (dB + 80) / 80  →  0.0 at bottom, 1.0 at top
    #   _amp_to_y(amp) = spec_y + amp * spec_h   ← bottom-origin
    #   floor of fill  = spec_y  (the bottom, not spec_y+spec_h)
    # ----------------------------------------------------------------
    import math as _mspec

    LOG_MIN  = _mspec.log10(20.0)
    LOG_RNG  = _mspec.log10(20000.0) - LOG_MIN
    CROSS_HZ = [120.0, 800.0, 5000.0]

    def _freq_to_x(hz):
        return spec_x + (_mspec.log10(max(hz, 20.0)) - LOG_MIN) / LOG_RNG * spec_w

    def _amp_to_y(amp):
        # amp 0.0 → spec_y (bottom = -80dB), amp 1.0 → spec_y+spec_h (top = 0dB)
        return spec_y + max(0.0, min(1.0, amp)) * spec_h

    def _db_to_y(db):
        return _amp_to_y((db + 80.0) / 80.0)

    # --- Fetch 128-bin full-spectrum + GR from engine ---
    # Average across ALL assigned channels so multi-channel racks show
    # a merged spectrum and the graph doesn't go blank.
    # Muted channels still have spec_bins data (the engine updates them
    # even when muted) so we include them for display — the graph should
    # show what's playing through the rack regardless of mute state.
    spec_bins = None
    gr_data   = [0.0, 0.0, 0.0, 0.0]
    if rack.enabled:
        try:
            from Loader import get_engine as _get_eng_mb
            _eng_mb  = _get_eng_mb()
            assigned = get_rack_channels(rack)
            if _eng_mb and assigned:
                _hj_mb = _eng_mb.get_engine()
                if _hj_mb:
                    _s_mb       = _hj_mb.get_state()
                    bin_acc     = [0.0] * 128
                    gr_acc      = [0.0, 0.0, 0.0, 0.0]
                    valid_count = 0
                    for ch in assigned:
                        if not (0 <= ch < 32): continue
                        _raw = list(_s_mb.get_spec_bins(ch))
                        # Include channel if it has any signal at all —
                        # use a very low threshold so muted-but-assigned
                        # channels don't block display from active channels.
                        if max(_raw) > 0.001:
                            for i in range(128):
                                bin_acc[i] += _raw[i]
                            gr_vals = _s_mb.get_gr_levels(ch)
                            if gr_vals:
                                for b in range(4):
                                    gr_acc[b] += gr_vals[b]
                            valid_count += 1
                    if valid_count > 0:
                        spec_bins = [v / valid_count for v in bin_acc]
                        gr_data   = [v / valid_count for v in gr_acc]
        except Exception:
            spec_bins = None

    # ----------------------------------------------------------------
    # 7-LAYER SPECTRUM — uses pre-computed module-level weight arrays.
    # No trig, no log10, no lambda, no shader construction per frame.
    # Only the vertex positions are recomputed (128 muls per layer).
    # ----------------------------------------------------------------
    bc = BAND_COLORS
    layer_colors = [
        ((bc[0][0]*0.5, bc[0][1]*0.5, bc[0][2]*0.5, 0.80),
         (bc[0][0]*0.9+0.05, bc[0][1]*0.9+0.05, bc[0][2]*0.9+0.05, 1.0)),
        ((0.05, 0.55, 0.65, 0.75), (0.1, 0.75, 0.85, 1.0)),
        ((bc[1][0]*0.5, bc[1][1]*0.5, bc[1][2]*0.5, 0.80),
         (bc[1][0]*0.9+0.05, bc[1][1]*0.9+0.05, bc[1][2]*0.9+0.05, 1.0)),
        ((0.65, 0.58, 0.05, 0.75), (0.85, 0.78, 0.1, 1.0)),
        ((bc[2][0]*0.5, bc[2][1]*0.5, bc[2][2]*0.5, 0.80),
         (bc[2][0]*0.9+0.05, bc[2][1]*0.9+0.05, bc[2][2]*0.9+0.05, 1.0)),
        ((0.55, 0.1, 0.65, 0.75), (0.75, 0.2, 0.85, 1.0)),
        ((bc[3][0]*0.5, bc[3][1]*0.5, bc[3][2]*0.5, 0.80),
         (bc[3][0]*0.9+0.05, bc[3][1]*0.9+0.05, bc[3][2]*0.9+0.05, 1.0)),
    ]

    # x positions are constant for a given spec_w — precompute once per draw
    x_positions = [spec_x + ((_MB_LOG_HZ[i] - _MB_LOG_MIN) / _MB_LOG_RNG) * spec_w
                   for i in range(_MB_N_SPEC)]
    floor_y = spec_y

    if spec_bins is not None:
        # GR linear multipliers — one per main band
        gr_muls = [10.0 ** (-max(0.0, float(gr_data[b])) / 20.0) for b in range(4)]

        _sh = _get_mb_shader()  # cached shader — never recreated
        _sh.bind()
        gpu.state.line_width_set(max(1.2, scale * 1.2))

        for layer_idx in range(7):
            weights  = _MB_WEIGHTS[layer_idx]
            gr_band  = _MB_GR_BAND[layer_idx]
            mul      = gr_muls[gr_band] if gr_band is not None else 1.0
            fill_col, edge_col = layer_colors[layer_idx]

            # Build vertex arrays — pure arithmetic, no trig
            verts_fill = []
            pts        = []
            for i in range(_MB_N_SPEC):
                w   = weights[i]
                amp = float(spec_bins[i]) * mul * w
                if amp > 1.0: amp = 1.0
                px  = x_positions[i]
                py  = spec_y + amp * spec_h
                verts_fill.append((px, floor_y))
                verts_fill.append((px, py))
                pts.append((px, py))

            _b = batch_for_shader(_sh, "TRI_STRIP", {"pos": verts_fill})
            _sh.uniform_float("color", fill_col)
            _b.draw(_sh)

            _be = batch_for_shader(_sh, "LINE_STRIP", {"pos": pts})
            _sh.uniform_float("color", edge_col)
            _be.draw(_sh)

        gpu.state.line_width_set(1.0)

    # ----------------------------------------------------------------
    # dB GRID + AXIS LABELS  (drawn over waveforms)
    # 0dB at top (spec_y+spec_h), -80dB at bottom (spec_y)
    # ----------------------------------------------------------------
    fs_db = max(1, int(7*scale))
    for db_val, label in [(0,"0dB"),(-10,"-10"),(-20,"-20"),(-40,"-40"),(-60,"-60"),(-80,"-80")]:
        gy = _db_to_y(db_val)
        if spec_y - 1 <= gy <= spec_y + spec_h + 1:
            bright = 0.22 if db_val == 0 else 0.10
            _draw_rect(spec_x, gy, spec_w, max(0.5, scale*0.5),
                       (bright, bright, bright, 1.0))
            tw = _text_width(label, fs_db)
            _draw_text(label, spec_x - tw - 3*scale, gy - fs_db*0.5,
                       fs_db, (0.35, 0.35, 0.35, 1.0))

    # ----------------------------------------------------------------
    # CROSSOVER DIVIDERS + Hz LABELS
    # ----------------------------------------------------------------
    cross_labels = ["120Hz", "800Hz", "5kHz"]
    for f_c, lbl in zip(CROSS_HZ, cross_labels):
        dx    = _freq_to_x(f_c)
        _draw_rect(dx, spec_y, max(0.5, scale*0.5), spec_h, (0.4, 0.4, 0.4, 0.5))
        fs_cr = max(1, int(7*scale))
        tw_cr = _text_width(lbl, fs_cr)
        _draw_text(lbl, dx - tw_cr/2, spec_y + spec_h + 2*scale,
                   fs_cr, (0.4, 0.4, 0.4, 1.0))

    for hz_mark, lbl_mark in [(20,"20"),(50,"50"),(100,"100"),(200,"200"),
                               (500,"500"),(1000,"1k"),(2000,"2k"),
                               (5000,"5k"),(10000,"10k"),(20000,"20k")]:
        mx   = _freq_to_x(hz_mark)
        fs_m = max(1, int(6*scale))
        tw_m = _text_width(lbl_mark, fs_m)
        if spec_x + 4*scale <= mx <= spec_x + spec_w - 4*scale:
            _draw_text(lbl_mark, mx - tw_m/2, spec_y + spec_h + 10*scale,
                       fs_m, (0.28, 0.28, 0.28, 1.0))

    # ----------------------------------------------------------------
    # SETTINGS-DRIVEN RESPONSE CURVE OVERLAY
    #
    # Uses the SAME _db_to_y axis as the spectrum (0dB top, -80dB bottom).
    # The curve shows actual output level: REF_IN + gain_db + gr_db.
    # With defaults (0dB gain, threshold not hit) it sits at -18dB.
    # Heavy compression pushes it toward -60 or -70dB — a large visible drop
    # that maps directly to the spectrum scale the eye is already reading.
    # ----------------------------------------------------------------
    # REF_INPUT_DB: typical voice programme at -18dBFS.
    # PCM is normalised to -1..+1, same as the old engine — no sidechain offset needed.
    REF_INPUT_DB = -18.0

    def _band_output_db(b):
        """Actual output dB for this band at the reference input level."""
        thr_db  = -60.0 + _rp(rack, b)       * 40.0   # -60..-20dB, matches C++ denorm_threshold
        ratio   =  1.0  + _rp(rack, b + 4)   * 19.0   # 1..20
        knee_db =  0.5  + _rp(rack, b + 20)  * 23.5   # 0.5..24 dB
        gain_db = (_rp(rack, b + 16, 0.5) - 0.5) * 24.0  # -12..+12 dB
        half_k  = knee_db * 0.5
        in_db   = REF_INPUT_DB
        if in_db <= thr_db - half_k:
            gr_db = 0.0
        elif in_db <= thr_db + half_k and knee_db > 0.0:
            x     = in_db - thr_db + half_k
            gr_db = (1.0 / ratio - 1.0) * (x * x) / (2.0 * knee_db)
        else:
            gr_db = (in_db - thr_db) * (1.0 / ratio - 1.0)
        return REF_INPUT_DB + gain_db + gr_db  # output dB, same scale as spectrum

    band_out_db = [_band_output_db(b) for b in range(4)]

    # Reference line at -18dB (where curve sits with no processing)
    ref_y = _db_to_y(REF_INPUT_DB)
    _draw_rect(spec_x, ref_y, spec_w, max(0.8, scale * 0.6),
               (0.5, 0.5, 0.5, 0.55))

    # Map crossover Hz to fractional x positions
    cross_frac = [(_mspec.log10(f) - LOG_MIN) / LOG_RNG for f in CROSS_HZ]

    N_CURVE   = 200
    curve_pts = []
    for i in range(N_CURVE + 1):
        fx = i / N_CURVE
        if   fx <= cross_frac[0]:
            bi = 0; bt = fx / max(cross_frac[0], 1e-9)
        elif fx <= cross_frac[1]:
            bi = 1; bt = (fx - cross_frac[0]) / max(cross_frac[1] - cross_frac[0], 1e-9)
        elif fx <= cross_frac[2]:
            bi = 2; bt = (fx - cross_frac[1]) / max(cross_frac[2] - cross_frac[1], 1e-9)
        else:
            bi = 3; bt = (fx - cross_frac[2]) / max(1.0 - cross_frac[2], 1e-9)
        bt       = max(0.0, min(1.0, bt))
        s        = bt * bt * (3.0 - 2.0 * bt)
        db_here  = band_out_db[bi] + (band_out_db[min(3, bi+1)] - band_out_db[bi]) * s
        # Clamp to display range so curve never leaves the graph
        db_here  = max(-79.0, min(0.0, db_here))
        px = spec_x + fx * spec_w
        py = _db_to_y(db_here)
        curve_pts.append((px, py, bi))

    # Fill between curve and the -18dB reference line
    for i in range(len(curve_pts) - 1):
        px1, py1, bi = curve_pts[i]
        px2, py2, _  = curve_pts[i + 1]
        col           = BAND_COLORS[bi]
        r, g, b_c, _a = col
        y_lo = min(py1, ref_y);  y_hi = max(py1, ref_y)
        fh   = y_hi - y_lo
        if fh > 0.5:
            _draw_rect(px1, y_lo, max(px2 - px1, 0.5), fh,
                       (r * 0.45, g * 0.45, b_c * 0.45, 0.45))

    # Curve line — coloured by band
    for i in range(len(curve_pts) - 1):
        px1, py1, bi = curve_pts[i]
        px2, py2, _  = curve_pts[i + 1]
        col           = BAND_COLORS[bi]
        r, g, b_c, _a = col
        _draw_line(px1, py1, px2, py2,
                   (min(1.0, r*1.4), min(1.0, g*1.4), min(1.0, b_c*1.4), 1.0),
                   max(2.0, scale * 2.0))

    # Band-centre dots + output dB labels
    band_centres_hz = [60.0, 350.0, 2000.0, 12000.0]
    for band in range(4):
        bx_c  = _freq_to_x(band_centres_hz[band])
        py_c  = _db_to_y(band_out_db[band])
        py_c  = max(spec_y + 4*scale, min(spec_y + spec_h - 4*scale, py_c))
        col   = BAND_COLORS[band]
        r, g, b_c, _a = col
        _draw_circle(bx_c, py_c, 5*scale,
                     (min(1.0, r*1.5), min(1.0, g*1.5), min(1.0, b_c*1.5), 1.0))
        _draw_circle(bx_c, py_c, 5*scale, (0.08, 0.08, 0.08, 0.7), filled=False)
        fs_lbl = max(1, int(7*scale))
        lbl    = f"{band_out_db[band]:.1f}dB"
        tw_lbl = _text_width(lbl, fs_lbl)
        _draw_text(lbl, bx_c - tw_lbl/2, py_c + 7*scale,
                   fs_lbl, (r, g, b_c, 0.9))

    # Band name labels at bottom of spectrum
    for band in range(4):
        col  = BAND_COLORS[band]
        bx_c = _freq_to_x(band_centres_hz[band])
        fs_bn = max(1, int(8*scale))
        tw_bn = _text_width(BAND_NAMES[band], fs_bn)
        _draw_text(BAND_NAMES[band], bx_c - tw_bn/2,
                   spec_y + 2*scale,
                   fs_bn, (col[0]*0.8, col[1]*0.8, col[2]*0.8, 0.85))

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
    knob_r        = min(knob_area_w * 0.18, 18*scale)
    knob_r        = max(knob_r, 12*scale)
    knob_col_gap  = knob_area_w / 2
    knob_label_h  = 22*scale   # space needed below each knob for labels
    knob_gap      = 6*scale    # gap between top-row label and bottom-row knob

    for band in range(4):
      try:
        bx  = content_x + band * band_w
        col = BAND_COLORS[band]
        col_rgb = col[:3]  # _draw_knob expects 3-tuple; col has alpha

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

        # GR meter — slim bar to the right of the gain fader
        # Shows live gain reduction for this band, 0dB at top filling downward
        gr_meter_w = 6*scale
        gr_meter_x = fdr_x + fdr_w + 3*scale
        gr_meter_y = fdr_y
        gr_meter_h = fdr_h

        # Reuse gr_data already fetched at the top of this function —
        # avoids calling get_engine() 4 more times per frame per rack.
        gr_db_band = float(gr_data[band]) if band < len(gr_data) else 0.0
        sig_norm   = 0.0
        if spec_bins is not None and rack.enabled:
            try:
                from Loader import get_engine as _get_eng_gr
                _eng_gr = _get_eng_gr()
                assigned_gr2 = get_rack_channels(rack)
                if _eng_gr and assigned_gr2:
                    _hj_gr = _eng_gr.get_engine()
                    if _hj_gr:
                        # Average band level across assigned channels
                        lvl_acc = 0.0; lvl_cnt = 0
                        _s_gr = _hj_gr.get_state()
                        for ch_gr2 in assigned_gr2:
                            if 0 <= ch_gr2 < 32:
                                bl = _s_gr.get_band_levels(ch_gr2)
                                if bl and band < len(bl):
                                    lvl_acc += float(bl[band]); lvl_cnt += 1
                        if lvl_cnt > 0:
                            sig_norm = min(1.0, (lvl_acc / lvl_cnt) * 6.0)
            except Exception:
                pass

        _draw_gr_meter_band(gr_meter_x, gr_meter_y, gr_meter_w,
                            gr_meter_h, gr_db_band, scale,
                            signal_norm=sig_norm)

        # Thin divider after fader strip
        div_x = bx + fader_strip_w
        _draw_rect(div_x, ctrl_y, max(0.5,scale*0.5), ctrl_h, (0.18,0.18,0.18,1.0))

        # --- 3x2 KNOB GRID (Thr/Ratio/Knee top, Atk/Rel/Gain bottom) ---
        knob_base_x = div_x + 4*scale
        knob_col_gap3 = knob_area_w / 3
        kx0 = knob_base_x + knob_col_gap3 * 0.5
        kx1 = knob_base_x + knob_col_gap3 * 1.5
        kx2 = knob_base_x + knob_col_gap3 * 2.5
        ky1 = ctrl_y + ctrl_h * 0.25              # bottom row knob centre
        ky0 = ctrl_y + ctrl_h * 0.75              # top row knob centre

        # Threshold (p0-p3)
        thr_n   = _rp(rack, band)
        thr_db  = -60.0 + thr_n*40.0   # -60..-20dB, matches C++ denorm_threshold
        _draw_knob(kx0, ky0, knob_r, thr_n, col_rgb,
                   "Thr", f"{thr_db:.0f}dB", scale)

        # Ratio (p4-p7)
        rat_n  = _rp(rack, band+4)
        ratio  = 1.0 + rat_n*19.0
        _draw_knob(kx1, ky0, knob_r, rat_n, col_rgb,
                   "Ratio", f"{ratio:.1f}:1", scale)

        # Knee (p20-p23)
        kne_n  = _rp(rack, band+20, 0.14)
        kne_db = 0.5 + kne_n*23.5
        _draw_knob(kx2, ky0, knob_r, kne_n, col_rgb,
                   "Knee", f"{kne_db:.1f}dB", scale)

        # Attack (p8-p11)
        atk_n  = _rp(rack, band+8)
        atk_ms = 0.1 + atk_n*99.9
        _draw_knob(kx0, ky1, knob_r, atk_n, col_rgb,
                   "Atk", f"{atk_ms:.0f}ms", scale)

        # Release (p12-p15)
        rel_n  = _rp(rack, band+12)
        rel_ms = 10.0 + rel_n*990.0
        _draw_knob(kx1, ky1, knob_r, rel_n, col_rgb,
                   "Rel", f"{rel_ms:.0f}ms", scale)

        # Gain (p16-p19)
        gain_n  = _rp(rack, band+16, 0.5)
        gain_db = (gain_n - 0.5) * 24.0
        _draw_knob(kx2, ky1, knob_r, gain_n, col_rgb,
                   "Gain", f"{gain_db:+.0f}dB", scale)

        # Column divider (not after last band)
        if band < 3:
            _draw_rect(bx + band_w, fader_area_y,
                       max(0.5,scale*0.5), fader_area_h,
                       (0.18,0.18,0.18,1.0))
      except Exception as e:
        import traceback
        print(f"[MB] band {band} draw error: {e}")
        print(traceback.format_exc())

