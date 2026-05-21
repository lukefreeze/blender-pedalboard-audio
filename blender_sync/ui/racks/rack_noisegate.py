# =============================================================================
# rack_noisegate.py
# Noise gate rack UI
# ┌─ LAYOUT CONSTANTS ─────────────────────────────────────────────────────┐
# │ RACK_EXPANDED_H_NG — change height here
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

# ---------------------------------------------------------------------------
# Module-level rolling RMS buffer — one per channel, updated every draw call.
# Gives a scrolling waveform without needing _fft_timeline_full (dead code).
# 80 samples = same window width as the old timeline approach.
# ---------------------------------------------------------------------------
import collections as _coll
# Ring buffer per channel — stores (frame, rms) pairs from live engine meter.
# Updated every draw call. Holds N_WIN samples = ~5s of history at 24fps.
_NG_RMS_HISTORY  = {}   # ch_idx -> deque of (frame, rms_value)
_NG_GATE_HISTORY = {}   # ch_idx -> dict {frame: bool} gate open state
_NG_HISTORY_LEN  = 120  # frames of history — matches N_WIN display window


def _draw_noisegate_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """Noise gate display.

    Draws exactly the mockup design:
    - Grey waveform from _fft_timeline_full (real audio, mirrored top+bottom)
    - Green gate envelope line drawn from knob values (attack slope, hold flat,
      release slope) — triggered wherever waveform crosses threshold
    - Amber dashed threshold line
    - Colour-coded atk/hold/rel bracket annotations over first event
    - Gate open/closed readout top-right
    - Knob strip with colour-coded arcs matching bracket colours
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
    import math as _mg
    ui     = scale
    rail_h = RACK_RAIL_H * ui
    body_h = rh - rail_h

    # Display area (left of channel buttons)
    disp_x = rx + 42 * ui
    disp_w = rw - 42 * ui - 108 * ui - 8 * ui
    disp_h = body_h * 0.56 - 4 * ui
    disp_y = ry + body_h - disp_h - 2 * ui

    # Knob area (above display)
    knob_h = body_h * 0.42 - 4 * ui
    knob_y = ry + 2 * ui

    # Gate state Y positions
    open_y   = disp_y + disp_h * 0.92   # gate open = high signal = near bottom of display
    closed_y = disp_y + disp_h * 0.10   # gate closed = muted = near top of display
    centre_y = disp_y + disp_h * 0.50   # waveform centre

    # Knob values
    thr_norm  = getattr(rack, 'p0', 0.50)
    atk_norm  = getattr(rack, 'p1', 0.05)
    hold_norm = getattr(rack, 'p2', 0.16)
    rel_norm  = getattr(rack, 'p3', 0.14)
    rng_norm  = getattr(rack, 'p4', 0.00)
    thr_db    = -60.0 + thr_norm * 60.0
    atk_ms    = 0.1   + atk_norm  * 99.9
    hold_ms   = hold_norm * 500.0
    rel_ms    = 10.0  + rel_norm  * 990.0
    rng_db    = -90.0 + rng_norm  * 90.0

    # Threshold in linear amplitude (for RMS comparison)
    thr_lin = 10.0 ** (thr_db / 20.0)

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    # ── Display background ────────────────────────────────────────────────────
    _draw_rect(disp_x, disp_y, disp_w, disp_h, (0.035, 0.042, 0.050, 1.0))

    # ── Grey waveform from real audio ─────────────────────────────────────────
    rms_vals = None
    gate_events = []   # list of (close_x, attack_x, hold_x, release_x) in pixels
    gate_open_now = True
    gr_db_cur = 0.0

    # Pull live gate state from engine and store per-frame history
    # gr_levels[ch][1]=GR dB, [ch][2]=1.0 open / 0.0 closed
    try:
        import bpy as _bpy_ng
        from Loader import get_engine as _get_eng_ng
        _eng_ng = _get_eng_ng()
        if _eng_ng:
            _hj_ng = _eng_ng.get_engine()
            if _hj_ng:
                assigned_ng = get_rack_channels(rack)
                if assigned_ng:
                    _ch_ng  = list(assigned_ng)[0]
                    _st_ng  = _hj_ng.get_state()
                    _gr_ng  = _st_ng.get_gr_levels(_ch_ng)
                    if _gr_ng and len(_gr_ng) >= 3:
                        gr_db_cur     = float(_gr_ng[1])
                        gate_open_now = float(_gr_ng[2]) > 0.5
                        # Store current frame's gate state so the waveform
                        # display can draw a historically accurate envelope
                        _cur_f_ng = (_bpy_ng.context.scene.frame_current
                                     if _bpy_ng.context.scene else 0)
                        if _ch_ng not in _NG_GATE_HISTORY:
                            _NG_GATE_HISTORY[_ch_ng] = {}
                        _NG_GATE_HISTORY[_ch_ng][_cur_f_ng] = gate_open_now
                        # Trim old frames to avoid unbounded growth
                        if len(_NG_GATE_HISTORY[_ch_ng]) > 500:
                            oldest = min(_NG_GATE_HISTORY[_ch_ng])
                            del _NG_GATE_HISTORY[_ch_ng][oldest]
    except Exception:
        pass

    try:
        import bpy as _bpy
        assigned = get_rack_channels(rack)
        if not assigned:
            raise ValueError("no ch")
        ch = list(assigned)[0]

        scene = _bpy.context.scene
        if not scene:
            raise ValueError("no scene")

        fps   = scene.render.fps / scene.render.fps_base
        cur_f = scene.frame_current

        try:
            from Loader import get_engine as _get_eng_ng2
            _eng2 = _get_eng_ng2()
            if _eng2:
                _hj2 = _eng2.get_engine()
                if _hj2:
                    live_rms = float(_hj2.get_meter_rms(ch))
                    if ch not in _NG_RMS_HISTORY:
                        _NG_RMS_HISTORY[ch] = _coll.deque(maxlen=_NG_HISTORY_LEN)
                    _NG_RMS_HISTORY[ch].append((cur_f, live_rms))
        except Exception:
            pass

        # Build frame→rms lookup from ring buffer
        N_WIN  = 120
        half   = N_WIN // 2
        f_start = cur_f - half
        f_end   = f_start + N_WIN

        rms_by_frame = {}
        buf = _NG_RMS_HISTORY.get(ch)
        if buf:
            for frame_i, rms_i in buf:
                rms_by_frame[frame_i] = rms_i

        rms_vals      = [rms_by_frame.get(f_start + i, 0.0) for i in range(N_WIN)]
        playhead_slot = half

        # Threshold in same linear scale as RMS (0..1)
        thr_lin_disp = 10.0 ** (thr_db / 20.0)
        thr_lin      = thr_lin_disp

        rms_cur = rms_by_frame.get(cur_f, 0.0)
        if gr_db_cur == 0.0:
            gate_open_now = rms_cur >= thr_lin_disp

        # px widths based on fps
        ms_per_frame = 1000.0 / fps
        px_per_frame = disp_w / max(N_WIN - 1, 1)
        atk_px  = max(2.0, (atk_ms  / ms_per_frame) * px_per_frame)
        hold_px = max(2.0, (hold_ms / ms_per_frame) * px_per_frame)
        rel_px  = max(2.0, (rel_ms  / ms_per_frame) * px_per_frame)

    except Exception:
        rms_vals     = None
        thr_lin_disp = thr_lin
        atk_px  = max(2.0, disp_w * 0.025)
        hold_px = max(2.0, disp_w * 0.15)
        rel_px  = max(2.0, disp_w * 0.055)

    # Draw waveform if we have data
    if rms_vals:
        # Normalise to peak of visible window; floor at thr_lin so the
        # threshold line always has context even on quiet signals
        # Normalise to the peak of the visible window only.
        # Do NOT include thr_lin — threshold has nothing to do with waveform scale.
        # The waveform should always fill the same height regardless of threshold.
        max_rms = max(max(rms_vals), 0.001)
        half_h  = disp_h * 0.34
        wf_top  = []
        wf_bot  = []
        _N = N_WIN - 1  # spacing constant matches N_WIN
        for i, rms in enumerate(rms_vals):
            bx  = disp_x + (i / _N) * disp_w
            amp = (rms / max_rms) * half_h
            wf_top.append((bx, centre_y - amp))
            wf_bot.append((bx, centre_y + amp))

        # Fill between top and bottom
        fill_verts = []
        for (bx, ty), (_, by) in zip(wf_top, wf_bot):
            fill_verts += [(bx, ty), (bx, by)]
        if len(fill_verts) >= 4:
            bf = batch_for_shader(shader, "TRI_STRIP", {"pos": fill_verts})
            shader.bind(); shader.uniform_float("color", (0.15, 0.16, 0.20, 0.85))
            bf.draw(shader)
        for pts in [wf_top, wf_bot]:
            if len(pts) >= 2:
                bl = batch_for_shader(shader, "LINE_STRIP", {"pos": pts})
                gpu.state.line_width_set(max(1.0, ui * 0.8))
                shader.bind(); shader.uniform_float("color", (0.24, 0.26, 0.32, 0.70))
                bl.draw(shader)
        gpu.state.line_width_set(1.0)

    # ── Playhead cursor line ─────────────────────────────────────────────────
    # White vertical line showing current frame position in the waveform window
    if rms_vals:
        try:
            _ph_x = disp_x + (playhead_slot / max(N_WIN - 1, 1)) * disp_w
            _ph_verts = [(_ph_x, disp_y + 2*ui), (_ph_x, disp_y + disp_h - 2*ui)]
            _ph_batch = batch_for_shader(shader, "LINES", {"pos": _ph_verts})
            gpu.state.line_width_set(max(1.5, ui))
            shader.bind()
            shader.uniform_float("color", (0.85, 0.85, 0.90, 0.60))
            _ph_batch.draw(shader)
            gpu.state.line_width_set(1.0)
        except Exception:
            pass

    # ── Gate envelope line ────────────────────────────────────────────────────
    # Use recorded engine gate state per frame (from _NG_GATE_HISTORY) so the
    # envelope matches what the engine actually did — not a prediction from
    # comparing waveform to threshold (which has scale/calibration errors).
    rng_y = closed_y - rng_norm * (closed_y - open_y)

    gate_pts = []
    first_event = None

    if rms_vals:
        count    = len(rms_vals)
        px_per_i = disp_w / max(count - 1, 1)
        # f_start was set in the try block above; fall back if exception occurred
        try:
            _f0 = f_start
            _ch = ch
            gate_hist = _NG_GATE_HISTORY.get(_ch, {})
        except Exception:
            _f0 = 0; gate_hist = {}

        in_gate = False
        hold_remaining = 0.0

        for i in range(count):
            bx       = disp_x + i * px_per_i
            frame_no = _f0 + i
            # Use recorded engine state if available, else fall back to waveform
            if frame_no in gate_hist:
                is_above = gate_hist[frame_no]
            else:
                rms      = rms_vals[i]
                is_above = rms >= thr_lin

            if not in_gate and is_above:
                close_x = bx
                atk_end = min(bx + atk_px, disp_x + disp_w)
                gate_pts.append((bx, rng_y))
                gate_pts.append((atk_end, open_y))
                in_gate = True
                hold_remaining = hold_px
                if first_event is None:
                    first_event = (close_x, atk_end, None, None)
            elif in_gate and is_above:
                gate_pts.append((bx, open_y))
                hold_remaining -= px_per_i
                if first_event and first_event[2] is None:
                    first_event = (first_event[0], first_event[1], bx, None)
            elif in_gate and not is_above:
                if hold_remaining > 0:
                    gate_pts.append((bx, open_y))
                    hold_remaining -= px_per_i
                    if first_event and first_event[2] is None:
                        first_event = (first_event[0], first_event[1], bx, None)
                else:
                    rel_end = min(bx + rel_px, disp_x + disp_w)
                    gate_pts.append((bx, open_y))
                    gate_pts.append((rel_end, rng_y))
                    in_gate = False
                    if first_event and first_event[3] is None:
                        hx2 = first_event[2] if first_event[2] else bx
                        first_event = (first_event[0], first_event[1], hx2, rel_end)
            else:
                gate_pts.append((bx, rng_y))
    else:
        # No data — draw flat at open
        gate_pts = [(disp_x, open_y), (disp_x + disp_w, open_y)]

    # Fill under gate line
    if len(gate_pts) >= 2:
        fv2 = []
        for gx, gy in gate_pts:
            fv2 += [(gx, disp_y + disp_h - 2), (gx, gy)]
        if len(fv2) >= 4:
            bf2 = batch_for_shader(shader, "TRI_STRIP", {"pos": fv2})
            shader.bind(); shader.uniform_float("color", (0.04, 0.18, 0.07, 0.45))
            bf2.draw(shader)
        bg = batch_for_shader(shader, "LINE_STRIP", {"pos": gate_pts})
        gpu.state.line_width_set(max(2.0, ui * 1.5))
        shader.bind(); shader.uniform_float("color", (0.20, 0.88, 0.38, 1.0))
        bg.draw(shader); gpu.state.line_width_set(1.0)

    # ── Amber dashed threshold line ───────────────────────────────────────────
    thr_y = rng_y - thr_norm * (rng_y - open_y)
    dash = 7*ui; gap = 4*ui; x = disp_x; tog = True; segs = []
    while x < disp_x + disp_w:
        xe = min(x + (dash if tog else gap), disp_x + disp_w)
        if tog: segs += [(x, thr_y), (xe, thr_y)]
        x = xe; tog = not tog
    if segs:
        bd = batch_for_shader(shader, "LINES", {"pos": segs})
        gpu.state.line_width_set(max(1.8, ui * 1.2))
        shader.bind(); shader.uniform_float("color", (0.96, 0.62, 0.08, 1.0))
        bd.draw(shader); gpu.state.line_width_set(1.0)
    _draw_text(f"thr {thr_db:.0f}dB", disp_x + 4*ui, thr_y - 2*ui - 9*ui,
               max(1, int(8*ui)), (0.96, 0.62, 0.08, 1.0))

    # ── open/closed labels ────────────────────────────────────────────────────
    fs7 = max(1, int(7*ui))
    _draw_text("open",   disp_x + 4*ui, open_y + 2*ui,       fs7, (0.28, 0.55, 0.28, 0.70))
    _draw_text("closed", disp_x + 4*ui, closed_y - fs7 - 2*ui, fs7, (0.55, 0.28, 0.28, 0.70))

    # ── Bracket annotations (atk/hold/rel) on first gate event ───────────────
    ann_y = disp_y + 8*ui
    fs8   = max(1, int(8*ui))
    if first_event:
        cx, ax, hx, rx = first_event
        hx = hx or ax
        rx = rx or min(hx + rel_px, disp_x + disp_w)
        # attack bracket (blue)
        if ax > cx + 2:
            _draw_rect(cx, ann_y - 1, ax - cx, 1, (0.42, 0.55, 1.0, 0.9))
            _draw_rect(cx, ann_y - 3, 1, 5, (0.42, 0.55, 1.0, 0.9))
            _draw_rect(ax, ann_y - 3, 1, 5, (0.42, 0.55, 1.0, 0.9))
            _draw_text("atk", cx + 2, ann_y - fs8 - 3, fs8, (0.42, 0.55, 1.0, 1.0))
        # hold bracket (purple)
        if hx > ax + 2:
            _draw_rect(ax, ann_y - 1, hx - ax, 1, (0.65, 0.55, 0.98, 0.9))
            _draw_rect(ax, ann_y - 3, 1, 5, (0.65, 0.55, 0.98, 0.9))
            _draw_rect(hx, ann_y - 3, 1, 5, (0.65, 0.55, 0.98, 0.9))
            mid = ax + (hx - ax) / 2
            _draw_text("hold", mid - 12*ui, ann_y - fs8 - 3, fs8, (0.65, 0.55, 0.98, 1.0))
        # release bracket (orange)
        if rx > hx + 2:
            _draw_rect(hx, ann_y - 1, rx - hx, 1, (0.98, 0.45, 0.08, 0.9))
            _draw_rect(hx, ann_y - 3, 1, 5, (0.98, 0.45, 0.08, 0.9))
            _draw_rect(rx, ann_y - 3, 1, 5, (0.98, 0.45, 0.08, 0.9))
            _draw_text("rel", hx + 2, ann_y - fs8 - 3, fs8, (0.98, 0.45, 0.08, 1.0))

    # ── Gate open/closed readout ──────────────────────────────────────────────
    try:
        col = (0.20, 0.88, 0.32, 1.0) if gate_open_now else (0.88, 0.18, 0.18, 1.0)
        lbl = "gate open" if gate_open_now else "gate closed"
        fs9 = max(1, int(9*ui)); fs8b = max(1, int(8*ui)); pad = 4*ui
        tw  = _text_width(lbl, fs9)
        bw  = tw + pad*2; bh = fs9 + fs8b + pad*2 + 2*ui
        bx2 = disp_x + disp_w - bw - 4*ui
        by2 = disp_y + 4*ui
        bg_col = (0.05, 0.18, 0.07, 1.0) if gate_open_now else (0.18, 0.05, 0.05, 1.0)
        _draw_rect(bx2, by2, bw, bh, bg_col)
        _draw_text(lbl, bx2 + pad, by2 + bh - fs9 - pad, fs9, col)
        _draw_text(f"GR  {gr_db_cur:.1f}dB", bx2 + pad, by2 + pad, fs8b, (0.55, 0.65, 0.55, 1.0))
    except Exception:
        pass

    # ── Knob strip ────────────────────────────────────────────────────────────
    N  = 5
    cw = disp_w / N
    rk = knob_y + knob_h * 0.68
    kr = min(max(13*ui, cw * 0.16), 20*ui)
    kr = min(kr, knob_h * 0.42 * 0.42)

    knob_defs = [
        ("Threshold", thr_norm, f"{thr_db:.0f}dB",  (0.88, 0.30, 0.30)),
        ("Attack",    atk_norm, f"{atk_ms:.0f}ms",  (0.42, 0.55, 1.00)),
        ("Hold",      hold_norm,f"{hold_ms:.0f}ms", (0.65, 0.55, 0.98)),
        ("Release",   rel_norm, f"{rel_ms:.0f}ms",  (0.98, 0.45, 0.08)),
        ("Range",     rng_norm, f"{rng_db:.0f}dB",  (0.50, 0.50, 0.55)),
    ]
    for ki, (label, val, vstr, col_k) in enumerate(knob_defs):
        cx = disp_x + (ki + 0.5) * cw
        _draw_knob(cx, rk, kr, val, col_k, label, vstr, ui)



