# =============================================================================
# core/audio.py
# All audio processing: fader logic, effect chain, channel sound building,
# playback handlers, timeline building, and engine enable/disable.
# =============================================================================

import os
import sys
import math
import tempfile
import time as _time

import bpy

from core.engine import get_engine


# ---------------------------------------------------------------------------
# These timelines are read by Racks.py for waveform display.
# They are module-level so Racks can import them directly.
# ---------------------------------------------------------------------------
_fft_timeline          = {}
_fft_timeline_full     = {}
_gr_timeline           = {}
_gr_timeline_full      = {}
_gate_timeline         = {}
_gate_timeline_full    = {}
_fft_timeline_eq_input = {}

# ---------------------------------------------------------------------------
# Audio engine state
# ---------------------------------------------------------------------------
_pb_original_device = 'OpenAL'  # restored on HUD close
_pb_channels      = {}
_pb_start_wall    = 0.0
_pb_start_frame   = 0
_pb_engine_active   = False
_pb_eq_pending      = {}        # channel_idx -> scheduled rebuild time
_pb_eq_debounce     = 0.25      # seconds to wait after last knob move before rebuilding
_pb_last_frame      = -1        # last known frame, for loop jump detection
_pb_last_loop_time  = 0.0       # wall time of last loop restart (cooldown)
_pb_proc_wav_cache  = {}        # channel_idx -> last processed wav path (for instant loop restart)
_pb_full_wav_cache  = {}        # channel_idx -> full-track wav from frame_start (clean loop replay)


def apply_fader_to_channel(channel_idx, old_fader, new_fader):
    """Apply fader change — multiplies strip.volume by new/old ratio.
    Both fader and gain have a minimum of 0.001 so the ratio never
    reaches zero and the original strip volume is always recoverable."""
    scene = bpy.context.scene
    if not scene or not scene.sequence_editor: return
    if abs(new_fader - old_fader) < 1e-6: return
    old_fader = max(old_fader, 0.001)
    ratio = new_fader / old_fader
    for strip in scene.sequence_editor.sequences_all:
        if strip.type != "SOUND": continue
        if (strip.channel - 1) != channel_idx: continue
        strip.volume = max(0.001, strip.volume * ratio)
    _pb_engine_update_volume(channel_idx)


def apply_gain_to_channel(channel_idx, old_gain, new_gain):
    """Apply gain change — multiplies strip.volume by new/old ratio."""
    scene = bpy.context.scene
    if not scene or not scene.sequence_editor: return
    if abs(new_gain - old_gain) < 1e-6: return
    old_gain = max(old_gain, 0.001)
    ratio = new_gain / old_gain
    for strip in scene.sequence_editor.sequences_all:
        if strip.type != "SOUND": continue
        if (strip.channel - 1) != channel_idx: continue
        strip.volume = max(0.001, strip.volume * ratio)
    _pb_engine_update_volume(channel_idx)


# ---------------------------------------------------------------------------
# One-shot preview playback — plays a WAV file without touching aud.Device()
# or the Hijacker engine.  Uses platform-native playback in a subprocess so
# there is zero risk of conflicting with WASAPI.
# Returns a handle object with a .stop() method and .status property so
# callers (rack previews, watchdog timers) work the same as before.
# ---------------------------------------------------------------------------

class _OneshotHandle:
    """Thin wrapper around a subprocess so callers get stop()/status."""
    def __init__(self, proc):
        self._proc = proc

    @property
    def status(self):
        """True while audio is still playing."""
        return self._proc is not None and self._proc.poll() is None

    def stop(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass


_oneshot_handles = []  # keep references alive


def play_oneshot(filepath):
    """Play a WAV file without touching aud.Device or the Hijacker engine.
    Returns an _OneshotHandle with stop() and .status.
    Safe to call at any time — no WASAPI conflict possible.
    """
    global _oneshot_handles
    # Trim dead handles
    _oneshot_handles = [h for h in _oneshot_handles if h.status]
    try:
        import sys, subprocess
        if sys.platform == 'win32':
            # PowerShell Media.SoundPlayer — plays WAV, exits when done
            cmd = [
                'powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command',
                f'(New-Object Media.SoundPlayer "{filepath}").PlaySync()'
            ]
        elif sys.platform == 'darwin':
            cmd = ['afplay', filepath]
        else:
            cmd = ['aplay', filepath]
        proc   = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        handle = _OneshotHandle(proc)
        _oneshot_handles.append(handle)
        print(f"[AUDIO] play_oneshot: {filepath}")
        return handle
    except Exception as e:
        print(f"[AUDIO] play_oneshot error: {e}")
        return _OneshotHandle(None)


def stop_all_oneshots():
    """Stop any currently playing one-shot previews."""
    global _oneshot_handles
    for h in _oneshot_handles:
        h.stop()
    _oneshot_handles = []


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


def _pb_channel_volume(channel_idx):
    """Return the channel fader volume for hj.set_volume().

    strip.volume = original_strip_vol × fader × gain (all baked together).
    seg.volume already carries the per-strip original volume (strip.volume / fader).
    So hj.set_volume() only needs the fader — otherwise it would be double-applied.

    Mute/solo short-circuit to 0.0 as before.
    """
    scene  = bpy.context.scene
    tracks = getattr(scene, "pb_sync_tracks", []) if scene else []

    if channel_idx < len(tracks) and tracks[channel_idx].mute:
        return 0.0

    soloed = {i for i, t in enumerate(tracks) if t.solo}
    if soloed and channel_idx not in soloed:
        return 0.0

    if channel_idx < len(tracks):
        return tracks[channel_idx].volume
    return 1.0


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

        elif etype == "BOOSTER":
            # BOOSTER is an offline export/reimport processor — no real-time DSP slot.
            # Acknowledge it so the wire loop doesn't print "no rack assigned".
            print(f"[WIRE] ch{channel_idx+1} BOOSTER — offline only, no DSP slot")

        if slot_idx >= 8:
            break

    if slot_idx > 0:
        print(f"[WIRE] ch{channel_idx+1} wired {slot_idx} effects — DSP ACTIVE")
    else:
        print(f"[WIRE] ch{channel_idx+1} no rack assigned")







# =============================================================================
# TRANSPORT — The Hijacker engine
# =============================================================================
# All playback is now handled by hijacker_engine (C++ PortAudio).
# Python's role:
#   1. At play-start: build segment playlists from VSE strips, send to engine
#   2. Wire rack DSP params into engine effect slots
#   3. Handle seek by calling engine.seek()
#   4. Handle stop by calling engine.stop()
#   5. Mute/solo/volume: call engine.set_volume/mute/solo() — instant, no restart
#   6. EQ/rack changes: call engine.set_effect_slot() — heard next buffer (~5ms)
# =============================================================================


def _hj_build_segment_playlist(channel_idx, scene):
    """
    Build a list of HijackerSegment objects for one VSE channel.
    Pre-decodes any non-WAV strips to temp WAV so the engine
    only ever sees raw PCM files.
    Returns list of segment dicts ready to convert to engine.Segment objects.
    """
    import aud, os, tempfile, wave as _wave

    if not scene or not scene.sequence_editor:
        return []

    fps       = scene.render.fps / scene.render.fps_base
    seq_start = scene.frame_start / fps
    # Add one full frame to seq_end so the last frame plays completely.
    # scene.frame_end is the last frame NUMBER — audio clamped to exactly
    # frame_end/fps cuts off the final frame before it finishes playing.
    seq_end   = (scene.frame_end + 1) / fps

    strips = sorted(
        [s for s in scene.sequence_editor.sequences_all
         if s.type == "SOUND" and s.sound
         and (s.channel - 1) == channel_idx],
        key=lambda s: s.frame_final_start
    )
    if not strips:
        return []

    segments = []
    for strip in strips:
        actual_start_frame = strip.frame_final_end - strip.frame_final_duration
        timeline_pos_s     = actual_start_frame / fps
        duration_s         = strip.frame_final_duration / fps
        file_offset_s      = getattr(strip, 'frame_offset_start', 0) / fps

        # Skip entirely outside sequence
        if timeline_pos_s + duration_s <= seq_start: continue
        if timeline_pos_s >= seq_end:                continue

        # Clamp to sequence
        if timeline_pos_s < seq_start:
            file_offset_s += (seq_start - timeline_pos_s)
            duration_s    -= (seq_start - timeline_pos_s)
            timeline_pos_s = seq_start
        if timeline_pos_s + duration_s > seq_end:
            duration_s = seq_end - timeline_pos_s

        if duration_s <= 0.001:
            continue

        filepath = bpy.path.abspath(strip.sound.filepath)
        if not os.path.exists(filepath):
            print(f"[HIJACKER] ch{channel_idx+1} missing file: {filepath}")
            continue

        # Ensure it's a WAV — decode if needed
        wav_path = filepath
        if not filepath.lower().endswith('.wav'):
            cache_key = f"hj_decoded_{channel_idx}_{os.path.basename(filepath)}.wav"
            wav_path  = os.path.join(tempfile.gettempdir(), cache_key)
            if not os.path.exists(wav_path):
                try:
                    snd  = aud.Sound.file(filepath)
                    spec = snd.specs
                    sr   = int(spec[0])
                    nch  = int(spec[1])
                    data = snd.data()
                    import numpy as np, struct
                    if data is not None and data.size > 0:
                        i16 = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
                        with _wave.open(wav_path, 'wb') as wf:
                            wf.setnchannels(nch)
                            wf.setsampwidth(2)
                            wf.setframerate(sr)
                            wf.writeframes(i16.tobytes())
                        print(f"[HIJACKER] decoded {os.path.basename(filepath)} → WAV")
                    else:
                        wav_path = None
                except Exception as e:
                    print(f"[HIJACKER] decode failed {filepath}: {e}")
                    wav_path = None

        if not wav_path or not os.path.exists(wav_path):
            continue

        # Per-segment volume = strip.volume / fader_volume
        # strip.volume is the combined product of: original VSE strip volume ×
        # all fader moves × all gain moves. The fader is tracked separately in
        # pb_sync_tracks[ch].volume and applied channel-wide via hj.set_volume().
        # Dividing out the fader here means seg.volume carries only the per-strip
        # original difference — so two strips at vol=1 and vol=3 on the same
        # channel will genuinely play at different levels, while the fader still
        # scales both uniformly via hj.set_volume without being double-applied.
        scene2    = bpy.context.scene
        tracks2   = getattr(scene2, "pb_sync_tracks", []) if scene2 else []
        fader_vol = tracks2[channel_idx].volume if channel_idx < len(tracks2) else 1.0
        fader_vol = max(fader_vol, 0.001)
        seg_vol   = float(strip.volume) / fader_vol

        segments.append({
            'filepath':       wav_path,
            'file_offset_s':  file_offset_s,
            'duration_s':     duration_s,
            'timeline_pos_s': timeline_pos_s,
            'volume':         seg_vol,
        })

    return segments


def _hj_wire_effects(channel_idx, scene):
    """
    Write current rack parameters into the engine's effect slots for a channel.
    These take effect on the next audio buffer (~5ms) — no restart needed.
    """
    engine = get_engine()
    if not engine: return

    hj = engine.get_engine()
    if not hj: return

    # Clear all slots first
    for slot in range(8):
        hj.clear_effect_slot(channel_idx, slot)

    racks = getattr(scene, "pb_racks", []) if scene else []
    slot_idx = 0

    try:
        from Racks import get_rack_channels
    except Exception:
        return

    for rack in racks:
        if not rack.enabled: continue
        try:
            assigned = get_rack_channels(rack)
        except Exception:
            continue
        if channel_idx not in assigned: continue

        etype = rack.effect_type
        params = [0.0] * 24

        if etype == "COMP_SINGLE":
            params[:6] = [rack.p0, rack.p1, rack.p2, rack.p3, rack.p4, rack.p5]
            hj.set_effect_slot(channel_idx, slot_idx, engine.FX_COMP_SINGLE, params)
            slot_idx += 1
        elif etype == "COMP_MULTI":
            params = [rack.p0, rack.p1, rack.p2, rack.p3,
                      rack.p4, rack.p5, rack.p6, rack.p7,
                      rack.p8, rack.p9, rack.p10, rack.p11,
                      rack.p12, rack.p13, rack.p14, rack.p15,
                      rack.p16, rack.p17, rack.p18, rack.p19,
                      rack.p20, rack.p21, rack.p22, rack.p23]
            hj.set_effect_slot(channel_idx, slot_idx, engine.FX_COMP_MULTI, params)
            slot_idx += 1
        elif etype == "EQ":
            for bi in range(7):
                p = getattr(rack, f'p{bi}', 0.5)
                params[bi]      = p
                params[bi + 7]  = getattr(rack, f'p{bi+7}',  0.5)
                params[bi + 14] = getattr(rack, f'p{bi+14}', 0.5)
            hj.set_effect_slot(channel_idx, slot_idx, engine.FX_EQ_PARAM, params)
            slot_idx += 1
        elif etype == "REVERB":
            params[:5] = [rack.p0, rack.p1, rack.p2, rack.p3, rack.p4]
            hj.set_effect_slot(channel_idx, slot_idx, engine.FX_REVERB_PARAM, params)
            slot_idx += 1
        elif etype == "NOISE_GATE":
            params[:5] = [rack.p0, rack.p1, rack.p2, rack.p3, rack.p4]
            hj.set_effect_slot(channel_idx, slot_idx, engine.FX_GATE_PARAM, params)
            slot_idx += 1
        elif etype == "DELAY":
            params[:5] = [rack.p0, rack.p1, rack.p2, rack.p3, rack.p4]
            hj.set_effect_slot(channel_idx, slot_idx, engine.FX_DELAY_PARAM, params)
            slot_idx += 1
        elif etype == "MIXDOWN":
            pass   # no live DSP — renders offline via rack_mixdown._start_render

        if slot_idx >= 8:
            break


def _hj_load_all_channels(scene):
    """
    Build segment playlists for all active VSE channels and load them
    into the engine. Also wires effect slots for each channel.
    Called at play-start and after seeks.
    """
    engine = get_engine()
    if not engine: return

    hj = engine.get_engine()
    if not hj: return

    if not scene or not scene.sequence_editor: return

    # Clear ALL channels first — this is critical.
    # Without this, deleting a strip from the VSE leaves the engine playing
    # the old cached playlist for that channel on the next play-start,
    # because _hj_build_segment_playlist only iterates channels that still
    # have strips and never explicitly clears channels that no longer do.
    hj.clear_all_channels()

    channels = set()
    for s in scene.sequence_editor.sequences_all:
        if s.type == "SOUND" and s.sound:
            channels.add(s.channel - 1)

    tracks = getattr(scene, "pb_sync_tracks", [])

    for ch in sorted(channels):
        segs = _hj_build_segment_playlist(ch, scene)
        if not segs:
            hj.clear_channel(ch)
            continue

        # Convert to engine.Segment objects
        seg_objects = []
        for seg in segs:
            s = engine.Segment()
            s.filepath       = seg['filepath']
            s.file_offset_s  = seg['file_offset_s']
            s.duration_s     = seg['duration_s']
            s.timeline_pos_s = seg['timeline_pos_s']
            s.volume         = seg.get('volume', 1.0)
            seg_objects.append(s)

        hj.set_channel_playlist(ch, seg_objects)

        # Volume / mute / solo
        vol    = _pb_channel_volume(ch)
        muted  = tracks[ch].mute if ch < len(tracks) else False
        soloed = tracks[ch].solo if ch < len(tracks) else False
        hj.set_volume(ch, vol)
        hj.set_mute(ch, muted)
        hj.set_solo(ch, soloed)
        pan = getattr(tracks[ch], 'pan', 0.5) if ch < len(tracks) else 0.5
        hj.set_pan(ch, pan)

        # Wire DSP effects — includes both rack effects and channel strip EQ
        _pb_rebuild_eq(ch)

        print(f"[HIJACKER] ch{ch+1} loaded {len(seg_objects)} segments")


# ---------------------------------------------------------------------------
# Transport functions — called by Blender handlers and Python UI
# ---------------------------------------------------------------------------

def _pb_start_all(scene):
    _pb_start_all_from_frame(scene, int(scene.frame_current))


def _pb_start_all_from_frame(scene, frame):
    """Load all channels into the Hijacker engine and start playback."""
    global _pb_start_frame, _pb_last_frame

    engine = get_engine()
    if not engine:
        print("[HIJACKER] engine not available — cannot play")
        return

    hj = engine.get_engine()
    if not hj:
        print("[HIJACKER] engine instance not initialised")
        return

    # Clamp frame to sequence bounds — handle both before-start and after-end
    effective_start = int(scene.frame_preview_start if scene.use_preview_range
                          else scene.frame_start)
    effective_end   = int(scene.frame_preview_end   if scene.use_preview_range
                          else scene.frame_end)

    if frame < effective_start or frame > effective_end:
        frame = effective_start
        print(f"[HIJACKER] cursor outside sequence, snapping to frame {frame}")
        try: scene.frame_set(frame)
        except Exception: pass

    _pb_last_frame  = frame
    _pb_start_frame = frame

    fps        = scene.render.fps / scene.render.fps_base
    timeline_s = max(0.0, frame / fps)   # never negative

    # Load playlists
    _hj_load_all_channels(scene)

    # Start the engine — all channels begin from same sample atomically
    hj.play(timeline_s)
    print(f"[HIJACKER] all channels started from frame {frame}")


def _pb_stop_all(scene):
    """Stop the Hijacker engine."""
    engine = get_engine()
    if not engine: return
    hj = engine.get_engine()
    if not hj: return
    hj.stop()
    print(f"[HIJACKER] stopped")


def _pb_rebuild_eq(channel_idx):
    """
    Called when a rack parameter OR channel strip EQ/gain/pan changes.
    Pushes all current params to the engine — heard next buffer (~5ms).
    """
    scene = bpy.context.scene
    if not scene: return
    engine = get_engine()
    if not engine: return
    hj = engine.get_engine()
    if not hj: return

    # Push rack effects (parametric EQ, compressor, reverb etc.)
    _hj_wire_effects(channel_idx, scene)

    # Push channel strip 3-band EQ into a dedicated effect slot (slot 7)
    # Uses FX_EQ_PARAM with simplified 3-band mapping:
    #   Band 0 (low shelf)  ← eq_low
    #   Band 3 (mid peak)   ← eq_mid
    #   Band 6 (high shelf) ← eq_high
    tracks = getattr(scene, "pb_sync_tracks", [])
    if channel_idx < len(tracks):
        t = tracks[channel_idx]
        eq_h = getattr(t, 'eq_high', 0.0)
        eq_m = getattr(t, 'eq_mid',  0.0)
        eq_l = getattr(t, 'eq_low',  0.0)
        # Only add strip EQ slot if any band is non-zero
        if abs(eq_h) > 0.01 or abs(eq_m) > 0.01 or abs(eq_l) > 0.01:
            params = [0.5] * 24   # 0.5 = 0dB for all bands
            # Normalise: gain is stored as dB (-24..+24), engine wants 0..1
            params[0] = (eq_l + 24.0) / 48.0   # band 0 = low shelf
            params[3] = (eq_m + 24.0) / 48.0   # band 3 = mid peak
            params[6] = (eq_h + 24.0) / 48.0   # band 6 = high shelf
            # Frequencies — low=200Hz, mid=1kHz, high=8kHz (log-normalised)
            import math
            params[7]  = math.log10(200  / 20) / math.log10(20000 / 20)
            params[10] = math.log10(1000 / 20) / math.log10(20000 / 20)
            params[13] = math.log10(8000 / 20) / math.log10(20000 / 20)
            # Q — moderate for all bands
            params[14] = params[17] = params[20] = 0.3
            hj.set_effect_slot(channel_idx, 7, engine.FX_EQ_PARAM, params)
        else:
            hj.clear_effect_slot(channel_idx, 7)


def _pb_do_eq_rebuild(channel_idx):
    """Alias for _pb_rebuild_eq — kept for compatibility."""
    _pb_rebuild_eq(channel_idx)


# ---------------------------------------------------------------------------
# Transport handlers — Blender animation_playback_pre / post
# ---------------------------------------------------------------------------

@bpy.app.handlers.persistent
def _pb_on_play_start(scene, depsgraph=None):
    if not _pb_engine_active: return
    try:
        print(f"[HIJACKER] play start at frame {scene.frame_current}")
        _pb_start_all(scene)
    except Exception as e:
        print(f"[HIJACKER] play start error: {e}")
        import traceback; traceback.print_exc()


@bpy.app.handlers.persistent
def _pb_on_play_stop(scene, depsgraph=None):
    if not _pb_engine_active: return
    try:
        print(f"[HIJACKER] play stop at frame {scene.frame_current}")
        _pb_stop_all(scene)
    except Exception as e:
        print(f"[HIJACKER] play stop error: {e}")


@bpy.app.handlers.persistent
def _pb_loop_detect(scene, depsgraph=None):
    """
    Watches frame_change_post for genuine loop-backs and user seeks.
    Does NOT try to keep the cursor in sync — that's the meter timer's job.
    Does NOT act on large positive frame deltas — those are just Blender
    running slow under draw load, not real seeks.
    """
    global _pb_last_frame, _pb_last_loop_time, _pb_start_frame

    if not _pb_engine_active:
        _pb_last_frame = scene.frame_current
        return

    current = scene.frame_current

    try:
        is_playing = bpy.context.screen.is_animation_playing
    except Exception:
        is_playing = False

    if not is_playing:
        _pb_last_frame = current
        return

    frame_delta = current - _pb_last_frame

    effective_start = int(scene.frame_preview_start if scene.use_preview_range
                          else scene.frame_start)
    effective_end   = int(scene.frame_preview_end   if scene.use_preview_range
                          else scene.frame_end)

    # Any forward movement within the sequence is normal playback — even large
    # deltas caused by draw-load frame skipping. Just update _pb_last_frame
    # and let the engine play from wherever it actually is.
    if frame_delta >= 0 and current <= effective_end:
        _pb_last_frame = current
        return

    fps = scene.render.fps / scene.render.fps_base

    already_there   = (abs(current - _pb_start_frame) <= 3)
    time_since_last = _time.time() - _pb_last_loop_time

    if already_there and time_since_last < 0.5:
        _pb_last_frame = current
        return

    # Clamp seek target to valid range
    seek_frame = max(effective_start, min(effective_end - 1, current))

    if frame_delta < 0 and abs(current - effective_start) <= 2:
        # Genuine loop back to start (Blender looped the animation)
        print(f"[HIJACKER] loop: {_pb_last_frame}→{current}")
        _pb_last_loop_time = _time.time()
        _pb_start_frame    = effective_start
        engine = get_engine()
        if engine:
            hj = engine.get_engine()
            if hj:
                hj.seek(max(0.0, effective_start / fps))
    elif frame_delta < 0:
        # User dragged the cursor backwards during playback
        print(f"[HIJACKER] seek: {_pb_last_frame}→{current}")
        _pb_last_loop_time = _time.time()
        _pb_start_frame    = seek_frame
        engine = get_engine()
        if engine:
            hj = engine.get_engine()
            if hj:
                hj.seek(max(0.0, seek_frame / fps))
    # Positive delta past effective_end is handled by the meter timer
    # using the engine's own clock — we never act on it here to avoid
    # false stops caused by draw-load frame skipping.

    _pb_last_frame = current

    _pb_last_frame = current


# ---------------------------------------------------------------------------
# EQ debounce timer
# With Hijacker engine, "EQ rebuild" just updates effect slot params.
# No need for the heavy rebuild logic — but we keep the debounce so
# rapid knob drags don't spam set_effect_slot calls.
# ---------------------------------------------------------------------------

def _pb_eq_timer():
    if not _pb_engine_active:
        return 0.033
    try:
        now     = _time.time()
        pending = {ch: t for ch, t in list(_pb_eq_pending.items())
                   if now - t >= _pb_eq_debounce}
        for ch in pending:
            del _pb_eq_pending[ch]
            _pb_do_eq_rebuild(ch)

        # Run spectrum analysis here (Python timer thread, not audio thread).
        # 512-sample window costs ~65K muls/channel — cheap enough for 30Hz.
        try:
            engine = get_engine()
            if engine and hasattr(engine, 'compute_spec_bins_all'):
                hj = engine.get_engine()
                if hj and hj.is_running():
                    engine.compute_spec_bins_all(48000.0)
        except Exception:
            pass

    except Exception as e:
        print(f"[HIJACKER] EQ timer error: {e}")
    return 0.033


_pb_eq_timer_registered = False


# ---------------------------------------------------------------------------
# Engine enable / disable
# ---------------------------------------------------------------------------

def _pb_engine_enable():
    """Disable Blender's audio, start Hijacker engine, register handlers."""
    global _pb_engine_active, _pb_original_device, _pb_eq_timer_registered

    if _pb_engine_active: return

    # Disable Blender's audio device
    try:
        current_device = bpy.context.preferences.system.audio_device
        if current_device and current_device != 'None':
            _pb_original_device = current_device
        else:
            import sys as _sys
            _pb_original_device = 'WASAPI' if _sys.platform == 'win32' else 'OpenAL'
        bpy.context.preferences.system.audio_device = 'None'
        print(f"[HIJACKER] Blender audio disabled (was '{current_device}', "
              f"will restore to '{_pb_original_device}')")
    except Exception as e:
        print(f"[HIJACKER] could not disable Blender audio: {e}")

    # Init Hijacker PortAudio engine
    engine = get_engine()
    if engine:
        try:
            sample_rate = 44100
            scene = bpy.context.scene
            if scene and scene.sequence_editor:
                for strip in scene.sequence_editor.sequences_all:
                    if strip.type == "SOUND" and strip.sound:
                        try:
                            import aud as _aud_sr
                            sr = int(_aud_sr.Sound.file(
                                bpy.path.abspath(strip.sound.filepath)
                            ).specs[0])
                            if sr in (44100, 48000, 96000):
                                sample_rate = sr
                            break
                        except Exception:
                            pass
            ok = engine.engine_init(sample_rate)
            if ok:
                print(f"[HIJACKER] audio engine active @ {sample_rate}Hz")
            else:
                print("[HIJACKER] WARNING: engine_init failed — no audio output")
        except Exception as e:
            print(f"[HIJACKER] engine_init error: {e}")
    else:
        print("[HIJACKER] WARNING: hijacker_engine.pyd not found — compile with build.bat")

    # Register Blender transport handlers
    if _pb_on_play_start not in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.append(_pb_on_play_start)
    if _pb_on_play_stop not in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.append(_pb_on_play_stop)
    if _pb_loop_detect not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_pb_loop_detect)

    if not _pb_eq_timer_registered:
        bpy.app.timers.register(_pb_eq_timer, first_interval=0.033)
        _pb_eq_timer_registered = True

    _pb_engine_active = True
    print("[HIJACKER] audio engine enabled")


def _pb_engine_disable():
    """Stop Hijacker engine, restore Blender's audio."""
    global _pb_engine_active, _pb_eq_timer_registered

    if not _pb_engine_active: return

    # Stop engine
    engine = get_engine()
    if engine:
        try:
            hj = engine.get_engine()
            if hj: hj.stop()
            engine.engine_shutdown()
        except Exception as e:
            print(f"[HIJACKER] engine shutdown error: {e}")

    # Restore strip mute states
    try:
        scene = bpy.context.scene
        if scene and scene.sequence_editor:
            tracks = getattr(scene, "pb_sync_tracks", [])
            for strip in scene.sequence_editor.sequences_all:
                if strip.type == "SOUND":
                    idx = strip.channel - 1
                    strip.mute = tracks[idx].mute if idx < len(tracks) else False
    except Exception: pass

    # Remove handlers
    if _pb_on_play_start in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.remove(_pb_on_play_start)
    if _pb_on_play_stop in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.remove(_pb_on_play_stop)
    if _pb_loop_detect in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_pb_loop_detect)

    if _pb_eq_timer_registered:
        try: bpy.app.timers.unregister(_pb_eq_timer)
        except Exception: pass
        _pb_eq_timer_registered = False

    # Restore Blender audio
    try:
        bpy.context.preferences.system.audio_device = _pb_original_device
        print(f"[HIJACKER] Blender audio restored to '{_pb_original_device}'")
    except Exception as e:
        print(f"[HIJACKER] could not restore Blender audio: {e}")

    _pb_engine_active = False
    print("[HIJACKER] audio engine disabled")


# ---------------------------------------------------------------------------
# Volume update — called by fader changes
# ---------------------------------------------------------------------------

def _pb_engine_update_volume(channel_idx):
    """Real-time volume update — takes effect next audio buffer."""
    engine = get_engine()
    if not engine: return
    hj = engine.get_engine()
    if not hj: return
    vol = _pb_channel_volume(channel_idx)
    hj.set_volume(channel_idx, vol)


def _pb_reprocess_channel(channel_idx):
    """
    Called when a rack is assigned or a major change happens.
    Reloads the channel playlist and rewires effects.
    If playing, seeks to current position to restart with new settings.
    """
    scene = bpy.context.scene
    if not scene: return

    engine = get_engine()
    if not engine: return
    hj = engine.get_engine()
    if not hj: return

    segs = _hj_build_segment_playlist(channel_idx, scene)
    if segs:
        seg_objects = []
        eng_mod = get_engine()
        for seg in segs:
            s = eng_mod.Segment()
            s.filepath       = seg['filepath']
            s.file_offset_s  = seg['file_offset_s']
            s.duration_s     = seg['duration_s']
            s.timeline_pos_s = seg['timeline_pos_s']
            s.volume         = seg.get('volume', 1.0)
            seg_objects.append(s)
        hj.set_channel_playlist(channel_idx, seg_objects)

    # Wire effects — use _pb_rebuild_eq to include channel strip EQ slot
    _pb_rebuild_eq(channel_idx)

    # If currently playing, seek to refresh this channel
    is_playing = bool(bpy.context.screen and
                      bpy.context.screen.is_animation_playing)
    if is_playing:
        fps = scene.render.fps / scene.render.fps_base
        hj.seek(scene.frame_current / fps)

    print(f"[HIJACKER] ch{channel_idx+1} reprocessed")


# ---------------------------------------------------------------------------
# Mute / Solo — write to strip.mute for correct VSE appearance
# AND update handle volumes so our engine reflects the change instantly
# ---------------------------------------------------------------------------

def sync_vse_mute(channel_idx, state):
    """Mute: update strip.mute (VSE appearance) and engine mute instantly."""
    scene = bpy.context.scene
    if not scene or not scene.sequence_editor: return
    for strip in scene.sequence_editor.sequences_all:
        if strip.type == "SOUND" and (strip.channel - 1) == channel_idx:
            strip.mute = state
    # Update engine — takes effect next audio buffer (~5ms)
    engine = get_engine()
    if engine:
        hj = engine.get_engine()
        if hj:
            hj.set_mute(channel_idx, state)
            hj.set_volume(channel_idx, _pb_channel_volume(channel_idx))


def sync_vse_solo(channel_idx, solo_state):
    """Solo: update strip.mute on all channels, update engine mute/solo instantly."""
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

    # Update engine for all active channels — takes effect next buffer
    engine = get_engine()
    if engine:
        hj = engine.get_engine()
        if hj:
            active_chs = set()
            for strip in scene.sequence_editor.sequences_all:
                if strip.type == "SOUND" and strip.sound:
                    active_chs.add(strip.channel - 1)
            for idx in active_chs:
                muted  = tracks[idx].mute  if idx < len(tracks) else False
                soloed_ch = tracks[idx].solo if idx < len(tracks) else False
                # When any channel is soloed, non-soloed channels are muted
                effective_mute = (idx not in soloed) if any_solo else muted
                hj.set_mute(idx, effective_mute)
                hj.set_solo(idx, soloed_ch)
                hj.set_volume(idx, _pb_channel_volume(idx))

