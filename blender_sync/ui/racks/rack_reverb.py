# =============================================================================
# rack_reverb.py
# Reverb rack UI
# ┌─ LAYOUT CONSTANTS ─────────────────────────────────────────────────────┐
# │ RACK_EXPANDED_H_RV — change height here
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


def _draw_reverb_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """Draw the reverb rack body — Option C style.

    Display area (upper 60% of body):
      Left zone  — dry waveform from _fft_timeline (same as EQ pre-EQ layer)
      Divider    — dashed vertical line at the pre-delay position
      Right zone — computed reverb tail silhouette, exponentially decaying,
                   shape driven entirely by room_size and damping knobs

    Knob strip (lower 40%):
      Room | Damp | Wet | Pre-dly | Width
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
    import math as _mr
    ui_scale    = scale
    rail_h      = RACK_RAIL_H * ui_scale
    body_h      = rh - rail_h

    # --- Display geometry: display at TOP of body, knobs at BOTTOM ---
    margin_l    = 42 * ui_scale
    ch_btn_w    = 108 * ui_scale
    margin_r    = ch_btn_w + 8 * ui_scale
    disp_x      = rx + margin_l
    disp_w      = rw - margin_l - margin_r
    disp_prop   = 0.56          # display takes 56% of body height
    disp_h      = body_h * disp_prop - 4 * ui_scale
    disp_y      = ry + body_h - disp_h - 2 * ui_scale  # top of body

    # Knob strip sits at the bottom of the body
    knob_h      = body_h * 0.42 - 4 * ui_scale
    knob_y      = ry + 2 * ui_scale                     # bottom of body

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    # Display background
    _draw_rect(disp_x, disp_y, disp_w, disp_h, (0.07, 0.07, 0.09, 1.0))

    # Grid lines
    for db_frac in [0.25, 0.5, 0.75]:
        ly = disp_y + db_frac * disp_h
        gl = batch_for_shader(shader, "LINES",
                               {"pos": [(disp_x, ly), (disp_x + disp_w, ly)]})
        shader.bind()
        shader.uniform_float("color", (0.18, 0.18, 0.20, 1.0))
        gl.draw(shader)

    # --- Read knob params ---
    room_sz  = getattr(rack, 'p0', 0.5)
    damping  = getattr(rack, 'p1', 0.5)
    wet      = getattr(rack, 'p2', 0.3)
    pre_d    = getattr(rack, 'p3', 0.0)
    width    = getattr(rack, 'p4', 1.0)

    # Pre-delay position as fraction of display width (0–20% of display)
    pre_frac = pre_d * 0.20
    div_x    = disp_x + pre_frac * disp_w

    # --- LEFT ZONE: dry waveform from envelope cache ---
    # Uses _envelope_cache (pre-built at play start) keyed by filepath.
    # Shows a scrolling N_WIN-frame window of peak amplitude centred on
    # the playhead — identical pattern to the noise gate waveform.
    if rack.enabled:
        try:
            import bpy as _bpy_rv
            from core.meters import _envelope_cache, get_envelope as _get_env_rv
            assigned_rv = get_rack_channels(rack)
            if assigned_rv:
                ch_rv   = list(assigned_rv)[0]
                scene_rv = _bpy_rv.context.scene
                if scene_rv and scene_rv.sequence_editor:
                    fps_rv  = scene_rv.render.fps / scene_rv.render.fps_base
                    cur_f   = scene_rv.frame_current
                    N_WIN   = 80
                    half    = N_WIN // 2
                    f_start = cur_f - half
                    f_end   = f_start + N_WIN

                    strips_rv = [
                        s for s in scene_rv.sequence_editor.sequences_all
                        if s.type == "SOUND" and s.sound
                        and (s.channel - 1) == ch_rv
                    ]

                    peak_by_frame = {}
                    for strip in strips_rv:
                        fp = _bpy_rv.path.abspath(strip.sound.filepath)
                        if fp not in _envelope_cache:
                            _get_env_rv(fp, fps_rv)
                        env = _envelope_cache.get(fp)
                        if env is None or len(env) < 2:
                            continue
                        peak_list = env[1]
                        fs = int(strip.frame_start)
                        fo = int(getattr(strip, "frame_offset_start", 0))
                        for fi in range(f_start, f_end):
                            file_f = fi - fs + fo
                            if 0 <= file_f < len(peak_list):
                                peak_by_frame[fi] = float(peak_list[file_f])

                    rms_vals_rv = [peak_by_frame.get(f_start + i, 0.0)
                                   for i in range(N_WIN)]
                    max_rv = max(max(rms_vals_rv), 0.001)

                    # Draw waveform clipped to left zone (up to pre-delay divider)
                    left_w   = div_x - disp_x
                    half_h_rv = disp_h * 0.34
                    centre_rv = disp_y + disp_h * 0.5
                    wf_top_rv = []; wf_bot_rv = []
                    for i, amp in enumerate(rms_vals_rv):
                        bx  = disp_x + (i / max(N_WIN - 1, 1)) * left_w
                        h   = (amp / max_rv) * half_h_rv
                        wf_top_rv.append((bx, centre_rv - h))
                        wf_bot_rv.append((bx, centre_rv + h))

                    fill_rv = []
                    for (bx, ty), (_, by) in zip(wf_top_rv, wf_bot_rv):
                        fill_rv += [(bx, ty), (bx, by)]
                    if len(fill_rv) >= 4:
                        bf = batch_for_shader(shader, "TRI_STRIP", {"pos": fill_rv})
                        shader.bind()
                        shader.uniform_float("color", (0.17, 0.17, 0.22, 0.82))
                        bf.draw(shader)
                    for pts_rv in [wf_top_rv, wf_bot_rv]:
                        if len(pts_rv) >= 2:
                            be = batch_for_shader(shader, "LINE_STRIP", {"pos": pts_rv})
                            gpu.state.line_width_set(max(1.0, ui_scale * 0.7))
                            shader.bind()
                            shader.uniform_float("color", (0.32, 0.32, 0.40, 0.60))
                            be.draw(shader)
                            gpu.state.line_width_set(1.0)

                    # Playhead cursor in left zone
                    ph_x = disp_x + (half / max(N_WIN - 1, 1)) * left_w
                    ph_b = batch_for_shader(shader, "LINES",
                                            {"pos": [(ph_x, disp_y + 2*ui_scale),
                                                     (ph_x, disp_y + disp_h - 2*ui_scale)]})
                    gpu.state.line_width_set(max(1.5, ui_scale))
                    shader.bind()
                    shader.uniform_float("color", (0.85, 0.85, 0.90, 0.50))
                    ph_b.draw(shader)
                    gpu.state.line_width_set(1.0)

        except Exception:
            pass  # waveform is decorative — never crash

    # --- Pre-delay divider ---
    if pre_frac > 0.005:
        div_verts = [(div_x, disp_y), (div_x, disp_y + disp_h)]
        div_batch = batch_for_shader(shader, "LINES", {"pos": div_verts})
        shader.bind()
        shader.uniform_float("color", (0.55, 0.55, 0.60, 0.50))
        div_batch.draw(shader)
        # Label
        fs_pd = max(1, int(8*ui_scale))
        _draw_text(f"{int(pre_d*100)}ms", div_x + 2*ui_scale,
                   disp_y + disp_h - fs_pd - 2*ui_scale, fs_pd, (0.55, 0.55, 0.60, 0.80))

    # --- RIGHT ZONE: reverb tail silhouette ---
    # Exponential decay: y(t) = exp(-t * decay_rate)
    # decay_rate is derived from room_size and damping
    # RT60 (60dB decay time) = -60 / (20*log10(e) * decay_rate)
    # We map room_size → feedback (0.28-0.98), damping → HF rolloff
    feedback     = 0.28 + room_sz * 0.70
    # Approximate RT60 in display-space: larger room = longer tail
    if feedback < 0.9999:
        rt60_frac = -0.05 / _mr.log10(max(feedback, 1e-9))  # in display width units
    else:
        rt60_frac = 2.0
    rt60_frac = min(rt60_frac, 2.0)

    # HF curve decays faster by damping factor
    hf_rt60_frac = rt60_frac * (1.0 - damping * 0.7)

    tail_start_x = div_x
    tail_w       = disp_x + disp_w - tail_start_x
    N_TAIL       = 128
    centre_y     = disp_y + disp_h * 0.5
    peak_h       = disp_h * 0.45 * wet  # taller tail = more wet

    # Full-band tail (grey)
    tail_verts = []
    for i in range(N_TAIL + 1):
        t = i / N_TAIL
        x = tail_start_x + t * tail_w
        if rt60_frac > 0:
            amp = _mr.exp(-t * 3.0 / max(rt60_frac, 0.01))
        else:
            amp = 0.0
        h = amp * peak_h
        tail_verts.append((x, centre_y))
        tail_verts.append((x, centre_y + h))

    if len(tail_verts) >= 4:
        bt = batch_for_shader(shader, "TRI_STRIP", {"pos": tail_verts})
        shader.bind()
        shader.uniform_float("color", (0.28, 0.32, 0.38, 0.65))
        bt.draw(shader)

    # Mirror lower half
    tail_lower = []
    for i in range(N_TAIL + 1):
        t = i / N_TAIL
        x = tail_start_x + t * tail_w
        amp = _mr.exp(-t * 3.0 / max(rt60_frac, 0.01)) if rt60_frac > 0 else 0.0
        h = amp * peak_h
        tail_lower.append((x, centre_y))
        tail_lower.append((x, centre_y - h))

    if len(tail_lower) >= 4:
        bl = batch_for_shader(shader, "TRI_STRIP", {"pos": tail_lower})
        shader.bind()
        shader.uniform_float("color", (0.28, 0.32, 0.38, 0.65))
        bl.draw(shader)

    # HF tail overlay (lighter, decays faster — shows damping effect)
    hf_verts_top = []; hf_verts_bot = []
    for i in range(N_TAIL + 1):
        t = i / N_TAIL
        x = tail_start_x + t * tail_w
        amp = _mr.exp(-t * 3.0 / max(hf_rt60_frac, 0.01)) if hf_rt60_frac > 0 else 0.0
        h = amp * peak_h * 0.65
        hf_verts_top.append((x, centre_y + h))
        hf_verts_bot.append((x, centre_y - h))

    for hf_verts in [hf_verts_top, hf_verts_bot]:
        if len(hf_verts) >= 2:
            bh = batch_for_shader(shader, "LINE_STRIP", {"pos": hf_verts})
            gpu.state.line_width_set(max(1.0, ui_scale * 0.7))
            shader.bind()
            shader.uniform_float("color", (0.50, 0.60, 0.72, 0.70))
            bh.draw(shader)
            gpu.state.line_width_set(1.0)

    # RT60 label
    if rt60_frac > 0:
        rt60_ms = rt60_frac * 1000
        rt60_str = f"{rt60_ms:.0f}ms" if rt60_ms < 1000 else f"{rt60_ms/1000:.1f}s"
        fs_rt = max(1, int(9*ui_scale))
        _draw_text(f"RT60 {rt60_str}", disp_x + disp_w - 60*ui_scale,
                   disp_y + 6*ui_scale, fs_rt, (0.50, 0.60, 0.72, 0.85))

    # Labels
    fs_lbl = max(1, int(8*ui_scale))
    _draw_text("dry", disp_x + 3*ui_scale, disp_y + 5*ui_scale,
               fs_lbl, (0.45, 0.45, 0.48, 0.80))
    _draw_text("tail", tail_start_x + 4*ui_scale, disp_y + 5*ui_scale,
               fs_lbl, (0.50, 0.60, 0.72, 0.80))

    # --- KNOB STRIP (5 knobs: Room, Damp, Wet, Pre-dly, Width) ---
    N_KNOBS  = 5
    col_w    = disp_w / N_KNOBS
    row_slot = knob_h / 3.0
    row_knob = knob_y + knob_h - row_slot * 1.3
    kr       = min(max(13*ui_scale, col_w*0.16), 20*ui_scale)
    kr       = min(kr, row_slot * 0.42)

    RV_KNOB_PARAMS = ["Room", "Damp", "Wet", "Pre-dly", "Width"]
    rv_vals = [room_sz, damping, wet, pre_d, width]
    rv_col  = (0.35, 0.65, 0.90)

    for ki in range(N_KNOBS):
        cx = disp_x + (ki + 0.5) * col_w
        val = rv_vals[ki]
        pct_str = f"{int(val*100)}%"
        _draw_knob(cx, row_knob, kr, val, rv_col,
                   RV_KNOB_PARAMS[ki], pct_str, ui_scale)



