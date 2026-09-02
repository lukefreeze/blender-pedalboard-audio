# =============================================================================
# ui/racks/rack_voicefixer.py
# VoiceFixer speech restoration rack (system Python / PyTorch, BETA).
#
# Uses VoiceFixer neural vocoder to restore degraded speech:
# noise, reverb, low resolution and clipping handled in one pass.
# Modes: 0=Standard, 1=Smooth, 2=Aggressive
# =============================================================================

import os
import gpu
from gpu_extras.batch import batch_for_shader

try:
    from ui.mixer.draw_utils import (
        draw_rect   as _draw_rect,
        draw_line   as _draw_line,
        draw_circle as _draw_circle,
        draw_text   as _draw_text,
        text_width  as _text_width,
    )
except ImportError:
    pass

_BG          = (0.01, 0.03, 0.05, 1.0)
_BORDER      = (0.06, 0.16, 0.24, 1.0)
_PANEL       = (0.01, 0.02, 0.04, 1.0)
_PANEL_SEL   = (0.04, 0.10, 0.18, 1.0)
_ACCENT      = (0.10, 0.60, 0.90, 1.0)
_ACCENT_DIM  = (0.05, 0.25, 0.40, 1.0)
_TEXT        = (0.55, 0.80, 0.95, 1.0)
_TEXT_DIM    = (0.16, 0.36, 0.50, 1.0)
_TEXT_LABEL  = (0.06, 0.16, 0.24, 1.0)
_GREEN       = (0.05, 0.80, 0.30, 1.0)
_AMBER       = (0.86, 0.53, 0.00, 1.0)
_WARN_BG     = (0.08, 0.05, 0.00, 1.0)
_WARN_BORDER = (0.55, 0.33, 0.00, 1.0)
_WARN_TEXT   = (0.86, 0.53, 0.00, 1.0)
_WARN_DIM    = (0.50, 0.28, 0.00, 1.0)

RACK_RAIL_H = 32
_READY      = "READY"
_PROCESSING = "PROCESSING"
_DONE       = "DONE"
_ERROR      = "ERROR"

_MODE_LABELS = ["STANDARD", "SMOOTH", "AGGRESSIVE"]
_MODE_DESCS  = [
    "Best for most recordings",
    "Softer — may alter voice slightly",
    "Maximum restoration power",
]

# =============================================================================
# GLOBAL FONT SCALE
# Multiplies every font size drawn in the main rack body (not the setup
# warning / "checking..." screens). 1.0 = the original sizes below; bump it
# up to grow everything at once instead of retuning each fs_* individually.
# =============================================================================
VF_FONT_SCALE = 1.35


def _vf_fs(base_px, scale):
    """Font size helper — base_px * scale * VF_FONT_SCALE. Rack-wide font
    size knob: change VF_FONT_SCALE above instead of editing every call site."""
    return max(1, int(base_px * scale * VF_FONT_SCALE))


# =============================================================================
# LEFT PANEL TEXT NUDGE — SOURCE value
# Unscaled px, + = right/up. Moves the "CH X" / "USE RAIL" readout under the
# SOURCE label. Same convention as KNNVC_SOURCE_VALUE_X_OFFSET in
# rack_knnvc.py.
# =============================================================================
VF_SOURCE_VALUE_X_OFFSET = 15.0
VF_SOURCE_VALUE_Y_OFFSET = -5.0

# =============================================================================
# RESTORATION MODE SECTION TUNING
# Exposes position and size of the 3-stacked mode-selector buttons
# independently of the left panel's auto-computed proportions, so they can
# be lined up with larger/repositioned slot art.
#   VF_MODE_X_OFFSET / VF_MODE_Y_OFFSET : unscaled px, + = right/up — nudges
#       the whole 3-button block (position, label, and per-button text all
#       move together).
#   VF_MODE_W_SCALE / VF_MODE_H_SCALE : multiplier on the auto-computed
#       button width/height (1.0 = old behaviour — width fills the left
#       panel, height = mode_sec_h/3 - 3 capped at 28). NOT unscaled px —
#       these multiply the existing size, so 1.5 = 50% bigger, 0.5 = half.
#       (Previously these were raw unscaled-px overrides, which is why a
#       value like 0.9 collapsed the buttons to under 1px — fixed here.)
#   VF_MODE_GAP : unscaled px vertical gap between adjacent buttons (was
#       hardcoded to 3.0). Set to 0 to butt them together, or negative to
#       overlap slightly.
# =============================================================================
VF_MODE_X_OFFSET = 11.0
VF_MODE_Y_OFFSET = -30.0
VF_MODE_W_SCALE  = 0.95
VF_MODE_H_SCALE  = 1.4
VF_MODE_GAP      = 5.0

# =============================================================================
# RIGHT PANEL "ABOUT" NOTES NUDGE
# Unscaled px, + = right/up. Moves the whole help-text column (VoiceFixer /
# Neural vocoder / etc.) as one block — spacing between lines is unchanged.
# =============================================================================
VF_ABOUT_NOTES_X_OFFSET = 0.0
VF_ABOUT_NOTES_Y_OFFSET = 0.0

# =============================================================================
# STATUS BAR TEXT NUDGE
# Unscaled px, + = right/up. Moves the "ENGINE: ... | SRC:... | OUT:... |
# MODE:... | OFFLINE" line at the bottom of the rack (not the ABOUT notes
# column — that's VF_ABOUT_NOTES_*  above).
# =============================================================================
VF_STATUSBAR_TEXT_X_OFFSET = 5.0
VF_STATUSBAR_TEXT_Y_OFFSET = 3.0

# =============================================================================
# ENHANCE / PREVIEW BUTTON LABEL NUDGE
# Unscaled px, + = up. Moves the state-dependent button text (ENHANCE /
# RESTORING... / > PREVIEW / ■ STOP) — both buttons move together so they
# stay aligned with each other.
# =============================================================================
VF_BTN_LABEL_Y_OFFSET = 5.0

# =============================================================================
# OUTPUT CHANNEL VALUE NUDGE
# Unscaled px, + = right/up. Moves the digit drawn inside the small OUT CH
# box (not the "OUT CH" label, not the box itself — just the number glyph).
# =============================================================================
VF_OUTCH_VALUE_X_OFFSET = -10.0
VF_OUTCH_VALUE_Y_OFFSET = 3.0

# =============================================================================
# CENTRE PANEL "RESTORATION" STATE TEXT NUDGE
# Unscaled px, + = right/up. Moves the dynamic status text inside the centre
# state box (RESTORING.../RESTORATION DONE/ERROR/Ready to restore and all
# their sub-lines) as one block — spacing between lines is unchanged.
# =============================================================================
VF_STATE_TEXT_X_OFFSET = 10.0
VF_STATE_TEXT_Y_OFFSET = 0.0

# Dep check — background thread, never on draw thread
_vf_dep_cache      = {"ok": False, "checked": False, "checking": False}
_vf_check_running  = False


def _get_python_candidates():
    """Return ordered list of Python commands to try.
    Includes common install locations so Blender finds system Python
    even when it doesn't inherit the user's PATH on Windows/Mac/Linux.
    """
    import platform
    candidates = []
    if platform.system() == "Windows":
        # Common Windows install locations
        import os
        for ver in ["312", "311", "310", "39"]:
            for base in [
                os.path.expanduser(f"~\\AppData\\Local\\Programs\\Python\\Python{ver}\\python.exe"),
                f"C:\\Python{ver}\\python.exe",
                f"C:\\Program Files\\Python{ver}\\python.exe",
            ]:
                if os.path.exists(base):
                    candidates.append([base])
        # Also try py launcher and plain python
        candidates += [["py", f"-3.{ver[-2:]}"] for ver in ["312","311","310"]]
        candidates += [["python"], ["python3"]]
    else:
        # Mac / Linux — common locations
        import os
        for path in [
            "/usr/local/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/bin/python3",
        ]:
            if os.path.exists(path):
                candidates.append([path])
        candidates += [["python3"], ["python"]]
    return candidates


def _run_dep_check():
    global _vf_check_running
    found = False
    try:
        from core.ai_python_finder import find_python_with as _fpw
        # Use pip show instead of importing voicefixer directly — importing
        # pulls in torch which takes 2-3s just for the import check.
        import subprocess, platform
        from core.ai_python_finder import (
            _win_python_paths, _mac_python_paths, _linux_python_paths)
        sys = platform.system()
        candidates = (_win_python_paths() if sys == "Windows"
                      else _mac_python_paths() if sys == "Darwin"
                      else _linux_python_paths())
        for cmd in candidates:
            try:
                r = subprocess.run(
                    cmd + ["-m", "pip", "show", "voicefixer"],
                    capture_output=True, timeout=5)
                if r.returncode == 0:
                    found = True
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"[VOICEFIXER] dep check error: {e}")
    _vf_dep_cache["ok"]       = found
    _vf_dep_cache["checked"]  = True
    _vf_dep_cache["checking"] = False
    _vf_check_running         = False
    print(f"[VOICEFIXER] dep check complete: {'found' if found else 'not found'}")
    try:
        import bpy
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ('NODE_EDITOR', 'SEQUENCE_EDITOR'):
                    area.tag_redraw()
    except Exception:
        pass


def _check_voicefixer():
    global _vf_check_running
    if not _vf_dep_cache["checked"] and not _vf_check_running:
        _vf_check_running         = True
        _vf_dep_cache["checking"] = True
        import threading
        threading.Thread(target=_run_dep_check, daemon=True).start()
    return _vf_dep_cache["ok"]


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
    # behind it (same convention as rack_knnvc.py's has_skin gating).
    if not has_skin:
        _draw_rect(rx, body_bot, rw, body_h, _BG)
        bvs = [(rx,body_bot),(rx+rw,body_bot),(rx+rw,body_top),(rx,body_top),(rx,body_bot)]
        bb = batch_for_shader(sh,"LINE_STRIP",{"pos":bvs})
        sh.bind(); sh.uniform_float("color",_BORDER); bb.draw(sh)

    warn_w = rw - margin*4
    warn_h = min(body_h*0.78, 160*scale)
    warn_x = rx + (rw-warn_w)/2
    warn_y = body_bot + (body_h-warn_h)/2

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

    _draw_text("VOICEFIXER NOT FOUND — BETA RACK REQUIRES SETUP", tx, ty, fs_h, _WARN_TEXT)
    ty -= lh*1.4
    _draw_text("Speech restoration needs VoiceFixer in your system Python.", tx, ty, fs_b, _WARN_DIM)
    ty -= lh
    _draw_text("Open a terminal and run:", tx, ty, fs_b, _WARN_DIM)
    ty -= lh*1.2

    cmd_w = warn_w - 24*scale
    cmd_h = lh*1.6 + 6*scale
    cmd_x = tx; cmd_y = ty - cmd_h
    _draw_rect(cmd_x, cmd_y, cmd_w, cmd_h, (0.01,0.01,0.02,1.0))
    cvs = [(cmd_x,cmd_y),(cmd_x+cmd_w,cmd_y),(cmd_x+cmd_w,cmd_y+cmd_h),(cmd_x,cmd_y+cmd_h),(cmd_x,cmd_y)]
    cb = batch_for_shader(sh,"LINE_STRIP",{"pos":cvs})
    sh.bind(); sh.uniform_float("color",(0.18,0.32,0.44,1.0)); cb.draw(sh)
    _draw_text("pip install voicefixer",
               cmd_x+6*scale, cmd_y+cmd_h/2-fs_b/2, fs_b, _WARN_TEXT)

    ty = cmd_y - lh*1.2
    _draw_text("Models (~625MB) auto-download on first use. Restart Blender after install.", tx, ty, fs_s, _WARN_DIM)
    ty -= lh
    _draw_text("Works on Windows, macOS (CPU/MPS) and Linux. CUDA recommended.", tx, ty, fs_s, _WARN_DIM)

    btn_w = min(140*scale, warn_w*0.38)
    btn_h = max(16*scale, fs_s+8*scale)
    btn_x = warn_x + warn_w - btn_w - 12*scale
    btn_y = warn_y + 8*scale
    _draw_rect(btn_x, btn_y, btn_w, btn_h, (0.04,0.08,0.12,1.0))
    bvs2 = [(btn_x,btn_y),(btn_x+btn_w,btn_y),(btn_x+btn_w,btn_y+btn_h),(btn_x,btn_y+btn_h),(btn_x,btn_y)]
    bb2 = batch_for_shader(sh,"LINE_STRIP",{"pos":bvs2})
    sh.bind(); sh.uniform_float("color",_WARN_BORDER); bb2.draw(sh)
    fs_btn = max(1,int(7*scale))
    lbl = "OPEN SETUP GUIDE  >"
    tw_btn = _text_width(lbl, fs_btn)
    _draw_text(lbl, btn_x+btn_w/2-tw_btn/2, btn_y+btn_h/2-fs_btn/2, fs_btn, _WARN_TEXT)
    return btn_x, btn_y, btn_w, btn_h


def _draw_voicefixer_body(rx, ry, rw, rh, rack, ai_idx, scale):
    sh = gpu.shader.from_builtin("UNIFORM_COLOR")

    # Full-rack photoreal skin — when present, Racks.py's _draw_ai_rack_expanded
    # has already blit the whole unit (rail + body) before calling this
    # function, so the flat panel fills/borders below are skipped entirely and
    # only dynamic content (state text, selection highlights, live values)
    # draws on top at the same coordinates. Falls back to the old flat panel
    # look if the PNG isn't found yet — same convention as rack_knnvc.py /
    # rack_piper.py's _has_skin gating.
    # (Replaces the old body-only draw_element() skin attempt, which predates
    # the full-unit blit Racks.py now does before dispatching here.)
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_vf
        _has_skin = _gtc_vf("rack_voicefixer_bg") is not None
    except Exception:
        _has_skin = False

    # Deps not ready yet — Racks.py's _draw_ai_rack_expanded withholds the
    # full-unit skin blit in this state (mirrors the RVC rack's dep-check
    # gating), so even though the skin texture may already be loaded in
    # cache, nothing has actually been drawn behind us. Force has_skin=False
    # here so the warning/checking screens draw their own flat black body
    # chrome instead of assuming art is already sitting underneath them.
    if not _check_voicefixer():
        if _vf_dep_cache["checking"]:
            # Background check still running — show brief status, not full warning
            rail_h   = RACK_RAIL_H * scale
            body_bot = ry
            body_top = ry + rh - rail_h
            body_h   = body_top - body_bot
            _draw_rect(rx, body_bot, rw, body_h, _BG)
            fs = max(1, int(8 * scale))
            msg = "Checking VoiceFixer installation..."
            tw  = _text_width(msg, fs)
            _draw_text(msg, rx + rw/2 - tw/2, body_bot + body_h/2 - fs/2,
                       fs, _TEXT_DIM)
            return
        bx, by, bw, bh = _draw_setup_warning(rx, ry, rw, rh, scale, has_skin=False)
        try: rack['vf_setup_btn'] = (bx, by, bw, bh)
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
    work_bot = sbar_y + sbar_h + 2*scale
    work_top = body_top - 2*scale
    work_h   = work_top - work_bot
    fs_lbl   = _vf_fs(7, scale)
    status   = getattr(rack, "ai_status", _READY)
    mode_idx = int(getattr(rack, "p0", 0.0))
    mode_idx = max(0, min(2, mode_idx))

    # ── LEFT PANEL: source info + mode selector ───────────────────────────────
    left_w  = rw * 0.30
    lx = rx + margin
    ly = work_bot
    lh = work_h
    if not _has_skin:
        _draw_rect(lx, ly, left_w, lh, _PANEL)
        lvs = [(lx,ly),(lx+left_w,ly),(lx+left_w,ly+lh),(lx,ly+lh),(lx,ly)]
        lb = batch_for_shader(sh,"LINE_STRIP",{"pos":lvs})
        sh.bind(); sh.uniform_float("color",_BORDER); lb.draw(sh)

    # "SOURCE" label suppressed once skinned — expected to be baked into
    # rack_voicefixer_bg.png like Piper's "SCRIPT"/"VOICE" labels were.
    if not _has_skin:
        _draw_text("SOURCE", lx+5*scale, ly+lh-fs_lbl-4*scale, fs_lbl, _TEXT_LABEL)
    active_chs = [ci+1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
    src_disp   = f"CH {active_chs[0]}" if active_chs else "USE RAIL"
    _draw_text(src_disp, lx+5*scale + VF_SOURCE_VALUE_X_OFFSET*scale,
               ly+lh-fs_lbl*2-12*scale + VF_SOURCE_VALUE_Y_OFFSET*scale,
               _vf_fs(7, scale), _ACCENT if active_chs else _TEXT_DIM)

    # Mode selector — three stacked buttons. Position via VF_MODE_X_OFFSET/
    # _Y_OFFSET; size via VF_MODE_W_SCALE/_H_SCALE (multipliers, not px —
    # see the constants block above).
    mode_sec_h = lh * 0.62
    mode_y     = ly + lh * 0.04
    mode_x_off = VF_MODE_X_OFFSET * scale
    mode_sep_y = ly + lh - fs_lbl*2 - 18*scale + VF_MODE_Y_OFFSET*scale
    if not _has_skin:
        _draw_text("RESTORATION MODE", lx+5*scale+mode_x_off, mode_sep_y+fs_lbl+3*scale, fs_lbl, _TEXT_LABEL)

    btn_h_m = min(mode_sec_h/3 - 3*scale, 28*scale) * VF_MODE_H_SCALE
    btn_w_m = (left_w - 8*scale) * VF_MODE_W_SCALE
    btn_x_m = lx + 4*scale + mode_x_off
    for mi, (lbl_m, desc_m) in enumerate(zip(_MODE_LABELS, _MODE_DESCS)):
        btn_y_m = mode_sep_y - (mi+1)*(btn_h_m+VF_MODE_GAP*scale)
        if btn_y_m < ly + 2*scale:
            break
        issel = (mi == mode_idx)
        bg    = _PANEL_SEL if issel else _PANEL
        bc    = _ACCENT    if issel else _BORDER
        # Per-slot button chrome suppressed once skinned (each mode slot's
        # box is baked into rack_voicefixer_bg.png); a thin accent border
        # still draws over the selected slot as a highlight, since which
        # mode is selected is dynamic and can't be pre-baked into the art.
        if not _has_skin:
            _draw_rect(btn_x_m, btn_y_m, btn_w_m, btn_h_m, bg)
        if not _has_skin or issel:
            bvs2 = [(btn_x_m,btn_y_m),(btn_x_m+btn_w_m,btn_y_m),
                    (btn_x_m+btn_w_m,btn_y_m+btn_h_m),
                    (btn_x_m,btn_y_m+btn_h_m),(btn_x_m,btn_y_m)]
            mb = batch_for_shader(sh,"LINE_STRIP",{"pos":bvs2})
            sh.bind(); sh.uniform_float("color",bc); mb.draw(sh)
        fs_m  = _vf_fs(7, scale)
        fs_d  = _vf_fs(6, scale)
        tc    = _TEXT if issel else _TEXT_DIM
        _draw_text(lbl_m, btn_x_m+4*scale, btn_y_m+btn_h_m*0.62-fs_m/2, fs_m, tc)
        _draw_text(desc_m, btn_x_m+4*scale, btn_y_m+btn_h_m*0.25-fs_d/2, fs_d,
                   _ACCENT_DIM if issel else _TEXT_LABEL)

    # ── CENTRE PANEL: state + buttons + output channel ────────────────────────
    right_w  = rw * 0.24
    centre_w = rw - left_w - right_w - margin*4
    centre_x = lx + left_w + margin
    cx = centre_x
    cy2 = work_bot
    ch2 = work_h
    if not _has_skin:
        _draw_rect(cx, cy2, centre_w, ch2, _PANEL)
        cvs3 = [(cx,cy2),(cx+centre_w,cy2),(cx+centre_w,cy2+ch2),(cx,cy2+ch2),(cx,cy2)]
        cb3 = batch_for_shader(sh,"LINE_STRIP",{"pos":cvs3})
        sh.bind(); sh.uniform_float("color",_BORDER); cb3.draw(sh)
        _draw_text("RESTORATION", cx+5*scale, cy2+ch2-fs_lbl-4*scale, fs_lbl, _TEXT_LABEL)

    # State display — fill suppressed once skinned; the status-coloured
    # border below still draws every time since it's a live state indicator,
    # not static chrome (same convention as rack_knnvc.py's state area).
    state_h = ch2 * 0.44
    state_y = cy2 + ch2 - fs_lbl - 10*scale - state_h
    if not _has_skin:
        _draw_rect(cx+4*scale, state_y, centre_w-8*scale, state_h, _BG)
    stvs = [(cx+4*scale,state_y),(cx+centre_w-4*scale,state_y),
            (cx+centre_w-4*scale,state_y+state_h),
            (cx+4*scale,state_y+state_h),(cx+4*scale,state_y)]
    stb = batch_for_shader(sh,"LINE_STRIP",{"pos":stvs})

    # State text nudge — applied to every dynamic status line/sub-line below.
    _st_tx = cx + 8*scale + VF_STATE_TEXT_X_OFFSET*scale
    _st_ty = VF_STATE_TEXT_Y_OFFSET*scale

    if status == _PROCESSING:
        if not _has_skin:
            sh.bind(); sh.uniform_float("color",(0.04,0.12,0.20,1.0)); stb.draw(sh)
        fs_st = _vf_fs(8, scale)
        _draw_text("RESTORING...", _st_tx,
                   state_y+state_h*0.65-fs_st/2+_st_ty, fs_st, _ACCENT)
        _draw_text("Neural vocoder rebuilding speech",
                   _st_tx, state_y+state_h*0.42+_st_ty,
                   _vf_fs(6, scale), _TEXT_DIM)
        _draw_text("This may take 30-90 seconds",
                   _st_tx, state_y+state_h*0.24+_st_ty,
                   _vf_fs(6, scale), _TEXT_DIM)

    elif status == _DONE:
        if not _has_skin:
            sh.bind(); sh.uniform_float("color",(0.02,0.08,0.04,1.0)); stb.draw(sh)
        fs_st = _vf_fs(8, scale)
        _draw_text("RESTORATION DONE", _st_tx,
                   state_y+state_h*0.78-fs_st/2+_st_ty, fs_st, _GREEN)
        import glob as _glob, wave
        tmp_matches = sorted(_glob.glob(
            os.path.join(__import__('tempfile').gettempdir(), f"pb_vf_{ai_idx}_*.wav")))
        fs_info = _vf_fs(7, scale)
        if tmp_matches:
            wav = tmp_matches[-1]
            try:
                sz = os.path.getsize(wav)
                sz_str = f"{sz//1024}KB" if sz < 1024*1024 else f"{sz//(1024*1024)}MB"
                with wave.open(wav,'r') as wf:
                    dur = wf.getnframes()/wf.getframerate()
                    sr  = wf.getframerate()
                _draw_text(f"Duration: {dur:.1f}s", _st_tx,
                           state_y+state_h*0.55-fs_info/2+_st_ty, fs_info, _TEXT_DIM)
                _draw_text(f"Sample rate: {sr}Hz", _st_tx,
                           state_y+state_h*0.38-fs_info/2+_st_ty, fs_info, _TEXT_DIM)
                _draw_text(f"Size: {sz_str}", _st_tx,
                           state_y+state_h*0.21-fs_info/2+_st_ty, fs_info, _TEXT_DIM)
            except Exception:
                _draw_text("Output placed in VSE", _st_tx,
                           state_y+state_h*0.45-fs_info/2+_st_ty, fs_info, _TEXT_DIM)

    elif status == _ERROR:
        if not _has_skin:
            sh.bind(); sh.uniform_float("color",(0.08,0.01,0.01,1.0)); stb.draw(sh)
        fs_st = _vf_fs(8, scale)
        _draw_text("ERROR — check console", _st_tx,
                   state_y+state_h*0.60-fs_st/2+_st_ty, fs_st, (0.90,0.20,0.10,1.0))
        _draw_text("Assign source channel in rail",
                   _st_tx, state_y+state_h*0.38+_st_ty,
                   _vf_fs(6, scale), _TEXT_DIM)

    else:  # READY
        if not _has_skin:
            sh.bind(); sh.uniform_float("color",_BORDER); stb.draw(sh)
        fs_st = _vf_fs(8, scale)
        if not active_chs:
            _draw_text("Assign source channel", _st_tx,
                       state_y+state_h*0.65+_st_ty, fs_st, _TEXT_DIM)
            _draw_text("in the rack rail above", _st_tx,
                       state_y+state_h*0.45+_st_ty, _vf_fs(7, scale), _TEXT_LABEL)
        else:
            _draw_text("Ready to restore", _st_tx,
                       state_y+state_h*0.65+_st_ty, fs_st, _ACCENT)
            _draw_text(f"Src: CH{active_chs[0]}  |  Mode: {_MODE_LABELS[mode_idx]}",
                       _st_tx, state_y+state_h*0.42+_st_ty,
                       _vf_fs(7, scale), _TEXT_DIM)
            _draw_text("VoiceFixer — neural vocoder restoration",
                       _st_tx, state_y+state_h*0.22+_st_ty,
                       _vf_fs(6, scale), _TEXT_LABEL)

    # ENHANCE + PREVIEW buttons — chrome suppressed once skinned, baked into
    # the art; only the state-dependent label (ENHANCE/RESTORING.../PREVIEW/
    # STOP) below still needs to draw live.
    btn_h2 = max(22*scale, ch2*0.11)
    btn_y2 = state_y - 2*scale - btn_h2
    enh_w  = centre_w * 0.52
    prv_w  = centre_w * 0.38
    enh_x  = cx + 4*scale
    prv_x  = enh_x + enh_w + 4*scale

    e_lbl = "RESTORING..." if status == _PROCESSING else "ENHANCE"
    e_bg  = (0.02,0.08,0.14,1.0) if status == _PROCESSING else (0.03,0.10,0.18,1.0)
    if not _has_skin:
        _draw_rect(enh_x, btn_y2, enh_w, btn_h2, e_bg)
        evs = [(enh_x,btn_y2),(enh_x+enh_w,btn_y2),(enh_x+enh_w,btn_y2+btn_h2),
               (enh_x,btn_y2+btn_h2),(enh_x,btn_y2)]
        eb = batch_for_shader(sh,"LINE_STRIP",{"pos":evs})
        sh.bind(); sh.uniform_float("color",_ACCENT); eb.draw(sh)
    fs_btn = _vf_fs(8, scale)
    tw_e   = _text_width(e_lbl, fs_btn)
    _draw_text(e_lbl, enh_x+enh_w/2-tw_e/2,
               btn_y2+btn_h2/2-fs_btn/2 + VF_BTN_LABEL_Y_OFFSET*scale, fs_btn, _ACCENT)

    # Preview button with toggle state
    _has_out    = False
    _is_playing = False
    try:
        from core.ai_voicefixer import has_rack_output, is_rack_previewing
        _has_out    = has_rack_output(ai_idx)
        _is_playing = is_rack_previewing(ai_idx)
    except Exception:
        pass
    _prv_bg  = (0.04,0.12,0.20,1.0) if _is_playing else (0.01,0.04,0.06,1.0)
    prv_col  = _ACCENT if (_has_out or _is_playing) else _ACCENT_DIM
    pv_lbl   = "■ STOP" if _is_playing else "> PREVIEW"
    if not _has_skin:
        _draw_rect(prv_x, btn_y2, prv_w, btn_h2, _prv_bg)
        pvs2 = [(prv_x,btn_y2),(prv_x+prv_w,btn_y2),(prv_x+prv_w,btn_y2+btn_h2),
                (prv_x,btn_y2+btn_h2),(prv_x,btn_y2)]
        pb2 = batch_for_shader(sh,"LINE_STRIP",{"pos":pvs2})
        sh.bind(); sh.uniform_float("color",prv_col); pb2.draw(sh)
    tw_pv = _text_width(pv_lbl, fs_btn)
    _draw_text(pv_lbl, prv_x+prv_w/2-tw_pv/2,
               btn_y2+btn_h2/2-fs_btn/2 + VF_BTN_LABEL_Y_OFFSET*scale, fs_btn, prv_col)

    # Output channel < > arrows (stored in p3)
    row2_h = max(16*scale, ch2*0.08)
    row2_y = btn_y2 - 2*scale - row2_h
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
    # Left arrow — suppressed once skinned (baked into the art either side
    # of the output-channel box); the box's hit test is unaffected, this
    # only turns off the drawn triangle.
    if not _has_skin:
        mv_l = [(cx+5*scale+lbl_tw+arr_w*0.7, row2_y+row2_h*0.2),
                (cx+5*scale+lbl_tw+arr_w*0.3, row2_y+row2_h*0.5),
                (cx+5*scale+lbl_tw+arr_w*0.7, row2_y+row2_h*0.8)]
        al = batch_for_shader(sh,"LINE_STRIP",{"pos":mv_l})
        sh.bind(); sh.uniform_float("color",_ACCENT_DIM); al.draw(sh)

    if not _has_skin:
        _draw_rect(oc_x, row2_y, oc_s, row2_h, _PANEL)
        ocvs = [(oc_x,row2_y),(oc_x+oc_s,row2_y),(oc_x+oc_s,row2_y+row2_h),
                (oc_x,row2_y+row2_h),(oc_x,row2_y)]
        ocb = batch_for_shader(sh,"LINE_STRIP",{"pos":ocvs})
        sh.bind(); sh.uniform_float("color",_BORDER); ocb.draw(sh)
    oc_str = str(out_ch_val)
    tw_oc  = _text_width(oc_str, fs_lbl)
    _draw_text(oc_str, oc_x+oc_s/2-tw_oc/2 + VF_OUTCH_VALUE_X_OFFSET*scale,
               row2_y+row2_h/2-fs_lbl/2 + VF_OUTCH_VALUE_Y_OFFSET*scale, fs_lbl, _ACCENT)

    plus_x = oc_x + oc_s + 2*scale
    if not _has_skin:
        _draw_rect(plus_x, row2_y, arr_w, row2_h, _PANEL)
    # Right arrow — suppressed once skinned, same reasoning as the left
    # arrow above.
    if not _has_skin:
        mv_r = [(plus_x+arr_w*0.3, row2_y+row2_h*0.2),
                (plus_x+arr_w*0.7, row2_y+row2_h*0.5),
                (plus_x+arr_w*0.3, row2_y+row2_h*0.8)]
        ar = batch_for_shader(sh,"LINE_STRIP",{"pos":mv_r})
        sh.bind(); sh.uniform_float("color",_ACCENT_DIM); ar.draw(sh)

    # ── RIGHT PANEL: info ─────────────────────────────────────────────────────
    rx2 = centre_x + centre_w + margin
    ry2 = work_bot
    rh2 = work_h
    if not _has_skin:
        _draw_rect(rx2, ry2, right_w, rh2, _PANEL)
        rvs = [(rx2,ry2),(rx2+right_w,ry2),(rx2+right_w,ry2+rh2),(rx2,ry2+rh2),(rx2,ry2)]
        rb = batch_for_shader(sh,"LINE_STRIP",{"pos":rvs})
        sh.bind(); sh.uniform_float("color",_BORDER); rb.draw(sh)
        _draw_text("ABOUT", rx2+5*scale, ry2+rh2-fs_lbl-4*scale, fs_lbl, _TEXT_LABEL)

    # Help-text column — kept even when skinned, same treatment as
    # rack_knnvc.py's TOPK/REF SECS notes: unlikely to be baked into a
    # control-panel graphic, flag to confirm once the PNG exists; easy to
    # wrap in `if not _has_skin:` if it turns out to be baked. Position of
    # the whole block nudged via VF_ABOUT_NOTES_X_OFFSET/_Y_OFFSET above —
    # line-to-line spacing is unchanged, the block just moves as one piece.
    fs_n   = _vf_fs(6, scale)
    notes_x = rx2 + 6*scale + VF_ABOUT_NOTES_X_OFFSET*scale
    note_y = ry2 + rh2*0.88 + VF_ABOUT_NOTES_Y_OFFSET*scale
    notes  = [
        "VoiceFixer",
        "Neural vocoder",
        "speech restoration",
        "",
        "Handles: noise,",
        "reverb, low-res,",
        "clipping — in",
        "one pass",
        "",
        "Output: 44.1kHz",
        "CUDA / MPS / CPU",
        "",
        "~625MB models",
        "auto-downloaded",
    ]
    for note in notes:
        if note_y < ry2 + fs_n: break
        _draw_text(note, notes_x, note_y, fs_n, _TEXT_DIM if note else _TEXT_DIM)
        note_y -= (fs_n + 3*scale)

    # ── STATUS BAR ────────────────────────────────────────────────────────────
    if not _has_skin:
        _draw_rect(sbar_x, sbar_y, sbar_w, sbar_h, (0.01,0.02,0.04,1.0))
        svs = [(sbar_x,sbar_y),(sbar_x+sbar_w,sbar_y),(sbar_x+sbar_w,sbar_y+sbar_h),
               (sbar_x,sbar_y+sbar_h),(sbar_x,sbar_y)]
        sb2 = batch_for_shader(sh,"LINE_STRIP",{"pos":svs})
        sh.bind(); sh.uniform_float("color",_BORDER); sb2.draw(sh)

    src_str = f"SRC:CH{active_chs[0]}" if active_chs else "SRC:unset"
    fs_sb   = _vf_fs(7, scale)
    _draw_text(
        f"ENGINE: VoiceFixer  |  {src_str}  |  OUT:CH{out_ch_val}  |  MODE:{_MODE_LABELS[mode_idx]}  |  OFFLINE",
        sbar_x+7*scale + VF_STATUSBAR_TEXT_X_OFFSET*scale,
        sbar_y+sbar_h/2-fs_sb/2 + VF_STATUSBAR_TEXT_Y_OFFSET*scale,
        fs_sb, _TEXT_DIM)

    # Status LED — suppressed once skinned; baked into the background art in
    # the bottom-right corner of the rack, same as rack_knnvc.py's.
    if not _has_skin:
        dot_cols = {
            _READY:      _AMBER,
            _PROCESSING: (0.90,0.50,0.10,1.0),
            _DONE:       _GREEN,
            _ERROR:      (0.90,0.10,0.05,1.0),
        }
        _draw_circle(sbar_x+sbar_w-9*scale, sbar_y+sbar_h/2,
                     3.5*scale, dot_cols.get(status, _AMBER))