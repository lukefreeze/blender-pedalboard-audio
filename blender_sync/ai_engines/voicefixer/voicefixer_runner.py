"""
ai_engines/voicefixer/voicefixer_runner.py
==========================================
Standalone runner for VoiceFixer speech restoration.
Called as a subprocess by core/ai_voicefixer.py.

Usage:
    python voicefixer_runner.py --src INPUT.wav --output OUTPUT.wav --mode 0

Modes:
    0 = Standard restoration (default, recommended)
    1 = Smooth (preprocessing removes high freq first, alters voice slightly)
    2 = Aggressive (best for seriously degraded recordings)

Outputs:
    PROGRESS:N  — progress percentage (0-100)
    DONE:/path  — output path on success
    ERROR:msg   — error message on failure
"""

import sys
import os
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src",    required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode",   type=int, default=0)
    args = parser.parse_args()

    if not os.path.exists(args.src):
        print(f"ERROR: source file not found: {args.src}")
        sys.exit(1)

    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)

    try:
        print("PROGRESS:5")
        sys.stdout.flush()

        from voicefixer import VoiceFixer
        print("PROGRESS:15")
        sys.stdout.flush()

        import torch
        use_cuda = torch.cuda.is_available()
        use_mps  = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        # VoiceFixer only supports cuda=True/False, MPS falls back to CPU
        cuda = use_cuda
        device_str = "cuda" if use_cuda else ("mps" if use_mps else "cpu")
        print(f"PROGRESS:20")
        sys.stdout.flush()

        vf = VoiceFixer()
        print("PROGRESS:30")
        sys.stdout.flush()

        vf.restore(
            input=args.src,
            output=args.output,
            cuda=cuda,
            mode=args.mode,
        )
        print("PROGRESS:95")
        sys.stdout.flush()

        if os.path.exists(args.output):
            print(f"DONE:{args.output}")
        else:
            print(f"ERROR: output file not created at {args.output}")
        sys.stdout.flush()

    except Exception as e:
        import traceback
        print(f"ERROR:{e}")
        traceback.print_exc()
        sys.stdout.flush()
        sys.exit(1)


if __name__ == "__main__":
    main()
