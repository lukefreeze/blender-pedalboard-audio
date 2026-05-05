# =============================================================================
# core/ai_deepfilternet.py
# Addon-side caller for the DeepFilterNet3 subprocess runner.
#
# Responsibilities:
#   - Find the correct pre-built executable for the current OS/arch
#   - Extract raw audio from the assigned VSE channel to a temp WAV
#   - Launch the runner in a background thread (Blender stays responsive)
#   - Stream PROGRESS updates back to the rack's ai_status
#   - On completion, store output waveform data and update VSE strip
# =============================================================================

import os
import sys
import subprocess
import threading
import tempfile
import wave
import struct
import math

import bpy

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_ADDON_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENGINES_DIR = os.path.join(_ADDON_DIR, "ai_engines", "deepfilternet")
_MODELS_DIR  = os.path.join(_ENGINES_DIR, "models")

def _get_runner_path():
    """Return the path to the correct pre-built runner executable for this OS."""
    import platform
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        subfolder = "win_x64"
        exe_name  = "deepfilter_runner.exe"
    elif system == "darwin":
        subfolder = "macos_arm" if "arm" in machine else "macos_x64"
        exe_name  = "deepfilter_runner"
    elif system == "linux":
        subfolder = "linux_x64"
        exe_name  = "deepfilter_runner"
    else:
        return None

    path = os.path.join(_ENGINES_DIR, subfolder, exe_name)
    return path if os.path.exists(path) else None


def _models_exist():
    """Check that all three ONNX model files are present."""
    required = ["enc.onnx", "erb_dec.onnx", "df_dec.onnx", "config.ini"]
    return all(os.path.exists(os.path.join(_MODELS_DIR, f)) for f in required)


# ---------------------------------------------------------------------------
# Audio extraction — pull raw WAV from VSE strips for the assigned channel
# ---------------------------------------------------------------------------
def _extract_channel_wav(channel_idx, out_path):
    """
    Extract all audio from VSE channel channel_idx into a WAV file.
    Uses aud.Sound.data() to get raw PCM numpy array, then writes
    WAV manually — avoids the broken aud.write() path for MP3 sources.
    Returns (sample_rate, n_channels) or raises on failure.
    """
    import aud
    import numpy as np

    scene = bpy.context.scene
    if not scene or not scene.sequence_editor:
        raise RuntimeError("No scene or sequence editor")

    fps       = scene.render.fps / scene.render.fps_base
    seq_start = scene.frame_start / fps
    seq_end   = scene.frame_end   / fps

    strips = sorted(
        [s for s in scene.sequence_editor.sequences_all
         if s.type == "SOUND" and s.sound
         and (s.channel - 1) == channel_idx],
        key=lambda s: s.frame_final_start
        if hasattr(s, 'frame_final_start') else s.frame_start
    )
    if not strips:
        raise RuntimeError(f"No audio strips on channel {channel_idx + 1}")

    # Get sample rate and channels from first strip
    first_path = bpy.path.abspath(strips[0].sound.filepath)
    spec       = aud.Sound.file(first_path).specs
    TARGET_SR  = int(spec[0])
    TARGET_NCH = min(int(spec[1]), 2)   # cap at stereo

    # Total duration of the sequence range
    total_dur_s   = seq_end - seq_start
    total_samples = max(1, int(total_dur_s * TARGET_SR))
    audio_out     = np.zeros((total_samples, TARGET_NCH), dtype=np.float32)

    for strip in strips:
        try:
            filepath   = bpy.path.abspath(strip.sound.filepath)
            # Where this strip starts and ends on the timeline (seconds)
            strip_tl_start = strip.frame_final_start / fps if hasattr(strip, 'frame_final_start') else strip.frame_start / fps
            strip_tl_end   = strip.frame_final_end   / fps

            vis_start = max(strip_tl_start, seq_start)
            vis_end   = min(strip_tl_end,   seq_end)
            if vis_end <= vis_start:
                continue

            # Offset into the source file
            file_offset = getattr(strip, 'frame_offset_start', 0) / fps
            src_start   = file_offset + (vis_start - strip_tl_start)
            src_end     = src_start + (vis_end - vis_start)

            # Read via aud.Sound.data() — returns numpy float32 array
            snd   = aud.Sound.file(filepath).limit(src_start, src_end)
            # Resample and rechannelise to match target
            snd   = snd.resample(TARGET_SR, False).rechannel(TARGET_NCH)
            data  = np.array(snd.data(), dtype=np.float32)

            if data.ndim == 1:
                data = data.reshape(-1, 1)
                if TARGET_NCH == 2:
                    data = np.repeat(data, 2, axis=1)

            # Write into the output buffer at the correct timeline offset
            buf_start = int((vis_start - seq_start) * TARGET_SR)
            buf_end   = buf_start + len(data)
            buf_end   = min(buf_end, total_samples)
            data      = data[:buf_end - buf_start]
            audio_out[buf_start:buf_end] += data

        except Exception as e:
            print(f"[DNF] strip '{strip.name}' extract failed: {e}")
            continue

    # Clip and write as 16-bit WAV using Python's wave module
    audio_clip = np.clip(audio_out, -1.0, 1.0)
    s16        = (audio_clip * 32767.0).astype(np.int16)

    with wave.open(out_path, 'wb') as wf:
        wf.setnchannels(TARGET_NCH)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_SR)
        wf.writeframes(s16.tobytes())

    print(f"[DNF] wrote {len(s16)} frames @ {TARGET_SR}Hz {TARGET_NCH}ch → {out_path}")
    return TARGET_SR, TARGET_NCH


def _wav_to_waveform(wav_path, n_slots=80):
    """
    Read a WAV file and return a list of n_slots RMS amplitude values (0-1).
    Used to populate the waveform display in the rack UI.
    """
    import numpy as np
    try:
        with wave.open(wav_path, 'rb') as wf:
            sr    = wf.getframerate()
            nch   = wf.getnchannels()
            sw    = wf.getsampwidth()
            nfr   = wf.getnframes()
            raw   = wf.readframes(nfr)
        if sw == 2:
            s = (np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                 / 32768.0)
        else:
            s = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
        if nch > 1:
            s = s.reshape(-1, nch).mean(axis=1)
        slot_size = max(1, len(s) // n_slots)
        result = []
        for i in range(n_slots):
            chunk = s[i*slot_size:(i+1)*slot_size]
            rms   = float(np.sqrt(np.mean(chunk**2))) if len(chunk) > 0 else 0.0
            result.append(min(1.0, rms * 4.0))   # scale up for visibility
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Active processing jobs — keyed by ai_rack_idx
# ---------------------------------------------------------------------------
_active_jobs = {}   # ai_idx -> threading.Thread


def is_processing(ai_idx):
    t = _active_jobs.get(ai_idx)
    return t is not None and t.is_alive()


def cancel_processing(ai_idx):
    """Mark a job as cancelled — the thread will check this flag."""
    _cancel_flags[ai_idx] = True

_cancel_flags = {}


# ---------------------------------------------------------------------------
# Main process function — called from handle_ai_rack_click
# ---------------------------------------------------------------------------
def process_deepfilternet(ai_idx, context):
    """
    Launch DeepFilterNet processing for ai rack ai_idx in a background thread.
    Updates rack.ai_status and triggers redraws via a Blender timer.
    """
    if is_processing(ai_idx):
        print(f"[DNF] rack {ai_idx} already processing")
        return

    scene = context.scene
    if not scene:
        return

    ai_racks = getattr(scene, "pb_ai_racks", [])
    if ai_idx >= len(ai_racks):
        return

    rack = ai_racks[ai_idx]

    # ── Pre-flight checks ─────────────────────────────────────────────────
    runner = _get_runner_path()
    if runner is None:
        rack.ai_status = "ERROR"
        print("[DNF] ERROR: runner executable not found")
        print(f"[DNF] Expected at: {os.path.join(_ENGINES_DIR, '<platform>', 'deepfilter_runner[.exe]')}")
        return

    if not _models_exist():
        rack.ai_status = "ERROR"
        print(f"[DNF] ERROR: ONNX models not found in {_MODELS_DIR}")
        print("[DNF] Copy enc.onnx, erb_dec.onnx, df_dec.onnx, config.ini into that folder")
        return

    try:
        import Racks as _rk
        assigned = list(_rk.get_ai_rack_channels(rack))
    except Exception:
        assigned = []

    if not assigned:
        rack.ai_status = "ERROR"
        print("[DNF] ERROR: No channel assigned — click a CH button in the rail first")
        return

    ch_idx = assigned[0]

    # ── Knob values ───────────────────────────────────────────────────────
    atten_norm  = float(getattr(rack, 'p0', 0.5))
    sensitiv    = float(getattr(rack, 'p1', 0.75))
    postgain_n  = float(getattr(rack, 'p2', 0.5))
    postgain_db = -12.0 + postgain_n * 24.0   # maps 0-1 → -12..+12 dB

    # ── Set status ────────────────────────────────────────────────────────
    rack.ai_status = "PROCESSING"
    _cancel_flags[ai_idx] = False

    # ── Create temp files ─────────────────────────────────────────────────
    tmp_dir    = tempfile.gettempdir()
    input_wav  = os.path.join(tmp_dir, f"pb_dnf_in_{ai_idx}.wav")
    output_wav = os.path.join(tmp_dir, f"pb_dnf_out_{ai_idx}.wav")

    # ── Store context needed by background thread ─────────────────────────
    # (Cannot access bpy.context from a background thread)
    scene_name = scene.name

    def _worker():
        """Background thread: extract audio, run runner, update state."""
        try:
            # Step 1: extract audio to WAV
            print(f"[DNF] rack {ai_idx}: extracting ch{ch_idx+1} audio...")
            try:
                sr, nch = _extract_channel_wav(ch_idx, input_wav)
                print(f"[DNF] extracted: {sr}Hz {nch}ch → {input_wav}")
            except Exception as e:
                _finish(ai_idx, scene_name, "ERROR",
                        error_msg=f"Audio extraction failed: {e}")
                return

            if _cancel_flags.get(ai_idx):
                _finish(ai_idx, scene_name, "READY"); return

            # Step 2: run the subprocess
            cmd = [
                runner,
                "--input",    input_wav,
                "--output",   output_wav,
                "--models",   _MODELS_DIR,
                "--atten",    str(atten_norm),
                "--sensitiv", str(sensitiv),
                "--postgain", str(postgain_db),
            ]
            print(f"[DNF] launching: {' '.join(cmd)}")

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            last_progress = 0
            output_path   = None
            error_msg     = None

            for line in proc.stdout:
                line = line.strip()
                if not line: continue

                if line.startswith("PROGRESS:"):
                    try:
                        pct = int(line.split(":")[1])
                        if pct != last_progress:
                            last_progress = pct
                            print(f"[DNF] progress {pct}%")
                    except Exception:
                        pass

                elif line.startswith("DONE:"):
                    output_path = line[5:]
                    print(f"[DNF] done: {output_path}")

                elif line.startswith("ERROR:"):
                    error_msg = line[6:]
                    print(f"[DNF] runner error: {error_msg}")

                if _cancel_flags.get(ai_idx):
                    proc.terminate()
                    _finish(ai_idx, scene_name, "READY"); return

            proc.wait()
            stderr_txt = proc.stderr.read()
            if stderr_txt:
                print(f"[DNF] stderr: {stderr_txt[:500]}")

            if proc.returncode != 0 or error_msg or not output_path:
                msg = error_msg or f"Runner exited with code {proc.returncode}"
                _finish(ai_idx, scene_name, "ERROR", error_msg=msg)
                return

            # Step 3: build output waveform for display
            out_wave = _wav_to_waveform(output_wav)
            _store_output_wave(ai_idx, ch_idx, out_wave, output_wav)

            # Step 4: finish
            _finish(ai_idx, scene_name, "DONE",
                    output_wav=output_wav, ch_idx=ch_idx)

        except Exception as e:
            import traceback
            traceback.print_exc()
            _finish(ai_idx, scene_name, "ERROR", error_msg=str(e))

    t = threading.Thread(target=_worker, name=f"DNF_rack{ai_idx}", daemon=True)
    _active_jobs[ai_idx] = t
    t.start()

    # Register a timer to redraw while processing
    if not bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.register(_redraw_timer, first_interval=0.25)

    print(f"[DNF] rack {ai_idx} processing started in background thread")


# ---------------------------------------------------------------------------
# Output wave storage — module-level dict, read by rack_deepfilternet.py
# ---------------------------------------------------------------------------
_ai_output_wave  = {}   # ch_idx -> list of floats 0-1 (waveform for display)
_ai_output_paths = {}   # ai_idx -> output wav path


def _store_output_wave(ai_idx, ch_idx, wave_data, wav_path):
    """Store output waveform data and wav path."""
    _ai_output_wave[ch_idx]  = wave_data
    _ai_output_paths[ai_idx] = wav_path


def get_output_wave(ch_idx):
    """Called by rack_deepfilternet.py to get the output waveform."""
    return _ai_output_wave.get(ch_idx, [])


def get_output_path(ai_idx):
    """Return the processed WAV path for a rack, or None."""
    return _ai_output_paths.get(ai_idx)


# ---------------------------------------------------------------------------
# Finish callback — must run on main thread via timer
# ---------------------------------------------------------------------------
_pending_finish = {}   # ai_idx -> dict


def _finish(ai_idx, scene_name, status, output_wav=None,
            ch_idx=None, error_msg=None):
    """Queue a finish event to be processed on the main thread."""
    _pending_finish[ai_idx] = {
        'status':     status,
        'output_wav': output_wav,
        'ch_idx':     ch_idx,
        'error_msg':  error_msg,
        'scene_name': scene_name,
    }


def _redraw_timer():
    """
    Timer callback running on the main thread.
    Processes any pending finish events and triggers redraws.
    Returns interval or None to unregister.
    """
    # Process pending finish events
    for ai_idx, info in list(_pending_finish.items()):
        del _pending_finish[ai_idx]
        _apply_finish(ai_idx, info)

    # Trigger redraw on all node editor areas
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'NODE_EDITOR':
                    area.tag_redraw()
    except Exception:
        pass

    # Keep running while any job is active or finish events are pending
    any_active = (any(t.is_alive() for t in _active_jobs.values()) or
                  bool(_pending_finish))
    return 0.25 if any_active else None


def _apply_finish(ai_idx, info):
    """Apply a finish event on the main thread."""
    try:
        scene = bpy.data.scenes.get(info['scene_name'])
        if not scene:
            return

        ai_racks = getattr(scene, "pb_ai_racks", [])
        if ai_idx >= len(ai_racks):
            return

        rack           = ai_racks[ai_idx]
        rack.ai_status = info['status']

        if info['status'] == "ERROR":
            print(f"[DNF] rack {ai_idx} ERROR: {info.get('error_msg', 'unknown')}")

        elif info['status'] == "DONE":
            output_wav = info.get('output_wav')
            ch_idx     = info.get('ch_idx')
            if output_wav and ch_idx is not None:
                rack.ai_output_path = output_wav
                print(f"[DNF] rack {ai_idx} DONE — output: {output_wav}")
                _place_output_in_vse(scene, ch_idx, ai_idx, output_wav, rack)

    except Exception as e:
        print(f"[DNF] _apply_finish error: {e}")


# ---------------------------------------------------------------------------
# VSE output placement
# ---------------------------------------------------------------------------
def _place_output_in_vse(scene, ch_idx, ai_idx, output_wav, rack):
    """
    Add the processed WAV as a new sound strip in the VSE.

    Strategy:
    - Find all original strips on ch_idx
    - Place the new strip on the next free channel above them, at the same
      frame position and duration as the original content
    - Name it clearly so the user knows what it is
    - Keep it UNmuted so it plays immediately — user can mute the original
      or use ON/OFF on the rack to compare
    - Store the new strip channel on the rack so we can toggle it with ON/OFF
    """
    try:
        if not scene.sequence_editor:
            scene.sequence_editor_create()
        seq = scene.sequence_editor

        fps = scene.render.fps / scene.render.fps_base

        # Find original strips on this channel
        orig_strips = sorted(
            [s for s in seq.sequences_all
             if s.type == "SOUND" and s.sound
             and (s.channel - 1) == ch_idx],
            key=lambda s: s.frame_final_start
            if hasattr(s, 'frame_final_start') else s.frame_start
        )
        if not orig_strips:
            print(f"[DNF] no original strips found on ch{ch_idx+1}")
            return

        # Timeline span of all original strips
        first_frame = min(
            s.frame_final_start if hasattr(s, 'frame_final_start') else s.frame_start
            for s in orig_strips)
        last_frame  = max(s.frame_final_end for s in orig_strips)

        # Find next free channel above the original
        used_channels = {s.channel for s in seq.sequences_all}
        target_ch = ch_idx + 2   # +1 for 1-based, +1 to go above original
        while target_ch in used_channels:
            target_ch += 1

        # Add the new sound strip
        new_strip = seq.sequences.new_sound(
            name    = f"DNF_ch{ch_idx+1}_rack{ai_idx}",
            filepath= output_wav,
            channel = target_ch,
            frame_start = int(first_frame),
        )

        # Trim to match original span exactly
        new_strip.frame_final_end = int(last_frame)

        # Mute the original strips (VSE visual)
        for s in orig_strips:
            s.mute = True

        # Store which channel we placed output on so ON/OFF can toggle it
        rack['dnf_output_channel'] = target_ch
        rack['dnf_source_channel'] = ch_idx + 1   # 1-based VSE channel

        # Also mute via pb_sync_tracks so engine handle goes silent immediately
        src_idx = ch_idx             # 0-based
        out_idx = target_ch - 1     # 0-based
        tracks  = getattr(scene, "pb_sync_tracks", [])
        if src_idx < len(tracks):
            tracks[src_idx].mute = True    # silence original in engine
        # Output channel may not yet be in pb_sync_tracks if it's new —
        # the engine will add it on next play-start, so just ensure it's
        # not muted when it does appear
        if out_idx < len(tracks):
            tracks[out_idx].mute = False

        # Update engine handle volumes immediately
        try:
            from core.audio import _pb_engine_update_volume
            _pb_engine_update_volume(src_idx)
            _pb_engine_update_volume(out_idx)
        except Exception as _ve:
            print(f"[DNF] volume update after placement error: {_ve}")

        print(f"[DNF] placed processed audio on VSE channel {target_ch}")
        print(f"[DNF] original ch{ch_idx+1} muted — ON/OFF toggles A/B comparison")

        # Force VSE redraw
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ('SEQUENCE_EDITOR', 'NODE_EDITOR'):
                    area.tag_redraw()

    except Exception as e:
        import traceback
        print(f"[DNF] VSE placement failed: {e}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Status query helpers — called from rack_deepfilternet.py draw
# ---------------------------------------------------------------------------
def get_progress(ai_idx):
    """Return 0-100 progress for a running job, or None if not running."""
    t = _active_jobs.get(ai_idx)
    if t and t.is_alive():
        return -1   # running but no granular progress exposed yet
    return None
