"""
core/ai_whisper.py
==================
Backend for the Whisper speech-to-text rack.

Uses faster-whisper (pip install faster-whisper) running in system Python
as a subprocess.  Runner prints PROGRESS:N, RESULT:<json>, ERROR:<msg>.

Output: timestamped segments placed as VSE TEXT strips on the timeline,
styled with the font/size/position settings from the rack UI.
Optionally writes an .srt file alongside.

Params read from rack:
  p0  model index     (0=tiny 1=base 2=small 3=medium 4=large-v3)
  p1  language index  (0=auto, 1=en, 2=de, ...)
  p2  mode            (0=transcribe, 1=translate to EN)
  p3  output channel  (1-based; 0=auto)
  p4  font size       (default 48)
  p5  style flags     (1=bold 2=italic 4=underline 8=shadow 16=box)
  p6  position        (0=top 1=mid 2=bot)
  p7  VAD filter      (0=off 1=on)
  ch0..ch8            input channel single-select
  wsp_font_path       path to .ttf/.otf
  rack['wsp_srt_enabled']  bool
  wsp_srt_path        SRT output folder/filename
"""

import os
import json
import subprocess
import tempfile
import threading
import time
import bpy

_active_jobs    = {}
_cancel_flags   = {}
_pending_finish = {}

_PYTHON_CMD = None

MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"]

LANGUAGES = [
    ("auto", "Auto-detect"),
    ("en",   "English"),
    ("de",   "German"),
    ("fr",   "French"),
    ("es",   "Spanish"),
    ("it",   "Italian"),
    ("ja",   "Japanese"),
    ("zh",   "Chinese"),
    ("ru",   "Russian"),
    ("pt",   "Portuguese"),
    ("nl",   "Dutch"),
    ("ko",   "Korean"),
    ("ar",   "Arabic"),
    ("hi",   "Hindi"),
    ("pl",   "Polish"),
    ("sv",   "Swedish"),
]

_POSITION_Y = {0: 0.90, 1: 0.50, 2: 0.05}


def _find_system_python():
    global _PYTHON_CMD
    if _PYTHON_CMD:
        return _PYTHON_CMD
    for cmd in [["py", "-3.12"], ["py", "-3.11"], ["py", "-3.10"],
                ["python"], ["python3"]]:
        try:
            r = subprocess.run(
                cmd + ["-c", "import faster_whisper; print('ok')"],
                capture_output=True, timeout=6, text=True)
            if r.returncode == 0 and "ok" in r.stdout:
                _PYTHON_CMD = cmd
                print(f"[WHISPER] system Python: {' '.join(cmd)}")
                return _PYTHON_CMD
        except Exception:
            continue
    return None


def _get_runner_path():
    addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_dir, "ai_engines", "whisper", "whisper_runner.py")


def is_processing(ai_idx):
    t = _active_jobs.get(ai_idx)
    return t is not None and t.is_alive()


def _redraw_timer():
    for ai_idx, info in list(_pending_finish.items()):
        del _pending_finish[ai_idx]
        _apply_finish(ai_idx, info)
    return 0.25


def _write_srt(segments, srt_path):
    try:
        d = os.path.dirname(srt_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(srt_path, "w", encoding="utf-8") as f:
            idx = 1
            for seg in segments:
                text = seg.get("text", "").strip()
                if not text:
                    continue
                s = seg.get("start", 0.0)
                e = seg.get("end",   s + 0.5)
                def _t(sec):
                    h  = int(sec // 3600)
                    m  = int((sec % 3600) // 60)
                    sc = int(sec % 60)
                    ms = int((sec - int(sec)) * 1000)
                    return f"{h:02d}:{m:02d}:{sc:02d},{ms:03d}"
                f.write(f"{idx}\n{_t(s)} --> {_t(e)}\n{text}\n\n")
                idx += 1
        print(f"[WHISPER] SRT written: {srt_path}")
    except Exception as e:
        print(f"[WHISPER] SRT write error: {e}")


def _apply_finish(ai_idx, info):
    try:
        scene = bpy.data.scenes.get(info["scene_name"])
        if not scene:
            return
        ai_racks = getattr(scene, "pb_ai_racks", [])
        if ai_idx >= len(ai_racks):
            return
        rack = ai_racks[ai_idx]
        rack.ai_status = info["status"]

        if info["status"] != "DONE":
            return

        segments = info.get("segments", [])
        if not segments:
            print(f"[WHISPER] rack {ai_idx}: no segments returned")
            return

        seq = scene.sequence_editor
        if not seq:
            scene.sequence_editor_create()
            seq = scene.sequence_editor

        fps         = scene.render.fps / scene.render.fps_base
        frame_start = info.get("strip_frame_start", 0)
        subtitle_ch = info.get("subtitle_channel", 10)
        font_size   = int(info.get("font_size",    48))
        style_flags = int(info.get("style_flags",  0))
        pos_idx     = int(info.get("pos_idx",      2))
        font_path   = info.get("font_path",        "")
        srt_on      = info.get("srt_enabled",      False)
        srt_path    = info.get("srt_path",         "")

        pos_y = _POSITION_Y.get(pos_idx, 0.05)

        # Load font
        bl_font = None
        if font_path and os.path.exists(font_path):
            try:
                bl_font = bpy.data.fonts.load(font_path)
            except Exception as fe:
                print(f"[WHISPER] font load error: {fe}")

        # Write SRT
        if srt_on and srt_path:
            if os.path.isdir(srt_path):
                srt_path = os.path.join(srt_path, f"whisper_{ai_idx}.srt")
            elif not srt_path.lower().endswith(".srt"):
                srt_path += ".srt"
            _write_srt(segments, srt_path)

        placed = 0
        for seg in segments:
            start_s = seg.get("start", 0.0)
            end_s   = seg.get("end",   start_s + 0.5)
            text    = seg.get("text",  "").strip()
            if not text:
                continue
            sf  = frame_start + int(start_s * fps)
            ef  = frame_start + int(end_s   * fps)
            dur = max(1, ef - sf)
            sname = f"WSP_{ai_idx}_{placed}_{int(time.time()) % 100000}"
            try:
                t = seq.sequences.new_effect(
                    name=sname, type='TEXT', channel=subtitle_ch,
                    frame_start=sf, frame_end=sf + dur)
                t.text        = text
                t.font_size   = font_size
                t.location[1] = pos_y
                if hasattr(t, 'use_bold'):      t.use_bold      = bool(style_flags & 1)
                if hasattr(t, 'use_italic'):    t.use_italic    = bool(style_flags & 2)
                if hasattr(t, 'use_underline'): t.use_underline = bool(style_flags & 4)
                if hasattr(t, 'use_shadow'):    t.use_shadow    = bool(style_flags & 8)
                if hasattr(t, 'use_box'):       t.use_box       = bool(style_flags & 16)
                if bl_font and hasattr(t, 'font'):
                    t.font = bl_font
                placed += 1
            except Exception as e:
                print(f"[WHISPER] strip {placed} error: {e}")

        print(f"[WHISPER] rack {ai_idx}: placed {placed} strips on ch{subtitle_ch}")
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ('SEQUENCE_EDITOR', 'NODE_EDITOR'):
                    area.tag_redraw()

    except Exception as e:
        print(f"[WHISPER] _apply_finish error: {e}")
        import traceback; traceback.print_exc()


def transcribe_whisper(ai_idx, context):
    """Start Whisper transcription in a background thread."""
    if is_processing(ai_idx):
        print(f"[WHISPER] rack {ai_idx} already processing")
        return

    scene = context.scene
    if not scene:
        return
    ai_racks = getattr(scene, "pb_ai_racks", [])
    if ai_idx >= len(ai_racks):
        return

    rack       = ai_racks[ai_idx]
    python_cmd = _find_system_python()
    if not python_cmd:
        rack.ai_status = "ERROR"
        print("[WHISPER] ERROR: system Python with faster-whisper not found")
        return

    runner_path = _get_runner_path()
    if not os.path.exists(runner_path):
        rack.ai_status = "ERROR"
        print(f"[WHISPER] ERROR: runner not found at {runner_path}")
        return

    # Input channel
    active_chs = [ci + 1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
    if not active_chs:
        rack.ai_status = "ERROR"
        print("[WHISPER] ERROR: no input channel assigned")
        return
    src_ch = active_chs[0]

    seq = scene.sequence_editor
    src_strip = None
    if seq:
        for strip in seq.sequences_all:
            if strip.channel == src_ch and hasattr(strip, "sound"):
                src_strip = strip
                break
    if not src_strip:
        rack.ai_status = "ERROR"
        print(f"[WHISPER] ERROR: no audio strip on channel {src_ch}")
        return

    fps               = scene.render.fps / scene.render.fps_base
    strip_frame_start = src_strip.frame_final_start

    # Output channel
    out_ch = int(getattr(rack, "p3", 0.0))
    if out_ch < 1:
        occupied = {s.channel for s in seq.sequences_all} if seq else set()
        out_ch = src_ch + 1
        while out_ch in occupied:
            out_ch += 1
        out_ch = max(1, min(32, out_ch))

    # Params
    model_idx   = max(0, min(int(getattr(rack, "p0", 1.0)), len(MODEL_SIZES) - 1))
    model_str   = MODEL_SIZES[model_idx]
    lang_idx    = max(0, min(int(getattr(rack, "p1", 0.0)), len(LANGUAGES) - 1))
    lang_code, _ = LANGUAGES[lang_idx]
    if lang_code == "auto":
        lang_code = None
    mode_idx    = int(getattr(rack, "p2", 0.0))
    translate   = (mode_idx == 1)
    vad_on      = float(getattr(rack, "p7", 1.0)) > 0.5
    font_size   = int(getattr(rack, "p4", 40.0))
    style_flags = int(getattr(rack, "p5", 0.0))
    pos_idx     = int(getattr(rack, "p6", 2.0))
    font_path   = getattr(rack, "wsp_font_path", "")
    srt_enabled = rack.get("wsp_srt_enabled", True)
    srt_path    = getattr(rack, "wsp_srt_path", "")
    chunk_len   = getattr(rack, "wsp_chunk_length", 10)

    # Export to temp WAV
    tmp_dir    = tempfile.gettempdir()
    src_wav    = os.path.join(tmp_dir, f"pb_wsp_{ai_idx}_{int(time.time())}.wav")
    scene_name = scene.name

    try:
        seq_strips     = list(scene.sequence_editor.sequences_all) if scene.sequence_editor else []
        original_mutes = {}
        for s in seq_strips:
            if hasattr(s, 'mute'):
                original_mutes[s.name] = s.mute
                s.mute = (s.channel != src_ch)

        bpy.ops.sound.mixdown(
            filepath=src_wav, check_existing=False,
            relative_path=False, codec='PCM', container='WAV')

        for s in seq_strips:
            if hasattr(s, 'mute') and s.name in original_mutes:
                s.mute = original_mutes[s.name]

        if not os.path.exists(src_wav):
            raise RuntimeError("mixdown produced no file")

        import wave
        with wave.open(src_wav, 'r') as wf:
            dur = wf.getnframes() / wf.getframerate()
        print(f"[WHISPER] exported: {os.path.basename(src_wav)} ({dur:.2f}s)")

    except Exception as ex:
        rack.ai_status = "ERROR"
        print(f"[WHISPER] export error: {ex}")
        import traceback; traceback.print_exc()
        return

    args_file = os.path.join(tmp_dir, f"pb_wsp_{ai_idx}_args.json")
    with open(args_file, "w", encoding="utf-8") as f:
        json.dump({
            "src":          src_wav,
            "model":        model_str,
            "language":     lang_code,
            "translate":    translate,
            "vad":          vad_on,
            "chunk_length": chunk_len,
        }, f)

    rack.ai_status        = "PROCESSING"
    _cancel_flags[ai_idx] = False

    def _worker():
        try:
            proc = subprocess.Popen(
                python_cmd + [runner_path, "--args", args_file],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8")

            result_segs = []
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("PROGRESS:"):
                    try:
                        print(f"[WHISPER] rack {ai_idx}: {line.split(':',1)[1].strip()}%")
                    except Exception:
                        pass
                elif line.startswith("RESULT:"):
                    try:
                        result_segs = json.loads(line.split(":", 1)[1].strip())
                    except Exception as je:
                        print(f"[WHISPER] JSON parse error: {je}")
                elif line.startswith("ERROR:"):
                    print(f"[WHISPER] runner: {line}")
                    _pending_finish[ai_idx] = {
                        "status": "ERROR", "segments": [],
                        "scene_name": scene_name,
                        "strip_frame_start": strip_frame_start,
                        "subtitle_channel": out_ch,
                    }

            proc.wait()
            if proc.returncode != 0 and ai_idx not in _pending_finish:
                print(f"[WHISPER] runner failed rc={proc.returncode}: "
                      f"{proc.stderr.read()[-400:]}")
                _pending_finish[ai_idx] = {
                    "status": "ERROR", "segments": [],
                    "scene_name": scene_name,
                    "strip_frame_start": strip_frame_start,
                    "subtitle_channel": out_ch,
                }
            elif ai_idx not in _pending_finish:
                _pending_finish[ai_idx] = {
                    "status":            "DONE",
                    "segments":          result_segs,
                    "scene_name":        scene_name,
                    "strip_frame_start": strip_frame_start,
                    "subtitle_channel":  out_ch,
                    "font_size":         font_size,
                    "style_flags":       style_flags,
                    "pos_idx":           pos_idx,
                    "font_path":         font_path,
                    "srt_enabled":       srt_enabled,
                    "srt_path":          srt_path,
                }

        except Exception as e:
            print(f"[WHISPER] worker error: {e}")
            import traceback; traceback.print_exc()
            _pending_finish[ai_idx] = {
                "status": "ERROR", "segments": [],
                "scene_name": scene_name,
                "strip_frame_start": strip_frame_start,
                "subtitle_channel": out_ch,
            }
        finally:
            try:
                os.remove(args_file)
            except Exception:
                pass

    t = threading.Thread(target=_worker, name=f"Whisper_{ai_idx}", daemon=True)
    _active_jobs[ai_idx] = t
    t.start()

    if not bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.register(_redraw_timer, first_interval=0.25)

    print(f"[WHISPER] rack {ai_idx} started — model={model_str} "
          f"lang={lang_code or 'auto'} translate={translate} "
          f"vad={vad_on} outch={out_ch}")
