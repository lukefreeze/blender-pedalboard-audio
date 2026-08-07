"""
ai_engines/demucs/demucs_runner.py
====================================
Subprocess worker for Demucs stem separation.
Targets demucs 4.x (4.0.1+).

Called by core/ai_demucs.py as:
    py -3.12 demucs_runner.py --args <json_args_file>

JSON args:
    {
        "src":       "/path/to/input.wav",
        "out_dir":   "/path/to/output/",
        "model":     "htdemucs_ft",
        "stems":     ["drums", "bass", "vocals", "other"],
        "preview":   false
    }

Stdout protocol:
    PROGRESS:<0-100>
    STEM_DONE:<stem_name>:<output_wav_path>
    ERROR:<message>
"""

import argparse
import json
import os
import sys


def main():
    # Force UTF-8 on Windows — default CP1252 codepage breaks paths with
    # non-ASCII characters in STEM_DONE output lines.
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--args", required=True)
    args = parser.parse_args()

    if not os.path.exists(args.args):
        print(f"ERROR:args file not found: {args.args}")
        sys.exit(1)

    with open(args.args, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    src_path = cfg.get("src", "")
    out_dir  = cfg.get("out_dir", "")
    model    = cfg.get("model", "htdemucs_ft")
    stems    = cfg.get("stems", ["drums", "bass", "vocals", "other"])
    preview  = bool(cfg.get("preview", False))

    if not os.path.exists(src_path):
        print(f"ERROR:source not found: {src_path}")
        sys.exit(1)

    os.makedirs(out_dir, exist_ok=True)

    print("PROGRESS:5")
    sys.stdout.flush()

    try:
        import torch
        import demucs
    except ImportError as e:
        print(f"ERROR:demucs not installed: {e}")
        sys.exit(1)

    print("PROGRESS:10")
    sys.stdout.flush()

    import tempfile
    import shutil
    import subprocess
    import threading

    tmp_out = tempfile.mkdtemp(prefix="pb_demucs_")

    try:
        # demucs 4.x CLI — WAV is default output format (no --wav flag needed)
        # Use default 16-bit PCM output (not --int24) so meters.py can read it
        cmd_args = [
            sys.executable, "-m", "demucs",
            "-n",    model,
            "-o",    tmp_out,
            "--clip-mode", "rescale",
        ]

        if preview:
            # Fewer shifts and less overlap = much faster, lower quality
            cmd_args += ["--overlap", "0.1", "--shifts", "0"]
        else:
            cmd_args += ["--overlap", "0.25", "--shifts", "1"]

        cmd_args.append(src_path)

        print("PROGRESS:15")
        sys.stdout.flush()

        # On Windows the winget ffmpeg installs a broken App Execution Alias.
        # Prepend the Python executable's directory to PATH so that any
        # ffmpeg/ffprobe copied there is found first.
        import os as _os
        env = _os.environ.copy()
        python_dir = _os.path.dirname(sys.executable)
        env["PATH"] = python_dir + _os.pathsep + env.get("PATH", "")

        proc = subprocess.Popen(
            cmd_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        # Relay stderr progress lines
        progress_val = [15]

        def _read_stderr():
            for line in proc.stderr:
                line = line.strip()
                if "%" in line:
                    try:
                        pct_str = [p for p in line.split() if "%" in p][0]
                        pct = float(pct_str.replace("%", ""))
                        mapped = 15 + int(pct * 0.75)
                        if mapped > progress_val[0]:
                            progress_val[0] = mapped
                            print(f"PROGRESS:{mapped}")
                            sys.stdout.flush()
                    except Exception:
                        pass
                else:
                    # Print non-progress stderr for debugging
                    if line:
                        sys.stderr.write(line + "\n")
                        sys.stderr.flush()

        t = threading.Thread(target=_read_stderr, daemon=True)
        t.start()
        proc.wait()
        t.join(timeout=5)

        if proc.returncode != 0:
            print(f"ERROR:demucs exited with code {proc.returncode}")
            sys.exit(1)

        print("PROGRESS:92")
        sys.stdout.flush()

        # Find demucs output — writes to tmp_out/<model>/<track_name>/<stem>.wav
        stem_dir = None
        for root, dirs, files in os.walk(tmp_out):
            wav_files = [f for f in files if f.endswith(".wav")]
            if wav_files:
                stem_dir = root
                break

        if not stem_dir:
            print(f"ERROR:no output files found in {tmp_out}")
            sys.exit(1)

        # Copy requested stems to out_dir
        copied = 0
        for stem in stems:
            # Try exact match first, then case-insensitive
            src_stem = os.path.join(stem_dir, f"{stem}.wav")
            if not os.path.exists(src_stem):
                for f in os.listdir(stem_dir):
                    if f.lower() == f"{stem.lower()}.wav":
                        src_stem = os.path.join(stem_dir, f)
                        break
            if os.path.exists(src_stem):
                dst = os.path.join(out_dir, f"{stem}.wav")
                shutil.copy2(src_stem, dst)
                print(f"STEM_DONE:{stem}:{dst}")
                sys.stdout.flush()
                copied += 1
            else:
                print(f"ERROR:stem not found: {stem} (looked in {stem_dir})")

        if copied == 0:
            print("ERROR:no stems were produced")
            sys.exit(1)

        print("PROGRESS:100")
        sys.stdout.flush()

    finally:
        try:
            shutil.rmtree(tmp_out, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()