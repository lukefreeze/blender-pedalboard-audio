# =============================================================================
# rack_eq.py
# Parametric EQ rack UI (7-band)
# ┌─ LAYOUT CONSTANTS ─────────────────────────────────────────────────────┐
# │ RACK_EXPANDED_H_EQ — change height here
# └────────────────────────────────────────────────────────────────────────┘
# =============================================================================

import math
import bpy
import gpu
import blf
from gpu_extras.batch import batch_for_shader

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

# EQ band definitions and frequency/Q scale constants
EQ_BANDS = [
    # (name, colour, filter_type, default_freq_hz, default_Q)
    ("Low",    (0.25, 0.55, 1.0),  "low_shelf",  100.0,  0.7),
    ("L-Mid",  (0.25, 0.85, 0.45), "peak",       300.0,  1.0),
    ("Mid",    (0.85, 0.75, 0.15), "peak",      1000.0,  1.0),
    ("H-Mid",  (1.0,  0.45, 0.15), "peak",      4000.0,  1.0),
    ("High",   (0.9,  0.25, 0.7),  "high_shelf",10000.0, 0.7),
]
EQ_FREQ_MIN_LOG = math.log10(20.0)
EQ_FREQ_MAX_LOG = math.log10(20000.0)
EQ_Q_MIN_LOG    = math.log10(0.1)
EQ_Q_MAX_LOG    = math.log10(10.0)


def _eq_freq_from_norm(norm):
    """Convert 0-1 norm to Hz (log scale)."""
    return 10.0 ** (EQ_FREQ_MIN_LOG + norm * (EQ_FREQ_MAX_LOG - EQ_FREQ_MIN_LOG))


def _eq_freq_to_norm(hz):
    return max(0.0, min(1.0,
        (math.log10(max(20.0, hz)) - EQ_FREQ_MIN_LOG) /
        (EQ_FREQ_MAX_LOG - EQ_FREQ_MIN_LOG)))


def _eq_q_from_norm(norm):
    return 10.0 ** (EQ_Q_MIN_LOG + norm * (EQ_Q_MAX_LOG - EQ_Q_MIN_LOG))


def _eq_q_to_norm(q):
    return max(0.0, min(1.0,
        (math.log10(max(0.1, q)) - EQ_Q_MIN_LOG) /
        (EQ_Q_MAX_LOG - EQ_Q_MIN_LOG)))


def _eq_get_band(rack, band_idx):
    """Return (gain_db, freq_hz, q, freq_norm, q_norm) for a band."""
    gain_norm = getattr(rack, f'p{band_idx}',      0.5)
    freq_norm = getattr(rack, f'p{band_idx + 5}', -1.0)
    q_norm    = getattr(rack, f'p{band_idx + 10}',-1.0)
    gain_db   = (gain_norm - 0.5) * 48.0   # -24..+24 dB
    _, _, _, def_freq, def_q = EQ_BANDS[band_idx]
    if freq_norm < 0.0:
        freq_norm = _eq_freq_to_norm(def_freq)
    if q_norm < 0.0:
        q_norm = _eq_q_to_norm(def_q)
    return gain_db, _eq_freq_from_norm(freq_norm), _eq_q_from_norm(q_norm), freq_norm, q_norm


def _eq_biquad_response(freq_hz, gain_db, band_filter_type, q, f_test):
    """
    Compute magnitude response in dB at f_test Hz for one EQ band.
    Uses Audio EQ Cookbook biquad formulae (same as Loader.py).
    sample_rate assumed 48000 for display purposes.
    """
    sr = 48000.0
    w0 = 2.0 * math.pi * freq_hz / sr
    wt = 2.0 * math.pi * f_test  / sr
    cw0, sw0 = math.cos(w0), math.sin(w0)
    cwt       = math.cos(wt)
    swt       = math.sin(wt)
    alpha     = sw0 / (2.0 * q)
    A         = 10.0 ** (gain_db / 40.0)

    if band_filter_type == "low_shelf":
        sq = 2.0 * math.sqrt(A) * alpha
        b0 = A*((A+1) - (A-1)*cw0 + sq)
        b1 = 2*A*((A-1) - (A+1)*cw0)
        b2 = A*((A+1) - (A-1)*cw0 - sq)
        a0 = (A+1) + (A-1)*cw0 + sq
        a1 = -2*((A-1) + (A+1)*cw0)
        a2 = (A+1) + (A-1)*cw0 - sq
    elif band_filter_type == "high_shelf":
        sq = 2.0 * math.sqrt(A) * alpha
        b0 = A*((A+1) + (A-1)*cw0 + sq)
        b1 = -2*A*((A-1) + (A+1)*cw0)
        b2 = A*((A+1) + (A-1)*cw0 - sq)
        a0 = (A+1) - (A-1)*cw0 + sq
        a1 = 2*((A-1) - (A+1)*cw0)
        a2 = (A+1) - (A-1)*cw0 - sq
    else:  # peak
        alpha_a = sw0 / (2.0 * q)
        b0 = 1 + alpha_a * A
        b1 = -2 * cw0
        b2 = 1 - alpha_a * A
        a0 = 1 + alpha_a / A
        a1 = -2 * cw0
        a2 = 1 - alpha_a / A

    # Evaluate H(e^jwt) via the bilinear s→z substitution
    # |H(z)| at z=e^jwt:  num = b0 + b1*e^-jwt + b2*e^-2jwt
    #                      den = a0 + a1*e^-jwt + a2*e^-2jwt
    try:
        nr = b0/a0 + (b1/a0)*cwt + (b2/a0)*math.cos(2*wt)
        ni = -(b1/a0)*swt - (b2/a0)*math.sin(2*wt)
        dr = 1.0   + (a1/a0)*cwt + (a2/a0)*math.cos(2*wt)
        di = -(a1/a0)*swt - (a2/a0)*math.sin(2*wt)
        mag_sq = (nr*nr + ni*ni) / max(1e-30, dr*dr + di*di)
        return 10.0 * math.log10(max(1e-10, mag_sq))
    except Exception:
        return 0.0


def _draw_eq_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """
    Parametric EQ rack body — Adobe Premiere Pro style.

    Layout:
      Top 62% : frequency display
                - Audio waveform silhouette (mirrored, from RMS envelope)
                - dB grid +/-18dB, octave frequency grid
                - Per-band dim coloured response curves
                - Combined cyan response curve + filled teal area
                - 7 draggable band handle dots
      Bottom 38%: 7-column knob strip  L | 1 | 2 | 3 | 4 | 5 | H
                  Each column: band label, Gain knob, Freq knob, Q knob

    Parameter storage (7-band layout):
      p0-p6  : gain (0.5 = 0dB, range -24..+24dB)
      p7-p13 : freq (log-normalised 0-1)
      p14-p20: Q    (log-normalised 0-1)
    """
    # Lazy imports — avoids circular import at module load time
    import Racks as _racks_mod
    get_rack_channels = _racks_mod.get_rack_channels
    try:
        import core.audio as _audio_mod
        _fft_timeline     = _audio_mod._fft_timeline
        _gr_timeline      = _audio_mod._gr_timeline
        _fft_timeline_full = getattr(_audio_mod, '_fft_timeline_full', {})
        _fft_timeline_eq_input = getattr(_audio_mod, '_fft_timeline_eq_input', {})
        _gr_levels        = getattr(_audio_mod, '_gr_levels', {})
    except Exception:
        _fft_timeline = _gr_timeline = _fft_timeline_full = {}
        _fft_timeline_eq_input = _gr_levels = {}
    import math as _m

    EQ7_BANDS = [
        ("L",  (0.30, 0.60, 1.00), "low_shelf",   80.0,  0.7),
        ("1",  (0.25, 0.90, 0.55), "peak",        250.0,  1.0),
        ("2",  (0.50, 0.90, 0.20), "peak",        700.0,  1.0),
        ("3",  (0.95, 0.85, 0.10), "peak",       2000.0,  1.0),
        ("4",  (1.00, 0.55, 0.10), "peak",       5000.0,  1.0),
        ("5",  (0.95, 0.30, 0.55), "peak",      10000.0,  1.0),
        ("H",  (0.80, 0.30, 1.00), "high_shelf", 16000.0, 0.7),
    ]
    N_BANDS = 7

    def _get_b(bi):
        gn  = getattr(rack, f"p{bi}",      0.5)
        fn  = getattr(rack, f"p{bi + 7}", -1.0)
        qn  = getattr(rack, f"p{bi + 14}",-1.0)
        gdb = (gn - 0.5) * 48.0
        _, _, bft, df, dq = EQ7_BANDS[bi]
        if fn < 0.0: fn = _eq_freq_to_norm(df)
        if qn < 0.0: qn = _eq_q_to_norm(dq)
        return gdb, _eq_freq_from_norm(fn), _eq_q_from_norm(qn), gn, fn, qn

    # -----------------------------------------------------------------------
    # Geometry
    # -----------------------------------------------------------------------
    rail_h   = RACK_RAIL_H * scale
    body_h   = rh - rail_h
    ch_btn_w = 108 * scale
    margin_l = 42 * scale
    margin_r = ch_btn_w + 8 * scale

    disp_x = rx + margin_l
    disp_w = rw - margin_l - margin_r
    # 54% display / 44% knobs — more knob room for larger controls + visible labels
    disp_h = body_h * 0.54
    disp_y = ry + body_h - disp_h - 2 * scale   # top of body (display at top)

    knob_h = body_h * 0.44 - 6 * scale
    knob_y = ry + 2 * scale                      # knob strip at bottom of body

    db_range  = 18.0
    zero_db_y = disp_y + disp_h * 0.5
    px_per_db = (disp_h * 0.5) / db_range

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    # -----------------------------------------------------------------------
    # DISPLAY BACKGROUND + GRID
    # -----------------------------------------------------------------------
    _draw_rect(disp_x, disp_y, disp_w, disp_h, (0.035, 0.035, 0.040, 1.0))

    for db_val in (18, 12, 6, 0, -6, -12, -18):
        gy = zero_db_y + db_val * px_per_db
        if not (disp_y <= gy <= disp_y + disp_h):
            continue
        bright = 0.22 if db_val == 0 else 0.09
        lw     = max(1.0, scale) if db_val == 0 else max(0.5, scale * 0.5)
        _draw_rect(disp_x, gy, disp_w, lw, (bright, bright, bright, 0.9))
        lbl   = "0dB" if db_val == 0 else f"{db_val:+d}"
        fs_db = max(1, int(7 * scale))
        tw_db = _text_width(lbl, fs_db)
        _draw_text(lbl, disp_x - tw_db - 4 * scale,
                   gy - fs_db * 0.5, fs_db, (0.30, 0.30, 0.30, 1.0))

    freq_marks = [
        (20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"),
        (1000, "1k"), (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k"),
    ]
    for fhz_m, lbl_m in freq_marks:
        t_m  = (_m.log10(fhz_m) - EQ_FREQ_MIN_LOG) / (EQ_FREQ_MAX_LOG - EQ_FREQ_MIN_LOG)
        gx_m = disp_x + t_m * disp_w
        if not (disp_x <= gx_m <= disp_x + disp_w):
            continue
        _draw_rect(gx_m, disp_y, max(0.5, scale * 0.5), disp_h,
                   (0.11, 0.11, 0.11, 1.0))
        fs_f = max(1, int(7 * scale))
        tw_f = _text_width(lbl_m, fs_f)
        _draw_text(lbl_m, gx_m - tw_f * 0.5,
                   disp_y - 11 * scale, fs_f, (0.28, 0.28, 0.28, 1.0))

    # -----------------------------------------------------------------------
    # SPECTRUM ANALYSER — FabFilter Pro-Q style
    # Reads _fft_timeline built by Loader.py during batch processing.
    # Shape: (n_snaps, 4, 32) — 4 bands × 32 log-spaced bins = 128 points.
    # Bands: 0=20-120Hz  1=120-800Hz  2=800-5000Hz  3=5000-20000Hz
    #
    # Two filled silhouette layers, bottom-to-top (NOT bars):
    #   Layer 1 — pre-EQ:  dark grey filled polygon from floor up
    #   Layer 2 — post-EQ: same data with current EQ gain curve applied
    # Jagged/spiky look comes naturally from 128 narrow adjacent bins.
    # Silent / dark until audio has been processed at least once.
    # -----------------------------------------------------------------------
    try:
        from Loader import _fft_timeline
        import math as _ms

        assigned_sp = get_rack_channels(rack)
        if assigned_sp:
            ch_sp = list(assigned_sp)[0]
            tl_sp = _fft_timeline.get(ch_sp)
            if tl_sp is not None and len(tl_sp['snapshots']) > 0:
                scene_sp    = bpy.context.scene
                cur_frame   = scene_sp.frame_current if scene_sp else 0
                start_frame = tl_sp['start_frame']
                fps_sp      = tl_sp['fps']
                snap_sec    = tl_sp['snap_frames'] / tl_sp['sr']
                elapsed_sec = max(0.0, (cur_frame - start_frame) / fps_sp)
                snap_f      = elapsed_sec / snap_sec
                snap_idx    = max(0, min(len(tl_sp['snapshots']) - 1, int(snap_f)))
                frac        = snap_f - int(snap_f)

                import numpy as _nps
                frame_data = tl_sp['snapshots'][snap_idx].astype(float)
                if frac > 0.0 and snap_idx + 1 < len(tl_sp['snapshots']):
                    frame_data = (frame_data * (1.0 - frac) +
                                  tl_sp['snapshots'][snap_idx + 1].astype(float) * frac)

                # Build (x_norm 0-1, amplitude 0-1) pairs for all 128 bins
                CROSSOVERS = [20, 120, 800, 5000, 20000]
                BINS_PER   = 32
                LOG_MIN    = _ms.log10(20.0)
                LOG_RANGE  = _ms.log10(20000.0) - LOG_MIN

                freq_amp = []   # (t_x, amp) sorted by frequency
                import numpy as _nps2
                for band in range(4):
                    f_lo = CROSSOVERS[band]
                    f_hi = CROSSOVERS[band + 1]
                    bins = frame_data[band]
                    freqs = _nps2.logspace(_ms.log10(max(f_lo, 1.0)),
                                           _ms.log10(f_hi), BINS_PER)
                    for bi in range(BINS_PER):
                        t_x = (_ms.log10(max(float(freqs[bi]), 20.0)) - LOG_MIN) / LOG_RANGE
                        freq_amp.append((t_x, max(0.0, min(1.0, float(bins[bi])))))

                freq_amp.sort(key=lambda p: p[0])

                def _spectrum_fill(pairs, color, h_scale=0.90):
                    """Draw filled silhouette from floor up as a single polygon."""
                    if len(pairs) < 2:
                        return
                    verts = []
                    for t_x, amp in pairs:
                        bx = disp_x + t_x * disp_w
                        verts.append((bx, disp_y))
                        verts.append((bx, disp_y + amp * disp_h * h_scale))
                    if len(verts) >= 4:
                        bf = batch_for_shader(shader, "TRI_STRIP", {"pos": verts})
                        shader.bind()
                        shader.uniform_float("color", color)
                        bf.draw(shader)

                def _spectrum_edge(pairs, color, h_scale=0.90):
                    """Draw the top edge of the spectrum as a LINE_STRIP."""
                    if len(pairs) < 2:
                        return
                    verts = [(disp_x + t_x * disp_w,
                              disp_y + amp * disp_h * h_scale)
                             for t_x, amp in pairs]
                    bt = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
                    gpu.state.line_width_set(max(1.0, scale * 0.7))
                    shader.bind()
                    shader.uniform_float("color", color)
                    bt.draw(shader)
                    gpu.state.line_width_set(1.0)

                # --- Layer 1: pre-EQ spectrum (dark grey) ---
                _spectrum_fill(freq_amp, (0.17, 0.17, 0.19, 0.82))
                _spectrum_edge(freq_amp, (0.32, 0.32, 0.36, 0.55))

                # --- Layer 2: post-EQ spectrum (EQ curve applied) ---
                # Multiply each bin's amplitude by the linear gain the current
                # EQ settings produce at that frequency — shows shaping live.
                try:
                    band_params_sp = [_get_b(bi) for bi in range(N_BANDS)]
                    post_pairs = []
                    for t_x, amp in freq_amp:
                        freq_hz = 20.0 * (10.0 ** (t_x * LOG_RANGE))
                        eq_db = 0.0
                        for bi in range(N_BANDS):
                            gdb_s, fhz_s, q_s, _, _, _ = band_params_sp[bi]
                            eq_db += _eq_biquad_response(
                                fhz_s, gdb_s, EQ7_BANDS[bi][2], q_s, freq_hz)
                        lin = 10.0 ** (eq_db / 20.0)
                        post_pairs.append((t_x, max(0.0, min(1.0, amp * lin))))

                    _spectrum_fill(post_pairs, (0.28, 0.30, 0.35, 0.72))
                    _spectrum_edge(post_pairs, (0.52, 0.58, 0.68, 0.90))
                except Exception:
                    pass  # post-EQ layer is bonus — never block pre-EQ draw

    except Exception:
        pass  # spectrum is decorative — never crash the draw callback

    # -----------------------------------------------------------------------
    # EQ CURVES
    # -----------------------------------------------------------------------
    N_PTS = 300
    try:
        band_params = [_get_b(bi) for bi in range(N_BANDS)]

        # Combined response
        combined_db = []
        for pi in range(N_PTS):
            t      = pi / (N_PTS - 1)
            f_test = _eq_freq_from_norm(t)
            total  = 0.0
            for bi in range(N_BANDS):
                gdb_b, fhz_b, q_b, _, _, _ = band_params[bi]
                total += _eq_biquad_response(fhz_b, gdb_b, EQ7_BANDS[bi][2], q_b, f_test)
            combined_db.append(total)

        # Curve points (clamped)
        curve_pts = []
        for pi in range(N_PTS):
            t   = pi / (N_PTS - 1)
            db  = max(-db_range * 1.1, min(db_range * 1.1, combined_db[pi]))
            cpx = disp_x + t * disp_w
            cpy = zero_db_y + db * px_per_db
            cpy = max(disp_y + 1, min(disp_y + disp_h - 1, cpy))
            curve_pts.append((cpx, cpy))

        # Filled teal area between curve and 0dB
        y_zero  = max(disp_y, min(disp_y + disp_h, zero_db_y))
        fill_v2 = []
        for i in range(N_PTS):
            fill_v2.append(curve_pts[i])
            fill_v2.append((curve_pts[i][0], y_zero))
        if len(fill_v2) >= 4:
            bfill = batch_for_shader(shader, "TRI_STRIP", {"pos": fill_v2})
            shader.bind()
            shader.uniform_float("color", (0.05, 0.35, 0.52, 0.18))
            bfill.draw(shader)

        # Per-band dim curves
        for bi in range(N_BANDS):
            gdb_b, fhz_b, q_b, _, _, _ = band_params[bi]
            if abs(gdb_b) < 0.5:
                continue
            bcol_b = EQ7_BANDS[bi][1]
            bftype_b = EQ7_BANDS[bi][2]
            bpts = []
            for pi in range(N_PTS):
                t   = pi / (N_PTS - 1)
                db  = _eq_biquad_response(fhz_b, gdb_b, bftype_b, q_b,
                                          _eq_freq_from_norm(t))
                db  = max(-db_range * 1.1, min(db_range * 1.1, db))
                bpx  = disp_x + t * disp_w
                bpyv = zero_db_y + db * px_per_db
                bpyv = max(disp_y + 1, min(disp_y + disp_h - 1, bpyv))
                bpts.append((bpx, bpyv))
            if len(bpts) >= 2:
                bb_b = batch_for_shader(shader, "LINE_STRIP", {"pos": bpts})
                gpu.state.line_width_set(max(1.0, scale))
                shader.bind()
                shader.uniform_float("color", (*bcol_b, 0.35))
                bb_b.draw(shader)
                gpu.state.line_width_set(1.0)

        # Combined curve (bright cyan)
        if len(curve_pts) >= 2:
            bc = batch_for_shader(shader, "LINE_STRIP", {"pos": curve_pts})
            gpu.state.line_width_set(max(2.0, scale * 2.0))
            shader.bind()
            shader.uniform_float("color", (0.15, 0.75, 1.00, 0.95))
            bc.draw(shader)
            gpu.state.line_width_set(1.0)

        # Band handle dots — positioned on the combined curve at each band freq
        for bi in range(N_BANDS):
            gdb_b, fhz_b, q_b, gn_b, fn_b, qn_b = band_params[bi]
            bname_b, bcol_b = EQ7_BANDS[bi][0], EQ7_BANDS[bi][1]
            dot_x = disp_x + fn_b * disp_w
            f_here = _eq_freq_from_norm(fn_b)
            dot_db = sum(
                _eq_biquad_response(band_params[b][1], band_params[b][0],
                                    EQ7_BANDS[b][2], band_params[b][2], f_here)
                for b in range(N_BANDS)
            )
            dot_db = max(-db_range, min(db_range, dot_db))
            dot_y  = zero_db_y + dot_db * px_per_db
            dot_y  = max(disp_y + 5*scale, min(disp_y + disp_h - 5*scale, dot_y))
            dot_r  = max(6*scale, 7*scale)
            _draw_circle(dot_x, dot_y, dot_r + 2*scale, (*bcol_b, 0.20))
            _draw_circle(dot_x, dot_y, dot_r,           (*bcol_b, 1.00))
            _draw_circle(dot_x, dot_y, dot_r * 0.35,    (1.0, 1.0, 1.0, 0.80))
            fs_dot = max(1, int(8 * scale))
            tw_dot = _text_width(bname_b, fs_dot)
            _draw_text(bname_b, dot_x - tw_dot * 0.5,
                       dot_y - dot_r - 11 * scale, fs_dot, (*bcol_b, 0.9))

    except Exception:
        import traceback; traceback.print_exc()

    # Display border
    bv3 = [(disp_x, disp_y), (disp_x + disp_w, disp_y),
           (disp_x + disp_w, disp_y + disp_h),
           (disp_x, disp_y + disp_h), (disp_x, disp_y)]
    bb3 = batch_for_shader(shader, "LINE_STRIP", {"pos": bv3})
    shader.bind(); shader.uniform_float("color", (0.20, 0.20, 0.20, 1.0))
    bb3.draw(shader)

    # -----------------------------------------------------------------------
    # KNOB STRIP  — 7 equal columns: L | 1 | 2 | 3 | 4 | 5 | H
    # Each column (top to bottom): label, Gain knob, Freq knob, Q knob
    # -----------------------------------------------------------------------
    col_w    = disp_w / N_BANDS
    kr_gain  = min(max(16 * scale, col_w * 0.18), 26 * scale)
    kr_small = min(max(11 * scale, col_w * 0.13), 18 * scale)

    # Row layout: place rows evenly within knob_h, top-to-bottom:
    # label → gain knob → freq knob → Q knob
    # Divide available height across 4 rows with equal spacing.
    row_slot = knob_h / 4.0
    row_lbl  = knob_y + knob_h - row_slot * 0.28
    row_gain = knob_y + knob_h - row_slot * 1.2
    row_freq = knob_y + knob_h - row_slot * 2.3
    row_q    = knob_y + knob_h - row_slot * 3.35
    # Clamp knob radii so they fit within a slot
    kr_gain  = min(kr_gain,  row_slot * 0.42)
    kr_small = min(kr_small, row_slot * 0.32)

    fs_lbl = max(1, int(11 * scale))

    for bi in range(N_BANDS):
        gdb_b, fhz_b, q_b, gn_b, fn_b, qn_b = band_params[bi]
        bname_b, bcol_b, bftype_b = EQ7_BANDS[bi][0], EQ7_BANDS[bi][1], EQ7_BANDS[bi][2]
        col_cx = disp_x + (bi + 0.5) * col_w

        # Band label
        tw_l = _text_width(bname_b, fs_lbl)
        _draw_text(bname_b, col_cx - tw_l * 0.5,
                   row_lbl - fs_lbl, fs_lbl, (*bcol_b, 1.0))

        # Gain knob
        gain_str = f"{gdb_b:+.1f}dB"
        _draw_knob(col_cx, row_gain, kr_gain, gn_b, bcol_b,
                   "Gain", gain_str, scale)

        # Freq knob
        freq_str = (f"{fhz_b/1000:.2f}k" if fhz_b >= 1000
                    else f"{fhz_b:.0f}Hz")
        _draw_knob(col_cx, row_freq, kr_small, fn_b, bcol_b,
                   "Freq", freq_str, scale)

        # Q knob (peaks only)
        if bftype_b == "peak":
            q_str = f"{q_b:.2f}"
            _draw_knob(col_cx, row_q, kr_small, qn_b, bcol_b,
                       "Q", q_str, scale)
        else:
            _draw_circle(col_cx, row_q, kr_small, (0.09, 0.09, 0.09, 1.0))
            _draw_circle(col_cx, row_q, kr_small, (0.18, 0.18, 0.18, 1.0),
                         filled=False)
            fs_sh = max(1, int(8 * scale))
            lbl_sh = "shelf"
            tw_sh  = _text_width(lbl_sh, fs_sh)
            _draw_text(lbl_sh, col_cx - tw_sh * 0.5,
                       row_q - fs_sh * 0.5, fs_sh, (0.22, 0.22, 0.22, 1.0))

        # Column divider
        if bi < N_BANDS - 1:
            div_xd = disp_x + (bi + 1) * col_w
            _draw_rect(div_xd - max(0.5, scale * 0.5),
                       knob_y, max(0.5, scale * 0.5), knob_h,
                       (0.16, 0.16, 0.16, 1.0))

