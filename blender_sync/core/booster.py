# =============================================================================
# core/booster.py
# THE BOOSTER!! backend — export/boost/reimport.
#
# Called from Racks.py handle_click when the APPLY BOOST button is clicked.
# Runs in a background thread exactly like ai_deepfilternet.py.
#
# Steps:
#   1. Extract channel audio to temp WAV using aud (same as DeepFilterNet)
#   2. Run booster_runner.py subprocess to apply gain + soft limiter
#   3. Save boosted WAV as <original>_boosted_+NdB.wav alongside the original
#   4. Place new strip on next free VSE channel, strip.volume = 1.0
#   5. Mute original strip so new louder one plays immediately
# =============================================================================

import os
import sys
import json
import wave
import subprocess
import threading
import tempfile

import bpy

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_ADDON_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNNER     = os.path.join(_ADDON_DIR, "ai_engines", "booster", "booster_runner.py")


def _get_python():
    import platform
    return ("py", ["-3.12"]) if platform.system() == "Windows" else ("python3", [])


# ---------------------------------------------------------------------------
# Active jobs
# ---------------------------------------------------------------------------
_active_jobs  = {}   # rack_idx -> Thread
_cancel_flags = {}   # rack_idx -> bool
_pending_finish = {} # rack_idx -> dict


def is_processing(rack_idx):
    t = _active_jobs.get(rack_idx)
    return t is not None and t.is_alive()


# ---------------------------------------------------------------------------
# Audio extraction — reuse DeepFilterNet's proven _extract_channel_wav
# ---------------------------------------------------------------------------
def _extract_channel_wav(channel_idx, out_path):
    """Extract all audio from VSE channel to a WAV file using aud."""
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
        key=lambda s: (s.frame_final_start
                       if hasattr(s, 'frame_final_start') else s.frame_start)
    )
    if not strips:
        raise RuntimeError(f"No audio strips on channel {channel_idx + 1}")

    first_path = bpy.path.abspath(strips[0].sound.filepath)
    spec       = aud.Sound.file(first_path).specs
    TARGET_SR  = int(spec[0])
    TARGET_NCH = min(int(spec[1]), 2)

    total_dur_s   = seq_end - seq_start
    total_samples = max(1, int(total_dur_s * TARGET_SR))
    audio_out     = np.zeros((total_samples, TARGET_NCH), dtype=np.float32)

    for strip in strips:
        try:
            filepath       = bpy.path.abspath(strip.sound.filepath)
            strip_tl_start = (strip.frame_final_start / fps
                              if hasattr(strip, 'frame_final_start')
                              else strip.frame_start / fps)
            strip_tl_end   = strip.frame_final_end / fps
            vis_start      = max(strip_tl_start, seq_start)
            vis_end        = min(strip_tl_end,   seq_end)
            if vis_end <= vis_start:
                continue

            file_offset = getattr(strip, 'frame_offset_start', 0) / fps
            src_start   = file_offset + (vis_start - strip_tl_start)
            src_end     = src_start + (vis_end - vis_start)

            snd  = aud.Sound.file(filepath).limit(src_start, src_end)
            snd  = snd.resample(TARGET_SR, False).rechannel(TARGET_NCH)
            data = np.array(snd.data(), dtype=np.float32)

            if data.ndim == 1:
                data = data.reshape(-1, 1)
                if TARGET_NCH == 2:
                    data = np.repeat(data, 2, axis=1)

            buf_start = int((vis_start - seq_start) * TARGET_SR)
            buf_end   = min(buf_start + len(data), total_samples)
            data      = data[:buf_end - buf_start]
            audio_out[buf_start:buf_end] += data

        except Exception as e:
            print(f"[BOOSTER] strip '{strip.name}' extract failed: {e}")

    audio_clip = np.clip(audio_out, -1.0, 1.0)
    s16        = (audio_clip * 32767.0).astype(np.int16)

    with wave.open(out_path, 'wb') as wf:
        wf.setnchannels(TARGET_NCH)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_SR)
        wf.writeframes(s16.tobytes())

    print(f"[BOOSTER] extracted ch{channel_idx+1}: "
          f"{len(s16)} frames @ {TARGET_SR}Hz {TARGET_NCH}ch")
    return TARGET_SR, TARGET_NCH


# ---------------------------------------------------------------------------
# VSE output placement — same strategy as DeepFilterNet
# ---------------------------------------------------------------------------
def _place_output_in_vse(scene, ch_idx, rack_idx, output_wav, rack):
    """Place boosted WAV on next free VSE channel, mute original."""
    try:
        if not scene.sequence_editor:
            scene.sequence_editor_create()
        seq = scene.sequence_editor

        fps = scene.render.fps / scene.render.fps_base

        orig_strips = sorted(
            [s for s in seq.sequences_all
             if s.type == "SOUND" and s.sound
             and (s.channel - 1) == ch_idx],
            key=lambda s: (s.frame_final_start
                           if hasattr(s, 'frame_final_start') else s.frame_start)
        )
        if not orig_strips:
            print(f"[BOOSTER] no original strips on ch{ch_idx+1}")
            return

        first_frame = min(
            s.frame_final_start if hasattr(s, 'frame_final_start') else s.frame_start
            for s in orig_strips)
        last_frame  = max(s.frame_final_end for s in orig_strips)

        # Find target channel — use rack's p2 setting (0 = auto-next-free)
        used = {s.channel for s in seq.sequences_all}
        requested = int(getattr(rack, 'p2', 0))
        if requested > 0 and requested not in used:
            target_ch = requested
        else:
            # Auto: find next free channel above the source
            target_ch = ch_idx + 2
            while target_ch in used:
                target_ch += 1

        new_strip = seq.sequences.new_sound(
            name        = f"BOOSTED_ch{ch_idx+1}_r{rack_idx}",
            filepath    = output_wav,
            channel     = target_ch,
            frame_start = int(first_frame),
        )
        new_strip.frame_final_end = int(last_frame)
        new_strip.volume          = 1.0

        # Mute originals
        for s in orig_strips:
            s.mute = True

        # Update engine handles
        src_idx = ch_idx
        out_idx = target_ch - 1
        tracks  = getattr(scene, "pb_sync_tracks", [])
        if src_idx < len(tracks):
            tracks[src_idx].mute = True
        if out_idx < len(tracks):
            tracks[out_idx].mute = False
        try:
            from core.audio import _pb_engine_update_volume
            _pb_engine_update_volume(src_idx)
            _pb_engine_update_volume(out_idx)
        except Exception as e:
            print(f"[BOOSTER] volume update error: {e}")

        print(f"[BOOSTER] placed boosted strip on VSE ch{target_ch}, vol=1.0")
        print(f"[BOOSTER] original ch{ch_idx+1} muted — mute/unmute to A/B compare")

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ('SEQUENCE_EDITOR', 'NODE_EDITOR'):
                    area.tag_redraw()

    except Exception as e:
        print(f"[BOOSTER] _place_output_in_vse error: {e}")
        import traceback; traceback.print_exc()


# ---------------------------------------------------------------------------
# Finish / timer
# ---------------------------------------------------------------------------
def _finish(rack_idx, scene_name, status, output_wav=None,
            ch_idx=None, error_msg=None):
    _pending_finish[rack_idx] = {
        'status':     status,
        'output_wav': output_wav,
        'ch_idx':     ch_idx,
        'error_msg':  error_msg,
        'scene_name': scene_name,
    }


def _redraw_timer():
    for rack_idx, info in list(_pending_finish.items()):
        del _pending_finish[rack_idx]
        _apply_finish(rack_idx, info)

    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'NODE_EDITOR':
                    area.tag_redraw()
    except Exception:
        pass

    any_active = (any(t.is_alive() for t in _active_jobs.values()) or
                  bool(_pending_finish))
    return 0.25 if any_active else None


def _apply_finish(rack_idx, info):
    try:
        scene = bpy.data.scenes.get(info['scene_name'])
        if not scene:
            return

        racks = getattr(scene, "pb_racks", [])
        if rack_idx >= len(racks):
            return

        rack           = racks[rack_idx]
        rack.ai_status = info['status']

        if info['status'] == 'ERROR':
            print(f"[BOOSTER] rack {rack_idx} ERROR: {info.get('error_msg')}")
        elif info['status'] == 'DONE':
            output_wav = info.get('output_wav')
            ch_idx     = info.get('ch_idx')
            if output_wav and ch_idx is not None:
                _place_output_in_vse(scene, ch_idx, rack_idx, output_wav, rack)
    except Exception as e:
        print(f"[BOOSTER] _apply_finish error: {e}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def process_booster(rack_idx, context):
    """Launch BOOSTER processing in a background thread."""
    if is_processing(rack_idx):
        print(f"[BOOSTER] rack {rack_idx} already processing")
        return

    scene = context.scene
    if not scene:
        return

    racks = getattr(scene, "pb_racks", [])
    if rack_idx >= len(racks):
        return

    rack = racks[rack_idx]

    try:
        import Racks as _rk
        assigned = list(_rk.get_rack_channels(rack))
    except Exception:
        assigned = []

    if not assigned:
        rack.ai_status = "NO_CHANNEL"
        print("[BOOSTER] ERROR: no channel assigned")
        return

    ch_idx     = assigned[0]
    boost_norm = float(getattr(rack, 'p0', 0.30))
    limiter_on = float(getattr(rack, 'p1', 1.0)) > 0.5
    boost_db   = boost_norm * 40.0

    rack.ai_status        = "PROCESSING"
    _cancel_flags[rack_idx] = False

    tmp_dir   = tempfile.gettempdir()
    input_wav = os.path.join(tmp_dir, f"pb_boost_in_{rack_idx}.wav")

    # Build output path alongside original file
    try:
        seq = scene.sequence_editor
        orig_strips = [s for s in seq.sequences_all
                       if s.type == "SOUND" and s.sound
                       and (s.channel - 1) == ch_idx]
        if orig_strips:
            orig_path = bpy.path.abspath(orig_strips[0].sound.filepath)
            base, ext = os.path.splitext(orig_path)
            db_tag    = f"+{boost_db:.0f}dB"
            out_ext   = ext if ext.lower() == '.wav' else '.wav'
            output_wav = f"{base}_boosted_{db_tag}{out_ext}"
        else:
            output_wav = os.path.join(tmp_dir, f"pb_boost_out_{rack_idx}.wav")
    except Exception:
        output_wav = os.path.join(tmp_dir, f"pb_boost_out_{rack_idx}.wav")

    scene_name = scene.name

    def _worker():
        try:
            # Step 1: extract
            print(f"[BOOSTER] rack {rack_idx}: extracting ch{ch_idx+1}...")
            try:
                _extract_channel_wav(ch_idx, input_wav)
            except Exception as e:
                _finish(rack_idx, scene_name, "ERROR",
                        error_msg=f"Audio extraction failed: {e}")
                return

            if _cancel_flags.get(rack_idx):
                _finish(rack_idx, scene_name, "READY"); return

            # Step 2: build args and run subprocess
            args_path = os.path.join(tmp_dir, f"pb_boost_args_{rack_idx}.json")
            with open(args_path, "w") as f:
                json.dump({
                    "src":      input_wav,
                    "output":   output_wav,
                    "boost_db": boost_db,
                    "limiter":  limiter_on,
                }, f)

            py_exe, py_flags = _get_python()
            cmd = [py_exe] + py_flags + [_RUNNER, "--args", args_path]
            print(f"[BOOSTER] rack {rack_idx}: boost={boost_db:.1f}dB "
                  f"limiter={'on' if limiter_on else 'off'}")

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            for line in proc.stdout:
                line = line.strip()
                if line.startswith("PROGRESS:"):
                    try:
                        pct = int(line.split(":")[1])
                        print(f"[BOOSTER] rack {rack_idx}: {pct}%")
                    except Exception:
                        pass
                elif line.startswith("ERROR:"):
                    print(f"[BOOSTER] runner: {line}")

                if _cancel_flags.get(rack_idx):
                    proc.terminate()
                    _finish(rack_idx, scene_name, "READY"); return

            proc.wait()
            stderr_txt = proc.stderr.read()
            if stderr_txt.strip():
                print(f"[BOOSTER] stderr: {stderr_txt[:400]}")

            if proc.returncode != 0:
                _finish(rack_idx, scene_name, "ERROR",
                        error_msg=f"Runner exited rc={proc.returncode}")
                return

            if not os.path.exists(output_wav) or os.path.getsize(output_wav) < 44:
                _finish(rack_idx, scene_name, "ERROR",
                        error_msg="Output file missing or empty")
                return

            _finish(rack_idx, scene_name, "DONE",
                    output_wav=output_wav, ch_idx=ch_idx)

        except Exception as e:
            import traceback; traceback.print_exc()
            _finish(rack_idx, scene_name, "ERROR", error_msg=str(e))
        finally:
            for f in (input_wav, args_path):
                try: os.remove(f)
                except Exception: pass

    t = threading.Thread(target=_worker,
                         name=f"BOOSTER_rack{rack_idx}", daemon=True)
    _active_jobs[rack_idx] = t
    t.start()

    if not bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.register(_redraw_timer, first_interval=0.25)

    print(f"[BOOSTER] rack {rack_idx} started in background thread")
