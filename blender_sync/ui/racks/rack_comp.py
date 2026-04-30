# =============================================================================
# rack_comp.py
# Single-band and multiband compressor rack UI
# ┌─ LAYOUT CONSTANTS ─────────────────────────────────────────────────────┐
# │ RACK_EXPANDED_H, RACK_EXPANDED_H_MB — change height here
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

    # Horizontal grid lines (dB scale)
    for gi in range(1, 5):
        gy = spec_y + gi/5 * spec_h
        _draw_rect(spec_x, gy, spec_w, max(0.5,scale*0.5), (0.09,0.09,0.09,1.0))

    # dB scale labels on left
    fs_db = max(1, int(7*scale))
    for label, frac in [("+6",0.1),("0",0.3),("-6",0.5),("-12",0.7),("-24",0.9)]:
        ly = spec_y + frac * spec_h
        tw = _text_width(label, fs_db)
        _draw_text(label, spec_x - tw - 3*scale, ly - fs_db/2,
                   fs_db, (0.3,0.3,0.3,1.0))

    # Get FFT data — clear when rack is bypassed
    fft_data = None
    gr_data  = [0.0, 0.0, 0.0, 0.0]
    if not rack.enabled:
        pass
    else:
     try:
        from Loader import _fft_timeline
        import bpy as _bpy2
        assigned = get_rack_channels(rack)
        if assigned:
            ch = list(assigned)[0]
            tl = _fft_timeline.get(ch)
            if tl is not None and len(tl['snapshots']) > 0:
                scene2      = _bpy2.context.scene
                cur_frame   = scene2.frame_current if scene2 else 0
                start_frame = tl['start_frame']
                fps         = tl['fps']
                snap_sec    = tl['snap_frames'] / tl['sr']
                elapsed_sec = (cur_frame - start_frame) / fps
                snap_f      = elapsed_sec / snap_sec
                snap_idx    = int(snap_f)
                frac        = snap_f - snap_idx
                snaps       = tl['snapshots']
                snap_idx    = max(0, min(len(snaps)-1, snap_idx))
                # snaps is (n_snaps, 4, 8) numpy array — interpolate for smooth motion
                import numpy as _np2
                frame_data  = snaps[snap_idx]
                if frac > 0.0 and snap_idx + 1 < len(snaps):
                    next_data  = snaps[snap_idx + 1]
                    frame_data = frame_data * (1.0 - frac) + next_data * frac
                fft_data    = [frame_data[b].tolist() for b in range(4)]
     except Exception as _fe:
        import traceback as _tb
        _tb.print_exc()
        fft_data = None

    # Also get GR levels for the GR bar
    try:
        from Loader import get_engine
        engine = get_engine()
        assigned2 = get_rack_channels(rack)
        if engine and assigned2:
            ch2 = list(assigned2)[0]
            if 0 <= ch2 < 32:
                gr_data = engine.get_state().get_gr_levels(ch2)
    except Exception:
        pass

    # --- Background: grey FFT bars (full width, all bands combined) ---
    FFT_BINS = 32
    total_bars = FFT_BINS * 4
    bar_w_full = spec_w / total_bars
    for band in range(4):
        col = BAND_COLORS[band]
        if fft_data and fft_data[band]:
            base_bins = list(fft_data[band])
        else:
            base_bins = [0.04] * FFT_BINS

        # Use real FFT timeline data — no fake animation
        for bi, base_val in enumerate(base_bins):
            val     = max(0.0, min(1.0, base_val))
            bar_idx = band * FFT_BINS + bi
            bar_h   = val * spec_h * 0.85
            bx      = spec_x + bar_idx * bar_w_full
            r,g,b_c,a = col
            _draw_rect(bx, spec_y, max(bar_w_full-0.5, 0.5), bar_h,
                       (r*0.2+0.04, g*0.2+0.04, b_c*0.2+0.04, 0.9))

    # --- Band divider lines ---
    for b in range(1, 4):
        dx = spec_x + b * band_w
        _draw_rect(dx, spec_y, max(0.5,scale*0.5), spec_h, (0.2,0.2,0.2,1.0))
        # Crossover frequency labels
        cross_labels = ["120hz", "800hz", "5khz"]
        fs_cr = max(1, int(7*scale))
        tw_cr = _text_width(cross_labels[b-1], fs_cr)
        _draw_text(cross_labels[b-1], dx - tw_cr/2,
                   spec_y + spec_h + 2*scale,
                   fs_cr, (0.35,0.35,0.35,1.0))

    # --- Settings-driven frequency response curve ---
    # Shows the effect of current knob settings on the frequency spectrum.
    # Each band's gain setting + compression depth shapes the curve.
    # Updates instantly as knobs move — no animation, pure representation.
    #
    # Curve logic per band:
    #   - At 0dB gain with no threshold hit: flat at 0dB
    #   - Gain knob shifts band up/down
    #   - Threshold + ratio creates a soft-knee dip based on a nominal
    #     input level (we use -18dB RMS as the reference signal level)
    #     This shows how much the compressor would affect a typical signal
    #
    # Y axis: -12dB (bottom) to +12dB (top), 0dB = centre
    # X axis: full spectrum left to right across all 4 bands

    zero_db_y  = spec_y + spec_h * 0.5      # 0dB at vertical centre
    db_per_px  = 12.0 / (spec_h * 0.5)      # 12dB maps to half height
    scale_px   = (spec_h * 0.5) / 12.0      # pixels per dB

    # Draw 0dB reference line
    _draw_rect(spec_x, zero_db_y, spec_w, max(0.5, scale*0.5),
               (0.35, 0.35, 0.35, 0.6))

    # dB grid lines and labels
    fs_db = max(1, int(7*scale))
    for db_val, label in [(12,"+12"),(6,"+6"),(0,"0"),(-6,"-6"),(-12,"-12")]:
        gy = zero_db_y - db_val * scale_px
        if spec_y <= gy <= spec_y + spec_h:
            _draw_rect(spec_x, gy, spec_w, max(0.5,scale*0.3),
                       (0.12,0.12,0.12,1.0))
            tw = _text_width(label, fs_db)
            _draw_text(label, spec_x - tw - 3*scale, gy - fs_db*0.5,
                       fs_db, (0.3,0.3,0.3,1.0))

    # Compute per-band gain offset from knob settings
    # Reference input: -18dB RMS — represents typical programme level
    REF_INPUT_DB = -18.0

    def band_output_db(b):
        """Net dB change this band applies to the reference signal."""
        thr_db  = -40.0 + _rp(rack, b)    * 40.0   # threshold
        ratio   =  1.0  + _rp(rack, b+4)  * 19.0   # ratio
        knee_db =  0.5  + _rp(rack, b+20) * 23.5   # knee
        gain_db = (_rp(rack, b+16, 0.5) - 0.5) * 24.0  # band gain

        # Soft knee gain reduction at reference input
        half_k = knee_db * 0.5
        in_db  = REF_INPUT_DB
        if in_db <= thr_db - half_k:
            gr_db = 0.0
        elif in_db <= thr_db + half_k and knee_db > 0:
            x     = in_db - thr_db + half_k
            gr_db = (1.0/ratio - 1.0) * (x*x) / (2.0*knee_db)
        else:
            gr_db = (in_db - thr_db) * (1.0/ratio - 1.0)

        return gain_db + gr_db  # total net effect on signal

    # Calculate net dB per band
    band_db = [band_output_db(b) for b in range(4)]

    # Build smooth curve — cubic smooth-step between band centres
    # with flat regions within each band and smooth transitions at crossovers
    N_PTS = 200
    curve_pts = []
    for i in range(N_PTS + 1):
        fx     = i / N_PTS
        band_f = fx * 4.0
        band_i = min(3, int(band_f))
        band_t = band_f - band_i

        # Smooth blend at band boundaries
        db_this = band_db[band_i]
        db_next = band_db[min(3, band_i + 1)]
        # Sigmoid transition — flat in band centre, smooth at edges
        s       = band_t * band_t * (3.0 - 2.0 * band_t)
        db_here = db_this + (db_next - db_this) * s

        px = spec_x + fx * spec_w
        py = zero_db_y - db_here * scale_px
        py = max(spec_y + 2*scale, min(spec_y + spec_h - 2*scale, py))
        curve_pts.append((px, py))

    # Draw filled area between curve and 0dB line
    for i in range(len(curve_pts) - 1):
        px1, py1 = curve_pts[i]
        px2, py2 = curve_pts[i+1]
        band_here = min(3, int((px1 - spec_x) / band_w))
        col       = BAND_COLORS[band_here]
        r,g,b_c,a = col
        y_top  = min(py1, zero_db_y)
        y_bot  = max(py1, zero_db_y)
        fill_h = y_bot - y_top
        if fill_h > 0.5:
            _draw_rect(px1, y_top, max(px2-px1, 0.5), fill_h,
                       (r*0.35, g*0.35, b_c*0.35, 0.4))

    # Draw the curve line
    for i in range(len(curve_pts) - 1):
        px1, py1 = curve_pts[i]
        px2, py2 = curve_pts[i+1]
        band_here = min(3, int((px1 - spec_x) / band_w))
        col       = BAND_COLORS[band_here]
        r,g,b_c,a = col
        _draw_line(px1, py1, px2, py2,
                   (min(1,r*1.4), min(1,g*1.4), min(1,b_c*1.4), 1.0),
                   max(2.0, scale*2.0))

    # Band centre dots (like IK Quad Comp)
    for band in range(4):
        bx_c = spec_x + (band + 0.5) * band_w
        db_b = band_db[band]
        py_c = zero_db_y - db_b * scale_px
        py_c = max(spec_y + 4*scale, min(spec_y + spec_h - 4*scale, py_c))
        col  = BAND_COLORS[band]
        r,g,b_c,a = col
        _draw_circle(bx_c, py_c, 5*scale,
                     (min(1,r*1.5), min(1,g*1.5), min(1,b_c*1.5), 1.0))
        _draw_circle(bx_c, py_c, 5*scale, (0.1,0.1,0.1,0.6), filled=False)
        # Value label
        fs_lbl = max(1, int(7*scale))
        lbl    = f"{db_b:+.1f}dB"
        tw_lbl = _text_width(lbl, fs_lbl)
        _draw_text(lbl, bx_c - tw_lbl/2, py_c + 8*scale,
                   fs_lbl, (r, g, b_c, 0.9))

    # Band name labels
    for band in range(4):
        col   = BAND_COLORS[band]
        bx_c  = spec_x + (band + 0.5) * band_w
        fs_bn = max(1, int(8*scale))
        tw_bn = _text_width(BAND_NAMES[band], fs_bn)
        _draw_text(BAND_NAMES[band], bx_c - tw_bn/2,
                   spec_y + spec_h - 14*scale,
                   fs_bn, (col[0]*0.8, col[1]*0.8, col[2]*0.8, 0.8))

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

        # Read GR + signal from timelines — zero when rack is bypassed
        gr_db_band = 0.0
        sig_norm   = 0.0
        if rack.enabled:
            try:
                from Loader import _gr_timeline, _fft_timeline
                import bpy as _grbpy2
                scene_gr2    = _grbpy2.context.scene
                assigned_gr2 = get_rack_channels(rack)
                if assigned_gr2 and scene_gr2:
                    ch_gr2 = list(assigned_gr2)[0]
                    cur_f2 = scene_gr2.frame_current
                    tl_gr2 = _gr_timeline.get(ch_gr2)
                    if tl_gr2 is not None and len(tl_gr2['snapshots']) > 0:
                        snap_sec2 = tl_gr2['snap_frames'] / tl_gr2['sr']
                        elap_sec2 = (cur_f2 - tl_gr2['start_frame']) / tl_gr2['fps']
                        snap_idx2 = max(0, min(len(tl_gr2['snapshots'])-1,
                                              int(elap_sec2 / snap_sec2)))
                        gr_db_band = float(tl_gr2['snapshots'][snap_idx2][band])
                    tl_fft2 = _fft_timeline.get(ch_gr2)
                    if tl_fft2 is not None and len(tl_fft2['snapshots']) > 0:
                        snap_sec3 = tl_fft2['snap_frames'] / tl_fft2['sr']
                        elap_sec3 = (cur_f2 - tl_fft2['start_frame']) / tl_fft2['fps']
                        snap_idx3 = max(0, min(len(tl_fft2['snapshots'])-1,
                                              int(elap_sec3 / snap_sec3)))
                        sig_norm  = float(tl_fft2['snapshots'][snap_idx3][band].mean())
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
        thr_db  = -40.0 + thr_n*40.0
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



# ---------------------------------------------------------------------------
# EQ rack body
# ---------------------------------------------------------------------------


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


