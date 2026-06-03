# =============================================================================
# core/meters.py
# VU meter timer and peak-hold ballistics.
#
# Meter levels come directly from the C++ engine (get_meter_rms / get_meter_peak)
# which are updated every audio callback (~10ms). No envelope pre-building needed.
# =============================================================================

import bpy

from core.constants import (
    MAX_CHANNELS, DEFAULT_CHANNELS, METER_POLL_INTERVAL,
    METER_DECAY, PEAK_HOLD_TIME,
)
from core.engine import get_engine


# Module-level meter state (read by ui/mixer/mixer_hud.py for drawing)
_engine_levels          = [0.0] * MAX_CHANNELS
_peak_hold              = [0.0] * MAX_CHANNELS
_peak_hold_timer        = [0.0] * MAX_CHANNELS
_meter_timer_registered = False
_redraw_counter         = 0   # throttle redraws to every other tick (10fps)


# ---------------------------------------------------------------------------
# Meter timer
# Runs independently so meters animate even without mouse movement.
# Reads directly from the C++ engine — accurate for any file length,
# no truncation issues, no memory overhead.
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

        fps    = scene.render.fps / scene.render.fps_base
        tracks = getattr(scene, "pb_sync_tracks", [])
        is_playing = bool(bpy.context.screen and
                          bpy.context.screen.is_animation_playing)
        current_frame = scene.frame_current

        # Auto-detect new channels — round up to next multiple of 9.
        # Only call sync once per detection — if len(tracks) still doesn't
        # match after sync, wait for next timer tick rather than looping.
        if scene.sequence_editor:
            highest = max((s.channel for s in scene.sequence_editor.sequences_all
                           if s.type == "SOUND" and s.sound), default=0)
            needed  = max(DEFAULT_CHANNELS, ((highest + 8) // 9) * 9)
            if needed > len(tracks):
                from ui.mixer.interaction import _sync_tracks_to_vse
                _sync_tracks_to_vse(scene, reset_values=False)
                tracks = getattr(scene, "pb_sync_tracks", [])
                for area in bpy.context.screen.areas:
                    area.tag_redraw()

        # Read live meter levels from C++ engine
        engine = get_engine()
        hj     = engine.get_engine() if engine else None

        new_rms  = [0.0] * MAX_CHANNELS
        new_peak = [0.0] * MAX_CHANNELS

        if hj:
            for i in range(MAX_CHANNELS):
                new_rms[i]  = hj.get_meter_rms(i)
                new_peak[i] = hj.get_meter_peak(i)
            # Diagnostic: print non-zero levels once every 100 ticks
            if not hasattr(_meter_timer, '_diag_count'):
                _meter_timer._diag_count = 0
            _meter_timer._diag_count += 1
            if _meter_timer._diag_count % 100 == 1:
                non_zero = [(i, f"{new_rms[i]:.3f}") for i in range(9) if new_rms[i] > 0.0]
                print(f"[METER] diag: hj={hj is not None}, is_playing={is_playing}, non_zero_rms={non_zero}")
        else:
            if not hasattr(_meter_timer, '_no_hj_warned'):
                _meter_timer._no_hj_warned = True
                print("[METER] WARNING: hj is None — engine not available, meters will be 0")

        # Update EngineState.current_frame for rack waveform display.
        # No cursor driving — Blender's cursor stays under its own control.
        if hj and is_playing:
            try:
                st = hj.get_state()
                if st:
                    fps_r = scene.render.fps / scene.render.fps_base
                    st.current_frame = int(hj.get_playhead_s() * fps_r)
            except Exception:
                pass

        # Feed the shared waveform ring buffer — used by noise gate and
        # deepfilternet racks. Populated here so the buffer fills during
        # playback regardless of which racks are currently visible.
        if is_playing and hj:
            try:
                import collections as _coll
                from ui.racks.rack_noisegate import (
                    _NG_RMS_HISTORY, _NG_HISTORY_LEN)
                for i in range(MAX_CHANNELS):
                    if new_rms[i] > 0.0 or i in _NG_RMS_HISTORY:
                        if i not in _NG_RMS_HISTORY:
                            _NG_RMS_HISTORY[i] = _coll.deque(
                                maxlen=_NG_HISTORY_LEN)
                        _NG_RMS_HISTORY[i].append((current_frame, new_rms[i]))
            except Exception:
                pass

        # Zero out muted channels
        for i in range(MAX_CHANNELS):
            if i < len(tracks) and tracks[i].mute:
                new_rms[i] = new_peak[i] = 0.0

        # Apply ballistics
        decay = METER_DECAY * (2.0 if is_playing else 0.5)

        for i in range(MAX_CHANNELS):
            rms  = min(new_rms[i],  1.0)
            peak = min(new_peak[i], 1.0)

            # Peak-hold dot
            if peak >= _peak_hold[i]:
                _peak_hold[i]       = peak
                _peak_hold_timer[i] = 0.0
            else:
                _peak_hold_timer[i] += METER_POLL_INTERVAL
                if _peak_hold_timer[i] > PEAK_HOLD_TIME:
                    _peak_hold[i] = max(0.0, _peak_hold[i] - decay)

            # Bar — instant attack, smooth decay
            _engine_levels[i] = (rms if rms > _engine_levels[i]
                                  else max(0.0, _engine_levels[i] - decay))

        # Read GR levels from C++ engine for rack display
        if hj:
            try:
                s = hj.get_state()
                try:
                    from Racks import _gr_levels, get_rack_channels
                    racks = getattr(scene, "pb_racks", []) if scene else []
                    for ri, rack in enumerate(racks):
                        if not rack.enabled: continue
                        assigned = get_rack_channels(rack)
                        for ch in assigned:
                            if ch < 0 or ch >= MAX_CHANNELS: continue
                            try:
                                gr_vals = s.get_gr_levels(ch)
                                if rack.effect_type == "COMP_SINGLE":
                                    _gr_levels.setdefault(ri, {})[ch] = (
                                        gr_vals[0] / 24.0 if gr_vals else 0.0)
                                elif rack.effect_type == "COMP_MULTI":
                                    _gr_levels.setdefault(ri, {})[ch] = (
                                        max(gr_vals) / 24.0 if gr_vals else 0.0)
                            except Exception:
                                pass
                except Exception:
                    pass
            except Exception:
                pass

        # Update rack LED states
        try:
            from Racks import update_led_states
            update_led_states(is_playing)
        except Exception:
            pass

    except Exception as e:
        print(f"[METER] timer error: {e}")

    # Throttled HUD redraw — every other tick (10fps) to halve GPU draw load.
    global _redraw_counter
    _redraw_counter += 1
    if _redraw_counter >= 2:
        _redraw_counter = 0
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == "NODE_EDITOR":
                        area.tag_redraw()
        except Exception:
            pass

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
        try:
            bpy.app.timers.unregister(_meter_timer)
        except Exception:
            pass
        _meter_timer_registered = False

# ---------------------------------------------------------------------------
# Legacy stubs — kept so Loader.py and audio.py imports don't break.
# Envelope building is no longer used; meters read from the C++ engine.
# ---------------------------------------------------------------------------
_envelope_cache = {}

def get_envelope(filepath, fps):
    return [], []

def prebuild_envelopes():
    pass
