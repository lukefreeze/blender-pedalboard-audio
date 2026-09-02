# =============================================================================
# ui/racks/rack_rvc.py
# kNN-VC Voice Conversion rack — system Python / PyTorch dependent (BETA).
#
# New features:
#   - Reference voice cards have ▶ preview button each
#   - Centre panel shows useful state info instead of empty waveform box
#   - "ADD FROM TIMELINE" section with separate channel selector + name input
#   - All audio preview via core.audio.play_oneshot (pedalboard engine device)
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

_BG          = (0.04,  0.02,  0.06,  1.0)
_BORDER      = (0.22,  0.08,  0.30,  1.0)
_PANEL       = (0.03,  0.01,  0.04,  1.0)
_PANEL_SEL   = (0.12,  0.04,  0.18,  1.0)
_ACCENT      = (0.67,  0.27,  0.87,  1.0)
_ACCENT_DIM  = (0.34,  0.10,  0.44,  1.0)
_TEXT        = (0.80,  0.55,  0.95,  1.0)
_TEXT_DIM    = (0.38,  0.16,  0.50,  1.0)
_TEXT_LABEL  = (0.25,  0.08,  0.32,  1.0)
_WARN_BG     = (0.08,  0.05,  0.00,  1.0)
_WARN_BORDER = (0.55,  0.33,  0.00,  1.0)
_WARN_TEXT   = (0.86,  0.53,  0.00,  1.0)
_WARN_DIM    = (0.50,  0.28,  0.00,  1.0)
_GREEN       = (0.05,  0.80,  0.30,  1.0)
_AMBER       = (0.86,  0.53,  0.00,  1.0)

RACK_RAIL_H = 32

# =============================================================================
# CONTROLS PANEL KNOB TUNING
# The drawn knob (arc + pointer) is computed from the panel geometry, then
# scaled by these multipliers — independent per knob so TOPK and REF SECS
# can each be grown/shrunk to match their respective graphic in the skin
# art. 1.0 = original computed size (min(18*scale, right_w*0.28, work_h*0.18)).
# Mirrors rack_base.py's SB_KNOB_SCALE / rack_piper.py's *_W_SCALE pattern.
# =============================================================================
KNNVC_TOPK_KNOB_SCALE    = 1.7
KNNVC_REFSECS_KNOB_SCALE = 1.7

# Per-knob centre position nudge — unscaled px, + = right/up. Needed because
# growing a knob via the *_SCALE constants above only grows the arc/pointer
# around its existing centre; it doesn't re-centre it against the (larger)
# knob graphic baked into the skin art, so each knob needs its own nudge to
# land back on top of its artwork. Same convention as CH_BTN_LABEL_X_OFFSET
# in rack_base.py / PIPER_*_VALUE_X_OFFSET in rack_piper.py.
KNNVC_TOPK_KNOB_X_OFFSET    = -2.0
KNNVC_TOPK_KNOB_Y_OFFSET    = -1.0
KNNVC_REFSECS_KNOB_X_OFFSET = -4.0
KNNVC_REFSECS_KNOB_Y_OFFSET = 0.5

# Left-panel text nudges — unscaled px, + = right. "USE RAIL"/"CH X" is the
# SOURCE box value (src_disp, below); the per-channel digits are the small
# 1-9 buttons in the ADD FROM TIMELINE selector row — NOT the "CH1Sample"
# name-field text, which has its own KNNVC_ADD_NAME_X_OFFSET further down.
KNNVC_SOURCE_VALUE_X_OFFSET = 6.0   # "CH X" / "USE RAIL" readout, top of left panel
KNNVC_SRC_CH_LABEL_X_OFFSET = 0.0   # per-channel number, ADD FROM TIMELINE selector row

# Add-voice name field text nudge — unscaled px, + = right. Moves both the
# live value ("CH1Sample" default, or whatever's been typed) and the "enter
# name..." placeholder; the blinking cursor position is offset by the same
# amount so it stays aligned with the text it's drawn against.
KNNVC_ADD_NAME_X_OFFSET = 4.0

# ADD TO VOICES button label nudge — unscaled px, + = up/- = down.
KNNVC_ADD_BTN_LABEL_Y_OFFSET = -1.5

_READY      = "READY"
_NO_REF     = "NO_REF"
_PROCESSING = "PROCESSING"
_DONE       = "DONE"
_ERROR      = "ERROR"

# Dep check — background thread only, never on draw thread
_dep_cache         = {"ok": False, "checked": False, "checking": False}
_dep_check_running = False


def _run_dep_check():
    global _dep_check_running
    ok = False
    try:
        import subprocess, platform
        from core.ai_python_finder import (
            _win_python_paths, _mac_python_paths, _linux_python_paths)
        sys_name = platform.system()
        candidates = (_win_python_paths() if sys_name == "Windows"
                      else _mac_python_paths() if sys_name == "Darwin"
                      else _linux_python_paths())
        for cmd in candidates:
            try:
                r = subprocess.run(
                    cmd + ["-m", "pip", "show", "torch"],
                    capture_output=True, timeout=5)
                if r.returncode == 0:
                    ok = True
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"[KNNVC] dep check error: {e}")
    _dep_cache["ok"]       = ok
    _dep_cache["checked"]  = True
    _dep_cache["checking"] = False
    _dep_check_running             = False
    print(f"[KNNVC] dep check complete: {'found' if ok else 'not found'}")
    try:
        import bpy
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ('NODE_EDITOR', 'SEQUENCE_EDITOR'):
                    area.tag_redraw()
    except Exception:
        pass

def _check_deps():
    global _dep_check_running
    if not _dep_cache["checked"] and not _dep_check_running:
        _dep_check_running = True
        _dep_cache["checking"] = True
        import threading
        t = threading.Thread(target=_run_dep_check, daemon=True)
        t.start()
    return _dep_cache["ok"]


def _get_voices_dir():
    addon_dir = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(addon_dir, "ai_engines", "knnvc", "voices")


def _discover_ref_voices(ai_idx):
    """Return list of (display_name, wav_path) from ai_engines/knnvc/voices/."""
    try:
        voices_dir = _get_voices_dir()
        if not os.path.isdir(voices_dir):
            return []
        voices = []
        for f in sorted(os.listdir(voices_dir)):
            if f.lower().endswith(".wav"):
                name = os.path.splitext(f)[0].replace("_", " ").title()
                voices.append((name, os.path.join(voices_dir, f)))
        return voices
    except Exception:
        return []


def _draw_setup_warning(rx, ry, rw, rh, scale, has_skin=False):
    sh = gpu.shader.from_builtin("UNIFORM_COLOR")
    rail_h   = RACK_RAIL_H * scale
    body_bot = ry
    body_top = ry + rh - rail_h
    body_h   = body_top - body_bot
    margin   = 8 * scale

    # Outer body fill/border — suppressed when the full-unit skin PNG is
    # already blit underneath by _draw_ai_rack_expanded (Racks.py). The
    # warning card itself always draws its own box below regardless of skin,
    # since it needs to read clearly as an alert no matter what art sits
    # behind it (same convention as rack_piper.py's has_skin gating).
    if not has_skin:
        _draw_rect(rx, body_bot, rw, body_h, _BG)
        bvs = [(rx,body_bot),(rx+rw,body_bot),(rx+rw,body_top),(rx,body_top),(rx,body_bot)]
        bb = batch_for_shader(sh,"LINE_STRIP",{"pos":bvs})
        sh.bind(); sh.uniform_float("color",_BORDER); bb.draw(sh)

    warn_w = rw - margin * 4
    warn_h = min(body_h * 0.78, 160 * scale)
    warn_x = rx + (rw - warn_w) / 2
    warn_y = body_bot + (body_h - warn_h) / 2

    _draw_rect(warn_x, warn_y, warn_w, warn_h, _WARN_BG)
    _draw_rect(warn_x, warn_y, 4*scale, warn_h, _WARN_TEXT)
    wvs = [(warn_x,warn_y),(warn_x+warn_w,warn_y),
           (warn_x+warn_w,warn_y+warn_h),(warn_x,warn_y+warn_h),(warn_x,warn_y)]
    wb = batch_for_shader(sh,"LINE_STRIP",{"pos":wvs})
    sh.bind(); sh.uniform_float("color",_WARN_BORDER); wb.draw(sh)

    tx   = warn_x + 12*scale
    fs_h = max(1, int(9*scale))
    fs_b = max(1, int(8*scale))
    fs_s = max(1, int(7*scale))
    lh   = fs_b + 5*scale
    ty   = warn_y + warn_h - fs_h - 8*scale

    _draw_text("PYTORCH NOT FOUND - BETA RACK REQUIRES SETUP", tx, ty, fs_h, _WARN_TEXT)
    ty -= lh * 1.4
    _draw_text("Voice conversion needs PyTorch + torchaudio in your system Python.", tx, ty, fs_b, _WARN_DIM)
    ty -= lh
    _draw_text("Open a terminal and run:", tx, ty, fs_b, _WARN_DIM)
    ty -= lh * 1.2

    cmd_w = warn_w - 24*scale
    cmd_h = lh * 1.6 + 6*scale
    cmd_x = tx; cmd_y = ty - cmd_h
    _draw_rect(cmd_x, cmd_y, cmd_w, cmd_h, (0.02,0.01,0.00,1.0))
    cvs = [(cmd_x,cmd_y),(cmd_x+cmd_w,cmd_y),(cmd_x+cmd_w,cmd_y+cmd_h),(cmd_x,cmd_y+cmd_h),(cmd_x,cmd_y)]
    cb = batch_for_shader(sh,"LINE_STRIP",{"pos":cvs})
    sh.bind(); sh.uniform_float("color",(0.44,0.24,0.00,1.0)); cb.draw(sh)
    _draw_text("pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126",
               cmd_x+6*scale, cmd_y+cmd_h/2-fs_b/2, fs_b, _WARN_TEXT)

    ty = cmd_y - lh * 1.2
    _draw_text("Models (~1.3GB) auto-download on first use. Restart Blender after install.", tx, ty, fs_s, _WARN_DIM)
    ty -= lh
    _draw_text("Use clean speech clips (10-30s) as reference. Avoid music/background noise.", tx, ty, fs_s, _WARN_DIM)

    btn_w = min(140*scale, warn_w*0.38)
    btn_h = max(16*scale, fs_s+8*scale)
    btn_x = warn_x + warn_w - btn_w - 12*scale
    btn_y = warn_y + 8*scale
    _draw_rect(btn_x, btn_y, btn_w, btn_h, (0.10,0.06,0.00,1.0))
    bvs2 = [(btn_x,btn_y),(btn_x+btn_w,btn_y),(btn_x+btn_w,btn_y+btn_h),(btn_x,btn_y+btn_h),(btn_x,btn_y)]
    bb2 = batch_for_shader(sh,"LINE_STRIP",{"pos":bvs2})
    sh.bind(); sh.uniform_float("color",_WARN_BORDER); bb2.draw(sh)
    fs_btn = max(1,int(7*scale))
    lbl = "OPEN SETUP GUIDE  >"
    tw_btn = _text_width(lbl,fs_btn)
    _draw_text(lbl, btn_x+btn_w/2-tw_btn/2, btn_y+btn_h/2-fs_btn/2, fs_btn, _WARN_TEXT)
    return btn_x, btn_y, btn_w, btn_h


def _draw_rvc_body(rx, ry, rw, rh, rack, ai_idx, scale):
    sh = gpu.shader.from_builtin("UNIFORM_COLOR")

    # Full-rack photoreal skin — when present, Racks.py's _draw_ai_rack_expanded
    # has already blit the whole unit (rail + body) before calling this
    # function, so the flat panel fills/borders below are skipped entirely and
    # only dynamic content (state text, selection highlights, cursor, knob
    # values) draws on top. Falls back to the old flat panel look if the PNG
    # isn't found yet — same convention as rack_piper.py's _has_skin gating.
    # (Replaces the old body-only draw_element() skin attempt, which predates
    # the full-unit blit Racks.py now does before dispatching here.)
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_rvc
        _has_skin = _gtc_rvc("rack_knnvc_bg") is not None
    except Exception:
        _has_skin = False

    # Deps not ready yet — Racks.py's _draw_ai_rack_expanded withholds the
    # full-unit skin blit in this state (see its RVC-specific check right
    # before the blit), so even though the skin texture may already be
    # loaded in cache, nothing has actually been drawn behind us. Force
    # has_skin=False here so the warning card draws its own flat black body
    # chrome instead of assuming art is already sitting underneath it.
    if not _check_deps():
        bx,by,bw,bh = _draw_setup_warning(rx, ry, rw, rh, scale, has_skin=False)
        try: rack['rvc_setup_btn'] = (bx,by,bw,bh)
        except Exception: pass
        return

    rail_h   = RACK_RAIL_H * scale
    body_bot = ry
    body_top = ry + rh - rail_h
    body_h   = body_top - body_bot
    margin   = 8 * scale

    if not _has_skin:
        _draw_rect(rx, body_bot, rw, body_h, _BG)
        bvs = [(rx,body_bot),(rx+rw,body_bot),(rx+rw,body_top),(rx,body_top),(rx,body_bot)]
        bb = batch_for_shader(sh,"LINE_STRIP",{"pos":bvs})
        sh.bind(); sh.uniform_float("color",_BORDER); bb.draw(sh)

    sbar_h   = max(16*scale, body_h*0.07)
    sbar_y   = body_bot
    sbar_x   = rx + margin
    sbar_w   = rw - margin*2

    left_w   = rw * 0.30
    right_w  = rw * 0.24
    centre_w = rw - left_w - right_w - margin*4
    left_x   = rx + margin
    centre_x = left_x + left_w + margin
    right_x  = centre_x + centre_w + margin
    work_bot = sbar_y + sbar_h + 2*scale
    work_top = body_top - 2*scale
    work_h   = work_top - work_bot
    fs_lbl   = max(1, int(7*scale))
    fs_sm    = max(1, int(7*scale))
    status   = getattr(rack, "ai_status", _READY)

    # ── LEFT PANEL: source info + voice cards + add from timeline ─────────────
    lx = left_x; ly = work_bot; lh = work_h
    if not _has_skin:
        _draw_rect(lx, ly, left_w, lh, _PANEL)
        lvs = [(lx,ly),(lx+left_w,ly),(lx+left_w,ly+lh),(lx,ly+lh),(lx,ly)]
        lb = batch_for_shader(sh,"LINE_STRIP",{"pos":lvs})
        sh.bind(); sh.uniform_float("color",_BORDER); lb.draw(sh)

    # Source channel display (assigned from rail)
    # "SOURCE" label suppressed once skinned — expected to be baked into
    # rack_knnvc_bg.png like Piper's "SCRIPT"/"VOICE" labels were.
    if not _has_skin:
        _draw_text("SOURCE", lx+5*scale, ly+lh-fs_lbl-4*scale, fs_lbl, _TEXT_LABEL)
    active_chs = [ci+1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
    src_disp   = f"CH {active_chs[0]}" if active_chs else "USE RAIL"
    fs_ch      = max(1, int(7*scale))
    _draw_text(src_disp, lx+5*scale + KNNVC_SOURCE_VALUE_X_OFFSET*scale,
               ly+lh-fs_lbl-fs_ch-10*scale, fs_ch,
               _ACCENT if active_chs else _TEXT_DIM)

    # ── ADD FROM TIMELINE section (bottom of left panel) ──────────────────
    add_section_h = min(work_h * 0.38, 90*scale)
    add_y         = ly
    add_sep_y     = add_y + add_section_h

    # Separator line — suppressed once skinned, baked into the art.
    if not _has_skin:
        sep_verts = [(lx+4*scale, add_sep_y), (lx+left_w-4*scale, add_sep_y)]
        sep_b = batch_for_shader(sh,"LINES",{"pos":sep_verts})
        sh.bind(); sh.uniform_float("color",_BORDER); sep_b.draw(sh)

    if not _has_skin:
        _draw_text("ADD FROM TIMELINE", lx+5*scale, add_sep_y+fs_lbl+3*scale, fs_lbl, _TEXT_LABEL)

    # Channel selector for timeline extraction (stored in p5 as int 0-8)
    add_ch = int(getattr(rack, "p5", 0.0))  # 0-based
    ch_btn_s = min(16*scale, (left_w-10*scale)/9)
    ch_btn_y = add_y + add_section_h - fs_lbl - ch_btn_s - 4*scale
    if not _has_skin:
        _draw_text("SRC", lx+5*scale, ch_btn_y+ch_btn_s/2-fs_lbl/2, max(1,int(6*scale)), _TEXT_LABEL)
    for ci in range(9):
        bx2 = lx + 5*scale + 16*scale + ci*(ch_btn_s+1*scale)
        by2 = ch_btn_y
        issel = (ci == add_ch)
        bc = _ACCENT if issel else _ACCENT_DIM
        bg = _PANEL_SEL if issel else _PANEL
        # Per-slot button chrome suppressed once skinned — selection state is
        # conveyed by the number colour below instead (mirrors rack_base.py's
        # _draw_channel_buttons number_only fallback treatment).
        if not _has_skin:
            _draw_rect(bx2, by2, ch_btn_s, ch_btn_s, bg)
            cbvs = [(bx2,by2),(bx2+ch_btn_s,by2),(bx2+ch_btn_s,by2+ch_btn_s),(bx2,by2+ch_btn_s),(bx2,by2)]
            cbat = batch_for_shader(sh,"LINE_STRIP",{"pos":cbvs})
            sh.bind(); sh.uniform_float("color",bc); cbat.draw(sh)
        fs_ci = max(1,int(6*scale))
        lc = str(ci+1)
        tw_c = _text_width(lc,fs_ci)
        _draw_text(lc, bx2+ch_btn_s/2-tw_c/2 + KNNVC_SRC_CH_LABEL_X_OFFSET*scale,
                   by2+ch_btn_s/2-fs_ci/2, fs_ci, bc)

    # Name input field (stored in rack['add_voice_name'])
    # Check if this field is active for text input
    import time as _time
    _name_active = False
    _name_cursor = 0
    try:
        from ui.mixer.interaction import _active_text_field as _atf
        if (_atf and _atf.get('ai_idx') == ai_idx
                and _atf.get('field') == 'add_voice_name'):
            _name_active = True
            _name_cursor = _atf.get('cursor', 0)
    except ImportError:
        pass

    # Don't auto-populate — only use stored value, show placeholder if empty
    name_val     = rack.get('add_voice_name', None)
    if name_val is None:
        name_val = f"CH{add_ch+1}Sample"
        rack['add_voice_name'] = name_val

    name_field_h = max(14*scale, fs_sm+4*scale)
    name_field_y = ch_btn_y - name_field_h - 3*scale
    name_field_w = left_w - 10*scale

    # Background — brighter when active. Flat-fallback only; once skinned the
    # baked art reads clearly enough on its own (same call made for Piper's
    # script-panel focus glow — see rack_piper.py's panel_bg comment).
    _nf_bg  = (0.08,0.03,0.12,1.0) if _name_active else _BG
    _nf_col = _ACCENT if _name_active else _ACCENT_DIM
    if not _has_skin:
        _draw_rect(lx+5*scale, name_field_y, name_field_w, name_field_h, _nf_bg)
        nfvs = [(lx+5*scale, name_field_y), (lx+5*scale+name_field_w, name_field_y),
                (lx+5*scale+name_field_w, name_field_y+name_field_h),
                (lx+5*scale, name_field_y+name_field_h), (lx+5*scale, name_field_y)]
        nfb = batch_for_shader(sh,"LINE_STRIP",{"pos":nfvs})
        sh.bind(); sh.uniform_float("color",_nf_col); nfb.draw(sh)

    # Text display
    _name_text_x = lx + 8*scale + KNNVC_ADD_NAME_X_OFFSET*scale
    if name_val:
        disp_name = name_val[:16] if len(name_val) <= 16 else name_val[:15]+"..."
        _draw_text(disp_name, _name_text_x, name_field_y+name_field_h/2-fs_sm/2, fs_sm, _TEXT)
    else:
        _draw_text("enter name...", _name_text_x,
                   name_field_y+name_field_h/2-fs_sm/2, fs_sm, _TEXT_LABEL)

    # Blinking cursor when active
    if _name_active:
        _blink = int(_time.time() * 2) % 2 == 0
        if _blink:
            _pre_cur = name_val[:min(_name_cursor, len(name_val))]
            _cur_x   = _name_text_x + _text_width(_pre_cur, fs_sm)
            _draw_line(_cur_x, name_field_y + 2*scale,
                       _cur_x, name_field_y + name_field_h - 2*scale,
                       _TEXT, max(1.0, scale))

    # ADD button
    add_btn_h = max(16*scale, fs_sm+4*scale)
    add_btn_y = name_field_y - add_btn_h - 3*scale
    add_btn_w = left_w - 10*scale
    add_busy  = rack.get('add_voice_busy', False)
    add_col   = _AMBER if not add_busy else (0.60,0.35,0.00,1.0)
    add_lbl   = "ADDING..." if add_busy else "ADD TO VOICES >"
    # Button chrome suppressed once skinned — baked into the art; only the
    # state-dependent label (ADD TO VOICES/ADDING...) below still draws live.
    if not _has_skin:
        _draw_rect(lx+5*scale, add_btn_y, add_btn_w, add_btn_h, (0.06,0.03,0.00,1.0))
        abvs = [(lx+5*scale,add_btn_y),(lx+5*scale+add_btn_w,add_btn_y),
                (lx+5*scale+add_btn_w,add_btn_y+add_btn_h),
                (lx+5*scale,add_btn_y+add_btn_h),(lx+5*scale,add_btn_y)]
        abb = batch_for_shader(sh,"LINE_STRIP",{"pos":abvs})
        sh.bind(); sh.uniform_float("color",add_col); abb.draw(sh)
    fs_add = max(1,int(7*scale))
    tw_add = _text_width(add_lbl, fs_add)
    _draw_text(add_lbl, lx+5*scale+add_btn_w/2-tw_add/2,
               add_btn_y+add_btn_h/2-fs_add/2 + KNNVC_ADD_BTN_LABEL_Y_OFFSET*scale,
               fs_add, add_col)

    # ── VOICE CARDS (above add section) ────────────────────────────────────
    voices   = _discover_ref_voices(ai_idx)
    sel_idx  = int(getattr(rack, "p4", 0.0)) % max(1, len(voices)) if voices else 0
    card_h   = max(20*scale, work_h * 0.11)
    card_gap = 2*scale
    cards_top  = add_sep_y
    cards_bot  = ly + lh - fs_lbl - fs_ch*2 - 14*scale
    cards_h    = cards_bot - (add_sep_y + 2*scale)

    # Hint text above cards — suppressed once skinned, baked into the art.
    hint_y = cards_bot - fs_lbl - 2*scale
    if not _has_skin:
        _draw_text("REFERENCE VOICES", lx+5*scale, hint_y, fs_lbl, _TEXT_LABEL)

    prev_btn_w = max(16*scale, card_h*0.7)
    card_name_w = left_w - 8*scale - prev_btn_w - 4*scale
    max_vis  = max(1, int((hint_y - add_sep_y - 6*scale) / (card_h + card_gap)))

    # Scroll offset
    scroll_ofs = int(rack.get('rvc_scroll', 0))
    scroll_ofs = max(0, min(scroll_ofs, max(0, len(voices) - 1)))

    # ▲/▼ scroll arrows
    arr_sz = max(12*scale, fs_lbl + 4*scale)
    arr_x  = lx + left_w - arr_sz*2 - 6*scale
    arr_y  = hint_y - 1*scale
    can_up = scroll_ofs > 0
    can_dn = len(voices) > 0 and (scroll_ofs + max_vis) < len(voices)

    # ▲ up arrow
    up_col = _ACCENT if can_up else _ACCENT_DIM
    if not _has_skin:
        _draw_rect(arr_x, arr_y, arr_sz, arr_sz, _PANEL)
    _up_tri = [
        (arr_x + arr_sz*0.50, arr_y + arr_sz*0.72),
        (arr_x + arr_sz*0.20, arr_y + arr_sz*0.28),
        (arr_x + arr_sz*0.80, arr_y + arr_sz*0.28),
    ]
    _ub = batch_for_shader(sh, "TRI_FAN", {"pos": _up_tri})
    sh.bind(); sh.uniform_float("color", up_col); _ub.draw(sh)

    # ▼ down arrow
    dn_x   = arr_x + arr_sz + 2*scale
    dn_col = _ACCENT if can_dn else _ACCENT_DIM
    if not _has_skin:
        _draw_rect(dn_x, arr_y, arr_sz, arr_sz, _PANEL)
    _dn_tri = [
        (dn_x + arr_sz*0.50, arr_y + arr_sz*0.28),
        (dn_x + arr_sz*0.20, arr_y + arr_sz*0.72),
        (dn_x + arr_sz*0.80, arr_y + arr_sz*0.72),
    ]
    _db = batch_for_shader(sh, "TRI_FAN", {"pos": _dn_tri})
    sh.bind(); sh.uniform_float("color", dn_col); _db.draw(sh)

    # Stash arrow hit boxes and max_vis for hit test
    try:
        rack['rvc_arr_up']  = (arr_x, arr_y, arr_sz, arr_sz)
        rack['rvc_arr_dn']  = (dn_x,  arr_y, arr_sz, arr_sz)
        rack['rvc_max_vis'] = max_vis
    except Exception:
        pass

    if voices:
        for slot in range(max_vis):
            vi = slot + scroll_ofs
            if vi >= len(voices): break
            name, wav_path = voices[vi]
            cy_card = hint_y - fs_lbl - 4*scale - slot*(card_h+card_gap) - card_h
            if cy_card < add_sep_y + 2*scale: break
            issel = (vi == sel_idx)
            bg = _PANEL_SEL if issel else _PANEL
            bc = _ACCENT    if issel else _BORDER

            # Card background — fill+border suppressed once skinned (each
            # slot's box is baked into rack_knnvc_bg.png); a thin accent
            # border still draws over the selected slot as a highlight,
            # since which voice is selected/scrolled-to is dynamic and can't
            # be pre-baked into the art.
            cvs2 = [(lx+4*scale,cy_card),(lx+left_w-4*scale,cy_card),
                    (lx+left_w-4*scale,cy_card+card_h),
                    (lx+4*scale,cy_card+card_h),(lx+4*scale,cy_card)]
            if not _has_skin:
                _draw_rect(lx+4*scale, cy_card, left_w-8*scale, card_h, bg)
            if not _has_skin or issel:
                cb2 = batch_for_shader(sh,"LINE_STRIP",{"pos":cvs2})
                sh.bind(); sh.uniform_float("color",bc); cb2.draw(sh)

            # Voice name
            tc = _TEXT if issel else _TEXT_DIM
            max_chars = int(card_name_w / max(1, fs_sm*0.6))
            disp = name[:max_chars]
            _draw_text(disp, lx+8*scale, cy_card+card_h/2-fs_sm/2, fs_sm, tc)

            # ▶ / ■ preview button — toggles play/stop
            pb_x = lx + left_w - 4*scale - prev_btn_w
            pb_y = cy_card + card_h*0.1
            pb_h = card_h * 0.8
            _is_prev = False
            try:
                from core.ai_knnvc import is_voice_previewing as _ivp
                _is_prev = _ivp(ai_idx, vi)
            except Exception:
                pass
            _pb_bg  = (0.18,0.04,0.24,1.0) if _is_prev else (0.10,0.03,0.15,1.0)
            # Idle glyph used to sit on its own contrast box; now that the
            # box is suppressed when skinned, _ACCENT_DIM reads as invisible
            # against the art. Keep it bright whenever skinned so the ">" is
            # visible in both play and stop states; unskinned fallback keeps
            # the original dim/bright distinction since its box still gives contrast.
            _pb_col = _ACCENT if (_is_prev or _has_skin) else _ACCENT_DIM
            _pb_ico = "■" if _is_prev else ">"
            # Button chrome suppressed once skinned — baked into the art;
            # only the ▶/■ glyph (drawn below) still needs to swap live.
            if not _has_skin:
                _draw_rect(pb_x, pb_y, prev_btn_w, pb_h, _pb_bg)
                pbvs = [(pb_x,pb_y),(pb_x+prev_btn_w,pb_y),
                        (pb_x+prev_btn_w,pb_y+pb_h),(pb_x,pb_y+pb_h),(pb_x,pb_y)]
                pbb = batch_for_shader(sh,"LINE_STRIP",{"pos":pbvs})
                sh.bind(); sh.uniform_float("color",_pb_col); pbb.draw(sh)
            fs_pv = max(1,int(6*scale))
            tw_pv = _text_width(_pb_ico,fs_pv)
            _draw_text(_pb_ico, pb_x+prev_btn_w/2-tw_pv/2,
                       pb_y+pb_h/2-fs_pv/2, fs_pv, _pb_col)

        if len(voices) > max_vis:
            pg = f"{min(max_vis,len(voices))}/{len(voices)}"
            _draw_text(pg, lx+5*scale, add_sep_y+3*scale, max(1,int(6*scale)), _TEXT_LABEL)
    else:
        _draw_text("No voices found", lx+5*scale,
                   add_sep_y + cards_h*0.6, fs_sm, _TEXT_DIM)
        _draw_text("Use ADD FROM TIMELINE", lx+5*scale,
                   add_sep_y + cards_h*0.4, max(1,int(6*scale)), _TEXT_LABEL)
        _draw_text("or drop WAVs into voices/", lx+5*scale,
                   add_sep_y + cards_h*0.25, max(1,int(6*scale)), _TEXT_LABEL)

    # ── CENTRE PANEL: state display + convert/preview + output ch ─────────────
    cx = centre_x; cy2 = work_bot; ch2 = work_h
    if not _has_skin:
        _draw_rect(cx, cy2, centre_w, ch2, _PANEL)
        cvs3 = [(cx,cy2),(cx+centre_w,cy2),(cx+centre_w,cy2+ch2),(cx,cy2+ch2),(cx,cy2)]
        cb3 = batch_for_shader(sh,"LINE_STRIP",{"pos":cvs3})
        sh.bind(); sh.uniform_float("color",_BORDER); cb3.draw(sh)
        _draw_text("CONVERSION", cx+5*scale, cy2+ch2-fs_lbl-4*scale, fs_lbl, _TEXT_LABEL)

    # State display area (replaces the empty waveform box). Fill suppressed
    # once skinned; the status-coloured border below still draws every time
    # since it's a live state indicator, not static chrome.
    state_h = ch2 * 0.44
    state_y = cy2 + ch2 - fs_lbl - 10*scale - state_h
    if not _has_skin:
        _draw_rect(cx+4*scale, state_y, centre_w-8*scale, state_h, _BG)
    stvs = [(cx+4*scale,state_y),(cx+centre_w-4*scale,state_y),
            (cx+centre_w-4*scale,state_y+state_h),
            (cx+4*scale,state_y+state_h),(cx+4*scale,state_y)]
    stb = batch_for_shader(sh,"LINE_STRIP",{"pos":stvs})

    if status == _PROCESSING:
        sh.bind(); sh.uniform_float("color",(0.30,0.10,0.40,1.0)); stb.draw(sh)
        fs_st = max(1,int(8*scale))
        lbl_p = "CONVERTING..."
        tw_p  = _text_width(lbl_p, fs_st)
        _draw_text(lbl_p, cx+centre_w/2-tw_p/2,
                   state_y+state_h*0.6-fs_st/2, fs_st, _ACCENT)
        _draw_text("This may take 30-60 seconds",
                   cx+centre_w/2-_text_width("This may take 30-60 seconds",max(1,int(6*scale)))/2,
                   state_y+state_h*0.35, max(1,int(6*scale)), _TEXT_DIM)

    elif status == _DONE:
        sh.bind(); sh.uniform_float("color",(0.02,0.08,0.04,1.0)); stb.draw(sh)
        # Show output info
        import glob, tempfile
        tmp_dir = tempfile.gettempdir()
        matches = sorted(glob.glob(os.path.join(tmp_dir, f"pb_knnvc_{ai_idx}_*.wav")))
        fs_st = max(1,int(8*scale))
        _draw_text("CONVERSION DONE", cx+8*scale,
                   state_y+state_h*0.78-fs_st/2, fs_st, _GREEN)
        fs_info = max(1,int(7*scale))
        if matches:
            wav = matches[-1]
            try:
                sz  = os.path.getsize(wav)
                sz_str = f"{sz//1024}KB" if sz < 1024*1024 else f"{sz//(1024*1024)}MB"
                import wave
                with wave.open(wav,'r') as wf:
                    dur = wf.getnframes()/wf.getframerate()
                    sr  = wf.getframerate()
                _draw_text(f"Duration: {dur:.1f}s", cx+8*scale,
                           state_y+state_h*0.55-fs_info/2, fs_info, _TEXT_DIM)
                _draw_text(f"Sample rate: {sr}Hz", cx+8*scale,
                           state_y+state_h*0.38-fs_info/2, fs_info, _TEXT_DIM)
                _draw_text(f"File size: {sz_str}", cx+8*scale,
                           state_y+state_h*0.21-fs_info/2, fs_info, _TEXT_DIM)
            except Exception:
                _draw_text("Output placed in VSE", cx+8*scale,
                           state_y+state_h*0.45-fs_info/2, fs_info, _TEXT_DIM)
        else:
            _draw_text("Output placed in VSE", cx+8*scale,
                       state_y+state_h*0.45-fs_info/2, fs_info, _TEXT_DIM)

    elif status == _ERROR:
        sh.bind(); sh.uniform_float("color",(0.08,0.01,0.01,1.0)); stb.draw(sh)
        fs_st = max(1,int(8*scale))
        _draw_text("ERROR — check console", cx+8*scale,
                   state_y+state_h*0.6-fs_st/2, fs_st, (0.90,0.20,0.10,1.0))
        _draw_text("Assign source channel in rail",
                   cx+8*scale, state_y+state_h*0.38,
                   max(1,int(6*scale)), _TEXT_DIM)
        _draw_text("and select a reference voice",
                   cx+8*scale, state_y+state_h*0.22,
                   max(1,int(6*scale)), _TEXT_DIM)

    else:
        # READY / NO_REF
        sh.bind(); sh.uniform_float("color",_BORDER); stb.draw(sh)
        fs_st = max(1,int(8*scale))
        ref_sel = voices[sel_idx][0] if voices else None
        src_rdy = bool(active_chs)

        if not src_rdy:
            _draw_text("Assign source channel", cx+8*scale,
                       state_y+state_h*0.65, fs_st, _TEXT_DIM)
            _draw_text("in the rack rail above", cx+8*scale,
                       state_y+state_h*0.45, max(1,int(7*scale)), _TEXT_LABEL)
        elif not voices:
            _draw_text("Add a reference voice", cx+8*scale,
                       state_y+state_h*0.65, fs_st, _TEXT_DIM)
            _draw_text("using ADD FROM TIMELINE", cx+8*scale,
                       state_y+state_h*0.45, max(1,int(7*scale)), _TEXT_LABEL)
        else:
            _draw_text("Ready to convert", cx+8*scale,
                       state_y+state_h*0.65, fs_st, _ACCENT)
            ref_disp = ref_sel[:20] if ref_sel else "none"
            _draw_text(f"Ref: {ref_disp}", cx+8*scale,
                       state_y+state_h*0.45, max(1,int(7*scale)), _TEXT_DIM)
            src_disp2 = f"Src: CH{active_chs[0]}"
            _draw_text(src_disp2, cx+8*scale,
                       state_y+state_h*0.26, max(1,int(7*scale)), _TEXT_DIM)

    # CONVERT + PREVIEW buttons
    btn_h2  = max(22*scale, ch2*0.11)
    btn_y2  = state_y - 2*scale - btn_h2
    conv_w  = centre_w * 0.52
    prev_w  = centre_w * 0.38
    conv_x  = cx + 4*scale
    prev_x  = conv_x + conv_w + 4*scale

    # Label reflects state: GENERATING during convert, GENERATE when ready,
    # or PLACE (if preview output exists and just needs placing on timeline)
    _has_out_gen = False
    try:
        from core.ai_knnvc import has_rack_output as _hro
        _has_out_gen = _hro(ai_idx)
    except Exception:
        pass
    if status == _PROCESSING:
        c_lbl = "GENERATING..."
    elif _has_out_gen:
        c_lbl = "PLACE ON TRACK"
    else:
        c_lbl = "GENERATE"
    c_bg  = (0.08,0.02,0.12,1.0) if status == _PROCESSING else (0.10,0.03,0.15,1.0)
    # Button chrome suppressed once skinned — baked into the art; only the
    # state-dependent label (GENERATE/GENERATING.../PLACE ON TRACK) below
    # still needs to draw live.
    if not _has_skin:
        _draw_rect(conv_x, btn_y2, conv_w, btn_h2, c_bg)
        evs = [(conv_x,btn_y2),(conv_x+conv_w,btn_y2),(conv_x+conv_w,btn_y2+btn_h2),
               (conv_x,btn_y2+btn_h2),(conv_x,btn_y2)]
        eb = batch_for_shader(sh,"LINE_STRIP",{"pos":evs})
        sh.bind(); sh.uniform_float("color",_ACCENT); eb.draw(sh)
    fs_btn = max(1,int(8*scale))
    tw_c2  = _text_width(c_lbl, fs_btn)
    _draw_text(c_lbl, conv_x+conv_w/2-tw_c2/2,
               btn_y2+btn_h2/2-fs_btn/2, fs_btn, _ACCENT)

    # Preview button — shows ■ STOP when playing, > PREVIEW when not
    # Only active when this rack has a fresh conversion output
    _has_out   = False
    _is_playing = False
    try:
        from core.ai_knnvc import has_rack_output, is_rack_previewing
        _has_out    = has_rack_output(ai_idx)
        _is_playing = is_rack_previewing(ai_idx)
    except Exception:
        pass
    _prev_bg  = (0.18,0.04,0.24,1.0) if _is_playing else (0.04,0.02,0.06,1.0)
    prev_col  = _ACCENT if (_has_out or _is_playing) else _ACCENT_DIM
    pv_lbl    = "■ STOP" if _is_playing else "> PREVIEW"
    # Button chrome suppressed once skinned — baked into the art; only the
    # state-dependent label (PREVIEW/STOP) below still needs to draw live.
    if not _has_skin:
        _draw_rect(prev_x, btn_y2, prev_w, btn_h2, _prev_bg)
        pvs2 = [(prev_x,btn_y2),(prev_x+prev_w,btn_y2),(prev_x+prev_w,btn_y2+btn_h2),
                (prev_x,btn_y2+btn_h2),(prev_x,btn_y2)]
        pb2 = batch_for_shader(sh,"LINE_STRIP",{"pos":pvs2})
        sh.bind(); sh.uniform_float("color",prev_col); pb2.draw(sh)
    tw_pv  = _text_width(pv_lbl, fs_btn)
    _draw_text(pv_lbl, prev_x+prev_w/2-tw_pv/2,
               btn_y2+btn_h2/2-fs_btn/2, fs_btn, prev_col)

    # Output channel with < > arrows — stored in p3
    row2_y = btn_y2 - 2*scale - max(16*scale, ch2*0.08)
    row2_h = max(16*scale, ch2*0.08)
    out_ch_val = int(getattr(rack, "p3", 0.0)) or (active_chs[0]+1 if active_chs else 2)
    out_ch_val = max(1, min(9, out_ch_val))

    if not _has_skin:
        _draw_text("OUT CH", cx+5*scale, row2_y+row2_h/2-fs_lbl/2, fs_lbl, _TEXT_LABEL)
    lbl_tw = _text_width("OUT CH ", fs_lbl)
    arr_w  = max(14*scale, row2_h)
    oc_s   = max(22*scale, row2_h)
    oc_x   = cx + 5*scale + lbl_tw + arr_w + 2*scale

    if not _has_skin:
        _draw_rect(cx+5*scale+lbl_tw, row2_y, arr_w, row2_h, _PANEL)
    _ax = cx+5*scale+lbl_tw
    # Left arrow — suppressed once skinned (baked into the art either side
    # of the output-channel box); the box's hit test is unaffected, this
    # only turns off the drawn triangle.
    if not _has_skin:
        mv_l = [(_ax+arr_w*0.65, row2_y+row2_h*0.18),
                (_ax+arr_w*0.28, row2_y+row2_h*0.50),
                (_ax+arr_w*0.65, row2_y+row2_h*0.82)]
        al = batch_for_shader(sh,"TRI_FAN",{"pos":mv_l})
        sh.bind(); sh.uniform_float("color",_ACCENT); al.draw(sh)

    if not _has_skin:
        _draw_rect(oc_x, row2_y, oc_s, row2_h, _PANEL)
        ocvs = [(oc_x,row2_y),(oc_x+oc_s,row2_y),(oc_x+oc_s,row2_y+row2_h),
                (oc_x,row2_y+row2_h),(oc_x,row2_y)]
        ocb = batch_for_shader(sh,"LINE_STRIP",{"pos":ocvs})
        sh.bind(); sh.uniform_float("color",_BORDER); ocb.draw(sh)
    oc_str = str(out_ch_val)
    tw_oc  = _text_width(oc_str, fs_lbl)
    _draw_text(oc_str, oc_x+oc_s/2-tw_oc/2, row2_y+row2_h/2-fs_lbl/2, fs_lbl, _ACCENT)

    plus_x = oc_x + oc_s + 2*scale
    if not _has_skin:
        _draw_rect(plus_x, row2_y, arr_w, row2_h, _PANEL)
    # Right arrow — suppressed once skinned, same reasoning as the left
    # arrow above.
    if not _has_skin:
        mv_r = [(plus_x+arr_w*0.35, row2_y+row2_h*0.18),
                (plus_x+arr_w*0.72, row2_y+row2_h*0.50),
                (plus_x+arr_w*0.35, row2_y+row2_h*0.82)]
        ar = batch_for_shader(sh,"TRI_FAN",{"pos":mv_r})
        sh.bind(); sh.uniform_float("color",_ACCENT); ar.draw(sh)

    # ── RIGHT PANEL: knobs ────────────────────────────────────────────────────
    rx2 = right_x; ry2 = work_bot; rh2 = work_h
    if not _has_skin:
        _draw_rect(rx2, ry2, right_w, rh2, _PANEL)
        rvs = [(rx2,ry2),(rx2+right_w,ry2),(rx2+right_w,ry2+rh2),(rx2,ry2+rh2),(rx2,ry2)]
        rb = batch_for_shader(sh,"LINE_STRIP",{"pos":rvs})
        sh.bind(); sh.uniform_float("color",_BORDER); rb.draw(sh)
        _draw_text("CONTROLS", rx2+5*scale, ry2+rh2-fs_lbl-4*scale, fs_lbl, _TEXT_LABEL)

    knob_r_base = min(18*scale, right_w*0.28, work_h*0.18)
    ky0         = ry2 + rh2 * 0.72

    # Knob names ("TOPK"/"REF SECS") suppressed once skinned — same treatment
    # as Piper's SPEED/NOISE/NOISE W knob labels; the live value string
    # (topk_val / ref_secs) always keeps drawing since it's dynamic. Knob
    # radius is scaled per-knob via KNNVC_TOPK_KNOB_SCALE / _REFSECS_ above;
    # centre position is nudged per-knob via the *_X_OFFSET/_Y_OFFSET
    # constants so each can be matched to its background art independently.
    norm0    = float(getattr(rack,"p0",0.5))
    topk_val = int(2 + norm0*6)
    _draw_knob(rx2+right_w*0.30 + KNNVC_TOPK_KNOB_X_OFFSET*scale,
               ky0 + KNNVC_TOPK_KNOB_Y_OFFSET*scale,
               knob_r_base * KNNVC_TOPK_KNOB_SCALE, norm0,
               (_ACCENT[0],_ACCENT[1],_ACCENT[2]),
               "" if _has_skin else "TOPK", str(topk_val), scale)

    norm1    = float(getattr(rack,"p1",0.5))
    ref_secs = int(10 + norm1*50)
    _draw_knob(rx2+right_w*0.72 + KNNVC_REFSECS_KNOB_X_OFFSET*scale,
               ky0 + KNNVC_REFSECS_KNOB_Y_OFFSET*scale,
               knob_r_base * KNNVC_REFSECS_KNOB_SCALE, norm1,
               (_ACCENT[0],_ACCENT[1],_ACCENT[2]),
               "" if _has_skin else "REF SECS", str(ref_secs)+"s", scale)

    # Help-text column — kept even when skinned. Unlike the section headers
    # above, this is six lines of tooltip-style copy that's unlikely to be
    # baked into a control-panel graphic — flag to confirm once the PNG
    # exists; easy to wrap in `if not _has_skin:` if it turns out to be baked.
    fs_n   = max(1,int(6*scale))
    note_y = ry2 + rh2*0.30
    for note in ["TOPK: neighbours","(2=sharp,8=smooth)","","REF SECS: max ref","clip length","(30s recommended)"]:
        _draw_text(note, rx2+6*scale, note_y, fs_n, _TEXT_LABEL)
        note_y -= (fs_n+2*scale)

    # ── STATUS BAR ───────────────────────────────────────────────────────────
    if not _has_skin:
        _draw_rect(sbar_x, sbar_y, sbar_w, sbar_h, (0.03,0.01,0.04,1.0))
        svs = [(sbar_x,sbar_y),(sbar_x+sbar_w,sbar_y),(sbar_x+sbar_w,sbar_y+sbar_h),
               (sbar_x,sbar_y+sbar_h),(sbar_x,sbar_y)]
        sb2 = batch_for_shader(sh,"LINE_STRIP",{"pos":svs})
        sh.bind(); sh.uniform_float("color",_BORDER); sb2.draw(sh)

    voices_now  = _discover_ref_voices(ai_idx)
    ref_name    = "none"
    if voices_now:
        si = int(getattr(rack,"p4",0.0)) % max(1,len(voices_now))
        ref_name = os.path.splitext(os.path.basename(voices_now[si][1]))[0] if si < len(voices_now) else "none"
    topk_disp   = int(2 + float(getattr(rack,"p0",0.5))*6)
    src_str     = f"SRC:CH{active_chs[0]}" if active_chs else "SRC:unset"
    out_ch_sb   = int(getattr(rack,"p3",0.0)) or (active_chs[0]+1 if active_chs else 2)

    fs_sb = max(1,int(7*scale))
    _draw_text(
        f"ENGINE: kNN-VC  |  {src_str}  |  OUT:CH{out_ch_sb}  |  REF:{ref_name}  |  TOPK:{topk_disp}  |  OFFLINE",
        sbar_x+7*scale, sbar_y+sbar_h/2-fs_sb/2, fs_sb, _TEXT_DIM)

    # Status LED — suppressed once skinned; baked into the background art in
    # the bottom-right corner of the rack, same as the other static chrome.
    if not _has_skin:
        dot_cols = {
            _READY:      _AMBER,
            _NO_REF:     _AMBER,
            _PROCESSING: (0.90,0.50,0.10,1.0),
            _DONE:       _GREEN,
            _ERROR:      (0.90,0.10,0.05,1.0),
        }
        _draw_circle(sbar_x+sbar_w-9*scale, sbar_y+sbar_h/2,
                     3.5*scale, dot_cols.get(status,_AMBER))