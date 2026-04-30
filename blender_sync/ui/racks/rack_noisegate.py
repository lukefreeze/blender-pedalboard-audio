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

    try:
        import bpy as _bpy, numpy as _np
        from Loader import _fft_timeline_full, _fft_timeline
        assigned = get_rack_channels(rack)
        if not assigned:
            raise ValueError("no ch")
        ch = list(assigned)[0]
        tl = _fft_timeline_full.get(ch) or _fft_timeline.get(ch)
        if tl is None or not len(tl["snapshots"]):
            raise ValueError("no data")

        # Always prefer the full-track timeline — it has consistent start_frame=1
        # and covers the whole track, so cur_snap is always comparable.
        tl_full = _fft_timeline_full.get(ch)
        if tl_full and len(tl_full["snapshots"]):
            tl = tl_full

        n_tl     = len(tl["snapshots"])
        scene    = _bpy.context.scene
        cur_f    = scene.frame_current if scene else 0
        snap_sec = tl["snap_frames"] / float(tl["sr"])
        elapsed  = max(0.0, (cur_f - tl["start_frame"]) / float(tl["fps"]))
        cur_snap = max(0, min(n_tl - 1, int(elapsed / snap_sec)))

        # Show a fixed N_WIN window of snapshots.
        # Window slides so cur_snap is always visible:
        #   - If track fits in N_WIN, show all of it (0..n_tl-1) + silence pad.
        #   - Otherwise, centre the window on cur_snap.
        N_WIN    = 80
        if n_tl <= N_WIN:
            # Short track — show everything, silence-pad the right
            ws       = 0
            n_real   = n_tl
            n_pad_r  = N_WIN - n_real
            raw_vals = ([float(_np.mean(tl["snapshots"][i])) for i in range(n_real)]
                        + [0.0] * n_pad_r)
        else:
            # Long track — slide window to keep cur_snap near right edge (2/3 in)
            ws = max(0, cur_snap - (N_WIN * 2 // 3))
            we = ws + N_WIN
            if we > n_tl:
                we = n_tl
                ws = max(0, we - N_WIN)
            raw_vals = [float(_np.mean(tl["snapshots"][ws + i]))
                        for i in range(we - ws)]
            if len(raw_vals) < N_WIN:
                raw_vals = raw_vals + [0.0] * (N_WIN - len(raw_vals))
        rms_vals  = raw_vals  # always N_WIN entries
        # cur_snap position within the displayed window (for playhead line)
        playhead_slot = max(0, min(N_WIN - 1, cur_snap - ws))

        # Current gate state from RMS vs threshold
        rms_cur = rms_vals[-1] if rms_vals else 0.0
        gate_open_now = rms_cur >= thr_lin

        # Pixel widths for attack / hold / release — based on fixed N_WIN spacing
        ms_per_snap = snap_sec * 1000.0
        px_per_snap = disp_w / max(N_WIN - 1, 1)
        atk_px  = max(2.0, (atk_ms  / ms_per_snap) * px_per_snap)
        hold_px = max(2.0, (hold_ms / ms_per_snap) * px_per_snap)
        rel_px  = max(2.0, (rel_ms  / ms_per_snap) * px_per_snap)

    except Exception:
        rms_vals = None
        atk_px  = max(2.0, disp_w * 0.025)
        hold_px = max(2.0, disp_w * 0.15)
        rel_px  = max(2.0, disp_w * 0.055)

    # Draw waveform if we have data
    if rms_vals:
        max_rms = max(rms_vals + [0.01])
        half_h  = disp_h * 0.34
        wf_top  = []
        wf_bot  = []
        _N = 79  # N_WIN - 1, constant so x-spacing never changes
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
    # Built from knob values: find where waveform crosses threshold → draw
    # attack slope up, hold flat, release slope down, then closed again.
    # Range floor: where the line sits when closed
    rng_y = closed_y - rng_norm * (closed_y - open_y)

    gate_pts = []
    first_event = None   # (close_x, atk_x, hold_x, rel_x) for annotations

    if rms_vals:
        count    = len(rms_vals)
        px_per_i = disp_w / 79  # always N_WIN-1 = 79 so spacing is constant
        in_gate = False
        hold_remaining = 0.0

        i = 0
        while i < count:
            bx  = disp_x + i * px_per_i
            rms = rms_vals[i]
            is_above = rms >= thr_lin

            if not in_gate and is_above:
                # Transition: closed → open (attack)
                close_x = bx
                atk_end = min(bx + atk_px, disp_x + disp_w)
                if gate_pts and gate_pts[-1][1] != rng_y:
                    gate_pts.append((bx, rng_y))
                else:
                    gate_pts.append((bx, rng_y))
                gate_pts.append((atk_end, open_y))
                in_gate = True
                hold_remaining = hold_px
                if first_event is None:
                    first_event = (close_x, atk_end, None, None)
            elif in_gate and is_above:
                # Still open — flat at open_y, consume hold
                gate_pts.append((bx, open_y))
                hold_remaining -= px_per_i
                if first_event and first_event[2] is None:
                    first_event = (first_event[0], first_event[1], bx, None)
            elif in_gate and not is_above:
                # Below threshold — hold then release
                if hold_remaining > 0:
                    gate_pts.append((bx, open_y))
                    hold_remaining -= px_per_i
                    if first_event and first_event[2] is None:
                        first_event = (first_event[0], first_event[1], bx, None)
                else:
                    # Release slope
                    rel_end = min(bx + rel_px, disp_x + disp_w)
                    gate_pts.append((bx, open_y))
                    gate_pts.append((rel_end, rng_y))
                    in_gate = False
                    if first_event and first_event[3] is None:
                        if first_event[2] is None:
                            first_event = (first_event[0], first_event[1], bx, rel_end)
                        else:
                            first_event = (first_event[0], first_event[1], first_event[2], rel_end)
            else:
                # Closed — flat at range floor
                gate_pts.append((bx, rng_y))
            i += 1
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



