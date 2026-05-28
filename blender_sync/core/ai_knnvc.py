"""
core/ai_knnvc.py
================
Backend for the kNN-VC Voice Conversion rack.
"""

import os
import subprocess
import tempfile
import threading
import time
import bpy

_active_jobs    = {}
_cancel_flags   = {}
_pending_finish = {}

_PYTHON_CMD = None


def _find_system_python():
    global _PYTHON_CMD
    if _PYTHON_CMD:
        return _PYTHON_CMD
    try:
        from core.ai_python_finder import find_python_with as _fpw
        pkg = "torch"
        cmd = _fpw(pkg)
        if cmd:
            _PYTHON_CMD = cmd
            print(f"[KNNVC] system Python: {' '.join(cmd)}")
            return _PYTHON_CMD
    except Exception as _e:
        print(f"[KNNVC] finder error: {_e}")
    return None

def _get_runner_path():
    addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_dir, "ai_engines", "knnvc", "knnvc_runner.py")


def _get_patch_path():
    addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_dir, "ai_engines", "knnvc", "patch_knnvc.py")


def _get_voices_dir():
    addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_dir, "ai_engines", "knnvc", "voices")


def is_processing(ai_idx):
    t = _active_jobs.get(ai_idx)
    return t is not None and t.is_alive()


def cancel_knnvc(ai_idx):
    _cancel_flags[ai_idx] = True


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

            if info.get("preview_only"):
                # Preview-only: output is ready for playback but not yet on timeline.
                # Auto-start playback so the user hears it immediately.
                print(f"[KNNVC] rack {ai_idx}: preview ready, auto-playing")
                try:
                    from core.audio import play_oneshot
                    handle = play_oneshot(output_path)
                    _rack_preview_state[ai_idx] = handle
                except Exception as pe:
                    print(f"[KNNVC] auto-play error: {pe}")
            else:
                # Generate mode: copy to project folder then place the strip
                persistent_path = _persist_output(output_path, ai_idx)
                # Update the stored path so has_rack_output still works
                _rack_output_path[ai_idx] = persistent_path

                seq = scene.sequence_editor
                if not seq:
                    scene.sequence_editor_create()
                    seq = scene.sequence_editor

                active_chs = [ci+1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
                target_ch  = int(getattr(rack, "p3", 0.0)) or (active_chs[0]+1 if active_chs else 2)
                target_ch  = max(1, min(9, target_ch))

                place_frame = scene.frame_start
                strip_name  = f"kNNVC_{ai_idx}_{int(time.time()) % 100000}"
                seq.sequences.new_sound(
                    name=strip_name,
                    filepath=persistent_path,
                    channel=target_ch,
                    frame_start=place_frame,
                )
                print(f"[KNNVC] placed strip '{strip_name}' on ch{target_ch} at frame {place_frame}")

            # Redraw either way
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type in ('SEQUENCE_EDITOR','NODE_EDITOR'):
                        area.tag_redraw()
    except Exception as e:
        print(f"[KNNVC] _apply_finish error: {e}")
        import traceback; traceback.print_exc()


# Track the output path and playback handle for each rack's last conversion
_rack_output_path   = {}   # ai_idx -> wav path from most recent conversion
_rack_preview_state = {}   # ai_idx -> aud.Handle or None


def _get_persistent_output_dir():
    """Return a persistent output folder next to the saved .blend file.

    If the .blend file has not been saved yet, falls back to a 'knnvc_output'
    subfolder inside the addon directory so the file is at least not in the
    OS temp dir.  Returns the absolute path (created if necessary).
    """
    blend_path = bpy.data.filepath
    if blend_path:
        project_dir = os.path.dirname(os.path.abspath(blend_path))
        out_dir = os.path.join(project_dir, "knnvc_output")
    else:
        # Unsaved project — store next to the addon so it survives restarts
        addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out_dir = os.path.join(addon_dir, "knnvc_output")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _persist_output(tmp_wav, ai_idx):
    """Copy *tmp_wav* from the temp dir into the project's knnvc_output folder.

    Returns the new persistent path, or *tmp_wav* unchanged if the copy fails.
    The destination filename includes a human-readable timestamp so files
    accumulate safely rather than overwriting each other.
    """
    import shutil
    try:
        out_dir   = _get_persistent_output_dir()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        dest_name = f"knnvc_rack{ai_idx}_{timestamp}.wav"
        dest_path = os.path.join(out_dir, dest_name)
        shutil.copy2(tmp_wav, dest_path)
        print(f"[KNNVC] output saved to project folder: {dest_path}")
        return dest_path
    except Exception as e:
        print(f"[KNNVC] WARNING: could not copy to project folder ({e}), "
              f"using temp path — file may be lost on reboot")
        return tmp_wav


def set_rack_output(ai_idx, wav_path):
    """Called when a conversion completes — records the output path for preview."""
    _rack_output_path[ai_idx] = wav_path
    _rack_preview_state.pop(ai_idx, None)  # clear any old handle


def is_rack_previewing(ai_idx):
    """Return True if the conversion preview for this rack is currently playing."""
    handle = _rack_preview_state.get(ai_idx)
    if handle is None:
        return False
    try:
        return bool(handle.status)
    except Exception:
        return False


def has_rack_output(ai_idx):
    """Return True if this rack has a valid conversion output ready to preview."""
    path = _rack_output_path.get(ai_idx)
    return bool(path and os.path.exists(path))


def preview_knnvc(ai_idx, context):
    """Toggle preview of voice conversion output for this rack.

    Behaviour:
    - If already playing -> stop.
    - If conversion is in progress -> do nothing (already processing).
    - Otherwise -> always re-run conversion with preview_only=True so the
      result is always fresh. Never plays a cached file.
      _apply_finish will auto-play when done and NOT place on timeline.
    """
    # Stop if already playing
    if is_rack_previewing(ai_idx):
        handle = _rack_preview_state.get(ai_idx)
        if handle:
            try: handle.stop()
            except Exception: pass
        _rack_preview_state.pop(ai_idx, None)
        print(f"[KNNVC] preview stopped: rack={ai_idx}")
        return

    # If conversion is already running, do nothing — it will auto-play on finish
    if is_processing(ai_idx):
        print(f"[KNNVC] preview: conversion already in progress, waiting")
        return

    # Always re-run conversion in preview_only mode to guarantee fresh output.
    # _apply_finish will auto-play when done and NOT place on timeline.
    print(f"[KNNVC] preview: starting fresh conversion (preview only)")
    convert_knnvc(ai_idx, context, preview_only=True)


# Tracks which (ai_idx, voice_idx) is currently previewing and its handle
# Format: {ai_idx: {'voice_idx': int, 'handle': aud.Handle}}
_voice_preview_state = {}


def is_voice_previewing(ai_idx, voice_idx):
    """Return True if this voice card is currently playing a preview."""
    state = _voice_preview_state.get(ai_idx)
    if not state or state.get('voice_idx') != voice_idx:
        return False
    handle = state.get('handle')
    if handle is None:
        return False
    try:
        return bool(handle.status)   # False when playback finished
    except Exception:
        return False


def preview_voice_card(ai_idx, voice_idx, context):
    """Toggle preview of a reference voice card.
    If this card is already playing, stop it. Otherwise start it.
    """
    # If this exact card is already playing, stop it
    if is_voice_previewing(ai_idx, voice_idx):
        state = _voice_preview_state.get(ai_idx, {})
        handle = state.get('handle')
        if handle:
            try: handle.stop()
            except Exception: pass
        _voice_preview_state.pop(ai_idx, None)
        print(f"[KNNVC] voice preview stopped: rack={ai_idx} voice={voice_idx}")
        return

    # Stop any other card that might be playing on this rack
    state = _voice_preview_state.get(ai_idx, {})
    old_handle = state.get('handle')
    if old_handle:
        try: old_handle.stop()
        except Exception: pass
    _voice_preview_state.pop(ai_idx, None)

    try:
        from ui.racks.rack_knnvc import _discover_ref_voices
        voices = _discover_ref_voices(ai_idx)
    except Exception:
        voices = []
    if not voices or voice_idx >= len(voices):
        print(f"[KNNVC] voice card preview: index {voice_idx} out of range")
        return
    wav_path = voices[voice_idx][1]
    if not os.path.exists(wav_path):
        print(f"[KNNVC] voice card preview: file not found: {wav_path}")
        return
    try:
        from core.audio import play_oneshot
        handle = play_oneshot(wav_path)
        _voice_preview_state[ai_idx] = {'voice_idx': voice_idx, 'handle': handle}
        print(f"[KNNVC] voice preview: {os.path.basename(wav_path)}")
    except Exception as e:
        print(f"[KNNVC] voice preview error: {e}")


def add_voice_from_timeline(ai_idx, context):
    """
    Extract audio from the timeline channel stored in rack.p5,
    convert to 16kHz mono WAV via torchaudio subprocess,
    save to ai_engines/knnvc/voices/<name>.wav.
    rack['add_voice_name'] holds the user-entered name.
    """
    scene = context.scene
    if not scene:
        return
    ai_racks = getattr(scene, "pb_ai_racks", [])
    if ai_idx >= len(ai_racks):
        return
    rack = ai_racks[ai_idx]

    python_cmd = _find_system_python()
    if not python_cmd:
        print("[KNNVC] add_voice: system Python with torch not found")
        return

    # Source channel for extraction (p5, 0-based)
    add_ch  = int(getattr(rack, "p5", 0.0)) + 1  # convert to 1-based
    seq     = scene.sequence_editor
    src_wav      = None
    trim_start_s = None   # start time in seconds within the source file
    trim_end_s   = None   # end time in seconds within the source file
    if seq:
        fps = scene.render.fps / scene.render.fps_base
        for strip in seq.sequences_all:
            if strip.channel == add_ch and hasattr(strip, "sound"):
                src_wav = bpy.path.abspath(strip.sound.filepath)
                # Calculate the trimmed region using VSE in/out points
                # strip.frame_offset_start = how many frames into the source the strip starts
                # strip.frame_final_duration = how many frames long the strip is in the timeline
                trim_start_s = strip.frame_offset_start / fps
                trim_end_s   = trim_start_s + (strip.frame_final_duration / fps)
                print(f"[KNNVC] add_voice: trim {trim_start_s:.2f}s -> {trim_end_s:.2f}s "
                      f"(offset={strip.frame_offset_start}, dur={strip.frame_final_duration})")
                break
    if not src_wav or not os.path.exists(src_wav):
        print(f"[KNNVC] add_voice: no audio strip on channel {add_ch}")
        return

    # Voice name — use stored value but never write fallback back to rack
    stored_name = rack.get('add_voice_name', None)
    name = str(stored_name).strip() if stored_name else ""
    if not name:
        name = f"CH{add_ch}Sample"
    # Sanitise filename
    safe_name = "".join(c if c.isalnum() or c in "_- " else "_" for c in name).strip()
    if not safe_name:
        safe_name = f"CH{add_ch}Sample"

    voices_dir  = _get_voices_dir()
    os.makedirs(voices_dir, exist_ok=True)
    output_path = os.path.join(voices_dir, safe_name + ".wav")

    rack['add_voice_busy'] = True

    def _worker():
        try:
            # Write paths to a JSON args file to avoid quoting/apostrophe issues
            import json
            args_file = os.path.join(tempfile.gettempdir(),
                                     f"pb_knnvc_add_{ai_idx}.json")
            with open(args_file, 'w', encoding='utf-8') as _af:
                json.dump({
                    'src': src_wav,
                    'out': output_path,
                    'trim_start': trim_start_s,
                    'trim_end':   trim_end_s,
                }, _af)

            convert_script = (
                "import json, torchaudio, os\n"
                f"args = json.load(open({repr(args_file)}, encoding=\'utf-8\'))\n"
                "wav, sr = torchaudio.load(args[\'src\'])\n"
                "# Apply VSE trim if specified\n"
                "ts = args.get(\'trim_start\'); te = args.get(\'trim_end\')\n"
                "if ts is not None and te is not None:\n"
                "    s = max(0, int(ts * sr)); e = min(wav.shape[-1], int(te * sr))\n"
                "    wav = wav[:, s:e]\n"
                "if wav.shape[0] > 1: wav = wav.mean(dim=0, keepdim=True)\n"
                "if sr != 16000: wav = torchaudio.functional.resample(wav, sr, 16000)\n"
                "torchaudio.save(args[\'out\'], wav, 16000)\n"
                f"os.remove({repr(args_file)})\n"
                "print(\'DONE\')\n"
            )
            r = subprocess.run(
                python_cmd + ["-c", convert_script],
                capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and "DONE" in r.stdout:
                print(f"[KNNVC] add_voice: saved '{safe_name}.wav' to voices/")
            else:
                print(f"[KNNVC] add_voice: conversion failed: {r.stderr[-300:]}")
        except Exception as e:
            print(f"[KNNVC] add_voice error: {e}")
        finally:
            try:
                ai_racks2 = getattr(bpy.data.scenes.get(scene.name), "pb_ai_racks", [])
                if ai_idx < len(ai_racks2):
                    ai_racks2[ai_idx]['add_voice_busy'] = False
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True, name=f"kNNVC_add_{ai_idx}")
    t.start()
    print(f"[KNNVC] add_voice: extracting CH{add_ch} -> '{safe_name}.wav'")


def convert_knnvc(ai_idx, context, preview_only=False):
    """Start kNN-VC voice conversion in a background thread.
    preview_only=True: process audio but don't place on timeline (for PREVIEW button).
    preview_only=False: process and place on timeline (GENERATE/CONVERT button).
    """
    if is_processing(ai_idx):
        print(f"[KNNVC] rack {ai_idx} already processing")
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
        print("[KNNVC] ERROR: system Python with torch not found")
        return

    runner_path = _get_runner_path()
    if not os.path.exists(runner_path):
        rack.ai_status = "ERROR"
        print(f"[KNNVC] ERROR: runner not found at {runner_path}")
        return

    active_chs = [ci+1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
    if not active_chs:
        rack.ai_status = "ERROR"
        print("[KNNVC] ERROR: no source channel assigned in rail")
        return
    src_ch  = active_chs[0]
    seq     = scene.sequence_editor
    src_wav = None
    if seq:
        for strip in seq.sequences_all:
            if strip.channel == src_ch and hasattr(strip, "sound"):
                src_wav = bpy.path.abspath(strip.sound.filepath)
                break
    if not src_wav or not os.path.exists(src_wav):
        rack.ai_status = "ERROR"
        print(f"[KNNVC] ERROR: no audio on channel {src_ch}")
        return

    try:
        from ui.racks.rack_knnvc import _discover_ref_voices
        voices = _discover_ref_voices(ai_idx)
    except Exception:
        voices = []
    sel_idx = int(getattr(rack,"p4",0.0)) % max(1,len(voices)) if voices else -1
    ref_wav = voices[sel_idx][1] if sel_idx >= 0 and voices else None
    if not ref_wav or not os.path.exists(ref_wav):
        rack.ai_status = "ERROR"
        print("[KNNVC] ERROR: no reference voice selected or file missing")
        return

    topk     = int(2 + float(getattr(rack,"p0",0.5))*6)
    ref_secs = int(10 + float(getattr(rack,"p1",0.5))*50)

    tmp_dir    = tempfile.gettempdir()
    output_wav = os.path.join(tmp_dir, f"pb_knnvc_{ai_idx}_{int(time.time())}.wav")
    scene_name = scene.name
    patch_path = _get_patch_path()

    rack.ai_status        = "PROCESSING"
    _cancel_flags[ai_idx] = False

    def _worker():
        try:
            cmd = python_cmd + [
                runner_path,
                "--src",      src_wav,
                "--ref",      ref_wav,
                "--output",   output_wav,
                "--topk",     str(topk),
                "--ref_secs", str(ref_secs),
                "--patch",    patch_path,
            ]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8")

            for line in proc.stdout:
                line = line.strip()
                if line.startswith("PROGRESS:"):
                    try: print(f"[KNNVC] rack {ai_idx}: {line.split(':')[1]}%")
                    except Exception: pass
                elif line.startswith("DONE:"):
                    out = line.split(":",1)[1].strip()
                    set_rack_output(ai_idx, out)
                    _pending_finish[ai_idx] = {
                        "status": "DONE", "output_path": out,
                        "scene_name": scene_name, "preview_only": preview_only}
                elif line.startswith("ERROR:"):
                    print(f"[KNNVC] {line}")
                    _pending_finish[ai_idx] = {
                        "status": "ERROR", "output_path": None, "scene_name": scene_name}

            proc.wait()
            if proc.returncode != 0 and ai_idx not in _pending_finish:
                stderr = proc.stderr.read()
                print(f"[KNNVC] runner failed: {stderr}")
                _pending_finish[ai_idx] = {
                    "status": "ERROR", "output_path": None, "scene_name": scene_name}
        except Exception as e:
            print(f"[KNNVC] worker error: {e}")
            _pending_finish[ai_idx] = {
                "status": "ERROR", "output_path": None, "scene_name": scene_name}

    t = threading.Thread(target=_worker, name=f"kNNVC_{ai_idx}", daemon=True)
    _active_jobs[ai_idx] = t
    t.start()

    if not bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.register(_redraw_timer, first_interval=0.25)
    print(f"[KNNVC] rack {ai_idx} conversion started")
