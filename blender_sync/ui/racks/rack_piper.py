# =============================================================================
# ui/racks/rack_piper.py
# Piper TTS rack body — called by _draw_ai_rack_expanded in Racks.py.
#
# Layout (body only — rail drawn by Racks.py):
#
#  ┌─────────────────────────────────────────────────────────────────────┐
#  │ [TOP RAIL — collapse▲  badge#  PIPER TTS  ◄ VoiceName ►  ON/OFF X]│
#  ├──────────────────────────────────┬──────────────────────────────────┤
#  │  SCRIPT (text input area)        │  VOICE SELECTOR (card list)      │
#  │                                  │                                  │
#  │  [GENERATE]  [CLEAR]             │  en_US-lessac-medium  ← selected │
#  │  char count                      │  en_US-ryan-high                 │
#  ├──────────────────────────────────┴──────────────────────────────────┤
#  │  OUTPUT WAVEFORM (after generation)                                  │
#  ├─────────────────────────┬───────────────────────────────────────────┤
#  │  PLACE ON CHANNEL  CH1… │  SPEED  NOISE  NOISE_W knobs              │
#  ├─────────────────────────┴───────────────────────────────────────────┤
#  │  ENGINE: piper.exe  |  MODEL: voice  |  OFFLINE              ●      │
#  └─────────────────────────────────────────────────────────────────────┘
#
# rx, ry = bottom-left of FULL rack.  rw, rh = full dimensions.
# Body = ry → ry + rh - RACK_RAIL_H*scale
# =============================================================================

import os
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
# Colour palette — pink/magenta theme to distinguish from DNF red
# ---------------------------------------------------------------------------
_BG         = (0.05,  0.02,  0.04,  1.0)
_BORDER     = (0.22,  0.06,  0.14,  1.0)
_PANEL      = (0.03,  0.01,  0.02,  1.0)
_PANEL_SEL  = (0.12,  0.03,  0.07,  1.0)   # selected voice card bg
_TEXT       = (0.85,  0.20,  0.45,  1.0)   # bright pink label
_TEXT_DIM   = (0.38,  0.08,  0.18,  1.0)   # dim pink
_TEXT_VOICE = (0.70,  0.16,  0.35,  1.0)   # voice name text
_ACCENT     = (0.75,  0.12,  0.32,  1.0)   # borders, highlights
_WAVE_COL   = (0.80,  0.25,  0.55)          # (r,g,b) no alpha — output wave
_GREEN      = (0.05,  0.80,  0.30,  1.0)
_GRID       = (0.18,  0.05,  0.10,  0.5)

RACK_RAIL_H = 32


# ---------------------------------------------------------------------------
# Waveform panel (reused from deepfilternet style)
# ---------------------------------------------------------------------------
def _draw_wave(px, py, pw, ph, scale, data, rgb, label, placeholder=None):
    _draw_rect(px, py, pw, ph, _PANEL)
    sh = gpu.shader.from_builtin("UNIFORM_COLOR")
    bv = [(px,py),(px+pw,py),(px+pw,py+ph),(px,py+ph),(px,py)]
    b  = batch_for_shader(sh, "LINE_STRIP", {"pos": bv})
    sh.bind(); sh.uniform_float("color", _BORDER); b.draw(sh)
    fs = max(1, int(8*scale))
    _draw_text(label, px+5*scale, py+ph-fs-3*scale, fs, _TEXT_DIM)
    cy  = py + ph*0.5
    amp = ph*0.38
    _draw_line(px+2*scale, cy, px+pw-2*scale, cy, _GRID, max(0.5, scale*0.5))
    if data and len(data) > 1:
        n    = len(data)
        step = (pw - 4*scale) / max(1, n-1)
        ox   = px + 2*scale
        top  = [(ox+i*step, cy+data[i]*amp) for i in range(n)]
        bot  = [(ox+i*step, cy-data[i]*amp) for i in range(n)]
        bt = batch_for_shader(sh, "LINE_STRIP", {"pos": top})
        sh.bind(); sh.uniform_float("color", rgb+(0.88,)); bt.draw(sh)
        bb = batch_for_shader(sh, "LINE_STRIP", {"pos": bot})
        sh.bind(); sh.uniform_float("color", (rgb[0]*0.6, rgb[1]*0.6, rgb[2]*0.6, 0.55)); bb.draw(sh)
    else:
        flat = (rgb[0]*0.25, rgb[1]*0.25, rgb[2]*0.25, 0.4)
        _draw_line(px+2*scale, cy, px+pw-2*scale, cy, flat, max(0.8, scale*0.8))
        if placeholder:
            fs_ph = max(1, int(7*scale))
            tw    = _text_width(placeholder, fs_ph)
            _draw_text(placeholder, px+pw/2-tw/2, cy-fs_ph/2, fs_ph, _TEXT_DIM)


# ---------------------------------------------------------------------------
# Main body draw
# ---------------------------------------------------------------------------
def _draw_piper_body(rx, ry, rw, rh, rack, ai_idx, scale):
    """Draw the Piper TTS rack body below the rail."""

    status     = getattr(rack, 'ai_status', 'READY')
    script_txt = getattr(rack, 'ai_text', '') or ''

    # ── Geometry ────────────────────────────────────────────────────────────
    rail_h   = RACK_RAIL_H * scale
    body_bot = ry
    body_top = ry + rh - rail_h
    body_h   = body_top - body_bot

    sbar_h   = max(16*scale, body_h * 0.065)
    sbar_y   = body_bot

    # Bottom strip: channel placement + knobs
    ctrl_h   = body_h * 0.22
    ctrl_bot = sbar_y + sbar_h + 2*scale
    ctrl_top = ctrl_bot + ctrl_h

    # Wave strip
    wave_h   = body_h * 0.18
    wave_y   = ctrl_top + 2*scale
    wave_top = wave_y + wave_h

    # Upper zone: script | voices
    upper_h  = body_top - wave_top - 4*scale
    upper_y  = wave_top + 2*scale

    split_x  = rx + rw * 0.52   # script left | voices right

    # ── Background ───────────────────────────────────────────────────────────
    _draw_rect(rx, body_bot, rw, body_h, _BG)
    sh  = gpu.shader.from_builtin("UNIFORM_COLOR")
    bv  = [(rx,body_bot),(rx+rw,body_bot),(rx+rw,body_top),(rx,body_top),(rx,body_bot)]
    b   = batch_for_shader(sh, "LINE_STRIP", {"pos": bv})
    sh.bind(); sh.uniform_float("color", _BORDER); b.draw(sh)

    margin = 8*scale

    # ── SCRIPT PANEL (left) ──────────────────────────────────────────────────
    sp_x = rx + margin
    sp_y = upper_y
    sp_w = split_x - rx - margin*2
    sp_h = upper_h

    # Check if this rack's text field is active
    import time as _time
    text_active = False
    cursor_pos  = len(script_txt)
    try:
        from ui.mixer.interaction import _active_text_field
        if _active_text_field and _active_text_field.get('ai_idx') == ai_idx:
            text_active = True
            cursor_pos  = _active_text_field.get('cursor', len(script_txt))
    except Exception:
        pass

    # Border glows brighter when active
    panel_border = _ACCENT if text_active else _BORDER
    panel_bg     = (0.06, 0.02, 0.04, 1.0) if text_active else _PANEL

    _draw_rect(sp_x, sp_y, sp_w, sp_h, panel_bg)
    bvs = [(sp_x,sp_y),(sp_x+sp_w,sp_y),(sp_x+sp_w,sp_y+sp_h),(sp_x,sp_y+sp_h),(sp_x,sp_y)]
    bbs = batch_for_shader(sh, "LINE_STRIP", {"pos": bvs})
    sh.bind()
    sh.uniform_float("color", panel_border)
    bbs.draw(sh)

    # "Click to type" hint when empty and inactive
    if not script_txt and not text_active:
        hint_fs = max(1, int(7*scale))
        _draw_text("Click to type script…",
                   sp_x+6*scale, sp_y+sp_h*0.52, hint_fs, _TEXT_DIM)

    fs_lbl = max(1, int(8*scale))
    _draw_text("SCRIPT", sp_x+4*scale, sp_y+sp_h-fs_lbl-3*scale, fs_lbl, _TEXT_DIM)

    # Draw text lines
    fs_txt    = max(1, int(7*scale))
    line_h    = fs_txt * 1.7
    max_chars = max(1, int((sp_w - 8*scale) / max(1, fs_txt*0.62)))
    text_y    = sp_y + sp_h - fs_lbl - 8*scale - line_h
    lines_area = sp_h - fs_lbl - 8*scale - 28*scale   # leave room for buttons
    max_lines  = max(1, int(lines_area / line_h))

    # Wrap script text into display lines
    display_lines = []
    if script_txt:
        words = script_txt.split()
        current = ""
        for word in words:
            test = (current + " " + word).strip() if current else word
            if len(test) <= max_chars:
                current = test
            else:
                if current:
                    display_lines.append(current)
                current = word
        if current:
            display_lines.append(current)
    else:
        display_lines = []

    if display_lines:
        # Work out cursor line/col from cursor_pos
        char_count_so_far = 0
        cursor_line = len(display_lines) - 1
        cursor_col  = len(display_lines[-1]) if display_lines else 0
        if text_active:
            for li, ln in enumerate(display_lines):
                end = char_count_so_far + len(ln)
                if cursor_pos <= end:
                    cursor_line = li
                    cursor_col  = cursor_pos - char_count_so_far
                    break
                char_count_so_far = end + 1   # +1 for space between words

        visible_lines = display_lines[-max_lines:]
        offset        = max(0, len(display_lines) - max_lines)

        # Get selection range for highlight
        sel_start_g = -1
        sel_end_g   = -1
        if text_active:
            try:
                from ui.mixer.interaction import _active_text_field as _atf
                if _atf and _atf.get('ai_idx') == ai_idx:
                    ss = _atf.get('sel_start', -1)
                    se = _atf.get('sel_end',   -1)
                    if ss >= 0 and se >= 0 and ss != se:
                        sel_start_g = min(ss, se)
                        sel_end_g   = max(ss, se)
            except Exception:
                pass

        # Build cumulative char positions per display line
        line_char_starts = []
        acc = 0
        for ln in display_lines:
            line_char_starts.append(acc)
            acc += len(ln) + 1  # +1 for space between words

        for i, line in enumerate(visible_lines):
            ly     = text_y - i * line_h
            li_abs = i + offset
            if ly < sp_y + 28*scale:
                break

            # Selection highlight
            if sel_start_g >= 0 and li_abs < len(line_char_starts):
                ls = line_char_starts[li_abs]
                le = ls + len(line)
                ov_s = max(sel_start_g, ls) - ls
                ov_e = min(sel_end_g,   le) - ls
                if ov_s < ov_e:
                    pre_w  = _text_width(line[:ov_s], fs_txt)
                    sel_w  = _text_width(line[ov_s:ov_e], fs_txt)
                    sx     = sp_x + 6*scale + pre_w
                    _draw_rect(sx, ly - 1*scale, max(sel_w, 2*scale),
                               fs_txt + 2*scale, (0.50, 0.10, 0.25, 0.45))

            _draw_text(line, sp_x+6*scale, ly, fs_txt, _TEXT)

            # Cursor blink on the active line
            if text_active and li_abs == cursor_line:
                blink = int(_time.time() * 2) % 2 == 0
                if blink:
                    pre   = line[:cursor_col]
                    cur_x = sp_x + 6*scale + _text_width(pre, fs_txt)
                    _draw_line(cur_x, ly - 1*scale, cur_x, ly + fs_txt + 1*scale,
                               _TEXT, max(1.0, scale))

    # Char counter
    char_count = len(script_txt)
    cc_fs = max(1, int(6*scale))
    cc_txt = f"{char_count} / 4096"
    cc_tw  = _text_width(cc_txt, cc_fs)
    _draw_text(cc_txt, sp_x+sp_w-cc_tw-4*scale, sp_y+3*scale, cc_fs, _TEXT_DIM)

    # GENERATE button
    gen_w = min(80*scale, sp_w*0.48)
    gen_h = max(16*scale, 20*scale)
    gen_x = sp_x + 4*scale
    gen_y = sp_y + 4*scale

    if status == "PROCESSING":
        g_bg  = (0.16, 0.04, 0.08, 1.0)
        g_col = (0.80, 0.25, 0.50, 1.0)
        g_lbl = "GENERATING…"
    else:
        g_bg  = (0.14, 0.03, 0.07, 1.0)
        g_col = _TEXT
        g_lbl = "GENERATE"

    _draw_rect(gen_x, gen_y, gen_w, gen_h, g_bg)
    gv = [(gen_x,gen_y),(gen_x+gen_w,gen_y),(gen_x+gen_w,gen_y+gen_h),
          (gen_x,gen_y+gen_h),(gen_x,gen_y)]
    gb = batch_for_shader(sh, "LINE_STRIP", {"pos": gv})
    sh.bind(); sh.uniform_float("color", g_col); gb.draw(sh)
    fs_g  = max(1, int(8*scale))
    tw_g  = _text_width(g_lbl, fs_g)
    _draw_text(g_lbl, gen_x+gen_w/2-tw_g/2, gen_y+gen_h/2-fs_g/2, fs_g, g_col)

    # PREVIEW button (next to GENERATE)
    pv_w = min(60*scale, sp_w*0.36)
    pv_x = gen_x + gen_w + 4*scale
    pv_y = gen_y
    if status == "PREVIEWING":
        pv_bg  = (0.12, 0.04, 0.08, 1.0)
        pv_col = (0.80, 0.25, 0.50, 1.0)
        pv_lbl = "▶ …"
    else:
        pv_bg  = (0.08, 0.02, 0.06, 1.0)
        pv_col = (0.60, 0.15, 0.35, 1.0)
        pv_lbl = "▶ PREVIEW"
    _draw_rect(pv_x, pv_y, pv_w, gen_h, pv_bg)
    pvvs = [(pv_x,pv_y),(pv_x+pv_w,pv_y),(pv_x+pv_w,pv_y+gen_h),
            (pv_x,pv_y+gen_h),(pv_x,pv_y)]
    pvb = batch_for_shader(sh, "LINE_STRIP", {"pos": pvvs})
    sh.bind(); sh.uniform_float("color", pv_col); pvb.draw(sh)
    fs_pv = max(1, int(7*scale))
    tw_pv = _text_width(pv_lbl, fs_pv)
    _draw_text(pv_lbl, pv_x+pv_w/2-tw_pv/2, pv_y+gen_h/2-fs_pv/2, fs_pv, pv_col)

    # CLEAR button
    cl_w = min(38*scale, sp_w*0.22)
    cl_x = pv_x + pv_w + 4*scale
    cl_y = gen_y
    _draw_rect(cl_x, cl_y, cl_w, gen_h, (0.08, 0.02, 0.04, 1.0))
    cv = [(cl_x,cl_y),(cl_x+cl_w,cl_y),(cl_x+cl_w,cl_y+gen_h),
          (cl_x,cl_y+gen_h),(cl_x,cl_y)]
    cb = batch_for_shader(sh, "LINE_STRIP", {"pos": cv})
    sh.bind(); sh.uniform_float("color", _TEXT_DIM); cb.draw(sh)
    fs_cl = max(1, int(7*scale))
    tw_cl = _text_width("CLEAR", fs_cl)
    _draw_text("CLEAR", cl_x+cl_w/2-tw_cl/2, cl_y+gen_h/2-fs_cl/2, fs_cl, _TEXT_DIM)

    # ── VOICE SELECTOR (right) ───────────────────────────────────────────────
    try:
        from core.ai_piper import get_voices
        voices = get_voices()
    except Exception:
        voices = []

    vp_x = split_x + margin*0.5
    vp_y = upper_y
    vp_w = rx + rw - split_x - margin*1.5
    vp_h = upper_h

    _draw_rect(vp_x, vp_y, vp_w, vp_h, _PANEL)
    bvv = [(vp_x,vp_y),(vp_x+vp_w,vp_y),(vp_x+vp_w,vp_y+vp_h),(vp_x,vp_y+vp_h),(vp_x,vp_y)]
    bvb = batch_for_shader(sh, "LINE_STRIP", {"pos": bvv})
    sh.bind(); sh.uniform_float("color", _BORDER); bvb.draw(sh)

    _draw_text("VOICE", vp_x+4*scale, vp_y+vp_h-fs_lbl-3*scale, fs_lbl, _TEXT_DIM)

    # Voice index stored in p4 (float), separate from preset_idx (knob preset)
    selected_idx  = int(getattr(rack, 'p4', 0.0)) % max(1, len(voices)) if voices else 0
    voice_scroll  = int(getattr(rack, 'p5', 0.0))   # scroll offset stored in p5
    card_h   = max(22*scale, vp_h * 0.20)
    card_gap = 2*scale
    fs_vn    = max(1, int(8*scale))
    fs_vs    = max(1, int(7*scale))

    # How many cards fit vertically (reserve space for label + scroll arrows)
    arrow_h   = 14*scale
    cards_area = vp_h - fs_lbl - 8*scale - arrow_h*2 - 4*scale
    max_visible = max(1, int(cards_area / (card_h + card_gap)))

    if voices:
        n_voices     = len(voices)
        voice_scroll = max(0, min(voice_scroll, max(0, n_voices - max_visible)))

        # ▲ up arrow (scroll up)
        arr_top_y = vp_y + vp_h - fs_lbl - 8*scale - arrow_h
        sh.bind(); sh.uniform_float("color", _TEXT_DIM if voice_scroll > 0 else _BORDER)
        arr_up = [(vp_x + vp_w/2, arr_top_y+arrow_h-2*scale),
                  (vp_x + vp_w/2 - 8*scale, arr_top_y+2*scale),
                  (vp_x + vp_w/2 + 8*scale, arr_top_y+2*scale)]
        ab_up = batch_for_shader(sh, "TRIS", {"pos": arr_up})
        ab_up.draw(sh)

        # Voice cards
        v_start_y = arr_top_y - card_gap
        for slot in range(max_visible):
            vi = voice_scroll + slot
            if vi >= n_voices:
                break
            name, onnx_path, _ = voices[vi]
            cy_card = v_start_y - slot*(card_h+card_gap) - card_h
            if cy_card < vp_y + arrow_h + 2*scale:
                break
            is_sel = (vi == selected_idx)
            bg = _PANEL_SEL if is_sel else _PANEL
            bc = _ACCENT    if is_sel else _BORDER
            _draw_rect(vp_x+4*scale, cy_card, vp_w-8*scale, card_h, bg)
            cv2 = [(vp_x+4*scale,cy_card),(vp_x+vp_w-4*scale,cy_card),
                   (vp_x+vp_w-4*scale,cy_card+card_h),
                   (vp_x+4*scale,cy_card+card_h),(vp_x+4*scale,cy_card)]
            cb2 = batch_for_shader(sh, "LINE_STRIP", {"pos": cv2})
            sh.bind(); sh.uniform_float("color", bc); cb2.draw(sh)
            tc = _TEXT if is_sel else _TEXT_VOICE
            _draw_text(name, vp_x+8*scale, cy_card+card_h-fs_vn-3*scale, fs_vn, tc)
            fn = os.path.basename(onnx_path).replace(".onnx", "")
            _draw_text(fn, vp_x+8*scale, cy_card+3*scale, fs_vs, _TEXT_DIM)

        # ▼ down arrow
        arr_bot_y = vp_y + 2*scale
        can_scroll_down = (voice_scroll + max_visible) < n_voices
        sh.bind(); sh.uniform_float("color", _TEXT_DIM if can_scroll_down else _BORDER)
        arr_dn = [(vp_x + vp_w/2, arr_bot_y+2*scale),
                  (vp_x + vp_w/2 - 8*scale, arr_bot_y+arrow_h-2*scale),
                  (vp_x + vp_w/2 + 8*scale, arr_bot_y+arrow_h-2*scale)]
        ab_dn = batch_for_shader(sh, "TRIS", {"pos": arr_dn})
        ab_dn.draw(sh)

        # Page indicator e.g. "4-6 / 8"
        if n_voices > max_visible:
            pg_fs = max(1, int(6*scale))
            pg_txt = f"{voice_scroll+1}-{min(voice_scroll+max_visible, n_voices)} / {n_voices}"
            pg_tw  = _text_width(pg_txt, pg_fs)
            _draw_text(pg_txt, vp_x+vp_w/2-pg_tw/2, arr_bot_y+arrow_h+1*scale,
                       pg_fs, _TEXT_DIM)
    else:
        no_fs = max(1, int(7*scale))
        _draw_text("No voices found in", vp_x+6*scale, vp_y+vp_h*0.6, no_fs, _TEXT_DIM)
        _draw_text("ai_engines/piper/voices/", vp_x+6*scale, vp_y+vp_h*0.45, no_fs, _TEXT_DIM)

    # ── OUTPUT WAVEFORM ───────────────────────────────────────────────────────
    try:
        from core.ai_piper import get_output_wave
        wave_data = get_output_wave(ai_idx)
    except Exception:
        wave_data = []

    ph_wave = None if status == "DONE" else "CLICK GENERATE TO SYNTHESISE"
    _draw_wave(rx+margin, wave_y, rw-margin*2, wave_h, scale,
               wave_data, _WAVE_COL, "OUTPUT — GENERATED SPEECH",
               placeholder=ph_wave)

    # ── CONTROLS STRIP (left: channel, right: knobs) ──────────────────────────
    ctrl_split = rx + rw * 0.45

    # PLACE ON CHANNEL label + buttons
    fs_cl2 = max(1, int(7*scale))
    _draw_text("PLACE ON CHANNEL",
               rx+margin, ctrl_bot+ctrl_h*0.72, fs_cl2, _TEXT_DIM)

    ch_s    = 18*scale
    ch_gap  = 3*scale
    ch_y    = ctrl_bot + 2*scale
    scene_c = bpy.context.scene
    high_c  = 0
    if scene_c and scene_c.sequence_editor:
        for _s in scene_c.sequence_editor.sequences_all:
            if _s.type == "SOUND" and _s.sound:
                high_c = max(high_c, _s.channel - 1)
    num_ch = max(9, high_c + 1)
    max_fit = max(1, int((ctrl_split - rx - margin*2) / (ch_s + ch_gap)))
    num_ch  = min(num_ch, max_fit)

    for ci in range(num_ch):
        bx = rx + margin + ci*(ch_s+ch_gap)
        by = ch_y
        assigned_c = getattr(rack, f'ch{ci}', False)
        bg = (0.0, 0.18, 0.10, 1.0) if assigned_c else (0.08, 0.02, 0.04, 1.0)
        bc = (0.0, 0.75, 0.45, 1.0) if assigned_c else (0.25, 0.06, 0.12, 1.0)
        _draw_rect(bx, by, ch_s, ch_s, bg)
        cv3 = [(bx,by),(bx+ch_s,by),(bx+ch_s,by+ch_s),(bx,by+ch_s),(bx,by)]
        cb3 = batch_for_shader(sh, "LINE_STRIP", {"pos": cv3})
        sh.bind(); sh.uniform_float("color", bc); cb3.draw(sh)
        fs_ci = max(1, int(7*scale))
        lbl_c = str(ci+1)
        tw_c  = _text_width(lbl_c, fs_ci)
        _draw_text(lbl_c, bx+ch_s/2-tw_c/2, by+ch_s/2-fs_ci/2, fs_ci, bc)

    # Speed, Noise, Noise_W knobs
    knob_defs = [
        ('p0', 'SPEED',   0.5,  '1.0×'),
        ('p1', 'NOISE',   0.667, '0.67'),
        ('p2', 'NOISE W', 0.8,  '0.80'),
    ]
    n_knobs  = len(knob_defs)
    knob_zone_w = rx + rw - ctrl_split - margin
    knob_w   = knob_zone_w / n_knobs
    knob_r   = min(12*scale, ctrl_h*0.38)
    knob_y_c = ctrl_bot + ctrl_h * 0.58

    for i, (attr, lbl, pdef, _fmt) in enumerate(knob_defs):
        kx   = ctrl_split + knob_w*(i+0.5)
        norm = getattr(rack, attr, pdef)
        # Format value
        if attr == 'p0':
            val = 2.0 - norm*1.5
            vs  = f"{val:.1f}×"
        else:
            vs = f"{norm:.2f}"
        _draw_knob(kx, knob_y_c, knob_r, norm,
                   (_TEXT[0], _TEXT[1], _TEXT[2]),
                   lbl, vs, scale)

    # ── STATUS BAR ────────────────────────────────────────────────────────────
    _draw_rect(rx+6*scale, sbar_y, rw-12*scale, sbar_h, (0.04, 0.01, 0.02, 1.0))
    svs = [(rx+6*scale,sbar_y),(rx+rw-6*scale,sbar_y),
           (rx+rw-6*scale,sbar_y+sbar_h),(rx+6*scale,sbar_y+sbar_h),(rx+6*scale,sbar_y)]
    sb = batch_for_shader(sh, "LINE_STRIP", {"pos": svs})
    sh.bind(); sh.uniform_float("color", _BORDER); sb.draw(sh)

    # Voice name in status bar
    voice_name = "no voice loaded"
    if voices:
        vi = int(getattr(rack, 'p4', 0.0)) % max(1, len(voices))
        voice_name = os.path.basename(voices[vi][1]).replace(".onnx", "")
    fs_sb = max(1, int(7*scale))
    _draw_text(
        f"ENGINE: piper.exe  |  MODEL: {voice_name}  |  OFFLINE",
        rx+14*scale, sbar_y+sbar_h/2-fs_sb/2, fs_sb, _TEXT_DIM)

    dot_cols = {
        'READY':      _GREEN,
        'PROCESSING': (0.90, 0.50, 0.10, 1.0),
        'PREVIEWING': (0.60, 0.20, 0.80, 1.0),
        'DONE':       _GREEN,
        'ERROR':      (0.90, 0.10, 0.05, 1.0),
    }
    _draw_circle(rx+rw-14*scale, sbar_y+sbar_h/2,
                 3.5*scale, dot_cols.get(status, (0.4, 0.4, 0.4, 1.0)))
