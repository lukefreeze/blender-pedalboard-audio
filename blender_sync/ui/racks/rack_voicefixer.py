# =============================================================================
# ui/racks/rack_voicefixer.py
# VoiceFixer speech restoration rack (system Python / PyTorch, BETA).
#
# Uses VoiceFixer neural vocoder to restore degraded speech:
# noise, reverb, low resolution and clipping handled in one pass.
# Modes: 0=Standard, 1=Smooth, 2=Aggressive
# =============================================================================

import os
import time
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
    _vf_dep_cache["ok"]       = found
    _vf_dep_cache["checked"]  = True
    _vf_dep_cache["checking"] = False
    _vf_check_running         = False
    print(f"[VOICEFIXER] dep check complete: {'found' if found else 'not found'}")


def _check_voicefixer():
    global _vf_check_running
    if not _vf_dep_cache["checked"] and not _vf_check_running:
        _vf_check_running         = True
        _vf_dep_cache["checking"] = True
        import threading
        threading.Thread(target=_run_dep_check, daemon=True).start()
    return _vf_dep_cache["ok"]


def _draw_setup_warning(rx, ry, rw, rh, scale):
    sh = gpu.shader.from_builtin("UNIFORM_COLOR")
    rail_h   = RACK_RAIL_H * scale
    body_bot = ry
    body_top = ry + rh - rail_h
    body_h   = body_top - body_bot
    margin   = 8 * scale

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
        bx, by, bw, bh = _draw_setup_warning(rx, ry, rw, rh, scale)
        try: rack['vf_setup_btn'] = (bx, by, bw, bh)
        except Exception: pass
        return

    rail_h   = RACK_RAIL_H * scale
    body_bot = ry
    body_top = ry + rh - rail_h
    body_h   = body_top - body_bot
    margin   = 8 * scale

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
    fs_lbl   = max(1, int(7*scale))
    status   = getattr(rack, "ai_status", _READY)
    mode_idx = int(getattr(rack, "p0", 0.0))
    mode_idx = max(0, min(2, mode_idx))

    # ── LEFT PANEL: source info + mode selector ───────────────────────────────
    left_w  = rw * 0.30
    lx = rx + margin
    ly = work_bot
    lh = work_h
    _draw_rect(lx, ly, left_w, lh, _PANEL)
    lvs = [(lx,ly),(lx+left_w,ly),(lx+left_w,ly+lh),(lx,ly+lh),(lx,ly)]
    lb = batch_for_shader(sh,"LINE_STRIP",{"pos":lvs})
    sh.bind(); sh.uniform_float("color",_BORDER); lb.draw(sh)

    _draw_text("SOURCE", lx+5*scale, ly+lh-fs_lbl-4*scale, fs_lbl, _TEXT_LABEL)
    active_chs = [ci+1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
    src_disp   = f"CH {active_chs[0]}" if active_chs else "USE RAIL"
    _draw_text(src_disp, lx+5*scale, ly+lh-fs_lbl*2-12*scale,
               max(1,int(7*scale)), _ACCENT if active_chs else _TEXT_DIM)

    # Mode selector — three stacked buttons
    mode_sec_h = lh * 0.62
    mode_y     = ly + lh * 0.04
    mode_sep_y = ly + lh - fs_lbl*2 - 18*scale
    _draw_text("RESTORATION MODE", lx+5*scale, mode_sep_y+fs_lbl+3*scale, fs_lbl, _TEXT_LABEL)

    btn_h_m = min(mode_sec_h/3 - 3*scale, 28*scale)
    for mi, (lbl_m, desc_m) in enumerate(zip(_MODE_LABELS, _MODE_DESCS)):
        btn_y_m = mode_sep_y - (mi+1)*(btn_h_m+3*scale)
        if btn_y_m < ly + 2*scale:
            break
        issel = (mi == mode_idx)
        bg    = _PANEL_SEL if issel else _PANEL
        bc    = _ACCENT    if issel else _BORDER
        _draw_rect(lx+4*scale, btn_y_m, left_w-8*scale, btn_h_m, bg)
        bvs2 = [(lx+4*scale,btn_y_m),(lx+left_w-4*scale,btn_y_m),
                (lx+left_w-4*scale,btn_y_m+btn_h_m),
                (lx+4*scale,btn_y_m+btn_h_m),(lx+4*scale,btn_y_m)]
        mb = batch_for_shader(sh,"LINE_STRIP",{"pos":bvs2})
        sh.bind(); sh.uniform_float("color",bc); mb.draw(sh)
        fs_m  = max(1,int(7*scale))
        fs_d  = max(1,int(6*scale))
        tc    = _TEXT if issel else _TEXT_DIM
        _draw_text(lbl_m, lx+8*scale, btn_y_m+btn_h_m*0.62-fs_m/2, fs_m, tc)
        _draw_text(desc_m, lx+8*scale, btn_y_m+btn_h_m*0.25-fs_d/2, fs_d,
                   _ACCENT_DIM if issel else _TEXT_LABEL)

    # ── CENTRE PANEL: state + buttons + output channel ────────────────────────
    right_w  = rw * 0.24
    centre_w = rw - left_w - right_w - margin*4
    centre_x = lx + left_w + margin
    cx = centre_x
    cy2 = work_bot
    ch2 = work_h
    _draw_rect(cx, cy2, centre_w, ch2, _PANEL)
    cvs3 = [(cx,cy2),(cx+centre_w,cy2),(cx+centre_w,cy2+ch2),(cx,cy2+ch2),(cx,cy2)]
    cb3 = batch_for_shader(sh,"LINE_STRIP",{"pos":cvs3})
    sh.bind(); sh.uniform_float("color",_BORDER); cb3.draw(sh)
    _draw_text("RESTORATION", cx+5*scale, cy2+ch2-fs_lbl-4*scale, fs_lbl, _TEXT_LABEL)

    # State display
    state_h = ch2 * 0.44
    state_y = cy2 + ch2 - fs_lbl - 10*scale - state_h
    _draw_rect(cx+4*scale, state_y, centre_w-8*scale, state_h, _BG)
    stvs = [(cx+4*scale,state_y),(cx+centre_w-4*scale,state_y),
            (cx+centre_w-4*scale,state_y+state_h),
            (cx+4*scale,state_y+state_h),(cx+4*scale,state_y)]
    stb = batch_for_shader(sh,"LINE_STRIP",{"pos":stvs})

    if status == _PROCESSING:
        sh.bind(); sh.uniform_float("color",(0.04,0.12,0.20,1.0)); stb.draw(sh)
        fs_st = max(1,int(8*scale))
        _draw_text("RESTORING...", cx+8*scale,
                   state_y+state_h*0.65-fs_st/2, fs_st, _ACCENT)
        _draw_text("Neural vocoder rebuilding speech",
                   cx+8*scale, state_y+state_h*0.42,
                   max(1,int(6*scale)), _TEXT_DIM)
        _draw_text("This may take 30-90 seconds",
                   cx+8*scale, state_y+state_h*0.24,
                   max(1,int(6*scale)), _TEXT_DIM)

    elif status == _DONE:
        sh.bind(); sh.uniform_float("color",(0.02,0.08,0.04,1.0)); stb.draw(sh)
        fs_st = max(1,int(8*scale))
        _draw_text("RESTORATION DONE", cx+8*scale,
                   state_y+state_h*0.78-fs_st/2, fs_st, _GREEN)
        import glob as _glob, wave
        tmp_matches = sorted(_glob.glob(
            os.path.join(__import__('tempfile').gettempdir(), f"pb_vf_{ai_idx}_*.wav")))
        fs_info = max(1,int(7*scale))
        if tmp_matches:
            wav = tmp_matches[-1]
            try:
                sz = os.path.getsize(wav)
                sz_str = f"{sz//1024}KB" if sz < 1024*1024 else f"{sz//(1024*1024)}MB"
                with wave.open(wav,'r') as wf:
                    dur = wf.getnframes()/wf.getframerate()
                    sr  = wf.getframerate()
                _draw_text(f"Duration: {dur:.1f}s", cx+8*scale,
                           state_y+state_h*0.55-fs_info/2, fs_info, _TEXT_DIM)
                _draw_text(f"Sample rate: {sr}Hz", cx+8*scale,
                           state_y+state_h*0.38-fs_info/2, fs_info, _TEXT_DIM)
                _draw_text(f"Size: {sz_str}", cx+8*scale,
                           state_y+state_h*0.21-fs_info/2, fs_info, _TEXT_DIM)
            except Exception:
                _draw_text("Output placed in VSE", cx+8*scale,
                           state_y+state_h*0.45-fs_info/2, fs_info, _TEXT_DIM)

    elif status == _ERROR:
        sh.bind(); sh.uniform_float("color",(0.08,0.01,0.01,1.0)); stb.draw(sh)
        fs_st = max(1,int(8*scale))
        _draw_text("ERROR — check console", cx+8*scale,
                   state_y+state_h*0.60-fs_st/2, fs_st, (0.90,0.20,0.10,1.0))
        _draw_text("Assign source channel in rail",
                   cx+8*scale, state_y+state_h*0.38,
                   max(1,int(6*scale)), _TEXT_DIM)

    else:  # READY
        sh.bind(); sh.uniform_float("color",_BORDER); stb.draw(sh)
        fs_st = max(1,int(8*scale))
        if not active_chs:
            _draw_text("Assign source channel", cx+8*scale,
                       state_y+state_h*0.65, fs_st, _TEXT_DIM)
            _draw_text("in the rack rail above", cx+8*scale,
                       state_y+state_h*0.45, max(1,int(7*scale)), _TEXT_LABEL)
        else:
            _draw_text("Ready to restore", cx+8*scale,
                       state_y+state_h*0.65, fs_st, _ACCENT)
            _draw_text(f"Src: CH{active_chs[0]}  |  Mode: {_MODE_LABELS[mode_idx]}",
                       cx+8*scale, state_y+state_h*0.42,
                       max(1,int(7*scale)), _TEXT_DIM)
            _draw_text("VoiceFixer — neural vocoder restoration",
                       cx+8*scale, state_y+state_h*0.22,
                       max(1,int(6*scale)), _TEXT_LABEL)

    # ENHANCE + PREVIEW buttons
    btn_h2 = max(22*scale, ch2*0.11)
    btn_y2 = state_y - 2*scale - btn_h2
    enh_w  = centre_w * 0.52
    prv_w  = centre_w * 0.38
    enh_x  = cx + 4*scale
    prv_x  = enh_x + enh_w + 4*scale

    e_lbl = "RESTORING..." if status == _PROCESSING else "ENHANCE"
    e_bg  = (0.02,0.08,0.14,1.0) if status == _PROCESSING else (0.03,0.10,0.18,1.0)
    _draw_rect(enh_x, btn_y2, enh_w, btn_h2, e_bg)
    evs = [(enh_x,btn_y2),(enh_x+enh_w,btn_y2),(enh_x+enh_w,btn_y2+btn_h2),
           (enh_x,btn_y2+btn_h2),(enh_x,btn_y2)]
    eb = batch_for_shader(sh,"LINE_STRIP",{"pos":evs})
    sh.bind(); sh.uniform_float("color",_ACCENT); eb.draw(sh)
    fs_btn = max(1,int(8*scale))
    tw_e   = _text_width(e_lbl, fs_btn)
    _draw_text(e_lbl, enh_x+enh_w/2-tw_e/2, btn_y2+btn_h2/2-fs_btn/2, fs_btn, _ACCENT)

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
    _draw_rect(prv_x, btn_y2, prv_w, btn_h2, _prv_bg)
    pvs2 = [(prv_x,btn_y2),(prv_x+prv_w,btn_y2),(prv_x+prv_w,btn_y2+btn_h2),
            (prv_x,btn_y2+btn_h2),(prv_x,btn_y2)]
    pb2 = batch_for_shader(sh,"LINE_STRIP",{"pos":pvs2})
    sh.bind(); sh.uniform_float("color",prv_col); pb2.draw(sh)
    tw_pv = _text_width(pv_lbl, fs_btn)
    _draw_text(pv_lbl, prv_x+prv_w/2-tw_pv/2,
               btn_y2+btn_h2/2-fs_btn/2, fs_btn, prv_col)

    # Output channel < > arrows (stored in p3)
    row2_h = max(16*scale, ch2*0.08)
    row2_y = btn_y2 - 2*scale - row2_h
    out_ch_val = int(getattr(rack, "p3", 0.0)) or (active_chs[0]+1 if active_chs else 2)
    out_ch_val = max(1, min(9, out_ch_val))

    _draw_text("OUT CH", cx+5*scale, row2_y+row2_h/2-fs_lbl/2, fs_lbl, _TEXT_LABEL)
    lbl_tw = _text_width("OUT CH ", fs_lbl)
    arr_w  = max(14*scale, row2_h)
    oc_s   = max(22*scale, row2_h)
    oc_x   = cx + 5*scale + lbl_tw + arr_w + 2*scale

    _draw_rect(cx+5*scale+lbl_tw, row2_y, arr_w, row2_h, _PANEL)
    mv_l = [(cx+5*scale+lbl_tw+arr_w*0.7, row2_y+row2_h*0.2),
            (cx+5*scale+lbl_tw+arr_w*0.3, row2_y+row2_h*0.5),
            (cx+5*scale+lbl_tw+arr_w*0.7, row2_y+row2_h*0.8)]
    al = batch_for_shader(sh,"LINE_STRIP",{"pos":mv_l})
    sh.bind(); sh.uniform_float("color",_ACCENT_DIM); al.draw(sh)

    _draw_rect(oc_x, row2_y, oc_s, row2_h, _PANEL)
    ocvs = [(oc_x,row2_y),(oc_x+oc_s,row2_y),(oc_x+oc_s,row2_y+row2_h),
            (oc_x,row2_y+row2_h),(oc_x,row2_y)]
    ocb = batch_for_shader(sh,"LINE_STRIP",{"pos":ocvs})
    sh.bind(); sh.uniform_float("color",_BORDER); ocb.draw(sh)
    oc_str = str(out_ch_val)
    tw_oc  = _text_width(oc_str, fs_lbl)
    _draw_text(oc_str, oc_x+oc_s/2-tw_oc/2, row2_y+row2_h/2-fs_lbl/2, fs_lbl, _ACCENT)

    plus_x = oc_x + oc_s + 2*scale
    _draw_rect(plus_x, row2_y, arr_w, row2_h, _PANEL)
    mv_r = [(plus_x+arr_w*0.3, row2_y+row2_h*0.2),
            (plus_x+arr_w*0.7, row2_y+row2_h*0.5),
            (plus_x+arr_w*0.3, row2_y+row2_h*0.8)]
    ar = batch_for_shader(sh,"LINE_STRIP",{"pos":mv_r})
    sh.bind(); sh.uniform_float("color",_ACCENT_DIM); ar.draw(sh)

    # ── RIGHT PANEL: info ─────────────────────────────────────────────────────
    rx2 = centre_x + centre_w + margin
    ry2 = work_bot
    rh2 = work_h
    _draw_rect(rx2, ry2, right_w, rh2, _PANEL)
    rvs = [(rx2,ry2),(rx2+right_w,ry2),(rx2+right_w,ry2+rh2),(rx2,ry2+rh2),(rx2,ry2)]
    rb = batch_for_shader(sh,"LINE_STRIP",{"pos":rvs})
    sh.bind(); sh.uniform_float("color",_BORDER); rb.draw(sh)
    _draw_text("ABOUT", rx2+5*scale, ry2+rh2-fs_lbl-4*scale, fs_lbl, _TEXT_LABEL)

    fs_n   = max(1,int(6*scale))
    note_y = ry2 + rh2*0.88
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
        _draw_text(note, rx2+6*scale, note_y, fs_n, _TEXT_LABEL if note else _TEXT_LABEL)
        note_y -= (fs_n + 3*scale)

    # ── STATUS BAR ────────────────────────────────────────────────────────────
    _draw_rect(sbar_x, sbar_y, sbar_w, sbar_h, (0.01,0.02,0.04,1.0))
    svs = [(sbar_x,sbar_y),(sbar_x+sbar_w,sbar_y),(sbar_x+sbar_w,sbar_y+sbar_h),
           (sbar_x,sbar_y+sbar_h),(sbar_x,sbar_y)]
    sb2 = batch_for_shader(sh,"LINE_STRIP",{"pos":svs})
    sh.bind(); sh.uniform_float("color",_BORDER); sb2.draw(sh)

    src_str = f"SRC:CH{active_chs[0]}" if active_chs else "SRC:unset"
    fs_sb   = max(1,int(7*scale))
    _draw_text(
        f"ENGINE: VoiceFixer  |  {src_str}  |  OUT:CH{out_ch_val}  |  MODE:{_MODE_LABELS[mode_idx]}  |  OFFLINE",
        sbar_x+7*scale, sbar_y+sbar_h/2-fs_sb/2, fs_sb, _TEXT_DIM)

    dot_cols = {
        _READY:      _AMBER,
        _PROCESSING: (0.90,0.50,0.10,1.0),
        _DONE:       _GREEN,
        _ERROR:      (0.90,0.10,0.05,1.0),
    }
    _draw_circle(sbar_x+sbar_w-9*scale, sbar_y+sbar_h/2,
                 3.5*scale, dot_cols.get(status, _AMBER))
