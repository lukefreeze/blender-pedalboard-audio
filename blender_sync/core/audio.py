# =============================================================================
# core/audio.py
# All audio processing: fader logic, effect chain, channel sound building,
# playback handlers, timeline building, and engine enable/disable.
# =============================================================================

import os
import sys
import math
import wave
import struct
import tempfile

import bpy

from core.constants import (
    MAX_CHANNELS, DEFAULT_CHANNELS, FADER_MIN, FADER_MAX,
    GAIN_MIN, GAIN_MAX, SEND_BTN_H, SEND_BTN_GAP, SEND_MIN_SLOTS,
    SEND_START_Y, EFFECT_ABBREV,
)
from core.engine import get_engine


# ---------------------------------------------------------------------------
# These timelines are read by Racks.py for waveform display.
# They are module-level so Racks can import them directly.
# ---------------------------------------------------------------------------
_fft_timeline      = {}   # ch -> {snapshots, snap_frames, sr, fps, start_frame}
_fft_timeline_full = {}   # full-track version
_gr_timeline       = {}
_gr_timeline_full  = {}
_gate_timeline     = {}
_gate_timeline_full= {}
_fft_timeline_eq_input = {}

# Active channel handles: {channel_idx: {'handle': aud.Handle, ...}}
_pb_channels       = {}
_pb_proc_wav_cache = {}
_pb_full_wav_cache = {}
_pb_eq_pending     = {}
_pb_engine_active  = False
_pb_original_device= 'None'
_pb_eq_timer_registered = False

_pb_start_wall     = 0.0
_pb_start_frame    = 0
_pb_last_frame     = 0
_pb_last_loop_time = 0.0

def apply_fader_to_channel(channel_idx, old_fader, new_fader):
    """Apply proportional fader change to all strips on this VSE channel."""
    scene = bpy.context.scene
    if not scene or not scene.sequence_editor: return
    if abs(new_fader - old_fader) < 1e-6: return
    ratio = new_fader / old_fader
    for strip in scene.sequence_editor.sequences_all:
        if strip.type != "SOUND": continue
        if (strip.channel - 1) != channel_idx: continue
        if strip.volume == 0.0: strip.volume = 0.001
        strip.volume = max(0.001, strip.volume * ratio)
    # Sync to audio engine handle if playing
    _pb_engine_update_volume(channel_idx)


def apply_gain_to_channel(channel_idx, old_gain, new_gain):
    """Apply proportional gain change to all strips on this VSE channel."""
    scene = bpy.context.scene
    if not scene or not scene.sequence_editor: return
    if abs(new_gain - old_gain) < 1e-6: return
    ratio = new_gain / old_gain
    for strip in scene.sequence_editor.sequences_all:
        if strip.type != "SOUND": continue
        if (strip.channel - 1) != channel_idx: continue
        if strip.volume == 0.0: strip.volume = 0.001
        strip.volume = max(0.001, strip.volume * ratio)
    # Sync to audio engine handle if playing
    _pb_engine_update_volume(channel_idx)


# ---------------------------------------------------------------------------
# Pedalboard Audio Engine
#
# Replaces Blender's native audio playback when the HUD is active.
# Blender's audio device is set to None so only our engine plays.
# VSE strip.mute and strip.volume are never touched for audio routing —
# they remain as the user set them and the VSE looks completely normal.
#
# Architecture:
#   - One aud.Handle per VSE channel
#   - On PLAY: start all handles from correct time offset, run free
#   - On STOP: stop all handles, snap cursor to audio position
#   - Volume = strip.volume * fader * gain  (set on handle, not strip)
#   - Mute/solo operate on handle.volume AND strip.mute (VSE stays correct)
#   - EQ: biquad filter chain rebuilt when knob changes, handle restarted
#         from wall-clock position so audio stays in sync
#   - frame_change_post detects play/stop transitions only — never seeks
#     during playback (audio runs on hardware clock, no timer drift)
# ---------------------------------------------------------------------------

import time as _time
import math as _math

# Audio engine state
_pb_device        = None    # aud.Device — Blender's real output device
_pb_channels      = {}      # channel_idx -> dict (see _pb_start_channel)
_pb_start_wall    = 0.0     # wall clock time when play was pressed
_pb_start_frame   = 0       # scene frame when play was pressed
_pb_engine_active   = False   # True while HUD is open and engine owns audio
_pb_original_device = 'OpenAL'  # restored on HUD close
_pb_eq_pending      = {}        # channel_idx -> scheduled rebuild time
_pb_eq_debounce     = 0.25      # seconds to wait after last knob move before rebuilding
_pb_is_loop_restart = False     # True when stop was a loop transition
_pb_stop_time       = 0.0       # wall time when stop fired
_pb_last_frame      = -1        # last known frame, for loop jump detection
_pb_last_loop_time  = 0.0       # wall time of last loop restart (cooldown)
_pb_proc_wav_cache  = {}        # channel_idx -> last processed wav path (for instant loop restart)
_pb_full_wav_cache  = {}        # channel_idx -> full-track wav from frame_start (clean loop replay)

# FFT timeline — stores snapshots of per-band FFT during batch processing
# _fft_timeline[ch] = {
#   'snapshots': list of (n_bands, n_bins) arrays,
#   'fps': frames per snapshot,
#   'start_frame': timeline frame the audio starts at,
#   'sr': sample rate
# }
_fft_timeline      = {}   # trimmed — from position_seconds to end
_fft_timeline_full = {}   # full track — always from seq frame_start
_fft_timeline_eq_input = {}  # pre-EQ signal per channel — for EQ spectrum display
_gr_timeline       = {}
_gr_timeline_full  = {}
_gate_timeline     = {}   # gate open/closed + GR per channel per snapshot
_gate_timeline_full= {}





def _pb_get_device():
    global _pb_device
    if _pb_device is None:
        import aud
        _pb_device = aud.Device()
    return _pb_device


# ---------------------------------------------------------------------------
# Biquad EQ coefficient calculation
# Based on Audio EQ Cookbook by Robert Bristow-Johnson.
# sample_rate: Hz   gain_db: dB boost/cut   freq: Hz   Q: resonance (0.7 default)
# ---------------------------------------------------------------------------

def _biquad_low_shelf(gain_db, freq, sample_rate, Q=0.7):
    """Low shelf filter coefficients (b, a) for aud.Sound.filter()."""
    import math
    A  = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * freq / sample_rate
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / (2 * Q)
    sq    = 2 * math.sqrt(A) * alpha
    b0 =  A * ((A+1) - (A-1)*cw + sq)
    b1 =  2*A*((A-1) - (A+1)*cw)
    b2 =  A * ((A+1) - (A-1)*cw - sq)
    a0 =       (A+1) + (A-1)*cw + sq
    a1 = -2  * ((A-1) + (A+1)*cw)
    a2 =       (A+1) + (A-1)*cw - sq
    return ([b0/a0, b1/a0, b2/a0], [1.0, a1/a0, a2/a0])

def _biquad_high_shelf(gain_db, freq, sample_rate, Q=0.7):
    """High shelf filter coefficients."""
    import math
    A  = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * freq / sample_rate
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / (2 * Q)
    sq    = 2 * math.sqrt(A) * alpha
    b0 =  A * ((A+1) + (A-1)*cw + sq)
    b1 = -2*A*((A-1) + (A+1)*cw)
    b2 =  A * ((A+1) + (A-1)*cw - sq)
    a0 =       (A+1) - (A-1)*cw + sq
    a1 =  2  * ((A-1) - (A+1)*cw)
    a2 =       (A+1) - (A-1)*cw - sq
    return ([b0/a0, b1/a0, b2/a0], [1.0, a1/a0, a2/a0])

def _biquad_peak(gain_db, freq, sample_rate, Q=1.0):
    """Peaking EQ filter coefficients."""
    import math
    A     = 10 ** (gain_db / 40.0)
    w0    = 2 * math.pi * freq / sample_rate
    alpha = math.sin(w0) / (2 * Q)
    cw    = math.cos(w0)
    b0 = 1 + alpha * A
    b1 = -2 * cw
    b2 = 1 - alpha * A
    a0 = 1 + alpha / A
    a1 = -2 * cw
    a2 = 1 - alpha / A
    return ([b0/a0, b1/a0, b2/a0], [1.0, a1/a0, a2/a0])


def _apply_effect_chain(samples, channel_idx, sr):
    """Apply all racks assigned to channel_idx IN UI ORDER via the C++ engine.

    Builds the engine's effect_chain slot table from scene.pb_racks in list
    order, then makes a single process_buffer() call. The C++ engine runs
    every enabled slot in slot order, so rack order is exactly respected.

    Also captures the signal at the EQ input position (pre-EQ, post-compressor)
    and stores it as _fft_timeline_eq_input[channel_idx] so the EQ spectrum
    display reflects what is actually arriving at the EQ rack.

    Returns (processed_np, pre_comp_np).
    """
    import numpy as _np

    engine = get_engine()
    scene  = bpy.context.scene
    if not scene or not engine:
        return samples, samples

    try:
        from Racks import get_rack_channels as _grc
    except Exception:
        return samples, samples

    racks = getattr(scene, "pb_racks", [])
    state = engine.get_state()

    # Clear all effect slots for this channel
    for slot in range(8):
        try:
            fx = state.get_effect_slot(channel_idx, slot)
            fx.enabled = False
            fx.type    = 0   # FX_NONE
        except Exception:
            pass

    slot_idx      = 0
    chain_log     = []
    pre_comp      = samples.copy()
    hit_comp      = False
    eq_slot_start = None   # slot index where EQ first appears

    for rack in racks:
        if not rack.enabled:
            continue
        if channel_idx not in _grc(rack):
            continue
        if slot_idx >= 8:
            break

        etype = rack.effect_type

        try:
            fx = state.get_effect_slot(channel_idx, slot_idx)

            if etype == "COMP_SINGLE":
                if not hit_comp:
                    hit_comp = True
                fx.type    = engine.FX_COMP_SINGLE
                fx.enabled = True
                fx.params  = [rack.p0, rack.p1, rack.p2, rack.p3,
                              rack.p4, rack.p5,
                              0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                              0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                              0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                chain_log.append("COMP_SINGLE")
                slot_idx += 1

            elif etype == "COMP_MULTI":
                if not hit_comp:
                    hit_comp = True
                fx.type    = engine.FX_COMP_MULTI
                fx.enabled = True
                fx.params  = [rack.p0,  rack.p1,  rack.p2,  rack.p3,
                              rack.p4,  rack.p5,  rack.p6,  rack.p7,
                              rack.p8,  rack.p9,  rack.p10, rack.p11,
                              rack.p12, rack.p13, rack.p14, rack.p15,
                              rack.p16, rack.p17, rack.p18, rack.p19,
                              rack.p20, rack.p21, rack.p22, rack.p23]
                chain_log.append("COMP_MULTI")
                slot_idx += 1

            elif etype == "EQ":
                if eq_slot_start is None:
                    eq_slot_start = slot_idx   # remember where EQ starts
                fx.type    = engine.FX_EQ_PARAM
                fx.enabled = True
                fx.params  = ([getattr(rack, f'p{i}', 0.5 if i < 7 else 0.0)
                               for i in range(21)] + [0.0, 0.0, 0.0])
                chain_log.append("EQ7")
                slot_idx += 1

            elif etype == "REVERB":
                fx.type    = engine.FX_REVERB_PARAM
                fx.enabled = True
                # p0=room, p1=damp, p2=wet, p3=pre_delay, p4=width
                fx.params  = ([getattr(rack, f'p{i}', 0.0)
                               for i in range(5)] + [0.0]*19)
                chain_log.append("REVERB")
                slot_idx += 1

            elif etype == "NOISE_GATE":
                fx.type    = engine.FX_GATE_PARAM
                fx.enabled = True
                # p0=threshold, p1=attack, p2=hold, p3=release, p4=range
                fx.params  = ([getattr(rack, f'p{i}', 0.0)
                               for i in range(5)] + [0.0]*19)
                chain_log.append("GATE")
                slot_idx += 1

            elif etype == "DELAY":
                fx.type    = engine.FX_DELAY_PARAM
                fx.enabled = True
                # p0=time, p1=feedback, p2=mix, p3=spread, p4=filter
                fx.params  = ([getattr(rack, f'p{i}', 0.0)
                               for i in range(5)] + [0.0]*19)
                chain_log.append("DELAY")
                slot_idx += 1

        except Exception as _se:
            print(f"[CHAIN] ch{channel_idx+1} slot{slot_idx} error: {_se}")

    if not chain_log:
        print(f"[CHAIN] ch{channel_idx+1} no active racks — audio unchanged")
        return samples, samples

    # --- Capture the signal at the EQ input ---
    # If there are effects before the EQ, run just those slots first to get
    # the intermediate signal, store it for the EQ spectrum display.
    if eq_slot_start is not None and eq_slot_start > 0:
        try:
            # Temporarily disable all slots at and after the EQ
            for s in range(eq_slot_start, 8):
                try:
                    state.get_effect_slot(channel_idx, s).enabled = False
                except Exception:
                    pass
            # Run just the pre-EQ slots to get the EQ input signal
            pre_eq_buf = _np.ascontiguousarray(samples, dtype=_np.float32)
            pre_eq_sig = _np.asarray(
                engine.process_buffer(channel_idx, pre_eq_buf, sr),
                dtype=_np.float32)
            # Store for the EQ spectrum display
            _fft_timeline_eq_input[channel_idx] = pre_eq_sig
            # Re-enable the EQ slots
            for s in range(eq_slot_start, slot_idx):
                try:
                    state.get_effect_slot(channel_idx, s).enabled = True
                except Exception:
                    pass
        except Exception as _ee:
            # Non-fatal — EQ display falls back to full timeline
            _fft_timeline_eq_input.pop(channel_idx, None)
    else:
        # No effects before EQ — EQ input IS the raw signal
        _fft_timeline_eq_input[channel_idx] = samples

    # --- Single process_buffer call — C++ runs all slots in order ---
    try:
        buf       = _np.ascontiguousarray(samples, dtype=_np.float32)
        processed = _np.asarray(
            engine.process_buffer(channel_idx, buf, sr),
            dtype=_np.float32)
        print(f"[CHAIN] ch{channel_idx+1}: {' → '.join(chain_log)}")

        # Apply stereo pan post-effects (constant power law)
        # pan=0.5 → centre, pan=0 → hard left, pan=1 → hard right
        try:
            import math as _mpan
            tracks_pan = getattr(scene, 'pb_sync_tracks', [])
            if channel_idx < len(tracks_pan):
                pan = getattr(tracks_pan[channel_idx], 'pan', 0.5)
                if abs(pan - 0.5) > 0.01 and processed.ndim == 2 and processed.shape[1] >= 2:
                    angle   = pan * (_mpan.pi / 2.0)
                    gain_l  = _mpan.cos(angle)
                    gain_r  = _mpan.sin(angle)
                    processed = processed.copy()
                    processed[:, 0] *= gain_l
                    processed[:, 1] *= gain_r
        except Exception:
            pass  # pan is non-critical — never block audio

        return processed, pre_comp
    except Exception as _pe:
        print(f"[CHAIN] ch{channel_idx+1} process_buffer error: {_pe}")
        return samples, samples






# Reads strip volumes from VSE strips (user's values, not modified by us).
# Joins multiple strips on same channel with silence for gaps.
# ---------------------------------------------------------------------------

def _pb_build_channel_sound(channel_idx, start_seconds):
    """
    Build a single aud.Sound for channel_idx starting at start_seconds.

    Key facts from strip properties:
      strip.frame_start        — timeline position of strip origin (CAN BE NEGATIVE
                                  if the strip was slid left past frame 1)
      strip.frame_final_end    — timeline frame where audible content ends
      strip.frame_offset_start — frames into the SOURCE FILE where audio begins
                                  (non-zero when left edge of strip was cut)
      strip.frame_offset_end   — frames trimmed from the END of the source file

    The audible timeline window is always:
      visible_start = max(strip.frame_start, scene.frame_start)
      visible_end   = strip.frame_final_end   (already accounts for offsets)

    The corresponding file position for any timeline frame F is:
      file_frame = frame_offset_start + (F - strip.frame_start)
    """
    import aud
    scene = bpy.context.scene
    if not scene or not scene.sequence_editor: return None, 0.0

    fps           = scene.render.fps / scene.render.fps_base
    # Respect preview range if active
    seq_start_s = (scene.frame_preview_start if scene.use_preview_range
                   else scene.frame_start) / fps
    seq_end_s   = (scene.frame_preview_end if scene.use_preview_range
                   else scene.frame_end) / fps

    tracks = getattr(scene, "pb_sync_tracks", [])
    track  = tracks[channel_idx] if channel_idx < len(tracks) else None

    # Collect strips on this channel.
    # Do NOT filter by strip.mute here — mute is handled via handle.volume.
    # strip.mute is purely a VSE visual indicator controlled by the user.
    strips = sorted(
        [s for s in scene.sequence_editor.sequences_all
         if s.type == "SOUND" and s.sound
         and (s.channel - 1) == channel_idx],
        key=lambda s: s.frame_final_end - s.frame_final_duration
    )
    if not strips: return None, 0.0

    joined   = None
    prev_end = start_seconds   # track where we are in output timeline seconds

    for strip in strips:
        # Visible timeline window for this strip (seconds)
        # Clamp to sequence bounds and our start position
        # Correct visible timeline start:
        # frame_final_end - frame_final_duration gives the actual start frame
        # regardless of where strip.frame_start is (which can be negative
        # or shared between multiple strips cut from the same original).
        # This is the only reliable way to get the visible start.
        actual_start_frame = strip.frame_final_end - strip.frame_final_duration
        vis_start_s = max(actual_start_frame / fps, seq_start_s)
        vis_end_s   = min(strip.frame_final_end / fps, seq_end_s)

        # Skip if entirely outside what we need
        if vis_end_s <= start_seconds: continue
        if vis_start_s >= seq_end_s:   continue

        # Actual start of what we want from this strip
        play_start_s = max(vis_start_s, start_seconds)
        play_end_s   = vis_end_s
        duration     = play_end_s - play_start_s
        if duration < 0.001: continue

        # File position corresponding to play_start_s:
        #   file_frame = frame_offset_start + (timeline_frame - strip.frame_start)
        # In seconds:
        #   file_pos = (frame_offset_start / fps) + (play_start_s - strip.frame_start/fps)
        # Correct file offset formula (verified empirically):
        # frame_offset_start is how far into the source file the strip begins,
        # measured from the strip origin — it already fully encodes the cut.
        # We only add how far into the strip's VISIBLE portion we are starting,
        # i.e. (play_start_s - vis_start_s), not from strip.frame_start.
        file_offset_start_s = getattr(strip, 'frame_offset_start', 0.0) / fps
        file_pos_start      = file_offset_start_s + (play_start_s - vis_start_s)
        file_pos_end        = file_pos_start + duration

        if file_pos_start < 0.0:
            file_pos_start = 0.0
        if file_pos_end <= file_pos_start:
            continue

        filepath = bpy.path.abspath(strip.sound.filepath)
        try:
            raw_sound  = aud.Sound.file(filepath)
            strip_sound = raw_sound.limit(file_pos_start, file_pos_end)

            # Build silence matching source audio spec to avoid sample rate
            # mismatch glitches. resample+rechannel before limit is required.
            specs   = raw_sound.specs
            src_sr  = int(specs[0])
            src_nch = int(specs[1])

            def make_silence(duration_s):
                return (aud.Sound.silence()
                        .resample(src_sr, False)
                        .rechannel(src_nch)
                        .limit(0.0, duration_s))

            if joined is None:
                lead = play_start_s - start_seconds
                if lead > 0.01:
                    joined = make_silence(lead).join(strip_sound)
                else:
                    joined = strip_sound
            else:
                gap = play_start_s - prev_end
                if gap > 0.01:
                    joined = joined.join(make_silence(gap))
                joined = joined.join(strip_sound)

            prev_end = play_end_s
            print(f"[ENGINE] ch{channel_idx+1} strip '{strip.name}' "
                  f"file[{file_pos_start:.2f}→{file_pos_end:.2f}s] "
                  f"timeline[{play_start_s:.2f}→{play_end_s:.2f}s]")

        except Exception as e:
            print(f"[ENGINE] strip build failed ch{channel_idx+1} "
                  f"'{strip.name}': {e}")
            continue

    if joined is None: return None, 0.0

    # Hard limit to sequence end — prevents audio running past loop point
    total_duration = seq_end_s - start_seconds
    if total_duration > 0.0:
        joined = joined.limit(0.0, total_duration)
    else:
        return None, 0.0

    # Apply 3-band EQ
    try:
        sample_rate = int(aud.Sound.file(
            bpy.path.abspath(strips[0].sound.filepath)).specs[0])
    except Exception:
        sample_rate = 44100

    eq_low = eq_mid = eq_high = 0.0
    if track:
        eq_low, eq_mid, eq_high = track.eq_low, track.eq_mid, track.eq_high

    try:
        if abs(eq_low)  > 0.1:
            b, a = _biquad_low_shelf(eq_low,  200.0,  sample_rate)
            joined = joined.filter(b, a)
        if abs(eq_mid)  > 0.1:
            b, a = _biquad_peak(eq_mid,        1000.0, sample_rate)
            joined = joined.filter(b, a)
        if abs(eq_high) > 0.1:
            b, a = _biquad_high_shelf(eq_high, 8000.0, sample_rate)
            joined = joined.filter(b, a)
    except Exception as e:
        print(f"[ENGINE] EQ filter failed ch{channel_idx+1}: {e}")

    avg_vol = sum(s.volume for s in strips) / len(strips)
    return joined, avg_vol


def _pb_channel_volume(channel_idx):
    """Calculate handle.volume for a channel.
    Returns 0 if muted or if another channel is soloed.
    Volume is read from strip.volume which has fader and gain baked in."""
    scene  = bpy.context.scene
    tracks = getattr(scene, "pb_sync_tracks", []) if scene else []

    # Muted channel — silent
    if channel_idx < len(tracks) and tracks[channel_idx].mute:
        return 0.0

    # Solo: if any channel is soloed and this one isn't, silent
    soloed = {i for i, t in enumerate(tracks) if t.solo}
    if soloed and channel_idx not in soloed:
        return 0.0

    if not scene or not scene.sequence_editor: return 1.0
    strips = [s for s in scene.sequence_editor.sequences_all
              if s.type == "SOUND" and (s.channel - 1) == channel_idx]
    if not strips: return 1.0
    return sum(s.volume for s in strips) / len(strips)


def _pb_wire_rack_to_engine(channel_idx):
    """Write rack compressor params into the C++ effect chain for a channel.
    Called at play-start so the real-time DSP uses current knob values.
    """
    engine = get_engine()
    if not engine:
        return

    scene = bpy.context.scene
    if not scene:
        return

    racks  = getattr(scene, "pb_racks", [])
    state  = engine.get_state()

    # Clear all effect slots for this channel first
    for slot in range(8):
        try:
            fx = state.get_effect_slot(channel_idx, slot)
            fx.enabled = False
            fx.type    = 0  # FX_NONE
        except Exception:
            pass

    slot_idx = 0

    print(f"[WIRE] ch{channel_idx+1} checking {len(racks)} racks")
    for rack in racks:
        if not rack.enabled:
            continue

        try:
            from Racks import get_rack_channels
            assigned = get_rack_channels(rack)
        except Exception:
            assigned = []

        print(f"[WIRE]   rack type={rack.effect_type} assigned={assigned}")
        if channel_idx not in assigned:
            continue

        etype = rack.effect_type

        if etype == "COMP_SINGLE":
            try:
                fx         = state.get_effect_slot(channel_idx, slot_idx)
                fx.type    = engine.FX_COMP_SINGLE
                fx.enabled = True
                fx.params  = [rack.p0, rack.p1, rack.p2, rack.p3,
                              rack.p4, rack.p5,
                              0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                              0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                              0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                slot_idx  += 1
                print(f"[WIRE] ch{channel_idx+1} slot{slot_idx-1} "
                      f"COMP_SINGLE thr={rack.p0:.2f} ratio={rack.p1:.2f} "
                      f"knee={rack.p5:.2f}")
            except Exception as e:
                print(f"[WIRE] COMP_SINGLE failed: {e}")

        elif etype == "COMP_MULTI":
            try:
                fx         = state.get_effect_slot(channel_idx, slot_idx)
                fx.type    = engine.FX_COMP_MULTI
                fx.enabled = True
                fx.params  = [rack.p0,  rack.p1,  rack.p2,  rack.p3,
                              rack.p4,  rack.p5,  rack.p6,  rack.p7,
                              rack.p8,  rack.p9,  rack.p10, rack.p11,
                              rack.p12, rack.p13, rack.p14, rack.p15,
                              rack.p16, rack.p17, rack.p18, rack.p19,
                              rack.p20, rack.p21, rack.p22, rack.p23]
                slot_idx  += 1
                print(f"[WIRE] ch{channel_idx+1} slot{slot_idx-1} "
                      f"COMP_MULTI band0 thr={rack.p0:.2f} ratio={rack.p4:.2f}")
            except Exception as e:
                print(f"[WIRE] COMP_MULTI failed: {e}")

        elif etype == "EQ":
            # EQ now runs in C++ via FX_EQ_PARAM.
            # Wire params into the slot so the GR metering chunk-loop
            # also applies EQ correctly when it re-runs process_buffer.
            try:
                fx         = state.get_effect_slot(channel_idx, slot_idx)
                fx.type    = engine.FX_EQ_PARAM
                fx.enabled = True
                fx.params  = ([getattr(rack, f'p{i}', 0.5 if i < 7 else 0.0)
                               for i in range(21)] + [0.0, 0.0, 0.0])
                slot_idx  += 1
                print(f"[WIRE] ch{channel_idx+1} slot{slot_idx-1} EQ7 — C++ biquad")
            except Exception as e:
                print(f"[WIRE] EQ wiring failed: {e}")

        elif etype == "REVERB":
            try:
                fx         = state.get_effect_slot(channel_idx, slot_idx)
                fx.type    = engine.FX_REVERB_PARAM
                fx.enabled = True
                fx.params  = ([getattr(rack, f'p{i}', 0.0)
                               for i in range(5)] + [0.0]*19)
                slot_idx  += 1
                print(f"[WIRE] ch{channel_idx+1} slot{slot_idx-1} REVERB "
                      f"room={rack.p0:.2f} damp={rack.p1:.2f} wet={rack.p2:.2f}")
            except Exception as e:
                print(f"[WIRE] REVERB wiring failed: {e}")

        elif etype == "NOISE_GATE":
            try:
                fx         = state.get_effect_slot(channel_idx, slot_idx)
                fx.type    = engine.FX_GATE_PARAM
                fx.enabled = True
                fx.params  = ([getattr(rack, f'p{i}', 0.0)
                               for i in range(5)] + [0.0]*19)
                slot_idx  += 1
                print(f"[WIRE] ch{channel_idx+1} slot{slot_idx-1} GATE "
                      f"thr={rack.p0:.2f} atk={rack.p1:.2f} rel={rack.p3:.2f}")
            except Exception as e:
                print(f"[WIRE] GATE wiring failed: {e}")

        elif etype == "DELAY":
            try:
                fx         = state.get_effect_slot(channel_idx, slot_idx)
                fx.type    = engine.FX_DELAY_PARAM
                fx.enabled = True
                # p0=time, p1=feedback, p2=mix, p3=spread, p4=filter
                fx.params  = ([getattr(rack, f'p{i}', 0.0)
                               for i in range(5)] + [0.0]*19)
                slot_idx  += 1
                delay_ms = 1.0 + rack.p0 * 1999.0
                print(f"[WIRE] ch{channel_idx+1} slot{slot_idx-1} DELAY "
                      f"t={delay_ms:.0f}ms fb={rack.p1:.2f} mix={rack.p2:.2f} "
                      f"ping={'Y' if rack.p3 > 0.5 else 'N'}")
            except Exception as e:
                print(f"[WIRE] DELAY wiring failed: {e}")

        if slot_idx >= 8:
            break

    if slot_idx > 0:
        print(f"[WIRE] ch{channel_idx+1} wired {slot_idx} effects — DSP ACTIVE")
    else:
        print(f"[WIRE] ch{channel_idx+1} no rack assigned")


def _pb_reprocess_channel(channel_idx):
    """Reprocess and restart a channel from current playhead position.
    Called when rack settings change during playback."""
    import bpy as _bpy
    scene = _bpy.context.scene
    if not scene: return
    is_playing = getattr(_bpy.context.screen, 'is_animation_playing', False)
    if not is_playing: return
    fps = scene.render.fps / scene.render.fps_base

    # Clear the wav cache so the fresh reprocess is never skipped
    import os
    cached = _pb_proc_wav_cache.get(channel_idx)
    if cached:
        try:
            if os.path.exists(cached): os.remove(cached)
        except Exception:
            pass
        _pb_proc_wav_cache.pop(channel_idx, None)

    # Use scene.frame_current for position — more reliable than wall clock
    # which drifts after loops and repeated reprocesses
    seq_start_s = ((scene.frame_preview_start if scene.use_preview_range
                    else scene.frame_start) / fps)
    seq_end_s   = ((scene.frame_preview_end   if scene.use_preview_range
                    else scene.frame_end)   / fps)
    current_pos = max(seq_start_s,
                      min(seq_end_s - 0.1,
                          scene.frame_current / fps))

    print(f"[ENGINE] ch{channel_idx+1} reprocessing at {current_pos:.2f}s "
          f"(frame {scene.frame_current}, "
          f"seq {seq_start_s:.1f}s→{seq_end_s:.1f}s)")

    # Sanity check — if current_pos is past end, snap to start
    if current_pos >= seq_end_s - 0.5:
        current_pos = seq_start_s
        print(f"[ENGINE] ch{channel_idx+1} position past end, snapping to start")

    _pb_start_channel(channel_idx, current_pos)


def _build_timelines(channel_idx, proc_np, sr, scene_fps,
                     position_seconds, full_track=False, raw_np=None):
    """Build FFT and GR timelines from processed audio numpy array.
    proc_np: processed output — used for FFT spectrum display.
    raw_np:  unprocessed input — used for GR computation (signal before compression).
    GR values are real soft-knee gain reduction in dB (0..24).
    """
    import numpy as _np2
    snap_frames   = int(sr * 0.08)
    BINS_PER_BAND = 32
    CROSSOVERS    = [20, 120, 800, 5000, 20000]

    # Use input audio for GR if provided, else fall back to output
    gr_source = raw_np if raw_np is not None else proc_np
    mono_gr   = gr_source[:, 0] if gr_source.ndim == 2 else gr_source.flatten()

    # Read rack params for GR computation
    band_params = []
    try:
        _scene_bt = bpy.context.scene
        _racks_bt = getattr(_scene_bt, "pb_racks", []) if _scene_bt else []
        from Racks import get_rack_channels as _grc_bt
        for _rk in _racks_bt:
            if _rk.enabled and channel_idx in _grc_bt(_rk):
                if _rk.effect_type == "COMP_MULTI":
                    for _b in range(4):
                        band_params.append((
                            -40.0 + getattr(_rk, f'p{_b}',    0.45) * 40.0,
                             1.0  + getattr(_rk, f'p{_b+4}',  0.05) * 19.0,
                             0.5  + getattr(_rk, f'p{_b+20}', 0.14) * 23.5,
                        ))
                    break
                elif _rk.effect_type == "COMP_SINGLE":
                    thr = -40.0 + getattr(_rk, 'p0', 0.55) * 40.0
                    rat =  1.0  + getattr(_rk, 'p1', 0.11) * 19.0
                    kne =  0.5  + getattr(_rk, 'p5', 0.15) * 23.5
                    band_params = [(thr, rat, kne)] * 4
                    break
    except Exception:
        pass

    mono    = proc_np[:, 0] if proc_np.ndim == 2 else proc_np.flatten()
    n_snaps = max(1, len(mono) // snap_frames)
    fft_snaps = []
    gr_snaps  = []

    for snap_i in range(n_snaps):
        start    = snap_i * snap_frames
        chunk    = mono[start:start + snap_frames]
        if len(chunk) < 128:
            break
        win      = _np2.hanning(len(chunk)).astype(_np2.float32)
        fft_c    = _np2.abs(_np2.fft.rfft(chunk * win))
        fft_c   /= (len(chunk) * 0.5 + 1e-9)
        freq_res = sr / len(chunk)
        n_fft    = len(fft_c)

        # FFT bins per band (from processed output — for spectrum display)
        band_fft = []
        for b in range(4):
            freqs = _np2.logspace(
                _np2.log10(max(CROSSOVERS[b], 1)),
                _np2.log10(CROSSOVERS[b+1]), BINS_PER_BAND)
            idxs  = _np2.clip((freqs/freq_res).astype(int), 0, n_fft-1)
            band_fft.append(_np2.clip(
                (20*_np2.log10(fft_c[idxs]+1e-9)+80)/80,
                0.0, 1.0).astype(_np2.float32))
        fft_snaps.append(_np2.stack(band_fft))

        # GR from C++ engine — process this chunk and read gr_levels directly
        gr_snap = _np2.zeros(4, dtype=_np2.float32)
        try:
            _eng_bt = get_engine()
            if _eng_bt:
                chunk_raw = gr_source[start:start + snap_frames]
                if chunk_raw.shape[0] >= 64:
                    if chunk_raw.ndim == 1:
                        chunk_raw = chunk_raw.reshape(-1, 1)
                    chunk_c = _np2.ascontiguousarray(chunk_raw, dtype=_np2.float32)
                    _eng_bt.process_buffer(channel_idx, chunk_c, sr)
                    gv = _eng_bt.get_state().get_gr_levels(channel_idx)
                    for b in range(4):
                        gr_snap[b] = min(24.0, max(0.0, float(gv[b])))
        except Exception:
            pass
        gr_snaps.append(gr_snap)

    if not fft_snaps:
        return

    _scene_bt2 = bpy.context.scene
    _sf_bt2 = float((_scene_bt2.frame_preview_start
                     if _scene_bt2 and _scene_bt2.use_preview_range
                     else (_scene_bt2.frame_start if _scene_bt2 else 1)))
    tl = {
        'snapshots'  : _np2.stack(fft_snaps),
        'snap_frames': snap_frames,
        'start_frame': _sf_bt2,
        'sr': sr, 'fps': scene_fps,
    }
    gl = {
        'snapshots'  : _np2.stack(gr_snaps),
        'snap_frames': snap_frames,
        'start_frame': _sf_bt2,
        'sr': sr, 'fps': scene_fps,
    }
    if full_track:
        _fft_timeline_full[channel_idx] = tl
        _gr_timeline_full[channel_idx]  = gl
    else:
        _fft_timeline[channel_idx] = tl
        _gr_timeline[channel_idx]  = gl

    # --- Gate timeline: open/closed + GR per 80ms snapshot ---
    # Uses gr_levels[ch][1]=GR magnitude, gr_levels[ch][2]=open flag
    # written by apply_noise_gate per chunk.
    try:
        from Racks import get_rack_channels as _grc_gt
        scene_gt = bpy.context.scene
        racks_gt = getattr(scene_gt, "pb_racks", []) if scene_gt else []
        has_gate = False
        for r in racks_gt:
            _rch = _grc_gt(r)
            if r.enabled and r.effect_type == "NOISE_GATE" and channel_idx in _rch:
                has_gate = True
                break
        if not has_gate:
            print(f"[GATE_TL_SKIP] ch{channel_idx+1} full_track={full_track} "
                  f"racks={len(racks_gt)} "
                  f"gate_racks={[r.effect_type for r in racks_gt if r.effect_type=='NOISE_GATE']} "
                  f"assigned={[_grc_gt(r) for r in racks_gt if r.effect_type=='NOISE_GATE']}")
        if has_gate:
            gate_snaps = []
            # Reset the gate DSP state so envelope follower starts fresh
            try:
                engine.create_channel(channel_idx)
                # Re-wire the gate effect so slots are set after create_channel reset
                from Racks import get_rack_params as _grp_gt
                for r in racks_gt:
                    if r.enabled and r.effect_type == "NOISE_GATE" and channel_idx in _grc_gt(r):
                        p = _grp_gt(r)
                        engine.set_effect(channel_idx, 0, engine.FX_GATE_PARAM,
                                          p[0], p[1], p[2], p[3], p[4], 0.0, 0.0, 0.0)
                        break
            except Exception:
                pass
            # Use raw pre-effects audio for gate timeline (same source as FFT)
            _gate_src = raw_np if raw_np is not None else proc_np
            inp_gt = _np2.asarray(_gate_src, dtype=_np2.float32)
            if inp_gt.ndim == 1:
                inp_gt = inp_gt.reshape(-1, 1)
            n_snaps_gt = max(1, inp_gt.shape[0] // snap_frames)
            for snap_i in range(n_snaps_gt):
                s = snap_i * snap_frames
                chunk_gt = inp_gt[s:s + snap_frames]
                if chunk_gt.shape[0] < 64:
                    break
                chunk_gt = _np2.ascontiguousarray(chunk_gt, dtype=_np2.float32)
                try:
                    engine.process_buffer(channel_idx, chunk_gt, int(sr))
                    gr_db   = float(engine.get_state().get_gr_levels(channel_idx)[1])
                    is_open = float(engine.get_state().get_gr_levels(channel_idx)[2])
                    # Fallback: if is_open is always 1 (state not updating),
                    # derive from GR amount — if gate is attenuating, it's closed
                    if is_open > 0.5 and gr_db > 2.0:
                        is_open = 0.0
                    gate_snaps.append(_np2.array([gr_db, is_open], dtype=_np2.float32))
                except Exception:
                    gate_snaps.append(_np2.array([0.0, 1.0], dtype=_np2.float32))
            if gate_snaps:
                gate_tl = {
                    'snapshots'  : _np2.stack(gate_snaps),
                    'snap_frames': snap_frames,
                    'start_frame': _sf_bt2,
                    'sr': float(sr), 'fps': float(scene_fps),
                }
                if full_track:
                    _gate_timeline_full[channel_idx] = gate_tl
                else:
                    _gate_timeline[channel_idx]      = gate_tl
    except Exception:
        pass  # gate timeline is decorative — never block playback


def _pb_start_channel(channel_idx, position_seconds):
    """Start or restart a channel handle from position_seconds."""
    global _pb_channels
    import aud

    # Keep existing handle alive until new sound is ready — prevents audio gap
    existing = _pb_channels.get(channel_idx)

    sound, base_vol = _pb_build_channel_sound(channel_idx, position_seconds)
    if sound is None:
        # Nothing to play — stop existing and clear
        if existing:
            try: existing['handle'].stop()
            except Exception: pass
        _pb_channels.pop(channel_idx, None)
        return

    # Wire rack params into C++ effect chain before playback starts
    _pb_wire_rack_to_engine(channel_idx)

    # Batch DSP processing via C++ engine.
    # Blender 4.5 does not expose ISound* pointers so we cannot intercept
    # the audio thread. Instead we process offline:
    #   1. Extract raw samples from the aud.Sound via .data()
    #   2. Pass numpy array to C++ process_buffer() — full DSP runs here
    #   3. Write processed samples to temp wav
    #   4. Play the temp wav via aud.Device
    engine  = get_engine()
    pb_sound = sound   # fallback

    if engine:
        # Only process through DSP if a rack is actually assigned to this channel
        has_rack = False
        try:
            from Racks import get_rack_channels
            scene = bpy.context.scene
            racks = getattr(scene, "pb_racks", []) if scene else []
            for rack in racks:
                if rack.enabled and channel_idx in get_rack_channels(rack):
                    has_rack = True
                    break
        except Exception:
            pass

        if has_rack:
            try:
                import tempfile, os, aud as _aud
                import numpy as np
                import wave, struct

                # Get specs
                try:
                    sr  = int(sound.specs[0])
                    nch = int(sound.specs[1])
                except Exception:
                    sr, nch = 44100, 2

                # Extract raw samples — returns (n_frames, n_channels) float32
                samples = sound.data()
                if samples is None or samples.size == 0:
                    raise RuntimeError("sound.data() returned empty array")

                if samples.ndim == 1:
                    samples = samples.reshape(-1, 1)
                samples = np.ascontiguousarray(samples, dtype=np.float32)

                print(f"[ENGINE] ch{channel_idx+1} processing "
                      f"{samples.shape[0]} frames × {samples.shape[1]}ch "
                      f"@ {sr}Hz — effect chain")

                # Run effect chain in UI rack order (EQ + compressors, order-aware)
                processed, raw_for_gr = _apply_effect_chain(
                    samples, channel_idx, sr)

                # Build FFT timeline from processed audio
                # Compute FFT every ~100ms = one snapshot per 100ms of audio
                # Stored as list of 4-band × 32-bin arrays for Racks.py to read
                try:
                    proc_for_fft = np.asarray(processed, dtype=np.float32)
                    if proc_for_fft.ndim == 2:
                        mono_fft = proc_for_fft[:, 0]  # use left channel
                    else:
                        mono_fft = proc_for_fft.flatten()

                    snap_frames   = int(sr * 0.08)  # snapshot every 80ms
                    n_snaps       = max(1, len(mono_fft) // snap_frames)
                    fft_snaps     = []
                    BINS_PER_BAND = 32  # 32 bins per band → 128 total
                    TOTAL_BINS    = BINS_PER_BAND * 4

                    # Crossover frequencies matching C++ Linkwitz-Riley
                    # Low: 20-120Hz, L-Mid: 120-800Hz,
                    # H-Mid: 800-5000Hz, High: 5000-20000Hz
                    CROSSOVERS = [20, 120, 800, 5000, 20000]

                    for snap_i in range(n_snaps):
                        start = snap_i * snap_frames
                        chunk = mono_fft[start:start + snap_frames]
                        if len(chunk) < 128:
                            break
                        win   = np.hanning(len(chunk)).astype(np.float32)
                        fft_c = np.abs(np.fft.rfft(chunk * win))
                        fft_c = fft_c / (len(chunk) * 0.5 + 1e-9)

                        # Frequency resolution per bin
                        freq_res  = sr / len(chunk)
                        n_bins_fft = len(fft_c)

                        # Map each band's frequency range to FFT bins
                        # using logarithmic spacing within each band
                        band_snap_list = []
                        for b in range(4):
                            f_lo = CROSSOVERS[b]
                            f_hi = CROSSOVERS[b + 1]
                            # Log-spaced frequency points within this band
                            freqs  = np.logspace(
                                np.log10(max(f_lo, 1)),
                                np.log10(f_hi),
                                BINS_PER_BAND)
                            # Map frequencies to FFT bin indices
                            idxs   = np.clip(
                                (freqs / freq_res).astype(int),
                                0, n_bins_fft - 1)
                            band_vals = fft_c[idxs]
                            band_db   = np.clip(
                                (20*np.log10(band_vals+1e-9)+80)/80,
                                0.0, 1.0).astype(np.float32)
                            band_snap_list.append(band_db)

                        band_snap = np.stack(band_snap_list)
                        fft_snaps.append(band_snap)

                    # Stack into (n_snaps, 4, 32)
                    fft_array = np.stack(fft_snaps)
                    scene_fps = (bpy.context.scene.render.fps /
                                 bpy.context.scene.render.fps_base
                                 if bpy.context.scene else 24.0)
                    _scene_sf = bpy.context.scene
                    _sf_frames = ((_scene_sf.frame_preview_start
                                   if _scene_sf and _scene_sf.use_preview_range
                                   else (_scene_sf.frame_start if _scene_sf else 1)))
                    _fft_timeline[channel_idx] = {
                        'snapshots'  : fft_array,
                        'snap_frames': snap_frames,
                        'start_frame': float(_sf_frames),
                        'sr'         : sr,
                        'fps'        : scene_fps,
                    }
                    print(f"[ENGINE] ch{channel_idx+1} FFT timeline: "
                          f"{len(fft_snaps)} snapshots")

                    # Build a separate FFT timeline for the EQ input signal.
                    # _fft_timeline_eq_input[ch] is set by _apply_effect_chain
                    # to the pre-EQ audio (post any upstream compressors).
                    # If no EQ rack is assigned, it won't be set and the EQ
                    # display just uses the main _fft_timeline (same result).
                    eq_input_sig = _fft_timeline_eq_input.get(channel_idx)
                    if eq_input_sig is not None:
                        try:
                            eq_mono = (eq_input_sig[:, 0]
                                       if eq_input_sig.ndim == 2
                                       else eq_input_sig.flatten())
                            eq_snaps = []
                            for snap_i in range(n_snaps):
                                start_eq = snap_i * snap_frames
                                chunk_eq = eq_mono[start_eq:start_eq + snap_frames]
                                if len(chunk_eq) < 128:
                                    break
                                win_eq = np.hanning(len(chunk_eq)).astype(np.float32)
                                fft_eq = np.abs(np.fft.rfft(chunk_eq * win_eq))
                                fft_eq = fft_eq / (len(chunk_eq) * 0.5 + 1e-9)
                                freq_res_eq = sr / len(chunk_eq)
                                n_bins_eq   = len(fft_eq)
                                band_list_eq = []
                                for b in range(4):
                                    f_lo = CROSSOVERS[b]
                                    f_hi = CROSSOVERS[b + 1]
                                    freqs_eq = np.logspace(
                                        np.log10(max(f_lo, 1)),
                                        np.log10(f_hi), BINS_PER_BAND)
                                    idxs_eq = np.clip(
                                        (freqs_eq / freq_res_eq).astype(int),
                                        0, n_bins_eq - 1)
                                    band_vals_eq = fft_eq[idxs_eq]
                                    band_db_eq   = np.clip(
                                        (20*np.log10(band_vals_eq+1e-9)+80)/80,
                                        0.0, 1.0).astype(np.float32)
                                    band_list_eq.append(band_db_eq)
                                eq_snaps.append(np.stack(band_list_eq))
                            if eq_snaps:
                                _fft_timeline[channel_idx] = {
                                    'snapshots'  : np.stack(eq_snaps),
                                    'snap_frames': snap_frames,
                                    'start_frame': position_seconds * scene_fps,
                                    'sr'         : sr,
                                    'fps'        : scene_fps,
                                }
                        except Exception as _eqfe:
                            pass   # non-fatal — falls back to main timeline
                except Exception as fe:
                    print(f"[ENGINE] FFT timeline failed: {fe}")

                # Build GR timeline by calling process_buffer on each 80ms chunk
                # and reading the exact GR values from the C++ engine.
                # raw_for_gr = the signal as it arrived at the compressor,
                # which may differ from samples if an EQ rack is placed before it.
                try:
                    snap_frames_gr = int(sr * 0.08)  # 80ms snapshots
                    inp_gr = np.asarray(raw_for_gr, dtype=np.float32)
                    n_snaps_gr = max(1, inp_gr.shape[0] // snap_frames_gr)
                    gr_snaps = []

                    for snap_i_gr in range(n_snaps_gr):
                        start_gr = snap_i_gr * snap_frames_gr
                        chunk_gr = inp_gr[start_gr:start_gr + snap_frames_gr]
                        if chunk_gr.shape[0] < 64:
                            break
                        if chunk_gr.ndim == 1:
                            chunk_gr = chunk_gr.reshape(-1, 1)
                        chunk_gr = np.ascontiguousarray(chunk_gr, dtype=np.float32)

                        # Run C++ compressor on this chunk — gr_levels gets updated
                        try:
                            engine.process_buffer(channel_idx, chunk_gr, sr)
                            gr_vals = engine.get_state().get_gr_levels(channel_idx)
                            # gr_vals are already in dB (positive = gain reduction)
                            # e.g. 3.5 means 3.5dB of compression applied
                            gr_snap = np.array([
                                min(24.0, max(0.0, float(gr_vals[b])))
                                for b in range(4)
                            ], dtype=np.float32)
                        except Exception:
                            gr_snap = np.zeros(4, dtype=np.float32)

                        gr_snaps.append(gr_snap)

                    if gr_snaps:
                        gr_array = np.stack(gr_snaps)  # (n_snaps, 4)
                        _gr_timeline[channel_idx] = {
                            'snapshots'  : gr_array,
                            'snap_frames': snap_frames_gr,
                            'start_frame': position_seconds * scene_fps,
                            'sr'         : sr,
                            'fps'        : scene_fps,
                        }
                        peak_gr = gr_array.max(axis=0)
                        print(f"[ENGINE] ch{channel_idx+1} GR timeline: "
                              f"{len(gr_snaps)} snapshots | "
                              f"peak GR: "
                              f"Low={peak_gr[0]:.1f}dB "
                              f"LMid={peak_gr[1]:.1f}dB "
                              f"HMid={peak_gr[2]:.1f}dB "
                              f"High={peak_gr[3]:.1f}dB")
                except Exception as gre:
                    print(f"[ENGINE] GR timeline failed: {gre}")

                # Write processed wav using wave module
                # (aud.Sound.buffer() not available in Blender 4.5)
                tmp_path = os.path.join(tempfile.gettempdir(),
                                        f"pb_proc_ch{channel_idx}.wav")

                proc_np = np.asarray(processed, dtype=np.float32)
                int16   = np.clip(proc_np, -1.0, 1.0)
                int16   = (int16 * 32767).astype(np.int16)

                with wave.open(tmp_path, 'wb') as wf:
                    wf.setnchannels(nch)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(int16.tobytes())

                if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                    pb_sound = _aud.Sound.file(tmp_path)
                    _pb_proc_wav_cache[channel_idx] = tmp_path  # cache for loop restart

                    # Build full-track wav for clean loop replay.
                    # Only needed when play started mid-sequence.
                    try:
                        scene_ft = bpy.context.scene
                        fps_ft   = scene_fps
                        start_s  = ((scene_ft.frame_preview_start
                                     if scene_ft.use_preview_range
                                     else scene_ft.frame_start)
                                    / fps_ft) if scene_ft else 0.0
                        if position_seconds > start_s + 0.1:
                            # Mid-track start — build full-track version
                            snd_ft, _ = _pb_build_channel_sound(
                                channel_idx, start_s)
                            if snd_ft is not None:
                                samp_ft = snd_ft.data()
                                if samp_ft is not None and samp_ft.size > 0:
                                    if samp_ft.ndim == 1:
                                        samp_ft = samp_ft.reshape(-1, 1)
                                    samp_ft = np.ascontiguousarray(
                                        samp_ft, dtype=np.float32)
                                    proc_ft, _ = _apply_effect_chain(
                                        samp_ft, channel_idx, sr)
                                    ft_path = os.path.join(
                                        tempfile.gettempdir(),
                                        f"pb_full_ch{channel_idx}.wav")
                                    ft_np  = np.asarray(proc_ft, dtype=np.float32)
                                    ft_i16 = (np.clip(ft_np,-1.0,1.0)*32767
                                              ).astype(np.int16)
                                    with wave.open(ft_path, 'wb') as wff:
                                        wff.setnchannels(nch)
                                        wff.setsampwidth(2)
                                        wff.setframerate(sr)
                                        wff.writeframes(ft_i16.tobytes())
                                    if os.path.getsize(ft_path) > 0:
                                        _pb_full_wav_cache[channel_idx] = ft_path
                                        print(f"[ENGINE] ch{channel_idx+1} "
                                              f"full-track cached "
                                              f"({os.path.getsize(ft_path)//1024}KB)")
                                        # Build full-track timelines from this audio
                                        try:
                                            _ft_np = np.asarray(proc_ft,
                                                                 dtype=np.float32)
                                            # samp_ft is the raw input for full-track
                                            _raw_np = np.asarray(samp_ft,
                                                                  dtype=np.float32)
                                            _build_timelines(
                                                channel_idx, _ft_np, sr,
                                                scene_fps, start_s,
                                                full_track=True,
                                                raw_np=_raw_np)
                                            # DEBUG: confirm full-track timeline contents
                                            _ft_check = _fft_timeline_full.get(channel_idx)
                                            _gt_check = _gate_timeline_full.get(channel_idx)
                                            print(f"[FULL_TL] ch{channel_idx+1} "
                                                  f"fft_snaps={len(_ft_check['snapshots']) if _ft_check else 0} "
                                                  f"gate_snaps={len(_gt_check['snapshots']) if _gt_check else 0} "
                                                  f"start_frame={_ft_check['start_frame'] if _ft_check else 'None'} "
                                                  f"snap_frames={_ft_check['snap_frames'] if _ft_check else 'None'} "
                                                  f"sr={_ft_check['sr'] if _ft_check else 'None'} "
                                                  f"fps={_ft_check['fps'] if _ft_check else 'None'}")
                                        except Exception:
                                            _fft_timeline_full[channel_idx] = (
                                                _fft_timeline.get(channel_idx))
                                            _gr_timeline_full[channel_idx]  = (
                                                _gr_timeline.get(channel_idx))
                        else:
                            # Started from beginning — trimmed IS the full track
                            _pb_full_wav_cache[channel_idx] = tmp_path
                            print(f"[ENGINE] ch{channel_idx+1} "
                                  f"started at beginning — trimmed = full-track")
                            _fft_timeline_full[channel_idx] = (
                                _fft_timeline.get(channel_idx))
                            _gr_timeline_full[channel_idx]  = (
                                _gr_timeline.get(channel_idx))
                    except Exception as _fte:
                        # Non-fatal — fall back to trimmed wav on loop
                        _pb_full_wav_cache[channel_idx] = tmp_path
                        print(f"[ENGINE] ch{channel_idx+1} full-track build failed "
                              f"({_fte}) — loop will use trimmed")
                    # Verify compression actually changed the audio
                    proc_np  = np.asarray(processed, dtype=np.float32)
                    in_rms   = float(np.sqrt(np.mean(samples**2)))
                    out_rms  = float(np.sqrt(np.mean(proc_np**2)))
                    ratio_db = 20*np.log10(out_rms/(in_rms+1e-9))
                    print(f"[ENGINE] ch{channel_idx+1} DSP done — "
                          f"wav={os.path.getsize(tmp_path)//1024}KB | "
                          f"in_rms={in_rms:.4f} out_rms={out_rms:.4f} "
                          f"level_change={ratio_db:+.1f}dB — "
                          f"{'COMPRESSION APPLIED' if abs(ratio_db) > 0.1 else 'NO CHANGE DETECTED'}")


                else:
                    raise RuntimeError("processed wav write failed")

            except Exception as e:
                print(f"[ENGINE] ch{channel_idx+1} DSP failed ({e}) — "
                      f"playing unprocessed")
                import traceback; traceback.print_exc()
        else:
            # No rack assigned — but pan still applies.
            # If pan is not centre, extract samples, apply pan, write wav.
            print(f"[ENGINE] ch{channel_idx+1} no rack assigned — "
                  f"playing unprocessed")
            try:
                import math as _mp
                scene_p = bpy.context.scene
                tracks_p = getattr(scene_p, 'pb_sync_tracks', [])
                pan = getattr(tracks_p[channel_idx], 'pan', 0.5) \
                      if channel_idx < len(tracks_p) else 0.5
                if abs(pan - 0.5) > 0.01:
                    import tempfile, os, aud as _aud, numpy as _npan, wave
                    try:
                        sr_p  = int(sound.specs[0])
                        nch_p = int(sound.specs[1])
                    except Exception:
                        sr_p, nch_p = 44100, 2
                    samp_p = sound.data()
                    if samp_p is not None and samp_p.size > 0 and nch_p >= 2:
                        angle   = pan * (_mp.pi / 2.0)
                        gain_l  = _mp.cos(angle)
                        gain_r  = _mp.sin(angle)
                        samp_p  = samp_p.astype(_npan.float32)
                        samp_p[:, 0] *= gain_l
                        samp_p[:, 1] *= gain_r
                        pan_path = os.path.join(tempfile.gettempdir(),
                                                f"pb_pan_ch{channel_idx}.wav")
                        i16 = (_npan.clip(samp_p, -1.0, 1.0) * 32767).astype(_npan.int16)
                        with wave.open(pan_path, 'wb') as wf:
                            wf.setnchannels(nch_p)
                            wf.setsampwidth(2)
                            wf.setframerate(sr_p)
                            wf.writeframes(i16.tobytes())
                        pb_sound = _aud.Sound.file(pan_path)
                        print(f"[PAN] ch{channel_idx+1} no-rack pan={pan:.2f} "
                              f"L={gain_l:.2f} R={gain_r:.2f}")
            except Exception as _pe:
                pass  # pan is non-critical — fall back to raw sound

    # Stop old handle now — new sound plays immediately after (minimal gap)
    if existing:
        try: existing['handle'].stop()
        except Exception: pass

    device = _pb_get_device()
    handle = device.play(pb_sound)
    handle.volume = _pb_channel_volume(channel_idx)
    # Position 0 because we already trimmed sound to start at position_seconds
    handle.position = 0.0

    _pb_channels[channel_idx] = {
        'handle'      : handle,
        'start_wall'  : _time.time(),
        'start_pos'   : position_seconds,
        'channel_idx' : channel_idx,
        'proc_wav'    : _pb_proc_wav_cache.get(channel_idx),
    }
    print(f"[ENGINE] ch{channel_idx+1} started at {round(position_seconds,2)}s "
          f"vol={round(handle.volume,3)}")


def _pb_start_all(scene):
    """Start all channels from the current timeline position."""
    _pb_start_all_from_frame(scene, int(scene.frame_current))


def _pb_start_all_from_frame(scene, frame):
    """Start all channels from an explicit frame number.
    Clamps frame to the valid sequence range before doing anything —
    prevents crashes and silence when cursor is outside the sequence.
    """
    global _pb_start_wall, _pb_start_frame, _pb_last_frame

    # Clamp to sequence bounds — handles cursor past end or before start
    effective_start = int(scene.frame_preview_start if scene.use_preview_range
                          else scene.frame_start)
    effective_end   = int(scene.frame_preview_end   if scene.use_preview_range
                          else scene.frame_end)
    frame = int(frame)

    # If cursor is outside the sequence, snap to start
    if frame < effective_start or frame >= effective_end:
        frame = effective_start
        print(f"[ENGINE] cursor outside sequence, snapping to frame {frame}")
    else:
        frame = max(effective_start, min(effective_end, frame))

    _pb_last_frame     = frame
    _pb_last_loop_time = 0.0     # reset cooldown on fresh start
    fps             = scene.render.fps / scene.render.fps_base
    _pb_start_frame = frame
    _pb_start_wall  = _time.time()

    channels = set()
    for s in scene.sequence_editor.sequences_all:
        if s.type == "SOUND" and s.sound:
            channels.add(s.channel - 1)

    for idx in channels:
        pos_seconds = frame / fps
        _pb_start_channel(idx, pos_seconds)

    print(f"[ENGINE] all channels started from frame {frame}")


def _pb_stop_all(scene):
    """Stop all channel handles.
    On genuine user stop: leave cursor where Blender put it (scene.frame_current
    is already correct — Blender stops the timeline at the right frame).
    On loop transition: do nothing, let Blender restart from its loop point.
    """
    global _pb_channels, _pb_eq_pending

    if not _pb_channels: return  # guard against re-entry

    # Stop all handles
    channels_snapshot = dict(_pb_channels)
    _pb_channels.clear()
    _pb_eq_pending.clear()
    for ch in channels_snapshot.values():
        try: ch['handle'].stop()
        except Exception: pass

    # Restore strip mute states respecting both mute AND solo
    try:
        scene = bpy.context.scene
        if scene and scene.sequence_editor:
            tracks = getattr(scene, "pb_sync_tracks", [])
            soloed = {i for i, t in enumerate(tracks) if t.solo}
            any_solo = len(soloed) > 0
            for strip in scene.sequence_editor.sequences_all:
                if strip.type == "SOUND":
                    idx = strip.channel - 1
                    if any_solo:
                        strip.mute = (idx not in soloed)
                    else:
                        strip.mute = tracks[idx].mute if idx < len(tracks) else False
    except Exception: pass

    # Record when stop fired — if play fires within 200ms it's a loop restart,
    # not a genuine user stop. This is reliable regardless of frame position.
    global _pb_is_loop_restart, _pb_stop_time
    _pb_stop_time       = _time.time()
    _pb_is_loop_restart = False   # will be set by play handler if needed
    print(f"[ENGINE] stopped at frame {scene.frame_current}")


def _pb_engine_update_volume(channel_idx):
    """Update handle volume immediately — called when fader/gain/mute changes."""
    ch = _pb_channels.get(channel_idx)
    if ch:
        try: ch['handle'].volume = _pb_channel_volume(channel_idx)
        except Exception: pass


def _pb_rebuild_eq(channel_idx):
    """
    Schedule an EQ rebuild for channel_idx.
    Uses debouncing — only actually rebuilds after the knob has been
    still for _pb_eq_debounce seconds. This prevents rapid clicking
    when dragging an EQ knob continuously.
    """
    global _pb_eq_pending
    # Record when this channel last requested a rebuild
    _pb_eq_pending[channel_idx] = _time.time()


def _pb_do_eq_rebuild(channel_idx):
    """
    Actually perform the EQ rebuild — called by timer after debounce.
    Works both during playback and when stopped.
    When playing: restarts the channel from current position with new EQ.
    When stopped: rebuilds the channel sound in-place so next play uses new EQ.
    """
    if channel_idx not in _pb_channels: return
    scene = bpy.context.scene
    if not scene: return
    fps = scene.render.fps / scene.render.fps_base
    seq_start_s = ((scene.frame_preview_start if scene.use_preview_range
                    else scene.frame_start) / fps)
    seq_end_s   = ((scene.frame_preview_end   if scene.use_preview_range
                    else scene.frame_end)   / fps)
    current_pos = max(seq_start_s,
                      min(seq_end_s - 0.1, scene.frame_current / fps))
    print(f"[ENGINE] rebuilding EQ ch{channel_idx+1} at {round(current_pos,3)}s")

    is_playing = bool(bpy.context.screen and
                      bpy.context.screen.is_animation_playing)
    if is_playing:
        # Full restart from current position
        _pb_start_channel(channel_idx, current_pos)
    else:
        # Stopped: rebuild the processed WAV cache so next play uses new EQ.
        # _pb_build_channel_sound rebuilds + caches without starting playback.
        try:
            _pb_build_channel_sound(channel_idx, current_pos)
            print(f"[ENGINE] EQ ch{channel_idx+1} cache rebuilt (stopped)")
        except Exception as e:
            print(f"[ENGINE] EQ rebuild (stopped) error: {e}")


# ---------------------------------------------------------------------------
# Transport handlers — animation_playback_pre / animation_playback_post
#
# These fire EXACTLY ONCE per genuine play/stop event.
# They do NOT fire on cursor moves, scrubbing, or frame changes.
# This is the correct Blender-native hook for audio transport.
#
# When the user moves the cursor during playback, Blender fires:
#   animation_playback_post (stop) → animation_playback_pre (play)
# automatically, so we get a free restart from the correct position.
# ---------------------------------------------------------------------------

@bpy.app.handlers.persistent
def _pb_on_play_start(scene, depsgraph=None):
    """Fired by Blender exactly once when animation playback begins.
    On a loop restart, use scene.frame_start directly — Blender hasn't
    updated frame_current yet when this handler fires after a loop.
    """
    global _pb_is_loop_restart
    if not _pb_engine_active:
        return
    try:
        # If play fires within 200ms of stop, it's a loop restart.
        # Use frame_start directly — frame_current is unreliable at this moment.
        time_since_stop = _time.time() - _pb_stop_time
        is_loop = (time_since_stop < 0.20)

        if is_loop:
            effective_start = int(scene.frame_preview_start
                                  if scene.use_preview_range
                                  else scene.frame_start)
            print(f"[ENGINE] loop restart from frame {effective_start} "
                  f"({time_since_stop*1000:.0f}ms after stop)")
            _pb_start_all_from_frame(scene, effective_start)
        else:
            print(f"[ENGINE] play start at frame {scene.frame_current}")
            _pb_start_all(scene)
    except Exception as e:
        print(f"[ENGINE] play start error: {e}")


@bpy.app.handlers.persistent
def _pb_on_play_stop(scene, depsgraph=None):
    """Fired by Blender exactly once when animation playback stops."""
    if not _pb_engine_active:
        return
    try:
        print(f"[ENGINE] play stop at frame {scene.frame_current}")
        _pb_stop_all(scene)
    except Exception as e:
        print(f"[ENGINE] play stop error: {e}")


@bpy.app.handlers.persistent
def _pb_loop_detect(scene, depsgraph=None):
    """Watches frame_change_post for the loop jump that Blender does silently.
    Blender does NOT fire animation_playback_post/pre at loop points —
    the frame simply jumps backwards. We detect this and restart audio.
    1 second cooldown prevents multiple restarts from duplicate frame events.
    """
    global _pb_last_frame, _pb_last_loop_time
    if not _pb_engine_active or not _pb_channels:
        _pb_last_frame = scene.frame_current
        return

    current = scene.frame_current

    # Cooldown: ignore loop detection for 1 second after last restart
    if _time.time() - _pb_last_loop_time < 1.0:
        _pb_last_frame = current
        return

    # A loop jump is a large backward jump during playback.
    # Threshold of 10 frames avoids false positives from scrubbing.
    if _pb_last_frame > 0 and (current < _pb_last_frame - 10):
        effective_start = int(scene.frame_preview_start
                              if scene.use_preview_range
                              else scene.frame_start)
        print(f"[ENGINE] loop jump detected: {_pb_last_frame}→{current}, "
              f"restarting from frame {effective_start}")
        _pb_last_loop_time = _time.time()

        # CRITICAL: reset position tracking globals BEFORE replaying cache
        # so that _pb_reprocess_channel uses correct position after loop
        fps_loop        = scene.render.fps / scene.render.fps_base
        _pb_start_frame = effective_start
        _pb_start_wall  = _time.time()

        # Reset timeline start_frame to loop start.
        # Swap to full-track timelines where available so bars cover full sequence.
        _loop_start_s = effective_start / fps_loop
        for _ch_k in set(list(_fft_timeline.keys()) +
                          list(_fft_timeline_full.keys())):
            _ft = _fft_timeline_full.get(_ch_k) or _fft_timeline.get(_ch_k)
            if _ft:
                _ft['start_frame'] = _loop_start_s * _ft['fps']
                _fft_timeline[_ch_k] = _ft
        for _ch_k in set(list(_gr_timeline.keys()) +
                          list(_gr_timeline_full.keys())):
            _gt = _gr_timeline_full.get(_ch_k) or _gr_timeline.get(_ch_k)
            if _gt:
                _gt['start_frame'] = _loop_start_s * _gt['fps']
                _gr_timeline[_ch_k] = _gt

        # Stop existing handles
        channels_snapshot = dict(_pb_channels)
        _pb_channels.clear()
        for ch_data in channels_snapshot.values():
            try: ch_data['handle'].stop()
            except Exception: pass

        # Replay full-track cached wav (from frame_start) for clean loop.
        # Falls back to trimmed cache if full-track not available.
        device = _pb_get_device()
        all_ch = set(list(_pb_full_wav_cache.keys()) +
                     list(_pb_proc_wav_cache.keys()))
        for ch_idx in all_ch:
            full_wav    = _pb_full_wav_cache.get(ch_idx)
            trimmed_wav = _pb_proc_wav_cache.get(ch_idx)
            replay_wav  = (full_wav    if full_wav    and os.path.exists(full_wav)
                           else trimmed_wav if trimmed_wav and os.path.exists(trimmed_wav)
                           else None)
            try:
                if replay_wav:
                    import aud as _aud_loop
                    snd = _aud_loop.Sound.file(replay_wav)
                    h   = device.play(snd)
                    h.volume = _pb_channel_volume(ch_idx)
                    h.position = 0.0
                    _pb_channels[ch_idx] = {
                        'handle'     : h,
                        'start_wall' : _pb_start_wall,
                        'start_pos'  : effective_start / fps_loop,
                        'channel_idx': ch_idx,
                        'proc_wav'   : replay_wav,
                    }
                    src = ("full-track" if replay_wav == full_wav
                           else "trimmed fallback")
                    wav_kb = os.path.getsize(replay_wav) // 1024
                    print(f"[ENGINE] ch{ch_idx+1} loop: replay from {src} cache "
                          f"({wav_kb}KB) from frame {effective_start}")
                else:
                    print(f"[ENGINE] ch{ch_idx+1} loop: no cache available — "
                          f"will restart on next play")
            except Exception as _le:
                print(f"[ENGINE] ch{ch_idx+1} loop cache replay failed: {_le}")

        # Restart any channels that had no cached wav (EQ-only, no rack assigned).
        # These play directly from aud.Sound so we just re-call _pb_start_channel.
        try:
            from Racks import get_rack_channels as _grc_loop
            scene_loop = bpy.context.scene
            racks_loop = getattr(scene_loop, "pb_racks", []) if scene_loop else []
            cached_channels = set(list(_pb_full_wav_cache.keys()) +
                                  list(_pb_proc_wav_cache.keys()))
            # Find channels that were playing but have no rack and no cache
            for _ch_idx in list(channels_snapshot.keys()):
                if _ch_idx in cached_channels:
                    continue  # already handled above
                has_rack_loop = any(
                    r.enabled and _ch_idx in _grc_loop(r)
                    for r in racks_loop
                )
                if not has_rack_loop:
                    loop_pos_s = effective_start / fps_loop
                    print(f"[ENGINE] ch{_ch_idx+1} loop: no-rack restart "
                          f"from {loop_pos_s:.2f}s")
                    _pb_start_channel(_ch_idx, loop_pos_s)
        except Exception as _nrl:
            print(f"[ENGINE] loop no-rack restart failed: {_nrl}")

    _pb_last_frame = current


# ---------------------------------------------------------------------------
# EQ debounce timer — processes pending EQ rebuilds
# Runs independently of transport so EQ updates work while paused too
# ---------------------------------------------------------------------------

def _pb_eq_timer():
    """Check for pending EQ rebuilds and execute them after debounce."""
    if not _pb_engine_active:
        return 0.1
    try:
        now     = _time.time()
        pending = {ch: t for ch, t in list(_pb_eq_pending.items())
                   if now - t >= _pb_eq_debounce}
        for ch in pending:
            del _pb_eq_pending[ch]
            _pb_do_eq_rebuild(ch)
    except Exception as e:
        print(f"[ENGINE] EQ timer error: {e}")
    return 0.1   # poll every 100ms


_pb_eq_timer_registered = False


# ---------------------------------------------------------------------------
# Engine enable / disable
# ---------------------------------------------------------------------------

def _pb_engine_enable():
    """Disable Blender's audio, register our playback handlers."""
    global _pb_engine_active, _pb_original_device, _pb_eq_timer_registered

    if _pb_engine_active: return

    # Save and disable Blender's audio device
    try:
        current_device = bpy.context.preferences.system.audio_device
        # Never save 'None' as the device to restore to — if audio was already
        # disabled, restore to WASAPI (Windows) or OpenAL as a safe default.
        if current_device and current_device != 'None':
            _pb_original_device = current_device
        else:
            # Detect platform default
            import sys
            _pb_original_device = 'WASAPI' if sys.platform == 'win32' else 'OpenAL'
        bpy.context.preferences.system.audio_device = 'None'
        print(f"[ENGINE] Blender audio disabled (was '{current_device}', "
              f"will restore to '{_pb_original_device}')")
    except Exception as e:
        print(f"[ENGINE] could not disable Blender audio: {e}")

    # Register transport handlers
    if _pb_on_play_start not in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.append(_pb_on_play_start)
    if _pb_on_play_stop not in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.append(_pb_on_play_stop)
    # Register loop detection handler
    if _pb_loop_detect not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_pb_loop_detect)

    # Register EQ debounce timer
    if not _pb_eq_timer_registered:
        bpy.app.timers.register(_pb_eq_timer, first_interval=0.1)
        _pb_eq_timer_registered = True

    _pb_engine_active = True
    print("[ENGINE] Pedalboard audio engine active")


def _pb_engine_disable():
    """Stop all audio, restore Blender's audio device."""
    global _pb_engine_active, _pb_channels, _pb_eq_timer_registered

    if not _pb_engine_active: return

    # Stop all handles
    channels_snapshot = dict(_pb_channels)
    _pb_channels.clear()
    _pb_eq_pending.clear()
    for ch in channels_snapshot.values():
        try: ch['handle'].stop()
        except Exception: pass

    # Restore strip mute states to user's channel mute setting
    # (on disable we restore fully — solo state is cleared)
    try:
        scene = bpy.context.scene
        if scene and scene.sequence_editor:
            tracks = getattr(scene, "pb_sync_tracks", [])
            for strip in scene.sequence_editor.sequences_all:
                if strip.type == "SOUND":
                    idx = strip.channel - 1
                    strip.mute = tracks[idx].mute if idx < len(tracks) else False
    except Exception: pass

    # Remove transport handlers
    if _pb_on_play_start in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.remove(_pb_on_play_start)
    if _pb_on_play_stop in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.remove(_pb_on_play_stop)
    if _pb_loop_detect in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_pb_loop_detect)

    # Cancel EQ timer
    if _pb_eq_timer_registered:
        try: bpy.app.timers.unregister(_pb_eq_timer)
        except Exception: pass
        _pb_eq_timer_registered = False

    # Restore Blender's audio device
    try:
        bpy.context.preferences.system.audio_device = _pb_original_device
        print(f"[ENGINE] Blender audio restored to '{_pb_original_device}'")
    except Exception as e:
        print(f"[ENGINE] could not restore Blender audio: {e}")

    _pb_engine_active = False
    print("[ENGINE] Pedalboard audio engine stopped")


# ---------------------------------------------------------------------------
# Mute / Solo — write to strip.mute for correct VSE appearance
# AND update handle volumes so our engine reflects the change instantly
# ---------------------------------------------------------------------------

def sync_vse_mute(channel_idx, state):
    """Mute: update strip.mute (VSE appearance) and handle volume."""
    scene = bpy.context.scene
    if not scene or not scene.sequence_editor: return
    for strip in scene.sequence_editor.sequences_all:
        if strip.type == "SOUND" and (strip.channel - 1) == channel_idx:
            strip.mute = state
    _pb_engine_update_volume(channel_idx)


def sync_vse_solo(channel_idx, solo_state):
    """Solo: update strip.mute on all channels, update all handle volumes."""
    scene = bpy.context.scene
    if not scene or not scene.sequence_editor: return
    tracks   = getattr(scene, "pb_sync_tracks", [])
    soloed   = {i for i, t in enumerate(tracks) if t.solo}
    any_solo = len(soloed) > 0
    for strip in scene.sequence_editor.sequences_all:
        if strip.type != "SOUND": continue
        idx        = strip.channel - 1
        strip.mute = (idx not in soloed) if any_solo else (
            tracks[idx].mute if idx < len(tracks) else False)
    # Update all handles
    for idx in list(_pb_channels.keys()):
        _pb_engine_update_volume(idx)


# ---------------------------------------------------------------------------
# Envelope pre-build for all strips (called on refresh / HUD enable)
# ---------------------------------------------------------------------------

def prebuild_envelopes():
    scene = bpy.context.scene
    if not scene or not scene.sequence_editor: return
    fps  = scene.render.fps / scene.render.fps_base
    seen = set()
    for strip in scene.sequence_editor.sequences_all:
        if strip.type != "SOUND" or not strip.sound: continue
        filepath = bpy.path.abspath(strip.sound.filepath)
        from core.meters import _envelope_cache, get_envelope as _get_env
        if filepath not in seen and filepath not in _envelope_cache:
            _get_env(filepath, fps)
            seen.add(filepath)


# ---------------------------------------------------------------------------
# Peak envelope builder
# ---------------------------------------------------------------------------