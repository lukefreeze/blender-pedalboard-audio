"""
core/ai_voicefixer.py
=====================
Backend for the VoiceFixer speech restoration rack.

Uses VoiceFixer (neural vocoder-based general speech restoration).
Handles noise, reverberation, low resolution and clipping in one pass.

System Python required: voicefixer, torch
Models: auto-downloaded on first use (~625MB cached to ~/.cache/voicefixer)

Modes:
    0 = Standard (recommended default)
    1 = Smooth (preprocessing step, alters voice slightly)
    2 = Aggressive (best for seriously degraded audio)
"""

import os
import glob
import subprocess
import tempfile
import threading
import time
import bpy

_active_jobs      = {}   # ai_idx -> Thread
_cancel_flags     = {}   # ai_idx -> bool
_pending_finish   = {}   # ai_idx -> info dict
_rack_output_path = {}   # ai_idx -> output wav path (current session only)
_rack_preview_state = {} # ai_idx -> aud.Handle

_PYTHON_CMD = None


def _find_system_python():
    global _PYTHON_CMD
    if _PYTHON_CMD:
        return _PYTHON_CMD
    candidates = [["py","-3.12"],["py","-3.11"],["py","-3.10"],["python"],["python3"]]
    for cmd in candidates:
        try:
            r = subprocess.run(cmd + ["-c", "import voicefixer; print('ok')"],
                               capture_output=True, timeout=6, text=True)
            if r.returncode == 0 and "ok" in r.stdout:
                _PYTHON_CMD = cmd
                print(f"[VOICEFIXER] system Python: {' '.join(cmd)}")
                return _PYTHON_CMD
        except Exception:
            continue
    return None


def _get_runner_path():
    addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_dir, "ai_engines", "voicefixer", "voicefixer_runner.py")


def is_processing(ai_idx):
    t = _active_jobs.get(ai_idx)
    return t is not None and t.is_alive()


def set_rack_output(ai_idx, wav_path):
    _rack_output_path[ai_idx] = wav_path
    _rack_preview_state.pop(ai_idx, None)


def has_rack_output(ai_idx):
    path = _rack_output_path.get(ai_idx)
    return bool(path and os.path.exists(path))


def is_rack_previewing(ai_idx):
    handle = _rack_preview_state.get(ai_idx)
    if handle is None:
        return False
    try:
        return bool(handle.status)
    except Exception:
        return False


def preview_voicefixer(ai_idx, context):
    """Toggle playback of the last VoiceFixer output."""
    if is_rack_previewing(ai_idx):
        handle = _rack_preview_state.get(ai_idx)
        if handle:
            try: handle.stop()
            except Exception: pass
        _rack_preview_state.pop(ai_idx, None)
        print(f"[VOICEFIXER] preview stopped: rack={ai_idx}")
        return

    wav_path = _rack_output_path.get(ai_idx)
    if not wav_path or not os.path.exists(wav_path):
        print(f"[VOICEFIXER] preview: no output for rack {ai_idx} — run ENHANCE first")
        return
    try:
        from core.audio import play_oneshot
        handle = play_oneshot(wav_path)
        _rack_preview_state[ai_idx] = handle
        print(f"[VOICEFIXER] preview: {os.path.basename(wav_path)}")
    except Exception as e:
        print(f"[VOICEFIXER] preview error: {e}")


def _redraw_timer():
    for ai_idx, info in list(_pending_finish.items()):
        del _pending_finish[ai_idx]
        _apply_finish(ai_idx, info)
    return 0.25


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

        if info["status"] == "DONE" and info.get("output_path"):
            output_path = info["output_path"]
            seq = scene.sequence_editor
            if not seq:
                scene.sequence_editor_create()
                seq = scene.sequence_editor

            active_chs = [ci+1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
            target_ch  = int(getattr(rack, "p3", 0.0)) or (active_chs[0]+1 if active_chs else 2)
            target_ch  = max(1, min(9, target_ch))

            place_frame = info.get("strip_frame_start", scene.frame_current)
            strip_name  = f"VF_{ai_idx}_{int(time.time()) % 100000}"
            seq.sequences.new_sound(
                name=strip_name,
                filepath=output_path,
                channel=target_ch,
                frame_start=place_frame,
            )
            print(f"[VOICEFIXER] placed strip '{strip_name}' on ch{target_ch} at frame {place_frame}")

            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type in ('SEQUENCE_EDITOR', 'NODE_EDITOR'):
                        area.tag_redraw()
    except Exception as e:
        print(f"[VOICEFIXER] _apply_finish error: {e}")
        import traceback; traceback.print_exc()


def enhance_voicefixer(ai_idx, context):
    """Start VoiceFixer restoration in a background thread."""
    if is_processing(ai_idx):
        print(f"[VOICEFIXER] rack {ai_idx} already processing")
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
        print("[VOICEFIXER] ERROR: system Python with voicefixer not found")
        return

    runner_path = _get_runner_path()
    if not os.path.exists(runner_path):
        rack.ai_status = "ERROR"
        print(f"[VOICEFIXER] ERROR: runner not found at {runner_path}")
        return

    # Source audio from assigned channel
    active_chs = [ci+1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
    if not active_chs:
        rack.ai_status = "ERROR"
        print("[VOICEFIXER] ERROR: no source channel assigned in rail")
        return
    src_ch = active_chs[0]
    seq    = scene.sequence_editor
    src_strip = None
    if seq:
        for strip in seq.sequences_all:
            if strip.channel == src_ch and hasattr(strip, "sound"):
                src_strip = strip
                break
    if not src_strip:
        rack.ai_status = "ERROR"
        print(f"[VOICEFIXER] ERROR: no audio strip on channel {src_ch}")
        return

    src_file = bpy.path.abspath(src_strip.sound.filepath)
    if not os.path.exists(src_file):
        rack.ai_status = "ERROR"
        print(f"[VOICEFIXER] ERROR: source file not found: {src_file}")
        return

    # Get strip timeline position for placing the output later
    fps             = scene.render.fps / scene.render.fps_base
    strip_frame_start = src_strip.frame_final_start  # timeline position
    trim_start_s    = src_strip.frame_offset_start / fps
    trim_end_s      = trim_start_s + (src_strip.frame_final_duration / fps)

    # Mode: p0 stores 0/1/2
    mode = int(getattr(rack, "p0", 0.0))

    tmp_dir    = tempfile.gettempdir()
    # Export trimmed region to a temp WAV first
    src_wav    = os.path.join(tmp_dir, f"pb_vf_{ai_idx}_src_{int(time.time())}.wav")
    output_wav = os.path.join(tmp_dir, f"pb_vf_{ai_idx}_{int(time.time())}.wav")
    scene_name = scene.name

    # Use Blender's Render Audio (sound.mixdown) — same as File > Render Audio.
    # Mute all channels except the source, run mixdown at scene length,
    # then restore mutes. This exports exactly what's in the sequence.
    try:
        seq_strips     = list(scene.sequence_editor.sequences_all) if scene.sequence_editor else []
        original_mutes = {}
        for s in seq_strips:
            if hasattr(s, 'mute'):
                original_mutes[s.name] = s.mute
                s.mute = (s.channel != src_ch)

        bpy.ops.sound.mixdown(
            filepath=src_wav,
            check_existing=False,
            relative_path=False,
            codec='PCM',
            container='WAV',
        )

        for s in seq_strips:
            if hasattr(s, 'mute') and s.name in original_mutes:
                s.mute = original_mutes[s.name]

        if not os.path.exists(src_wav):
            raise RuntimeError("mixdown produced no file")

        import wave
        with wave.open(src_wav, 'r') as wf:
            dur = wf.getnframes() / wf.getframerate()
        print(f"[VOICEFIXER] exported via Render Audio: {os.path.basename(src_wav)} ({dur:.2f}s)")

    except Exception as export_err:
        rack.ai_status = "ERROR"
        print(f"[VOICEFIXER] ERROR: export failed: {export_err}")
        import traceback; traceback.print_exc()
        return
    rack.ai_status        = "PROCESSING"
    _cancel_flags[ai_idx] = False

    def _worker():
        try:
            cmd = python_cmd + [
                runner_path,
                "--src",    src_wav,
                "--output", output_wav,
                "--mode",   str(mode),
            ]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8")

            for line in proc.stdout:
                line = line.strip()
                if line.startswith("PROGRESS:"):
                    try: print(f"[VOICEFIXER] rack {ai_idx}: {line.split(':')[1]}%")
                    except Exception: pass
                elif line.startswith("DONE:"):
                    out = line.split(":", 1)[1].strip()
                    set_rack_output(ai_idx, out)
                    _pending_finish[ai_idx] = {
                        "status": "DONE", "output_path": out,
                        "scene_name": scene_name,
                        "strip_frame_start": strip_frame_start}
                elif line.startswith("ERROR:"):
                    print(f"[VOICEFIXER] {line}")
                    _pending_finish[ai_idx] = {
                        "status": "ERROR", "output_path": None, "scene_name": scene_name}

            proc.wait()
            if proc.returncode != 0 and ai_idx not in _pending_finish:
                stderr = proc.stderr.read()
                print(f"[VOICEFIXER] runner failed: {stderr}")
                _pending_finish[ai_idx] = {
                    "status": "ERROR", "output_path": None, "scene_name": scene_name}
        except Exception as e:
            print(f"[VOICEFIXER] worker error: {e}")
            _pending_finish[ai_idx] = {
                "status": "ERROR", "output_path": None, "scene_name": scene_name}

    t = threading.Thread(target=_worker, name=f"VoiceFixer_{ai_idx}", daemon=True)
    _active_jobs[ai_idx] = t
    t.start()

    if not bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.register(_redraw_timer, first_interval=0.25)
    print(f"[VOICEFIXER] rack {ai_idx} enhancement started (mode {mode})")
