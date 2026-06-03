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
RACK_EXPANDED_H_MX  = 520    # tall enough for all controls

# ---------------------------------------------------------------------------
# Render state — one active render job at a time
# ---------------------------------------------------------------------------
_mx_state = {
    'running':    False,
    'progress':   0.0,       # 0..1
    'status_msg': 'ready',
    'rack_idx':   -1,
    'error':      '',
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

    # ── Skin background
    try:
        from ui.mixer.draw_utils import draw_element as _de
        _de("rack_mixdown_bg", rx, ry, rw, body_h, _draw_rect, (0.04, 0.04, 0.04, 1.0))
    except Exception:
        pass

    shader = _get_shader()

    # ── Column boundaries (unscaled px, then * ui) ──────────────────────
    A_W  = 160 * ui    # render mode column width
    CH_W = 100 * ui    # channel buttons column (owned by rack_base)
    C_W  = 200 * ui    # stats column width
    # B fills whatever is left
    A_X  = rx                        # col A start
    B_X  = rx + A_W                  # col B start
    C_X  = rx + rw - CH_W - C_W      # col C start
    D_X  = rx + rw - CH_W            # col D start (don't draw here)
    B_W  = C_X - B_X                 # col B width

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
            _draw_rect(bx, y, bw, h, bg); border(bx, y, bw, h, bc)
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
            _draw_rect(bx, y, bw, h, bg); border(bx, y, bw, h, bc)
            tw = _text_width(lbl, fs9)
            _draw_text(lbl, bx + bw/2 - tw/2, y + h/2 - fs9/2, fs9, tc)

    def mode_btn(x, y, w, h, lbl, active, col=(0.0, 0.55, 0.85, 1.0)):
        bg = (0.04, 0.14, 0.24, 1.0) if active else (0.08, 0.09, 0.11, 1.0)
        bc = col                      if active else (0.22, 0.22, 0.25, 1.0)
        tc = col                      if active else (0.35, 0.38, 0.44, 1.0)
        _draw_rect(x, y, w, h, bg); border(x, y, w, h, bc)
        tw = _text_width(lbl, fs9)
        _draw_text(lbl, x + w/2 - tw/2, y + h/2 - fs9/2, fs9, tc)

    def info_row(x, y, w, h, ltxt, rtxt,
                 bg=(0.05,0.09,0.07,1.0), bc=(0.18,0.26,0.22,1.0),
                 lc=(0.35,0.50,0.40,0.8), rc=(0.2,0.85,0.6,1.0)):
        _draw_rect(x, y, w, h, bg); border(x, y, w, h, bc)
        _draw_text(ltxt, x + 5*ui, y + h/2 - fs9/2, fs9, lc)
        tw = _text_width(rtxt, fs10)
        _draw_text(rtxt, x + w - tw - 7*ui, y + h/2 - fs10/2, fs10, rc)

    # Draw column dividers
    divider(A_X + A_W)
    divider(C_X)

    # ════════════════════════════════════════════════════════════════════
    # COL A — Render mode
    # ════════════════════════════════════════════════════════════════════
    a_y = body_top - pad
    def a_nxt(h):
        nonlocal a_y; a_y -= h; return a_y

    a_nxt(4*ui)
    slabel("render mode", a_xi, a_y - 8*ui)
    a_nxt(13*ui)
    btn_h = 24 * ui
    mode_btn(a_xi, a_nxt(btn_h + 4*ui), a_wi, btn_h,
             "mix to single file", not is_bake)
    mode_btn(a_xi, a_nxt(btn_h + 4*ui), a_wi, btn_h,
             "bake per channel",   is_bake)

    # ════════════════════════════════════════════════════════════════════
    # COL B — Settings, import, render button
    # ════════════════════════════════════════════════════════════════════
    b_y = body_top - pad
    def b_nxt(h):
        nonlocal b_y; b_y -= h; return b_y

    tog_h = 20 * ui
    row_h = 20 * ui

    # OUTPUT PATH
    b_nxt(4*ui)
    slabel("output " + ("folder" if is_bake else "file"), b_xi, b_y - 8*ui)
    b_nxt(13*ui)
    path_h   = 22 * ui
    browse_w = max(52*ui, _text_width("folder…", fs9) + 16*ui)
    path_bw  = b_wi - browse_w - 4*ui
    path_y   = b_nxt(path_h + 3*ui)
    _draw_rect(b_xi, path_y, path_bw, path_h, (0.05, 0.06, 0.08, 1.0))
    border(b_xi, path_y, path_bw, path_h, (0.20, 0.22, 0.28, 1.0))
    disp = (out_path if out_path
            else ("(click 'save as…' to set path)" if not is_bake
                  else "(click 'choose folder…')"))
    _draw_text(disp, b_xi + 5*ui, path_y + path_h/2 - fs9/2, fs9,
               (0.72, 0.75, 0.82, 1.0) if out_path else (0.42, 0.45, 0.52, 1.0))
    br_x = b_xi + path_bw + 4*ui
    br_lbl = "folder…" if is_bake else "save as…"
    _draw_rect(br_x, path_y, browse_w, path_h, (0.10, 0.13, 0.18, 1.0))
    border(br_x, path_y, browse_w, path_h, (0.28, 0.38, 0.50, 1.0))
    tw = _text_width(br_lbl, fs9)
    _draw_text(br_lbl, br_x + browse_w/2 - tw/2,
               path_y + path_h/2 - fs9/2, fs9, (0.45, 0.65, 0.85, 1.0))

    # FORMAT / SAMPLE RATE / BIT DEPTH — three equal sub-columns
    b_nxt(6*ui)
    col3_w = (b_wi - 2*4*ui) / 3
    col3_g = 4*ui
    for ci, lbl in enumerate(["format", "sample rate", "bit depth"]):
        slabel(lbl, b_xi + ci*(col3_w + col3_g), b_y - 8*ui)
    b_nxt(13*ui)
    ty = b_nxt(tog_h + 2*ui)
    two_btn(b_xi,                         ty, col3_w, tog_h,
            rack.p1 > 0.5, "WAV", "FLAC", col_on=(0.2, 0.6, 0.9, 1.0))
    three_btn(b_xi + col3_w + col3_g,     ty, col3_w, tog_h,
              rack.p2, ["44k", "48k", "96k"], col_on=(0.3, 0.7, 0.5, 1.0))
    three_btn(b_xi + 2*(col3_w + col3_g), ty, col3_w, tog_h,
              rack.p3, ["16", "24", "32f"],   col_on=(0.6, 0.5, 0.8, 1.0))

    # RANGE
    b_nxt(6*ui)
    slabel("range", b_xi, b_y - 8*ui)
    b_nxt(13*ui)
    rng_y = b_nxt(tog_h + 2*ui)
    two_btn(b_xi, rng_y, b_wi, tog_h,
            is_custom, "full timeline", "custom frames",
            col_on=(0.7, 0.5, 0.2, 1.0))
    if is_custom:
        b_nxt(2*ui)
        cf_h = 20*ui; cfy = b_nxt(cf_h + 2*ui); hw = b_wi/2 - 3*ui
        for i, (lbl, val) in enumerate([("start", f_start), ("end", f_end)]):
            bx = b_xi + i*(hw + 6*ui)
            _draw_rect(bx, cfy, hw, cf_h, (0.05, 0.08, 0.12, 1.0))
            border(bx, cfy, hw, cf_h, (0.20, 0.28, 0.38, 1.0))
            _draw_text(lbl, bx + 5*ui, cfy + cf_h/2 - fs9/2, fs9,
                       (0.45, 0.50, 0.60, 0.8))
            vw = _text_width(str(val), fs10)
            _draw_text(str(val), bx + hw - vw - 6*ui,
                       cfy + cf_h/2 - fs10/2, fs10, (0.8, 0.85, 0.95, 1.0))
    else:
        b_nxt(2*ui)
        _draw_text(f"frames {f_start}–{f_end}  ({dur_str})",
                   b_xi + 4*ui, b_nxt(fs8 + 3*ui), fs8, (0.40, 0.50, 0.40, 0.9))

    # IMPORT BACK INTO VSE
    b_nxt(6*ui)
    slabel("import back into VSE", b_xi, b_y - 8*ui, (0.20, 0.60, 0.40, 1.0))
    b_nxt(13*ui)
    if is_bake:
        opts = [("mute originals, place on free channels", 0.0),
                ("remove originals, replace in-place",     1.0)]
        for lbl, oval in opts:
            b_nxt(2*ui)
            oy  = b_nxt(row_h)
            sel = abs(import_mode - oval) < 0.3
            bg  = (0.04, 0.14, 0.10, 1.0) if sel else (0.07, 0.09, 0.08, 1.0)
            bc  = (0.0, 0.65, 0.45, 1.0)  if sel else (0.20, 0.25, 0.22, 1.0)
            _draw_rect(b_xi, oy, b_wi, row_h, bg); border(b_xi, oy, b_wi, row_h, bc)
            dot_x = b_xi + 10*ui; dot_y = oy + row_h/2; dot_r = 3.5*ui
            if sel:
                segs = 10; tris = []
                for _si in range(segs):
                    a0 = 2*math.pi*_si/segs; a1 = 2*math.pi*(_si+1)/segs
                    tris += [(dot_x, dot_y),
                             (dot_x + dot_r*math.cos(a0), dot_y + dot_r*math.sin(a0)),
                             (dot_x + dot_r*math.cos(a1), dot_y + dot_r*math.sin(a1))]
                db = batch_for_shader(shader, "TRIS", {"pos": tris})
                shader.bind(); shader.uniform_float("color", (0.0, 0.8, 0.55, 1.0))
                db.draw(shader)
            _draw_text(lbl, b_xi + 20*ui, oy + row_h/2 - fs9/2, fs9,
                       (0.2, 0.85, 0.6, 1.0) if sel else (0.35, 0.45, 0.40, 1.0))
    else:
        mode_labels = {0.0: "mute originals", 0.5: "keep active",
                       1.0: "remove originals"}
        closest  = min(mode_labels, key=lambda k: abs(k - import_mode))
        mode_lbl = mode_labels[closest]
        hw2      = b_wi / 2 - 2*ui
        b_nxt(2*ui); r1y = b_nxt(row_h)
        info_row(b_xi,          r1y, hw2, row_h, "place on channel", "auto (above all)")
        info_row(b_xi+hw2+4*ui, r1y, hw2, row_h, "after render",     mode_lbl)
        b_nxt(1*ui)
        _draw_text("click to cycle: mute → keep active → remove",
                   b_xi + 4*ui, b_nxt(fs8 + 3*ui), fs8, (0.30, 0.42, 0.35, 0.7))

    # Version note
    blend_base = (os.path.splitext(os.path.basename(bpy.data.filepath))[0]
                  if bpy.data.filepath else "untitled")
    b_nxt(3*ui)
    _draw_text(f"blend saved as {blend_base}_v001.blend before render",
               b_xi + 4*ui, b_nxt(fs8 + 3*ui), fs8, (0.25, 0.45, 0.35, 0.8))

    # RENDER BUTTON
    b_nxt(6*ui)
    btn_h2 = 26*ui; btn_y = b_nxt(btn_h2 + 4*ui)
    if running:
        bb, bc2, tc2, bl = ((0.05,0.15,0.10,1.0),(0.15,0.40,0.25,1.0),
                            (0.2,0.55,0.35,1.0), "rendering…")
    else:
        bb, bc2, tc2 = ((0.04,0.22,0.13,1.0),(0.0,0.65,0.42,1.0),
                        (0.0,0.90,0.58,1.0))
        bl = ("bake channels & replace strips"
              if is_bake else "render & import mixdown")
    _draw_rect(b_xi, btn_y, b_wi, btn_h2, bb)
    border(b_xi, btn_y, b_wi, btn_h2, bc2)
    tw = _text_width(bl, fs11)
    _draw_text(bl, b_xi + b_wi/2 - tw/2, btn_y + btn_h2/2 - fs11/2, fs11, tc2)

    # ════════════════════════════════════════════════════════════════════
    # COL C — Stats + progress
    # ════════════════════════════════════════════════════════════════════
    c_y = body_top - pad
    def c_nxt(h):
        nonlocal c_y; c_y -= h; return c_y

    def stat_card(lbl, val, y):
        card_h = 34*ui
        _draw_rect(c_xi, y, c_wi, card_h, (0.07, 0.08, 0.10, 1.0))
        border(c_xi, y, c_wi, card_h, (0.18, 0.20, 0.24, 1.0))
        vw = _text_width(val, fs11)
        _draw_text(val, c_xi + c_wi/2 - vw/2,
                   y + card_h*0.57, fs11, (0.78, 0.82, 0.92, 1.0))
        lw = _text_width(lbl, fs8)
        _draw_text(lbl, c_xi + c_wi/2 - lw/2,
                   y + card_h*0.22, fs8, (0.38, 0.42, 0.52, 1.0))

    # Format badge
    c_nxt(4*ui)
    badge_h = 44*ui; badge_y = c_nxt(badge_h)
    _draw_rect(c_xi, badge_y, c_wi, badge_h, (0.05, 0.12, 0.20, 1.0))
    border(c_xi, badge_y, c_wi, badge_h, (0.10, 0.28, 0.45, 1.0))
    fw = _text_width(fmt, fs12)
    _draw_text(fmt, c_xi + c_wi/2 - fw/2, badge_y + badge_h*0.58,
               fs12, (0.45, 0.75, 1.0, 1.0))
    sub = f"{bd}-bit · {sr//1000}k"
    sw = _text_width(sub, fs8)
    _draw_text(sub, c_xi + c_wi/2 - sw/2, badge_y + badge_h*0.22,
               fs8, (0.25, 0.48, 0.68, 1.0))

    c_nxt(6*ui)
    stat_card("duration",  dur_str,       c_nxt(34*ui))
    c_nxt(4*ui)
    size_v = (f"~{est_mb*max(1,n_sel)} MB" if is_bake else f"~{est_mb} MB")
    stat_card("est. size", size_v,         c_nxt(34*ui))
    c_nxt(4*ui)
    stat_card("selected",  f"{n_sel} ch",  c_nxt(34*ui))

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

    # Offline hint (word-wrapped)
    c_nxt(8*ui)
    hint = "renders offline — timeline doesn't need to play"
    words = hint.split(); line = ""
    for w in words:
        test = (line + " " + w).strip()
        if _text_width(test, fs8) > c_wi - 4*ui and line:
            _draw_text(line, c_xi + 2*ui, c_nxt(fs8 + 3*ui), fs8,
                       (0.30, 0.38, 0.48, 1.0))
            line = w
        else:
            line = test
    if line:
        _draw_text(line, c_xi + 2*ui, c_nxt(fs8 + 3*ui), fs8,
                   (0.30, 0.38, 0.48, 1.0))
