"""
core/ai_demucs.py
=================
Backend for the Demucs stem separation rack.

Checks for system Python with demucs installed.
Extracts source channel to WAV, runs demucs_runner.py subprocess,
places each stem as a new VSE sound strip on its assigned channel.

Params from rack (PB_AIRackSettings):
    p0  model index   (0=htdemucs, 1=htdemucs_ft, 2=htdemucs_6s, 3=mdx_extra)
    p2  mute original (>0.5 = mute source strip after split)
    p3  stem toggles  packed bits: bit0=drums bit1=bass bit2=vocals bit3=other
                      bit4=piano bit5=guitar  (default 0b001111 = 15 = all 4 basic)
    p4  drums out ch  (0=auto)
    p5  bass out ch   (0=auto)
    p6  vocals out ch (0=auto)
    p7  other out ch  (0=auto)
    ai_output_path    stores last output dir (reused on re-run)
    # piano/guitar channels stored as dict in rack['demucs_extra_ch'] — accessed via p3/p4 overflow
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

MODELS = ["htdemucs", "htdemucs_ft", "htdemucs_6s", "mdx_extra"]
MODEL_STEMS = {
    "htdemucs":    ["drums", "bass", "vocals", "other"],
    "htdemucs_ft": ["drums", "bass", "vocals", "other"],
    "htdemucs_6s": ["drums", "bass", "vocals", "other", "piano", "guitar"],
    "mdx_extra":   ["drums", "bass", "vocals", "other"],
}
STEM_LABELS = {
    "drums":  "Drums",
    "bass":   "Bass",
    "vocals": "Vocals",
    "other":  "Other",
    "piano":  "Piano",
    "guitar": "Guitar",
}
STEM_BITS = {"drums": 0, "bass": 1, "vocals": 2, "other": 3, "piano": 4, "guitar": 5}
STEM_CH_PROPS = {"drums": "p4", "bass": "p5", "vocals": "p6", "other": "p7",
                 "piano": "p8", "guitar": "p9"}


def _find_system_python():
    global _PYTHON_CMD
    if _PYTHON_CMD:
        return _PYTHON_CMD
    for cmd in [["py", "-3.12"], ["py", "-3.11"], ["py", "-3.10"],
                ["python"], ["python3"]]:
        try:
            r = subprocess.run(
                cmd + ["-c", "import demucs; print('ok')"],
                capture_output=True, timeout=6, text=True)
            if r.returncode == 0 and "ok" in r.stdout:
                _PYTHON_CMD = cmd
                print(f"[DEMUCS] system Python: {' '.join(cmd)}")
                return _PYTHON_CMD
        except Exception:
            continue
    return None


def _get_runner_path():
    addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_dir, "ai_engines", "demucs", "demucs_runner.py")


def is_processing(ai_idx):
    t = _active_jobs.get(ai_idx)
    return t is not None and t.is_alive()


def get_active_stems(rack):
    """Return list of stem names that are toggled on for this rack."""
    model_idx = max(0, min(int(getattr(rack, "p0", 1.0)), len(MODELS) - 1))
    model     = MODELS[model_idx]
    available = MODEL_STEMS[model]
    bits      = int(getattr(rack, "p3", 15.0))   # default all 4 basic on
    return [s for s in available if (bits >> STEM_BITS[s]) & 1]


def get_stem_channel(rack, stem):
    """Return target VSE channel for a stem (0 = auto)."""
    prop = STEM_CH_PROPS.get(stem)
    if prop:
        return int(getattr(rack, prop, 0.0))
    return 0


def set_stem_channel(rack, stem, ch):
    prop = STEM_CH_PROPS.get(stem)
    if prop:
        setattr(rack, prop, float(ch))


def _redraw_timer():
    for ai_idx, info in list(_pending_finish.items()):
        del _pending_finish[ai_idx]
        _apply_finish(ai_idx, info)
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "NODE_EDITOR":
                    area.tag_redraw()
    except Exception:
        pass
    any_active = any(t.is_alive() for t in _active_jobs.values()) or bool(_pending_finish)
    return 0.25 if any_active else None


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
            print(f"[DEMUCS] rack {ai_idx} finished with status {info['status']}")
            return

        stems_placed = info.get("stems_placed", {})  # {stem: wav_path}
        src_ch_idx   = info.get("src_ch_idx", 0)
        frame_start  = info.get("frame_start", 1)
        mute_orig    = info.get("mute_original", True)

        if not stems_placed:
            print(f"[DEMUCS] rack {ai_idx}: no stems to place")
            return

        seq = scene.sequence_editor
        if not seq:
            scene.sequence_editor_create()
            seq = scene.sequence_editor

        used_channels = {s.channel for s in seq.sequences_all}

        # Auto-assign channels sequentially from after source channel
        auto_start = src_ch_idx + 2  # 1-based VSE channel
        auto_next  = auto_start
        while auto_next in used_channels:
            auto_next += 1

        placed = []
        for stem, wav_path in stems_placed.items():
            if not os.path.exists(wav_path):
                print(f"[DEMUCS] stem WAV missing: {wav_path}")
                continue

            # Get requested channel
            target_ch = info.get("stem_channels", {}).get(stem, 0)
            if target_ch < 1 or target_ch in used_channels:
                target_ch = auto_next
                while target_ch in used_channels:
                    target_ch += 1
            auto_next = target_ch + 1

            strip_name = f"DEMUCS_{STEM_LABELS.get(stem, stem).upper()}_r{ai_idx}"
            try:
                new_strip = seq.sequences.new_sound(
                    name=strip_name,
                    filepath=wav_path,
                    channel=target_ch,
                    frame_start=frame_start,
                )
                new_strip.volume = 1.0
                used_channels.add(target_ch)
                placed.append((stem, target_ch))
                print(f"[DEMUCS] placed {stem} → ch{target_ch} ({strip_name}) "
                      f"frame_start={new_strip.frame_start} "
                      f"frame_offset_start={getattr(new_strip,'frame_offset_start',0)} "
                      f"frame_final_start={new_strip.frame_final_start} "
                      f"duration={new_strip.frame_final_duration}")
            except Exception as e:
                print(f"[DEMUCS] strip place error for {stem}: {e}")

        # Mute original — use the full mixer path so the engine and
        # pb_sync_tracks.mute are updated, not just the VSE strip visual.
        if mute_orig and placed:
            try:
                from core.audio import sync_vse_mute
                tracks = getattr(scene, "pb_sync_tracks", [])
                if src_ch_idx < len(tracks):
                    tracks[src_ch_idx].mute = True
                sync_vse_mute(src_ch_idx, True)
                print(f"[DEMUCS] muted original ch{src_ch_idx + 1} (engine + VSE)")
            except Exception as _me:
                # Fallback: at least set the VSE strip visual
                for strip in seq.sequences_all:
                    if (strip.type == "SOUND" and strip.sound and
                            strip.channel == src_ch_idx + 1):
                        strip.mute = True
                print(f"[DEMUCS] mute fallback for ch{src_ch_idx + 1}: {_me}")

        print(f"[DEMUCS] rack {ai_idx}: placed {len(placed)} stems")

        # Expand the mixer fader strips to cover the new channels.
        # Without this, pb_sync_tracks still has only 9 entries and channels
        # 10+ have no faders until the meter timer auto-detects them.
        try:
            from ui.mixer.interaction import _sync_tracks_to_vse
            _sync_tracks_to_vse(scene, reset_values=False)
        except Exception as e:
            print(f"[DEMUCS] track sync error: {e}")

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ("SEQUENCE_EDITOR", "NODE_EDITOR"):
                    area.tag_redraw()

    except Exception as e:
        print(f"[DEMUCS] _apply_finish error: {e}")
        import traceback; traceback.print_exc()


def separate_demucs(ai_idx, context):
    """Launch Demucs stem separation in a background thread."""
    if is_processing(ai_idx):
        print(f"[DEMUCS] rack {ai_idx} already processing")
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
        print("[DEMUCS] ERROR: system Python with demucs not found")
        print("[DEMUCS] Install with: pip install demucs")
        return

    runner_path = _get_runner_path()
    if not os.path.exists(runner_path):
        rack.ai_status = "ERROR"
        print(f"[DEMUCS] ERROR: runner not found at {runner_path}")
        return

    # Source channel
    active_chs = [ci + 1 for ci in range(9) if getattr(rack, f"ch{ci}", False)]
    if not active_chs:
        rack.ai_status = "ERROR"
        print("[DEMUCS] ERROR: no input channel assigned")
        return
    src_ch_1based = active_chs[0]
    src_ch_idx    = src_ch_1based - 1

    seq = scene.sequence_editor
    src_strip = None
    if seq:
        for strip in sorted(seq.sequences_all,
                            key=lambda s: s.frame_final_start):
            if (strip.channel == src_ch_1based and
                    hasattr(strip, "sound") and strip.sound):
                src_strip = strip
                break
    if not src_strip:
        rack.ai_status = "ERROR"
        print(f"[DEMUCS] ERROR: no audio strip on channel {src_ch_1based}")
        return

    # Params
    model_idx  = max(0, min(int(getattr(rack, "p0", 1.0)), len(MODELS) - 1))
    model      = MODELS[model_idx]
    mute_orig  = float(getattr(rack, "p2", 1.0)) > 0.5
    active_stems = get_active_stems(rack)
    stem_channels = {s: get_stem_channel(rack, s) for s in active_stems}

    if not active_stems:
        rack.ai_status = "ERROR"
        print("[DEMUCS] ERROR: no stems selected")
        return

    # The mixdown export runs from scene.frame_start to scene.frame_end,
    # so the exported WAV starts at scene.frame_start with silence before
    # the strip content. Stems must be placed at scene.frame_start to align.
    frame_start  = scene.frame_start
    scene_name   = scene.name

    fps = scene.render.fps / scene.render.fps_base
    strip_offset_s = (src_strip.frame_final_start - scene.frame_start) / fps
    print(f"[DEMUCS] SOURCE STRIP: frame_start={src_strip.frame_start} "
          f"frame_offset_start={getattr(src_strip,'frame_offset_start',0)} "
          f"frame_final_start={src_strip.frame_final_start} "
          f"frame_final_duration={src_strip.frame_final_duration}")
    print(f"[DEMUCS] strip starts at frame {src_strip.frame_final_start}, "
          f"scene starts at {scene.frame_start}, "
          f"offset={strip_offset_s:.2f}s — placing stems at scene.frame_start")

    # Build output dir alongside the original source file
    # e.g. /path/to/song_demucs_htdemucs/ containing drums.wav, bass.wav etc.
    # Falls back to temp dir if source path can't be determined.
    tmp_dir = tempfile.gettempdir()
    src_wav = os.path.join(tmp_dir, f"pb_demucs_{ai_idx}_src_{int(time.time())}.wav")
    try:
        orig_path  = bpy.path.abspath(src_strip.sound.filepath)
        orig_base  = os.path.splitext(orig_path)[0]
        out_dir    = f"{orig_base}_demucs_{model}"
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        out_dir = os.path.join(tmp_dir, f"pb_demucs_{ai_idx}_out_{int(time.time())}")
        os.makedirs(out_dir, exist_ok=True)

    try:
        seq_strips     = list(seq.sequences_all)
        original_mutes = {s.name: s.mute for s in seq_strips if hasattr(s, "mute")}
        for s in seq_strips:
            if hasattr(s, "mute"):
                s.mute = (s.channel != src_ch_1based)

        bpy.ops.sound.mixdown(
            filepath=src_wav, check_existing=False,
            relative_path=False, codec="PCM", container="WAV")

        for s in seq_strips:
            if hasattr(s, "mute") and s.name in original_mutes:
                s.mute = original_mutes[s.name]

        if not os.path.exists(src_wav):
            raise RuntimeError("mixdown produced no file")

        import wave
        with wave.open(src_wav, "r") as wf:
            dur = wf.getnframes() / wf.getframerate()
        print(f"[DEMUCS] exported ch{src_ch_1based}: {os.path.basename(src_wav)} ({dur:.1f}s)")

    except Exception as ex:
        rack.ai_status = "ERROR"
        print(f"[DEMUCS] export error: {ex}")
        import traceback; traceback.print_exc()
        return

    # Write args file
    args_path = os.path.join(tmp_dir, f"pb_demucs_{ai_idx}_args.json")
    with open(args_path, "w", encoding="utf-8") as f:
        json.dump({
            "src":     src_wav,
            "out_dir": out_dir,
            "model":   model,
            "stems":   active_stems,
            "preview": False,
        }, f)

    rack.ai_status        = "PROCESSING"
    _cancel_flags[ai_idx] = False

    def _worker():
        stems_placed = {}
        try:
            proc = subprocess.Popen(
                python_cmd + [runner_path, "--args", args_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8")

            for line in proc.stdout:
                line = line.strip()
                if line.startswith("PROGRESS:"):
                    try:
                        pct = int(line.split(":")[1])
                        print(f"[DEMUCS] rack {ai_idx}: {pct}%")
                    except Exception:
                        pass
                elif line.startswith("STEM_DONE:"):
                    try:
                        _, stem, wav = line.split(":", 2)
                        stems_placed[stem] = wav
                        print(f"[DEMUCS] rack {ai_idx}: {stem} done → {wav}")
                    except Exception:
                        pass
                elif line.startswith("ERROR:"):
                    print(f"[DEMUCS] runner: {line}")

                if _cancel_flags.get(ai_idx):
                    proc.terminate()
                    _pending_finish[ai_idx] = {
                        "status": "READY", "scene_name": scene_name}
                    return

            proc.wait()
            stderr_txt = proc.stderr.read()
            if stderr_txt.strip():
                print(f"[DEMUCS] stderr: {stderr_txt[:400]}")

            if proc.returncode != 0 or not stems_placed:
                _pending_finish[ai_idx] = {
                    "status": "ERROR", "scene_name": scene_name,
                    "stems_placed": {}}
            else:
                _pending_finish[ai_idx] = {
                    "status":        "DONE",
                    "scene_name":    scene_name,
                    "stems_placed":  stems_placed,
                    "stem_channels": stem_channels,
                    "src_ch_idx":    src_ch_idx,
                    "frame_start":   frame_start,
                    "mute_original": mute_orig,
                }

        except Exception as e:
            print(f"[DEMUCS] worker error: {e}")
            import traceback; traceback.print_exc()
            _pending_finish[ai_idx] = {
                "status": "ERROR", "scene_name": scene_name, "stems_placed": {}}
        finally:
            try: os.remove(args_path)
            except Exception: pass
            try: os.remove(src_wav)
            except Exception: pass

    t = threading.Thread(target=_worker, name=f"Demucs_{ai_idx}", daemon=True)
    _active_jobs[ai_idx] = t
    t.start()

    if not bpy.app.timers.is_registered(_redraw_timer):
        bpy.app.timers.register(_redraw_timer, first_interval=0.25)

    print(f"[DEMUCS] rack {ai_idx} started — model={model} stems={active_stems}")
