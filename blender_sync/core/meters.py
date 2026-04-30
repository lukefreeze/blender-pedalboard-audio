# =============================================================================
# core/meters.py
# Peak envelope building, VU meter timer, and peak-hold ballistics.
# =============================================================================

import bpy
import struct

from core.constants import (
    MAX_CHANNELS, DEFAULT_CHANNELS, METER_POLL_INTERVAL,
    METER_DECAY, PEAK_HOLD_TIME,
)
from core.engine import get_engine


# Module-level meter state (read by ui/mixer/mixer_hud.py for drawing)
_engine_levels         = [0.0] * MAX_CHANNELS
_peak_hold             = [0.0] * MAX_CHANNELS
_peak_hold_timer       = [0.0] * MAX_CHANNELS
_meter_timer_registered = False

# Envelope cache: {filepath -> (rms_list, peak_list)}
_envelope_cache = {}

# These are set by Loader.py so meters.py can read ui state without circular import
_get_pb_ui_enabled = lambda: False   # replaced in Loader.py

def _build_envelope(filepath, fps):
    """
    Read the audio file once, build two arrays per video frame:
      envelope_rms  — RMS level (drives the animated bar, reflects loudness)
      envelope_peak — true peak  (drives the peak-hold dot)
    Returns (rms_list, peak_list).
    RMS varies much more than peak across a track, giving the
    jumping-up-and-down behaviour you see in professional meters.
    """
    import aud, math as _math
    try:
        raw      = aud.Sound.file(filepath).data()
        n_floats = len(raw) // 4
        if n_floats == 0:
            return [], []

        samples     = struct.unpack_from(f'{n_floats}f', raw)
        specs       = aud.Sound.file(filepath).specs
        sample_rate = int(specs[0])
        num_ch      = max(1, int(specs[1]))
        spf         = max(1, int(sample_rate * num_ch / fps))

        rms_env  = []
        peak_env = []
        i        = 0
        total    = len(samples)
        while i < total:
            end      = min(i + spf, total)
            peak     = 0.0
            rms_sum  = 0.0
            count    = end - i
            for j in range(i, end):
                v = samples[j]
                if v > peak:  peak = v
                if -v > peak: peak = -v
                rms_sum += v * v
            rms = _math.sqrt(rms_sum / count) if count > 0 else 0.0
            rms_env.append(min(rms, 1.0))
            peak_env.append(min(peak, 1.0))
            i += spf

        filename = filepath.replace('\\','/').split('/')[-1]
        print(f"[ENVELOPE] {filename}: {len(rms_env)} frames "
              f"({round(len(rms_env)/fps,1)}s) RMS+peak")
        return rms_env, peak_env
    except Exception as e:
        print(f"[ENVELOPE] failed: {e}")
        return [], []


def get_envelope(filepath, fps):
    """Return cached (rms_envelope, peak_envelope) tuple, building if needed."""
    if filepath not in _envelope_cache:
        print(f"[ENVELOPE] building: {filepath}")
        _envelope_cache[filepath] = _build_envelope(filepath, fps)
    return _envelope_cache[filepath]


# ---------------------------------------------------------------------------
# Meter timer
# Runs independently so meters animate even without mouse movement.
# Reads peak from envelope at current frame — accurate to timeline cursor.
# ---------------------------------------------------------------------------
def _meter_timer():
    global _engine_levels, _peak_hold, _peak_hold_timer

    from ui.mixer.mixer_hud import pb_ui_enabled
    if not pb_ui_enabled:
        _engine_levels = [0.0] * MAX_CHANNELS
        return METER_POLL_INTERVAL

    try:
        scene = bpy.context.scene
        if not scene or not scene.sequence_editor:
            return METER_POLL_INTERVAL

        current_frame = scene.frame_current
        fps           = scene.render.fps / scene.render.fps_base
        tracks        = getattr(scene, "pb_sync_tracks", [])
        is_playing    = bool(bpy.context.screen and
                             bpy.context.screen.is_animation_playing)

        # Auto-detect new channels — sync if VSE has strips on channels
        # beyond what we currently have tracks for
        if scene.sequence_editor:
            highest = max((s.channel for s in scene.sequence_editor.sequences_all
                           if s.type == "SOUND" and s.sound), default=0)
            needed  = max(DEFAULT_CHANNELS, highest)
            if needed > len(tracks):
                from ui.mixer.interaction import _sync_tracks_to_vse
                _sync_tracks_to_vse(scene, reset_values=False)
                tracks = getattr(scene, "pb_sync_tracks", [])
                for area in bpy.context.screen.areas:
                    area.tag_redraw()

        # Look 2 frames ahead when playing to compensate for audio hardware
        # clock running slightly ahead of Blender's UI frame counter.
        # This keeps the meter visually in sync with what you hear.
        if is_playing:
            current_frame = current_frame + 2

        # --- Freeze meters when paused and cursor is not moving ---
        # Compare to last known frame; only update if playing or scrubbing.
        last_frame = getattr(_meter_timer, '_last_frame', None)
        cursor_moved = (last_frame != current_frame)
        _meter_timer._last_frame = current_frame

        if not is_playing and not cursor_moved:
            # Nothing changed — just keep redrawing so HUD stays visible
            # but don't recalculate levels (no flicker).
            try:
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type == "NODE_EDITOR":
                            area.tag_redraw()
            except Exception: pass
            return METER_POLL_INTERVAL

        # --- Read RMS and peak from pre-built envelopes ---
        new_rms  = [0.0] * MAX_CHANNELS   # drives the animated bar
        new_peak = [0.0] * MAX_CHANNELS   # drives the peak-hold dot

        for strip in scene.sequence_editor.sequences_all:
            if strip.type != "SOUND" or not strip.sound: continue
            if strip.mute: continue

            idx = strip.channel - 1
            if idx < 0 or idx >= MAX_CHANNELS: continue
            if idx < len(tracks) and tracks[idx].mute: continue

            # Use actual timeline start (same formula as build function)
            actual_start_frame = strip.frame_final_end - strip.frame_final_duration
            if not (actual_start_frame <= current_frame <= strip.frame_final_end):
                continue

            filepath = bpy.path.abspath(strip.sound.filepath)
            rms_env, peak_env = get_envelope(filepath, fps)
            if not rms_env: continue

            frame_offset = int(current_frame - actual_start_frame)
            if not (0 <= frame_offset < len(rms_env)): continue

            # Scale by strip volume (has fader already baked in)
            vol = strip.volume
            scaled_rms  = rms_env[frame_offset]  * vol
            scaled_peak = peak_env[frame_offset] * vol if peak_env else scaled_rms

            if scaled_rms  > new_rms[idx]:  new_rms[idx]  = scaled_rms
            if scaled_peak > new_peak[idx]: new_peak[idx] = scaled_peak

        # --- Apply ballistics ---
        # Faster decay when playing (like a real VU meter),
        # slower when scrubbing so you can see the level clearly.
        decay = METER_DECAY * (2.0 if is_playing else 0.5)

        for i in range(MAX_CHANNELS):
            if i < len(tracks) and tracks[i].mute:
                new_rms[i] = new_peak[i] = 0.0

            rms  = min(new_rms[i],  1.0)
            peak = min(new_peak[i], 1.0)

            # Peak-hold dot uses true peak
            if peak >= _peak_hold[i]:
                _peak_hold[i]       = peak
                _peak_hold_timer[i] = 0.0
            else:
                _peak_hold_timer[i] += METER_POLL_INTERVAL
                if _peak_hold_timer[i] > PEAK_HOLD_TIME:
                    _peak_hold[i] = max(0.0, _peak_hold[i] - decay)

            # Bar uses RMS — instant attack, smooth decay
            _engine_levels[i] = (rms if rms > _engine_levels[i]
                                  else max(0.0, _engine_levels[i] - decay))

        # Mirror meter levels to C++ engine state
        engine = get_engine()
        if engine:
            try:
                s  = engine.get_state()
                ml = list(s.meter_levels)
                for i in range(min(MAX_CHANNELS, len(ml))):
                    ml[i] = _engine_levels[i]
                s.meter_levels = ml

                # Read GR levels back from C++ for rack display
                # Racks.py reads _gr_levels to drive GR meters
                try:
                    from Racks import _gr_levels, get_rack_channels
                    scene = bpy.context.scene
                    racks = getattr(scene, "pb_racks", []) if scene else []
                    for ri, rack in enumerate(racks):
                        if not rack.enabled: continue
                        assigned = get_rack_channels(rack)
                        for ch in assigned:
                            if ch < 0 or ch >= MAX_CHANNELS: continue
                            try:
                                gr_vals = s.get_gr_levels(ch)
                                # For single band use band 0,
                                # for multiband use max across bands
                                if rack.effect_type == "COMP_SINGLE":
                                    _gr_levels.setdefault(ri, {})[ch] = (
                                        gr_vals[0] / 24.0 if gr_vals else 0.0)
                                elif rack.effect_type == "COMP_MULTI":
                                    for b in range(min(4, len(gr_vals))):
                                        _gr_levels.setdefault(ri, {})[ch] = (
                                            max(gr_vals) / 24.0)
                            except Exception:
                                pass
                except Exception:
                    pass

            except Exception: pass

        # Update rack LED states
        try:
            from Racks import update_led_states
            update_led_states(is_playing)
        except Exception: pass

    except Exception as e:
        print(f"[METER] timer error: {e}")

    # Force HUD redraw
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "NODE_EDITOR":
                    area.tag_redraw()
    except Exception: pass

    return METER_POLL_INTERVAL


def _ensure_meter_timer():
    global _meter_timer_registered
    if not _meter_timer_registered:
        bpy.app.timers.register(_meter_timer, first_interval=METER_POLL_INTERVAL)
        _meter_timer_registered = True
        print("[METER] timer registered")

def _cancel_meter_timer():
    global _meter_timer_registered
    if _meter_timer_registered:
        try: bpy.app.timers.unregister(_meter_timer)
        except Exception: pass
        _meter_timer_registered = False





# ---------------------------------------------------------------------------
