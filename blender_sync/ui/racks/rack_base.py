# =============================================================================
# ui/racks/rack_base.py
# Shared rack chassis, rail, channel buttons, spectrum display, GR meters,
# and the expanded/collapsed shell renderers that dispatch to rack_*.py.
# ┌─ LAYOUT CONSTANTS ─────────────────────────────────────────────────────┐
# │ RACK_WIDTH, RACK_EXPANDED_H, RACK_RAIL_H, RACK_GAP, RACK_COLLAPSED_H  │
# │ Change any of these to resize the rack chassis globally.               │
# └────────────────────────────────────────────────────────────────────────┘
# =============================================================================
print("[RACK_BASE] loading rack_base.py")

import math
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



# Rack layout constants — defined here directly to avoid circular imports.
# (rack_base is imported BY Racks.py, so importing FROM Racks at module level
#  always fails. These values must be kept in sync with Racks.py manually.)
RACK_WIDTH          = 1200
RACK_EXPANDED_H     = 260
RACK_EXPANDED_H_MB  = 400
RACK_EXPANDED_H_EQ  = 580
RACK_EXPANDED_H_RV  = 340
RACK_EXPANDED_H_NG  = 320
RACK_EXPANDED_H_DL  = 320
RACK_EXPANDED_H_MX  = 320
RACK_COLLAPSED_H    = 48
RACK_RAIL_H         = 32
RACK_GAP            = 8

SPEC_H          = 200
SPEC_W          = 480
SPEC_X          = 430
SPEC_BANDS      = 48
KNOB_SECTION_W  = 360
KNOB_SPACING    = 68
KNOB_START_X    = 55
CH_BTN_SIZE     = 26
GR_BAR_W        = 12
GR_BAR_SPACING  = 16

# EFFECT_TYPES and PRESETS are fetched lazily from Racks inside each function
# that needs them — see _get_racks_state() helper below.

def _get_racks_state():
    """Lazily import live state from Racks.py. Safe to call inside any draw function."""
    import Racks as _r
    return {
        'EFFECT_TYPES':     _r.EFFECT_TYPES,
        'PRESETS':          _r.PRESETS,
        '_reorder_open':    _r._reorder_open,
        '_reorder_rack_idx':_r._reorder_rack_idx,
        '_led_states':      _r._led_states,
    }



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
    pass

def _draw_spectrum(rx, ry, rw, rh, rack_idx, scale):
    """Draw the spectrum analyser display."""
    # Lazy import — get_rack_channels not available at module level
    try:
        import Racks as _rs_spec
        get_rack_channels = _rs_spec.get_rack_channels
    except Exception:
        get_rack_channels = lambda r: set()
    # Background
    _draw_rect(rx, ry, rw, rh, (0.04, 0.04, 0.04, 1.0))
    # Border
    shader = _get_shader()
    verts = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind(); shader.uniform_float("color", (0.15,0.15,0.15,1.0))
    batch.draw(shader)

    # Grid lines horizontal (every 6dB)
    for i in range(1, 6):
        gy = ry + (i/6)*rh
        _draw_rect(rx, gy, rw, max(0.5, scale*0.5), (0.1,0.1,0.1,1.0))

    # Grid lines vertical
    for i in range(1, 8):
        gx = rx + (i/8)*rw
        _draw_rect(gx, ry, max(0.5, scale*0.5), rh, (0.1,0.1,0.1,1.0))

    # Spectrum bars — clear when rack is bypassed, no static fallback
    fft_flat = None
    _rack_on = True
    try:
        import bpy as _bpys0
        _sc0   = _bpys0.context.scene
        _rs0   = getattr(_sc0, "pb_racks", []) if _sc0 else []
        _rack_on = _rs0[rack_idx].enabled if rack_idx < len(_rs0) else True
    except Exception:
        pass

    if _rack_on:
        try:
            from Loader import get_engine as _get_eng_spec
            import bpy as _bpys
            import numpy as _np
            scene_s = _bpys.context.scene
            racks_s = getattr(scene_s, "pb_racks", [])
            if rack_idx < len(racks_s):
                assigned_s = get_rack_channels(racks_s[rack_idx])
                if assigned_s:
                    ch_s = list(assigned_s)[0]
                    _eng_spec = _get_eng_spec()
                    if _eng_spec:
                        _hj_spec = _eng_spec.get_engine()
                        if _hj_spec and 0 <= ch_s < 32:
                            _s_spec = _hj_spec.get_state()
                            all_bins = list(_s_spec.get_spec_bins(ch_s))
                            if any(v > 0 for v in all_bins):
                                fft_flat = _np.array(all_bins, dtype=float)
        except Exception:
            fft_flat = None

    bar_w = (rw - 4*scale) / SPEC_BANDS
    if fft_flat is not None and _rack_on:
        import math as _mth
        n_bins = len(fft_flat)

        # Log-spaced centre bin for each bar
        centres = [_mth.pow(n_bins, b / SPEC_BANDS) - 1.0
                   for b in range(SPEC_BANDS)]

        # Sigma = half the gap to adjacent centres, wider for low-freq bars
        # that share only a few real FFT bins between many display bars
        sigmas = []
        for b in range(SPEC_BANDS):
            if b == 0:
                gap = max(0.5, centres[1] - centres[0])
            elif b == SPEC_BANDS - 1:
                gap = max(0.5, centres[-1] - centres[-2])
            else:
                gap = max(0.5, (centres[b + 1] - centres[b - 1]) * 0.5)
            sigmas.append(max(1.5, gap * 1.5))

        for b in range(SPEC_BANDS):
            t      = b / SPEC_BANDS
            centre = centres[b]
            sigma  = sigmas[b]
            # Gaussian-weighted average — each bar blends across its neighbourhood
            lo = max(0, int(centre - 3.0 * sigma))
            hi = min(n_bins - 1, int(centre + 3.0 * sigma) + 1)
            total_w = 0.0
            total_v = 0.0
            for i in range(lo, hi + 1):
                w = _mth.exp(-0.5 * ((i - centre) / sigma) ** 2)
                total_w += w
                total_v += w * float(fft_flat[i])
            h_frac = (total_v / total_w) if total_w > 0 else float(fft_flat[max(0, min(n_bins - 1, int(centre)))])
            h_frac = max(0.04, min(0.95, h_frac))
            bar_h  = h_frac * rh
            bx     = rx + 2*scale + b * bar_w
            if t < 0.3:   col = (0.0, 0.7,  0.45, 0.75)
            elif t < 0.6: col = (0.0, 0.85, 0.55, 0.85)
            elif t < 0.8: col = (0.9, 0.65, 0.0,  0.7)
            else:         col = (0.7, 0.35, 0.0,  0.5)
            _draw_rect(bx, ry, max(bar_w - scale, 1.0), bar_h, col)

    # GR curve — classic soft knee transfer function (input→output diagonal).
    # X = input level -60..0 dB, Y = output level -60..0 dB.
    # 1:1 slope below threshold, compressed slope above, soft knee join.
    import math as _math
    scene = bpy.context.scene
    racks = getattr(scene, "pb_racks", [])
    if rack_idx < len(racks):
        rack = racks[rack_idx]
        if rack.effect_type == "COMP_SINGLE":
            thr_db  = -40.0 + rack.p0 * 40.0
            ratio   =  1.0  + rack.p1 * 19.0
            knee_db =  0.5  + rack.p5 * 23.5

            pts = []
            for s in range(129):
                t      = s / 128.0
                in_db  = -60.0 + t * 60.0
                half_k = knee_db * 0.5
                if in_db <= thr_db - half_k:
                    out_db = in_db
                elif in_db <= thr_db + half_k and knee_db > 0:
                    x      = in_db - thr_db + half_k
                    out_db = in_db + (1.0/ratio - 1.0)*(x*x)/(2.0*knee_db)
                else:
                    out_db = thr_db + (in_db - thr_db) / ratio
                out_norm = (out_db + 60.0) / 60.0
                pts.append((rx + t*rw, ry + out_norm*rh))

            # Thick red diagonal line — clearly visible over bars
            for i in range(len(pts)-1):
                _draw_line(pts[i][0], pts[i][1],
                           pts[i+1][0], pts[i+1][1],
                           (0.9, 0.2, 0.2, 0.9), max(2.5, scale*2.5))

            # Threshold vertical marker
            thr_norm = (thr_db + 60.0) / 60.0
            thr_x    = rx + thr_norm * rw
            _draw_line(thr_x, ry, thr_x, ry+rh,
                       (0.6, 0.2, 0.2, 0.4), max(1.0, scale*1.0))

    # Frequency labels
    freq_labels = [("20", 0.0), ("200", 0.22), ("1k", 0.44),
                   ("4k", 0.63), ("10k", 0.8), ("20k", 0.95)]
    fs = max(1, int(7*scale))
    for label, t in freq_labels:
        lx = rx + t*rw
        _draw_text(label, lx, ry - 12*scale, fs, (0.3,0.3,0.3,1.0))

    # dB scale on left
    db_labels = [("0", 1.0), ("-12", 0.66), ("-24", 0.33), ("-36", 0.0)]
    for label, t in db_labels:
        ly = ry + t*rh - 3*scale
        tw = _text_width(label, fs)
        _draw_text(label, rx - tw - 4*scale, ly, fs, (0.3,0.3,0.3,1.0))

    # Legend
    lfs = max(1, int(7*scale))
    _draw_rect(rx + 4*scale, ry + rh - 12*scale, 12*scale, 2*scale,
               (0.0, 0.8, 0.5, 0.8))
    _draw_text("signal", rx + 18*scale, ry + rh - 14*scale, lfs,
               (0.4, 0.4, 0.4, 1.0))
    _draw_rect(rx + 60*scale, ry + rh - 12*scale, 12*scale, 2*scale,
               (0.9, 0.2, 0.2, 0.7))
    _draw_text("GR curve", rx + 74*scale, ry + rh - 14*scale, lfs,
               (0.4, 0.4, 0.4, 1.0))


def _draw_gr_meters(rx, ry, rh, rack_idx, assigned_channels, scale):
    """Draw one slim GR meter per assigned channel.
    GR meter: 0dB at TOP, reduction fills downward from top.
    Like a VU meter — full bar = heavy compression, empty = no compression.
    """
    if not assigned_channels: return
    bar_w   = GR_BAR_W * scale
    spacing = GR_BAR_SPACING * scale
    fs      = max(1, int(7*scale))

    import bpy as _bpy_gr
    scene_gr = _bpy_gr.context.scene
    racks_gr = getattr(scene_gr, "pb_racks", []) if scene_gr else []
    rack_gr  = racks_gr[rack_idx] if rack_idx < len(racks_gr) else None
    is_enabled = rack_gr.enabled if rack_gr else True

    for i, ch_idx in enumerate(assigned_channels[:6]):
        bx = rx + i * spacing
        tw = _text_width(str(ch_idx+1), fs)
        _draw_text(str(ch_idx+1), bx + bar_w/2 - tw/2,
                   ry - 12*scale, fs, (0.4,0.4,0.4,1.0))
        _draw_rect(bx, ry, bar_w, rh, (0.04, 0.04, 0.04, 1.0))

        # Premier Pro style: green signal bar + red GR cap.
        # Signal from FFT timeline. GR computed from signal + rack params
        # (COMP_SINGLE has no separate GR timeline — compute it here).
        sig_norm  = 0.0
        gr_db_val = 0.0
        if is_enabled:
            try:
                from Loader import get_engine as _get_eng_sb
                _eng_sb = _get_eng_sb()
                if _eng_sb:
                    _hj_sb = _eng_sb.get_engine()
                    if _hj_sb and 0 <= ch_idx < 32:
                        _st_sb = _hj_sb.get_state()
                        _gv_sb = _st_sb.get_gr_levels(ch_idx)
                        gr_db_val = min(12.0, max(0.0, float(_gv_sb[0]))) if _gv_sb else 0.0
                        sig_norm  = min(1.0, _hj_sb.get_meter_rms(ch_idx) * 4.0)
            except Exception:
                pass

        sig_h   = max(0.0, min(1.0, sig_norm)) * rh
        if sig_h > 0.5:
            gr_h    = max(0.0, min(1.0, gr_db_val / 24.0)) * sig_h  # 24dB full scale
            green_h = sig_h - gr_h
            if green_h > 0.5:
                _draw_rect(bx, ry, bar_w, green_h, (0.05, 0.55, 0.25, 0.85))
                if green_h > 3*scale:
                    _draw_rect(bx, ry+green_h-2*scale, bar_w, 2*scale,
                               (0.1, 0.9, 0.4, 0.95))
            if gr_h > 0.5:
                _draw_rect(bx, ry+green_h, bar_w, gr_h, (0.85, 0.15, 0.15, 0.9))
                if gr_h > 2*scale:
                    _draw_rect(bx, ry+green_h+gr_h-2*scale, bar_w, 2*scale,
                               (1.0, 0.35, 0.35, 1.0))

        for db_t in [0.25, 0.5, 0.75]:
            _draw_rect(bx, ry + db_t*rh, bar_w, max(0.5, scale*0.5),
                       (0.2, 0.2, 0.2, 1.0))
        _draw_text("GR", bx + bar_w/2 - _text_width("GR",fs)/2,
                   ry + rh + 2*scale, fs, (0.3,0.3,0.3,1.0))


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


def _draw_channel_buttons(rx, ry, rack, scale):
    """Draw channel assignment buttons — always exactly 9 per rack group.

    ch0–ch8 are LOCAL indices within the rack's group.
    The label shows the absolute VSE channel number (group_idx*9 + local + 1).
    """
    btn_s = CH_BTN_SIZE * scale
    gap   = 4 * scale
    fs    = max(1, int(10*scale))

    group_idx = getattr(rack, 'group_idx', 0)
    offset    = group_idx * 9   # absolute VSE channel offset

    shader = _get_shader()
    for local_idx in range(9):
        row = local_idx // 3
        col = local_idx % 3
        bx  = rx + col * (btn_s + gap)
        by  = ry - row * (btn_s + gap) - btn_s

        attr     = f'ch{local_idx}'
        assigned = getattr(rack, attr, False)

        if assigned:
            bg = (0.0, 0.18, 0.10, 1.0)
            bc = (0.0, 0.75, 0.45, 1.0)
            tc = (0.0, 0.85, 0.55, 1.0)
        else:
            bg = (0.07, 0.07, 0.07, 1.0)
            bc = (0.2,  0.2,  0.2,  1.0)
            tc = (0.2,  0.2,  0.2,  1.0)

        _draw_rect(bx, by, btn_s, btn_s, bg)
        verts = [(bx,by),(bx+btn_s,by),(bx+btn_s,by+btn_s),(bx,by+btn_s),(bx,by)]
        batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
        shader.bind(); shader.uniform_float("color", bc); batch.draw(shader)

        # Label shows absolute VSE channel number
        label = str(offset + local_idx + 1)
        tw    = _text_width(label, fs)
        _draw_text(label, bx + btn_s/2 - tw/2,
                   by + btn_s/2 - fs/2, fs, tc)


# Band colours for multiband display (matching C6-style)
BAND_COLORS = [
    (0.2, 0.5, 1.0, 0.85),   # Low      — blue
    (0.2, 0.9, 0.4, 0.85),   # Low-Mid  — green
    (1.0, 0.7, 0.1, 0.85),   # High-Mid — amber
    (1.0, 0.3, 0.3, 0.85),   # High     — red
]
BAND_NAMES  = ["Low", "L-Mid", "H-Mid", "High"]
BAND_FREQS  = ["<120Hz", "120-800Hz", "800Hz-5kHz", ">5kHz"]




def _draw_rack_expanded(rx, ry, rack, rack_idx, scale, rack_width=None):
    """Draw a fully expanded rack unit."""
    _rs = _get_racks_state()
    EFFECT_TYPES      = _rs['EFFECT_TYPES']
    PRESETS           = _rs['PRESETS']
    _reorder_open     = _rs['_reorder_open']
    _reorder_rack_idx = _rs['_reorder_rack_idx']
    _led_states       = _rs['_led_states']
    rw  = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh  = (RACK_EXPANDED_H_MB  if rack.effect_type == "COMP_MULTI"
           else RACK_EXPANDED_H_EQ if rack.effect_type == "EQ"
           else RACK_EXPANDED_H_RV if rack.effect_type == "REVERB"
           else RACK_EXPANDED_H_NG if rack.effect_type == "NOISE_GATE"
           else RACK_EXPANDED_H_DL if rack.effect_type == "DELAY"
           else RACK_EXPANDED_H_DL if rack.effect_type == "BOOSTER"
           else RACK_EXPANDED_H_MX if rack.effect_type == "MIXDOWN"
           else RACK_EXPANDED_H) * scale

    # --- CHASSIS ---
    _draw_rect(rx, ry, rw, rh, (0.1, 0.1, 0.1, 1.0))
    shader = _get_shader()
    verts  = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch  = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.28,0.28,0.28,1.0))
    batch.draw(shader)

    # --- TOP RAIL ---
    rail_h = RACK_RAIL_H * scale
    _draw_rect(rx, ry+rh-rail_h, rw, rail_h, (0.16, 0.16, 0.16, 1.0))
    _draw_rect(rx, ry+rh-rail_h-2*scale, rw, 2*scale, (0.1,0.1,0.1,1.0))

    # Corner screws
    for sx2, sy2 in [(rx+14*scale, ry+rh-16*scale),
                     (rx+rw-14*scale, ry+rh-16*scale),
                     (rx+14*scale, ry+14*scale),
                     (rx+rw-14*scale, ry+14*scale)]:
        _draw_circle(sx2, sy2, 4*scale, (0.07,0.07,0.07,1.0))
        _draw_circle(sx2, sy2, 4*scale, (0.3,0.3,0.3,1.0), filled=False)
        _draw_line(sx2-3*scale, sy2, sx2+3*scale, sy2, (0.3,0.3,0.3,0.8))
        _draw_line(sx2, sy2-3*scale, sx2, sy2+3*scale, (0.3,0.3,0.3,0.8))

    # --- COLLAPSE ARROW — dedicated button, far left of rail ---
    # Clear ▲ icon in its own 28px zone so it's always visible and clickable
    col_btn_x = rx + 4*scale
    col_btn_y = ry + rh - 28*scale
    col_btn_w = 24*scale
    col_btn_h = 20*scale
    _draw_rect(col_btn_x, col_btn_y, col_btn_w, col_btn_h, (0.10, 0.10, 0.12, 1.0))
    col_bverts = [(col_btn_x, col_btn_y), (col_btn_x+col_btn_w, col_btn_y),
                  (col_btn_x+col_btn_w, col_btn_y+col_btn_h),
                  (col_btn_x, col_btn_y+col_btn_h), (col_btn_x, col_btn_y)]
    col_bb = batch_for_shader(shader, "LINE_STRIP", {"pos": col_bverts})
    shader.bind(); shader.uniform_float("color", (0.35, 0.35, 0.40, 1.0))
    col_bb.draw(shader)
    # ▲ triangle pointing up — indicates click to collapse
    ax = col_btn_x + col_btn_w * 0.5
    ay = col_btn_y + col_btn_h * 0.5
    arrow = [(ax - 5*scale, ay - 3*scale),
             (ax + 5*scale, ay - 3*scale),
             (ax,           ay + 5*scale)]
    batch = batch_for_shader(shader, "TRIS", {"pos": arrow})
    shader.uniform_float("color", (0.65, 0.65, 0.70, 1.0)); batch.draw(shader)

    # --- RACK NUMBER BADGE + EFFECT NAME ---
    # Badge starts after the collapse button — no overlap
    etype  = rack.effect_type
    enames = dict(EFFECT_TYPES)
    ename  = enames.get(etype, etype)
    fs_name = max(1, int(11*scale))

    badge_label = str(rack_idx + 1)
    badge_fs    = max(1, int(13*scale))
    badge_x     = col_btn_x + col_btn_w + 4*scale   # starts after collapse button
    badge_y     = ry + rh - 28*scale
    badge_w     = max(22*scale, _text_width(badge_label, badge_fs) + 12*scale)
    badge_h     = 20*scale

    badge_open  = _reorder_open and _reorder_rack_idx == rack_idx
    badge_bg    = (0.2, 0.45, 0.75, 1.0) if badge_open else (0.12, 0.25, 0.45, 1.0)
    _draw_rect(badge_x, badge_y, badge_w, badge_h, badge_bg)
    bverts = [(badge_x, badge_y), (badge_x+badge_w, badge_y),
              (badge_x+badge_w, badge_y+badge_h),
              (badge_x, badge_y+badge_h), (badge_x, badge_y)]
    bb2 = batch_for_shader(shader, "LINE_STRIP", {"pos": bverts})
    shader.bind()
    shader.uniform_float("color", (0.35, 0.7, 1.0, 0.7))
    bb2.draw(shader)
    tw_b = _text_width(badge_label, badge_fs)
    _draw_text(badge_label, badge_x + badge_w/2 - tw_b/2,
               badge_y + badge_h/2 - badge_fs/2,
               badge_fs, (0.35, 0.7, 1.0, 1.0))
    arr_fs = max(1, int(8*scale))
    _draw_text("▾", badge_x + badge_w - 10*scale,
               badge_y + 2*scale, arr_fs, (0.35, 0.7, 1.0, 0.8))
    badge_w = badge_w + 6*scale

    _draw_text(ename.upper(),
               badge_x + badge_w,
               ry+rh-22*scale, fs_name, (0.75,0.75,0.75,1.0))

    # --- PRESET SELECTOR ---
    presets   = PRESETS.get(etype, ["Default"])
    p_idx     = rack.preset_idx % max(1, len(presets))
    p_name    = presets[p_idx]
    p_box_x   = rx + 280*scale
    p_box_w   = 160*scale
    p_box_y   = ry + rh - 26*scale
    p_box_h   = 16*scale

    lax = p_box_x - 14*scale
    lay = ry + rh - 18*scale
    la  = [(lax, lay), (lax+10*scale, lay+6*scale), (lax+10*scale, lay-6*scale)]
    batch = batch_for_shader(shader,"TRIS",{"pos":la})
    shader.uniform_float("color",(0.4,0.4,0.4,1.0)); batch.draw(shader)

    _draw_rect(p_box_x, p_box_y, p_box_w, p_box_h, (0.07,0.07,0.07,1.0))
    fs_p = max(1, int(9*scale))
    tw   = _text_width(p_name, fs_p)
    _draw_text(p_name, p_box_x + p_box_w/2 - tw/2,
               p_box_y + p_box_h/2 - fs_p/2 + 1, fs_p, (0.7,0.7,0.7,1.0))

    rax = p_box_x + p_box_w + 4*scale
    ray = ry + rh - 18*scale
    ra  = [(rax+10*scale, ray), (rax, ray+6*scale), (rax, ray-6*scale)]
    batch = batch_for_shader(shader,"TRIS",{"pos":ra})
    shader.uniform_float("color",(0.4,0.4,0.4,1.0)); batch.draw(shader)

    # --- DELETE BUTTON ---
    del_x = rx + rw - 26*scale
    del_y = ry + rh - 27*scale
    del_w = 18*scale
    del_h = 16*scale
    _draw_rect(del_x, del_y, del_w, del_h, (0.18, 0.04, 0.04, 1.0))
    shader2 = _get_shader()
    dv = [(del_x,del_y),(del_x+del_w,del_y),
          (del_x+del_w,del_y+del_h),(del_x,del_y+del_h),(del_x,del_y)]
    db = batch_for_shader(shader2,"LINE_STRIP",{"pos":dv})
    shader2.bind(); shader2.uniform_float("color",(0.6,0.1,0.1,1.0)); db.draw(shader2)
    fs_del = max(1, int(9*scale))
    tw_del = _text_width("X", fs_del)
    _draw_text("X", del_x+del_w/2-tw_del/2, del_y+del_h/2-fs_del/2+1,
               fs_del, (0.8, 0.15, 0.15, 1.0))

    # --- ON/BYPASS BUTTON ---
    on_x = rx + rw - 68*scale
    on_y = ry + rh - 27*scale
    on_w = 40*scale
    on_h = 16*scale
    if rack.enabled:
        _draw_rect(on_x, on_y, on_w, on_h, (0.0, 0.13, 0.0, 1.0))
        on_col = (0.0, 0.65, 0.3, 1.0)
        on_txt = "ON"
    else:
        _draw_rect(on_x, on_y, on_w, on_h, (0.13, 0.0, 0.0, 1.0))
        on_col = (0.65, 0.0, 0.0, 1.0)
        on_txt = "OFF"
    verts = [(on_x,on_y),(on_x+on_w,on_y),
             (on_x+on_w,on_y+on_h),(on_x,on_y+on_h),(on_x,on_y)]
    batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.uniform_float("color", on_col); batch.draw(shader)
    fs_on = max(1, int(9*scale))
    tw    = _text_width(on_txt, fs_on)
    _draw_text(on_txt, on_x+on_w/2-tw/2, on_y+on_h/2-fs_on/2+1,
               fs_on, on_col)

    # --- BODY CONTENT — dispatch by effect type ---
    # Lazy imports: body draw functions live in separate files.
    # Imported here (inside function) to avoid circular import at load time.
    from ui.racks.rack_comp    import _draw_multiband_body
    from ui.racks.rack_eq      import _draw_eq_body
    from ui.racks.rack_reverb  import _draw_reverb_body
    from ui.racks.rack_noisegate import _draw_noisegate_body
    from ui.racks.rack_delay   import _draw_delay_body
    # EFFECT_PARAMS and other live state from Racks
    import Racks as _racks_mod
    EFFECT_PARAMS     = _racks_mod.EFFECT_PARAMS
    KNOB_START_X      = _racks_mod.KNOB_START_X
    KNOB_SPACING      = _racks_mod.KNOB_SPACING
    KNOB_SECTION_W    = _racks_mod.KNOB_SECTION_W
    SPEC_H            = _racks_mod.SPEC_H
    SPEC_W            = _racks_mod.SPEC_W
    SPEC_X            = _racks_mod.SPEC_X
    get_rack_channels = _racks_mod.get_rack_channels
    _gr_levels        = _racks_mod._gr_levels
    body_h = rh - RACK_RAIL_H * scale
    spec_h = min(SPEC_H * scale, body_h - 50*scale)

    if etype == "COMP_MULTI":
        _draw_multiband_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "EQ":
        _draw_eq_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "REVERB":
        _draw_reverb_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "NOISE_GATE":
        _draw_noisegate_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "DELAY":
        _draw_delay_body(rx, ry, rw, rh, rack, rack_idx, scale)
    elif etype == "BOOSTER":
        try:
            from ui.racks.rack_booster import _draw_booster_body
            _draw_booster_body(rx, ry, rw, rh, rack, rack_idx, scale)
        except ImportError as e:
            print(f"[BOOSTER] ImportError: {e}")
        except Exception as e:
            print(f"[BOOSTER] draw error: {e}")
            import traceback; traceback.print_exc()
    elif etype == "MIXDOWN":
        try:
            from ui.racks.rack_mixdown import _draw_mixdown_body
            _draw_mixdown_body(rx, ry, rw, rh, rack, rack_idx, scale)
        except ImportError as e:
            print(f"[MIXDOWN] ImportError — rack_mixdown.py not found: {e}")
        except Exception as e:
            print(f"[MIXDOWN] draw error: {e}")
            import traceback; traceback.print_exc()
    else:
        # Single band: 2x3 knob grid + spectrum + GR meters
        params  = EFFECT_PARAMS.get(etype, [])
        col     = (0.0, 0.65, 0.4)
        knob_r  = 18 * scale
        knob_kx  = [rx + (KNOB_START_X + c*KNOB_SPACING) * scale for c in range(3)]
        body_top = ry
        body_bot = ry + rh - RACK_RAIL_H*scale
        mid_y    = (body_top + body_bot) * 0.5
        ky0      = mid_y + knob_r + 14*scale
        ky1      = mid_y - knob_r - 14*scale

        param_order = [0,1,5, 2,3,4]
        for idx, pi in enumerate(param_order):
            col_i = idx % 3
            row_i = idx // 3
            kx    = knob_kx[col_i]
            ky    = ky0 if row_i == 0 else ky1
            if pi < len(params):
                pkey, plabel, pmin, pmax, pdef, pfmt = params[pi]
                norm   = getattr(rack, f'p{pi}', 0.0)
                actual = pmin + norm*(pmax-pmin)
                try:    val_str = pfmt.format(actual)
                except: val_str = f"{actual:.1f}"
                _draw_knob(kx, ky, knob_r, norm, col, plabel, val_str, scale)

        div_x = rx + KNOB_SECTION_W * scale
        _draw_rect(div_x, ry+4*scale, max(1.0, scale),
                   rh-RACK_RAIL_H*scale-8*scale, (0.2, 0.2, 0.2, 1.0))

        spec_x = rx + SPEC_X * scale
        spec_y = ry + (body_h - spec_h) / 2 - 5*scale
        spec_w = SPEC_W * scale
        _draw_spectrum(spec_x, spec_y, spec_w, spec_h, rack_idx, scale)

        assigned = get_rack_channels(rack)
        gr_x     = spec_x + spec_w + 16*scale
        _draw_gr_meters(gr_x, spec_y, spec_h, rack_idx, assigned, scale)

    # Channel buttons always on right
    ch_right_x = rx + rw - 100*scale
    ch_top_y   = ry + rh - RACK_RAIL_H*scale - 20*scale
    _draw_channel_buttons(ch_right_x, ch_top_y, rack, scale)
    fs_ch = max(1, int(8*scale))
    _draw_text("CHANNELS", ch_right_x + 10*scale,
               ch_top_y + 8*scale, fs_ch, (0.35,0.35,0.35,1.0))



def _draw_rack_collapsed(rx, ry, rack, rack_idx, scale, rack_width=None):
    """Draw a collapsed rack unit — single row.

    Layout (left → right):
      [screws] [▶ expand arrow] [badge#] [EFFECT NAME]  [CH badges…]  [preset]  [ON/OFF] [X]
    Right side buttons are right-aligned; channel badges sit just left of them.
    """
    _rs = _get_racks_state()
    EFFECT_TYPES      = _rs['EFFECT_TYPES']
    PRESETS           = _rs['PRESETS']
    _led_states       = _rs['_led_states']

    # Lazy import — cannot be done at module level (circular import)
    try:
        import Racks as _rk
        get_rack_channels = _rk.get_rack_channels
    except Exception:
        get_rack_channels = lambda r: set()

    rw = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh = RACK_COLLAPSED_H * scale
    cy = ry + rh / 2   # vertical centre

    # Background + border
    _draw_rect(rx, ry, rw, rh, (0.1, 0.1, 0.1, 1.0))
    shader = _get_shader()
    verts  = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch  = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.25,0.25,0.25,1.0))
    batch.draw(shader)

    # Corner screws
    for sx2, sy2 in [(rx+12*scale, cy), (rx+rw-12*scale, cy)]:
        _draw_circle(sx2, sy2, 3*scale, (0.07,0.07,0.07,1.0))
        _draw_circle(sx2, sy2, 3*scale, (0.28,0.28,0.28,1.0), filled=False)
        _draw_line(sx2-2*scale, sy2, sx2+2*scale, sy2, (0.28,0.28,0.28,0.8))
        _draw_line(sx2, sy2-2*scale, sx2, sy2+2*scale, (0.28,0.28,0.28,0.8))

    # Expand arrow (▶)
    ax = rx + 26*scale
    arrow = [(ax-5*scale, cy+5*scale), (ax-5*scale, cy-5*scale), (ax+5*scale, cy)]
    batch = batch_for_shader(shader,"TRIS",{"pos":arrow})
    shader.uniform_float("color",(0.45,0.45,0.45,1.0)); batch.draw(shader)

    # Badge number + effect name (left side)
    etype       = rack.effect_type
    enames      = dict(EFFECT_TYPES)
    ename       = enames.get(etype, etype)
    badge_fs    = max(1, int(12*scale))
    name_fs     = max(1, int(10*scale))
    badge_label = str(rack_idx + 1)
    badge_x     = rx + 38*scale
    _draw_text(badge_label, badge_x, cy - badge_fs/2,
               badge_fs, (0.35, 0.7, 1.0, 1.0))
    badge_w = _text_width(badge_label, badge_fs) + 5*scale
    _draw_text(ename.upper(), badge_x + badge_w, cy - name_fs/2,
               name_fs, (0.6, 0.6, 0.6, 1.0))

    # ── Right-side controls — same pixel sizes as expanded rack, centred in row ──
    # Expanded rack reference: X = 18×16, ON/OFF = 40×16, ch badges = 26×26 (CH_BTN_SIZE)
    btn_h    = 16*scale   # matches expanded rail button height exactly
    btn_y    = cy - btn_h / 2
    ch_size  = CH_BTN_SIZE * scale   # 26px — square channel badges, same as expanded

    # [X] delete — rightmost (18×16, matching expanded)
    del_w  = 18*scale
    del_x  = rx + rw - 22*scale
    _draw_rect(del_x, btn_y, del_w, btn_h, (0.18,0.04,0.04,1.0))
    sd = _get_shader()
    dv = [(del_x,btn_y),(del_x+del_w,btn_y),(del_x+del_w,btn_y+btn_h),(del_x,btn_y+btn_h),(del_x,btn_y)]
    sd.bind(); sd.uniform_float("color",(0.6,0.1,0.1,1.0))
    batch_for_shader(sd,"LINE_STRIP",{"pos":dv}).draw(sd)
    fs_x = max(1, int(9*scale))
    tw_x = _text_width("X", fs_x)
    _draw_text("X", del_x+del_w/2-tw_x/2, btn_y+btn_h/2-fs_x/2+1, fs_x, (0.8,0.15,0.15,1.0))

    # [ON/OFF] button — left of delete (40×16, matching expanded)
    onoff_w = 40*scale
    onoff_x = del_x - onoff_w - 4*scale
    if rack.enabled:
        _draw_rect(onoff_x, btn_y, onoff_w, btn_h, (0.0, 0.13, 0.0, 1.0))
        on_col = (0.0, 0.65, 0.3, 1.0)
        on_txt = "ON"
    else:
        _draw_rect(onoff_x, btn_y, onoff_w, btn_h, (0.13, 0.0, 0.0, 1.0))
        on_col = (0.65, 0.0, 0.0, 1.0)
        on_txt = "OFF"
    so = _get_shader()
    ov = [(onoff_x,btn_y),(onoff_x+onoff_w,btn_y),(onoff_x+onoff_w,btn_y+btn_h),(onoff_x,btn_y+btn_h),(onoff_x,btn_y)]
    so.bind(); so.uniform_float("color", on_col)
    batch_for_shader(so,"LINE_STRIP",{"pos":ov}).draw(so)
    fs_on = max(1, int(9*scale))
    tw_on = _text_width(on_txt, fs_on)
    _draw_text(on_txt, onoff_x+onoff_w/2-tw_on/2, btn_y+btn_h/2-fs_on/2+1, fs_on, on_col)

    # Channel assignment badges — left of ON/OFF (26×26 squares, matching expanded)
    assigned = get_rack_channels(rack)
    ch_gap   = 4*scale
    fs_b     = max(1, int(10*scale))
    ch_right = onoff_x - 6*scale
    for i, ch_idx in enumerate(sorted(assigned)):
        bx = ch_right - (i + 1) * (ch_size + ch_gap)
        ch_y = cy - ch_size / 2   # vertically centred (taller than btn_h, that's fine)
        if ch_idx < 9 and getattr(rack, f'ch{ch_idx}', False):
            bg = (0.0, 0.18, 0.10, 1.0)
            bc = (0.0, 0.75, 0.45, 1.0)
            tc = (0.0, 0.85, 0.55, 1.0)
        else:
            bg = (0.07, 0.07, 0.07, 1.0)
            bc = (0.2,  0.2,  0.2,  1.0)
            tc = (0.2,  0.2,  0.2,  1.0)
        _draw_rect(bx, ch_y, ch_size, ch_size, bg)
        sv = _get_shader()
        cv = [(bx,ch_y),(bx+ch_size,ch_y),(bx+ch_size,ch_y+ch_size),(bx,ch_y+ch_size),(bx,ch_y)]
        sv.bind(); sv.uniform_float("color", bc)
        batch_for_shader(sv,"LINE_STRIP",{"pos":cv}).draw(sv)
        tw = _text_width(str(ch_idx+1), fs_b)
        _draw_text(str(ch_idx+1), bx+ch_size/2-tw/2, ch_y+ch_size/2-fs_b/2, fs_b, tc)
        # LED dot — same style as expanded rack channel buttons
        is_lit = _led_states.get((rack_idx, ch_idx), False)
        led_col = (0.0,1.0,0.55,1.0) if is_lit else (0.0,0.25,0.14,1.0)
        _draw_circle(bx + ch_size - 4*scale, ch_y + 4*scale, 2.5*scale, led_col)

    # Preset name — centred in remaining middle space
    presets = PRESETS.get(etype, ["Default"])
    p_idx   = rack.preset_idx % max(1, len(presets))
    p_name  = presets[p_idx]
    fs_p    = max(1, int(8*scale))
    # Centre between end of name label and start of channel badges
    mid_start = badge_x + badge_w + _text_width(ename.upper(), name_fs) + 10*scale
    mid_end   = ch_right - len(assigned) * (ch_size + ch_gap)
    mid_cx    = (mid_start + mid_end) / 2
    tw_p      = _text_width(p_name, fs_p)
    if mid_cx - tw_p/2 > mid_start:   # only draw if it fits
        _draw_text(p_name, mid_cx - tw_p/2, cy - fs_p/2, fs_p, (0.3,0.3,0.3,1.0))


def _draw_add_rack_button(rx, ry, scale, rack_width=None):
    """Draw the + ADD RACK EFFECT button."""
    rw  = (rack_width if rack_width is not None else RACK_WIDTH) * scale
    rh  = 28*scale
    fs  = max(1, int(10*scale))

    _draw_rect(rx, ry, rw, rh, (0.07, 0.07, 0.07, 1.0))

    shader = _get_shader()
    border_verts = [(rx,ry),(rx+rw,ry),(rx+rw,ry+rh),(rx,ry+rh),(rx,ry)]
    batch = batch_for_shader(shader,"LINE_STRIP",{"pos":border_verts})
    shader.bind()
    shader.uniform_float("color",(0.2,0.2,0.2,1.0))
    batch.draw(shader)

    label = "+  ADD RACK EFFECT"
    tw    = _text_width(label, fs)
    _draw_text(label, rx + rw/2 - tw/2,
               ry + rh/2 - fs/2, fs, (0.3,0.3,0.3,1.0))


def _draw_add_popup(px, py, scale):
    """Draw the effect type selector popup."""
    _rs = _get_racks_state()
    EFFECT_TYPES = _rs['EFFECT_TYPES']
    popup_w  = 200*scale
    title_h  = 24*scale
    popup_h  = (len(EFFECT_TYPES) * 28 + 10) * scale + title_h
    popup_x  = px
    popup_y  = py

    # Shadow
    _draw_rect(popup_x+3*scale, popup_y-3*scale,
               popup_w, popup_h, (0.0,0.0,0.0,0.5))
    # Background
    _draw_rect(popup_x, popup_y, popup_w, popup_h, (0.15,0.15,0.15,1.0))
    shader = _get_shader()
    verts  = [(popup_x, popup_y),
              (popup_x+popup_w, popup_y),
              (popup_x+popup_w, popup_y+popup_h),
              (popup_x, popup_y+popup_h),
              (popup_x, popup_y)]
    batch = batch_for_shader(shader,"LINE_STRIP",{"pos":verts})
    shader.bind()
    shader.uniform_float("color",(0.35,0.35,0.35,1.0))
    batch.draw(shader)

    # Title bar at top of popup
    _draw_rect(popup_x, popup_y+popup_h-title_h,
               popup_w, title_h, (0.18,0.18,0.18,1.0))
    fs_t = max(1, int(9*scale))
    _draw_text("SELECT EFFECT", popup_x+8*scale,
               popup_y+popup_h-title_h+6*scale, fs_t, (0.6,0.6,0.6,1.0))
    _draw_rect(popup_x, popup_y+popup_h-title_h,
               popup_w, max(0.5,scale*0.5), (0.3,0.3,0.3,1.0))

    # Effect type buttons — start below title bar
    fs_b = max(1, int(10*scale))
    for i, (etype, ename) in enumerate(EFFECT_TYPES):
        by = popup_y + popup_h - title_h - (i+1)*28*scale - 4*scale
        bh = 24*scale
        # Hover highlight — simple alternating for now
        bg = (0.18,0.18,0.18,1.0) if i % 2 == 0 else (0.14,0.14,0.14,1.0)
        _draw_rect(popup_x+2*scale, by, popup_w-4*scale, bh, bg)
        _draw_text(ename, popup_x+12*scale, by+bh/2-fs_b/2,
                   fs_b, (0.75,0.75,0.75,1.0))


# ---------------------------------------------------------------------------
# Main draw entry point — called by Loader.py draw_callback_px
# ---------------------------------------------------------------------------
