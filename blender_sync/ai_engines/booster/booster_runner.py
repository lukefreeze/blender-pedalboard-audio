"""
ai_engines/booster/booster_runner.py
=====================================
Subprocess worker for THE BOOSTER!! rack.

Called by core/booster.py as:
    py -3.12 booster_runner.py --args <json_args_file>

JSON args:
    {
        "src":       "/path/to/input.wav",
        "output":    "/path/to/output_boosted_+18dB.wav",
        "boost_db":  18.0,
        "limiter":   true
    }

Stdout protocol:
    PROGRESS:<0-100>
    ERROR:<message>
"""

import argparse
import json
import os
import sys
import wave
import array
import math
import struct


def soft_limit(x):
    """Tanh soft limiter — smooth saturation, no hard clipping above 0dBFS."""
    if abs(x) <= 1.0:
        return x
    sign = 1.0 if x >= 0 else -1.0
    return sign * math.tanh(abs(x))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args", required=True)
    args = parser.parse_args()

    if not os.path.exists(args.args):
        print(f"ERROR:args file not found: {args.args}")
        sys.exit(1)

    with open(args.args, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    src_path  = cfg.get("src", "")
    out_path  = cfg.get("output", "")
    boost_db  = float(cfg.get("boost_db", 12.0))
    use_limit = bool(cfg.get("limiter", True))

    if not os.path.exists(src_path):
        print(f"ERROR:source not found: {src_path}")
        sys.exit(1)

    print("PROGRESS:5")
    sys.stdout.flush()

    # ── Read WAV ──────────────────────────────────────────────────────────────
    try:
        with wave.open(src_path, 'r') as wf:
            n_ch     = wf.getnchannels()
            samp_w   = wf.getsampwidth()
            frame_r  = wf.getframerate()
            n_frames = wf.getnframes()
            raw      = wf.readframes(n_frames)
    except Exception as e:
        print(f"ERROR:could not read WAV: {e}")
        sys.exit(1)

    print("PROGRESS:15")
    sys.stdout.flush()

    # ── Decode to float ───────────────────────────────────────────────────────
    if samp_w == 2:
        n_samp   = len(raw) // 2
        samples  = array.array('h')
        samples.frombytes(raw)
        fsamples = [s / 32768.0 for s in samples]
    elif samp_w == 4:
        n_samp = len(raw) // 4
        try:
            fsamples = list(struct.unpack(f'{n_samp}f', raw))
            # Sanity: float32 should be in -1..1, int32 would be huge
            if fsamples and max(abs(x) for x in fsamples[:min(100, n_samp)]) > 10.0:
                fsamples = [s / 2147483648.0 for s in
                            struct.unpack(f'{n_samp}i', raw)]
        except Exception:
            fsamples = [s / 2147483648.0 for s in
                        struct.unpack(f'{n_samp}i', raw)]
    elif samp_w == 1:
        fsamples = [(b - 128) / 128.0 for b in array.array('B', raw)]
    else:
        print(f"ERROR:unsupported sample width {samp_w} bytes")
        sys.exit(1)

    print("PROGRESS:25")
    sys.stdout.flush()

    # ── Apply gain ────────────────────────────────────────────────────────────
    gain_linear = 10.0 ** (boost_db / 20.0)
    n_total     = len(fsamples)
    boosted     = [0.0] * n_total
    report_step = max(1, n_total // 60)

    for i in range(n_total):
        val = fsamples[i] * gain_linear
        if use_limit:
            val = soft_limit(val)
        else:
            val = max(-1.0, min(1.0, val))   # hard clip as safety floor
        boosted[i] = val

        if i % report_step == 0:
            pct = 25 + int(65 * i / n_total)
            print(f"PROGRESS:{pct}")
            sys.stdout.flush()

    print("PROGRESS:92")
    sys.stdout.flush()

    # ── Write float32 WAV ─────────────────────────────────────────────────────
    # Must be float32 because _build_envelope in meters.py reads aud.Sound.data()
    # which returns float32 bytes. Writing int16 would halve the apparent sample
    # count and make the VU meter stop after 1-2 seconds.
    import struct as _struct
    out_bytes = _struct.pack(f'{len(boosted)}f', *boosted)

    try:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with wave.open(out_path, 'w') as wf_out:
            wf_out.setnchannels(n_ch)
            wf_out.setsampwidth(4)          # 4 bytes = float32
            wf_out.setframerate(frame_r)
            wf_out.writeframes(out_bytes)
    except Exception as e:
        print(f"ERROR:could not write output: {e}")
        sys.exit(1)

    print("PROGRESS:98")
    sys.stdout.flush()
    print("PROGRESS:100")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
