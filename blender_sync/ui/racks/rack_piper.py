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
_CH_NUM_OFF = (0.55,  0.20,  0.32,  1.0)   # channel number text, unassigned state — brighter than the old (0.25,0.06,0.12) so it reads against the baked button art

RACK_RAIL_H = 32

# =============================================================================
# GENERATE / PREVIEW / CLEAR BUTTON TUNING
# All values are UNSCALED px, applied at draw time (multiplied by scale).
# Each button's final position/size is its own natural flow position (the
# same left-to-right layout as before, computed from sp_x/sp_w only — never
# from another button's tuned position) PLUS its own offset/scale below.
# That means nudging GENERATE never moves PREVIEW or CLEAR and vice versa —
# each of the three is fully independent.
# Mirrored in Racks.py's hit_test_ai_racks() Piper block, which imports
# these same constants — change here and clicks stay aligned with the art.
# =============================================================================
PIPER_GEN_BTN_X_OFFSET = 6.5   # GENERATE: left(-)/right(+)
PIPER_GEN_BTN_Y_OFFSET = 1.5    # GENERATE: down(-)/up(+)
PIPER_GEN_BTN_W_SCALE  = 0.96    # GENERATE width multiplier
PIPER_GEN_BTN_H_SCALE  = 1.18    # height multiplier — shared by all three boxes

PIPER_PV_BTN_X_OFFSET  = 6.5    # PREVIEW: left(-)/right(+)
PIPER_PV_BTN_Y_OFFSET  = 1.5    # PREVIEW: down(-)/up(+)
PIPER_PV_BTN_W_SCALE   = 1.125    # PREVIEW width multiplier

PIPER_CL_BTN_X_OFFSET  = 16    # CLEAR: left(-)/right(+)
PIPER_CL_BTN_Y_OFFSET  = 1.5    # CLEAR: down(-)/up(+)
PIPER_CL_BTN_W_SCALE   = 1.225    # CLEAR width multiplier

# =============================================================================
# VOICE PANEL POSITION / WIDTH TUNING
# X_OFFSET is unscaled px (+ = right/-  = left), applied on top of the panel's
# natural left edge (split_x). W_SCALE is a multiplier on the computed panel
# width — width still grows/shrinks from the right edge regardless of X_OFFSET.
# Mirrored in Racks.py's hit_test_ai_racks() Piper block (_vp_x_p) so the
# voice-card and scroll-arrow click zones stay aligned with the art.
# =============================================================================
PIPER_VOICE_PANEL_X_OFFSET = 5.0
PIPER_VOICE_PANEL_W_SCALE  = 0.98

# =============================================================================
# KNOB TUNING
# SPEED / NOISE / NOISE W labels are now baked into the background art, so
# the knob label text itself is suppressed below — only the value readout
# (e.g. "1.0×") still draws. Each knob's value has its own independent X/Y
# offset (unscaled px, + = right/up) since the three don't necessarily need
# identical nudges against the baked art.
# =============================================================================
PIPER_SPEED_VALUE_X_OFFSET  = -2.0
PIPER_SPEED_VALUE_Y_OFFSET  = -4.0
PIPER_NOISE_VALUE_X_OFFSET  = -2.0
PIPER_NOISE_VALUE_Y_OFFSET  = -4.0
PIPER_NOISEW_VALUE_X_OFFSET = -2.0
PIPER_NOISEW_VALUE_Y_OFFSET = -4.0

# =============================================================================
# OUTPUT WAVEFORM Y NUDGE
# Purely visual — offsets where the waveform panel is drawn without touching
# the layout math the script/voice panels above it are derived from.
# =============================================================================
PIPER_WAVE_Y_OFFSET = -15.0

# =============================================================================
# BOTTOM-LEFT "PLACE ON CHANNEL" NUMBER LABEL TUNING
# The channel buttons themselves are baked into the background art, so only
# the number text draws in code. Unscaled px, + = right/up. Numbers were
# sitting too high against the baked art, hence the negative Y default.
# =============================================================================
PIPER_CH_BTN_LABEL_X_OFFSET = 0.0
PIPER_CH_BTN_LABEL_Y_OFFSET = 3.0

# =============================================================================
# SCRIPT TEXT AREA TUNING
# Controls the typed script text itself — not the panel/box, which is baked
# into the skin art and untouched by these. FONT_SIZE is unscaled px (same
# baseline-before-scale convention as the rest of the file) for the typed
# lines. X_OFFSET shifts where each line starts (+ = right, - = left), and
# RIGHT_INSET pulls the wrap boundary in from the right edge — use either or
# both together to narrow the effective typing column from one or both
# sides without touching sp_x/sp_w, which GENERATE/PREVIEW/CLEAR and the
# voice panel are still measured from.
# =============================================================================
PIPER_SCRIPT_FONT_SIZE        = 9.0   # base px size of typed text (was fixed at 7)
PIPER_SCRIPT_TEXT_X_OFFSET    = 6.0   # shifts the typed text right(+)/left(-)
PIPER_SCRIPT_TEXT_RIGHT_INSET = 6.0   # pulls the wrap edge in from the right


# ---------------------------------------------------------------------------
# Click/drag → character index in the script text.
#
# Reuses the EXACT SAME word-wrap layout as the script panel draw code
# below (_draw_piper_body), so a click always lands on the character it
# visually appears next to. Shared by Racks.py's handle_ai_rack_click()
# (click-to-position-cursor) and ui.mixer.interaction's drag-select
# handling — both import this rather than keeping their own copy, so the
# two can never drift out of sync with what's drawn on screen.
#
# sp_x/sp_y/sp_w/sp_h are the SAME script-panel geometry _draw_piper_body
# uses (sp_x/sp_y/sp_w/sp_h there); mouse_x/mouse_y are region-space,
# scroll-adjusted coordinates — the same space rack_x/rack_y hit-testing
# already works in throughout Racks.py.
# ---------------------------------------------------------------------------
def cursor_index_from_xy(text, sp_x, sp_y, sp_w, sp_h, scale, mouse_x, mouse_y):
    fs_lbl  = max(1, int(8*scale))
    fs_txt  = max(1, int(PIPER_SCRIPT_FONT_SIZE*scale))
    line_h  = fs_txt * 1.7
    txt_x0  = sp_x + 6*scale + PIPER_SCRIPT_TEXT_X_OFFSET*scale
    txt_w   = sp_w - 8*scale - (PIPER_SCRIPT_TEXT_X_OFFSET + PIPER_SCRIPT_TEXT_RIGHT_INSET)*scale
    max_chars  = max(1, int(txt_w / max(1, fs_txt*0.62)))
    text_y     = sp_y + sp_h - fs_lbl - 8*scale - line_h
    lines_area = sp_h - fs_lbl - 8*scale - 28*scale
    max_lines  = max(1, int(lines_area / line_h))

    if not text:
        return 0

    # Word-wrap — identical algorithm to _draw_piper_body below.
    display_lines = []
    current = ""
    for word in text.split():
        test = (current + " " + word).strip() if current else word
        if len(test) <= max_chars:
            current = test
        else:
            if current:
                display_lines.append(current)
            current = word
    if current:
        display_lines.append(current)
    if not display_lines:
        return 0

    visible_lines = display_lines[-max_lines:]
    offset        = max(0, len(display_lines) - max_lines)

    # Cumulative char-start per display line — identical to _draw_piper_body.
    line_char_starts = []
    acc = 0
    for ln in display_lines:
        line_char_starts.append(acc)
        acc += len(ln) + 1

    # Closest visible line to mouse_y (same top-down layout as the draw loop).
    best_i, best_dist = 0, None
    for i in range(len(visible_lines)):
        ly = text_y - i * line_h
        if ly < sp_y + 28*scale:
            break
        dist = abs(mouse_y - ly)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_i    = i
    li_abs = min(best_i + offset, len(display_lines) - 1)
    line   = display_lines[li_abs]

    # Closest character boundary within that line to mouse_x.
    rel_x = mouse_x - txt_x0
    if rel_x <= 0:
        col = 0
    else:
        col = len(line)
        for c in range(len(line) + 1):
            w = _text_width(line[:c], fs_txt)
            if w >= rel_x:
                if c > 0:
                    w_prev = _text_width(line[:c-1], fs_txt)
                    col = c-1 if (rel_x - w_prev) < (w - rel_x) else c
                else:
                    col = 0
                break

    return line_char_starts[li_abs] + col


# ---------------------------------------------------------------------------
# Waveform panel (reused from deepfilternet style)
# ---------------------------------------------------------------------------
def _draw_wave(px, py, pw, ph, scale, data, rgb, label, placeholder=None, has_skin=False):
    sh = gpu.shader.from_builtin("UNIFORM_COLOR")
    if not has_skin:
        _draw_rect(px, py, pw, ph, _PANEL)
        bv = [(px,py),(px+pw,py),(px+pw,py+ph),(px,py+ph),(px,py)]
        b  = batch_for_shader(sh, "LINE_STRIP", {"pos": bv})
        sh.bind(); sh.uniform_float("color", _BORDER); b.draw(sh)
    fs = max(1, int(8*scale))
    if label:
        _draw_text(label, px+5*scale, py+ph-fs-3*scale, fs, _TEXT_DIM)
    cy  = py + ph*0.5
    amp = ph*0.38
    if not has_skin:
        # Idle centre-line reference — only needed against the flat GPU
        # panel; the skin art already has its own baked-in graph baseline.
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
        if not has_skin:
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

    # Full-rack photoreal skin — when present, Racks.py's _draw_ai_rack_expanded
    # has already blit the whole unit (rail + body) before calling this
    # function, so the flat panel fills below are skipped entirely and only
    # dynamic content (text, waveform, knob state, highlights) draws on top.
    # Falls back to the old flat panel look if the PNG isn't found.
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_pp
        _has_skin = _gtc_pp("rack_piper_bg") is not None
    except Exception:
        _has_skin = False

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

    sh = gpu.shader.from_builtin("UNIFORM_COLOR")
    if not _has_skin:
        # ── Background ───────────────────────────────────────────────────
        _draw_rect(rx, body_bot, rw, body_h, _BG)
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

    # Panel fill/border — flat-GPU fallback only. The focus glow (red/pink
    # border box on click) has been removed per request: the baked skin art
    # already reads clearly enough without it, so the border is now only
    # ever drawn for the no-skin fallback, never as an active-state cue.
    panel_bg = (0.06, 0.02, 0.04, 1.0) if text_active else _PANEL

    if not _has_skin:
        _draw_rect(sp_x, sp_y, sp_w, sp_h, panel_bg)
        bvs = [(sp_x,sp_y),(sp_x+sp_w,sp_y),(sp_x+sp_w,sp_y+sp_h),(sp_x,sp_y+sp_h),(sp_x,sp_y)]
        bbs = batch_for_shader(sh, "LINE_STRIP", {"pos": bvs})
        sh.bind()
        sh.uniform_float("color", _ACCENT if text_active else _BORDER)
        bbs.draw(sh)

    # Typed-text left start and wrap-width, independent of the panel's own
    # sp_x/sp_w (which the buttons/voice panel below are measured from) —
    # see SCRIPT TEXT AREA TUNING above.
    _txt_x0 = sp_x + 6*scale + PIPER_SCRIPT_TEXT_X_OFFSET*scale

    # "Click to type" hint when empty and inactive
    if not script_txt and not text_active:
        hint_fs = max(1, int(PIPER_SCRIPT_FONT_SIZE*scale))
        _draw_text("Click to type script…",
                   _txt_x0, sp_y+sp_h*0.52, hint_fs, _TEXT_DIM)

    # "SCRIPT" label removed — baked into the skin art now.
    fs_lbl = max(1, int(8*scale))

    # Draw text lines
    fs_txt    = max(1, int(PIPER_SCRIPT_FONT_SIZE*scale))
    line_h    = fs_txt * 1.7
    _txt_w    = sp_w - 8*scale - (PIPER_SCRIPT_TEXT_X_OFFSET + PIPER_SCRIPT_TEXT_RIGHT_INSET)*scale
    max_chars = max(1, int(_txt_w / max(1, fs_txt*0.62)))
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
                    sx     = _txt_x0 + pre_w
                    _draw_rect(sx, ly - 1*scale, max(sel_w, 2*scale),
                               fs_txt + 2*scale, (0.50, 0.10, 0.25, 0.45))

            _draw_text(line, _txt_x0, ly, fs_txt, _TEXT)

            # Cursor blink on the active line
            if text_active and li_abs == cursor_line:
                blink = int(_time.time() * 2) % 2 == 0
                if blink:
                    pre   = line[:cursor_col]
                    cur_x = _txt_x0 + _text_width(pre, fs_txt)
                    _draw_line(cur_x, ly - 1*scale, cur_x, ly + fs_txt + 1*scale,
                               _TEXT, max(1.0, scale))

    # Char counter
    char_count = len(script_txt)
    cc_fs = max(1, int(6*scale))
    cc_txt = f"{char_count} / 4096"
    cc_tw  = _text_width(cc_txt, cc_fs)
    _draw_text(cc_txt, sp_x+sp_w-cc_tw-4*scale, sp_y+3*scale, cc_fs, _TEXT_DIM)

    # Natural flow base positions — the same left-to-right layout as before,
    # computed only from sp_x/sp_w/sp_y (never from another button's tuned
    # position), so each button's own offset/scale below is fully independent.
    _gen_w_base = min(80*scale, sp_w*0.48)
    _pv_w_base  = min(60*scale, sp_w*0.36)
    _cl_w_base  = min(38*scale, sp_w*0.22)
    _gen_x_base = sp_x + 4*scale
    _pv_x_base  = _gen_x_base + _gen_w_base + 4*scale
    _cl_x_base  = _pv_x_base + _pv_w_base + 4*scale
    _row_y_base = sp_y + 4*scale

    # GENERATE button
    gen_w = _gen_w_base * PIPER_GEN_BTN_W_SCALE
    gen_h = max(16*scale, 20*scale) * PIPER_GEN_BTN_H_SCALE
    gen_x = _gen_x_base + PIPER_GEN_BTN_X_OFFSET*scale
    gen_y = _row_y_base + PIPER_GEN_BTN_Y_OFFSET*scale

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

    # PREVIEW button (independent — see natural flow base positions above)
    pv_w = _pv_w_base * PIPER_PV_BTN_W_SCALE
    pv_x = _pv_x_base + PIPER_PV_BTN_X_OFFSET*scale
    pv_y = _row_y_base + PIPER_PV_BTN_Y_OFFSET*scale
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

    # CLEAR button (independent — see natural flow base positions above)
    cl_w = _cl_w_base * PIPER_CL_BTN_W_SCALE
    cl_x = _cl_x_base + PIPER_CL_BTN_X_OFFSET*scale
    cl_y = _row_y_base + PIPER_CL_BTN_Y_OFFSET*scale
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

    vp_x = split_x + margin*0.5 + PIPER_VOICE_PANEL_X_OFFSET*scale
    vp_y = upper_y
    vp_w = (rx + rw - split_x - margin*1.5) * PIPER_VOICE_PANEL_W_SCALE
    vp_h = upper_h

    if not _has_skin:
        _draw_rect(vp_x, vp_y, vp_w, vp_h, _PANEL)
        bvv = [(vp_x,vp_y),(vp_x+vp_w,vp_y),(vp_x+vp_w,vp_y+vp_h),(vp_x,vp_y+vp_h),(vp_x,vp_y)]
        bvb = batch_for_shader(sh, "LINE_STRIP", {"pos": bvv})
        sh.bind(); sh.uniform_float("color", _BORDER); bvb.draw(sh)

    # "VOICE" label removed — baked into the skin art now.

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
    # Label removed — "OUTPUT — GENERATED SPEECH" is baked into the skin art now.
    _draw_wave(rx+margin, wave_y + PIPER_WAVE_Y_OFFSET*scale, rw-margin*2, wave_h, scale,
               wave_data, _WAVE_COL, "",
               placeholder=ph_wave, has_skin=_has_skin)

    # ── CONTROLS STRIP (left: channel, right: knobs) ──────────────────────────
    ctrl_split = rx + rw * 0.45

    # "PLACE ON CHANNEL" label removed — baked into the skin art now.

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
        bc = (0.0, 0.75, 0.45, 1.0) if assigned_c else _CH_NUM_OFF
        if not _has_skin:
            _draw_rect(bx, by, ch_s, ch_s, bg)
            cv3 = [(bx,by),(bx+ch_s,by),(bx+ch_s,by+ch_s),(bx,by+ch_s),(bx,by)]
            cb3 = batch_for_shader(sh, "LINE_STRIP", {"pos": cv3})
            sh.bind(); sh.uniform_float("color", bc); cb3.draw(sh)
        fs_ci = max(1, int(7*scale))
        lbl_c = str(ci+1)
        tw_c  = _text_width(lbl_c, fs_ci)
        _draw_text(lbl_c, bx+ch_s/2-tw_c/2 + PIPER_CH_BTN_LABEL_X_OFFSET*scale,
                   by+ch_s/2-fs_ci/2 + PIPER_CH_BTN_LABEL_Y_OFFSET*scale, fs_ci, bc)

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

    # Per-knob value text offsets, in knob_defs order (SPEED, NOISE, NOISE W)
    _knob_value_offsets = [
        (PIPER_SPEED_VALUE_X_OFFSET,  PIPER_SPEED_VALUE_Y_OFFSET),
        (PIPER_NOISE_VALUE_X_OFFSET,  PIPER_NOISE_VALUE_Y_OFFSET),
        (PIPER_NOISEW_VALUE_X_OFFSET, PIPER_NOISEW_VALUE_Y_OFFSET),
    ]

    for i, (attr, lbl, pdef, _fmt) in enumerate(knob_defs):
        kx   = ctrl_split + knob_w*(i+0.5)
        norm = getattr(rack, attr, pdef)
        # Format value
        if attr == 'p0':
            val = 2.0 - norm*1.5
            vs  = f"{val:.1f}×"
        else:
            vs = f"{norm:.2f}"
        _vx_off, _vy_off = _knob_value_offsets[i]
        _draw_knob(kx, knob_y_c, knob_r, norm,
                   (_TEXT[0], _TEXT[1], _TEXT[2]),
                   "", vs, scale,
                   value_x_offset=_vx_off, value_y_offset=_vy_off)

    # ── STATUS BAR ────────────────────────────────────────────────────────────
    if not _has_skin:
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

    # Status LED — only drawn for the flat-GPU fallback now; the skin art has
    # its own baked-in LED in the bottom-right corner of the status bar.
    if not _has_skin:
        dot_cols = {
            'READY':      _GREEN,
            'PROCESSING': (0.90, 0.50, 0.10, 1.0),
            'PREVIEWING': (0.60, 0.20, 0.80, 1.0),
            'DONE':       _GREEN,
            'ERROR':      (0.90, 0.10, 0.05, 1.0),
        }
        _draw_circle(rx+rw-14*scale, sbar_y+sbar_h/2,
                     3.5*scale, dot_cols.get(status, (0.4, 0.4, 0.4, 1.0)))