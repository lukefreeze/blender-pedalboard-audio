"""
core/ai_knnvc.py
================
Backend for the kNN-VC Voice Conversion rack.
"""

import os
import glob
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
    candidates = [["py","-3.12"],["py","-3.11"],["py","-3.10"],["python"],["python3"]]
    for cmd in candidates:
        try:
            r = subprocess.run(cmd+["-c","import torch, torchaudio; print('ok')"],
                               capture_output=True, timeout=5, text=True)
            if r.returncode == 0 and "ok" in r.stdout:
                _PYTHON_CMD = cmd
                print(f"[KNNVC] system Python: {' '.join(cmd)}")
                return _PYTHON_CMD
        except Exception:
            continue
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
            seq = scene.sequence_editor
            if not seq:
                scene.sequence_editor_create()
                seq = scene.sequence_editor

            # Read active channels from rack attributes
            active_chs = [ci+1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
            target_ch  = int(getattr(rack, "p3", 0.0)) or (active_chs[0]+1 if active_chs else 2)
            target_ch  = max(1, min(9, target_ch))

            place_frame = scene.frame_current
            strip_name  = f"kNNVC_{ai_idx}_{int(time.time()) % 100000}"
            seq.sequences.new_sound(
                name=strip_name,
                filepath=output_path,
                channel=target_ch,
                frame_start=place_frame,
            )
            print(f"[KNNVC] placed strip '{strip_name}' on ch{target_ch} at frame {place_frame}")

            # Redraw
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
    """Toggle playback of the last conversion output for this rack."""
    # Stop if already playing
    if is_rack_previewing(ai_idx):
        handle = _rack_preview_state.get(ai_idx)
        if handle:
            try: handle.stop()
            except Exception: pass
        _rack_preview_state.pop(ai_idx, None)
        print(f"[KNNVC] preview stopped: rack={ai_idx}")
        return

    wav_path = _rack_output_path.get(ai_idx)
    if not wav_path or not os.path.exists(wav_path):
        print(f"[KNNVC] preview: no output for rack {ai_idx} — run CONVERT first")
        return
    try:
        from core.audio import play_oneshot
        handle = play_oneshot(wav_path)
        _rack_preview_state[ai_idx] = handle
        print(f"[KNNVC] preview: {os.path.basename(wav_path)}")
    except Exception as e:
        print(f"[KNNVC] preview error: {e}")


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


def convert_knnvc(ai_idx, context):
    """Start kNN-VC voice conversion in a background thread."""
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
                        "status": "DONE", "output_path": out, "scene_name": scene_name}
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
