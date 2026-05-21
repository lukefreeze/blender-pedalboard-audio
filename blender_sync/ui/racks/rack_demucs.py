# =============================================================================
# ui/racks/rack_demucs.py
# Demucs — Stem Splitter rack UI
#
# p0 = model index     (0=htdemucs 1=htdemucs_ft 2=htdemucs_6s 3=mdx_extra)
# p1 = preview mode    (>0.5 = fast preview)
# p2 = mute original   (>0.5 = mute source after split)
# p3 = stem toggle bits (bit0=drums bit1=bass bit2=vocals bit3=other
#                        bit4=piano bit5=guitar)
# p4 = drums out ch    (0=auto)
# p5 = bass out ch     (0=auto)
# p6 = vocals out ch   (0=auto)
# p7 = other out ch    (0=auto)
# =============================================================================

import math
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

RACK_RAIL_H = 32

_BG         = (0.02, 0.06, 0.03, 1.0)
_BORDER     = (0.08, 0.25, 0.10, 1.0)
_GREEN      = (0.15, 0.85, 0.35, 1.0)
_GREEN_DIM  = (0.06, 0.35, 0.14, 1.0)
_GREEN_MID  = (0.08, 0.55, 0.22, 1.0)
_RED        = (0.85, 0.15, 0.10, 1.0)
_AMBER      = (0.85, 0.55, 0.05, 1.0)
_TEXT_DIM   = (0.18, 0.45, 0.22, 1.0)
_TEXT_MUTED = (0.08, 0.22, 0.10, 1.0)
_PANEL      = (0.01, 0.04, 0.02, 1.0)

MODELS = ["htdemucs", "htdemucs_ft", "htdemucs_6s", "mdx_extra"]
MODEL_DESC = {
    "htdemucs":    "4 stems · fast",
    "htdemucs_ft": "4 stems · best",
    "htdemucs_6s": "6 stems · fine",
    "mdx_extra":   "4 stems · alt",
}
MODEL_STEMS = {
    "htdemucs":    ["drums", "bass", "vocals", "other"],
    "htdemucs_ft": ["drums", "bass", "vocals", "other"],
    "htdemucs_6s": ["drums", "bass", "vocals", "other", "piano", "guitar"],
    "mdx_extra":   ["drums", "bass", "vocals", "other"],
}
STEM_ICONS = {
    "drums":  "DRUMS",
    "bass":   "BASS",
    "vocals": "VOX",
    "other":  "OTHER",
    "piano":  "PIANO",
    "guitar": "GTR",
}
STEM_BITS = {"drums": 0, "bass": 1, "vocals": 2, "other": 3, "piano": 4, "guitar": 5}
STEM_CH_PROPS = {"drums": "p4", "bass": "p5", "vocals": "p6", "other": "p7"}

# ---------------------------------------------------------------------------
# Dependency check — runs once in background, cached for session
# ---------------------------------------------------------------------------
_dm_dep_cache     = {"ok": False, "checked": False, "checking": False}
_dm_check_running = False

_WARN_BG     = (0.02, 0.05, 0.02, 1.0)
_WARN_BORDER = (0.15, 0.55, 0.15, 1.0)
_WARN_TEXT   = (0.30, 0.90, 0.35, 1.0)
_WARN_DIM    = (0.15, 0.50, 0.20, 1.0)


def _run_dep_check():
    global _dm_check_running
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
                    cmd + ["-m", "pip", "show", "demucs"],
                    capture_output=True, timeout=5)
                if r.returncode == 0:
                    ok = True
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"[DEMUCS] dep check error: {e}")
    _dm_dep_cache["ok"]       = ok
    _dm_dep_cache["checked"]  = True
    _dm_dep_cache["checking"] = False
    _dm_check_running             = False
    print(f"[DEMUCS] dep check complete: {'found' if ok else 'not found'}")
    try:
        import bpy
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ('NODE_EDITOR', 'SEQUENCE_EDITOR'):
                    area.tag_redraw()
    except Exception:
        pass

def _check_demucs():
    global _dm_check_running
    if not _dm_dep_cache["checked"] and not _dm_check_running:
        _dm_check_running = True
        _dm_dep_cache["checking"] = True
        import threading
        threading.Thread(target=_run_dep_check, daemon=True).start()
    return _dm_dep_cache["ok"]


def _draw_setup_warning(rx, ry, rw, rh, scale):
    """Draw the 'demucs not installed' warning panel."""
    sh       = gpu.shader.from_builtin("UNIFORM_COLOR")
    rail_h   = RACK_RAIL_H * scale
    body_bot = ry
    body_top = ry + rh - rail_h
    body_h   = body_top - body_bot
    margin   = 8 * scale

    _draw_rect(rx, body_bot, rw, body_h, _BG)
    bvs = [(rx, body_bot), (rx+rw, body_bot),
           (rx+rw, body_top), (rx, body_top), (rx, body_bot)]
    bb = batch_for_shader(sh, "LINE_STRIP", {"pos": bvs})
    sh.bind(); sh.uniform_float("color", _BORDER); bb.draw(sh)

    warn_w = rw - margin * 4
    warn_h = min(body_h * 0.82, 165 * scale)
    warn_x = rx + (rw - warn_w) / 2
    warn_y = body_bot + (body_h - warn_h) / 2

    _draw_rect(warn_x, warn_y, warn_w, warn_h, _WARN_BG)
    _draw_rect(warn_x, warn_y, 4 * scale, warn_h, _WARN_TEXT)
    wvs = [(warn_x, warn_y), (warn_x+warn_w, warn_y),
           (warn_x+warn_w, warn_y+warn_h), (warn_x, warn_y+warn_h), (warn_x, warn_y)]
    wb = batch_for_shader(sh, "LINE_STRIP", {"pos": wvs})
    sh.bind(); sh.uniform_float("color", _WARN_BORDER); wb.draw(sh)

    tx   = warn_x + 12 * scale
    fs_h = max(1, int(9 * scale))
    fs_b = max(1, int(8 * scale))
    fs_s = max(1, int(7 * scale))
    lh   = fs_b + 5 * scale
    ty   = warn_y + warn_h - fs_h - 8 * scale

    _draw_text("DEMUCS NOT FOUND — REQUIRES SETUP", tx, ty, fs_h, _WARN_TEXT)
    ty -= lh * 1.4
    _draw_text("Stem separation needs Demucs + ffmpeg in your system Python.", tx, ty, fs_b, _WARN_DIM)
    ty -= lh
    _draw_text("Open a terminal and run:", tx, ty, fs_b, _WARN_DIM)
    ty -= lh * 1.2

    cmd_w = warn_w - 24 * scale
    cmd_h = lh * 3.2 + 6 * scale
    cmd_x = tx
    cmd_y = ty - cmd_h
    _draw_rect(cmd_x, cmd_y, cmd_w, cmd_h, (0.01, 0.04, 0.01, 1.0))
    cvs = [(cmd_x, cmd_y), (cmd_x+cmd_w, cmd_y),
           (cmd_x+cmd_w, cmd_y+cmd_h), (cmd_x, cmd_y+cmd_h), (cmd_x, cmd_y)]
    cb = batch_for_shader(sh, "LINE_STRIP", {"pos": cvs})
    sh.bind(); sh.uniform_float("color", (0.10, 0.40, 0.12, 1.0)); cb.draw(sh)
    _draw_text("py -3.12 -m pip install demucs",
               cmd_x + 6 * scale, cmd_y + cmd_h - lh*1.2, fs_b, _WARN_TEXT)
    _draw_text("winget install Gyan.FFmpeg",
               cmd_x + 6 * scale, cmd_y + cmd_h - lh*2.4, fs_b, _WARN_TEXT)
    _draw_text("(then copy ffprobe.exe to your Python folder if ffmpeg alias is broken)",
               cmd_x + 6 * scale, cmd_y + 4*scale, max(1, int(6*scale)), _WARN_DIM)

    ty = cmd_y - lh * 1.2
    _draw_text("Models (~80-400MB) auto-download on first run. Restart Blender after install.",
               tx, ty, fs_s, _WARN_DIM)
    ty -= lh
    _draw_text("Requires ffprobe accessible from Python. CUDA recommended for large files.",
               tx, ty, fs_s, _WARN_DIM)

    btn_w = min(140 * scale, warn_w * 0.38)
    btn_h = max(16 * scale, fs_s + 8 * scale)
    btn_x = warn_x + warn_w - btn_w - 12 * scale
    btn_y = warn_y + 8 * scale
    _draw_rect(btn_x, btn_y, btn_w, btn_h, (0.02, 0.08, 0.03, 1.0))
    bvs2 = [(btn_x, btn_y), (btn_x+btn_w, btn_y),
            (btn_x+btn_w, btn_y+btn_h), (btn_x, btn_y+btn_h), (btn_x, btn_y)]
    bb2 = batch_for_shader(sh, "LINE_STRIP", {"pos": bvs2})
    sh.bind(); sh.uniform_float("color", _WARN_BORDER); bb2.draw(sh)
    fs_btn = max(1, int(7 * scale))
    lbl    = "OPEN SETUP GUIDE  >"
    tw_btn = _text_width(lbl, fs_btn)
    _draw_text(lbl, btn_x + btn_w/2 - tw_btn/2,
               btn_y + btn_h/2 - fs_btn/2, fs_btn, _WARN_TEXT)
    return btn_x, btn_y, btn_w, btn_h


def _get_free_channels(exclude=None):
    try:
        scene = bpy.context.scene
        if not scene or not scene.sequence_editor:
            return []
        used = {s.channel for s in scene.sequence_editor.sequences_all
                if s.type == "SOUND" and s.sound}
        if exclude:
            used.discard(exclude)
        return [ch for ch in range(1, 33) if ch not in used][:6]
    except Exception:
        return []


def _draw_demucs_body(rx, ry, rw, rh, rack, ai_idx, scale):
    # Dependency check — show warning panel if demucs not installed
    if not _check_demucs():
        bx, by, bw, bh = _draw_setup_warning(rx, ry, rw, rh, scale)
        try: rack['dm_setup_btn'] = (bx, by, bw, bh)
        except Exception: pass
        return
    model_idx  = max(0, min(int(getattr(rack, "p0", 1.0)), len(MODELS) - 1))
    model      = MODELS[model_idx]
    preview    = float(getattr(rack, "p1", 0.0)) > 0.5
    mute_orig  = float(getattr(rack, "p2", 1.0)) > 0.5
    stem_bits  = int(getattr(rack, "p3", 15.0))
    status     = getattr(rack, "ai_status", "READY")
    if status not in ("READY", "PROCESSING", "DONE", "ERROR"):
        status = "READY"

    all_stems  = MODEL_STEMS[model]
    basic_4    = ["drums", "bass", "vocals", "other"]

    rail_h   = RACK_RAIL_H * scale
    body_bot = ry
    body_top = ry + rh - rail_h
    body_h   = body_top - body_bot
    shader   = gpu.shader.from_builtin("UNIFORM_COLOR")

    # Background + border
    _draw_rect(rx, body_bot, rw, body_h, _BG)
    bv = [(rx, body_bot), (rx+rw, body_bot),
          (rx+rw, body_top), (rx, body_top), (rx, body_bot)]
    b  = batch_for_shader(shader, "LINE_STRIP", {"pos": bv})
    shader.bind(); shader.uniform_float("color", _BORDER); b.draw(shader)

    # Column layout — channel buttons at rw-100*scale (rack_base standard)
    content_w = rw - 100*scale
    left_w    = content_w * 0.24
    right_w   = content_w * 0.22
    centre_w  = content_w - left_w - right_w
    centre_x  = rx + left_w
    right_x   = centre_x + centre_w

    # Dividers
    for dx in (centre_x, right_x):
        dv = [(dx, body_bot+4*scale), (dx, body_top-4*scale)]
        db = batch_for_shader(shader, "LINES", {"pos": dv})
        shader.bind(); shader.uniform_float("color", (0.06, 0.18, 0.08, 1.0)); db.draw(shader)

    fs_lbl = max(1, int(7*scale))
    fs_sm  = max(1, int(8*scale))
    fs_med = max(1, int(9*scale))

    # ── LEFT: Model selector + run mode ──────────────────────────────────────
    mx = rx + 5*scale
    mw = left_w - 10*scale
    _draw_text("MODEL", mx, body_top - fs_lbl - 4*scale, fs_lbl, _TEXT_DIM)

    model_btn_h = max(13*scale, (body_h * 0.60 / len(MODELS)) - 3*scale)
    models_top  = body_top - fs_lbl - 8*scale
    for mi, m in enumerate(MODELS):
        by_m   = models_top - (mi+1) * (model_btn_h + 3*scale)
        active = (mi == model_idx)
        bg     = (0.04, 0.14, 0.06, 1.0) if active else _PANEL
        col    = _GREEN if active else _GREEN_DIM
        _draw_rect(mx, by_m, mw, model_btn_h, bg)
        mv = [(mx, by_m), (mx+mw, by_m), (mx+mw, by_m+model_btn_h),
              (mx, by_m+model_btn_h), (mx, by_m)]
        mb = batch_for_shader(shader, "LINE_STRIP", {"pos": mv})
        shader.bind(); shader.uniform_float("color", col); mb.draw(shader)
        lbl_fs = max(1, int(7*scale))
        _draw_text(m, mx+3*scale, by_m+model_btn_h/2-lbl_fs/2, lbl_fs, col)

    # Run mode buttons — Preview / Full
    mode_y  = body_bot + 24*scale
    mode_h  = min(18*scale, body_h * 0.12)
    half_w  = (mw - 3*scale) / 2

    for pi, (lbl, is_prev) in enumerate([("PREVIEW", True), ("FULL", False)]):
        bx_p   = mx + pi * (half_w + 3*scale)
        active = (preview == is_prev)
        bg_p   = (0.04, 0.14, 0.06, 1.0) if active else _PANEL
        col_p  = _GREEN if active else _GREEN_DIM
        _draw_rect(bx_p, mode_y, half_w, mode_h, bg_p)
        pv = [(bx_p, mode_y), (bx_p+half_w, mode_y),
              (bx_p+half_w, mode_y+mode_h), (bx_p, mode_y+mode_h), (bx_p, mode_y)]
        pb = batch_for_shader(shader, "LINE_STRIP", {"pos": pv})
        shader.bind(); shader.uniform_float("color", col_p); pb.draw(shader)
        tw_p = _text_width(lbl, max(1, int(6*scale)))
        _draw_text(lbl, bx_p+half_w/2-tw_p/2, mode_y+mode_h/2-max(1,int(6*scale))/2,
                   max(1, int(6*scale)), col_p)

    sub_lbl = "fast / lo-fi" if preview else "2-4 min"
    _draw_text(sub_lbl, mx, body_bot+4*scale, max(1, int(6*scale)), _TEXT_MUTED)

    # ── CENTRE: Stem rows ─────────────────────────────────────────────────────
    cx = centre_x + 5*scale
    cw = centre_w - 10*scale

    _draw_text("STEMS", cx, body_top - fs_lbl - 4*scale, fs_lbl, _TEXT_DIM)

    n_stems     = len(all_stems)
    stems_area_h = body_h - fs_lbl - 12*scale - 5*scale  # leave 5px for progress bar
    row_h       = min(22*scale, (stems_area_h - (n_stems-1)*3*scale) / n_stems)
    stems_top   = body_top - fs_lbl - 8*scale

    for si, stem in enumerate(all_stems):
        row_y   = stems_top - (si+1) * (row_h + 3*scale)
        enabled = bool((stem_bits >> STEM_BITS[stem]) & 1)
        is_6s_only = stem in ("piano", "guitar")
        available  = not (is_6s_only and model != "htdemucs_6s")
        dim_alpha  = 0.35 if not available else (1.0 if enabled else 0.55)

        # Row background
        row_bg = (0.02, 0.07, 0.03, 1.0) if enabled and available else _PANEL
        _draw_rect(cx, row_y, cw, row_h, row_bg)
        row_col = _GREEN if (enabled and available) else _GREEN_DIM
        rv = [(cx, row_y), (cx+cw, row_y), (cx+cw, row_y+row_h),
              (cx, row_y+row_h), (cx, row_y)]
        rb = batch_for_shader(shader, "LINE_STRIP", {"pos": rv})
        shader.bind()
        shader.uniform_float("color", (*row_col[:3], dim_alpha))
        rb.draw(shader)

        pad = 3*scale
        # Checkbox
        chk_s = min(row_h - 4*scale, 10*scale)
        chk_x = cx + pad
        chk_y = row_y + row_h/2 - chk_s/2
        chk_bg  = (0.04, 0.18, 0.07, 1.0) if enabled else _PANEL
        chk_col = _GREEN if enabled else _GREEN_DIM
        _draw_rect(chk_x, chk_y, chk_s, chk_s, chk_bg)
        cv = [(chk_x, chk_y), (chk_x+chk_s, chk_y),
              (chk_x+chk_s, chk_y+chk_s), (chk_x, chk_y+chk_s), (chk_x, chk_y)]
        cb = batch_for_shader(shader, "LINE_STRIP", {"pos": cv})
        shader.bind(); shader.uniform_float("color", chk_col); cb.draw(shader)
        if enabled and available:
            _draw_text("✓", chk_x+chk_s/2-3*scale, chk_y+1*scale,
                       max(1, int(6*scale)), _GREEN)

        # Icon label
        icon_fs = max(1, int(7*scale))
        icon_x  = chk_x + chk_s + 4*scale
        icon_w  = 28*scale
        icn_col = row_col if available else _TEXT_MUTED
        _draw_text(STEM_ICONS[stem], icon_x,
                   row_y + row_h/2 - icon_fs/2, icon_fs, icn_col)

        # Level bar (shows RMS post-split; empty during READY/PROCESSING)
        bar_x = icon_x + icon_w
        bar_w = cw - (bar_x - cx) - 54*scale - pad
        bar_h = max(4*scale, row_h * 0.40)
        bar_y = row_y + row_h/2 - bar_h/2
        _draw_rect(bar_x, bar_y, bar_w, bar_h, (0.01, 0.04, 0.02, 1.0))
        bvb = [(bar_x, bar_y), (bar_x+bar_w, bar_y),
               (bar_x+bar_w, bar_y+bar_h), (bar_x, bar_y+bar_h), (bar_x, bar_y)]
        bb_b = batch_for_shader(shader, "LINE_STRIP", {"pos": bvb})
        shader.bind(); shader.uniform_float("color", (0.04, 0.15, 0.06, 1.0)); bb_b.draw(shader)

        # Channel stepper
        prop   = STEM_CH_PROPS.get(stem)
        ch_val = int(getattr(rack, prop, 0.0)) if prop else 0
        ch_str = f"ch{ch_val}" if ch_val > 0 else "auto"
        st_x   = bar_x + bar_w + 3*scale
        st_w   = 50*scale
        st_h   = row_h - 4*scale
        st_y   = row_y + 2*scale
        _draw_rect(st_x, st_y, st_w, st_h, _PANEL)
        stv = [(st_x, st_y), (st_x+st_w, st_y), (st_x+st_w, st_y+st_h),
               (st_x, st_y+st_h), (st_x, st_y)]
        stb = batch_for_shader(shader, "LINE_STRIP", {"pos": stv})
        shader.bind()
        shader.uniform_float("color", _GREEN_DIM if available else _TEXT_MUTED)
        stb.draw(shader)
        _draw_text("−", st_x+3*scale, st_y+st_h/2-fs_sm/2, fs_sm, _GREEN_MID)
        _draw_text("+", st_x+st_w-_text_width("+", fs_sm)-3*scale,
                   st_y+st_h/2-fs_sm/2, fs_sm, _GREEN_MID)
        tw_ch = _text_width(ch_str, max(1, int(7*scale)))
        _draw_text(ch_str, st_x+st_w/2-tw_ch/2,
                   st_y+st_h/2-max(1,int(7*scale))/2, max(1, int(7*scale)),
                   _GREEN if (enabled and available) else _TEXT_MUTED)

    # Progress bar
    prog_y = body_bot + 1*scale
    prog_h = 3*scale
    _draw_rect(cx, prog_y, cw, prog_h, _PANEL)
    if status == "PROCESSING":
        t_now  = time.time()
        pulse  = 0.4 + 0.6 * abs(math.sin(t_now * 1.5))
        fill_w = cw * pulse
        _draw_rect(cx, prog_y, fill_w, prog_h, _GREEN)

    # 6s hint
    if model != "htdemucs_6s":
        hint = "Piano + Guitar: select htdemucs_6s"
        _draw_text(hint, cx, body_bot + 5*scale, max(1, int(6*scale)), _TEXT_MUTED)

    # ── RIGHT: Options + status + run button ──────────────────────────────────
    rx2 = right_x + 4*scale
    rw2 = right_w - 8*scale

    _draw_text("OPTIONS", rx2, body_top - fs_lbl - 4*scale, fs_lbl, _TEXT_DIM)

    # Mute original toggle
    opt_y = body_top - fs_lbl - 10*scale - 14*scale
    opt_h = 13*scale
    mute_bg  = (0.04, 0.14, 0.06, 1.0) if mute_orig else _PANEL
    mute_col = _GREEN if mute_orig else _GREEN_DIM
    _draw_rect(rx2, opt_y, rw2, opt_h, mute_bg)
    mov = [(rx2, opt_y), (rx2+rw2, opt_y), (rx2+rw2, opt_y+opt_h),
           (rx2, opt_y+opt_h), (rx2, opt_y)]
    mob = batch_for_shader(shader, "LINE_STRIP", {"pos": mov})
    shader.bind(); shader.uniform_float("color", mute_col); mob.draw(shader)
    _draw_text("MUTE ORIGINAL", rx2+3*scale, opt_y+opt_h/2-max(1,int(6*scale))/2,
               max(1, int(6*scale)), mute_col)

    # Free channels hint
    free    = _get_free_channels()
    free_y  = opt_y - fs_lbl - 4*scale
    if free:
        free_str = "free: " + " ".join(str(c) for c in free[:5])
        _draw_text(free_str, rx2, free_y, max(1, int(6*scale)), _TEXT_MUTED)

    # Status info
    dot_cols = {"READY": _GREEN_DIM, "PROCESSING": _AMBER,
                "DONE": _GREEN, "ERROR": _RED}
    status_y = body_bot + 32*scale
    _draw_circle(rx2 + 4*scale, status_y + 4*scale, 4*scale,
                 dot_cols.get(status, _GREEN_DIM))
    st_lbl = status
    _draw_text(st_lbl, rx2 + 12*scale, status_y, fs_sm,
               dot_cols.get(status, _GREEN_DIM))

    # Info lines
    info_y   = status_y - fs_lbl - 3*scale
    n_active = bin(stem_bits).count("1")
    _draw_text(f"{n_active} stems on", rx2, info_y, max(1, int(6*scale)), _TEXT_MUTED)
    _draw_text("CPU/GPU auto", rx2, info_y - fs_lbl - 2*scale,
               max(1, int(6*scale)), _TEXT_MUTED)

    # Run button
    run_h = min(22*scale, body_h * 0.16)
    run_y = body_bot + 4*scale
    if status == "PROCESSING":
        pulse  = 0.5 + 0.5 * math.sin(time.time() * 4.0)
        run_bg = (0.04, 0.14, 0.06, 1.0)
        run_col = (_GREEN[0], _GREEN[1] * 0.5 + 0.5 * pulse, _GREEN[2], 1.0)
        run_lbl = "SPLITTING..."
    elif status == "DONE":
        run_bg  = (0.02, 0.10, 0.04, 1.0)
        run_col = _GREEN
        run_lbl = "DONE  (run again)"
    elif status == "ERROR":
        run_bg  = (0.14, 0.02, 0.02, 1.0)
        run_col = _RED
        run_lbl = "ERROR — RETRY"
    else:
        run_bg  = (0.03, 0.12, 0.05, 1.0)
        run_col = _GREEN
        run_lbl = "▶  SPLIT STEMS"

    _draw_rect(rx2, run_y, rw2, run_h, run_bg)
    rv_run = [(rx2, run_y), (rx2+rw2, run_y), (rx2+rw2, run_y+run_h),
              (rx2, run_y+run_h), (rx2, run_y)]
    rb_run = batch_for_shader(shader, "LINE_STRIP", {"pos": rv_run})
    shader.bind(); shader.uniform_float("color", run_col); rb_run.draw(shader)
    fs_run = max(1, int(8*scale))
    tw_run = _text_width(run_lbl, fs_run)
    _draw_text(run_lbl, rx2+rw2/2-tw_run/2, run_y+run_h/2-fs_run/2, fs_run, run_col)
