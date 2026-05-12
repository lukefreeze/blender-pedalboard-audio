"""
ai_engines/whisper/whisper_runner.py
=====================================
System-Python subprocess runner for faster-whisper transcription.

Called by core/ai_whisper.py as:
    py -3.12 whisper_runner.py --args <json_args_file>

The JSON args file contains:
    {
        "src":      "/path/to/audio.wav",
        "model":    "base",           # tiny/base/small/medium/large-v3
        "language": "en"              # or null for auto-detect
    }

Stdout protocol:
    PROGRESS:<0-100>
    RESULT:<json array of {start, end, text} dicts>
    ERROR:<message>
"""

import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args", required=True,
                        help="Path to JSON args file")
    args = parser.parse_args()

    if not os.path.exists(args.args):
        print(f"ERROR:args file not found: {args.args}")
        sys.exit(1)

    with open(args.args, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    src_path = cfg.get("src", "")
    model    = cfg.get("model", "base")
    language = cfg.get("language", None)   # None = auto-detect

    if not os.path.exists(src_path):
        print(f"ERROR:source file not found: {src_path}")
        sys.exit(1)

    # ── Import faster-whisper ──────────────────────────────────────────────
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("ERROR:faster-whisper not installed — run: pip install faster-whisper")
        sys.exit(1)

    print("PROGRESS:5")
    sys.stdout.flush()

    # ── Load model ────────────────────────────────────────────────────────
    # Use CUDA if available, otherwise CPU with int8 for speed
    try:
        import torch
        device    = "cuda" if torch.cuda.is_available() else "cpu"
        compute   = "float16" if device == "cuda" else "int8"
    except ImportError:
        device  = "cpu"
        compute = "int8"

    try:
        whisper_model = WhisperModel(model, device=device, compute_type=compute)
    except Exception as e:
        print(f"ERROR:failed to load model '{model}': {e}")
        sys.exit(1)

    print("PROGRESS:15")
    sys.stdout.flush()

    # ── Normalise audio to 16kHz mono PCM int16 ───────────────────────────
    # Whisper expects 16kHz mono. Different source formats (sample rate,
    # bit depth, stereo) can cause ctranslate2 to crash on Windows (0xC0000409).
    # We pre-convert to a guaranteed-safe format before transcribing.
    try:
        import wave, struct, array, tempfile
        safe_path = os.path.join(tempfile.gettempdir(),
                                 os.path.basename(src_path).replace(".wav", "_16k.wav"))

        with wave.open(src_path, 'r') as wf:
            src_rate  = wf.getframerate()
            src_ch    = wf.getnchannels()
            src_sw    = wf.getsampwidth()
            src_frames = wf.getnframes()
            raw = wf.readframes(src_frames)

        # Convert to float samples
        if src_sw == 2:
            samples = array.array('h', raw)
            fsamples = [s / 32768.0 for s in samples]
        elif src_sw == 4:
            # Could be int32 or float32 — try float32 first (Blender default)
            import struct as _struct
            n = len(raw) // 4
            fsamples = list(_struct.unpack(f'{n}f', raw))
            # Clamp in case it was actually int32
            if max(abs(x) for x in fsamples[:100] if fsamples) > 1.5:
                fsamples = [s / 2147483648.0 for s in
                            _struct.unpack(f'{n}i', raw)]
        elif src_sw == 1:
            samples = array.array('B', raw)
            fsamples = [(s - 128) / 128.0 for s in samples]
        else:
            fsamples = [s / 32768.0 for s in array.array('h', raw)]

        # Mix down to mono
        if src_ch > 1:
            mono = [sum(fsamples[i:i+src_ch]) / src_ch
                    for i in range(0, len(fsamples), src_ch)]
        else:
            mono = fsamples

        # Resample to 16000 Hz (simple linear interpolation)
        target_rate = 16000
        if src_rate != target_rate:
            ratio   = src_rate / target_rate
            out_len = int(len(mono) / ratio)
            resampled = []
            for i in range(out_len):
                pos = i * ratio
                lo  = int(pos)
                hi  = min(lo + 1, len(mono) - 1)
                frac = pos - lo
                resampled.append(mono[lo] * (1 - frac) + mono[hi] * frac)
            mono = resampled

        # Convert to int16 and write
        out_samples = array.array('h',
            [max(-32768, min(32767, int(s * 32767))) for s in mono])
        with wave.open(safe_path, 'w') as wf_out:
            wf_out.setnchannels(1)
            wf_out.setsampwidth(2)
            wf_out.setframerate(target_rate)
            wf_out.writeframes(out_samples.tobytes())

        transcribe_src = safe_path
    except Exception as e:
        # If conversion fails, use original and hope for the best
        transcribe_src = src_path
        safe_path = None

    # ── Transcribe ────────────────────────────────────────────────────────
    try:
        translate    = cfg.get("translate",    False)
        vad          = cfg.get("vad",          True)
        task         = "translate" if translate else "transcribe"

        # NOTE: chunk_length is intentionally NOT passed to transcribe().
        # Passing chunk_length to ctranslate2 on Windows causes a fatal
        # stack overflow (0xC0000409) regardless of file length or beam size.
        # faster-whisper handles segmentation correctly without it.
        transcribe_kwargs = {
            "beam_size":                   1,
            "word_timestamps":             False,
            "vad_filter":                  vad,
            "task":                        task,
            "no_speech_threshold":         0.6,
            "compression_ratio_threshold": 2.4,
        }
        if language:
            transcribe_kwargs["language"] = language

        segments_iter, info = whisper_model.transcribe(transcribe_src, **transcribe_kwargs)
        print("PROGRESS:20")
        sys.stdout.flush()

        # Collect all segments — iterator is lazy so we drive it here
        segments_list = []
        for seg in segments_iter:
            segments_list.append({
                "start": round(seg.start, 3),
                "end":   round(seg.end,   3),
                "text":  seg.text,
            })
            if hasattr(info, "duration") and info.duration > 0:
                pct = int(20 + 75 * (seg.end / info.duration))
                pct = min(pct, 94)
                print(f"PROGRESS:{pct}")
                sys.stdout.flush()

    except Exception as e:
        print(f"ERROR:transcription failed: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    finally:
        # Clean up normalised temp file
        try:
            if safe_path and os.path.exists(safe_path):
                os.remove(safe_path)
        except Exception:
            pass

    print("PROGRESS:98")
    sys.stdout.flush()

    # ── Output ────────────────────────────────────────────────────────────
    result_json = json.dumps(segments_list, ensure_ascii=False)
    print(f"RESULT:{result_json}")
    sys.stdout.flush()

    print("PROGRESS:100")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
