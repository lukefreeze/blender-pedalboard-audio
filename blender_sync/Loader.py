import math
import struct
import sys

pyd_path = r"C:\Users\lukeb\Documents\BlenderTool\blender_sync"
if pyd_path not in sys.path:
    sys.path.append(pyd_path)

# ---------------------------------------------------------------------------
# Pedalboard — bundled wheel install into addon lib/ folder
# ---------------------------------------------------------------------------
import os, glob, subprocess

def _ensure_pedalboard():
    """Install pedalboard from bundled wheel into addon lib/ if not present."""
    # Use pyd_path which is hardcoded correctly at top of file
    # __file__ may not resolve correctly when loaded as a Blender addon
    addon_dir = pyd_path
    lib_dir   = os.path.join(addon_dir, "lib")

    # Add lib to path regardless — covers already-installed case
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)

    # Check if already importable
    try:
        import pedalboard
        print(f"[PEDALBOARD] v{pedalboard.__version__} ready")
        return True
    except ImportError:
        pass

    # Find bundled wheel
    wheels = glob.glob(os.path.join(addon_dir, "wheels", "pedalboard*.whl"))
    if not wheels:
        print("[PEDALBOARD] WARNING: no wheel found in wheels/ folder")
        return False

    print(f"[PEDALBOARD] Installing from {os.path.basename(wheels[0])}...")
    os.makedirs(lib_dir, exist_ok=True)
    result = subprocess.run([
        sys.executable, "-m", "pip", "install",
        "--target", lib_dir,
        "--no-deps",
        wheels[0]
    ], capture_output=True, text=True)

    if result.returncode == 0:
        print("[PEDALBOARD] Installed successfully")
        try:
            import pedalboard
            print(f"[PEDALBOARD] v{pedalboard.__version__} ready")
            return True
        except ImportError as e:
            print(f"[PEDALBOARD] Import failed after install: {e}")
            return False
    else:
        print(f"[PEDALBOARD] Install failed: {result.stderr[-500:]}")
        return False

PEDALBOARD_AVAILABLE = _ensure_pedalboard()

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

# Import racks UI — must be after pyd_path is added to sys.path
try:
    from Racks import draw_racks, register_racks, unregister_racks, handle_click as racks_handle_click, hit_test as racks_hit_test, update_led_states, rack_knob_hit_test
    print("[RACKS] Racks.py loaded")
except ImportError as e:
    print(f"[RACKS] WARNING: could not import Racks.py: {e}")
    def draw_racks(*a, **kw): pass
    def register_racks(): pass
    def unregister_racks(): pass
    def racks_handle_click(*a, **kw): return False
    def racks_hit_test(*a, **kw): return None
    def update_led_states(*a, **kw): pass

# ---------------------------------------------------------------------------
# Version-aware engine import
# ---------------------------------------------------------------------------
def _import_engine():
    major, minor, _ = bpy.app.version
    for name in (f"pedalboard_engine_bl{major}_{minor}", "pedalboard_engine"):
        try:
            mod = __import__(name)
            print(f"[ENGINE] loaded '{name}' for Blender {major}.{minor}")
            return mod
        except ImportError:
            pass
    print("[ENGINE] WARNING: pedalboard_engine not found")
    return None

_engine = None
def get_engine():
    global _engine
    if _engine is None:
        _engine = _import_engine()
    return _engine

# ---------------------------------------------------------------------------
# Runtime UI state
# ---------------------------------------------------------------------------
pb_ui_enabled  = False
HUD_AREA_PTR   = None   # pointer of the area where the HUD was enabled
UI_SCALE      = 1.0
SCROLL_X      = 0.0
SCROLL_Y      = 0.0
is_dragging_h = False
is_dragging_v = False
is_panning    = False
is_zooming    = False

active_knob_track  = -1
active_knob_type   = ""
active_rack_knob   = None   # (rack_idx, param_idx) or None
active_fader_track = -1

# Double-click detection for fader snap-to-1
_last_click_time   = 0.0
_last_click_track  = -1
DOUBLE_CLICK_TIME  = 0.4   # seconds

FADER_MIN = 0.001
FADER_MAX = 1.25

GAIN_MIN  = 0.25    # ~  -12 dB
GAIN_MAX  = 4.0     # ~  +12 dB

# Send button layout on fader strips
SEND_BTN_H      = 20   # height of each send button (unscaled px)
SEND_BTN_GAP    = 3    # gap between send buttons
SEND_MIN_SLOTS  = 3    # always show at least this many send slots
SEND_START_Y    = 165  # distance below base_y where send section starts (below gain knob)

# Effect type abbreviations for send buttons
EFFECT_ABBREV = {
    "COMP_SINGLE": "CMP",
    "COMP_MULTI":  "MBC",
    "EQ":         "EQ",
    "REVERB":     "R",
    "NOISE_GATE": "NG",
    "DELAY":      "D",
}
GAIN_DEFAULT = 1.0  # unity

FADER_TRACK_BOTTOM = 530
FADER_HEIGHT       = 180
FADER_HANDLE_H     = 20
FADER_HANDLE_W     = 40
FADER_HANDLE_X_OFF = 45
METER_W            = 10
METER_X_OFF        = 30

# Number box sits below the fader
NUMBOX_H           = 18
NUMBOX_Y_OFFSET    = 560   # distance below base_y (just below fader area)

MAX_CHANNELS        = 32
METER_POLL_INTERVAL = 0.05
METER_DECAY         = 0.10
PEAK_HOLD_TIME      = 1.5

_engine_levels         = [0.0] * MAX_CHANNELS
_peak_hold             = [0.0] * MAX_CHANNELS
_peak_hold_timer       = [0.0] * MAX_CHANNELS
_meter_timer_registered = False

# ---------------------------------------------------------------------------
# Envelope cache  {filepath -> list[float]}
# Built once per file, reused across channels and refreshes.
# ---------------------------------------------------------------------------
_envelope_cache = {}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save_ui_state():
    scene = bpy.context.scene
    if not scene: return
    scene.pb_ui_scroll_x = SCROLL_X
    scene.pb_ui_scroll_y = SCROLL_Y
    scene.pb_ui_scale    = UI_SCALE
    scene.pb_ui_enabled  = pb_ui_enabled

def load_ui_state():
    global UI_SCALE, SCROLL_X, SCROLL_Y, pb_ui_enabled
    scene = bpy.context.scene
    if not scene: return
    UI_SCALE      = getattr(scene, "pb_ui_scale",    1.0)
    SCROLL_X      = getattr(scene, "pb_ui_scroll_x", 0.0)
    SCROLL_Y      = getattr(scene, "pb_ui_scroll_y", 0.0)
    pb_ui_enabled = getattr(scene, "pb_ui_enabled",  False)
    print(f"[STATE] scale={round(UI_SCALE,2)} "
          f"scroll=({round(SCROLL_X)},{round(SCROLL_Y)}) enabled={pb_ui_enabled}")

# ---------------------------------------------------------------------------
# Fader logic
#
# The fader is a RATIO multiplier.  When it moves from old to new:
#   strip.volume = strip.volume * (new / old)
#
# Rules:
#   - Fader is clamped to [FADER_MIN, FADER_MAX] so it never reaches 0
#   - Before multiplying, if strip.volume == 0.0 nudge it to 0.001
#     so it can be raised back up with the fader
#   - strip.volume floor after multiplication is 0.001
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Fader and gain — proportional multipliers on strip.volume.
# VSE strips always show the user's intended volume.
# Fader: 0.001–1.25  Gain: 0.25–4.0  Neither can reach 0.
# ---------------------------------------------------------------------------

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
_gr_timeline       = {}   # trimmed — from position_seconds to end
_gr_timeline_full  = {}   # full track — always from seq frame_start




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


# ---------------------------------------------------------------------------
# Build the EQ-filtered sound for a channel
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

        if slot_idx >= 8:
            break

    if slot_idx > 0:
        print(f"[WIRE] ch{channel_idx+1} wired {slot_idx} effects — compression ACTIVE")
    else:
        print(f"[WIRE] ch{channel_idx+1} no matching rack assigned — no compression")


def _pb_reprocess_channel(channel_idx):
    """Reprocess and restart a channel from current playhead position.
    Called when rack settings change during playback."""
    import bpy as _bpy
    scene = _bpy.context.scene
    if not scene: return
    is_playing = getattr(_bpy.context.screen, 'is_animation_playing', False)
    if not is_playing: return
    fps = scene.render.fps / scene.render.fps_base

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

    tl = {
        'snapshots'  : _np2.stack(fft_snaps),
        'snap_frames': snap_frames,
        'start_frame': position_seconds * scene_fps,
        'sr': sr, 'fps': scene_fps,
    }
    gl = {
        'snapshots'  : _np2.stack(gr_snaps),
        'snap_frames': snap_frames,
        'start_frame': position_seconds * scene_fps,
        'sr': sr, 'fps': scene_fps,
    }
    if full_track:
        _fft_timeline_full[channel_idx] = tl
        _gr_timeline_full[channel_idx]  = gl
    else:
        _fft_timeline[channel_idx] = tl
        _gr_timeline[channel_idx]  = gl


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
                      f"@ {sr}Hz through C++ DSP")

                # Run full C++ effect chain
                processed = engine.process_buffer(channel_idx, samples, sr)

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
                    _fft_timeline[channel_idx] = {
                        'snapshots'  : fft_array,
                        'snap_frames': snap_frames,
                        'start_frame': position_seconds * scene_fps,
                        'sr'         : sr,
                        'fps'        : scene_fps,
                    }
                    print(f"[ENGINE] ch{channel_idx+1} FFT timeline: "
                          f"{len(fft_snaps)} snapshots")
                except Exception as fe:
                    print(f"[ENGINE] FFT timeline failed: {fe}")

                # Build GR timeline by calling process_buffer on each 80ms chunk
                # and reading the exact GR values from the C++ engine.
                # This is the only accurate way — FFT energy doesn't map to
                # compressor dB thresholds correctly.
                try:
                    snap_frames_gr = int(sr * 0.08)  # 80ms snapshots
                    inp_gr = np.asarray(samples, dtype=np.float32)
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
                                    proc_ft = engine.process_buffer(
                                        channel_idx, samp_ft, sr)
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
            print(f"[ENGINE] ch{channel_idx+1} no rack assigned — "
                  f"playing unprocessed")

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
    Uses scene.frame_current for accurate position (wall-clock drifts after
    long DSP processing runs).
    EQ is applied in _pb_build_channel_sound so we just restart from
    the current playhead — no C++ DSP needed unless a rack is also assigned.
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
    _pb_start_channel(channel_idx, current_pos)


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
        if filepath not in seen and filepath not in _envelope_cache:
            get_envelope(filepath, fps)
            seen.add(filepath)


# ---------------------------------------------------------------------------
# Peak envelope builder
# ---------------------------------------------------------------------------
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
# Drawing helpers
# ---------------------------------------------------------------------------
def draw_rect(x, y, w, h, color):
    if w <= 0 or h <= 0: return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch  = batch_for_shader(shader, "TRI_STRIP",
                              {"pos": [(x,y),(x+w,y),(x,y+h),(x+w,y+h)]})
    shader.bind(); shader.uniform_float("color", color); batch.draw(shader)

def draw_circle_knob(x, y, radius, value, color, label):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts  = [(x, y)]
    for i in range(17):
        a = 2*math.pi*i/16
        verts.append((x+math.cos(a)*radius, y+math.sin(a)*radius))
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", (color[0]*.3, color[1]*.3, color[2]*.3, 1.0))
    batch.draw(shader)
    a  = (1.25*math.pi) - (value*1.5*math.pi)
    le = (x+math.cos(a)*radius, y+math.sin(a)*radius)
    lb = batch_for_shader(shader, "LINES", {"pos": [(x,y), le]})
    shader.uniform_float("color", (1,1,1,1)); lb.draw(shader)
    blf.size(0, int(9*UI_SCALE))
    blf.position(0, x-(radius*.8), y-(radius+12*UI_SCALE), 0)
    blf.draw(0, label)

def draw_meter(x, y, w, h, level, peak):
    """Smooth solid bar meter: one filled rectangle that scales up and down.
    Green 0-70%, yellow 70-90%, red 90-100%.
    No tick marks — the solid bar is less flickery and more readable.
    White peak-hold line shows the highest recent level.
    Thin scale markers on the right edge only so the bar stays clean."""
    # Background
    draw_rect(x, y, w, h, (0.03, 0.03, 0.03, 1.0))

    if level > 0.0:
        fh = min(level, 1.0) * h
        gt, yt = h * 0.70, h * 0.90

        # Draw as one solid block per colour zone, bottom up.
        # The bottom always stays lit — only the top edge moves.
        green_h  = min(fh, gt)
        yellow_h = max(0.0, min(fh - gt, yt - gt))
        red_h    = max(0.0, fh - yt)

        if green_h  > 0: draw_rect(x, y,       w, green_h,  (0.08, 0.80, 0.08, 1.0))
        if yellow_h > 0: draw_rect(x, y+gt,     w, yellow_h, (0.85, 0.80, 0.05, 1.0))
        if red_h    > 0: draw_rect(x, y+yt,     w, red_h,    (0.90, 0.10, 0.10, 1.0))

        # Very subtle gradient: slightly brighter at the top of the filled area
        # achieved by a thin highlight line at the fill top edge
        draw_rect(x, y+fh-max(1.0,UI_SCALE), w, max(1.0,UI_SCALE),
                  (1.0, 1.0, 1.0, 0.15))

    # Subtle scale markers on right edge only (3px wide) at -6, -12, -18 dB
    # approx positions: -6dB~70%, -12dB~50%, -18dB~25% of full scale
    for pos in (0.70, 0.50, 0.25):
        draw_rect(x+w-max(2.0,2*UI_SCALE), y+pos*h,
                  max(2.0,2*UI_SCALE), max(1.0,UI_SCALE),
                  (0.5, 0.5, 0.5, 0.5))

    # Peak-hold line — solid white, 2px tall
    if peak > 0.01:
        py = y + peak*h - max(2.0, 2*UI_SCALE)
        draw_rect(x, py, w, max(2.0, 2*UI_SCALE), (1.0, 1.0, 1.0, 0.95))

def draw_numbox(x, y, w, h, value, highlighted=False):
    """Draw the fader value number box."""
    bg = (0.25, 0.25, 0.25, 1.0) if highlighted else (0.15, 0.15, 0.15, 1.0)
    draw_rect(x, y, w, h, bg)
    # Border
    draw_rect(x,   y,   w, 1, (0.4, 0.4, 0.4, 1.0))
    draw_rect(x,   y+h-1, w, 1, (0.4, 0.4, 0.4, 1.0))
    draw_rect(x,   y,   1, h, (0.4, 0.4, 0.4, 1.0))
    draw_rect(x+w-1, y, 1, h, (0.4, 0.4, 0.4, 1.0))
    # Value text centred
    text = f"{value:.3f}"
    blf.size(0, int(8*UI_SCALE))
    tw = blf.dimensions(0, text)[0]
    blf.color(0, 1, 1, 1, 1)
    blf.position(0, x + (w-tw)*0.5, y + h*0.25, 0)
    blf.draw(0, text)


# ---------------------------------------------------------------------------
# Draw callback
# ---------------------------------------------------------------------------
def _send_section_height(n_racks, scale):
    """Total pixel height of the send button section for n_racks."""
    slots = max(SEND_MIN_SLOTS, n_racks)
    return slots * (SEND_BTN_H + SEND_BTN_GAP) * scale


def _draw_send_buttons(sx, base_y, channel_idx, tracks, scale):
    """Draw the send button column for one fader strip."""
    scene  = bpy.context.scene
    racks  = getattr(scene, "pb_racks", []) if scene else []
    n_racks = len(racks)
    slots   = max(SEND_MIN_SLOTS, n_racks)

    btn_w = 100 * scale
    btn_h = SEND_BTN_H * scale
    btn_x = sx + 10*scale
    start_y = base_y - SEND_START_Y*scale

    for slot in range(slots):
        by = start_y - slot * (btn_h + SEND_BTN_GAP*scale)

        if slot < n_racks:
            rack    = racks[slot]
            attr    = f'ch{channel_idx}'
            active  = getattr(rack, attr, False)
            abbrev  = EFFECT_ABBREV.get(rack.effect_type, rack.effect_type[:3])
            label   = f"{abbrev} - {slot+1}"

            if active:
                bg  = (0.0,  0.18, 0.08, 1.0)
                bc  = (0.0,  0.65, 0.35, 1.0)
                dot = (0.0,  0.9,  0.5,  1.0)
                tc  = (0.0,  0.85, 0.5,  1.0)
            else:
                bg  = (0.09, 0.09, 0.09, 1.0)
                bc  = (0.22, 0.22, 0.22, 1.0)
                dot = (0.2,  0.2,  0.2,  1.0)
                tc  = (0.35, 0.35, 0.35, 1.0)
        else:
            # Empty placeholder slot
            bg    = (0.06, 0.06, 0.06, 1.0)
            bc    = (0.14, 0.14, 0.14, 1.0)
            dot   = (0.14, 0.14, 0.14, 1.0)
            tc    = (0.2,  0.2,  0.2,  1.0)
            label = ""

        draw_rect(btn_x, by, btn_w, btn_h, bg)

        # Border
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        verts  = [(btn_x, by), (btn_x+btn_w, by),
                  (btn_x+btn_w, by+btn_h), (btn_x, by+btn_h),
                  (btn_x, by)]
        batch  = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
        shader.bind()
        shader.uniform_float("color", bc)
        batch.draw(shader)

        # Active dot indicator
        dot_r = 3 * scale
        dot_x = btn_x + 8*scale
        dot_y = by + btn_h/2
        segs  = 10
        import math as _m
        verts2 = [(dot_x, dot_y)]
        for si in range(segs+1):
            a = 2*_m.pi*si/segs
            verts2.append((dot_x+_m.cos(a)*dot_r,
                           dot_y+_m.sin(a)*dot_r))
        batch2 = batch_for_shader(shader, "TRI_FAN", {"pos": verts2})
        shader.uniform_float("color", dot)
        batch2.draw(shader)

        # Label
        if label:
            blf.size(0, max(1, int(8*scale)))
            blf.color(0, *tc)
            blf.position(0, btn_x + 16*scale, by + btn_h/2 - 4*scale, 0)
            blf.draw(0, label)
            # Always reset to white so subsequent draws are not affected
            blf.color(0, 1, 1, 1, 1)


def draw_callback_px(self, context):
    global pb_ui_enabled, UI_SCALE, SCROLL_X, SCROLL_Y
    if not pb_ui_enabled: return
    # Only draw in the specific area where the HUD was enabled —
    # prevents the HUD appearing in other Shader Editor areas
    if HUD_AREA_PTR is not None and bpy.context.area is not None:
        if bpy.context.area.as_pointer() != HUD_AREA_PTR:
            return
    region = bpy.context.region
    if not region: return
    width, height = region.width, region.height

    try:
        gpu.state.blend_set("ALPHA")
        draw_rect(0, 0, width, height, (0.01, 0.01, 0.01, 0.95))

        blf.color(0, 1, 1, 1, 1)
        blf.size(0, int(20*UI_SCALE))
        blf.position(0, (30*UI_SCALE)+SCROLL_X,
                     height-(40*UI_SCALE)-SCROLL_Y, 0)
        blf.draw(0, f"PEDALBOARD HUD | Scale: {round(UI_SCALE,2)}")

        tracks = getattr(bpy.context.scene, "pb_sync_tracks", [])
        base_y = height - (150*UI_SCALE) - SCROLL_Y

        # Draw all allocated tracks contiguously — DEFAULT_CHANNELS minimum
        # plus any extra channels that have strips above that count.
        draw_col = 0
        for i, track in enumerate(tracks):
            sx = (30*UI_SCALE) + (draw_col*135*UI_SCALE) + SCROLL_X
            draw_col += 1
            if sx+(120*UI_SCALE) < 0 or sx > width: continue

            # Strip background — tall enough for numbox + send buttons
            n_racks_bg = len(getattr(bpy.context.scene, "pb_racks", []))
            send_h_bg  = _send_section_height(n_racks_bg, UI_SCALE)
            strip_h    = 600*UI_SCALE + send_h_bg
            draw_rect(sx, base_y-strip_h,
                      120*UI_SCALE, strip_h, (0.07,0.07,0.07,1.0))

            blf.size(0, int(11*UI_SCALE))
            blf.position(0, sx+(10*UI_SCALE), base_y+(5*UI_SCALE), 0)
            blf.draw(0, f"CH {i+1}")

            # --- MUTE & SOLO ---  (drawn first — top of strip)
            m_c = (0.9,0.1,0.1,1.0) if track.mute else (0.2,0.2,0.2,1.0)
            draw_rect(sx+(10*UI_SCALE), base_y-(50*UI_SCALE),
                      45*UI_SCALE, 30*UI_SCALE, m_c)
            s_c = (0.9,0.9,0.1,1.0) if track.solo else (0.2,0.2,0.2,1.0)
            draw_rect(sx+(65*UI_SCALE), base_y-(50*UI_SCALE),
                      45*UI_SCALE, 30*UI_SCALE, s_c)
            blf.size(0, int(9*UI_SCALE))
            blf.color(0,1,1,1,1)
            blf.position(0, sx+(24*UI_SCALE), base_y-(38*UI_SCALE), 0)
            blf.draw(0, "M")
            blf.position(0, sx+(79*UI_SCALE), base_y-(38*UI_SCALE), 0)
            blf.draw(0, "S")

            # --- GAIN KNOB --- (below mute/solo)
            kx = sx + (60*UI_SCALE)
            import math as _m
            gain_norm  = (track.gain - GAIN_MIN) / (GAIN_MAX - GAIN_MIN)
            gain_db    = round(20 * _m.log10(max(0.001, track.gain)), 1)
            gain_label = f"G:{gain_db:+.1f}dB"
            draw_circle_knob(kx, base_y-(100*UI_SCALE), 20*UI_SCALE,
                gain_norm, (0.1,0.5,0.1), gain_label)

            # --- SEND BUTTONS --- (below gain knob)
            n_racks   = len(getattr(bpy.context.scene, "pb_racks", []))
            send_h    = _send_section_height(n_racks, UI_SCALE)
            _draw_send_buttons(sx, base_y, i, tracks, UI_SCALE)
            # Reset blf color to white after send buttons to prevent bleed
            blf.color(0, 1, 1, 1, 1)

            # --- EQ KNOBS --- (below send buttons)
            eq_start = 175*UI_SCALE + send_h
            draw_circle_knob(kx, base_y-eq_start, 16*UI_SCALE,
                (track.eq_high+24)/48, (0.2,0.2,0.6), f"H:{int(track.eq_high)}")
            draw_circle_knob(kx, base_y-(eq_start+50*UI_SCALE), 16*UI_SCALE,
                (track.eq_mid+24)/48, (0.4,0.2,0.6), f"M:{int(track.eq_mid)}")
            draw_circle_knob(kx, base_y-(eq_start+100*UI_SCALE), 16*UI_SCALE,
                (track.eq_low+24)/48, (0.6,0.2,0.4), f"L:{int(track.eq_low)}")

            # --- FADER ---
            f_h   = FADER_HEIGHT * UI_SCALE
            # Push fader down by send section height so it clears the buttons
            n_racks_f = len(getattr(bpy.context.scene, "pb_racks", []))
            f_y   = base_y - (FADER_TRACK_BOTTOM * UI_SCALE) - _send_section_height(n_racks_f, UI_SCALE)
            f_hw  = FADER_HANDLE_W * UI_SCALE
            f_hh  = FADER_HANDLE_H * UI_SCALE
            f_hx  = sx + (FADER_HANDLE_X_OFF * UI_SCALE)

            # Rail
            rail_cx = f_hx + f_hw/2 - (5*UI_SCALE)
            draw_rect(rail_cx, f_y, 10*UI_SCALE, f_h, (0.02,0.02,0.02,1.0))

            # Handle position — map fader 0.001..1.25 to 0..f_h
            fader_norm = (track.volume - FADER_MIN) / (FADER_MAX - FADER_MIN)
            fader_norm = max(0.0, min(1.0, fader_norm))
            h_p = f_y + (fader_norm * f_h) - (f_hh/2)

            # Unity mark (thin line across rail at the 1.0 position)
            unity_norm = (1.0 - FADER_MIN) / (FADER_MAX - FADER_MIN)
            unity_y    = f_y + unity_norm * f_h
            draw_rect(rail_cx - (5*UI_SCALE), unity_y,
                      20*UI_SCALE, max(1.0, UI_SCALE), (0.6, 0.6, 0.6, 0.8))

            # Handle
            draw_rect(f_hx, h_p, f_hw, f_hh, (0.5,0.5,0.5,1.0))

            # --- METER (left of fader rail) ---
            meter_x = sx + (METER_X_OFF * UI_SCALE)
            draw_meter(meter_x, f_y, METER_W*UI_SCALE, f_h,
                       _engine_levels[i] if i < MAX_CHANNELS else 0.0,
                       _peak_hold[i]     if i < MAX_CHANNELS else 0.0)

            # --- NUMBER BOX below fader ---
            nb_w = 90 * UI_SCALE
            nb_h = NUMBOX_H * UI_SCALE
            nb_x = sx + (15 * UI_SCALE)
            nb_y = f_y - (nb_h + 4*UI_SCALE)
            draw_numbox(nb_x, nb_y, nb_w, nb_h, track.volume)

            # "SENDS" label — drawn above button 1
            # Button 1 top = base_y - SEND_START_Y*scale
            # Label sits (SEND_BTN_H + 4)px above button 1 top
            blf.size(0, int(8*UI_SCALE))
            blf.color(0, 0.35, 0.35, 0.35, 1.0)
            blf.position(0, sx + 10*UI_SCALE,
                         base_y - (SEND_START_Y - SEND_BTN_H - 6)*UI_SCALE, 0)
            blf.draw(0, "SENDS")
            blf.color(0, 1, 1, 1, 1)

        # --- RACKS ---
        try:
            draw_racks(width, height, SCROLL_X, SCROLL_Y, UI_SCALE)
        except Exception as e:
            print(f"[RACKS] draw error: {e}")

        # --- SCROLLBARS ---
        draw_rect(0, 0, width, 25, (0.05,0.05,0.05,1.0))
        hx = (abs(SCROLL_X)/5000)*(width-150) if SCROLL_X != 0 else 0
        draw_rect(max(0,hx), 2, 150, 21, (0.4,0.4,0.4,1.0))
        draw_rect(width-25, 0, 25, height, (0.05,0.05,0.05,1.0))
        vy = (height-100-(abs(SCROLL_Y)/2000*(height-100))
              if SCROLL_Y != 0 else height-100)
        draw_rect(width-23, vy, 21, 100, (0.4,0.4,0.4,1.0))

    except Exception as e:
        print(f"DRAW ERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        # Always reset GPU blend state to prevent glitched background
        try:
            gpu.state.blend_set("NONE")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Operator: set fader value via popup (single-click on number box)
# ---------------------------------------------------------------------------
class VSE_OT_SetFaderValue(bpy.types.Operator):
    bl_idname      = "vse.set_fader_value"
    bl_label       = "Set Fader Value"
    bl_description = "Type an exact fader value (0.001 – 1.25). Enter 1 for unity."
    bl_options     = {"REGISTER", "UNDO"}

    channel_idx: bpy.props.IntProperty()
    new_value:   bpy.props.FloatProperty(
        name="Fader Value", min=FADER_MIN, max=FADER_MAX,
        default=1.0, step=1, precision=3)

    def invoke(self, context, event):
        # Pre-fill with current value
        tracks = getattr(context.scene, "pb_sync_tracks", [])
        if self.channel_idx < len(tracks):
            self.new_value = tracks[self.channel_idx].volume
        return context.window_manager.invoke_props_dialog(self, width=200)

    def draw(self, context):
        self.layout.prop(self, "new_value")

    def execute(self, context):
        tracks = getattr(context.scene, "pb_sync_tracks", [])
        if self.channel_idx >= len(tracks):
            return {"CANCELLED"}
        track     = tracks[self.channel_idx]
        old_fader = track.volume
        new_fader = max(FADER_MIN, min(FADER_MAX, self.new_value))
        apply_fader_to_channel(self.channel_idx, old_fader, new_fader)
        track.volume = new_fader
        # Update meter immediately even if paused
        _meter_timer._last_frame = None
        _meter_timer()
        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Modal operator
# ---------------------------------------------------------------------------
class VSE_OT_PB_Interaction(bpy.types.Operator):
    bl_idname = "vse.pb_interaction"
    bl_label  = "PB Interaction"

    def modal(self, context, event):
        global pb_ui_enabled, UI_SCALE, SCROLL_X, SCROLL_Y, \
               is_panning, is_zooming, \
               active_knob_track, active_knob_type, active_fader_track, \
               active_rack_knob, \
               is_dragging_h, is_dragging_v, \
               _last_click_time, _last_click_track

        if not pb_ui_enabled: return {"FINISHED"}
        if context.area is None or context.area.type != "NODE_EDITOR":
            return {"PASS_THROUGH"}

        region = context.region
        rx, ry  = event.mouse_region_x, event.mouse_region_y
        ry_top  = region.height - ry

        is_inside   = 0<=rx<=region.width and 0<=ry<=region.height
        mid_drag    = is_panning or is_zooming
        widget_drag = (active_fader_track != -1 or active_knob_track != -1
                       or active_rack_knob is not None
                       or is_dragging_h or is_dragging_v)

        if not (is_inside or mid_drag or widget_drag):
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            if is_zooming:
                old_s    = UI_SCALE
                UI_SCALE = max(0.1, min(5.0, UI_SCALE +
                               (event.mouse_x-event.mouse_prev_x)*0.01))
                r        = UI_SCALE/old_s
                SCROLL_X = rx-(rx-SCROLL_X)*r
                SCROLL_Y = ry_top-(ry_top-SCROLL_Y)*r
                save_ui_state(); context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if is_panning:
                SCROLL_X += (event.mouse_x-event.mouse_prev_x)*2
                SCROLL_Y -= (event.mouse_y-event.mouse_prev_y)*2
                save_ui_state(); context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if is_dragging_h:
                SCROLL_X -= (event.mouse_x-event.mouse_prev_x)*12
                save_ui_state(); context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if is_dragging_v:
                SCROLL_Y += (event.mouse_y-event.mouse_prev_y)*12
                save_ui_state(); context.area.tag_redraw()
                return {"RUNNING_MODAL"}

            if active_fader_track != -1:
                tracks = context.scene.pb_sync_tracks
                track  = tracks[active_fader_track]
                delta  = (event.mouse_y-event.mouse_prev_y) / (FADER_HEIGHT*UI_SCALE)
                fader_delta = delta * (FADER_MAX - FADER_MIN)
                old_fader   = track.volume
                new_fader   = max(FADER_MIN, min(FADER_MAX,
                                                  track.volume + fader_delta))
                apply_fader_to_channel(active_fader_track, old_fader, new_fader)
                track.volume = new_fader
                # Force meter to recalculate immediately so the level updates
                # while paused — without this it only updates on next play tick.
                _meter_timer._last_frame = None
                _meter_timer()
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}

            if active_knob_track != -1:
                track = context.scene.pb_sync_tracks[active_knob_track]
                delta = (event.mouse_y-event.mouse_prev_y)*0.005
                if   active_knob_type == "GAIN":
                    old_gain = track.gain
                    new_gain = max(GAIN_MIN, min(GAIN_MAX, track.gain + delta * (GAIN_MAX - GAIN_MIN)))
                    apply_gain_to_channel(active_knob_track, old_gain, new_gain)
                    track.gain = new_gain
                    _meter_timer._last_frame = None
                    _meter_timer()
                elif active_knob_type == "HIGH":
                    track.eq_high = max(-24.0, min(24.0, track.eq_high+delta*100))
                    if _pb_engine_active: _pb_rebuild_eq(active_knob_track)
                elif active_knob_type == "MID":
                    track.eq_mid  = max(-24.0, min(24.0, track.eq_mid+delta*100))
                    if _pb_engine_active: _pb_rebuild_eq(active_knob_track)
                elif active_knob_type == "LOW":
                    track.eq_low  = max(-24.0, min(24.0, track.eq_low+delta*100))
                    if _pb_engine_active: _pb_rebuild_eq(active_knob_track)
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}

            if active_rack_knob is not None:
                rack_idx, param_idx = active_rack_knob
                racks = getattr(context.scene, "pb_racks", [])
                if rack_idx < len(racks):
                    rack   = racks[rack_idx]
                    from Racks import EFFECT_PARAMS, set_rack_param
                    delta  = (event.mouse_y - event.mouse_prev_y) * 0.004
                    if rack.effect_type == "COMP_MULTI":
                        if param_idx >= 16:
                            # Gain fader — larger delta so handle tracks mouse
                            from Racks import RACK_EXPANDED_H_MB, RACK_RAIL_H
                            rh_mb   = RACK_EXPANDED_H_MB * UI_SCALE
                            body_h  = rh_mb - RACK_RAIL_H * UI_SCALE
                            fdr_h   = max((body_h*0.52 - 8*UI_SCALE - 26*UI_SCALE - 26*UI_SCALE - 2*UI_SCALE), 40*UI_SCALE)
                            fdr_delta = (event.mouse_y - event.mouse_prev_y) / max(fdr_h, 1)
                            old_v = getattr(rack, f'p{param_idx}', 0.5)
                            new_v = max(0.0, min(1.0, old_v + fdr_delta))
                            set_rack_param(rack, param_idx, new_v)
                        else:
                            # Knobs — relative delta
                            old_v = getattr(rack, f'p{param_idx}', 0.0)
                            new_v = max(0.0, min(1.0, old_v + delta))
                            set_rack_param(rack, param_idx, new_v)
                    else:
                        if rack.effect_type == "EQ":
                            # EQ knobs: p0-p4=gain, p5-p9=freq, p10-p14=Q
                            # All stored 0-1 normalised, just clamp and set
                            old_v = getattr(rack, f'p{param_idx}', 0.0)
                            new_v = max(0.0, min(1.0, old_v + delta))
                            set_rack_param(rack, param_idx, new_v)
                        else:
                            params = EFFECT_PARAMS.get(rack.effect_type, [])
                            if param_idx < len(params):
                                old_v = getattr(rack, f'p{param_idx}', 0.0)
                                new_v = max(0.0, min(1.0, old_v + delta))
                                set_rack_param(rack, param_idx, new_v)
                    # Reprocess audio with new settings
                    if _pb_engine_active:
                        try:
                            from Racks import get_rack_channels
                            assigned = get_rack_channels(rack)
                            for ch in assigned:
                                _pb_wire_rack_to_engine(ch)
                                _pb_reprocess_channel(ch)
                        except Exception as e:
                            print(f"[WIRE] live update failed: {e}")
                    context.area.tag_redraw()
                return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                if ry < 25:
                    is_dragging_h = True; return {"RUNNING_MODAL"}
                if rx > region.width-25:
                    is_dragging_v = True; return {"RUNNING_MODAL"}

                import time
                now    = time.time()
                base_y = region.height-(150*UI_SCALE)-SCROLL_Y
                f_h    = FADER_HEIGHT * UI_SCALE
                # Must match draw loop exactly — include send section height
                n_racks_ht = len(getattr(context.scene, "pb_racks", []))
                send_h_ht  = _send_section_height(n_racks_ht, UI_SCALE)
                f_y    = base_y - (FADER_TRACK_BOTTOM*UI_SCALE) - send_h_ht
                f_hw   = FADER_HANDLE_W * UI_SCALE
                f_hh   = FADER_HANDLE_H * UI_SCALE
                nb_h   = NUMBOX_H * UI_SCALE

                # Must mirror draw loop's draw_col logic exactly
                _hit_draw_col = 0
                for i, track in enumerate(context.scene.pb_sync_tracks):
                    sx  = (30*UI_SCALE)+(_hit_draw_col*135*UI_SCALE)+SCROLL_X
                    _hit_draw_col += 1
                    kx  = sx+(60*UI_SCALE)
                    f_hx = sx+(FADER_HANDLE_X_OFF*UI_SCALE)
                    nb_x = sx+(15*UI_SCALE)
                    nb_y = f_y - (nb_h + 4*UI_SCALE)
                    nb_w = 90*UI_SCALE
                    fader_norm = (track.volume-FADER_MIN)/(FADER_MAX-FADER_MIN)
                    fader_norm = max(0.0, min(1.0, fader_norm))
                    h_p  = f_y+(fader_norm*f_h)-(f_hh/2)
                    fhb  = f_y - (f_hh/2)
                    fht  = f_y + f_h + (f_hh/2)

                    # Knobs — positions must match draw loop exactly
                    n_racks_k  = len(getattr(context.scene, "pb_racks", []))
                    send_h_k   = _send_section_height(n_racks_k, UI_SCALE)
                    eq_start_k = 175*UI_SCALE + send_h_k
                    if math.dist((rx,ry),(kx,base_y-100*UI_SCALE))<20*UI_SCALE:
                        active_knob_track,active_knob_type=i,"GAIN"; return {"RUNNING_MODAL"}
                    if math.dist((rx,ry),(kx,base_y-eq_start_k))<16*UI_SCALE:
                        active_knob_track,active_knob_type=i,"HIGH"; return {"RUNNING_MODAL"}
                    if math.dist((rx,ry),(kx,base_y-(eq_start_k+50*UI_SCALE)))<16*UI_SCALE:
                        active_knob_track,active_knob_type=i,"MID";  return {"RUNNING_MODAL"}
                    if math.dist((rx,ry),(kx,base_y-(eq_start_k+100*UI_SCALE)))<16*UI_SCALE:
                        active_knob_track,active_knob_type=i,"LOW";  return {"RUNNING_MODAL"}

                    # Fader track — checked BEFORE numbox so handle at
                    # bottom position is always reachable
                    if f_hx < rx < f_hx+f_hw and fhb < ry < fht:
                        # Double-click on fader snaps to 1.0
                        if (now - _last_click_time < DOUBLE_CLICK_TIME
                                and _last_click_track == i):
                            old_fader = track.volume
                            apply_fader_to_channel(i, old_fader, 1.0)
                            track.volume      = 1.0
                            _last_click_time  = 0.0
                            _last_click_track = -1
                            context.area.tag_redraw()
                            return {"RUNNING_MODAL"}
                        _last_click_time  = now
                        _last_click_track = i
                        active_fader_track = i
                        return {"RUNNING_MODAL"}

                    # Number box — single click opens popup, double-click snaps to 1.0
                    if nb_x < rx < nb_x+nb_w and nb_y < ry < nb_y+nb_h:
                        if (now - _last_click_time < DOUBLE_CLICK_TIME
                                and _last_click_track == i):
                            # Double-click: snap to unity
                            old_fader = track.volume
                            apply_fader_to_channel(i, old_fader, 1.0)
                            track.volume = 1.0
                            _last_click_time  = 0.0
                            _last_click_track = -1
                        else:
                            # Single click: open value entry popup
                            _last_click_time  = now
                            _last_click_track = i
                            bpy.ops.vse.set_fader_value(
                                "INVOKE_DEFAULT", channel_idx=i,
                                new_value=track.volume)
                        context.area.tag_redraw()
                        return {"RUNNING_MODAL"}

                    # Mute / Solo
                    if sx<rx<sx+120*UI_SCALE and base_y-50*UI_SCALE<ry<base_y:
                        if rx < sx+60*UI_SCALE:
                            track.mute = not track.mute
                            sync_vse_mute(i, track.mute)
                        else:
                            track.solo = not track.solo
                            sync_vse_solo(i, track.solo)
                        context.area.tag_redraw()
                        return {"RUNNING_MODAL"}

                    # Send buttons
                    n_racks_s = len(getattr(context.scene, "pb_racks", []))
                    slots_s   = max(SEND_MIN_SLOTS, n_racks_s)
                    btn_x_s   = sx + 10*UI_SCALE
                    btn_w_s   = 100*UI_SCALE
                    btn_h_s   = SEND_BTN_H*UI_SCALE
                    start_y_s = base_y - SEND_START_Y*UI_SCALE
                    if btn_x_s <= rx <= btn_x_s+btn_w_s:
                        for slot in range(slots_s):
                            by_s = start_y_s - slot*(btn_h_s+SEND_BTN_GAP*UI_SCALE)
                            if by_s <= ry <= by_s+btn_h_s and slot < n_racks_s:
                                rack  = context.scene.pb_racks[slot]
                                attr  = f'ch{i}'
                                if hasattr(rack, attr):
                                    setattr(rack, attr,
                                            not getattr(rack, attr, False))
                                    try:
                                        from Racks import _trigger_reprocess
                                        _trigger_reprocess(slot, rack, context)
                                    except Exception as _sre:
                                        print(f"[ENGINE] send reprocess failed: {_sre}")
                                    context.area.tag_redraw()
                                return {"RUNNING_MODAL"}

                # Check rack knob clicks
                rk_hit = rack_knob_hit_test(rx, ry, region.height,
                                            SCROLL_X, SCROLL_Y, UI_SCALE)
                if rk_hit is not None:
                    active_rack_knob = rk_hit
                    return {"RUNNING_MODAL"}

                # Check rack clicks (below fader section)
                hit = racks_hit_test(rx, ry, region.height,
                                     SCROLL_X, SCROLL_Y, UI_SCALE)
                if hit:
                    if racks_handle_click(hit, context):
                        context.area.tag_redraw()
                    return {"RUNNING_MODAL"}

            elif event.value == "RELEASE":
                active_knob_track  = -1
                active_knob_type   = ""
                active_fader_track = -1
                active_rack_knob   = None
                is_dragging_h      = False
                is_dragging_v      = False

        if event.type == "MIDDLEMOUSE":
            if event.value == "PRESS":
                is_zooming = event.ctrl; is_panning = not event.ctrl
            else:
                is_zooming = is_panning = False; save_ui_state()
            return {"RUNNING_MODAL"}

        if event.type in {"WHEELUPMOUSE","WHEELDOWNMOUSE"}:
            if is_inside:
                step = 150 if event.type=="WHEELUPMOUSE" else -150
                if event.shift: SCROLL_X += step
                else:           SCROLL_Y += step
                save_ui_state(); context.area.tag_redraw()
                return {"RUNNING_MODAL"}

        # Trackpad two-finger pan
        if event.type == "TRACKPADPAN" and is_inside:
            SCROLL_X += event.mouse_x - event.mouse_prev_x
            SCROLL_Y -= event.mouse_y - event.mouse_prev_y
            save_ui_state(); context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        # Trackpad two-finger pinch zoom
        # Delta is in X axis: prev_x > mouse_x = pinching in (zoom out)
        #                      prev_x < mouse_x = pinching out (zoom in)
        if event.type == "TRACKPADZOOM" and is_inside:
            zoom_delta = (event.mouse_x - event.mouse_prev_x) * 0.003
            new_scale  = max(0.3, min(3.0, UI_SCALE + zoom_delta))
            cx = region.width  / 2
            cy = region.height / 2
            SCROLL_X  = cx - (cx - SCROLL_X) * (new_scale / max(UI_SCALE, 0.001))
            SCROLL_Y  = cy - (cy - SCROLL_Y) * (new_scale / max(UI_SCALE, 0.001))
            UI_SCALE  = new_scale
            save_ui_state(); context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "HOME" and event.value == "PRESS":
            SCROLL_X = 0.0
            SCROLL_Y = 0.0
            save_ui_state()
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}


# ---------------------------------------------------------------------------
# Property group
# ---------------------------------------------------------------------------
class PB_TrackSettings(bpy.types.PropertyGroup):
    mute:    bpy.props.BoolProperty(default=False)
    solo:    bpy.props.BoolProperty(default=False)
    # volume stores the fader position (FADER_MIN..FADER_MAX), saved in .blend
    volume:  bpy.props.FloatProperty(
        default=1.0, min=FADER_MIN, max=FADER_MAX,
        description="Channel fader (proportional multiplier on strip volumes)")
    gain:    bpy.props.FloatProperty(default=GAIN_DEFAULT, min=GAIN_MIN, max=GAIN_MAX)
    eq_high: bpy.props.FloatProperty(default=0.0, min=-24.0, max=24.0)
    eq_mid:  bpy.props.FloatProperty(default=0.0, min=-24.0, max=24.0)
    eq_low:  bpy.props.FloatProperty(default=0.0, min=-24.0, max=24.0)


# ---------------------------------------------------------------------------
# Operators & panel
# ---------------------------------------------------------------------------
class VSE_OT_TogglePBGui(bpy.types.Operator):
    bl_idname = "vse.toggle_pb_gui"
    bl_label  = "Toggle Pedalboard"

    def execute(self, context):
        global pb_ui_enabled, SCROLL_X, SCROLL_Y, HUD_AREA_PTR
        load_ui_state()
        pb_ui_enabled = not pb_ui_enabled
        print(f"[TOGGLE] pb_ui_enabled={pb_ui_enabled}")
        if pb_ui_enabled:
            # Store the pointer of the area where the HUD is being enabled
            # so the draw callback only fires for this specific area
            HUD_AREA_PTR = context.area.as_pointer()
            print(f"[TOGGLE] HUD area ptr={HUD_AREA_PTR}")
            # Always reset scroll to (0,0) on enable so the faders are
            # immediately visible — user can scroll/zoom from there.
            SCROLL_X = 0.0
            SCROLL_Y = 0.0
            save_ui_state()
            bpy.ops.vse.pb_interaction("INVOKE_DEFAULT")
            _ensure_meter_timer()
            prebuild_envelopes()
            _pb_engine_enable()
        else:
            HUD_AREA_PTR = None
            save_ui_state()
            _cancel_meter_timer()
            _pb_engine_disable()
        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


# Minimum number of faders always shown — matches Blender VSE default layout
DEFAULT_CHANNELS = 9


def _sync_tracks_to_vse(scene, reset_values=False):
    """Sync pb_sync_tracks to VSE channel layout.

    Always maintains at least DEFAULT_CHANNELS (9) faders so the mixer
    matches Blender's default VSE layout even when channels are empty.
    Auto-expands beyond 9 when strips appear on higher channels.
    Metastrips (type=META) are treated as a single channel — their
    interior strips are not recursed into.
    Preserves existing fader/EQ values when reset_values=False.
    """
    if not scene: return

    # Find the highest channel that has a sound strip (non-meta, top-level)
    highest_strip_channel = 0
    if scene.sequence_editor:
        for s in scene.sequence_editor.sequences_all:
            if s.type == "SOUND" and s.sound:
                highest_strip_channel = max(highest_strip_channel, s.channel)

    # Always show at least DEFAULT_CHANNELS faders
    needed = max(DEFAULT_CHANNELS, highest_strip_channel)

    existing = len(scene.pb_sync_tracks)

    # Add any missing tracks
    for i in range(existing, needed):
        track = scene.pb_sync_tracks.add()
        track.volume = 1.0
        if scene.sequence_editor:
            for s in scene.sequence_editor.sequences_all:
                if s.channel == (i + 1) and s.type == "SOUND":
                    track.mute = s.mute
                    break

    # If reset_values, reset faders to unity but preserve mute from strips
    if reset_values:
        for i, track in enumerate(scene.pb_sync_tracks):
            track.volume = 1.0
            track.gain   = 1.0
            track.eq_low = track.eq_mid = track.eq_high = 0.0
            if scene.sequence_editor:
                for s in scene.sequence_editor.sequences_all:
                    if s.channel == (i + 1) and s.type == "SOUND":
                        track.mute = s.mute
                        break

    active = [s.channel for s in scene.sequence_editor.sequences_all
              if s.type == "SOUND" and s.sound] if scene.sequence_editor else []
    print(f"[TRACKS] {len(scene.pb_sync_tracks)} tracks "
          f"(default={DEFAULT_CHANNELS}, "
          f"highest strip ch={highest_strip_channel}, "
          f"active={sorted(set(active))[:12]}{'...' if len(set(active))>12 else ''})")


def _get_active_channel_count(scene):
    """Return the number of VSE channels that have sound strips."""
    if not scene or not scene.sequence_editor:
        return 0
    return len(set(
        s.channel - 1
        for s in scene.sequence_editor.sequences_all
        if s.type == "SOUND" and s.sound
    ))


class VSE_OT_RefreshPBTracks(bpy.types.Operator):
    bl_idname = "vse.refresh_pb_tracks"
    bl_label  = "Refresh Tracks"

    def execute(self, context):
        _sync_tracks_to_vse(context.scene, reset_values=True)

        if pb_ui_enabled:
            prebuild_envelopes()

        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


class VSE_PT_Pedalboard_Panel(bpy.types.Panel):
    bl_label       = "Pedalboard Engine"
    bl_space_type  = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category    = "Pedalboard"

    def draw(self, context):
        self.layout.operator("vse.refresh_pb_tracks")
        self.layout.operator("vse.toggle_pb_gui")

def draw_header_buttons(self, context):
    self.layout.operator("vse.toggle_pb_gui", text="Pedalboard HUD")


# ---------------------------------------------------------------------------
# Load handler
# ---------------------------------------------------------------------------
@bpy.app.handlers.persistent
def on_load_post(filepath, *args):
    global pb_ui_enabled, _envelope_cache, _engine
    _pb_engine_disable()
    _envelope_cache.clear()
    _engine = None
    load_ui_state()
    if pb_ui_enabled:
        bpy.app.timers.register(_deferred_invoke, first_interval=0.1)

def _deferred_invoke():
    try:
        bpy.ops.vse.pb_interaction("INVOKE_DEFAULT")
        _ensure_meter_timer()
        prebuild_envelopes()
        _pb_engine_enable()
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception as e:
        print(f"[STATE] deferred invoke failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
classes = (
    PB_TrackSettings,
    VSE_OT_SetFaderValue,
    VSE_OT_TogglePBGui,
    VSE_OT_RefreshPBTracks,
    VSE_OT_PB_Interaction,
    VSE_PT_Pedalboard_Panel,
)
_handle = None

def register():
    global _handle
    register_racks()
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.pb_sync_tracks  = bpy.props.CollectionProperty(type=PB_TrackSettings)
    bpy.types.Scene.pb_ui_scale     = bpy.props.FloatProperty(default=1.0)
    bpy.types.Scene.pb_ui_scroll_x  = bpy.props.FloatProperty(default=0.0)
    bpy.types.Scene.pb_ui_scroll_y  = bpy.props.FloatProperty(default=0.0)
    bpy.types.Scene.pb_ui_enabled   = bpy.props.BoolProperty(default=False)

    bpy.types.NODE_HT_header.append(draw_header_buttons)
    _handle = bpy.types.SpaceNodeEditor.draw_handler_add(
        draw_callback_px, (None, None), "WINDOW", "POST_PIXEL")

    if on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load_post)

    print("[REGISTER] Pedalboard registered")

def unregister():
    global _handle
    _cancel_meter_timer()
    _pb_engine_disable()
    unregister_racks()

    if on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load_post)

    if _handle:
        bpy.types.SpaceNodeEditor.draw_handler_remove(_handle, "WINDOW")

    bpy.types.NODE_HT_header.remove(draw_header_buttons)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.pb_ui_scale
    del bpy.types.Scene.pb_ui_scroll_x
    del bpy.types.Scene.pb_ui_scroll_y
    del bpy.types.Scene.pb_ui_enabled

    print("[UNREGISTER] Pedalboard unregistered")

if __name__ == "__main__":
    register()
