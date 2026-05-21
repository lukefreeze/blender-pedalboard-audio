# =============================================================================
# ui/racks/rack_whisper.py
# Whisper speech-to-text rack — faster-whisper, system Python (BETA).
#
# 3-column layout:
#   LEFT   — input channel selector, output channel selector, model size
#   CENTRE — font picker (system fonts), font size, style flags, position
#   RIGHT  — audio language, transcribe/translate mode, VAD toggle, SRT toggle
#   FOOTER — status bar + TRANSCRIBE button
#
# Params on PB_AIRackSettings:
#   p0  model index     (0=tiny 1=base 2=small 3=medium 4=large-v3)
#   p1  language index  (0=auto, 1=en, 2=de, ...)
#   p2  mode            (0=transcribe, 1=translate to EN)
#   p3  output channel  (1-based int stored as float)
#   p4  font size       (12-200, default 48)
#   p5  style flags     (bit: 1=bold 2=italic 4=underline 8=shadow 16=box)
#   p6  position        (0=top 1=mid 2=bot)
#   p7  VAD filter      (0=off 1=on, default 1)
#   ch0..ch8 — input channel (single-select)
#   wsp_font_path  — path to selected .ttf/.otf
#   ai_text        — display name of selected font
#   rack['wsp_srt_enabled']  — bool custom prop (default True)
#   wsp_srt_path   — SRT output path
# =============================================================================

import os
import sys
import subprocess
import threading
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

# ── Colour palette ─────────────────────────────────────────────────────────────
_BG         = (0.01, 0.04, 0.02, 1.0)
_BORDER     = (0.06, 0.24, 0.12, 1.0)
_PANEL      = (0.01, 0.03, 0.02, 1.0)
_PANEL_SEL  = (0.04, 0.16, 0.08, 1.0)
_ACCENT     = (0.10, 0.90, 0.45, 1.0)
_ACCENT_DIM = (0.04, 0.30, 0.16, 1.0)
_TEXT       = (0.55, 0.95, 0.72, 1.0)
_TEXT_DIM   = (0.16, 0.42, 0.26, 1.0)
_TEXT_LABEL = (0.08, 0.22, 0.12, 1.0)
_GREEN      = (0.05, 0.80, 0.30, 1.0)
_AMBER      = (0.86, 0.53, 0.00, 1.0)
_RED        = (0.90, 0.12, 0.12, 1.0)
_WARN_BG    = (0.06, 0.04, 0.00, 1.0)
_WARN_BORDER= (0.55, 0.33, 0.00, 1.0)
_WARN_TEXT  = (0.86, 0.53, 0.00, 1.0)
_WARN_DIM   = (0.50, 0.28, 0.00, 1.0)

RACK_RAIL_H = 32
_READY      = "READY"
_PROCESSING = "PROCESSING"
_DONE       = "DONE"
_ERROR      = "ERROR"

_MODEL_LABELS = ["TINY", "BASE", "SMALL", "MEDIUM", "LARGE-V3"]
_MODEL_DESCS  = [
    "39M  fastest, English only",
    "74M  good for English (default)",
    "244M  good multilingual",
    "769M  high accuracy",
    "1550M  best accuracy, slow",
]

_LANG_LABELS = [
    "Auto-detect", "English", "German", "French", "Spanish",
    "Italian", "Japanese", "Chinese", "Russian", "Portuguese",
    "Dutch", "Korean", "Arabic", "Hindi", "Polish", "Swedish",
]

_STYLE_NAMES = ["B", "I", "U", "SH", "BOX"]
_STYLE_FLAGS = [1,   2,  4,   8,   16 ]


# ── System font scanner ────────────────────────────────────────────────────────
_font_cache    = None
_font_scan_run = False
_font_lock     = threading.Lock()

# Per-rack font scroll offset (ai_idx -> int)
_font_scroll   = {}


def _scan_fonts_worker():
    global _font_cache, _font_scan_run
    dirs = []
    if sys.platform == "win32":
        dirs = [
            os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         "Microsoft", "Windows", "Fonts"),
        ]
    elif sys.platform == "darwin":
        dirs = ["/Library/Fonts", "/System/Library/Fonts",
                os.path.expanduser("~/Library/Fonts")]
    else:
        dirs = ["/usr/share/fonts", "/usr/local/share/fonts",
                os.path.expanduser("~/.fonts"),
                os.path.expanduser("~/.local/share/fonts")]
    fonts = []
    seen  = set()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for f in sorted(files):
                if f.lower().endswith((".ttf", ".otf")):
                    full = os.path.join(root, f)
                    key  = f.lower()
                    if key not in seen:
                        seen.add(key)
                        fonts.append((os.path.splitext(f)[0], full))
    fonts.sort(key=lambda x: x[0].lower())
    with _font_lock:
        _font_cache    = fonts
        _font_scan_run = False


def get_system_fonts():
    global _font_scan_run
    with _font_lock:
        if _font_cache is not None:
            return _font_cache
        if not _font_scan_run:
            _font_scan_run = True
            threading.Thread(target=_scan_fonts_worker, daemon=True).start()
        return []


# ── Dependency check ───────────────────────────────────────────────────────────
_dep_cache = {"ok": False, "checked": False, "checking": False}
_dep_running = False


def _dep_worker():
    global _dep_running
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
                    cmd + ["-m", "pip", "show", "faster-whisper"],
                    capture_output=True, timeout=5)
                if r.returncode == 0:
                    ok = True
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"[WHISPER] dep check error: {e}")
    _dep_cache["ok"]       = ok
    _dep_cache["checked"]  = True
    _dep_cache["checking"] = False
    _dep_running           = False
    print(f"[WHISPER] dep check complete: {'found' if ok else 'not found'}")
    try:
        import bpy
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ('NODE_EDITOR', 'SEQUENCE_EDITOR'):
                    area.tag_redraw()
    except Exception:
        pass
def _check_dep():
    global _dep_running
    if _dep_cache["checked"]:
        return _dep_cache["ok"]
    if not _dep_running:
        _dep_running           = True
        _dep_cache["checking"] = True
        threading.Thread(target=_dep_worker, daemon=True).start()
    return False


# ── GPU helpers ────────────────────────────────────────────────────────────────
def _r(rx, ry, rw, rh, col):
    _draw_rect(rx, ry, rw, rh, col)


def _box(rx, ry, rw, rh, col):
    sh = gpu.shader.from_builtin("UNIFORM_COLOR")
    vs = [(rx, ry), (rx+rw, ry), (rx+rw, ry+rh), (rx, ry+rh), (rx, ry)]
    b  = batch_for_shader(sh, "LINE_STRIP", {"pos": vs})
    sh.bind(); sh.uniform_float("color", col); b.draw(sh)


def _pill(px, py, pw, ph, on, scale):
    bg  = (0.04, 0.20, 0.10, 1.0) if on else (0.01, 0.04, 0.02, 1.0)
    col = _ACCENT if on else _ACCENT_DIM
    _r(px, py, pw, ph, bg)
    _box(px, py, pw, ph, col)
    dr = ph * 0.36
    dx = (px + pw - dr * 2 - ph * 0.1) if on else (px + ph * 0.1)
    _draw_circle(dx + dr, py + ph * 0.5, dr, col)


def _sel_row(lx, sy, col_w, row_h, arr_w, val_str, scale):
    fs_v = max(1, int(9 * scale))
    nbw  = col_w - arr_w * 2 - 4 * scale
    nbx  = lx + arr_w + 2 * scale
    rax  = nbx + nbw + 2 * scale
    for bx, lbl in [(lx, "<"), (rax, ">")]:
        _r(bx, sy, arr_w, row_h, (0.03, 0.08, 0.04, 1.0))
        _box(bx, sy, arr_w, row_h, _ACCENT_DIM)
        tw = _text_width(lbl, fs_v)
        _draw_text(lbl, bx + arr_w/2 - tw/2, sy + row_h/2 - fs_v/2, fs_v, _ACCENT)
    _r(nbx, sy, nbw, row_h, _PANEL)
    _box(nbx, sy, nbw, row_h, _ACCENT_DIM)
    tw2 = _text_width(val_str, fs_v)
    _draw_text(val_str, nbx + nbw/2 - tw2/2, sy + row_h/2 - fs_v/2, fs_v, _ACCENT)


def _hdiv(lx, y, col_w, scale):
    sh = gpu.shader.from_builtin("UNIFORM_COLOR")
    b  = batch_for_shader(sh, "LINES", {"pos": [(lx, y), (lx + col_w, y)]})
    sh.bind(); sh.uniform_float("color", (0.04, 0.14, 0.06, 1.0)); b.draw(sh)


# ── Warning panel ──────────────────────────────────────────────────────────────
def _draw_warning(rx, ry, rw, rh, scale):
    mg   = 8 * scale
    bw   = rw - mg * 4
    bh   = min(rh * 0.80, 175 * scale)
    bx   = rx + (rw - bw) / 2
    by   = ry + (rh - bh) / 2
    _r(bx, by, bw, bh, _WARN_BG)
    _r(bx, by, 4 * scale, bh, _WARN_TEXT)
    _box(bx, by, bw, bh, _WARN_BORDER)

    tx   = bx + 14 * scale
    fs_h = max(1, int(9 * scale))
    fs_b = max(1, int(8 * scale))
    fs_s = max(1, int(7 * scale))
    lh   = fs_b + 5 * scale
    ty   = by + bh - fs_h - 8 * scale

    _draw_text("FASTER-WHISPER NOT INSTALLED", tx, ty, fs_h, _WARN_TEXT)
    ty -= lh * 1.5
    _draw_text("Run in your terminal:", tx, ty, fs_b, _WARN_DIM)
    ty -= lh * 1.2
    cw = bw - 28 * scale; ch = lh * 1.6 + 6 * scale; cx2 = tx; cy2 = ty - ch
    _r(cx2, cy2, cw, ch, (0.02, 0.01, 0.00, 1.0))
    _box(cx2, cy2, cw, ch, (0.44, 0.24, 0.00, 1.0))
    _draw_text("pip install faster-whisper",
               cx2 + 6 * scale, cy2 + ch / 2 - fs_b / 2, fs_b, _WARN_TEXT)
    ty = cy2 - lh * 1.2
    _draw_text("CUDA auto-used if available.  CPU also works.", tx, ty, fs_s, _WARN_DIM)
    ty -= lh
    _draw_text("Models download on first run (~150MB for base).", tx, ty, fs_s, _WARN_DIM)

    dw = min(140 * scale, bw * 0.38); dh = max(16 * scale, fs_s + 8 * scale)
    dx2 = bx + bw - dw - 12 * scale; dy2 = by + 8 * scale
    _r(dx2, dy2, dw, dh, (0.10, 0.06, 0.00, 1.0))
    _box(dx2, dy2, dw, dh, _WARN_BORDER)
    lbl = "DOCS  >"; tw_d = _text_width(lbl, fs_s)
    _draw_text(lbl, dx2 + dw / 2 - tw_d / 2,
               dy2 + dh / 2 - fs_s / 2, fs_s, _WARN_TEXT)


# ── Main draw ──────────────────────────────────────────────────────────────────
def _draw_whisper_body(rx, ry, rw, rh, rack, ai_idx, scale):
    rail_h   = RACK_RAIL_H * scale
    body_bot = ry
    body_top = ry + rh - rail_h
    body_h   = body_top - body_bot

    _r(rx, body_bot, rw, body_h, _BG)
    _box(rx, body_bot, rw, body_h, _BORDER)

    if not _check_dep():
        if _dep_cache["checking"]:
            # Still checking — show brief status instead of full warning
            fs = max(1, int(8 * scale))
            msg = "Checking Whisper installation..."
            from ui.mixer.draw_utils import text_width as _tw
            tw = _tw(msg, fs)
            _draw_text(msg, rx + rw/2 - tw/2, body_bot + body_h/2 - fs/2,
                       fs, (0.4, 0.5, 0.6, 1.0))
            return
        _draw_warning(rx, body_bot, rw, body_h, scale)
        return

    get_system_fonts()   # kick off scan if not done

    mg       = 8 * scale
    fs_lbl   = max(1, int(8 * scale))
    fs_val   = max(1, int(9 * scale))
    fs_sm    = max(1, int(7 * scale))
    row_h    = max(18 * scale, body_h * 0.105)
    arr_w    = max(14 * scale, row_h * 0.9)
    btn_gap  = 3 * scale
    fitem_h  = max(12 * scale, fs_sm + 4 * scale)

    # ── Read all params ────────────────────────────────────────────────────────
    status     = getattr(rack, "ai_status", _READY)
    model_idx  = max(0, min(int(getattr(rack, "p0", 1.0)), len(_MODEL_LABELS) - 1))
    lang_idx   = max(0, min(int(getattr(rack, "p1", 0.0)), len(_LANG_LABELS) - 1))
    mode_idx   = int(getattr(rack, "p2", 0.0))
    out_ch     = int(getattr(rack, "p3", 0.0))   # 1-based; 0 = not set
    font_size  = int(getattr(rack, "p4", 48.0))
    style_flags= int(getattr(rack, "p5", 0.0))
    pos_idx    = int(getattr(rack, "p6", 2.0))   # 0=top 1=mid 2=bot; default bot
    vad_on     = float(getattr(rack, "p7", 1.0)) > 0.5
    srt_on     = rack.get("wsp_srt_enabled", True)
    srt_path   = getattr(rack, "wsp_srt_path", "")
    sel_font_path = getattr(rack, "wsp_font_path", "")
    chunk_len  = getattr(rack, "wsp_chunk_length", 10)
    active_chs = [ci for ci in range(9) if getattr(rack, f"ch{ci}", False)]
    in_ch      = active_chs[0] if active_chs else -1

    status_col = {_READY: _TEXT_DIM, _PROCESSING: _AMBER,
                  _DONE: _GREEN, _ERROR: _RED}.get(status, _TEXT_DIM)

    # ── Status / footer bar ────────────────────────────────────────────────────
    sbar_h   = max(22 * scale, body_h * 0.07)
    sbar_y   = body_bot
    work_bot = sbar_y + sbar_h + 2 * scale
    work_top = body_top - 2 * scale
    work_h   = work_top - work_bot

    _r(rx, sbar_y, rw, sbar_h, (0.01, 0.03, 0.02, 1.0))
    _box(rx, sbar_y, rw, sbar_h, _BORDER)

    sbar_txt = (f"STATUS: {status}  |  MODEL: {_MODEL_LABELS[model_idx]}  |  "
                f"LANG: {_LANG_LABELS[lang_idx]}  |  OUT CH: {out_ch or '?'}  |  "
                f"SEG: {chunk_len}s  |  VAD: {'ON' if vad_on else 'OFF'}  |  "
                f"MODE: {'TRANSLATE->EN' if mode_idx else 'TRANSCRIBE'}")
    _draw_text(sbar_txt, rx + mg,
               sbar_y + sbar_h / 2 - fs_sm / 2, fs_sm, status_col)

    # TRANSCRIBE button (right end of status bar)
    tbtn_w = min(rw * 0.22, 150 * scale)
    tbtn_x = rx + rw - tbtn_w - mg
    tbtn_y = sbar_y + 2 * scale
    tbtn_h = sbar_h - 4 * scale
    if status == _PROCESSING:
        tb_bg = (0.08, 0.18, 0.04, 1.0); tb_col = _AMBER; tb_lbl = "PROCESSING..."
    else:
        tb_bg = (0.02, 0.18, 0.06, 1.0); tb_col = _ACCENT; tb_lbl = "TRANSCRIBE"
    _r(tbtn_x, tbtn_y, tbtn_w, tbtn_h, tb_bg)
    _box(tbtn_x, tbtn_y, tbtn_w, tbtn_h, tb_col)
    tw_tb = _text_width(tb_lbl, fs_sm)
    _draw_text(tb_lbl, tbtn_x + tbtn_w / 2 - tw_tb / 2,
               tbtn_y + tbtn_h / 2 - fs_sm / 2, fs_sm, tb_col)

    # ── 3-column grid ─────────────────────────────────────────────────────────
    col_w  = (rw - mg * 4) / 3
    col1_x = rx + mg
    col2_x = col1_x + col_w + mg
    col3_x = col2_x + col_w + mg

    # Vertical dividers
    sh_dv = gpu.shader.from_builtin("UNIFORM_COLOR")
    for dvx in [col1_x + col_w + mg * 0.5, col2_x + col_w + mg * 0.5]:
        dv = [(dvx, work_bot + 4 * scale), (dvx, work_top - 4 * scale)]
        db = batch_for_shader(sh_dv, "LINES", {"pos": dv})
        sh_dv.bind(); sh_dv.uniform_float("color", (0.04, 0.14, 0.06, 1.0)); db.draw(sh_dv)

    # ─ COL 1 ──────────────────────────────────────────────────────────────────
    ch_s  = min(20 * scale, (col_w - btn_gap * 8) / 9)
    y1    = work_top

    # Input channel
    y1 -= fs_lbl + 4 * scale
    _draw_text("INPUT CHANNEL", col1_x, y1, fs_lbl, _TEXT_LABEL)
    y1 -= ch_s + 4 * scale
    for ci in range(9):
        bx = col1_x + ci * (ch_s + btn_gap)
        sel = (ci == in_ch)
        _r(bx, y1, ch_s, ch_s, _PANEL_SEL if sel else _PANEL)
        _box(bx, y1, ch_s, ch_s, _ACCENT if sel else _ACCENT_DIM)
        lc = str(ci + 1); tw_c = _text_width(lc, fs_sm)
        _draw_text(lc, bx + ch_s/2 - tw_c/2, y1 + ch_s/2 - fs_sm/2, fs_sm,
                   _ACCENT if sel else _TEXT_LABEL)

    # Output channel
    y1 -= fs_lbl + 10 * scale
    _draw_text("OUTPUT CHANNEL (TEXT STRIPS)", col1_x, y1, fs_lbl, _TEXT_LABEL)
    y1 -= ch_s + 4 * scale
    for ci in range(9):
        bx = col1_x + ci * (ch_s + btn_gap)
        sel = (ci + 1 == out_ch)
        _r(bx, y1, ch_s, ch_s, _PANEL_SEL if sel else _PANEL)
        _box(bx, y1, ch_s, ch_s, _ACCENT if sel else _ACCENT_DIM)
        lc = str(ci + 1); tw_c = _text_width(lc, fs_sm)
        _draw_text(lc, bx + ch_s/2 - tw_c/2, y1 + ch_s/2 - fs_sm/2, fs_sm,
                   _ACCENT if sel else _TEXT_LABEL)

    # Divider + model
    y1 -= 8 * scale
    _hdiv(col1_x, y1, col_w, scale)
    y1 -= 6 * scale

    y1 -= fs_lbl
    _draw_text("MODEL SIZE", col1_x, y1, fs_lbl, _TEXT_LABEL)
    y1 -= row_h + 2 * scale
    _sel_row(col1_x, y1, col_w, row_h, arr_w, _MODEL_LABELS[model_idx], scale)
    y1 -= fs_sm + 4 * scale
    _draw_text(_MODEL_DESCS[model_idx], col1_x, y1, fs_sm, _TEXT_LABEL)

    # Max segment length (chunk_length) — controls subtitle density
    y1 -= fs_lbl + 8 * scale
    _draw_text("MAX SEGMENT LENGTH", col1_x, y1, fs_lbl, _TEXT_LABEL)
    y1 -= row_h + 2 * scale
    _sel_row(col1_x, y1, col_w, row_h, arr_w, f"{chunk_len}s", scale)
    y1 -= fs_sm + 4 * scale
    _draw_text("5s = more strips   30s = fewer strips", col1_x, y1, fs_sm, _TEXT_LABEL)

    # ─ COL 2 ──────────────────────────────────────────────────────────────────
    fonts = get_system_fonts()
    y2    = work_top

    y2 -= fs_lbl + 4 * scale
    _draw_text("FONT", col2_x, y2, fs_lbl, _TEXT_LABEL)

    flist_h  = min(work_h * 0.30, fitem_h * 7)
    scroll_btn_w = max(14 * scale, fitem_h * 0.9)
    flist_w  = col_w - scroll_btn_w - 2 * scale   # narrowed to fit scroll buttons
    y2      -= flist_h + 2 * scale
    flist_y  = y2
    _r(col2_x, flist_y, flist_w, flist_h, (0.01, 0.03, 0.01, 1.0))
    _box(col2_x, flist_y, flist_w, flist_h, _BORDER)

    # Scroll buttons — ▲ top, ▼ bottom, to the right of the list
    sbtn_x = col2_x + flist_w + 2 * scale
    sbtn_h = flist_h / 2 - 1 * scale
    _r(sbtn_x, flist_y + sbtn_h + 2 * scale, scroll_btn_w, sbtn_h, (0.02, 0.06, 0.03, 1.0))
    _box(sbtn_x, flist_y + sbtn_h + 2 * scale, scroll_btn_w, sbtn_h, _ACCENT_DIM)
    tw_up = _text_width("▲", fs_sm)
    _draw_text("▲", sbtn_x + scroll_btn_w/2 - tw_up/2,
               flist_y + flist_h*0.75 - fs_sm/2, fs_sm, _ACCENT)

    _r(sbtn_x, flist_y, scroll_btn_w, sbtn_h, (0.02, 0.06, 0.03, 1.0))
    _box(sbtn_x, flist_y, scroll_btn_w, sbtn_h, _ACCENT_DIM)
    tw_dn = _text_width("▼", fs_sm)
    _draw_text("▼", sbtn_x + scroll_btn_w/2 - tw_dn/2,
               flist_y + flist_h*0.25 - fs_sm/2, fs_sm, _ACCENT)

    if fonts:
        vis = max(1, int(flist_h / fitem_h))
        # Use explicit scroll offset if set, otherwise centre on selection
        if ai_idx in _font_scroll:
            scroll = max(0, min(_font_scroll[ai_idx], len(fonts) - vis))
        else:
            sel_fi = 0
            for fi, (fn, fp) in enumerate(fonts):
                if fp == sel_font_path:
                    sel_fi = fi; break
            scroll = max(0, min(sel_fi - vis // 2, len(fonts) - vis))
            _font_scroll[ai_idx] = scroll

        for slot in range(vis):
            fi = scroll + slot
            if fi >= len(fonts): break
            fn, fp = fonts[fi]
            iy  = flist_y + flist_h - (slot + 1) * fitem_h
            sel = (fp == sel_font_path)
            if sel:
                _r(col2_x + 1 * scale, iy, flist_w - 2 * scale, fitem_h, _PANEL_SEL)
            disp = fn[:22] if len(fn) <= 22 else fn[:21] + "\u2026"
            _draw_text(disp, col2_x + 5 * scale,
                       iy + fitem_h / 2 - fs_sm / 2, fs_sm,
                       _ACCENT if sel else _TEXT_DIM)

        # Scroll position indicator
        if len(fonts) > vis:
            pct = scroll / max(1, len(fonts) - vis)
            ind_h = max(4 * scale, sbtn_h * 0.3)
            ind_y = flist_y + sbtn_h * 0.1 + pct * (sbtn_h * 0.8 - ind_h)
            _r(sbtn_x + 2 * scale, ind_y, scroll_btn_w - 4 * scale, ind_h, _ACCENT_DIM)
    else:
        _draw_text("Scanning fonts...", col2_x + 5 * scale,
                   flist_y + flist_h / 2 - fs_sm / 2, fs_sm, _TEXT_LABEL)

    # Size
    y2 -= fs_lbl + 8 * scale
    _draw_text("SIZE", col2_x, y2, fs_lbl, _TEXT_LABEL)
    y2 -= row_h + 2 * scale
    sz_aw = max(14 * scale, row_h * 0.9)
    sz_vw = col_w - sz_aw * 2 - 4 * scale
    for bx2, lbl2 in [(col2_x, "-"), (col2_x + sz_aw + 2 * scale + sz_vw + 2 * scale, "+")]:
        _r(bx2, y2, sz_aw, row_h, (0.03, 0.08, 0.04, 1.0))
        _box(bx2, y2, sz_aw, row_h, _ACCENT_DIM)
        tw2 = _text_width(lbl2, fs_val)
        _draw_text(lbl2, bx2 + sz_aw/2 - tw2/2, y2 + row_h/2 - fs_val/2, fs_val, _ACCENT)
    sv_x = col2_x + sz_aw + 2 * scale
    _r(sv_x, y2, sz_vw, row_h, _PANEL)
    _box(sv_x, y2, sz_vw, row_h, _ACCENT_DIM)
    fs_s = str(font_size); tw_s = _text_width(fs_s, fs_val)
    _draw_text(fs_s, sv_x + sz_vw/2 - tw_s/2, y2 + row_h/2 - fs_val/2, fs_val, _ACCENT)

    # Style
    y2 -= fs_lbl + 8 * scale
    _draw_text("STYLE", col2_x, y2, fs_lbl, _TEXT_LABEL)
    y2 -= row_h + 2 * scale
    sty_bw = (col_w - 4 * btn_gap) / 5
    for si, (sname, sflag) in enumerate(zip(_STYLE_NAMES, _STYLE_FLAGS)):
        sbx = col2_x + si * (sty_bw + btn_gap)
        on  = bool(style_flags & sflag)
        _r(sbx, y2, sty_bw, row_h, _PANEL_SEL if on else _PANEL)
        _box(sbx, y2, sty_bw, row_h, _ACCENT if on else _ACCENT_DIM)
        tw_s2 = _text_width(sname, fs_sm)
        _draw_text(sname, sbx + sty_bw/2 - tw_s2/2, y2 + row_h/2 - fs_sm/2,
                   fs_sm, _ACCENT if on else _TEXT_LABEL)

    # Position
    y2 -= fs_lbl + 8 * scale
    _draw_text("POSITION", col2_x, y2, fs_lbl, _TEXT_LABEL)
    y2 -= row_h + 2 * scale
    pos_bw = (col_w - 2 * btn_gap) / 3
    for pi, plbl in enumerate(["TOP", "MID", "BOT"]):
        pbx = col2_x + pi * (pos_bw + btn_gap)
        on  = (pi == pos_idx)
        _r(pbx, y2, pos_bw, row_h, _PANEL_SEL if on else _PANEL)
        _box(pbx, y2, pos_bw, row_h, _ACCENT if on else _ACCENT_DIM)
        tw_p = _text_width(plbl, fs_sm)
        _draw_text(plbl, pbx + pos_bw/2 - tw_p/2, y2 + row_h/2 - fs_sm/2,
                   fs_sm, _ACCENT if on else _TEXT_LABEL)

    # ─ COL 3 ──────────────────────────────────────────────────────────────────
    pill_w = 30 * scale
    pill_h = 14 * scale
    pill_x = col3_x + col_w - pill_w
    y3     = work_top

    # Language
    y3 -= fs_lbl + 4 * scale
    _draw_text("AUDIO LANGUAGE", col3_x, y3, fs_lbl, _TEXT_LABEL)
    y3 -= row_h + 2 * scale
    _sel_row(col3_x, y3, col_w, row_h, arr_w, _LANG_LABELS[lang_idx], scale)
    y3 -= fs_sm + 3 * scale
    _draw_text("Set manually if auto-detect is wrong", col3_x, y3, fs_sm, _TEXT_LABEL)

    # Mode
    y3 -= fs_lbl + 8 * scale
    _draw_text("MODE", col3_x, y3, fs_lbl, _TEXT_LABEL)
    y3 -= row_h + 2 * scale
    mbw = (col_w - btn_gap) / 2
    for mi, mlbl in enumerate(["TRANSCRIBE", "TRANSLATE\u2192EN"]):
        mbx = col3_x + mi * (mbw + btn_gap)
        on  = (mi == mode_idx)
        _r(mbx, y3, mbw, row_h, _PANEL_SEL if on else _PANEL)
        _box(mbx, y3, mbw, row_h, _ACCENT if on else _ACCENT_DIM)
        tw_m = _text_width(mlbl, fs_sm)
        _draw_text(mlbl, mbx + mbw/2 - tw_m/2, y3 + row_h/2 - fs_sm/2,
                   fs_sm, _ACCENT if on else _TEXT_LABEL)
    y3 -= fs_sm + 3 * scale
    if mode_idx == 1:
        _draw_text("Any language \u2192 English only (Whisper limit)",
                   col3_x, y3, fs_sm, _AMBER)
    else:
        _draw_text("Outputs text in source language", col3_x, y3, fs_sm, _TEXT_LABEL)

    # Divider
    y3 -= 8 * scale
    _hdiv(col3_x, y3, col_w, scale)
    y3 -= 6 * scale

    # VAD filter
    y3 -= fs_lbl + 2 * scale
    _draw_text("VAD FILTER", col3_x, y3, fs_lbl, _TEXT_LABEL)
    _pill(pill_x, y3 - 1 * scale, pill_w, pill_h, vad_on, scale)
    y3 -= pill_h + 2 * scale
    _draw_text("Strips silence before transcribing", col3_x, y3, fs_sm, _TEXT_LABEL)

    # SRT export
    y3 -= fs_lbl + 10 * scale
    _draw_text("SRT EXPORT", col3_x, y3, fs_lbl, _TEXT_LABEL)
    _pill(pill_x, y3 - 1 * scale, pill_w, pill_h, srt_on, scale)
    y3 -= pill_h + 4 * scale
    if srt_on:
        path_disp = srt_path if srt_path else "(no path set)"
        max_c = int(col_w / max(1, fs_sm * 0.55))
        if len(path_disp) > max_c:
            path_disp = "\u2026" + path_disp[-(max_c - 1):]
        _r(col3_x, y3, col_w, fitem_h, (0.01, 0.04, 0.02, 1.0))
        _box(col3_x, y3, col_w, fitem_h, _ACCENT_DIM)
        _draw_text(path_disp, col3_x + 4 * scale,
                   y3 + fitem_h / 2 - fs_sm / 2, fs_sm, _TEXT_DIM)
        y3 -= fitem_h

    # Last run
    y3 -= fs_sm + 8 * scale
    _draw_text("LAST RUN", col3_x, y3, fs_sm, _TEXT_LABEL)
    y3 -= fs_sm + 2 * scale
    if status == _DONE:
        _draw_text("\u2713 Strips placed on timeline", col3_x, y3, fs_sm, _GREEN)
    elif status == _ERROR:
        _draw_text("\u2717 Failed \u2014 check console", col3_x, y3, fs_sm, _RED)
    else:
        _draw_text("No output yet", col3_x, y3, fs_sm, _TEXT_LABEL)
