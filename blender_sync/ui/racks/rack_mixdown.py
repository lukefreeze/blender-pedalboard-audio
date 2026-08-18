# =============================================================================
# rack_mixdown.py
# Mixdown rack — render all channels (with effects baked) to WAV/FLAC.
#
# Two render modes:
#   MIX    — all selected channels summed to one stereo file
#   BAKE   — each selected channel rendered to its own file, effects baked
#
# Settings stored in PB_RackSettings floats:
#   p0 = render_mode      0.0=mix  1.0=bake
#   p1 = format           0.0=WAV  1.0=FLAC
#   p2 = sample_rate      0.0=44100  0.5=48000  1.0=96000
#   p3 = bit_depth        0.0=16  0.5=24  1.0=32f
#   p4 = range_mode       0.0=full_timeline  1.0=custom
#   p5 = custom_start     0..1 normalised over 1..10000
#   p6 = custom_end       0..1 normalised over 1..10000
#   p7 = import_mode
#         mix:  0.0=mute+free_ch  0.5=keep_active  1.0=remove+free_ch
#         bake: 0.0=mute+free_ch  1.0=remove+replace_inplace
#
# Output path stored in rack.mixdown_output_path (StringProperty added to
# PB_RackSettings in Racks.py)
#
# Render runs in a bpy.app.timers background loop so Blender stays responsive.
# =============================================================================

import math
import os
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
# ---------------------------------------------------------------------------
# Shader singleton — gpu.shader.from_builtin() is expensive; reuse one instance.
# ---------------------------------------------------------------------------
_shader = None

def _get_shader():
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _shader



try:
    from ui.mixer.draw_utils import (
        draw_rect   as _draw_rect,
        draw_line   as _draw_line,
        draw_text   as _draw_text,
        text_width  as _text_width,
        draw_knob   as _draw_knob,
    )
except ImportError:
    pass

RACK_RAIL_H         = 32

# =============================================================================
# MIXDOWN LAYOUT TUNING — all values unscaled px, multiplied by scale at draw.
# Positive X = right, Negative X = left.
# Positive Y = up,    Negative Y = down.
# W/H values add to the computed size (negative = smaller).
# =============================================================================

# ── Column widths and X offsets ──────────────────────────────────────────────
MX_COL_A_W      = 175.0   # render mode column width
MX_COL_A_X      = -6.0    # shift col A left/right
MX_COL_C_W      = 200.0   # stats column width
MX_COL_C_X      = 0.0     # shift col C left/right

# ── Button shrink from centre (does not affect positions) ─────────────────────
MX_BTN_SCALE    = 0.9

# =============================================================================
# ALL ELEMENT POSITIONS — every value is fully independent.
# X = distance from left edge of col B (b_xi). Y = distance from body top downward.
# W = width in unscaled px. H = height in unscaled px.
# Change any value without affecting any other element.
# =============================================================================

# ── Col A — Render mode buttons ───────────────────────────────────────────────
MX_A_MIX_X  = 0.0;   MX_A_MIX_Y  = 55.0;  MX_A_MIX_W  = 155.0;  MX_A_MIX_H  = 24.0
MX_A_BAKE_X = 0.0;   MX_A_BAKE_Y = 82.0;  MX_A_BAKE_W = 155.0;  MX_A_BAKE_H = 24.0

# ── Col B — Output path box ───────────────────────────────────────────────────
MX_PATH_X   = -42.0;   MX_PATH_Y   = 53.0;  MX_PATH_W   = 730.0;  MX_PATH_H   = 22.0

# ── Col B — Save-as button (fully independent of path box) ───────────────────
MX_SAVEAS_X = 652.0; MX_SAVEAS_Y = 53.0;  MX_SAVEAS_W = 58.0;   MX_SAVEAS_H = 22.0

# ── Col B — Format buttons (WAV, FLAC) — each fully independent ──────────────
MX_WAV_X    = -9.0;   MX_WAV_Y    = 95.0;  MX_WAV_W    = 123.0;  MX_WAV_H    = 20.0
MX_FLAC_X   = 108.0; MX_FLAC_Y   = 95.0;  MX_FLAC_W   = 123.0;  MX_FLAC_H   = 20.0

# ── Col B — Sample rate buttons (44k, 48k, 96k) — each fully independent ─────
MX_SR44_X   = 230.0; MX_SR44_Y   = 95.0;  MX_SR44_W   = 83.0;   MX_SR44_H   = 20.0
MX_SR48_X   = 310.0; MX_SR48_Y   = 95.0;  MX_SR48_W   = 83.0;   MX_SR48_H   = 20.0
MX_SR96_X   = 390.0; MX_SR96_Y   = 95.0;  MX_SR96_W   = 83.0;   MX_SR96_H   = 20.0

# ── Col B — Bit depth buttons (16, 24, 32f) — each fully independent ─────────
MX_BD16_X   = 471.0; MX_BD16_Y   = 95.0;  MX_BD16_W   = 80.0;   MX_BD16_H   = 20.0
MX_BD24_X   = 552.0; MX_BD24_Y   = 95.0;  MX_BD24_W   = 80.0;   MX_BD24_H   = 20.0
MX_BD32_X   = 631.0; MX_BD32_Y   = 95.0;  MX_BD32_W   = 80.0;   MX_BD32_H   = 20.0

# ── Col B — Range buttons (full timeline, custom frames) — each fully independent
MX_FULL_X   = -12.0;   MX_FULL_Y   = 136.0; MX_FULL_W   = 170.0;  MX_FULL_H   = 20.0
MX_CUST_X   = 149.0; MX_CUST_Y   = 136.0; MX_CUST_W   = 172.0;  MX_CUST_H   = 20.0

# ── Col B — Range: start frame box ────────────────────────────────────────────
MX_FSTART_X = 312.5; MX_FSTART_Y = 136.0; MX_FSTART_W = 207.0;  MX_FSTART_H = 20.0

# ── Col B — Range: end frame box ──────────────────────────────────────────────
MX_FEND_X   = 510.0; MX_FEND_Y   = 136.0; MX_FEND_W   = 207.0;  MX_FEND_H   = 20.0

# ── Col B — "frames N–N (duration)" info text, sits below the start frame box ──
MX_FRAMEINFO_X = 50.0;  MX_FRAMEINFO_Y = -4.0   # offset relative to start frame box (negative Y = further below)

# ── Col B — Place on channel ──────────────────────────────────────────────────
MX_PLACE_X  = -24.0;   MX_PLACE_Y  = 189.0; MX_PLACE_W  = 390.0;  MX_PLACE_H  = 20.0

# ── Col B — After render ──────────────────────────────────────────────────────
MX_AFTER_X  = 335.0; MX_AFTER_Y  = 189.0; MX_AFTER_W  = 390.0;  MX_AFTER_H  = 20.0

# ── Col B — Place-on-channel / after-render stepper "−"/"+" hitboxes ──────────
# Each hitbox is X (unscaled px offset from the stepper row's left edge) + W
# (unscaled px width) — same shape as every other tuning pair in this file.
# Shared by both steppers (place-on-channel and after-render), since they're
# the same width and layout. Set MX_STEPPER_DEBUG = True below to see them.
MX_STEPPER_MINUS_X = 90.0;    MX_STEPPER_MINUS_W = 70   # "−" click zone
MX_STEPPER_PLUS_X  = 253.5;  MX_STEPPER_PLUS_W  = 70   # "+" click zone

# Draws green ("−") / red ("+") outlines over the stepper hitboxes above so
# you can see exactly what's clickable while tuning. Set False when done.
MX_STEPPER_DEBUG = False

# ── "On" button texture overlay — shared by every mode_btn() toggle ──────────
# Off-state chrome for every toggle button (mode, format, sample rate, bit
# depth, range) is baked into rack_mixdown_bg.png — mode_btn() no longer draws
# a rect/border at all. When a button is active, its section's "on" PNG blits
# on top, sized off that button's own rect (same pattern as rack_booster.py's
# preset grid). One shared scale/nudge applies to every section here; if one
# section's PNG needs its own tuning later, split these into per-section
# constants the same way BST_BTN_ON_* would if boosters ever needed it per-key.
MX_BTN_ON_SCALE   = 1.0    # width multiplier relative to the button rect
MX_BTN_ON_SCALE_H = 1.0    # height multiplier relative to the button rect
MX_BTN_ON_X       = 0.0    # nudge left/right (unscaled px)
MX_BTN_ON_Y       = 0.0    # nudge up/down   (unscaled px)

# ── Col B — Render button ─────────────────────────────────────────────────────
MX_RENDER_X = -44.0;   MX_RENDER_Y = 252.0; MX_RENDER_W = 790.0;  MX_RENDER_H = 26.0

# ── Col B — "Import back into VSE" label ──────────────────────────────────────
MX_IMPORT_LBL_X = 0.0; MX_IMPORT_LBL_Y = 160.0  # Y from body_top downward
MX_BLEND_LBL_X  = 0.0;  MX_BLEND_LBL_Y  = 0.0   # Y offset relative to place row (negative = below)
MX_HINT_X       = 20.0;    MX_HINT_Y       = -10.0   # Y offset relative to after render row (negative = below)

# ── Col C — "renders offline" hint text ──────────────────────────────────────
MX_OFFLINE_X = 0.0;  MX_OFFLINE_Y = 200.0  # Y from body_top downward (c_xi based)

# ── Col C — Stat cards ────────────────────────────────────────────────────────
MX_CARD_FMT_X  = -8.0;  MX_CARD_FMT_Y  = 60.0;  MX_CARD_FMT_W  = 195.0; MX_CARD_FMT_H  = 44.0
MX_CARD_DUR_X  = -8.0;  MX_CARD_DUR_Y  = 100.0;  MX_CARD_DUR_W  = 195.0; MX_CARD_DUR_H  = 34.0
MX_CARD_SIZE_X = -8.0;  MX_CARD_SIZE_Y = 138.0; MX_CARD_SIZE_W = 195.0; MX_CARD_SIZE_H = 34.0
MX_CARD_SEL_X  = -8.0;  MX_CARD_SEL_Y  = 176.0; MX_CARD_SEL_W  = 195.0; MX_CARD_SEL_H = 34.0
RACK_EXPANDED_H_MX  = 520    # tall enough for all controls

# ---------------------------------------------------------------------------
# Render state — one active render job at a time
# ---------------------------------------------------------------------------
_mx_state = {
    'running':    False,
    'progress':   0.0,
    'status_msg': 'ready',
    'rack_idx':   -1,
    'error':      '',
    # Frame box text input
    'text_focus':  None,   # None, 'p5', or 'p6'
    'text_buf':    '',     # current typed string
    'text_rack':   -1,     # rack_idx being edited
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sr_from_norm(n):
    if n < 0.25: return 44100
    if n < 0.75: return 48000
    return 96000

def _bd_from_norm(n):
    if n < 0.25: return 16
    if n < 0.75: return 24
    return 32   # float32

def _fmt_from_norm(n):
    return 'FLAC' if n > 0.5 else 'WAV'

def _frame_from_norm(n):
    return max(1, int(1 + n * 9999))

def _norm_from_frame(f):
    return max(0.0, min(1.0, (f - 1) / 9999.0))

def _get_timeline_frames(rack, scene):
    """Return (start_frame, end_frame) for the render."""
    if rack.p4 > 0.5:   # custom
        s = _frame_from_norm(rack.p5)
        e = _frame_from_norm(rack.p6)
        return min(s, e), max(s, e)
    return scene.frame_start, scene.frame_end

def _duration_str(frames, fps):
    secs = frames / fps
    m = int(secs) // 60
    s = int(secs) % 60
    return f"{m}:{s:02d}"

def _est_size_mb(frames, fps, sr, bd, n_ch):
    secs    = frames / fps
    bytes_s = sr * (32 if bd == 32 else bd // 8) * n_ch
    return max(1, int(secs * bytes_s / 1_048_576))

def _auto_filename(blend_path, fmt, ch_idx=None):
    """Generate default output filename from blend file."""
    if blend_path:
        base = os.path.splitext(os.path.basename(blend_path))[0]
    else:
        base = "untitled"
    ext = ".flac" if fmt == 'FLAC' else ".wav"
    if ch_idx is not None:
        return f"{base}_ch{ch_idx+1}_baked{ext}"
    return f"{base}_mixdown{ext}"

def _increment_blend_version(blend_path):
    """Save current blend as _v001.blend (or next available version)."""
    if not blend_path:
        return
    base, _ = os.path.splitext(blend_path)
    # Strip existing version suffix if present
    import re
    base = re.sub(r'_v\d+$', '', base)
    n = 1
    while True:
        candidate = f"{base}_v{n:03d}.blend"
        if not os.path.exists(candidate):
            break
        n += 1
    bpy.ops.wm.save_as_mainfile(filepath=candidate, copy=True)
    print(f"[MIXDOWN] saved version: {candidate}")


# ---------------------------------------------------------------------------
# Core render functions
# ---------------------------------------------------------------------------

def _render_channel_to_numpy(channel_idx, start_frame, end_frame, scene,
                              engine_mod, target_sr=44100):
    """
    Render one VSE channel offline to a numpy float32 array.
    Returns (numpy_array, target_sr) or (None, 0) on failure.

    Decodes each strip individually and applies its own strip.volume,
    then assembles them with correct silence gaps. This correctly handles
    channels where different strips have different volumes.
    """
    import numpy as _np

    fps         = scene.render.fps / scene.render.fps_base
    tl_start_s  = scene.frame_start / fps
    tl_end_s    = scene.frame_end   / fps
    duration_s  = tl_end_s - tl_start_s

    if duration_s < 0.01:
        return None, 0

    try:
        import aud as _aud

        seq_start_s = (scene.frame_preview_start if scene.use_preview_range
                       else scene.frame_start) / fps
        seq_end_s   = (scene.frame_preview_end if scene.use_preview_range
                       else scene.frame_end) / fps

        strips = sorted(
            [s for s in scene.sequence_editor.sequences_all
             if s.type == 'SOUND' and s.sound
             and (s.channel - 1) == channel_idx],
            key=lambda s: s.frame_final_end - s.frame_final_duration
        )
        if not strips:
            return None, 0

        total_frames = max(1, int(round(duration_s * target_sr)))
        out_buf = _np.zeros((total_frames, 2), dtype=_np.float32)

        for strip in strips:
            actual_start_frame = strip.frame_final_end - strip.frame_final_duration
            vis_start_s = max(actual_start_frame / fps, seq_start_s)
            vis_end_s   = min(strip.frame_final_end / fps, seq_end_s)

            if vis_end_s <= tl_start_s: continue
            if vis_start_s >= tl_end_s: continue

            play_start_s = max(vis_start_s, tl_start_s)
            play_end_s   = min(vis_end_s, tl_end_s)
            strip_dur    = play_end_s - play_start_s
            if strip_dur < 0.001: continue

            file_offset_start_s = getattr(strip, 'frame_offset_start', 0.0) / fps
            file_pos_start = file_offset_start_s + (play_start_s - vis_start_s)
            file_pos_end   = file_pos_start + strip_dur
            if file_pos_start < 0.0: file_pos_start = 0.0
            if file_pos_end <= file_pos_start: continue

            try:
                raw_sound = _aud.Sound.file(bpy.path.abspath(strip.sound.filepath))
                chunk     = raw_sound.limit(file_pos_start, file_pos_end)
                raw       = chunk.data()
                spec      = chunk.specs
                src_sr    = int(spec[0])
                src_nch   = max(1, int(spec[1]))

                n = len(raw) // 4
                n = (n // src_nch) * src_nch
                if n == 0: continue

                s = _np.frombuffer(raw[:n*4], dtype=_np.float32).copy()
                s = s.reshape(-1, src_nch)
                if src_nch == 1:
                    s = _np.column_stack([s, s])

                # Apply this strip's own volume
                sv = float(strip.volume)
                if abs(sv - 1.0) > 0.0001:
                    s = (s * sv).astype(_np.float32)

                # Resample to target_sr if needed
                if src_sr != target_sr and src_sr > 0:
                    n_out = max(1, int(round(len(s) * target_sr / src_sr)))
                    idx   = _np.linspace(0, len(s) - 1, n_out)
                    lo    = _np.floor(idx).astype(_np.int32)
                    hi    = _np.minimum(lo + 1, len(s) - 1)
                    frac  = (idx - lo)[:, None]
                    s     = (s[lo] * (1.0 - frac) + s[hi] * frac
                             ).astype(_np.float32)

                # Write into output buffer at correct timeline position
                out_start = int(round((play_start_s - tl_start_s) * target_sr))
                out_end   = min(out_start + len(s), total_frames)
                copy_len  = out_end - out_start
                if copy_len > 0:
                    out_buf[out_start:out_end] += s[:copy_len]

                print(f"[MIXDOWN] ch{channel_idx+1} strip '{strip.name}' "
                      f"vol={sv:.3f}  {strip_dur:.2f}s → "
                      f"out[{out_start}:{out_end}]")

            except Exception as e:
                print(f"[MIXDOWN] ch{channel_idx+1} strip '{strip.name}' "
                      f"decode error: {e}")
                continue

        if _np.max(_np.abs(out_buf)) < 1e-9:
            return None, 0

        # Apply the same soft-limiter the engine uses on its output mix
        pos_over = out_buf >  0.95
        neg_over = out_buf < -0.95
        if _np.any(pos_over):
            over = out_buf[pos_over] - 0.95
            out_buf[pos_over] = 0.95 + over / (1.0 + over)
        if _np.any(neg_over):
            over = -out_buf[neg_over] - 0.95
            out_buf[neg_over] = -(0.95 + over / (1.0 + over))

        print(f"[MIXDOWN] ch{channel_idx+1} ready: "
              f"{len(out_buf)} frames @ {target_sr}Hz "
              f"({len(out_buf)/target_sr:.2f}s)")

        return out_buf, target_sr

    except Exception as e:
        print(f"[MIXDOWN] ch{channel_idx+1} render error: {e}")
        import traceback; traceback.print_exc()
        return None, 0


def _write_wav(filepath, audio_np, sr, bit_depth):
    """Write numpy float32 stereo array to WAV at given bit depth."""
    import wave as _wave
    import struct as _struct
    import numpy as _np

    n_frames, n_ch = audio_np.shape
    audio_np = _np.clip(audio_np, -1.0, 1.0)

    with _wave.open(filepath, 'w') as wf:
        wf.setnchannels(n_ch)
        wf.setframerate(sr)
        if bit_depth == 32:
            wf.setsampwidth(4)
            raw = _struct.pack(f'{n_frames * n_ch}f',
                               *audio_np.flatten().tolist())
        elif bit_depth == 24:
            wf.setsampwidth(3)
            # Vectorised — ~1000x faster than a per-sample Python loop.
            # Pack as little-endian int32 then keep only the 3 low bytes of each.
            samples = _np.clip(audio_np.flatten() * 8388607.0,
                               -8388608, 8388607).astype('<i4')
            raw = samples.view(_np.uint8).reshape(-1, 4)[:, :3].tobytes()
        else:  # 16-bit
            wf.setsampwidth(2)
            samples = (audio_np.flatten() * 32767.0).astype(_np.int16)
            raw = samples.tobytes()
        wf.writeframes(raw)


def _write_flac(filepath, audio_np, sr, bit_depth):
    """Write to FLAC using soundfile if available, else fall back to WAV."""
    try:
        import soundfile as sf
        import numpy as _np
        subtype_map = {16: 'PCM_16', 24: 'PCM_24', 32: 'FLOAT'}
        sf.write(filepath, audio_np,
                 samplerate=sr,
                 subtype=subtype_map.get(bit_depth, 'PCM_24'))
    except ImportError:
        # soundfile not available — write WAV instead and warn
        wav_path = os.path.splitext(filepath)[0] + '.wav'
        print(f"[MIXDOWN] soundfile not available — writing WAV to {wav_path}")
        _write_wav(wav_path, audio_np, sr, bit_depth)
        return wav_path
    return filepath


# ---------------------------------------------------------------------------
# Background render timer
# ---------------------------------------------------------------------------
_mx_job = {}   # job state persists across timer calls


def _start_render(rack_idx, rack, scene):
    global _mx_state, _mx_job

    if _mx_state['running']:
        print("[MIXDOWN] render already in progress")
        return

    import numpy as _np

    fps        = scene.render.fps / scene.render.fps_base
    fmt        = _fmt_from_norm(rack.p1)
    sr         = _sr_from_norm(rack.p2)
    bd         = _bd_from_norm(rack.p3)
    is_bake    = rack.p0 > 0.5
    # hijacker_engine WAV reader only supports 16-bit PCM.
    # Clamp WAV renders to 16-bit so the imported strip plays back immediately.
    # FLAC is written via soundfile so all bit depths work there.
    if fmt == 'WAV' and bd != 16:
        print(f"[MIXDOWN] clamping bit depth {bd}-bit → 16-bit for WAV "
              f"(hijacker_engine requires 16-bit PCM)")
        bd = 16
    f_start, f_end = _get_timeline_frames(rack, scene)

    # Collect channels to render
    from Racks import get_rack_channels
    assigned = list(get_rack_channels(rack))
    if not assigned:
        _mx_state['error'] = 'no channels assigned'
        return

    out_path = getattr(rack, 'mixdown_output_path', '')
    blend_path = bpy.data.filepath

    if not out_path:
        # Auto-generate path next to blend file
        blend_dir = os.path.dirname(blend_path) if blend_path else bpy.app.tempdir
        if is_bake:
            out_path = blend_dir
        else:
            out_path = os.path.join(blend_dir,
                                    _auto_filename(blend_path, fmt))

    _mx_state.update({
        'running':    True,
        'progress':   0.0,
        'status_msg': 'starting…',
        'rack_idx':   rack_idx,
        'error':      '',
    })

    _mx_job.update({
        'rack_idx':   rack_idx,
        'channels':   assigned,
        'ch_cursor':  0,
        'fmt':        fmt,
        'sr':         sr,
        'bd':         bd,
        'is_bake':    is_bake,
        'f_start':    f_start,
        'f_end':      f_end,
        'fps':        fps,
        'out_path':   out_path,
        'blend_path': blend_path,
        'mix_buffer': None,   # accumulates summed audio for mix mode
        'baked_files': [],    # [(ch_idx, filepath), …]
        'import_mode': rack.p7,
        'blend_saved': False,
    })

    if not bpy.app.timers.is_registered(_render_tick):
        bpy.app.timers.register(_render_tick, first_interval=0.05)

    print(f"[MIXDOWN] render started — {len(assigned)} ch, "
          f"frames {f_start}–{f_end}, mode={'bake' if is_bake else 'mix'}")


def _render_tick():
    """Timer callback — renders one channel per tick to keep Blender responsive."""
    global _mx_state, _mx_job

    if not _mx_state['running']:
        return None   # unregister

    try:
        import numpy as _np

        job     = _mx_job
        chs     = job['channels']
        cursor  = job['ch_cursor']
        total   = len(chs)

        if cursor >= total:
            # All channels processed — finalise
            _finalise_render()
            return None

        ch      = chs[cursor]
        scene   = bpy.context.scene

        _mx_state['status_msg'] = f"rendering ch{ch+1}… ({cursor+1}/{total})"
        _mx_state['progress']   = cursor / total

        # Get engine module
        try:
            from core.engine import get_engine as _get_eng
            eng_mod = _get_eng()
        except Exception:
            eng_mod = None

        audio, sr = _render_channel_to_numpy(
            ch, job['f_start'], job['f_end'], scene, eng_mod,
            target_sr=job['sr'])

        if audio is not None and len(audio) > 0:
            if job['is_bake']:
                # Write immediately per channel — use sr returned from render
                # (equals target_sr after resampling)
                fname = _auto_filename(job['blend_path'], job['fmt'], ch)
                if os.path.isdir(job['out_path']):
                    fpath = os.path.join(job['out_path'], fname)
                else:
                    fpath = job['out_path']

                if job['fmt'] == 'FLAC':
                    fpath = _write_flac(fpath, audio, sr, job['bd'])
                else:
                    _write_wav(fpath, audio, sr, job['bd'])

                job['baked_files'].append((ch, fpath))
                print(f"[MIXDOWN] baked ch{ch+1} → {fpath}")
            else:
                # Accumulate into mix buffer
                if job['mix_buffer'] is None:
                    job['mix_buffer'] = audio.copy()
                else:
                    # Pad shorter buffer
                    a, b = job['mix_buffer'], audio
                    if len(a) < len(b):
                        a = _np.pad(a, ((0, len(b)-len(a)), (0,0)))
                    elif len(b) < len(a):
                        b = _np.pad(b, ((0, len(a)-len(b)), (0,0)))
                    job['mix_buffer'] = _np.clip(a + b, -1.0, 1.0)

        job['ch_cursor'] += 1

        # Force HUD redraw so progress bar updates
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'NODE_EDITOR':
                    area.tag_redraw()

        return 0.05   # next tick in 50ms

    except Exception as e:
        import traceback
        _mx_state.update({
            'running': False,
            'error':   str(e),
            'status_msg': f'error: {e}',
        })
        print(f"[MIXDOWN] render_tick error: {e}")
        traceback.print_exc()
        return None


def _finalise_render():
    """Called when all channels have been processed — write file and import."""
    global _mx_state, _mx_job
    import numpy as _np

    job = _mx_job

    try:
        scene = bpy.context.scene
        fps   = job['fps']

        # --- Step 1: Save blend version BEFORE touching the timeline ---
        if not job['blend_saved'] and bpy.data.filepath:
            _increment_blend_version(bpy.data.filepath)
            job['blend_saved'] = True

        # --- Step 2: Write mix file (mix mode only) ---
        if not job['is_bake']:
            mix = job.get('mix_buffer')
            if mix is None or len(mix) == 0:
                raise RuntimeError("mix buffer empty — no audio rendered")

            out = job['out_path']
            if job['fmt'] == 'FLAC':
                out = _write_flac(out, mix, job['sr'], job['bd'])
            else:
                _write_wav(out, mix, job['sr'], job['bd'])
            print(f"[MIXDOWN] wrote mix → {out}")
            job['baked_files'] = [(None, out)]

        # --- Step 3: Import back into VSE ---
        seq = scene.sequence_editor
        if seq is None:
            seq = scene.sequence_editor_create()

        import_mode = job['import_mode']
        channels    = job['channels']

        if job['is_bake']:
            # Bake mode — replace/add per channel
            replace_inplace = import_mode > 0.5

            for ch_idx, fpath in job['baked_files']:
                if not os.path.exists(fpath):
                    continue

                # Find original strips on this channel
                orig_strips = [
                    s for s in list(seq.sequences_all)
                    if s.type == 'SOUND' and (s.channel - 1) == ch_idx
                ]

                if replace_inplace and orig_strips:
                    # Place on same channel, same start position
                    place_ch    = ch_idx + 1
                    place_frame = min(s.frame_final_end - s.frame_final_duration
                                     for s in orig_strips)
                    # Remove originals
                    for s in orig_strips:
                        seq.sequences.remove(s)
                else:
                    # Find next free channel above this one
                    used = {s.channel for s in seq.sequences_all}
                    place_ch = ch_idx + 1
                    while place_ch in used:
                        place_ch += 1
                    place_frame = job['f_start']
                    # Mute originals
                    for s in orig_strips:
                        s.mute = True

                seq.sequences.new_sound(
                    name=f"ch{ch_idx+1}_baked",
                    filepath=fpath,
                    channel=place_ch,
                    frame_start=place_frame,
                )
                print(f"[MIXDOWN] imported ch{ch_idx+1} bake → VSE ch{place_ch}")

        else:
            # Mix mode — single file import
            _, out = job['baked_files'][0]

            # Determine target channel
            used = {s.channel for s in seq.sequences_all}
            place_ch = max(used, default=0) + 1  # default: above all

            # Handle source channels
            mode   = import_mode
            tracks = getattr(scene, "pb_sync_tracks", [])
            for ch_idx in channels:
                orig = [s for s in list(seq.sequences_all)
                        if s.type == 'SOUND' and (s.channel - 1) == ch_idx]
                if mode < 0.25:      # mute + place on free channel
                    for s in orig: s.mute = True
                    # Also mute the mixer track so the fader desk shows it muted
                    if ch_idx < len(tracks):
                        tracks[ch_idx].mute = True
                    try:
                        from core.engine import get_engine as _ge
                        eng = _ge()
                        if eng: eng.set_mute(ch_idx, True)
                    except Exception: pass
                elif mode > 0.75:    # remove + place on free channel
                    for s in orig: seq.sequences.remove(s)

            seq.sequences.new_sound(
                name="mixdown",
                filepath=out,
                channel=place_ch,
                frame_start=job['f_start'],
            )
            print(f"[MIXDOWN] imported mixdown → VSE ch{place_ch}")

        _mx_state.update({
            'running':    False,
            'progress':   1.0,
            'status_msg': 'done ✓',
            'error':      '',
        })

    except Exception as e:
        import traceback
        _mx_state.update({
            'running': False,
            'error':   str(e),
            'status_msg': f'error: {e}',
        })
        print(f"[MIXDOWN] finalise error: {e}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Draw
# ---------------------------------------------------------------------------

def _draw_mixdown_body(rx, ry, rw, rh, rack, rack_idx, scale):
    """
    4-column layout matching the rest of the rack suite:

      Col A  0..160px    Render mode (mix / bake) — narrow left strip
      Col B  160..760px  Settings: path, format, SR, BD, range, import, render btn
      Col C  760..1100px Stats: format badge + duration/size/count cards + progress
      Col D  1100..1200px Channel buttons — drawn by rack_base, we leave this clear
    """
    ui     = scale
    rail_h = RACK_RAIL_H * ui
    body_h = rh - rail_h

    # ── Skin background — full rack height (rail/title baked into PNG)
    try:
        from ui.mixer.texture_cache import get_texture as _gtc_mx
        from ui.mixer.texture_cache import blit_texture as _blt_mx
        _mx_tex = _gtc_mx("rack_mixdown_bg")
        if _mx_tex:
            _blt_mx(_mx_tex, rx, ry, rw, rh, key="rack_mixdown_bg")
        else:
            _draw_rect(rx, ry, rw, body_h, (0.04, 0.04, 0.04, 1.0))
    except Exception:
        _draw_rect(rx, ry, rw, body_h, (0.04, 0.04, 0.04, 1.0))

    shader = _get_shader()

    # ── Column boundaries ────────────────────────────────────────────────
    A_W  = MX_COL_A_W * ui
    CH_W = 100 * ui
    C_W  = MX_COL_C_W * ui
    A_X  = rx + MX_COL_A_X * ui
    B_X  = A_X + A_W
    C_X  = rx + rw - CH_W - C_W + MX_COL_C_X * ui
    D_X  = rx + rw - CH_W
    B_W  = C_X - B_X

    pad   = 10 * ui
    # inner x/w for each column's content
    a_xi  = A_X + pad;  a_wi = A_W - 2*pad
    b_xi  = B_X + pad;  b_wi = B_W - 2*pad
    c_xi  = C_X + pad;  c_wi = C_W - 2*pad

    body_top = ry + body_h
    body_bot = ry

    # ── Read settings ────────────────────────────────────────────────────
    is_bake     = rack.p0 > 0.5
    fmt         = _fmt_from_norm(rack.p1)
    sr          = _sr_from_norm(rack.p2)
    bd          = _bd_from_norm(rack.p3)
    is_custom   = rack.p4 > 0.5
    import_mode = rack.p7

    scene = bpy.context.scene
    fps   = (scene.render.fps / scene.render.fps_base) if scene else 24.0
    f_start, f_end = _get_timeline_frames(rack, scene) if scene else (1, 250)
    duration_frames = max(1, f_end - f_start)
    dur_str = _duration_str(duration_frames, fps)

    try:
        from Racks import get_rack_channels
        assigned = list(get_rack_channels(rack))
    except Exception:
        assigned = []

    n_sel   = len(assigned)
    est_mb  = _est_size_mb(duration_frames, fps, sr, bd,
                           max(1, n_sel) if is_bake else 2)
    out_path = getattr(rack, 'mixdown_output_path', '')

    running   = _mx_state['running'] and _mx_state['rack_idx'] == rack_idx
    done      = (not running
                 and _mx_state.get('status_msg', '') == 'done ✓'
                 and _mx_state['rack_idx'] == rack_idx)
    progress  = _mx_state['progress'] if running else (1.0 if done else 0.0)
    status    = _mx_state['status_msg']
    has_error = 'error' in status.lower()

    # ── Font sizes ───────────────────────────────────────────────────────
    fs8  = max(1, int(8  * ui))
    fs9  = max(1, int(9  * ui))
    fs10 = max(1, int(10 * ui))
    fs11 = max(1, int(11 * ui))
    fs12 = max(1, int(12 * ui))

    # ── Shared helpers ───────────────────────────────────────────────────
    def border(x, y, w, h, col):
        verts = [(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)]
        b = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
        shader.bind(); shader.uniform_float("color", col); b.draw(shader)

    def _shrink(x, y, w, h):
        """Shrink a rect inward from its centre by MX_BTN_SCALE. Position unchanged."""
        if MX_BTN_SCALE >= 1.0:
            return x, y, w, h
        dw = w * (1.0 - MX_BTN_SCALE) / 2
        dh = h * (1.0 - MX_BTN_SCALE) / 2
        return x + dw, y + dh, w - dw*2, h - dh*2

    def divider(x):
        verts = [(x, body_bot + 4*ui), (x, body_top - 4*ui)]
        b = batch_for_shader(shader, "LINES", {"pos": verts})
        shader.bind(); shader.uniform_float("color", (0.18, 0.20, 0.23, 1.0))
        b.draw(shader)

    def slabel(txt, x, y, col=(0.42, 0.45, 0.55, 1.0)):
        _draw_text(txt.upper(), x, y, fs8, col)

    def two_btn(x, y, w, h, is_right, lbl0, lbl1, col_on=(0.0,0.6,0.4,1.0)):
        bw = w / 2 - ui
        for i, lbl in enumerate([lbl0, lbl1]):
            bx  = x + i * (bw + 2*ui)
            sel = (i == 1) if is_right else (i == 0)
            bg  = (0.05, 0.22, 0.15, 1.0) if sel else (0.08, 0.09, 0.11, 1.0)
            bc  = col_on                   if sel else (0.22, 0.22, 0.25, 1.0)
            tc  = (0.2, 0.9, 0.6, 1.0)    if sel else (0.38, 0.38, 0.44, 1.0)
            sx, sy, sw, sh = _shrink(bx, y, bw, h)
            _draw_rect(sx, sy, sw, sh, bg); border(sx, sy, sw, sh, bc)
            tw = _text_width(lbl, fs9)
            _draw_text(lbl, bx + bw/2 - tw/2, y + h/2 - fs9/2, fs9, tc)

    def three_btn(x, y, w, h, val, labels, col_on=(0.3,0.7,0.5,1.0)):
        bw = w / 3 - ui
        for i, lbl in enumerate(labels):
            bx  = x + i * (bw + 1.5*ui)
            sel = abs(val - [0.0, 0.5, 1.0][i]) < 0.2
            bg  = (0.05, 0.12, 0.22, 1.0) if sel else (0.08, 0.09, 0.11, 1.0)
            bc  = col_on                   if sel else (0.22, 0.22, 0.25, 1.0)
            tc  = col_on                   if sel else (0.35, 0.35, 0.40, 1.0)
            sx, sy, sw, sh = _shrink(bx, y, bw, h)
            _draw_rect(sx, sy, sw, sh, bg); border(sx, sy, sw, sh, bc)
            tw = _text_width(lbl, fs9)
            _draw_text(lbl, bx + bw/2 - tw/2, y + h/2 - fs9/2, fs9, tc)

    def mode_btn(x, y, w, h, lbl, active, col=(0.0, 0.55, 0.85, 1.0), on_key=None):
        """Off-state chrome is baked into rack_mixdown_bg.png — no rect/border
        drawn here at all. When active and on_key names a loaded skin texture,
        blit that section's 'on' PNG over the button rect (scaled/nudged by
        MX_BTN_ON_*), then draw the label on top either way."""
        tc = col if active else (0.35, 0.38, 0.44, 1.0)
        if active and on_key:
            try:
                from ui.mixer.texture_cache import get_texture as _gtc_mxb
                from ui.mixer.texture_cache import blit_texture as _blt_mxb
                _mxb_tex = _gtc_mxb(on_key)
                if _mxb_tex:
                    _bw = w * MX_BTN_ON_SCALE
                    _bh = h * MX_BTN_ON_SCALE_H
                    _bx = x + (w - _bw) / 2 + MX_BTN_ON_X * ui
                    _by = y + (h - _bh) / 2 + MX_BTN_ON_Y * ui
                    _blt_mxb(_mxb_tex, _bx, _by, _bw, _bh, key=on_key)
            except Exception:
                pass
        tw = _text_width(lbl, fs9)
        _draw_text(lbl, x + w/2 - tw/2, y + h/2 - fs9/2, fs9, tc)

    def info_row(x, y, w, h, ltxt, rtxt,
                 lc=(0.35,0.50,0.40,0.8), rc=(0.2,0.85,0.6,1.0),
                 dim=False):
        """Box chrome baked into background PNG — text only."""
        if dim:
            lc = (0.22, 0.28, 0.25, 0.7); rc = (0.25, 0.32, 0.28, 0.7)
        sx, sy, sw, sh = _shrink(x, y, w, h)
        _draw_text(ltxt, sx + 5*ui, sy + sh/2 - fs9/2, fs9, lc)
        tw = _text_width(rtxt, fs10)
        _draw_text(rtxt, sx + sw - tw - 7*ui, sy + sh/2 - fs10/2, fs10, rc)

    def stepper_row(x, y, w, h, ltxt, val_txt,
                    lc=(0.35,0.50,0.40,0.8), vc=(0.2,0.85,0.6,1.0)):
        """Value centred, − / + centred inside their own configured hitbox
        zones (MX_STEPPER_MINUS_X/W, MX_STEPPER_PLUS_X/W) — same source of
        truth the Racks.py hit test uses, so the glyphs always sit exactly
        on top of what's clickable. x/w here are the raw row rect (unshrunk),
        matching how the hit test measures MX_STEPPER_* offsets.
        Box chrome baked into background PNG — text only."""
        sx, sy, sw, sh = _shrink(x, y, w, h)
        # Label on left
        _draw_text(ltxt, sx + 5*ui, sy + sh/2 - fs9/2, fs9, lc)
        arr_col = (0.4, 0.6, 0.5, 0.9)
        minus_cx = x + (MX_STEPPER_MINUS_X + MX_STEPPER_MINUS_W/2) * ui
        plus_cx  = x + (MX_STEPPER_PLUS_X  + MX_STEPPER_PLUS_W/2)  * ui
        mw = _text_width("−", fs10)
        pw = _text_width("+", fs10)
        _draw_text("−", minus_cx - mw/2,
                   sy + sh/2 - fs10/2, fs10, arr_col)
        vw = _text_width(val_txt, fs10)
        _draw_text(val_txt, sx + sw/2 - vw/2,
                   sy + sh/2 - fs10/2, fs10, vc)
        _draw_text("+", plus_cx - pw/2,
                   sy + sh/2 - fs10/2, fs10, arr_col)

    def _stepper_debug(x, y, w, h):
        """Outline the − (green) / + (red) hitboxes exactly as Racks.py hit-tests
        them — i.e. against the raw row rect (x, w), not the shrunk visual box.
        Only draws when MX_STEPPER_DEBUG = True."""
        if not MX_STEPPER_DEBUG:
            return
        border(x + MX_STEPPER_MINUS_X*ui, y, MX_STEPPER_MINUS_W*ui, h,
               (0.0, 1.0, 0.0, 1.0))
        border(x + MX_STEPPER_PLUS_X*ui,  y, MX_STEPPER_PLUS_W*ui,  h,
               (1.0, 0.0, 0.0, 1.0))

    # Draw column dividers
    divider(A_X + A_W)
    divider(C_X)

    # ════════════════════════════════════════════════════════════════════
    # COL A — Render mode
    # ════════════════════════════════════════════════════════════════════
    a_y = body_top - pad
    def a_nxt(h):
        nonlocal a_y; a_y -= h; return a_y

    # Col A — absolute from a_xi / body_top
    # "render mode" label suppressed — baked into background PNG
    mode_btn(a_xi + MX_A_MIX_X*ui,  body_top - MX_A_MIX_Y*ui,
             MX_A_MIX_W*ui, MX_A_MIX_H*ui,
             "mix to single file", not is_bake, on_key="rack_mixdown_mode_btn_on")
    mode_btn(a_xi + MX_A_BAKE_X*ui, body_top - MX_A_BAKE_Y*ui,
             MX_A_BAKE_W*ui, MX_A_BAKE_H*ui,
             "bake per channel", is_bake, on_key="rack_mixdown_mode_btn_on")

    # ════════════════════════════════════════════════════════════════════
    # COL B — every element positioned absolutely from b_xi / body_top
    # ════════════════════════════════════════════════════════════════════

    # "output file/folder" label suppressed — baked into background PNG

    # Path box — chrome baked into background PNG, text only
    _path_x = b_xi + MX_PATH_X*ui
    _path_y = body_top - MX_PATH_Y*ui
    _path_w = MX_PATH_W*ui
    _path_h = MX_PATH_H*ui
    _px, _py, _pw, _ph = _shrink(_path_x, _path_y, _path_w, _path_h)
    disp = (out_path if out_path
            else ("(click 'save as…' to set path)" if not is_bake
                  else "(click 'choose folder…')"))
    _draw_text(disp, _px + 5*ui, _py + _ph/2 - fs9/2, fs9,
               (0.72, 0.75, 0.82, 1.0) if out_path else (0.42, 0.45, 0.52, 1.0))

    # Save-as button — fully suppressed (box + label baked into background PNG).
    # Racks.py's click zone for this ('mixdown_browse') is computed independently
    # from its own layout math, not from MX_SAVEAS_*, so click handling is unaffected.

    # "format", "sample rate", "bit depth" labels suppressed — baked into background PNG

    # WAV button
    _is_wav = not (rack.p1 > 0.5)
    mode_btn(b_xi + MX_WAV_X*ui,  body_top - MX_WAV_Y*ui,  MX_WAV_W*ui,  MX_WAV_H*ui,
             "WAV",  _is_wav,  col=(0.2, 0.6, 0.9, 1.0), on_key="rack_mixdown_format_btn_on")
    # FLAC button
    mode_btn(b_xi + MX_FLAC_X*ui, body_top - MX_FLAC_Y*ui, MX_FLAC_W*ui, MX_FLAC_H*ui,
             "FLAC", not _is_wav, col=(0.2, 0.6, 0.9, 1.0), on_key="rack_mixdown_format_btn_on")

    # 44k / 48k / 96k buttons
    _sr_vals = [0.0, 0.5, 1.0]
    for _lbl, _xc, _yc, _wc, _hc, _vi in [
        ("44k",  MX_SR44_X, MX_SR44_Y, MX_SR44_W, MX_SR44_H, 0),
        ("48k",  MX_SR48_X, MX_SR48_Y, MX_SR48_W, MX_SR48_H, 1),
        ("96k",  MX_SR96_X, MX_SR96_Y, MX_SR96_W, MX_SR96_H, 2),
    ]:
        mode_btn(b_xi + _xc*ui, body_top - _yc*ui, _wc*ui, _hc*ui,
                 _lbl, abs(rack.p2 - _sr_vals[_vi]) < 0.2, col=(0.3, 0.7, 0.5, 1.0),
                 on_key="rack_mixdown_sr_btn_on")

    # 16 / 24 / 32f buttons
    _bd_vals = [0.0, 0.5, 1.0]
    for _lbl, _xc, _yc, _wc, _hc, _vi in [
        ("16",   MX_BD16_X, MX_BD16_Y, MX_BD16_W, MX_BD16_H, 0),
        ("24",   MX_BD24_X, MX_BD24_Y, MX_BD24_W, MX_BD24_H, 1),
        ("32f",  MX_BD32_X, MX_BD32_Y, MX_BD32_W, MX_BD32_H, 2),
    ]:
        mode_btn(b_xi + _xc*ui, body_top - _yc*ui, _wc*ui, _hc*ui,
                 _lbl, abs(rack.p3 - _bd_vals[_vi]) < 0.2, col=(0.6, 0.5, 0.8, 1.0),
                 on_key="rack_mixdown_bd_btn_on")

    # "range" label suppressed — baked into background PNG

    # Full timeline button
    mode_btn(b_xi + MX_FULL_X*ui, body_top - MX_FULL_Y*ui, MX_FULL_W*ui, MX_FULL_H*ui,
             "full timeline", not is_custom, col=(0.7, 0.5, 0.2, 1.0),
             on_key="rack_mixdown_range_btn_on")
    # Custom frames button
    mode_btn(b_xi + MX_CUST_X*ui, body_top - MX_CUST_Y*ui, MX_CUST_W*ui, MX_CUST_H*ui,
             "custom frames", is_custom,     col=(0.7, 0.5, 0.2, 1.0),
             on_key="rack_mixdown_range_btn_on")

    # Start frame box — text input style
    _fsx = b_xi + MX_FSTART_X*ui;  _fsy = body_top - MX_FSTART_Y*ui
    _fsw = MX_FSTART_W*ui;          _fsh = MX_FSTART_H*ui
    try:
        from ui.mixer.interaction import _active_text_field as _atf
        _fs_focused = (is_custom and _atf is not None
                       and _atf.get('mx_frame') and _atf.get('param') == 'p5'
                       and _atf.get('rack_idx') == rack_idx)
        _fs_text = _atf['text'] if _fs_focused else None
    except Exception:
        _fs_focused = False; _fs_text = None
    _lc  = (0.45, 0.50, 0.60, 0.8) if is_custom else (0.25, 0.28, 0.32, 0.6)
    _vc  = (0.9, 0.92, 1.0, 1.0)   if _fs_focused else \
           (0.8, 0.85, 0.95, 1.0)  if is_custom   else (0.35, 0.38, 0.42, 0.6)
    _sx2, _sy2, _sw2, _sh2 = _shrink(_fsx, _fsy, _fsw, _fsh)
    # Box chrome baked into background PNG — only draw a highlight border while
    # actively editing, since that state can't be baked into a static image.
    if _fs_focused:
        border(_sx2, _sy2, _sw2, _sh2, (0.3, 0.55, 0.9, 1.0))
    _draw_text("start", _sx2 + 5*ui, _sy2 + _sh2/2 - fs9/2, fs9, _lc)
    _disp_s  = (_fs_text if _fs_focused else str(f_start))
    _cursor_s = ("|" if _fs_focused and int(__import__('time').time() * 2) % 2 == 0 else "")
    _svw = _text_width(_disp_s + _cursor_s, fs10)
    _draw_text(_disp_s + _cursor_s, _sx2 + _sw2 - _svw - 6*ui,
               _sy2 + _sh2/2 - fs10/2, fs10, _vc)

    # End frame box — text input style
    _fex = b_xi + MX_FEND_X*ui;  _fey = body_top - MX_FEND_Y*ui
    _few = MX_FEND_W*ui;          _feh = MX_FEND_H*ui
    try:
        from ui.mixer.interaction import _active_text_field as _atf2
        _fe_focused = (is_custom and _atf2 is not None
                       and _atf2.get('mx_frame') and _atf2.get('param') == 'p6'
                       and _atf2.get('rack_idx') == rack_idx)
        _fe_text = _atf2['text'] if _fe_focused else None
    except Exception:
        _fe_focused = False; _fe_text = None
    _vc2  = (0.9, 0.92, 1.0, 1.0)  if _fe_focused else \
            (0.8, 0.85, 0.95, 1.0) if is_custom   else (0.35, 0.38, 0.42, 0.6)
    _sx3, _sy3, _sw3, _sh3 = _shrink(_fex, _fey, _few, _feh)
    if _fe_focused:
        border(_sx3, _sy3, _sw3, _sh3, (0.3, 0.55, 0.9, 1.0))
    _draw_text("end", _sx3 + 5*ui, _sy3 + _sh3/2 - fs9/2, fs9, _lc)
    _disp_e  = (_fe_text if _fe_focused else str(f_end))
    _cursor_e = ("|" if _fe_focused and int(__import__('time').time() * 2) % 2 == 0 else "")
    _evw = _text_width(_disp_e + _cursor_e, fs10)
    _draw_text(_disp_e + _cursor_e, _sx3 + _sw3 - _evw - 6*ui,
               _sy3 + _sh3/2 - fs10/2, fs10, _vc2)

    # Frame info text
    _draw_text(f"frames {f_start}–{f_end}  ({dur_str})",
               b_xi + MX_FSTART_X*ui + MX_FRAMEINFO_X*ui,
               body_top - MX_FSTART_Y*ui - MX_FSTART_H*ui + MX_FRAMEINFO_Y*ui,
               fs8, (0.40, 0.50, 0.40, 0.9))

    # Import back into VSE label
    slabel("import back into VSE",
           b_xi + MX_IMPORT_LBL_X*ui,
           body_top - MX_IMPORT_LBL_Y*ui,
           (0.20, 0.60, 0.40, 1.0))

    # Place on channel — fully independent
    _place_x = b_xi + MX_PLACE_X*ui;  _place_y = body_top - MX_PLACE_Y*ui
    _place_w = MX_PLACE_W*ui;          _place_h = MX_PLACE_H*ui
    if is_bake:
        bake_mode_lbl = "replace in-place" if import_mode > 0.5 else "free channels"
        stepper_row(_place_x, _place_y, _place_w, _place_h, "place on", bake_mode_lbl)
        _stepper_debug(_place_x, _place_y, _place_w, _place_h)
    else:
        place_ch = getattr(rack, 'mixdown_place_ch', 0)
        place_lbl = f"ch {place_ch}" if place_ch > 0 else "auto"
        stepper_row(_place_x, _place_y, _place_w, _place_h, "place on channel", place_lbl)
        _stepper_debug(_place_x, _place_y, _place_w, _place_h)

    # After render — fully independent
    _after_x = b_xi + MX_AFTER_X*ui;  _after_y = body_top - MX_AFTER_Y*ui
    _after_w = MX_AFTER_W*ui;          _after_h = MX_AFTER_H*ui
    if is_bake:
        info_row(_after_x, _after_y, _after_w, _after_h,
                 "after render", "mute originals", dim=True)
    else:
        mode_labels = {0.0: "mute originals", 0.5: "keep active", 1.0: "remove originals"}
        closest  = min(mode_labels, key=lambda k: abs(k - import_mode))
        mode_lbl = mode_labels[closest]
        stepper_row(_after_x, _after_y, _after_w, _after_h, "after render", mode_lbl)
        _stepper_debug(_after_x, _after_y, _after_w, _after_h)

    # Hint + blend notice text
    _draw_text(("click: free channels ↔ replace in-place" if is_bake
                else "click: mute → keep active → remove"),
               _after_x + 4*ui + MX_HINT_X*ui,
               _after_y - fs8 - 4*ui + MX_HINT_Y*ui,
               fs8, (0.30, 0.42, 0.35, 0.7))
    blend_base = (os.path.splitext(os.path.basename(bpy.data.filepath))[0]
                  if bpy.data.filepath else "untitled")
    _draw_text(f"blend saved as {blend_base}_v001.blend before render",
               b_xi + MX_BLEND_LBL_X*ui,
               _place_y - fs8*2 - 10*ui + MX_BLEND_LBL_Y*ui,
               fs8, (0.25, 0.45, 0.35, 0.8))

    # Render button — fully independent
    _render_x = b_xi + MX_RENDER_X*ui;  _render_y = body_top - MX_RENDER_Y*ui
    _render_w = MX_RENDER_W*ui;          _render_h = MX_RENDER_H*ui

    if running:
        tc2, bl = (0.2,0.55,0.35,1.0), "rendering\u2026"
    else:
        tc2 = (0.0,0.90,0.58,1.0)
        bl  = ("bake channels & replace strips"
               if is_bake else "render & import mixdown")
    # Box chrome baked into background PNG \u2014 text only, colour still tracks state.
    tw = _text_width(bl, fs11)
    _draw_text(bl, _render_x + _render_w/2 - tw/2,
               _render_y + _render_h/2 - fs11/2, fs11, tc2)
    # ════════════════════════════════════════════════════════════════════
    c_y = body_top - pad
    def c_nxt(h):
        nonlocal c_y; c_y -= h; return c_y

    def stat_card(lbl, val, cx, cy, cw, ch):
        """Box chrome baked into background PNG — text only, at the same
        absolute position cx/cy with size cw/ch (all pre-scaled) as before."""
        _sx, _sy, _sw, _sh = _shrink(cx, cy, cw, ch)
        vw = _text_width(val, fs11)
        _draw_text(val, cx + cw/2 - vw/2, _sy + _sh*0.57, fs11, (0.78, 0.82, 0.92, 1.0))
        lw = _text_width(lbl, fs8)
        _draw_text(lbl, cx + cw/2 - lw/2, _sy + _sh*0.22, fs8, (0.38, 0.42, 0.52, 1.0))

    # Format badge — chrome baked into background PNG, text only
    _fmtcx = c_xi + MX_CARD_FMT_X*ui;  _fmtcy = body_top - MX_CARD_FMT_Y*ui
    _fmtcw = MX_CARD_FMT_W*ui;          _fmtch = MX_CARD_FMT_H*ui
    _fbx, _fby, _fbw, _fbh = _shrink(_fmtcx, _fmtcy, _fmtcw, _fmtch)
    fw = _text_width(fmt, fs12)
    _draw_text(fmt, _fmtcx + _fmtcw/2 - fw/2, _fby + _fbh*0.58,
               fs12, (0.45, 0.75, 1.0, 1.0))
    sub = f"{bd}-bit · {sr//1000}k"
    sw2 = _text_width(sub, fs8)
    _draw_text(sub, _fmtcx + _fmtcw/2 - sw2/2, _fby + _fbh*0.22,
               fs8, (0.25, 0.48, 0.68, 1.0))

    # Duration, size, selected — all absolute
    stat_card("duration",  dur_str,
              c_xi + MX_CARD_DUR_X*ui,  body_top - MX_CARD_DUR_Y*ui,
              MX_CARD_DUR_W*ui, MX_CARD_DUR_H*ui)
    size_v = (f"~{est_mb*max(1,n_sel)} MB" if is_bake else f"~{est_mb} MB")
    stat_card("est. size", size_v,
              c_xi + MX_CARD_SIZE_X*ui, body_top - MX_CARD_SIZE_Y*ui,
              MX_CARD_SIZE_W*ui, MX_CARD_SIZE_H*ui)
    sel_chs = [getattr(rack, 'group_idx', 0)*9 + j + 1
               for j in range(9) if getattr(rack, f'ch{j}', False)]
    if sel_chs:
        ch_str = ", ".join(str(c) for c in sel_chs)
        if _text_width(ch_str, fs11) > MX_CARD_SEL_W*ui - 8*ui:
            short = ", ".join(str(c) for c in sel_chs[:3])
            ch_str = f"{short} +{len(sel_chs)-3}"
    else:
        ch_str = "0 ch"
    stat_card("selected",  ch_str,
              c_xi + MX_CARD_SEL_X*ui,  body_top - MX_CARD_SEL_Y*ui,
              MX_CARD_SEL_W*ui, MX_CARD_SEL_H*ui)

    # Progress bar — only when rendering / done / error
    if running or done or has_error:
        c_nxt(8*ui)
        slabel("progress", c_xi, c_y - 8*ui)
        c_nxt(13*ui)
        prog_h = 8*ui; progy = c_nxt(prog_h + 2*ui)
        _draw_rect(c_xi, progy, c_wi, prog_h, (0.05, 0.07, 0.05, 1.0))
        fw2 = c_wi * min(1.0, max(0.0, progress))
        if fw2 > 0:
            _draw_rect(c_xi, progy, fw2, prog_h,
                       (0.2,0.85,0.5,1.0) if progress < 1.0 else (0.15,0.7,0.4,1.0))
        border(c_xi, progy, c_wi, prog_h, (0.20, 0.30, 0.22, 1.0))
        err_c = (0.9, 0.3, 0.3, 1.0); ok_c = (0.3, 0.8, 0.5, 1.0)
        pct   = (f"{int(progress*100)}%" if running
                 else ("done ✓" if done else "error"))
        sy = c_nxt(fs9 + 3*ui)
        _draw_text(pct, c_xi + 4*ui, sy, fs9, err_c if has_error else ok_c)
        ptw = _text_width(pct, fs9)
        _draw_text(status, c_xi + ptw + 10*ui, sy, fs9, (0.45, 0.55, 0.45, 0.9))

    # Offline hint — absolute position, no longer in c_nxt flow
    hint = "renders offline — timeline doesn't need to play"
    _hint_x = c_xi + MX_OFFLINE_X*ui
    _hint_y = body_top - MX_OFFLINE_Y*ui
    words = hint.split(); line = ""
    for w in words:
        test = (line + " " + w).strip()
        if _text_width(test, fs8) > c_wi - 4*ui and line:
            _draw_text(line, _hint_x + 2*ui, _hint_y, fs8, (0.30, 0.38, 0.48, 1.0))
            _hint_y -= (fs8 + 3*ui)
            line = w
        else:
            line = test
    if line:
        _draw_text(line, _hint_x + 2*ui, _hint_y, fs8, (0.30, 0.38, 0.48, 1.0))